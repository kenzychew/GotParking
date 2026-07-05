"""Tests for gotparking_training.series: the momentum/label join logic.

Covers Test Requirements case 4 ("label join tolerance incl. gap -> row
dropped") and case 5's momentum-from-offsets happy path, plus explicit
tolerance-boundary and momentum-gap coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gotparking_training.features import build_feature_vector
from gotparking_training.series import TimedSample, _nearest_value, build_rows_from_series

_START = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)  # a Monday, clean SGT slot boundary


def _clean_series(n: int, step_minutes: int = 5, base_value: float = 100.0) -> list[TimedSample]:
    """A synthetic series of `n` readings, `step_minutes` apart, value = base_value + index."""
    return [
        TimedSample(_START + timedelta(minutes=step_minutes * i), base_value + i)
        for i in range(n)
    ]


class TestNearestValue:
    """Direct tests of the nearest-within-tolerance lookup helper."""

    def test_exact_match(self) -> None:
        series = _clean_series(5)
        epochs = [s.at.timestamp() for s in series]

        value = _nearest_value(series, epochs, series[2].at, timedelta(minutes=2.5))

        assert value == 102.0

    def test_within_tolerance_boundary_inclusive(self) -> None:
        series = [TimedSample(_START, 100.0)]
        epochs = [s.at.timestamp() for s in series]
        target = _START + timedelta(minutes=2, seconds=30)  # exactly 2.5 min away

        value = _nearest_value(series, epochs, target, timedelta(minutes=2.5))

        assert value == 100.0

    def test_just_beyond_tolerance_returns_none(self) -> None:
        series = [TimedSample(_START, 100.0)]
        epochs = [s.at.timestamp() for s in series]
        target = _START + timedelta(minutes=2, seconds=31)  # 1 second beyond 2.5 min

        value = _nearest_value(series, epochs, target, timedelta(minutes=2.5))

        assert value is None

    def test_empty_series_returns_none(self) -> None:
        assert _nearest_value([], [], _START, timedelta(minutes=2.5)) is None


class TestBuildRowsFromSeriesHappyPath:
    """Momentum-from-offsets happy path (Test Requirements case 5)."""

    def test_produces_expected_row_count_and_values(self) -> None:
        # 30 readings, 5 min apart: momentum needs 12 steps back (60 min),
        # label needs 4 steps forward (20 min) -> valid base indices [12, 25].
        series = _clean_series(30)

        rows = build_rows_from_series("1", series)

        assert len(rows) == 14  # indices 12..25 inclusive
        assert [r.base_time for r in rows] == [series[i].at for i in range(12, 26)]

    def test_row_at_index_15_has_correct_momentum_and_label(self) -> None:
        series = _clean_series(30)

        rows = build_rows_from_series("1", series)
        row = next(r for r in rows if r.base_time == series[15].at)

        assert row.lots_now == 115.0
        assert row.label == 119.0  # index 19 (15 + 4 steps of 5 min = 20 min)
        # features[3:] == [lots_now, 15m_ago, 30m_ago, 60m_ago]
        assert row.features[3] == 115.0
        assert row.features[4] == 112.0  # index 12 (15 min back = 3 steps)
        assert row.features[5] == 109.0  # index 9 (30 min back = 6 steps)
        assert row.features[6] == 103.0  # index 3 (60 min back = 12 steps)

    def test_features_match_build_feature_vector_exactly(self) -> None:
        """The row's feature vector must be bit-identical to calling
        build_feature_vector directly with the same target/momentum inputs
        -- this is the wiring the join logic is responsible for getting right.
        """
        series = _clean_series(30)
        rows = build_rows_from_series("1", series)
        row = next(r for r in rows if r.base_time == series[15].at)

        expected = build_feature_vector(
            row.target_time, lots_now=115.0, lots_15m_ago=112.0, lots_30m_ago=109.0,
            lots_60m_ago=103.0,
        )
        assert row.features == expected

    def test_carpark_id_passthrough(self) -> None:
        series = _clean_series(30)
        rows = build_rows_from_series("suntec-1", series)
        assert all(r.carpark_id == "suntec-1" for r in rows)

    def test_unsorted_input_is_sorted_internally(self) -> None:
        series = _clean_series(30)
        shuffled = list(reversed(series))

        rows_from_sorted = build_rows_from_series("1", series)
        rows_from_shuffled = build_rows_from_series("1", shuffled)

        assert rows_from_shuffled == rows_from_sorted


class TestBuildRowsFromSeriesGaps:
    """Poll-gap handling: a missing reading must drop exactly the affected
    row(s), never fabricate a value (Test Requirements case 4).
    """

    def test_label_gap_drops_only_the_affected_row(self) -> None:
        # 20 readings -> valid base indices without gaps would be [12, 15].
        series = _clean_series(20)
        # Remove index 16: this is the LABEL target for base index 12
        # (12 + 4 steps == 16) and nothing else's momentum offset in [12,15].
        gapped = [s for i, s in enumerate(series) if i != 16]

        rows_full = build_rows_from_series("1", series)
        rows_gapped = build_rows_from_series("1", gapped)

        assert {r.base_time for r in rows_full} == {series[i].at for i in (12, 13, 14, 15)}
        assert {r.base_time for r in rows_gapped} == {series[i].at for i in (13, 14, 15)}
        assert series[12].at not in {r.base_time for r in rows_gapped}

    def test_momentum_gap_drops_only_the_affected_row(self) -> None:
        # Remove index 1: this is the 60-min-ago momentum offset for base
        # index 13 (13 - 12 == 1) and nothing else's label/momentum in
        # [12, 15] (index 12's own 60m-ago offset is index 0, untouched).
        series = _clean_series(20)
        gapped = [s for i, s in enumerate(series) if i != 1]

        rows_gapped = build_rows_from_series("1", gapped)

        assert {r.base_time for r in rows_gapped} == {series[i].at for i in (12, 14, 15)}
        assert series[13].at not in {r.base_time for r in rows_gapped}

    def test_empty_series_produces_no_rows(self) -> None:
        assert build_rows_from_series("1", []) == []

    def test_too_short_series_produces_no_rows(self) -> None:
        # Not enough history for even the smallest (15-min) momentum offset.
        series = _clean_series(2)
        assert build_rows_from_series("1", series) == []
