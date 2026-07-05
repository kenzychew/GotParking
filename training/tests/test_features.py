"""Tests for gotparking_training.features, including the CRITICAL
INTEGRATION CONTRACT cross-check against api/_lib/features.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gotparking_training.features import FEATURE_NAMES, build_feature_vector

from tests._load_api_module import api_lib_on_path, load_api_lib_module


class TestBuildFeatureVector:
    """Tests for the training-side feature vector builder."""

    def test_column_order_and_count(self) -> None:
        """FEATURE_NAMES has exactly 7 columns in the documented order."""
        assert FEATURE_NAMES == (
            "dow",
            "slot_of_day",
            "is_holiday",
            "lots_now",
            "lots_15m_ago",
            "lots_30m_ago",
            "lots_60m_ago",
        )

    def test_returns_seven_floats(self) -> None:
        target = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)  # Mon 11:00 SGT
        vector = build_feature_vector(target, lots_now=10, lots_15m_ago=12, lots_30m_ago=15,
                                       lots_60m_ago=20)

        assert len(vector) == 7
        assert all(isinstance(v, float) for v in vector)

    def test_features_describe_the_target_instant_not_the_base_instant(self) -> None:
        """dow/slot_of_day/is_holiday must describe target_utc (base+20min),
        not whatever "now" happens to be -- this is the easy-to-invert
        detail the module docstring warns about.
        """
        # Fri 23:52 SGT (Fri 15:52 UTC) is dow=4, slot 95 -- but the target
        # 20 minutes later crosses into Sat 00:12 SGT, dow=5, slot 0.
        target = datetime(2026, 7, 3, 16, 12, tzinfo=timezone.utc)  # Sat 00:12 SGT
        vector = build_feature_vector(target, lots_now=1, lots_15m_ago=1, lots_30m_ago=1,
                                       lots_60m_ago=1)

        assert vector[0] == 5.0  # Saturday, not Friday
        assert vector[1] == 0.0  # slot 0, not slot 95

    def test_lot_values_pass_through_as_floats_in_order(self) -> None:
        target = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)
        vector = build_feature_vector(target, lots_now=100, lots_15m_ago=95, lots_30m_ago=90,
                                       lots_60m_ago=80)

        assert vector[3:] == [100.0, 95.0, 90.0, 80.0]


class TestApiCrossCheck:
    """CRITICAL INTEGRATION CONTRACT: training's features.py must agree
    bit-for-bit with api/_lib/features.py (the serving side).
    """

    def test_feature_names_are_identical(self) -> None:
        with api_lib_on_path():
            api_features = load_api_lib_module("features")

        assert FEATURE_NAMES == api_features.FEATURE_NAMES

    def test_build_feature_vector_agrees_on_sample_inputs(self) -> None:
        with api_lib_on_path():
            api_features = load_api_lib_module("features")

        samples = [
            (datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc), 10, 12, 15, 20),
            (datetime(2026, 7, 3, 16, 12, tzinfo=timezone.utc), 1, 2, 3, 4),
            (datetime(2025, 12, 31, 16, 5, tzinfo=timezone.utc), 0, 0, 0, 0),
            (datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc), 250, 240, 230, 220),
        ]
        for target, now_v, m15, m30, m60 in samples:
            ours = build_feature_vector(target, now_v, m15, m30, m60)
            theirs = api_features.build_feature_vector(
                target, lots_now=now_v, lots_15m_ago=m15, lots_30m_ago=m30, lots_60m_ago=m60
            )
            assert ours == theirs, f"feature vector disagreement for target={target.isoformat()}"
