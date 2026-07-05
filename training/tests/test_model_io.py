"""Tests for gotparking_training.model_io.

Covers Test Requirements case 19: the uploaded path is exactly
`{version}.txt`, and the version passed to model_config is the bare
version string (this file asserts the artifact-contract half; test_train.py
asserts the model_config-patch half in the context of a full run).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import lightgbm
import numpy as np

from gotparking_training.model_io import (
    download_incumbent_booster,
    make_version,
    upload_model_artifact,
)
from tests.fakes import FakeSupabaseDB


class TestMakeVersion:
    def test_exact_format_for_a_known_instant(self) -> None:
        now = datetime(2026, 7, 6, 5, 0, 0, tzinfo=timezone.utc)
        assert make_version(now) == "lgbm_20260706_050000"

    def test_matches_the_documented_pattern(self) -> None:
        now = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        version = make_version(now)

        assert re.fullmatch(r"lgbm_\d{8}_\d{6}", version)
        assert not version.endswith(".txt")

    def test_is_utc_based_not_local(self) -> None:
        # No tzinfo conversion happens inside make_version -- it trusts the
        # caller passes a UTC instant, exactly like every other timestamp
        # in this codebase (see sg_time.py's _to_sgt_naive docstring).
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert make_version(now) == "lgbm_20260101_000000"


def _tiny_booster() -> lightgbm.Booster:
    rng = np.random.default_rng(7)
    n = 20
    x = rng.random((n, 7))
    y = x[:, 3]
    dataset = lightgbm.Dataset(x, label=y)
    params = {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1,
              "min_data_in_bin": 1, "num_leaves": 3}
    return lightgbm.train(params, dataset, num_boost_round=3)


class TestUploadModelArtifact:
    def test_uploads_to_exact_path_with_bare_version(self) -> None:
        db = FakeSupabaseDB()
        booster = _tiny_booster()

        upload_model_artifact(db, "lgbm_20260706_050000", booster)

        assert len(db.upload_calls) == 1
        bucket, path, content = db.upload_calls[0]
        assert bucket == "models"
        assert path == "lgbm_20260706_050000.txt"  # exactly {version}.txt
        assert content == booster.model_to_string().encode("utf-8")

    def test_uploaded_content_is_valid_lightgbm_text_format(self) -> None:
        db = FakeSupabaseDB()
        booster = _tiny_booster()

        upload_model_artifact(db, "lgbm_x", booster)

        _, _, content = db.upload_calls[0]
        # Must round-trip through the exact same construction serving uses:
        # lightgbm.Booster(model_str=<decoded utf-8 text>).
        reloaded = lightgbm.Booster(model_str=content.decode("utf-8"))
        sample = np.zeros((1, 7))
        assert np.asarray(reloaded.predict(sample)).shape == (1,)


class TestDownloadIncumbentBooster:
    def test_downloads_from_exact_path_and_parses(self) -> None:
        booster = _tiny_booster()
        db = FakeSupabaseDB(
            storage={"models/lgbm_20260628_050000.txt": booster.model_to_string().encode("utf-8")}
        )

        incumbent = download_incumbent_booster(db, "lgbm_20260628_050000")

        assert db.download_calls == ["models/lgbm_20260628_050000.txt"]
        sample = np.zeros((1, 7))
        assert np.asarray(incumbent.predict(sample)).shape == (1,)

    def test_upload_then_download_round_trips_predictions(self) -> None:
        db = FakeSupabaseDB()
        booster = _tiny_booster()
        sample = np.random.default_rng(1).random((5, 7))

        upload_model_artifact(db, "lgbm_20260706_050000", booster)
        reloaded = download_incumbent_booster(db, "lgbm_20260706_050000")

        np.testing.assert_array_equal(booster.predict(sample), reloaded.predict(sample))
