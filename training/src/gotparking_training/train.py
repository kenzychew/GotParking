"""Weekly training job orchestration (design doc T5): load, train, gate, promote.

Pipeline (design doc T5 steps 1-9):
  1. Load live history, exclude cold-start carparks.
  2/3. Build momentum/label rows via the shared SGT/feature contract.
  4. SINPA pretraining (best-effort, never fatal).
  5. Train the candidate strictly on pre-holdout data.
  6. Gate leakage-free against historical-average AND persistence
     (first_promotion phase) or the incumbent (retrain phase).
  7. On promotion: upload the artifact, then flip model_config.
  8. Always insert a training_runs audit row.
  9. Ping healthchecks on success; /fail on crash or upload failure.

See `run()` for the full pipeline and `main()` for the process entry point
(env-var settings, real SupabaseREST client, real healthchecks pings).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from gotparking_training import gate
from gotparking_training.comparators import (
    build_carpark_mean,
    build_historical_average,
    predict_historical_average,
    predict_persistence,
)
from gotparking_training.config import (
    DEFAULT_LGBM_PARAMS,
    DEFAULT_NUM_BOOST_ROUND,
    FAIL_REASON_MODEL_UPLOAD_FAILED,
    FAIL_REASON_TRAINING_CRASH,
    HOLDOUT_DAYS,
    load_settings,
)
from gotparking_training.data_loading import filter_eligible_carparks, load_carpark_history
from gotparking_training.healthchecks import ping_fail, ping_success
from gotparking_training.modeling import mean_absolute_error, predict, train_candidate
from gotparking_training.model_io import (
    download_incumbent_booster,
    make_version,
    upload_model_artifact,
)
from gotparking_training.repository import (
    insert_training_run,
    load_active_carparks,
    load_active_model_version,
    promote_model_config,
)
from gotparking_training.series import TrainingRow, build_rows_from_series
from gotparking_training.sinpa import SinpaCarparkMapping, SinpaUnavailableError
from gotparking_training.sinpa import load_sinpa_training_rows as _real_load_sinpa_training_rows
from gotparking_training.supabase_rest import SupabaseClient, SupabaseREST, SupabaseUnavailableError

logger = logging.getLogger(__name__)


class TrainingJobError(Exception):
    """An already-alerted failure: the raiser has already fired the
    appropriate /fail ping with a precise reason before raising this, so
    the top-level handler (`main`) must NOT fire a second, generic ping.
    """


@dataclass(frozen=True)
class RunResult:
    """Outcome of one training run.

    Attributes:
        candidate_version: The version identifier assigned to this training
            CYCLE (computed from the cycle's start time up front), whether
            or not a candidate was ultimately trained, backtested, or
            promoted under it -- every cycle gets one, since every cycle
            produces a `training_runs` audit row (design doc step 8:
            "ALWAYS insert a training_runs row").
        phase: "first_promotion" or "retrain" (see gate.py), computed from
            `model_config.active_model_version` up front -- always present,
            even for a cycle that stops before training a candidate.
        promoted: Whether a candidate was promoted this cycle.
        mae_candidate: Candidate MAE on holdout, or None if a candidate was
            never trained/backtested this cycle (a skipped cycle).
        mae_baseline: Historical-average comparator MAE, or None.
        mae_persistence: Persistence comparator MAE, or None.
        mae_incumbent: Incumbent MAE (retrain phase only), or None.
        used_sinpa: Whether SINPA pretraining data was used this cycle.
        notes: Free-text summary of what happened.
    """

    candidate_version: str
    phase: str
    promoted: bool
    mae_candidate: float | None
    mae_baseline: float | None
    mae_persistence: float | None
    mae_incumbent: float | None
    used_sinpa: bool
    notes: str


@dataclass
class TrainDeps:
    """Injectable dependencies for `run()`.

    Attributes:
        db: Supabase client (real SupabaseREST in production, FakeSupabaseDB
            in tests).
        clock: Zero-arg callable returning the current UTC instant.
        fail_ping: Callable taking a reason string, wired to fire a
            healthchecks `/fail` ping.
        load_sinpa: Callable matching `sinpa.load_sinpa_training_rows`'s
            signature (mappings -> {carpark_id: [TrainingRow]}). Defaults
            to the real SINPA loader; tests inject a fake to avoid any
            network access.
        lgbm_params: LightGBM hyperparameters.
        num_boost_round: Boosting rounds for both the pretrain and
            live/fine-tune stages.
        holdout_days: Size of the held-out window, in days.
    """

    db: SupabaseClient
    clock: Callable[[], datetime]
    fail_ping: Callable[[str], None]
    load_sinpa: Callable[
        [Sequence[SinpaCarparkMapping]], dict[str, list[TrainingRow]]
    ] = _real_load_sinpa_training_rows
    lgbm_params: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_LGBM_PARAMS))
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND
    holdout_days: int = HOLDOUT_DAYS


def _skip_result(
    db: SupabaseClient,
    candidate_version: str,
    phase: str,
    used_sinpa: bool,
    notes: str,
    ran_at: datetime,
) -> RunResult:
    """Record and return a RunResult for a cycle that stops before training
    a candidate (e.g. no eligible carparks, or an empty holdout window).

    Per the design doc's step 8 ("ALWAYS insert a training_runs row"), a
    skipped cycle is still a completed cycle from the audit trail's point
    of view -- without this, a run of weeks where every cycle skips (e.g.
    a data-quality issue upstream) would be silently invisible in
    `training_runs`, indistinguishable from the job never having run at
    all (which the healthchecks weekly-ping check already covers
    separately; this is the OTHER failure mode -- ran, but had nothing to
    train on).
    """
    logger.warning("training run skipped this cycle: %s", notes)
    insert_training_run(
        db,
        candidate_version=candidate_version,
        phase=phase,
        mae_candidate=None,
        mae_baseline=None,
        mae_persistence=None,
        mae_incumbent=None,
        used_sinpa=used_sinpa,
        promoted=False,
        notes=notes,
        ran_at=ran_at,
    )
    return RunResult(
        candidate_version=candidate_version,
        phase=phase,
        promoted=False,
        mae_candidate=None,
        mae_baseline=None,
        mae_persistence=None,
        mae_incumbent=None,
        used_sinpa=used_sinpa,
        notes=notes,
    )


def run(deps: TrainDeps) -> RunResult:
    """Run one full weekly training cycle.

    Args:
        deps: Injected dependencies.

    Returns:
        The cycle's outcome.

    Raises:
        TrainingJobError: If the model artifact upload fails after its
            retry -- a /fail ping (reason=MODEL_UPLOAD_FAILED) has already
            been fired, and a best-effort training_runs row has already
            been inserted, before this is raised.
        SupabaseUnavailableError: If any OTHER Supabase read/write fails
            after its retry (history load, carparks load, incumbent
            download, training_runs insert). Propagates uncaught to
            `main()`'s generic crash handler, which fires a
            TRAINING_CRASH /fail ping -- these are treated as unexpected
            crashes, not the specific "upload failed, abort promotion"
            case that `TrainingJobError` represents.
    """
    now = deps.clock()
    logger.info("training run started at %s", now.isoformat())

    # Computed up front, before any early-exit check, so every possible
    # return path -- including a cycle that skips before training a
    # candidate -- has a real (candidate_version, phase) pair to record in
    # the training_runs audit row (design doc step 8: "ALWAYS insert a
    # training_runs row"). `candidate_version` is a per-cycle identifier
    # here, not a claim that an artifact was uploaded under it -- `notes`
    # and `promoted` disambiguate what actually happened.
    active_version = load_active_model_version(deps.db)
    phase = gate.determine_phase(active_version)
    candidate_version = make_version(now)

    all_carparks = load_active_carparks(deps.db)
    history = load_carpark_history(deps.db)
    eligible_carparks = filter_eligible_carparks(all_carparks, history, now)

    if not eligible_carparks:
        return _skip_result(
            deps.db, candidate_version, phase, False,
            "no eligible carparks (all cold-start or no carparks)", now,
        )

    live_rows: list[TrainingRow] = []
    for carpark in eligible_carparks:
        live_rows.extend(build_rows_from_series(carpark.carpark_id, history[carpark.carpark_id]))

    if not live_rows:
        return _skip_result(
            deps.db, candidate_version, phase, False,
            "no labeled rows survived the join tolerance", now,
        )

    cutoff = max(row.target_time for row in live_rows) - timedelta(days=deps.holdout_days)
    pre_holdout = [row for row in live_rows if row.target_time < cutoff]
    holdout = [row for row in live_rows if row.target_time >= cutoff]

    # SINPA pretraining is independent of live cold-start status (Premise
    # #1: the cold-start gate only ever counts LIVE samples) -- every
    # SINPA-mapped carpark in the whitelist contributes, not just the
    # currently-eligible ones.
    sinpa_mappings = [
        SinpaCarparkMapping(carpark.carpark_id, carpark.sinpa_index)
        for carpark in all_carparks
        if carpark.sinpa_index is not None
    ]
    used_sinpa = False
    sinpa_rows: list[TrainingRow] = []
    if sinpa_mappings:
        try:
            sinpa_by_carpark = deps.load_sinpa(sinpa_mappings)
            for rows in sinpa_by_carpark.values():
                sinpa_rows.extend(rows)
            used_sinpa = bool(sinpa_rows)
        except SinpaUnavailableError as exc:
            logger.warning("SINPA pretraining unavailable this cycle, falling back to "
                            "live-only training: %s", exc)
    else:
        logger.info("no SINPA-mapped carparks in the whitelist; training live-only")

    if not pre_holdout:
        return _skip_result(
            deps.db, candidate_version, phase, used_sinpa,
            "no pre-holdout rows to train on", now,
        )

    candidate = train_candidate(
        pre_holdout,
        sinpa_rows if used_sinpa else None,
        params=deps.lgbm_params,
        num_boost_round=deps.num_boost_round,
    )

    if not holdout:
        return _skip_result(
            deps.db, candidate_version, phase, used_sinpa,
            "empty holdout window across all carparks", now,
        )

    hist_avg_table = build_historical_average(pre_holdout)
    carpark_mean_table = build_carpark_mean(pre_holdout)
    actuals = [row.label for row in holdout]

    mae_candidate = mean_absolute_error(list(predict(candidate, holdout)), actuals)
    baseline_preds = [
        predict_historical_average(hist_avg_table, carpark_mean_table, row) for row in holdout
    ]
    mae_baseline = mean_absolute_error(baseline_preds, actuals)
    persistence_preds = [predict_persistence(row) for row in holdout]
    mae_persistence = mean_absolute_error(persistence_preds, actuals)

    mae_incumbent: float | None = None
    if phase == gate.PHASE_RETRAIN:
        assert active_version is not None  # guaranteed by determine_phase
        incumbent = download_incumbent_booster(deps.db, active_version)
        mae_incumbent = mean_absolute_error(list(predict(incumbent, holdout)), actuals)

    promoted = gate.decide_promotion(phase, mae_candidate, mae_baseline, mae_persistence,
                                      mae_incumbent)

    logger.info(
        "backtest complete: phase=%s mae_candidate=%.4f mae_baseline=%.4f "
        "mae_persistence=%.4f mae_incumbent=%s promoted=%s",
        phase, mae_candidate, mae_baseline, mae_persistence, mae_incumbent, promoted,
    )

    if promoted:
        try:
            upload_model_artifact(deps.db, candidate_version, candidate)
        except SupabaseUnavailableError as exc:
            logger.error("model artifact upload failed after retry; aborting promotion: %s", exc)
            deps.fail_ping(FAIL_REASON_MODEL_UPLOAD_FAILED)
            insert_training_run(
                deps.db,
                candidate_version=candidate_version,
                phase=phase,
                mae_candidate=mae_candidate,
                mae_baseline=mae_baseline,
                mae_persistence=mae_persistence,
                mae_incumbent=mae_incumbent,
                used_sinpa=used_sinpa,
                promoted=False,
                notes="promotion aborted: model artifact upload failed",
                ran_at=now,
            )
            raise TrainingJobError("model artifact upload failed") from exc
        promote_model_config(deps.db, candidate_version, now)
        notes = "promoted"
    else:
        notes = "did not clear the promotion gate"

    insert_training_run(
        deps.db,
        candidate_version=candidate_version,
        phase=phase,
        mae_candidate=mae_candidate,
        mae_baseline=mae_baseline,
        mae_persistence=mae_persistence,
        mae_incumbent=mae_incumbent,
        used_sinpa=used_sinpa,
        promoted=promoted,
        notes=notes,
        ran_at=now,
    )

    return RunResult(
        candidate_version=candidate_version,
        phase=phase,
        promoted=promoted,
        mae_candidate=mae_candidate,
        mae_baseline=mae_baseline,
        mae_persistence=mae_persistence,
        mae_incumbent=mae_incumbent,
        used_sinpa=used_sinpa,
        notes=notes,
    )


def main() -> int:
    """Process entry point: load env settings, run the pipeline, ping.

    Returns:
        0 on success (whether or not a candidate was promoted -- "no
        promotion" is a normal, successful outcome); 1 on any failure.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    # Read the ping URL directly from the environment BEFORE load_settings(),
    # so a misconfigured/missing required secret (e.g. SUPABASE_URL) can
    # still fire a /fail ping below -- if this lived only on `settings`,
    # a config error would crash with an unhandled exception and no alert,
    # exactly the silent-failure gap Premise #8 exists to close.
    healthchecks_url = os.environ.get("HEALTHCHECKS_TRAINING_PING_URL", "").strip() or None

    try:
        settings = load_settings()
        healthchecks_url = settings.healthchecks_training_ping_url
        db: SupabaseClient = SupabaseREST(settings.supabase_url,
                                           settings.supabase_service_role_key)
    except Exception as exc:  # noqa: BLE001 - top-level guard, must catch everything
        logger.exception("training job failed to start (configuration error)")
        ping_fail(healthchecks_url, f"{FAIL_REASON_TRAINING_CRASH}: {exc}")
        return 1

    deps = TrainDeps(
        db=db,
        clock=lambda: datetime.now(timezone.utc),
        fail_ping=lambda reason: ping_fail(healthchecks_url, reason),
    )
    try:
        try:
            result = run(deps)
        except TrainingJobError as exc:
            logger.error("training job aborted: %s", exc)
            return 1
        except Exception as exc:  # noqa: BLE001 - top-level guard, must catch everything
            logger.exception("training job crashed unexpectedly")
            ping_fail(healthchecks_url, f"{FAIL_REASON_TRAINING_CRASH}: {exc}")
            return 1
    finally:
        db.close()

    ping_success(healthchecks_url)
    logger.info("training run complete: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
