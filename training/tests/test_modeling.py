"""Tests for gotparking_training.modeling (train/predict/MAE)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from gotparking_training.modeling import (
    mean_absolute_error,
    predict,
    rows_to_arrays,
    train_candidate,
)
from gotparking_training.series import TrainingRow


def _synthetic_rows(
    n: int, seed: int = 0, label_fn: Callable[[list[float]], float] | None = None
) -> list[TrainingRow]:
    """`n` synthetic rows with random-but-deterministic features; label
    defaults to `lots_now` (i.e. a trivially learnable persistence-like
    target) unless `label_fn(features) -> float` is supplied.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        lots_now = float(rng.integers(0, 200))
        features = [
            float(rng.integers(0, 7)),
            float(rng.integers(0, 96)),
            float(rng.integers(0, 2)),
            lots_now,
            float(rng.integers(0, 200)),
            float(rng.integers(0, 200)),
            float(rng.integers(0, 200)),
        ]
        label = label_fn(features) if label_fn else lots_now
        rows.append(
            TrainingRow(
                carpark_id="1",
                base_time=None,  # type: ignore[arg-type]
                target_time=None,  # type: ignore[arg-type]
                features=features,
                label=label,
                lots_now=lots_now,
            )
        )
    return rows


class TestRowsToArrays:
    def test_shapes_and_order(self) -> None:
        rows = _synthetic_rows(5)
        x, y = rows_to_arrays(rows)

        assert x.shape == (5, 7)
        assert y.shape == (5,)
        assert list(x[0]) == rows[0].features
        assert y[0] == rows[0].label

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="zero rows"):
            rows_to_arrays([])


class TestTrainCandidateLiveOnly:
    """Test Requirements case 5: train happy path (live-only)."""

    def test_produces_a_working_booster(self) -> None:
        rows = _synthetic_rows(60, seed=1)
        params = {
            "objective": "regression",
            "verbosity": -1,
            "min_data_in_leaf": 1,
            "min_data_in_bin": 1,
            "num_leaves": 7,
            "seed": 42,
        }

        booster = train_candidate(rows, sinpa_rows=None, params=params, num_boost_round=20)
        preds = predict(booster, rows)

        assert preds.shape == (60,)
        # lots_now is directly one of the features -- a well-behaved model
        # should learn this near-trivial relationship reasonably well.
        mae = mean_absolute_error(list(preds), [r.label for r in rows])
        assert mae < 30.0

    def test_empty_sinpa_rows_behaves_like_none(self) -> None:
        rows = _synthetic_rows(30, seed=2)
        params = {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1,
                  "min_data_in_bin": 1, "num_leaves": 3, "seed": 42}

        booster_none = train_candidate(rows, sinpa_rows=None, params=params, num_boost_round=5)
        booster_empty = train_candidate(rows, sinpa_rows=[], params=params, num_boost_round=5)

        preds_none = predict(booster_none, rows)
        preds_empty = predict(booster_empty, rows)
        assert list(preds_none) == list(preds_empty)


class TestTrainCandidatePretrainFineTune:
    """Test Requirements case 17: pretrain -> fine-tune path."""

    def test_produces_a_working_booster_from_sinpa_plus_live(self) -> None:
        sinpa_rows = _synthetic_rows(60, seed=10)
        live_rows = _synthetic_rows(40, seed=11)
        params = {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1,
                  "min_data_in_bin": 1, "num_leaves": 7, "seed": 42}

        booster = train_candidate(
            live_rows, sinpa_rows=sinpa_rows, params=params, num_boost_round=15
        )
        preds = predict(booster, live_rows)

        assert preds.shape == (40,)
        assert all(np.isfinite(preds))

    def test_pretrained_differs_from_live_only_in_general(self) -> None:
        """Not a strict requirement, but demonstrates init_model is
        actually being threaded through (fine-tuning from a pretrained
        starting point produces a different booster than training from
        scratch on the exact same live data)."""
        sinpa_rows = _synthetic_rows(60, seed=20, label_fn=lambda f: f[3] * 2 + 10)
        live_rows = _synthetic_rows(40, seed=21)
        params = {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1,
                  "min_data_in_bin": 1, "num_leaves": 7, "seed": 42}

        booster_pretrained = train_candidate(
            live_rows, sinpa_rows=sinpa_rows, params=params, num_boost_round=10
        )
        booster_live_only = train_candidate(
            live_rows, sinpa_rows=None, params=params, num_boost_round=10
        )

        preds_pretrained = predict(booster_pretrained, live_rows)
        preds_live_only = predict(booster_live_only, live_rows)
        assert not np.allclose(preds_pretrained, preds_live_only)


class TestPredict:
    def test_shape_matches_input_rows(self) -> None:
        rows = _synthetic_rows(10, seed=3)
        params = {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1,
                  "min_data_in_bin": 1, "num_leaves": 3, "seed": 42}
        booster = train_candidate(rows, sinpa_rows=None, params=params, num_boost_round=3)

        preds = predict(booster, rows)

        assert preds.shape == (10,)


class TestMeanAbsoluteError:
    def test_basic_computation(self) -> None:
        assert mean_absolute_error([1.0, 2.0, 3.0], [1.0, 4.0, 3.0]) == pytest.approx(2.0 / 3.0)

    def test_zero_error(self) -> None:
        assert mean_absolute_error([5.0, 5.0], [5.0, 5.0]) == 0.0

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="zero rows"):
            mean_absolute_error([], [])

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            mean_absolute_error([1.0, 2.0], [1.0])
