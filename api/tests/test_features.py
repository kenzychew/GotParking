"""Tests for LightGBM feature-vector construction (api/_lib/features.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from _lib.features import FEATURE_NAMES, build_feature_vector
from _lib.sg_time import sgt_parts


class TestBuildFeatureVector:
    """Tests for build_feature_vector()."""

    def test_returns_seven_features_in_contract_order(self) -> None:
        target = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)  # Mon 11:00 SGT

        vector = build_feature_vector(target, lots_now=100, lots_15m_ago=95,
                                       lots_30m_ago=90, lots_60m_ago=80)

        assert len(FEATURE_NAMES) == 7
        assert len(vector) == 7

    def test_dow_and_slot_match_sgt_parts(self) -> None:
        target = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)  # Sat 02:00 SGT
        expected_dow, expected_slot = sgt_parts(target)

        vector = build_feature_vector(target, 1, 2, 3, 4)

        assert vector[0] == float(expected_dow)
        assert vector[1] == float(expected_slot)

    def test_holiday_flag_is_one_on_a_holiday(self) -> None:
        # National Day 2026-08-09, well inside the SGT calendar day.
        target = datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc)  # 14:00 SGT

        vector = build_feature_vector(target, 1, 2, 3, 4)

        assert vector[2] == 1.0

    def test_holiday_flag_is_zero_on_an_ordinary_day(self) -> None:
        target = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)  # Mon 11:00 SGT

        vector = build_feature_vector(target, 1, 2, 3, 4)

        assert vector[2] == 0.0

    def test_lot_counts_are_passed_through_in_order(self) -> None:
        target = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)

        vector = build_feature_vector(
            target, lots_now=111, lots_15m_ago=222, lots_30m_ago=333, lots_60m_ago=444
        )

        assert vector[3:] == [111.0, 222.0, 333.0, 444.0]

    def test_all_values_are_floats(self) -> None:
        target = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)

        vector = build_feature_vector(target, 1, 2, 3, 4)

        assert all(isinstance(v, float) for v in vector)
