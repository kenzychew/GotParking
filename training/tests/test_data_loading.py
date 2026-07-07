"""Tests for gotparking_training.data_loading.

Covers Test Requirements case 1 (load happy path) and case 2 (cold-start
carpark excluded, both sub-cases).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gotparking_training.data_loading import (
    compute_history_stats,
    filter_eligible_carparks,
    is_system_wide_training_eligible,
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
    """Test Requirements case 2: cold-start carpark excluded (both sub-cases).

    All carparks here are constructed as `is_original_seed=True` -- these
    tests are about the PRE-EXISTING cold-start rule in isolation, so the
    T2 system-wide eligibility gate (see TestSystemWideEligibilityGate
    below) is held open throughout via `is_original_seed=True`.
    """

    def test_warm_carpark_is_included(self) -> None:
        carparks = [CarparkInfo("1", "Suntec City", 1584, True, is_original_seed=True)]
        history = {
            "1": [TimedSample(_NOW - timedelta(days=30) + timedelta(minutes=i), 100.0)
                  for i in range(20)]
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW, first_promotion_at=None)

        assert eligible == carparks

    def test_excludes_too_young_carpark(self) -> None:
        """Sub-case 1: first sample younger than 72h, even with many samples."""
        carparks = [CarparkInfo("1", "Suntec City", 1584, True, is_original_seed=True)]
        history = {
            "1": [TimedSample(_NOW - timedelta(hours=1) + timedelta(minutes=i), 100.0)
                  for i in range(50)]
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW, first_promotion_at=None)

        assert eligible == []

    def test_excludes_too_few_samples_carpark(self) -> None:
        """Sub-case 2: old enough but fewer than 10 samples."""
        carparks = [CarparkInfo("1", "Suntec City", 1584, True, is_original_seed=True)]
        history = {
            "1": [TimedSample(_NOW - timedelta(days=30), 100.0),
                  TimedSample(_NOW - timedelta(days=29), 101.0)]
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW, first_promotion_at=None)

        assert eligible == []

    def test_carpark_with_no_history_at_all_is_excluded(self) -> None:
        carparks = [CarparkInfo("50", "VivoCity P2", None, True, is_original_seed=True)]

        eligible = filter_eligible_carparks(
            carparks, history={}, now=_NOW, first_promotion_at=None
        )

        assert eligible == []

    def test_mixed_eligible_and_excluded(self) -> None:
        warm = CarparkInfo("1", "Suntec City", 1584, True, is_original_seed=True)
        cold = CarparkInfo("2", "New Carpark", None, True, is_original_seed=True)
        carparks = [warm, cold]
        history = {
            "1": [TimedSample(_NOW - timedelta(days=30) + timedelta(minutes=i), 100.0)
                  for i in range(20)],
            "2": [TimedSample(_NOW - timedelta(hours=1), 50.0)],
        }

        eligible = filter_eligible_carparks(carparks, history, _NOW, first_promotion_at=None)

        assert eligible == [warm]


class TestIsSystemWideTrainingEligible:
    """Unit coverage for the T2 boolean condition in isolation, shared by
    `filter_eligible_carparks` and `train.run`'s SINPA-mapping selection."""

    def test_original_seed_eligible_regardless_of_first_promotion_at(self) -> None:
        seed = CarparkInfo("1", "Suntec City", 1584, True, is_original_seed=True)
        assert is_system_wide_training_eligible(seed, first_promotion_at=None) is True
        assert is_system_wide_training_eligible(seed, first_promotion_at=_NOW) is True

    def test_non_seed_ineligible_until_first_promotion_at_set(self) -> None:
        non_seed = CarparkInfo("60", "New Carpark", None, True, is_original_seed=False)
        assert is_system_wide_training_eligible(non_seed, first_promotion_at=None) is False
        assert is_system_wide_training_eligible(non_seed, first_promotion_at=_NOW) is True


class TestSystemWideEligibilityGate:
    """Test Requirements (T2): the one-time system-wide training-eligibility
    gate, layered on top of (never replacing) the cold-start exclusion.

    All carparks below are warm (clear cold-start easily) so only the T2
    gate is under test.
    """

    def _warm_history_for(self, carpark_id: str) -> dict[str, list[TimedSample]]:
        return {
            carpark_id: [
                TimedSample(_NOW - timedelta(days=30) + timedelta(minutes=i), 100.0)
                for i in range(20)
            ]
        }

    def test_original_seed_carpark_stays_eligible_when_first_promotion_at_is_null(
        self,
    ) -> None:
        """Regression: the T2 flag must never retroactively exclude a
        carpark already mid-flight in production (an original seed
        carpark, before any promotion has ever happened)."""
        seed = CarparkInfo("1", "Suntec City", 1584, True, is_original_seed=True)

        eligible = filter_eligible_carparks(
            [seed], self._warm_history_for("1"), _NOW, first_promotion_at=None,
        )

        assert eligible == [seed]

    def test_non_seed_carpark_excluded_when_first_promotion_at_is_null(self) -> None:
        non_seed = CarparkInfo("60", "New Carpark", None, True, is_original_seed=False)

        eligible = filter_eligible_carparks(
            [non_seed], self._warm_history_for("60"), _NOW, first_promotion_at=None,
        )

        assert eligible == []

    def test_non_seed_carpark_becomes_eligible_once_first_promotion_at_is_set(self) -> None:
        non_seed = CarparkInfo("60", "New Carpark", None, True, is_original_seed=False)
        first_promotion_at = _NOW - timedelta(days=7)

        eligible = filter_eligible_carparks(
            [non_seed], self._warm_history_for("60"), _NOW,
            first_promotion_at=first_promotion_at,
        )

        assert eligible == [non_seed]
