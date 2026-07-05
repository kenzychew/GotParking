"""Environment-variable settings and tunable constants for T5.

Tunable thresholds live here as named constants so the pipeline modules
read like the design doc's prose instead of embedding magic numbers.
Several constants MUST match `api/_lib/config.py` exactly (documented per
constant below) -- these are the other half of the CRITICAL INTEGRATION
CONTRACT: a training job that excludes cold-start carparks differently
than the serving side would train on carparks that serving would never
consider warmed up (or vice versa).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# --- Cross-service contract constants (MUST match api/_lib/config.py) -----

#: Cold-start thresholds (Premise #10): a carpark is excluded from training
#: if its first sample is younger than this age, OR it has fewer than this
#: many samples in `carpark_history`. MUST match
#: `api/_lib/config.py::COLD_START_MIN_AGE_HOURS` exactly -- a mismatch
#: would mean training and serving disagree about which carparks are
#: "warmed up".
COLD_START_MIN_AGE_HOURS = 72

#: MUST match `api/_lib/config.py::COLD_START_MIN_SAMPLES` exactly.
COLD_START_MIN_SAMPLES = 10

#: Forecast horizon: labels are the observed value at
#: `base_time + FORECAST_HORIZON_MINUTES`. MUST match
#: `api/_lib/config.py::FORECAST_HORIZON_MINUTES` exactly.
FORECAST_HORIZON_MINUTES = 20

#: Momentum lookback offsets (minutes before the base time), in the exact
#: order the feature contract expects: 15m, 30m, 60m.
MOMENTUM_OFFSETS_MINUTES: tuple[int, ...] = (15, 30, 60)

#: Nearest-sample join tolerance for both momentum offsets and the label
#: (design doc: "the sample nearest t+20min within +/-2.5 min, else the
#: row is dropped" -- Premise #7 amended, label construction rule).
JOIN_TOLERANCE_MINUTES = 2.5

#: Supabase Storage bucket holding LightGBM model artifacts. MUST match
#: `api/_lib/config.py::MODEL_STORAGE_BUCKET` exactly -- this is where the
#: batch-predict function looks for the artifact this job uploads.
MODEL_STORAGE_BUCKET = "models"

#: PostgREST page size used when paginating `carpark_history`, which can
#: hold hundreds of thousands of rows well past any default per-request cap.
POSTGREST_PAGE_SIZE = 1000

# --- Training-only tunables ------------------------------------------------

#: Holdout window: the most recent N days of live data are held out for the
#: leakage-free backtest (Premise #7, amended D6). Comparators and the
#: candidate model are trained/fitted on data strictly before this window.
HOLDOUT_DAYS = 3

#: Phase-1 (baseline -> first ML promotion) margin: candidate MAE must be
#: <= this fraction of EACH comparator's MAE to promote (Premise #7,
#: amended D9). 0.9 == "at least 10% better".
PHASE1_MARGIN = 0.9

#: Phase-2 (retrain vs incumbent) epsilon: candidate MAE must be
#: <= this multiple of the incumbent's MAE to promote (anti-noise
#: tolerance; design doc: "unless WORSE than the incumbent by >2% MAE").
PHASE2_EPSILON = 1.02

#: Default LightGBM hyperparameters. Deliberately tolerant of small
#: datasets (min_data_in_leaf/min_data_in_bin=1, few leaves) since both the
#: unit tests and the early weeks of live production data involve small
#: row counts; seeded for reproducibility per the project's ML standards.
DEFAULT_LGBM_PARAMS: dict[str, object] = {
    "objective": "regression",
    "metric": "mae",
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
    "num_leaves": 15,
    "min_data_in_leaf": 1,
    "min_data_in_bin": 1,
    "learning_rate": 0.1,
}

#: Default boosting rounds for both the SINPA pretrain stage and the live
#: fine-tune/from-scratch stage.
DEFAULT_NUM_BOOST_ROUND = 100

#: Reason strings sent as the healthchecks `/fail` ping body -- kept as
#: constants so tests and code agree on the exact string.
FAIL_REASON_TRAINING_CRASH = "TRAINING_CRASH"
FAIL_REASON_MODEL_UPLOAD_FAILED = "MODEL_UPLOAD_FAILED"
FAIL_REASON_SUPABASE_UNAVAILABLE = "SUPABASE_UNAVAILABLE"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables.

    Attributes:
        supabase_url: Base Supabase project URL, no path suffix (e.g.
            "https://xyz.supabase.co").
        supabase_service_role_key: Service-role key; bypasses RLS. Used
            server-side only, never exposed anywhere client-facing.
        healthchecks_training_ping_url: Optional base ping URL (no trailing
            path segment) for this job's healthchecks.io check. A success
            ping (bare GET) is sent on successful completion; a `/fail`
            ping (POST with a reason body) is sent on crash or a hard
            failure such as a Storage upload failure. None (unset) means
            pinging is skipped entirely -- never raises either way.
    """

    supabase_url: str
    supabase_service_role_key: str
    healthchecks_training_ping_url: str | None


def load_settings() -> Settings:
    """Load :class:`Settings` from process environment variables.

    Returns:
        A populated Settings instance.

    Raises:
        RuntimeError: If a required environment variable is missing or
            blank. This indicates a deployment misconfiguration (a missing
            GitHub Actions secret), not a normal runtime failure mode --
            there is no sensible fallback for a missing Supabase URL or
            service-role key.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    healthchecks_url = os.environ.get("HEALTHCHECKS_TRAINING_PING_URL", "").strip() or None

    required = {
        "SUPABASE_URL": supabase_url,
        "SUPABASE_SERVICE_ROLE_KEY": supabase_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required environment variable(s): {', '.join(missing)}")

    return Settings(
        supabase_url=supabase_url.rstrip("/"),
        supabase_service_role_key=supabase_key,
        healthchecks_training_ping_url=healthchecks_url,
    )
