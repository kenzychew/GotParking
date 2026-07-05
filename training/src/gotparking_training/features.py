"""Feature-vector construction for the LightGBM training path.

MUST STAY IN SYNC with `api/_lib/features.py` -- the other half of the
CRITICAL INTEGRATION CONTRACT. The feature contract's column order is
load-bearing: [dow, slot_of_day, is_holiday, lots_now, lots_15m_ago,
lots_30m_ago, lots_60m_ago], predicting available lots at t+20min
(Premise #2, amended for momentum features). `tests/test_features.py`
cross-checks `FEATURE_NAMES` and `build_feature_vector`'s output against
api's copy directly, in addition to this deliberate copy-with-a-
must-stay-in-sync-comment.

Note the feature semantics that are easy to get backwards: `target_utc` is
the PREDICTION TARGET time (base_time + 20 minutes), not the observation
time -- the dow/slot_of_day/is_holiday features describe the instant being
predicted, matching exactly how api/_lib/batch_logic.py calls
`build_feature_vector(target, ...)` at serving time (`target = now + 20min`).
Training must anchor every row's time-derived features the same way, or the
model would learn a systematically time-shifted relationship it never sees
at serving time.
"""

from __future__ import annotations

from datetime import datetime

from gotparking_training.sg_time import is_holiday, sgt_parts

#: The feature contract's column order, exposed as a constant so the
#: dataset-building and serving-parity tests can assert against it without
#: repeating the literal list. MUST match api/_lib/features.py::FEATURE_NAMES
#: exactly (order and names).
FEATURE_NAMES: tuple[str, ...] = (
    "dow",
    "slot_of_day",
    "is_holiday",
    "lots_now",
    "lots_15m_ago",
    "lots_30m_ago",
    "lots_60m_ago",
)


def build_feature_vector(
    target_utc: datetime,
    lots_now: float,
    lots_15m_ago: float,
    lots_30m_ago: float,
    lots_60m_ago: float,
) -> list[float]:
    """Build one LightGBM feature vector, matching the serving contract.

    Args:
        target_utc: The prediction target time (base time + 20 minutes).
            Time and holiday features are computed for THIS instant, not
            the observation time -- the model predicts availability at the
            target time.
        lots_now: The carpark's available-lots reading at the observation
            (base) time.
        lots_15m_ago: Available lots approximately 15 minutes before the
            observation time, from the offline momentum join.
        lots_30m_ago: Available lots approximately 30 minutes before the
            observation time, from the offline momentum join.
        lots_60m_ago: Available lots approximately 60 minutes before the
            observation time, from the offline momentum join.

    Returns:
        A 7-element list of floats in the exact order documented by
        `FEATURE_NAMES`, ready to pass to `numpy.array([...])` and
        `lightgbm.Dataset`/`Booster.predict`.
    """
    dow, slot_of_day = sgt_parts(target_utc)
    holiday = 1.0 if is_holiday(target_utc) else 0.0
    return [
        float(dow),
        float(slot_of_day),
        holiday,
        float(lots_now),
        float(lots_15m_ago),
        float(lots_30m_ago),
        float(lots_60m_ago),
    ]
