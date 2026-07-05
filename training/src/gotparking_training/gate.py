"""Two-phase promotion gate (design doc Premise #7, amended D9).

Phase 1 ("first_promotion", when `model_config.active_model_version` is
null): the candidate must beat BOTH comparators by at least the phase-1
margin. Phase 2 ("retrain", once a model has been promoted at least once):
the candidate is promoted UNLESS it is worse than the incumbent by more
than the phase-2 epsilon -- a flat 10%-vs-incumbent bar would structurally
freeze the model at its first promoted version, since weekly retrains
rarely jump 10% again.
"""

from __future__ import annotations

from gotparking_training.config import PHASE1_MARGIN, PHASE2_EPSILON

#: `model_config.active_model_version` is null -- no model has ever been
#: promoted. The candidate must beat both comparators by PHASE1_MARGIN.
PHASE_FIRST_PROMOTION = "first_promotion"

#: A model is already serving. The candidate is promoted unless it is
#: worse than the incumbent by more than PHASE2_EPSILON.
PHASE_RETRAIN = "retrain"


def determine_phase(active_model_version: str | None) -> str:
    """Decide which gate phase applies this cycle.

    Args:
        active_model_version: `model_config.active_model_version`, as
            loaded from Supabase (None if no model has ever been promoted).

    Returns:
        `PHASE_FIRST_PROMOTION` if no model has ever been promoted, else
        `PHASE_RETRAIN`.
    """
    return PHASE_FIRST_PROMOTION if active_model_version is None else PHASE_RETRAIN


def decide_promotion(
    phase: str,
    mae_candidate: float,
    mae_baseline: float,
    mae_persistence: float,
    mae_incumbent: float | None,
) -> bool:
    """Decide whether the candidate should be promoted this cycle.

    Args:
        phase: `PHASE_FIRST_PROMOTION` or `PHASE_RETRAIN`, from
            `determine_phase`.
        mae_candidate: The candidate model's MAE on the holdout window.
        mae_baseline: The historical-average comparator's MAE on the same
            holdout window (ignored in the retrain phase).
        mae_persistence: The persistence comparator's MAE on the same
            holdout window (ignored in the retrain phase).
        mae_incumbent: The incumbent (currently-serving) model's MAE on
            the same holdout window. Required (non-None) in the retrain
            phase; ignored in the first_promotion phase.

    Returns:
        True if the candidate should be promoted.

    Raises:
        ValueError: If `phase` is `PHASE_RETRAIN` and `mae_incumbent` is
            None -- the retrain phase cannot gate without an incumbent MAE
            to compare against; this indicates a caller bug (the incumbent
            should always have been evaluated before reaching this call),
            not a normal runtime condition.

    Boundary semantics (both tested exactly, per the design doc's "test
    both boundaries exactly (<=/>)" instruction):
      * Phase 1: promote if `mae_candidate <= PHASE1_MARGIN * mae_baseline`
        AND `mae_candidate <= PHASE1_MARGIN * mae_persistence` -- exactly
        at the 10%-better boundary (mae_candidate == 0.9 * comparator)
        still promotes (uses <=, not <).
      * Phase 2: promote UNLESS
        `mae_candidate > PHASE2_EPSILON * mae_incumbent` -- exactly at the
        2%-worse boundary (mae_candidate == 1.02 * mae_incumbent) still
        promotes (the rejection condition is a strict >, not >=).
    """
    if phase == PHASE_FIRST_PROMOTION:
        return (
            mae_candidate <= PHASE1_MARGIN * mae_baseline
            and mae_candidate <= PHASE1_MARGIN * mae_persistence
        )
    if mae_incumbent is None:
        raise ValueError("retrain phase requires a non-None mae_incumbent")
    return not (mae_candidate > PHASE2_EPSILON * mae_incumbent)
