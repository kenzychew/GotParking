"""LightGBM training, prediction, and MAE helpers.

Design doc reference: Premise #7 (train strictly on pre-holdout data),
task T5 step 5 ("pretrain on SINPA then init_model fine-tune on live rows
when SINPA available; live-only otherwise").
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import lightgbm
import numpy as np
from numpy.typing import NDArray

from gotparking_training.config import DEFAULT_LGBM_PARAMS, DEFAULT_NUM_BOOST_ROUND
from gotparking_training.features import FEATURE_NAMES
from gotparking_training.series import TrainingRow

logger = logging.getLogger(__name__)


def rows_to_arrays(
    rows: Sequence[TrainingRow],
) -> tuple[NDArray[np.floating[Any]], NDArray[np.floating[Any]]]:
    """Convert TrainingRows into LightGBM-ready feature/label arrays.

    Args:
        rows: Training rows to convert.

    Returns:
        A `(X, y)` pair: `X` has shape (len(rows), 7) in `FEATURE_NAMES`
        order; `y` has shape (len(rows),).

    Raises:
        ValueError: If `rows` is empty -- an empty dataset cannot be
            converted to a meaningful array shape, and callers should
            check for this before training/predicting rather than relying
            on this function to produce a usable empty array.
    """
    if not rows:
        raise ValueError("cannot build arrays from zero rows")
    features = np.array([row.features for row in rows], dtype=np.float64)
    labels = np.array([row.label for row in rows], dtype=np.float64)
    return features, labels


def train_candidate(
    live_rows: Sequence[TrainingRow],
    sinpa_rows: Sequence[TrainingRow] | None,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
) -> lightgbm.Booster:
    """Train the candidate LightGBM model, strictly on pre-holdout data.

    Args:
        live_rows: Pre-holdout live TrainingRows. Must be non-empty.
        sinpa_rows: Pre-holdout SINPA pretraining TrainingRows, or None/
            empty when SINPA is unavailable this cycle. When present, a
            first booster is trained on `sinpa_rows` and passed as
            `init_model` to the live training call (pretrain-then-fine-
            tune, T5 step 5); when absent, the candidate trains live-only.
        params: LightGBM hyperparameters. Defaults to
            `config.DEFAULT_LGBM_PARAMS`.
        num_boost_round: Boosting rounds for both stages.

    Returns:
        The trained candidate Booster.
    """
    resolved_params = dict(DEFAULT_LGBM_PARAMS if params is None else params)
    live_x, live_y = rows_to_arrays(live_rows)
    live_dataset = lightgbm.Dataset(live_x, label=live_y, feature_name=list(FEATURE_NAMES))

    if sinpa_rows:
        logger.info("train_candidate: pretraining on %d SINPA rows", len(sinpa_rows))
        sinpa_x, sinpa_y = rows_to_arrays(sinpa_rows)
        sinpa_dataset = lightgbm.Dataset(
            sinpa_x, label=sinpa_y, feature_name=list(FEATURE_NAMES)
        )
        pretrain_booster = lightgbm.train(
            resolved_params, sinpa_dataset, num_boost_round=num_boost_round
        )
        logger.info(
            "train_candidate: fine-tuning on %d live rows (init_model=SINPA pretrain)",
            len(live_rows),
        )
        return lightgbm.train(
            resolved_params,
            live_dataset,
            num_boost_round=num_boost_round,
            init_model=pretrain_booster,
        )

    logger.info("train_candidate: training live-only on %d rows (no SINPA)", len(live_rows))
    return lightgbm.train(resolved_params, live_dataset, num_boost_round=num_boost_round)


def predict(booster: lightgbm.Booster, rows: Sequence[TrainingRow]) -> NDArray[np.floating[Any]]:
    """Run a booster's predictions over a set of TrainingRows.

    Args:
        booster: The (candidate or incumbent) model.
        rows: Rows to predict for. Must be non-empty.

    Returns:
        Predictions, shape (len(rows),).
    """
    features, _ = rows_to_arrays(rows)
    result: NDArray[np.floating[Any]] = booster.predict(features)  # type: ignore[assignment]
    return result


def mean_absolute_error(preds: Sequence[float], actuals: Sequence[float]) -> float:
    """Compute mean absolute error between two equal-length sequences.

    Args:
        preds: Predicted values.
        actuals: Observed (ground-truth) values.

    Returns:
        `mean(abs(preds - actuals))`.

    Raises:
        ValueError: If `preds` is empty, or the two sequences differ in
            length -- both indicate a caller bug (an empty holdout window
            must be handled by the caller BEFORE reaching this function,
            per the design doc's "never divide by zero" requirement).
    """
    if len(preds) == 0:
        raise ValueError("cannot compute MAE over zero rows")
    if len(preds) != len(actuals):
        raise ValueError(f"preds/actuals length mismatch: {len(preds)} != {len(actuals)}")
    arr_preds = np.asarray(preds, dtype=np.float64)
    arr_actuals = np.asarray(actuals, dtype=np.float64)
    return float(np.mean(np.abs(arr_preds - arr_actuals)))
