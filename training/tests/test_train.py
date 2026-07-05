"""Integration tests for gotparking_training.train (the full pipeline).

Uses FakeSupabaseDB end to end (no network, no real Supabase project).
Synthetic history is constructed as `available_lots(t) = BASE + AMPLITUDE *
slot_of_day(t)` -- a BOUNDED, dailycyclical function of slot_of_day (which
repeats every 96 slots), deliberately chosen so:
  * holdout feature values are never outside the training feature range
    (tree ensembles cannot extrapolate a trend; a bounded, repeating
    signal sidesteps that entirely), and
  * the historical-average comparator (falling back to a flat per-carpark
    mean, since these short synthetic windows span under 7 days and so
    never repeat an exact (carpark, dow, slot) cell between pre-holdout
    and holdout) is a poor, flat predictor of a signal that swings with
    slot_of_day -- while persistence (predicting lots_now) tracks the
    signal roughly, and a real model (seeing the TARGET's slot_of_day
    directly as a feature) can track it almost exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import lightgbm
import pytest

from gotparking_training.config import FAIL_REASON_MODEL_UPLOAD_FAILED
from gotparking_training.gate import PHASE_FIRST_PROMOTION, PHASE_RETRAIN
from gotparking_training.series import TrainingRow
from gotparking_training.sg_time import sgt_parts
from gotparking_training.sinpa import SinpaCarparkMapping, SinpaUnavailableError
from gotparking_training.train import RunResult, TrainDeps, TrainingJobError, main, run
from tests.fakes import FakeSupabaseDB, RecordingFailPing, make_clock

#: Shape of a synthetic `carpark_history` row dict (see
#: _slot_dependent_history_rows): carpark_id/polled_at are str, available_lots
#: is float.
HistoryRow = dict[str, str | float]

_GOOD_PARAMS = {
    "objective": "regression",
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
    "num_leaves": 31,
    "min_data_in_leaf": 3,
    "min_data_in_bin": 1,
    "learning_rate": 0.2,
}
_GOOD_ROUNDS = 60

# A deliberately crippled model: num_leaves=2 (LightGBM's minimum) with a
# single boosting round forces just ONE split across the entire dataset,
# so it can only ever produce a coarse 2-bucket approximation of the true
# (96-value) slot-of-day-dependent signal -- a robust, deterministic way
# to construct a "bad candidate"/"bad incumbent" for gate-rejection tests
# without relying on statistical noise arguments.
_CRIPPLED_PARAMS = {"objective": "regression", "verbosity": -1, "num_leaves": 2,
                    "min_data_in_leaf": 1, "min_data_in_bin": 1}
_CRIPPLED_ROUNDS = 1

_BASE_VALUE = 100.0
_AMPLITUDE = 10.0


def _slot_dependent_history_rows(
    carpark_id: str, start: datetime, n_ticks: int, step_minutes: int = 5,
) -> list[HistoryRow]:
    """Synthetic `carpark_history` rows: available_lots = BASE + AMPLITUDE *
    slot_of_day(polled_at) -- see module docstring for the rationale.
    """
    rows: list[HistoryRow] = []
    for i in range(n_ticks):
        at = start + timedelta(minutes=step_minutes * i)
        _, slot = sgt_parts(at)
        rows.append(
            {
                "carpark_id": carpark_id,
                "polled_at": at.isoformat(),
                "available_lots": _BASE_VALUE + _AMPLITUDE * slot,
            }
        )
    return rows


def _base_db(
    history_rows: list[HistoryRow], carpark_id: str = "1", sinpa_index: int | None = None,
) -> FakeSupabaseDB:
    return FakeSupabaseDB(
        tables={
            "carparks": [
                {"carpark_id": carpark_id, "name": "Test Carpark", "sinpa_index": sinpa_index,
                 "active": True}
            ],
            "carpark_history": history_rows,
            "model_config": [{"singleton": True, "active_model_version": None}],
        }
    )


def _train_a_booster(
    history_rows: list[HistoryRow], params: dict[str, object], num_boost_round: int,
) -> lightgbm.Booster:
    """Train a standalone booster on the same kind of data, for seeding an
    incumbent artifact directly (bypassing the full pipeline).
    """
    from gotparking_training.series import TimedSample, build_rows_from_series
    from gotparking_training.modeling import train_candidate

    series = [
        TimedSample(
            datetime.fromisoformat(str(row["polled_at"])), float(row["available_lots"])
        )
        for row in history_rows
    ]
    rows = build_rows_from_series("1", series)
    return train_candidate(rows, sinpa_rows=None, params=params, num_boost_round=num_boost_round)


def _make_deps(db: FakeSupabaseDB, now: datetime, **overrides: object) -> TrainDeps:
    fail_ping = overrides.pop("fail_ping", None) or RecordingFailPing()
    kwargs: dict[str, object] = {
        "db": db,
        "clock": make_clock(now),
        "fail_ping": fail_ping,
        "lgbm_params": _GOOD_PARAMS,
        "num_boost_round": _GOOD_ROUNDS,
        "holdout_days": 1,
    }
    kwargs.update(overrides)
    return TrainDeps(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def five_day_history() -> tuple[datetime, list[HistoryRow]]:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    n_ticks = 5 * 24 * 60 // 5  # 5 days at 5-min spacing
    rows = _slot_dependent_history_rows("1", start, n_ticks)
    now = start + timedelta(minutes=5 * n_ticks)
    return now, rows


class TestLoadHappyPathAndColdStartWiring:
    """Test Requirements case 1/2 at the pipeline level (unit coverage
    already lives in test_data_loading.py/test_cold_start.py)."""

    def test_run_loads_history_and_carparks_via_repository(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows)
        deps = _make_deps(db, now)

        result = run(deps)

        assert isinstance(result, RunResult)
        assert any(t == "carparks" for t, _ in db.select_calls)
        assert any(t == "carpark_history" for t, _ in db.select_calls)


class TestBeatsBothComparatorsPromotes:
    """Test Requirements case 5 (train happy path) and case 7 (beats both
    comparators -> promotes, phase 1)."""

    def test_promotes_in_first_promotion_phase(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows)
        deps = _make_deps(db, now)

        result = run(deps)

        assert result.phase == PHASE_FIRST_PROMOTION
        assert result.promoted is True
        assert result.mae_candidate is not None
        assert result.mae_baseline is not None
        assert result.mae_persistence is not None
        assert result.mae_candidate <= 0.9 * result.mae_baseline
        assert result.mae_candidate <= 0.9 * result.mae_persistence
        # Artifact uploaded and model_config flipped to the same version.
        assert len(db.upload_calls) == 1
        bucket, path, _ = db.upload_calls[0]
        assert bucket == "models"
        assert path == f"{result.candidate_version}.txt"
        assert db.updated[-1][2]["active_model_version"] == result.candidate_version
        # training_runs audit row inserted (design doc step 8: ALWAYS).
        assert len(db.inserted["training_runs"]) == 1
        assert db.inserted["training_runs"][0]["promoted"] is True


class TestFailsEitherComparatorDoesNotPromote:
    """Test Requirements case 8: fails either comparator -> no promote.

    Uses a deliberately crippled model (num_leaves=1, a constant
    predictor) so the outcome is deterministic rather than relying on
    statistical noise -- see module docstring.
    """

    def test_crippled_candidate_does_not_promote(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows)
        deps = _make_deps(
            db, now, lgbm_params=_CRIPPLED_PARAMS, num_boost_round=_CRIPPLED_ROUNDS
        )

        result = run(deps)

        assert result.phase == PHASE_FIRST_PROMOTION
        assert result.promoted is False
        assert db.updated == []
        assert db.upload_calls == []
        assert db.inserted["training_runs"][0]["promoted"] is False


class TestComparatorNeverReadsLiveBaselineTable:
    """Test Requirements case 6: comparator recomputed pre-holdout only --
    a poisoned live carpark_baseline table must never be queried."""

    def test_carpark_baseline_table_is_never_selected(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows)
        # Poison carpark_baseline with obviously-wrong data: if this were
        # ever read and used, the comparator MAE would come out very
        # different from what the pre-holdout-only computation gives.
        db.tables["carpark_baseline"] = [
            {"carpark_id": "1", "dow": d, "slot_of_day": s, "avg_available_lots": -99999.0}
            for d in range(7)
            for s in range(96)
        ]
        deps = _make_deps(db, now)

        run(deps)

        assert not any(table == "carpark_baseline" for table, _ in db.select_calls)


class TestPhaseTwoRetrainWiring:
    """Test Requirements cases 10-11 at the pipeline level (exact epsilon
    boundary is unit-tested in test_gate.py; here the model quality gap is
    deliberately large so the outcome is deterministic, and the point is
    to verify the incumbent is actually downloaded/evaluated via the
    correct artifact path)."""

    def test_clearly_better_candidate_promotes_in_retrain_phase(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        incumbent_booster = _train_a_booster(rows, _CRIPPLED_PARAMS, _CRIPPLED_ROUNDS)
        db = _base_db(rows)
        db.tables["model_config"] = [
            {"singleton": True, "active_model_version": "lgbm_20260525_050000"}
        ]
        db.storage["models/lgbm_20260525_050000.txt"] = (
            incumbent_booster.model_to_string().encode("utf-8")
        )
        deps = _make_deps(db, now)  # good candidate params

        result = run(deps)

        assert result.phase == PHASE_RETRAIN
        assert result.mae_incumbent is not None
        assert result.mae_candidate is not None
        assert db.download_calls == ["models/lgbm_20260525_050000.txt"]
        assert result.promoted is True
        assert result.mae_candidate <= 1.02 * result.mae_incumbent

    def test_clearly_worse_candidate_is_rejected_in_retrain_phase(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        incumbent_booster = _train_a_booster(rows, _GOOD_PARAMS, _GOOD_ROUNDS)
        db = _base_db(rows)
        db.tables["model_config"] = [
            {"singleton": True, "active_model_version": "lgbm_20260525_050000"}
        ]
        db.storage["models/lgbm_20260525_050000.txt"] = (
            incumbent_booster.model_to_string().encode("utf-8")
        )
        deps = _make_deps(
            db, now, lgbm_params=_CRIPPLED_PARAMS, num_boost_round=_CRIPPLED_ROUNDS
        )

        result = run(deps)

        assert result.phase == PHASE_RETRAIN
        assert result.promoted is False
        assert result.mae_candidate is not None
        assert result.mae_incumbent is not None
        assert result.mae_candidate > 1.02 * result.mae_incumbent
        assert db.updated == []
        assert db.upload_calls == []


class TestEmptyHoldoutCarparkExcluded:
    """Test Requirements case 12: empty holdout window per carpark ->
    excluded from that cycle's MAE calc, no crash."""

    def test_carpark_with_no_holdout_rows_does_not_crash_and_is_excluded(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows_1 = five_day_history
        # Carpark 2's history stops 2 full days before `now` -- entirely
        # before the 1-day holdout cutoff, so it contributes zero holdout
        # rows (but plenty of pre-holdout rows for training/comparators).
        start_2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        n_ticks_2 = 3 * 24 * 60 // 5  # 3 days, ending 2 days before `now`
        rows_2 = _slot_dependent_history_rows("2", start_2, n_ticks_2)

        db = FakeSupabaseDB(
            tables={
                "carparks": [
                    {"carpark_id": "1", "name": "Carpark One", "sinpa_index": None,
                     "active": True},
                    {"carpark_id": "2", "name": "Carpark Two", "sinpa_index": None,
                     "active": True},
                ],
                "carpark_history": rows_1 + rows_2,
                "model_config": [{"singleton": True, "active_model_version": None}],
            }
        )
        deps = _make_deps(db, now)

        result = run(deps)  # must not raise

        assert result.mae_candidate is not None  # carpark 1 still gates fine


class TestStorageUploadFailure:
    """Test Requirements case 14: Storage upload fails -> retry once
    (inside SupabaseREST; simulated here via FakeSupabaseDB.fail_upload)
    then /fail ping, abort promotion (never a silent skip)."""

    def test_upload_failure_pings_fail_and_raises_training_job_error(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows)
        db.fail_upload = True
        fail_ping = RecordingFailPing()
        deps = _make_deps(db, now, fail_ping=fail_ping)

        with pytest.raises(TrainingJobError, match="upload failed"):
            run(deps)

        assert fail_ping.reasons == [FAIL_REASON_MODEL_UPLOAD_FAILED]
        assert db.updated == []  # model_config never flipped
        # A training_runs row is still inserted, not a silent skip.
        assert len(db.inserted["training_runs"]) == 1
        assert db.inserted["training_runs"][0]["promoted"] is False
        assert "upload failed" in db.inserted["training_runs"][0]["notes"]


class TestSinpaIntegration:
    """Test Requirements cases 17-18 at the pipeline level."""

    def test_sinpa_available_sets_used_sinpa_true(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows, sinpa_index=1584)

        def fake_load_sinpa(
            mappings: Sequence[SinpaCarparkMapping],
        ) -> dict[str, list[TrainingRow]]:
            from gotparking_training.series import TimedSample, build_rows_from_series

            start = datetime(2020, 7, 1, tzinfo=timezone.utc)
            series = [
                TimedSample(start + timedelta(minutes=5 * i), _BASE_VALUE)
                for i in range(200)
            ]
            return {"1": build_rows_from_series("1", series)}

        deps = _make_deps(db, now, load_sinpa=fake_load_sinpa)

        result = run(deps)

        assert result.used_sinpa is True
        assert db.inserted["training_runs"][0]["used_sinpa"] is True

    def test_sinpa_failure_falls_back_to_live_only_without_crashing(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows, sinpa_index=1584)

        def failing_load_sinpa(
            mappings: Sequence[SinpaCarparkMapping],
        ) -> dict[str, list[TrainingRow]]:
            raise SinpaUnavailableError("HuggingFace unreachable")

        deps = _make_deps(db, now, load_sinpa=failing_load_sinpa)

        result = run(deps)  # must not raise

        assert result.used_sinpa is False
        assert result.promoted is True  # pipeline still completes normally
        assert db.inserted["training_runs"][0]["used_sinpa"] is False

    def test_no_sinpa_mapped_carparks_trains_live_only(
        self, five_day_history: tuple[datetime, list[HistoryRow]]
    ) -> None:
        now, rows = five_day_history
        db = _base_db(rows, sinpa_index=None)
        deps = _make_deps(db, now)

        result = run(deps)

        assert result.used_sinpa is False


class TestMainEntryPoint:
    """Test Requirements cases 13/15: crash -> /fail ping; weekly
    completion ping on success. Exercises `main()`'s control flow with
    `run`/`SupabaseREST`/`load_settings` monkeypatched, so these tests
    focus purely on main()'s exception-routing contract.
    """

    def test_success_sends_completion_ping_and_returns_0(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pings: dict[str, list[object]] = {"success": [], "fail": []}

        monkeypatch.setattr(
            "gotparking_training.train.load_settings",
            lambda: _FakeSettings("https://x.supabase.co", "key", "https://hc-ping.com/abc"),
        )
        monkeypatch.setattr("gotparking_training.train.SupabaseREST", _FakeSupabaseRESTClass)
        monkeypatch.setattr(
            "gotparking_training.train.run",
            lambda deps: RunResult(None, None, False, None, None, None, None, False, "ok"),
        )
        monkeypatch.setattr(
            "gotparking_training.train.ping_success", lambda url: pings["success"].append(url)
        )
        monkeypatch.setattr(
            "gotparking_training.train.ping_fail",
            lambda url, reason: pings["fail"].append(reason),
        )

        exit_code = main()

        assert exit_code == 0
        assert pings["success"] == ["https://hc-ping.com/abc"]
        assert pings["fail"] == []

    def test_unexpected_crash_pings_fail_with_generic_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pings: dict[str, list[object]] = {"success": [], "fail": []}

        monkeypatch.setattr(
            "gotparking_training.train.load_settings",
            lambda: _FakeSettings("https://x.supabase.co", "key", "https://hc-ping.com/abc"),
        )
        monkeypatch.setattr("gotparking_training.train.SupabaseREST", _FakeSupabaseRESTClass)

        def crashing_run(deps: object) -> RunResult:
            raise RuntimeError("unexpected bug")

        monkeypatch.setattr("gotparking_training.train.run", crashing_run)
        monkeypatch.setattr(
            "gotparking_training.train.ping_success", lambda url: pings["success"].append(url)
        )
        monkeypatch.setattr(
            "gotparking_training.train.ping_fail",
            lambda url, reason: pings["fail"].append(reason),
        )

        exit_code = main()

        assert exit_code == 1
        assert pings["success"] == []
        assert len(pings["fail"]) == 1
        fail_reason = pings["fail"][0]
        assert isinstance(fail_reason, str)
        assert "TRAINING_CRASH" in fail_reason
        assert "unexpected bug" in fail_reason

    def test_training_job_error_returns_1_without_a_second_ping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The raiser of TrainingJobError has already pinged with a
        precise reason -- main() must not fire a second, generic ping."""
        pings: dict[str, list[object]] = {"success": [], "fail": []}

        monkeypatch.setattr(
            "gotparking_training.train.load_settings",
            lambda: _FakeSettings("https://x.supabase.co", "key", "https://hc-ping.com/abc"),
        )
        monkeypatch.setattr("gotparking_training.train.SupabaseREST", _FakeSupabaseRESTClass)

        def already_handled_failure(deps: object) -> RunResult:
            raise TrainingJobError("model artifact upload failed")

        monkeypatch.setattr("gotparking_training.train.run", already_handled_failure)
        monkeypatch.setattr(
            "gotparking_training.train.ping_success", lambda url: pings["success"].append(url)
        )
        monkeypatch.setattr(
            "gotparking_training.train.ping_fail",
            lambda url, reason: pings["fail"].append(reason),
        )

        exit_code = main()

        assert exit_code == 1
        assert pings["success"] == []
        assert pings["fail"] == []  # already pinged inside run(), not re-pinged here

    def test_configuration_error_still_fires_a_fail_ping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A misconfigured/missing required secret (load_settings() raising,
        e.g. SUPABASE_URL unset) must still alert -- this failure happens
        before a Settings object exists, so main() must read the ping URL
        independently from the environment rather than only from settings.
        """
        success_calls: list[str | None] = []
        fail_calls: list[tuple[str | None, str]] = []
        monkeypatch.setenv("HEALTHCHECKS_TRAINING_PING_URL", "https://hc-ping.com/abc")

        def raise_missing_env() -> object:
            raise RuntimeError("missing required environment variable(s): SUPABASE_URL")

        monkeypatch.setattr("gotparking_training.train.load_settings", raise_missing_env)
        monkeypatch.setattr(
            "gotparking_training.train.ping_success", lambda url: success_calls.append(url)
        )
        monkeypatch.setattr(
            "gotparking_training.train.ping_fail",
            lambda url, reason: fail_calls.append((url, reason)),
        )

        exit_code = main()

        assert exit_code == 1
        assert success_calls == []
        assert len(fail_calls) == 1
        url, reason = fail_calls[0]
        assert url == "https://hc-ping.com/abc"
        assert "SUPABASE_URL" in reason


class _FakeSettings:
    def __init__(self, url: str, key: str, healthchecks_url: str | None) -> None:
        self.supabase_url = url
        self.supabase_service_role_key = key
        self.healthchecks_training_ping_url = healthchecks_url


class _FakeSupabaseRESTClass:
    """Stand-in for SupabaseREST's constructor signature; only `close()`
    is ever called on it by `main()`."""

    def __init__(self, base_url: str, service_role_key: str) -> None:
        self.base_url = base_url
        self.service_role_key = service_role_key

    def close(self) -> None:
        pass
