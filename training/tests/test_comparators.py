"""Tests for gotparking_training.comparators (historical-average + persistence).

Covers Test Requirements case 6 ("comparator recomputed pre-holdout only")
at the unit level -- this module has no Supabase dependency at all, so
"never reads the live carpark_baseline table" holds structurally. The
full-pipeline version of this test (asserting the poisoned live-baseline
stub is never queried) lives in test_train.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gotparking_training.comparators import (
    build_carpark_mean,
    build_historical_average,
    predict_historical_average,
    predict_persistence,
)
from gotparking_training.series import TrainingRow

# Mon 2026-07-06 03:00 UTC == Mon 11:00 SGT (dow=0, slot=44).
_MON_1100_SGT = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)
# Tue 2026-07-07 03:00 UTC == Tue 11:00 SGT (dow=1, slot=44).
_TUE_1100_SGT = datetime(2026, 7, 7, 3, 0, tzinfo=timezone.utc)


def _row(carpark_id: str, target_time: datetime, label: float, lots_now: float) -> TrainingRow:
    return TrainingRow(
        carpark_id=carpark_id,
        base_time=target_time,
        target_time=target_time,
        features=[],
        label=label,
        lots_now=lots_now,
    )


class TestBuildHistoricalAverage:
    def test_averages_labels_within_the_same_carpark_dow_slot_cell(self) -> None:
        rows = [
            _row("1", _MON_1100_SGT, label=10.0, lots_now=1.0),
            _row("1", _MON_1100_SGT, label=20.0, lots_now=1.0),
            _row("1", _MON_1100_SGT, label=30.0, lots_now=1.0),
        ]

        table = build_historical_average(rows)

        assert table[("1", 0, 44)] == 20.0

    def test_separates_by_carpark_and_by_dow_slot(self) -> None:
        rows = [
            _row("1", _MON_1100_SGT, label=10.0, lots_now=1.0),
            _row("1", _TUE_1100_SGT, label=100.0, lots_now=1.0),
            _row("2", _MON_1100_SGT, label=5.0, lots_now=1.0),
        ]

        table = build_historical_average(rows)

        assert table[("1", 0, 44)] == 10.0
        assert table[("1", 1, 44)] == 100.0
        assert table[("2", 0, 44)] == 5.0

    def test_empty_input_produces_empty_table(self) -> None:
        assert build_historical_average([]) == {}


class TestBuildCarparkMean:
    def test_averages_across_all_dow_slot_cells_for_a_carpark(self) -> None:
        rows = [
            _row("1", _MON_1100_SGT, label=10.0, lots_now=1.0),
            _row("1", _TUE_1100_SGT, label=30.0, lots_now=1.0),
        ]

        means = build_carpark_mean(rows)

        assert means["1"] == 20.0


class TestPredictHistoricalAverage:
    def test_uses_fine_cell_when_present(self) -> None:
        table = {("1", 0, 44): 20.0}
        carpark_mean = {"1": 999.0}
        row = _row("1", _MON_1100_SGT, label=0.0, lots_now=5.0)

        assert predict_historical_average(table, carpark_mean, row) == 20.0

    def test_falls_back_to_carpark_mean_when_fine_cell_missing(self) -> None:
        table: dict[tuple[str, int, int], float] = {}
        carpark_mean = {"1": 42.0}
        row = _row("1", _MON_1100_SGT, label=0.0, lots_now=5.0)

        assert predict_historical_average(table, carpark_mean, row) == 42.0

    def test_falls_back_to_lots_now_when_nothing_available(self) -> None:
        row = _row("1", _MON_1100_SGT, label=0.0, lots_now=7.0)

        assert predict_historical_average({}, {}, row) == 7.0


class TestPredictPersistence:
    def test_returns_lots_now(self) -> None:
        row = _row("1", _MON_1100_SGT, label=999.0, lots_now=13.0)

        assert predict_persistence(row) == 13.0
