"""Tests for gotparking_training.gate (two-phase promotion decision).

Covers Test Requirements cases 7-11: beats-both-comparators promotion,
fails-either rejection, the exact phase-1 10% boundary, phase-2
within-epsilon promotion, and phase-2 worse-by->2% rejection.
"""

from __future__ import annotations

import pytest

from gotparking_training.gate import (
    PHASE_FIRST_PROMOTION,
    PHASE_RETRAIN,
    decide_promotion,
    determine_phase,
)


class TestDeterminePhase:
    def test_none_active_version_is_first_promotion(self) -> None:
        assert determine_phase(None) == PHASE_FIRST_PROMOTION

    def test_any_active_version_is_retrain(self) -> None:
        assert determine_phase("lgbm_20260628_050000") == PHASE_RETRAIN


class TestPhaseOnePromotion:
    """Test Requirements case 7: beats both comparators -> promotes."""

    def test_beats_both_comparators_promotes(self) -> None:
        # 20% better than both -- comfortably past the 10% bar.
        promoted = decide_promotion(
            PHASE_FIRST_PROMOTION,
            mae_candidate=8.0,
            mae_baseline=10.0,
            mae_persistence=10.0,
            mae_incumbent=None,
        )
        assert promoted is True

    def test_fails_baseline_only_does_not_promote(self) -> None:
        """Test Requirements case 8: fails either comparator -> no promote."""
        promoted = decide_promotion(
            PHASE_FIRST_PROMOTION,
            mae_candidate=9.5,  # beats persistence (10% better) but not baseline
            mae_baseline=10.0,
            mae_persistence=100.0,
            mae_incumbent=None,
        )
        assert promoted is False

    def test_fails_persistence_only_does_not_promote(self) -> None:
        promoted = decide_promotion(
            PHASE_FIRST_PROMOTION,
            mae_candidate=9.5,  # beats baseline (10% better) but not persistence
            mae_baseline=100.0,
            mae_persistence=10.0,
            mae_incumbent=None,
        )
        assert promoted is False

    def test_fails_both_does_not_promote(self) -> None:
        promoted = decide_promotion(
            PHASE_FIRST_PROMOTION,
            mae_candidate=9.5,
            mae_baseline=10.0,
            mae_persistence=10.0,
            mae_incumbent=None,
        )
        assert promoted is False

    def test_exactly_at_10_percent_boundary_promotes(self) -> None:
        """Test Requirements case 9: phase-1 margin exactly at the 10%
        boundary. mae_candidate == 0.9 * comparator must still promote
        (the design doc specifies <=, not strict <)."""
        promoted = decide_promotion(
            PHASE_FIRST_PROMOTION,
            mae_candidate=9.0,  # exactly 0.9 * 10.0
            mae_baseline=10.0,
            mae_persistence=10.0,
            mae_incumbent=None,
        )
        assert promoted is True

    def test_just_worse_than_10_percent_boundary_does_not_promote(self) -> None:
        promoted = decide_promotion(
            PHASE_FIRST_PROMOTION,
            mae_candidate=9.001,  # just over 0.9 * 10.0
            mae_baseline=10.0,
            mae_persistence=10.0,
            mae_incumbent=None,
        )
        assert promoted is False


class TestPhaseTwoRetrain:
    """Test Requirements cases 10-11: phase-2 epsilon boundary."""

    def test_better_than_incumbent_promotes(self) -> None:
        promoted = decide_promotion(
            PHASE_RETRAIN,
            mae_candidate=8.0,
            mae_baseline=999.0,
            mae_persistence=999.0,
            mae_incumbent=10.0,
        )
        assert promoted is True

    def test_within_epsilon_worse_still_promotes(self) -> None:
        """Test Requirements case 10: within epsilon -> promotes."""
        promoted = decide_promotion(
            PHASE_RETRAIN,
            mae_candidate=10.1,  # 1% worse than incumbent, within the 2% bar
            mae_baseline=999.0,
            mae_persistence=999.0,
            mae_incumbent=10.0,
        )
        assert promoted is True

    def test_exactly_at_2_percent_worse_boundary_still_promotes(self) -> None:
        """Boundary: mae_candidate == 1.02 * mae_incumbent exactly.
        Rejection is a strict '>', so exactly-2%-worse still promotes."""
        promoted = decide_promotion(
            PHASE_RETRAIN,
            mae_candidate=10.2,  # exactly 1.02 * 10.0
            mae_baseline=999.0,
            mae_persistence=999.0,
            mae_incumbent=10.0,
        )
        assert promoted is True

    def test_worse_than_2_percent_rejected(self) -> None:
        """Test Requirements case 11: worse by >2% -> rejected."""
        promoted = decide_promotion(
            PHASE_RETRAIN,
            mae_candidate=10.21,  # just over 1.02 * 10.0
            mae_baseline=999.0,
            mae_persistence=999.0,
            mae_incumbent=10.0,
        )
        assert promoted is False

    def test_retrain_without_incumbent_mae_raises(self) -> None:
        with pytest.raises(ValueError, match="mae_incumbent"):
            decide_promotion(
                PHASE_RETRAIN,
                mae_candidate=8.0,
                mae_baseline=10.0,
                mae_persistence=10.0,
                mae_incumbent=None,
            )
