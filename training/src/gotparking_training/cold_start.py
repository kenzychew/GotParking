"""Cold-start exclusion rule (Premise #10), matching the serving side exactly.

MUST STAY IN SYNC with `api/_lib/batch_logic.py::_is_cold_start`. A carpark
whose first LIVE sample is younger than `COLD_START_MIN_AGE_HOURS`, OR that
has fewer than `COLD_START_MIN_SAMPLES` live samples, is excluded from the
ENTIRE training pipeline for this cycle (no rows from it are used for
training, comparator-fitting, or backtest evaluation) -- matching serving's
refusal to predict for it via the LightGBM/baseline path (it gets the
cold_start forecast state instead).

Critically, "samples" here means rows in `carpark_history` only -- SINPA
pretraining rows must NEVER count toward a carpark's cold-start sample
count (design doc Premise #1, amended: "the serving cold-start threshold
counts LIVE samples only -- 2020-era rows must never mark a carpark as
warmed up"). This module only ever sees live `carpark_history` stats
(`data_loading.py` computes them before SINPA is even considered), so this
invariant holds structurally, not just by convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gotparking_training.config import COLD_START_MIN_AGE_HOURS, COLD_START_MIN_SAMPLES


def is_cold_start(first_polled_at: datetime | None, sample_count: int, now: datetime) -> bool:
    """Decide cold-start exclusion for one carpark, per Premise #10.

    Args:
        first_polled_at: Timestamp of the carpark's earliest live
            `carpark_history` row, or None if it has no live history at
            all (e.g. a carpark added to the whitelist but not yet polled).
        sample_count: Total number of live `carpark_history` rows for this
            carpark. Must never include SINPA pretraining rows.
        now: The current instant (the training run's start time), used to
            compute the carpark's data age.

    Returns:
        True if the carpark should be EXCLUDED from this training cycle
        (either condition below is sufficient on its own):
          * no history at all, or first sample younger than
            `COLD_START_MIN_AGE_HOURS`;
          * fewer than `COLD_START_MIN_SAMPLES` total samples.
    """
    if first_polled_at is None or sample_count == 0:
        return True
    age = now - first_polled_at
    if age < timedelta(hours=COLD_START_MIN_AGE_HOURS):
        return True
    return sample_count < COLD_START_MIN_SAMPLES
