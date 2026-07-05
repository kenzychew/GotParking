"""Data-access helpers over `carparks`, `model_config`, and `training_runs`.

Thin, testable wrappers around `SupabaseClient` for the non-history tables
this job reads and writes. Kept separate from `data_loading.py` (which
focuses on `carpark_history` -- the larger, paginated table) so each module
stays small and single-purpose.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from gotparking_training.supabase_rest import SupabaseClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CarparkInfo:
    """One row from the `carparks` whitelist.

    Attributes:
        carpark_id: LTA DataMall carpark ID (string).
        name: Human-readable carpark name.
        sinpa_index: The carpark's column index into the SINPA dataset's
            1687-lot array, or None if this carpark is absent from SINPA
            (Raffles City, VivoCity P2 per docs/t0-sinpa-spike.md) -- those
            carparks train live-only, never via SINPA pretraining.
        active: Whether this carpark is currently in the served seed list.
    """

    carpark_id: str
    name: str
    sinpa_index: int | None
    active: bool


def load_active_carparks(db: SupabaseClient) -> list[CarparkInfo]:
    """Load the active-carpark whitelist (`carparks` where active=true).

    Args:
        db: Supabase client.

    Returns:
        One CarparkInfo per active carpark, including its SINPA mapping.
    """
    result = db.select(
        "carparks",
        params={"select": "carpark_id,name,sinpa_index,active", "active": "eq.true"},
    )
    return [
        CarparkInfo(
            carpark_id=row["carpark_id"],
            name=row["name"],
            sinpa_index=row.get("sinpa_index"),
            active=row["active"],
        )
        for row in result.rows
    ]


def load_active_model_version(db: SupabaseClient) -> str | None:
    """Load `model_config.active_model_version` (the singleton row).

    Args:
        db: Supabase client.

    Returns:
        The active version string, or None if no model has ever been
        promoted (the singleton row's `active_model_version` is null, or
        the row itself is somehow missing).
    """
    result = db.select("model_config", params={"select": "active_model_version", "limit": "1"})
    if not result.rows:
        return None
    return result.rows[0].get("active_model_version")


def promote_model_config(db: SupabaseClient, version: str, now: datetime) -> None:
    """Flip `model_config.active_model_version` to a newly promoted version.

    Args:
        db: Supabase client.
        version: The bare version string (no ".txt" suffix) to promote.
        now: The promotion instant, recorded as both `promoted_at` and
            `updated_at`.
    """
    db.update(
        "model_config",
        params={"singleton": "eq.true"},
        patch={
            "active_model_version": version,
            "promoted_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
    )
    logger.info("model_config promoted: active_model_version=%s", version)


def insert_training_run(
    db: SupabaseClient,
    *,
    candidate_version: str,
    phase: str,
    mae_candidate: float | None,
    mae_baseline: float | None,
    mae_persistence: float | None,
    mae_incumbent: float | None,
    used_sinpa: bool,
    promoted: bool,
    notes: str,
    ran_at: datetime,
) -> None:
    """Insert one audit row into `training_runs`.

    Per the design doc's step 8, a training_runs row is ALWAYS inserted for
    every completed backtest cycle (promoted or not) -- this is the
    Observability section's "promotion history" record. Column names match
    `db/schema.sql` exactly.

    Args:
        db: Supabase client.
        candidate_version: The candidate model's version string (still
            recorded even when `promoted` is False, for audit purposes).
        phase: "first_promotion" or "retrain" (see gate.py).
        mae_candidate: Candidate model's MAE on the holdout window.
        mae_baseline: Historical-average comparator's MAE.
        mae_persistence: Persistence comparator's MAE (the tracked,
            demo-facing metric per Premise #7 amended D8).
        mae_incumbent: Incumbent (currently-serving) model's MAE, only
            computed in the "retrain" phase -- None in "first_promotion".
        used_sinpa: Whether SINPA pretraining data was used this cycle.
        promoted: Whether this candidate was promoted.
        notes: Free-text note (e.g. "promoted", "did not beat gate",
            "promotion aborted: model artifact upload failed").
        ran_at: The training run's start instant.
    """
    row = {
        "ran_at": ran_at.isoformat(),
        "candidate_version": candidate_version,
        "phase": phase,
        "mae_candidate": mae_candidate,
        "mae_baseline": mae_baseline,
        "mae_persistence": mae_persistence,
        "mae_incumbent": mae_incumbent,
        "used_sinpa": used_sinpa,
        "promoted": promoted,
        "notes": notes,
    }
    db.insert("training_runs", [row])
    logger.info(
        "training_runs row inserted: version=%s phase=%s promoted=%s used_sinpa=%s",
        candidate_version, phase, promoted, used_sinpa,
    )
