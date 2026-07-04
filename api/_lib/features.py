"""Feature-vector construction for the LightGBM serving path.

The feature contract's column order is load-bearing -- it must match
training exactly: [dow, slot_of_day, is_holiday, lots_now, lots_15m_ago,
lots_30m_ago, lots_60m_ago], predicting available lots at t+20min
(Premise #2, amended for momentum features).
"""

from __future__ import annotations

from datetime import datetime

from _lib.sg_time import is_holiday, sgt_parts

#: The feature contract's column order, exposed as a constant so batch_logic
#: and tests can assert against it without repeating the literal list.
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
    lots_now: int,
    lots_15m_ago: int,
    lots_30m_ago: int,
    lots_60m_ago: int,
) -> list[float]:
    """Build the LightGBM feature vector for one carpark's prediction.

    Args:
        target_utc: The prediction target time (now + 20 minutes). Time and
            holiday features are computed for THIS instant, not the current
            time -- the model predicts availability at the target time.
        lots_now: The carpark's most recent known available-lots reading.
        lots_15m_ago: Available lots approximately 15 minutes before
            `lots_now`, from `carpark_momentum`.
        lots_30m_ago: Available lots approximately 30 minutes before
            `lots_now`, from `carpark_momentum`.
        lots_60m_ago: Available lots approximately 60 minutes before
            `lots_now`, from `carpark_momentum`.

    Returns:
        A 7-element list of floats in the exact order documented by
        `FEATURE_NAMES`, ready to pass to `numpy.array([...])` and
        `lightgbm.Booster.predict`.
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
