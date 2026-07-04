"""Tests for capacity-relative availability tiers (api/_lib/tiers.py)."""

from __future__ import annotations

import pytest

from _lib.tiers import TIER_LIMITED, TIER_PLENTY, TIER_VERY_LIMITED, compute_tier


class TestComputeTier:
    """Tests for compute_tier()."""

    def test_ratio_at_or_above_030_is_plenty(self) -> None:
        assert compute_tier(forecast_lots=30, capacity=100) == TIER_PLENTY  # exactly 0.30
        assert compute_tier(forecast_lots=90, capacity=100) == TIER_PLENTY

    def test_ratio_at_or_above_010_below_030_is_limited(self) -> None:
        assert compute_tier(forecast_lots=10, capacity=100) == TIER_LIMITED  # exactly 0.10
        assert compute_tier(forecast_lots=29, capacity=100) == TIER_LIMITED

    def test_ratio_below_010_is_very_limited(self) -> None:
        assert compute_tier(forecast_lots=9, capacity=100) == TIER_VERY_LIMITED
        assert compute_tier(forecast_lots=0, capacity=100) == TIER_VERY_LIMITED

    def test_negative_forecast_is_clamped_to_zero(self) -> None:
        assert compute_tier(forecast_lots=-5, capacity=100) == TIER_VERY_LIMITED

    @pytest.mark.parametrize("capacity", [0, -1])
    def test_non_positive_capacity_is_very_limited_not_a_crash(self, capacity: int) -> None:
        assert compute_tier(forecast_lots=50, capacity=capacity) == TIER_VERY_LIMITED

    def test_same_absolute_count_classifies_differently_by_capacity(self) -> None:
        # This is the whole point of capacity-relative tiers: the design doc
        # notes a fixed absolute cutoff would misclassify carparks spanning
        # a 27-to-396-lot range (T1). The same 40 free lots reads as
        # "plenty" at a 313@Somerset-scale (~128 capacity) carpark --
        # 40/128 = 0.3125 -- but "very_limited" at a VivoCity P3-scale
        # (~455 capacity) carpark -- 40/455 = 0.088.
        assert compute_tier(forecast_lots=40, capacity=128) == TIER_PLENTY
        assert compute_tier(forecast_lots=40, capacity=455) == TIER_VERY_LIMITED
