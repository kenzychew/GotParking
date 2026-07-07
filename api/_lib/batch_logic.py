"""Business logic for the secret-gated batch-predict endpoint.

Computes all active carparks' forecasts per invocation (triggered by the
poller after each successful poll cycle) and upserts them into
`carpark_forecast`. See the design doc's Premise #9 (amended, D10),
Premise #10 (cold start), Premise #11 (baseline + momentum, amended), and
the T4 task's BATCH ENDPOINT section for the exact contract.

The module has two layers:
  * `run_batch_predict` -- the per-carpark state-decision and forecast
    computation, given already-authenticated dependencies.
  * `handle_batch_predict` -- the auth gate (`x-batch-secret`) plus HTTP
    status-code shaping around `run_batch_predict`.

Both take a `BatchDeps` bundle of injected dependencies so tests can supply
fakes without touching a network or a real Supabase project.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

import lightgbm
import numpy as np

from _lib.config import (
    COLD_START_MIN_AGE_HOURS,
    COLD_START_MIN_SAMPLES,
    FAIL_REASON_MODEL_ARTIFACT_MISSING,
    FAIL_REASON_SUPABASE_UNAVAILABLE,
    FORECAST_HORIZON_MINUTES,
    MODEL_STORAGE_BUCKET,
    MOMENTUM_FRESHNESS_MINUTES,
)
from _lib.features import build_feature_vector
from _lib.http_helpers import HttpResponse, get_header
from _lib.model_cache import ModelCache, ModelLoadError
from _lib.sg_time import sgt_parts
from _lib.supabase_rest import SupabaseClient, SupabaseUnavailableError, parse_timestamp
from _lib.tiers import compute_tier

logger = logging.getLogger(__name__)

STATE_ML = "ml"
STATE_BASELINE = "baseline"
STATE_COLD_START = "cold_start"


@dataclass(frozen=True)
class CarparkInfo:
    """One row from the `carparks` whitelist (active carparks only)."""

    carpark_id: str
    name: str


@dataclass(frozen=True)
class HistoryStats:
    """Per-carpark aggregates derived from `carpark_history`.

    Attributes:
        first_polled_at: Timestamp of the earliest row for this carpark, or
            None if it has no history at all.
        sample_count: Total row count for this carpark.
        capacity: max(available_lots) ever observed, or None if no history.
        live_lots: available_lots from the most recent row, or None if no
            history.
    """

    first_polled_at: datetime | None
    sample_count: int
    capacity: int | None
    live_lots: int | None


@dataclass(frozen=True)
class MomentumRow:
    """One row from `carpark_momentum`."""

    lots_15m_ago: int | None
    lots_30m_ago: int | None
    lots_60m_ago: int | None
    updated_at: datetime


@dataclass(frozen=True)
class ForecastRow:
    """One row to upsert into `carpark_forecast`."""

    carpark_id: str
    state: str
    forecast_lots: int | None
    tier: str | None
    live_lots: int
    model_version: str | None
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        """Serialize to the exact PostgREST upsert payload shape."""
        return {
            "carpark_id": self.carpark_id,
            "state": self.state,
            "forecast_lots": self.forecast_lots,
            "tier": self.tier,
            "live_lots": self.live_lots,
            "model_version": self.model_version,
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True)
class BatchResult:
    """Outcome of a successful batch-predict run."""

    computed: int
    generated_at: str


class BatchPredictError(Exception):
    """Raised when the batch run cannot complete (Supabase unavailable
    after its single retry). A /fail ping has already been fired by the
    time this is raised.
    """


@dataclass
class BatchDeps:
    """Injectable dependencies for run_batch_predict()/handle_batch_predict().

    Production wiring lives in `api/batch_predict.py` (kept out of this
    module so it has zero direct env/network bootstrap and stays trivially
    testable); tests construct BatchDeps directly with fakes.

    Attributes:
        db: Supabase REST client (or a test fake with the same interface:
            `select`, `select_all`, `upsert`, `download_storage_object`).
        batch_shared_secret: Expected value of the `x-batch-secret` header.
        model_cache: The (shared, in production) LightGBM booster cache.
        fail_ping: Callable taking a reason string, wired to fire a
            healthchecks `/fail` ping (a no-op/recording fake in tests).
        clock: Zero-arg callable returning the current UTC instant.
    """

    db: SupabaseClient
    batch_shared_secret: str
    model_cache: ModelCache
    fail_ping: Callable[[str], None]
    clock: Callable[[], datetime]


def _is_cold_start(stats: HistoryStats | None, now: datetime) -> bool:
    """Decide cold-start per Premise #10.

    A carpark is cold_start if its first sample is younger than
    `COLD_START_MIN_AGE_HOURS`, OR it has fewer than
    `COLD_START_MIN_SAMPLES` samples (whichever condition is true first;
    both are checked).
    """
    if stats is None or stats.sample_count == 0 or stats.first_polled_at is None:
        return True
    age = now - stats.first_polled_at
    if age < timedelta(hours=COLD_START_MIN_AGE_HOURS):
        return True
    return stats.sample_count < COLD_START_MIN_SAMPLES


def _is_momentum_usable(momentum: MomentumRow | None, now: datetime) -> bool:
    """Decide whether a momentum row is fresh AND complete enough to serve
    the ml path (Premise #11, amended D5).

    A row that is missing entirely, stale (`updated_at` older than
    `MOMENTUM_FRESHNESS_MINUTES`), or has any of the three lag readings
    still null (possible for a carpark that only recently started
    accumulating momentum) is treated as unusable -- the carpark falls back
    to the baseline path for this cycle rather than feeding a partial or
    stale feature vector to the model.
    """
    if momentum is None:
        return False
    if now - momentum.updated_at > timedelta(minutes=MOMENTUM_FRESHNESS_MINUTES):
        return False
    return (
        momentum.lots_15m_ago is not None
        and momentum.lots_30m_ago is not None
        and momentum.lots_60m_ago is not None
    )


def _load_active_carparks(db: SupabaseClient) -> list[CarparkInfo]:
    """Load the active-carpark whitelist (`carparks` where active=true)."""
    result = db.select("carparks", params={"select": "carpark_id,name", "active": "eq.true"})
    return [CarparkInfo(carpark_id=r["carpark_id"], name=r["name"]) for r in result.rows]


def _load_active_model_version(db: SupabaseClient) -> str | None:
    """Load `model_config.active_model_version` (the singleton row)."""
    result = db.select("model_config", params={"select": "active_model_version", "limit": "1"})
    if not result.rows:
        return None
    return result.rows[0].get("active_model_version")


def _load_history_stats(db: SupabaseClient, carpark_ids: list[str]) -> dict[str, HistoryStats]:
    """Fetch (first_polled_at, sample_count, capacity, live_lots) per carpark.

    Issues exactly one `select_all` call against `carpark_history` for
    every requested carpark_id at once (via a `carpark_id=in.(...)`
    filter, the same idiom `_load_baseline` uses for `carpark_baseline`),
    rather than the three order+limit=1 point queries per carpark this
    function used previously -- that was O(3 x carparks) sequential HTTP
    round-trips, which stops being "acceptable at this scale" once carpark
    coverage grows past a handful of rows. `carpark_history`'s primary key
    is `(carpark_id, polled_at)` (already indexed), so a single
    `carpark_id,polled_at,available_lots` select for all requested
    carparks is well-supported. The four per-carpark stats are then
    derived in Python by grouping the returned rows by carpark_id and
    scanning each group in polled_at order: the earliest row gives
    first_polled_at, the row count gives sample_count, the latest row's
    available_lots gives live_lots, and the max available_lots across the
    group gives capacity.

    Args:
        db: Supabase client.
        carpark_ids: Active carpark IDs to load stats for.

    Returns:
        A dict with one entry per requested carpark_id. A carpark_id with
        no matching `carpark_history` rows still gets a safe-default entry
        (`HistoryStats(first_polled_at=None, sample_count=0, capacity=None,
        live_lots=None)`) rather than being omitted, since `_is_cold_start`
        expects every requested carpark_id to be present.
    """
    stats: dict[str, HistoryStats] = {
        carpark_id: HistoryStats(
            first_polled_at=None, sample_count=0, capacity=None, live_lots=None
        )
        for carpark_id in carpark_ids
    }
    if not carpark_ids:
        return stats

    ids = ",".join(carpark_ids)
    rows = db.select_all(
        "carpark_history",
        params={
            "select": "carpark_id,polled_at,available_lots",
            "carpark_id": f"in.({ids})",
            "order": "carpark_id.asc,polled_at.asc",
        },
    )

    grouped: dict[str, list[tuple[datetime, int]]] = {}
    for row in rows:
        grouped.setdefault(row["carpark_id"], []).append(
            (parse_timestamp(row["polled_at"]), row["available_lots"])
        )

    for carpark_id, samples in grouped.items():
        samples.sort(key=lambda sample: sample[0])
        stats[carpark_id] = HistoryStats(
            first_polled_at=samples[0][0],
            sample_count=len(samples),
            capacity=max(available for _, available in samples),
            live_lots=samples[-1][1],
        )
    return stats


def _load_momentum(db: SupabaseClient, carpark_ids: list[str]) -> dict[str, MomentumRow]:
    """Fetch `carpark_momentum` rows for the given carparks, in one call."""
    if not carpark_ids:
        return {}
    ids = ",".join(carpark_ids)
    result = db.select(
        "carpark_momentum",
        params={
            "select": "carpark_id,lots_15m_ago,lots_30m_ago,lots_60m_ago,updated_at",
            "carpark_id": f"in.({ids})",
        },
    )
    out: dict[str, MomentumRow] = {}
    for row in result.rows:
        out[row["carpark_id"]] = MomentumRow(
            lots_15m_ago=row.get("lots_15m_ago"),
            lots_30m_ago=row.get("lots_30m_ago"),
            lots_60m_ago=row.get("lots_60m_ago"),
            updated_at=parse_timestamp(row["updated_at"]),
        )
    return out


def _load_baseline(
    db: SupabaseClient, carpark_ids: list[str]
) -> dict[tuple[str, int, int], float]:
    """Fetch every `carpark_baseline` row for the given carparks.

    Uses `select_all` (paginated) rather than `select`: this table can hold
    up to `carparks x 7 dow x 96 slots` rows once the bootstrap window has
    elapsed, which can exceed PostgREST's default per-request row cap.
    """
    if not carpark_ids:
        return {}
    ids = ",".join(carpark_ids)
    rows = db.select_all(
        "carpark_baseline",
        params={
            "select": "carpark_id,dow,slot_of_day,avg_available_lots",
            "carpark_id": f"in.({ids})",
        },
    )
    return {
        (row["carpark_id"], row["dow"], row["slot_of_day"]): row["avg_available_lots"]
        for row in rows
    }


def _cold_start_row(carpark_id: str, live_lots: int, generated_at: str) -> ForecastRow:
    """Build a cold_start forecast row (forecast_lots/tier both null, per
    the `carpark_forecast_shape` CHECK constraint).
    """
    return ForecastRow(
        carpark_id=carpark_id,
        state=STATE_COLD_START,
        forecast_lots=None,
        tier=None,
        live_lots=live_lots,
        model_version=None,
        generated_at=generated_at,
    )


def _resolve_booster(
    deps: BatchDeps, active_version: str | None
) -> tuple[lightgbm.Booster | None, str | None]:
    """Resolve the booster to serve with this batch run, if any.

    Args:
        deps: Injected dependencies.
        active_version: `model_config.active_model_version`, or None if no
            model has ever been promoted.

    Returns:
        A `(booster, booster_version)` pair. Both are None if there is no
        promoted model, or the promoted model's artifact failed to load
        and no last-known-good booster is cached (in which case a
        MODEL_ARTIFACT_MISSING /fail ping has already been fired).
    """
    if not active_version:
        return None, None

    try:
        booster = deps.model_cache.get(
            active_version,
            lambda: deps.db.download_storage_object(
                MODEL_STORAGE_BUCKET, f"{active_version}.txt"
            ).decode("utf-8"),
        )
        return booster, active_version
    except ModelLoadError as exc:
        fallback = deps.model_cache.last_known_good()
        if fallback is not None:
            fallback_version, fallback_booster = fallback
            logger.warning(
                "batch_predict: artifact load failed for version=%s (%s); "
                "falling back to last-known-good version=%s",
                active_version,
                exc,
                fallback_version,
            )
            return fallback_booster, fallback_version

        logger.error(
            "batch_predict: artifact load failed for version=%s (%s) and no "
            "last-known-good model cached; serving baseline for all carparks",
            active_version,
            exc,
        )
        deps.fail_ping(FAIL_REASON_MODEL_ARTIFACT_MISSING)
        return None, None


def run_batch_predict(deps: BatchDeps) -> BatchResult:
    """Compute and upsert every active carpark's forecast for this cycle.

    Args:
        deps: Injected dependencies (already authenticated by the caller).

    Returns:
        The computed row count and the generated_at timestamp used.

    Raises:
        BatchPredictError: If Supabase reads or the final write fail after
            their single retry. A /fail ping has already been fired before
            this is raised.
    """
    now = deps.clock()
    generated_at = now.isoformat()
    target = now + timedelta(minutes=FORECAST_HORIZON_MINUTES)
    target_dow, target_slot = sgt_parts(target)

    try:
        carparks = _load_active_carparks(deps.db)
        carpark_ids = [c.carpark_id for c in carparks]
        active_version = _load_active_model_version(deps.db)
        history_stats = _load_history_stats(deps.db, carpark_ids)
        momentum = _load_momentum(deps.db, carpark_ids)
        baseline = _load_baseline(deps.db, carpark_ids)
    except SupabaseUnavailableError as exc:
        logger.error("batch_predict: Supabase read failed: %s", exc)
        deps.fail_ping(FAIL_REASON_SUPABASE_UNAVAILABLE)
        raise BatchPredictError("supabase_unavailable") from exc

    booster, booster_version = _resolve_booster(deps, active_version)

    rows: list[ForecastRow] = []
    for carpark in carparks:
        stats = history_stats.get(carpark.carpark_id)
        live_lots = stats.live_lots if stats is not None and stats.live_lots is not None else 0

        if _is_cold_start(stats, now):
            rows.append(_cold_start_row(carpark.carpark_id, live_lots, generated_at))
            continue

        capacity = stats.capacity if stats is not None and stats.capacity is not None else 0
        momentum_row = momentum.get(carpark.carpark_id)

        if booster is not None and momentum_row is not None and _is_momentum_usable(
            momentum_row, now
        ):
            # _is_momentum_usable already guarantees these three are not
            # None; asserted explicitly so the type checker can narrow them
            # from `int | None` to `int` at the call below.
            assert momentum_row.lots_15m_ago is not None
            assert momentum_row.lots_30m_ago is not None
            assert momentum_row.lots_60m_ago is not None
            feature_vector = build_feature_vector(
                target,
                lots_now=live_lots,
                lots_15m_ago=momentum_row.lots_15m_ago,
                lots_30m_ago=momentum_row.lots_30m_ago,
                lots_60m_ago=momentum_row.lots_60m_ago,
            )
            feature_matrix = np.array([feature_vector], dtype=np.float64)
            raw_prediction = float(booster.predict(feature_matrix)[0])
            forecast_lots = max(round(raw_prediction), 0)
            tier = compute_tier(forecast_lots, capacity)
            rows.append(
                ForecastRow(
                    carpark_id=carpark.carpark_id,
                    state=STATE_ML,
                    forecast_lots=forecast_lots,
                    tier=tier,
                    live_lots=live_lots,
                    model_version=booster_version,
                    generated_at=generated_at,
                )
            )
        else:
            baseline_value = baseline.get((carpark.carpark_id, target_dow, target_slot))
            base_value = baseline_value if baseline_value is not None else live_lots
            forecast_lots = max(round(base_value), 0)
            tier = compute_tier(forecast_lots, capacity)
            rows.append(
                ForecastRow(
                    carpark_id=carpark.carpark_id,
                    state=STATE_BASELINE,
                    forecast_lots=forecast_lots,
                    tier=tier,
                    live_lots=live_lots,
                    model_version=None,
                    generated_at=generated_at,
                )
            )

    try:
        deps.db.upsert(
            "carpark_forecast", [row.to_dict() for row in rows], on_conflict="carpark_id"
        )
    except SupabaseUnavailableError as exc:
        logger.error("batch_predict: Supabase write failed: %s", exc)
        deps.fail_ping(FAIL_REASON_SUPABASE_UNAVAILABLE)
        raise BatchPredictError("supabase_unavailable") from exc

    logger.info("batch_predict: computed %d rows, generated_at=%s", len(rows), generated_at)
    return BatchResult(computed=len(rows), generated_at=generated_at)


def handle_batch_predict(headers: Mapping[str, str], deps: BatchDeps) -> HttpResponse:
    """Authenticate and run a batch-predict request.

    Args:
        headers: Incoming request headers.
        deps: Injected dependencies.

    Returns:
        401 (unauthorized, no compute) on a missing/incorrect shared
        secret; 200 with `{"computed": N, "generated_at": ...}` on success;
        500 with a typed body if Supabase is unavailable (a deliberate,
        well-formed 500 -- distinct from an unhandled "raw" 500 -- per the
        design doc's step-8 contract for this internal, poller-triggered
        endpoint).
    """
    provided_secret = get_header(headers, "x-batch-secret")
    if (
        not provided_secret
        or not deps.batch_shared_secret
        or not hmac.compare_digest(provided_secret, deps.batch_shared_secret)
    ):
        logger.warning("batch_predict: rejected request (missing or invalid x-batch-secret)")
        return HttpResponse(401, {"error": "unauthorized"})

    try:
        result = run_batch_predict(deps)
    except BatchPredictError as exc:
        return HttpResponse(500, {"error": str(exc)})

    return HttpResponse(200, {"computed": result.computed, "generated_at": result.generated_at})
