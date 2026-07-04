"""Tests for the batch-predict endpoint (api/_lib/batch_logic.py).

Covers every case in the design doc's Test Requirements batch-predict
section:
  1. missing/bad secret -> 401, no compute
  2. computes all rows -- mixed states (ml / baseline / cold_start+live_lots)
  3. stale momentum (>15 min) -> baseline path for that carpark
  4. artifact missing/corrupt -> last-known-good; else baseline rows +
     /fail ping, table never empty
  5. model_config version change -> artifact reloaded exactly once
  6. Supabase read/write failure -> retry (covered in test_supabase_rest.py)
     then /fail ping (covered here at the orchestration level)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _lib.batch_logic import (
    BatchDeps,
    BatchPredictError,
    STATE_BASELINE,
    STATE_COLD_START,
    STATE_ML,
    handle_batch_predict,
    run_batch_predict,
)
from _lib.model_cache import ModelCache
from tests.fakes import FakeSupabaseDB, RecordingFailPing, make_clock, make_history_rows

SECRET = "test-shared-secret"

# Fixed "now": Monday 2026-07-06 03:00 UTC == 11:00 SGT.
# target = now + 20min = 03:20 UTC == 11:20 SGT -> dow=0 (Monday), slot=45.
NOW = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)
TARGET_DOW = 0
TARGET_SLOT = 45


def _deps(
    db: FakeSupabaseDB,
    *,
    model_cache: ModelCache | None = None,
    fail_ping: RecordingFailPing | None = None,
    now: datetime = NOW,
) -> BatchDeps:
    return BatchDeps(
        db=db,
        batch_shared_secret=SECRET,
        model_cache=model_cache if model_cache is not None else ModelCache(),
        fail_ping=fail_ping if fail_ping is not None else RecordingFailPing(),
        clock=make_clock(now),
    )


def _base_carparks() -> list[dict[str, object]]:
    return [
        {"carpark_id": "1", "name": "Suntec City", "active": True},
        {"carpark_id": "2", "name": "Marina Square", "active": True},
        {"carpark_id": "3", "name": "Raffles City", "active": True},
        {"carpark_id": "11", "name": "Cineleisure", "active": True},
        {"carpark_id": "13", "name": "Ngee Ann City", "active": True},
    ]


class TestAuth:
    """Case 1: missing/bad secret -> 401, no compute."""

    def test_missing_secret_header_rejected(self) -> None:
        db = FakeSupabaseDB(tables={"carparks": _base_carparks()})
        deps = _deps(db)

        response = handle_batch_predict({}, deps)

        assert response.status == 401
        assert db.select_calls == []  # no compute happened

    def test_wrong_secret_rejected(self) -> None:
        db = FakeSupabaseDB(tables={"carparks": _base_carparks()})
        deps = _deps(db)

        response = handle_batch_predict({"x-batch-secret": "wrong"}, deps)

        assert response.status == 401
        assert db.select_calls == []

    def test_correct_secret_is_accepted(self) -> None:
        db = FakeSupabaseDB(
            tables={
                "carparks": [],
                "model_config": [{"active_model_version": None}],
                "carpark_momentum": [],
                "carpark_baseline": [],
            }
        )
        deps = _deps(db)

        response = handle_batch_predict({"x-batch-secret": SECRET}, deps)

        assert response.status == 200

    def test_header_lookup_is_case_insensitive(self) -> None:
        db = FakeSupabaseDB(
            tables={
                "carparks": [],
                "model_config": [{"active_model_version": None}],
                "carpark_momentum": [],
                "carpark_baseline": [],
            }
        )
        deps = _deps(db)

        response = handle_batch_predict({"X-Batch-Secret": SECRET}, deps)

        assert response.status == 200


class TestMixedStates:
    """Case 2: computes rows with mixed states in one run."""

    def _mixed_db(self, model_bytes: bytes) -> FakeSupabaseDB:
        history: list[dict[str, object]] = []
        # Carpark "1": cold_start via too few samples (5 < 10).
        history += make_history_rows(
            "1", 5, NOW - timedelta(days=100), NOW - timedelta(minutes=5),
            capacity=50, live_lots=42,
        )
        # Carpark "2": cold_start via first sample too recent (<72h old),
        # despite having plenty of samples.
        history += make_history_rows(
            "2", 50, NOW - timedelta(hours=10), NOW - timedelta(minutes=5),
            capacity=80, live_lots=60,
        )
        # Carpark "3": eligible for ml (old history, fresh+complete momentum).
        history += make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        # Carpark "11": baseline via STALE momentum; baseline cell present.
        history += make_history_rows(
            "11", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=150, live_lots=90,
        )
        # Carpark "13": baseline via MISSING momentum row + missing baseline
        # cell -> persistence (live_lots) fallback.
        history += make_history_rows(
            "13", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=140, live_lots=10,
        )

        return FakeSupabaseDB(
            tables={
                "carparks": _base_carparks(),
                "model_config": [{"active_model_version": "v1"}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": 190,
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=5)).isoformat(),
                    },
                    {
                        "carpark_id": "11",
                        "lots_15m_ago": 85,
                        "lots_30m_ago": 80,
                        "lots_60m_ago": 75,
                        # Stale: 20 min > 15 min freshness window.
                        "updated_at": (NOW - timedelta(minutes=20)).isoformat(),
                    },
                    # "13" has no momentum row at all.
                ],
                "carpark_baseline": [
                    {
                        "carpark_id": "11",
                        "dow": TARGET_DOW,
                        "slot_of_day": TARGET_SLOT,
                        "avg_available_lots": 82.0,
                    },
                    # "13" has no baseline cell for (dow, slot) -> persistence.
                ],
            },
            storage={"models/v1.txt": model_bytes},
        )

    def test_computes_one_row_per_carpark_with_correct_states(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        db = self._mixed_db(tiny_lightgbm_model_str.encode("utf-8"))
        deps = _deps(db)

        result = run_batch_predict(deps)

        assert result.computed == 5
        written = {row["carpark_id"]: row for row in db.upserted["carpark_forecast"]}
        assert set(written) == {"1", "2", "3", "11", "13"}

        assert written["1"]["state"] == STATE_COLD_START
        assert written["1"]["forecast_lots"] is None
        assert written["1"]["tier"] is None
        assert written["1"]["live_lots"] == 42

        assert written["2"]["state"] == STATE_COLD_START
        assert written["2"]["live_lots"] == 60

        assert written["3"]["state"] == STATE_ML
        assert written["3"]["model_version"] == "v1"
        assert written["3"]["forecast_lots"] >= 0
        assert written["3"]["tier"] in {"plenty", "limited", "very_limited"}

        assert written["11"]["state"] == STATE_BASELINE
        assert written["11"]["model_version"] is None
        assert written["11"]["forecast_lots"] == 82  # from carpark_baseline, stale momentum ignored

        assert written["13"]["state"] == STATE_BASELINE
        assert written["13"]["forecast_lots"] == 10  # persistence: no baseline cell, no momentum

        assert result.generated_at == NOW.isoformat()

    def test_cold_start_rows_satisfy_shape_constraint(self, tiny_lightgbm_model_str: str) -> None:
        db = self._mixed_db(tiny_lightgbm_model_str.encode("utf-8"))
        deps = _deps(db)

        run_batch_predict(deps)

        for carpark_id in ("1", "2"):
            row = next(r for r in db.upserted["carpark_forecast"] if r["carpark_id"] == carpark_id)
            # Mirrors the carpark_forecast_shape CHECK constraint exactly.
            assert row["state"] == STATE_COLD_START
            assert row["forecast_lots"] is None
            assert row["tier"] is None


class TestStaleMomentum:
    """Case 3: stale momentum (>15 min) -> baseline path for that carpark."""

    def test_stale_momentum_forces_baseline_even_with_working_model(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        history = make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        db = FakeSupabaseDB(
            tables={
                "carparks": [{"carpark_id": "3", "name": "Raffles City", "active": True}],
                "model_config": [{"active_model_version": "v1"}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": 190,
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=16)).isoformat(),  # stale
                    }
                ],
                "carpark_baseline": [
                    {
                        "carpark_id": "3",
                        "dow": TARGET_DOW,
                        "slot_of_day": TARGET_SLOT,
                        "avg_available_lots": 210.0,
                    }
                ],
            },
            storage={"models/v1.txt": tiny_lightgbm_model_str.encode("utf-8")},
        )
        deps = _deps(db)

        run_batch_predict(deps)

        row = db.upserted["carpark_forecast"][0]
        assert row["state"] == STATE_BASELINE
        assert row["forecast_lots"] == 210
        assert row["model_version"] is None

    def test_momentum_exactly_at_freshness_boundary_is_still_fresh(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        """updated_at exactly 15 minutes old is NOT stale (boundary is
        "older than" 15 min, not "15 min or older")."""
        history = make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        db = FakeSupabaseDB(
            tables={
                "carparks": [{"carpark_id": "3", "name": "Raffles City", "active": True}],
                "model_config": [{"active_model_version": "v1"}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": 190,
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=15)).isoformat(),
                    }
                ],
                "carpark_baseline": [],
            },
            storage={"models/v1.txt": tiny_lightgbm_model_str.encode("utf-8")},
        )
        deps = _deps(db)

        run_batch_predict(deps)

        row = db.upserted["carpark_forecast"][0]
        assert row["state"] == STATE_ML

    def test_incomplete_momentum_also_falls_back_to_baseline(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        """A fresh but incomplete momentum row (missing a lag reading) is
        treated the same as stale -- never fed partially-null features."""
        history = make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        db = FakeSupabaseDB(
            tables={
                "carparks": [{"carpark_id": "3", "name": "Raffles City", "active": True}],
                "model_config": [{"active_model_version": "v1"}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": None,  # incomplete
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
                    }
                ],
                "carpark_baseline": [
                    {
                        "carpark_id": "3",
                        "dow": TARGET_DOW,
                        "slot_of_day": TARGET_SLOT,
                        "avg_available_lots": 199.0,
                    }
                ],
            },
            storage={"models/v1.txt": tiny_lightgbm_model_str.encode("utf-8")},
        )
        deps = _deps(db)

        run_batch_predict(deps)

        row = db.upserted["carpark_forecast"][0]
        assert row["state"] == STATE_BASELINE


class TestModelArtifactFallback:
    """Case 4: artifact missing/corrupt -> last-known-good; else baseline
    rows + /fail ping, table never empty.
    """

    def _single_ml_eligible_db(
        self, active_version: str, storage: dict[str, object]
    ) -> FakeSupabaseDB:
        history = make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        return FakeSupabaseDB(
            tables={
                "carparks": [{"carpark_id": "3", "name": "Raffles City", "active": True}],
                "model_config": [{"active_model_version": active_version}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": 190,
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
                    }
                ],
                "carpark_baseline": [
                    {
                        "carpark_id": "3",
                        "dow": TARGET_DOW,
                        "slot_of_day": TARGET_SLOT,
                        "avg_available_lots": 199.0,
                    }
                ],
            },
            storage=storage,
        )

    def test_missing_artifact_with_no_last_known_good_serves_baseline_and_pings(
        self,
    ) -> None:
        db = self._single_ml_eligible_db("v1", storage={})  # v1.txt not present at all
        fail_ping = RecordingFailPing()
        deps = _deps(db, fail_ping=fail_ping)

        result = run_batch_predict(deps)

        assert result.computed == 1
        row = db.upserted["carpark_forecast"][0]
        assert row["state"] == STATE_BASELINE
        assert row["forecast_lots"] == 199
        assert fail_ping.reasons == ["MODEL_ARTIFACT_MISSING"]

    def test_corrupt_artifact_with_no_last_known_good_serves_baseline_and_pings(
        self,
    ) -> None:
        db = self._single_ml_eligible_db("v1", storage={"models/v1.txt": b"not a valid model"})
        fail_ping = RecordingFailPing()
        deps = _deps(db, fail_ping=fail_ping)

        result = run_batch_predict(deps)

        assert result.computed == 1
        row = db.upserted["carpark_forecast"][0]
        assert row["state"] == STATE_BASELINE
        assert fail_ping.reasons == ["MODEL_ARTIFACT_MISSING"]

    def test_corrupt_new_version_falls_back_to_last_known_good_no_ping(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        cache = ModelCache()
        # Prime the cache with a good v1 first (simulating a prior successful run).
        cache.get("v1", lambda: tiny_lightgbm_model_str)

        db = self._single_ml_eligible_db(
            "v2", storage={"models/v2.txt": b"corrupt bytes, not a model"}
        )
        fail_ping = RecordingFailPing()
        deps = _deps(db, model_cache=cache, fail_ping=fail_ping)

        result = run_batch_predict(deps)

        assert result.computed == 1
        row = db.upserted["carpark_forecast"][0]
        # Falls back to v1 (last-known-good) and keeps serving via ml.
        assert row["state"] == STATE_ML
        assert row["model_version"] == "v1"
        # No ping -- a usable model IS serving, this is not an outage.
        assert fail_ping.reasons == []

    def test_forecast_table_is_never_empty_even_on_total_artifact_failure(self) -> None:
        db = self._single_ml_eligible_db("v1", storage={})
        deps = _deps(db)

        result = run_batch_predict(deps)

        assert result.computed == 1
        assert len(db.upserted["carpark_forecast"]) == 1


class TestModelVersionReload:
    """Case 5: model_config version change -> artifact reloaded exactly once."""

    def _db_with_version(self, version: str, model_bytes: bytes) -> FakeSupabaseDB:
        history = make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        return FakeSupabaseDB(
            tables={
                "carparks": [{"carpark_id": "3", "name": "Raffles City", "active": True}],
                "model_config": [{"active_model_version": version}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": 190,
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
                    }
                ],
                "carpark_baseline": [],
            },
            storage={f"models/{version}.txt": model_bytes},
        )

    def test_same_version_across_runs_fetches_artifact_only_once(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        cache = ModelCache()
        model_bytes = tiny_lightgbm_model_str.encode("utf-8")

        db_run1 = self._db_with_version("v1", model_bytes)
        run_batch_predict(_deps(db_run1, model_cache=cache))
        db_run2 = self._db_with_version("v1", model_bytes)
        run_batch_predict(_deps(db_run2, model_cache=cache))

        total_downloads = db_run1.download_calls + db_run2.download_calls
        assert total_downloads == ["models/v1.txt"]  # only the first run fetched it

    def test_version_change_triggers_exactly_one_new_fetch(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        cache = ModelCache()
        model_bytes = tiny_lightgbm_model_str.encode("utf-8")

        db_v1 = self._db_with_version("v1", model_bytes)
        run_batch_predict(_deps(db_v1, model_cache=cache))
        db_v2 = self._db_with_version("v2", model_bytes)
        run_batch_predict(_deps(db_v2, model_cache=cache))

        assert db_v1.download_calls == ["models/v1.txt"]
        assert db_v2.download_calls == ["models/v2.txt"]
        assert cache.loaded_versions() == frozenset({"v1", "v2"})

        row = db_v2.upserted["carpark_forecast"][0]
        assert row["model_version"] == "v2"


class TestSupabaseFailure:
    """Case 6: Supabase read/write failure -> /fail ping (retry-once itself
    is SupabaseREST's responsibility, covered in test_supabase_rest.py)."""

    def test_read_failure_pings_and_raises_batch_predict_error(self) -> None:
        db = FakeSupabaseDB(
            tables={"carparks": _base_carparks()},
            fail_tables={"carparks"},
        )
        fail_ping = RecordingFailPing()
        deps = _deps(db, fail_ping=fail_ping)

        with pytest.raises(BatchPredictError):
            run_batch_predict(deps)

        assert fail_ping.reasons == ["SUPABASE_UNAVAILABLE"]

    def test_read_failure_via_handle_batch_predict_returns_500_not_raw_crash(self) -> None:
        db = FakeSupabaseDB(
            tables={"carparks": _base_carparks()},
            fail_tables={"carparks"},
        )
        deps = _deps(db)

        response = handle_batch_predict({"x-batch-secret": SECRET}, deps)

        assert response.status == 500
        assert "error" in response.body

    def test_write_failure_pings_and_raises(self) -> None:
        db = FakeSupabaseDB(
            tables={
                "carparks": [],
                "model_config": [{"active_model_version": None}],
                "carpark_momentum": [],
                "carpark_baseline": [],
            },
            fail_upsert=True,
        )
        fail_ping = RecordingFailPing()
        deps = _deps(db, fail_ping=fail_ping)

        with pytest.raises(BatchPredictError):
            run_batch_predict(deps)

        assert fail_ping.reasons == ["SUPABASE_UNAVAILABLE"]


class TestNoActiveModel:
    """When no model has ever been promoted, every non-cold-start carpark
    is served via baseline (no /fail ping -- this is a normal, expected
    state, not a failure)."""

    def test_null_active_model_version_serves_baseline_for_all(self) -> None:
        history = make_history_rows(
            "3", 500, NOW - timedelta(days=30), NOW - timedelta(minutes=5),
            capacity=300, live_lots=200,
        )
        db = FakeSupabaseDB(
            tables={
                "carparks": [{"carpark_id": "3", "name": "Raffles City", "active": True}],
                "model_config": [{"active_model_version": None}],
                "carpark_history": history,
                "carpark_momentum": [
                    {
                        "carpark_id": "3",
                        "lots_15m_ago": 195,
                        "lots_30m_ago": 190,
                        "lots_60m_ago": 180,
                        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
                    }
                ],
                "carpark_baseline": [
                    {
                        "carpark_id": "3",
                        "dow": TARGET_DOW,
                        "slot_of_day": TARGET_SLOT,
                        "avg_available_lots": 199.0,
                    }
                ],
            },
        )
        fail_ping = RecordingFailPing()
        deps = _deps(db, fail_ping=fail_ping)

        run_batch_predict(deps)

        row = db.upserted["carpark_forecast"][0]
        assert row["state"] == STATE_BASELINE
        assert fail_ping.reasons == []  # not a failure, just no model yet


class TestTenCarparkCount:
    """Sanity check on the literal "computes all 10 rows" framing: with 10
    active (all cold_start, for fixture simplicity) carparks, exactly 10
    rows are computed and written."""

    def test_ten_active_carparks_yields_ten_rows(self) -> None:
        carparks = [
            {"carpark_id": str(i), "name": f"Carpark {i}", "active": True} for i in range(10)
        ]
        db = FakeSupabaseDB(
            tables={
                "carparks": carparks,
                "model_config": [{"active_model_version": None}],
                "carpark_history": [],  # no history at all -> every one cold_start
                "carpark_momentum": [],
                "carpark_baseline": [],
            }
        )
        deps = _deps(db)

        result = run_batch_predict(deps)

        assert result.computed == 10
        assert len(db.upserted["carpark_forecast"]) == 10
        assert all(row["state"] == STATE_COLD_START for row in db.upserted["carpark_forecast"])
        assert all(row["live_lots"] == 0 for row in db.upserted["carpark_forecast"])
