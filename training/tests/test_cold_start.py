"""Tests for gotparking_training.cold_start (Premise #10 exclusion rule).

Covers Test Requirements case 2 ("cold-start carpark excluded (both the 72h
and <10-samples sub-cases)") plus a cross-check against
`api/_lib/batch_logic.py::_is_cold_start`, the serving-side original this
rule must never drift from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gotparking_training.cold_start import is_cold_start

from tests._load_api_module import api_lib_on_path, load_api_lib_module

_NOW = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)


class TestIsColdStart:
    """Direct unit tests for both exclusion sub-cases."""

    def test_no_history_at_all_is_cold_start(self) -> None:
        assert is_cold_start(None, 0, _NOW) is True

    def test_too_young_even_with_many_samples(self) -> None:
        """Sub-case 1: first sample younger than 72h -- excluded regardless
        of how many samples exist (e.g. a burst of dense polling)."""
        first_polled_at = _NOW - timedelta(hours=71, minutes=59)
        assert is_cold_start(first_polled_at, sample_count=10_000, now=_NOW) is True

    def test_too_few_samples_even_if_old_enough(self) -> None:
        """Sub-case 2: old enough (>=72h) but fewer than 10 samples (e.g. a
        very sparse polling history) -- still excluded."""
        first_polled_at = _NOW - timedelta(days=30)
        assert is_cold_start(first_polled_at, sample_count=9, now=_NOW) is True

    def test_old_enough_and_enough_samples_is_not_cold_start(self) -> None:
        first_polled_at = _NOW - timedelta(days=30)
        assert is_cold_start(first_polled_at, sample_count=100, now=_NOW) is False

    def test_exactly_72_hours_is_not_too_young(self) -> None:
        """Boundary: age == 72h exactly is NOT "younger than" 72h."""
        first_polled_at = _NOW - timedelta(hours=72)
        assert is_cold_start(first_polled_at, sample_count=100, now=_NOW) is False

    def test_exactly_10_samples_is_not_too_few(self) -> None:
        """Boundary: sample_count == 10 exactly is NOT "fewer than" 10."""
        first_polled_at = _NOW - timedelta(days=30)
        assert is_cold_start(first_polled_at, sample_count=10, now=_NOW) is False

    def test_one_second_short_of_72_hours_is_too_young(self) -> None:
        first_polled_at = _NOW - timedelta(hours=72) + timedelta(seconds=1)
        assert is_cold_start(first_polled_at, sample_count=100, now=_NOW) is True

    def test_nine_samples_is_too_few(self) -> None:
        first_polled_at = _NOW - timedelta(days=30)
        assert is_cold_start(first_polled_at, sample_count=9, now=_NOW) is True


class TestApiCrossCheck:
    """CRITICAL INTEGRATION CONTRACT: training's is_cold_start must agree
    with api/_lib/batch_logic.py's _is_cold_start (serving side) for every
    scenario -- training and serving must exclude exactly the same
    carparks, or a carpark could be "warmed up" for one side and not the
    other.
    """

    def test_agrees_across_a_grid_of_ages_and_counts(self) -> None:
        with api_lib_on_path():
            api_batch_logic = load_api_lib_module("batch_logic")

            ages_hours = [0, 1, 71, 71.99, 72, 72.01, 100, 24 * 30]
            counts = [0, 1, 9, 10, 11, 1000]

            for age_hours in ages_hours:
                for count in counts:
                    first_at = None if count == 0 else _NOW - timedelta(hours=age_hours)
                    ours = is_cold_start(first_at, count, _NOW)
                    stats = (
                        None
                        if count == 0
                        else api_batch_logic.HistoryStats(
                            first_polled_at=first_at,
                            sample_count=count,
                            capacity=100,
                            live_lots=50,
                        )
                    )
                    theirs = api_batch_logic._is_cold_start(stats, _NOW)
                    assert ours == theirs, (
                        f"disagreement at age_hours={age_hours} count={count}: "
                        f"training={ours} api={theirs}"
                    )
