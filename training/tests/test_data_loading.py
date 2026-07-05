"""Tests for gotparking_training.data_loading.

Covers Test Requirements case 1 (load happy path) and case 2 (cold-start
carpark excluded, both sub-cases).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gotparking_training.data_loading import (
    compute_history_stats,
    filter_eligible_carparks,
    load_carpark_history,
)
from gotparking_training.repository import CarparkInfo
from gotparking_training.series import TimedSample
from tests.fakes import FakeSupabaseDB, make_history_rows

_NOW = datetime(2026, 7, 6, 5, 0, tzinfo=timezone.utc)


class TestLoadCarparkHistory:
    """Test Requirements case 1: load happy path."""

    def test_groups_rows_by_carpark_and_paginates(self) -> None:
        rows_1 = make_history_rows("1", 5, _NOW - timedelta(days=1), timedelta(minutes=5))
        rows_2 = make_history_rows("2", 3, _NOW - timedelta(hours=1), timedelta(minutes=5))
        db = FakeSupabaseDB(tables={"carpark_history": rows_1 + rows_2})

        history = load_carpark_history(db)

        assert set(history) == {"1", "2"}
        assert len(history["1"]) == 5
        assert len(history["2"]) == 3
        assert all(isinstance(s, TimedSample) for s in history["1"])

    def test_empty_table_returns_empty_dict(self) -> None:
        db = FakeSupabaseDB(tables={"carpark_history": []})
        assert load_carpark_history(db) == {}

    def test_paginates_using_select_all_page_size(self) -> None:
        # FakeSupabaseDB.select_all ignores page_size (returns everything in
        # one call), but this asserts select_all (not select) is the method
        # used, which is what actually triggers real pagination in
        # SupabaseREST against a large table.
        rows = make_history_rows("1", 10, _NOW - timedelta(days=1), timedelta(minutes=5))
        db = FakeSupabaseDB(tables={"carpark_history": rows})

        load_carpark_history(db)

        assert any(table == "carpark_history" for table, _ in db.select_calls)


class TestComputeHistoryStats:
    def test_empty_series(self) -> None:
        assert compute_history_stats([]) == (None, 0)

    def test_returns_earliest_timestamp_and_count(self) -> None:
        series = [
            TimedSample(_NOW - timedelta(hours=1), 10.0),
            TimedSample(_NOW - timedelta(hours=3), 12.0),
            TimedSample(_NOW - timedelta(hours=2), 11.0),
        ]
        first_at, count = compute_history_stats(series)

        assert first_at == _NOW - timedelta(hours=3)
        assert count == 3


class TestFilterEligibleCarparks:
    """Test Requirements case 2: cold-start carpark excluded (both sub-cases)."""

    def test_warm_carpark_is_included(self) -> None:
        carparks = [CarparkInfo("1", "Suntec City", 1584, True)]
        history = {
            "1": [TimedSample(_NOW - timedelta(days=30) + timedelta(minutes=i), 100.0)
                  for i in range(20)]
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW)

        assert eligible == carparks

    def test_excludes_too_young_carpark(self) -> None:
        """Sub-case 1: first sample younger than 72h, even with many samples."""
        carparks = [CarparkInfo("1", "Suntec City", 1584, True)]
        history = {
            "1": [TimedSample(_NOW - timedelta(hours=1) + timedelta(minutes=i), 100.0)
                  for i in range(50)]
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW)

        assert eligible == []

    def test_excludes_too_few_samples_carpark(self) -> None:
        """Sub-case 2: old enough but fewer than 10 samples."""
        carparks = [CarparkInfo("1", "Suntec City", 1584, True)]
        history = {
            "1": [TimedSample(_NOW - timedelta(days=30), 100.0),
                  TimedSample(_NOW - timedelta(days=29), 101.0)]
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW)

        assert eligible == []

    def test_carpark_with_no_history_at_all_is_excluded(self) -> None:
        carparks = [CarparkInfo("50", "VivoCity P2", None, True)]

        eligible = filter_eligible_carparks(carparks, history={}, now=_NOW)

        assert eligible == []

    def test_mixed_eligible_and_excluded(self) -> None:
        warm = CarparkInfo("1", "Suntec City", 1584, True)
        cold = CarparkInfo("2", "New Carpark", None, True)
        carparks = [warm, cold]
        history = {
            "1": [TimedSample(_NOW - timedelta(days=30) + timedelta(minutes=i), 100.0)
                  for i in range(20)],
            "2": [TimedSample(_NOW - timedelta(hours=1), 50.0)],
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW)

        assert eligible == [warm]
