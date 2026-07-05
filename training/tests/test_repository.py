"""Tests for gotparking_training.repository (carparks/model_config/training_runs)."""

from __future__ import annotations

from datetime import datetime, timezone

from gotparking_training.repository import (
    CarparkInfo,
    insert_training_run,
    load_active_carparks,
    load_active_model_version,
    promote_model_config,
)
from tests.fakes import FakeSupabaseDB

_NOW = datetime(2026, 7, 6, 5, 0, tzinfo=timezone.utc)


class TestLoadActiveCarparks:
    def test_returns_carparks_with_sinpa_mapping(self) -> None:
        db = FakeSupabaseDB(
            tables={
                "carparks": [
                    {"carpark_id": "1", "name": "Suntec City", "sinpa_index": 1584,
                     "active": True},
                    {"carpark_id": "3", "name": "Raffles City", "sinpa_index": None,
                     "active": True},
                    {"carpark_id": "99", "name": "Retired Carpark", "sinpa_index": None,
                     "active": False},
                ]
            }
        )

        carparks = load_active_carparks(db)

        assert carparks == [
            CarparkInfo("1", "Suntec City", 1584, True),
            CarparkInfo("3", "Raffles City", None, True),
        ]

    def test_only_selects_active_true(self) -> None:
        db = FakeSupabaseDB(tables={"carparks": []})
        load_active_carparks(db)

        assert db.select_calls[0][1]["active"] == "eq.true"


class TestLoadActiveModelVersion:
    def test_returns_version_when_set(self) -> None:
        db = FakeSupabaseDB(
            tables={"model_config": [{"active_model_version": "lgbm_20260628_050000"}]}
        )
        assert load_active_model_version(db) == "lgbm_20260628_050000"

    def test_returns_none_when_null(self) -> None:
        db = FakeSupabaseDB(tables={"model_config": [{"active_model_version": None}]})
        assert load_active_model_version(db) is None

    def test_returns_none_when_row_missing(self) -> None:
        db = FakeSupabaseDB(tables={"model_config": []})
        assert load_active_model_version(db) is None


class TestPromoteModelConfig:
    def test_patches_singleton_row_with_version_and_timestamps(self) -> None:
        db = FakeSupabaseDB(tables={"model_config": [{"singleton": True,
                                                        "active_model_version": None}]})

        promote_model_config(db, "lgbm_20260706_050000", _NOW)

        assert len(db.updated) == 1
        table, params, patch = db.updated[0]
        assert table == "model_config"
        assert params == {"singleton": "eq.true"}
        assert patch["active_model_version"] == "lgbm_20260706_050000"
        assert patch["promoted_at"] == _NOW.isoformat()
        assert patch["updated_at"] == _NOW.isoformat()


class TestInsertTrainingRun:
    def test_inserts_full_row_shape(self) -> None:
        db = FakeSupabaseDB()

        insert_training_run(
            db,
            candidate_version="lgbm_20260706_050000",
            phase="first_promotion",
            mae_candidate=10.0,
            mae_baseline=15.0,
            mae_persistence=20.0,
            mae_incumbent=None,
            used_sinpa=True,
            promoted=True,
            notes="promoted",
            ran_at=_NOW,
        )

        assert len(db.inserted["training_runs"]) == 1
        row = db.inserted["training_runs"][0]
        assert row["candidate_version"] == "lgbm_20260706_050000"
        assert row["phase"] == "first_promotion"
        assert row["mae_candidate"] == 10.0
        assert row["mae_baseline"] == 15.0
        assert row["mae_persistence"] == 20.0
        assert row["mae_incumbent"] is None
        assert row["used_sinpa"] is True
        assert row["promoted"] is True
        assert row["notes"] == "promoted"
        assert row["ran_at"] == _NOW.isoformat()

    def test_retrain_phase_can_carry_mae_incumbent(self) -> None:
        db = FakeSupabaseDB()

        insert_training_run(
            db,
            candidate_version="lgbm_20260713_050000",
            phase="retrain",
            mae_candidate=9.0,
            mae_baseline=15.0,
            mae_persistence=20.0,
            mae_incumbent=9.5,
            used_sinpa=False,
            promoted=True,
            notes="promoted",
            ran_at=_NOW,
        )

        row = db.inserted["training_runs"][0]
        assert row["mae_incumbent"] == 9.5
        assert row["used_sinpa"] is False
