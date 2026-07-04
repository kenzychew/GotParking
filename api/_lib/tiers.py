"""Capacity-relative availability tiers (Design Details: color-coding).

ratio = forecast_lots / capacity; >=0.30 "plenty", >=0.10 "limited", else
"very_limited". Thresholds are capacity-relative, not absolute lot counts,
because the 10 seed carparks span a 27-to-396-lot capacity range (T1) -- a
fixed absolute cutoff would misclassify most of them (e.g. 30 free lots is
"plenty" at a ~128-lot-capacity carpark but "very limited" at a
~455-lot-capacity one).
"""

from __future__ import annotations

from _lib.config import TIER_LIMITED_RATIO, TIER_PLENTY_RATIO

TIER_PLENTY = "plenty"
TIER_LIMITED = "limited"
TIER_VERY_LIMITED = "very_limited"


def compute_tier(forecast_lots: float, capacity: float) -> str:
    """Classify a forecast into a capacity-relative availability tier.

    Args:
        forecast_lots: The (possibly model-raw, possibly negative) forecast
            lot count. Clamped to >=0 before computing the ratio, per the
            explicit "clamp forecast to >=0 first" instruction.
        capacity: The carpark's known capacity (max observed
            `available_lots` across its history).

    Returns:
        One of "plenty", "limited", "very_limited".

    Note:
        A non-positive capacity is a defensive edge case -- it should not
        occur once a carpark has cleared cold-start, since capacity is
        derived from at least one real reading -- but is treated as
        "very_limited" rather than raising, since "plenty"/"limited" cannot
        be responsibly claimed without a meaningful capacity denominator.
    """
    clamped = max(forecast_lots, 0.0)
    if capacity <= 0:
        return TIER_VERY_LIMITED
    ratio = clamped / capacity
    if ratio >= TIER_PLENTY_RATIO:
        return TIER_PLENTY
    if ratio >= TIER_LIMITED_RATIO:
        return TIER_LIMITED
    return TIER_VERY_LIMITED
