"""Load live `carpark_history` and apply the cold-start exclusion.

Test Requirements case 1 (load happy path) and case 2 (cold-start carpark
excluded) live here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from gotparking_training.cold_start import is_cold_start
from gotparking_training.repository import CarparkInfo
from gotparking_training.series import TimedSample
from gotparking_training.supabase_rest import SupabaseClient, parse_timestamp

logger = logging.getLogger(__name__)


def load_carpark_history(db: SupabaseClient) -> dict[str, list[TimedSample]]:
    """Load every `carpark_history` row, paginated, grouped by carpark.

    Args:
        db: Supabase client.

    Returns:
        A dict mapping carpark_id -> its list of TimedSample readings (not
        necessarily sorted -- callers that need sorted order, such as
        `series.build_rows_from_series`, sort internally). A carpark with
        zero rows simply does not appear as a key here; callers should
        treat a missing key the same as an empty list.
    """
    rows = db.select_all(
        "carpark_history",
        params={"select": "carpark_id,polled_at,available_lots", "order": "polled_at.asc"},
    )
    by_carpark: dict[str, list[TimedSample]] = {}
    for row in rows:
        by_carpark.setdefault(row["carpark_id"], []).append(
            TimedSample(parse_timestamp(row["polled_at"]), float(row["available_lots"]))
        )
    logger.info(
        "load_carpark_history: loaded %d rows across %d carparks", len(rows), len(by_carpark)
    )
    return by_carpark


def compute_history_stats(series: Sequence[TimedSample]) -> tuple[datetime | None, int]:
    """Compute (first_polled_at, sample_count) for one carpark's series.

    Args:
        series: The carpark's readings (order does not matter).

    Returns:
        `(None, 0)` if `series` is empty, else `(min(timestamps), len(series))`.
    """
    if not series:
        return None, 0
    return min(sample.at for sample in series), len(series)


def is_system_wide_training_eligible(
    carpark: CarparkInfo, first_promotion_at: datetime | None,
) -> bool:
    """Decide whether `carpark` clears the one-time T2 eligibility gate.

    This is INDEPENDENT of cold-start/live-sample status (see
    `is_cold_start` for that): it only encodes the "which carparks are
    allowed to influence the pooled model at all, right now" rule that
    protects the original 10 seed carparks' first-ever promotion from
    being diluted by newly-onboarded, still-noisy carparks. Once ANY
    promotion has ever happened system-wide (`first_promotion_at` is not
    None), every carpark that separately clears cold-start becomes
    eligible -- matching the original (pre-T2) design, where this gate
    stops mattering after the first promotion rather than becoming a
    permanent per-carpark quality bar.

    Used both by `filter_eligible_carparks` (live training rows) and by
    `train.run`'s SINPA-mapping selection, so the two pooling paths can
    never silently diverge on this rule.

    Args:
        carpark: The carpark whitelist entry to check.
        first_promotion_at: `model_config.first_promotion_at` (from
            `repository.load_first_promotion_at`) -- None if no promotion
            has ever happened system-wide yet.

    Returns:
        True if `carpark.is_original_seed` is True, OR `first_promotion_at`
        is not None.
    """
    return carpark.is_original_seed or first_promotion_at is not None


def filter_eligible_carparks(
    carparks: Sequence[CarparkInfo],
    history: dict[str, list[TimedSample]],
    now: datetime,
    first_promotion_at: datetime | None,
) -> list[CarparkInfo]:
    """Apply the cold-start exclusion (Premise #10) AND the T2 one-time
    system-wide eligibility gate to the carpark whitelist.

    Args:
        carparks: The active-carpark whitelist (from
            `repository.load_active_carparks`).
        history: Live history grouped by carpark_id (from
            `load_carpark_history`). A carpark absent from this dict is
            treated as having zero samples (never polled yet).
        now: The training run's start instant, used to compute each
            carpark's data age.
        first_promotion_at: `model_config.first_promotion_at` (from
            `repository.load_first_promotion_at`) -- None if no promotion
            has ever happened system-wide yet. See
            `is_system_wide_training_eligible`.

    Returns:
        The subset of `carparks` that clear BOTH the cold-start threshold
        AND the system-wide eligibility gate, in the same order as the
        input. Excluded carparks are logged at INFO with the reason so a
        training run's logs explain exactly which carparks were skipped
        and why.
    """
    eligible: list[CarparkInfo] = []
    for carpark in carparks:
        series = history.get(carpark.carpark_id, [])
        first_at, count = compute_history_stats(series)
        if is_cold_start(first_at, count, now):
            logger.info(
                "excluding cold-start carpark carpark_id=%s name=%s first_polled_at=%s "
                "sample_count=%d",
                carpark.carpark_id, carpark.name, first_at, count,
            )
            continue
        if not is_system_wide_training_eligible(carpark, first_promotion_at):
            logger.info(
                "excluding non-seed carpark carpark_id=%s name=%s: no system-wide "
                "promotion has happened yet (model_config.first_promotion_at is null)",
                carpark.carpark_id, carpark.name,
            )
            continue
        eligible.append(carpark)
    return eligible
