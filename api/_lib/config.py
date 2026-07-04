"""Environment-variable settings and tunable constants for T4.

Shared by both endpoints (``api/batch_predict.py`` and ``api/forecast.py``).
Tunable thresholds live here as named constants so the business-logic
modules read like the design doc's prose instead of embedding magic numbers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# --- Tunable constants -------------------------------------------------

#: Forecast horizon: predictions target `now + FORECAST_HORIZON_MINUTES`
#: (Design Details: "Forecast horizon ... 20 minutes ahead, a single fixed
#: horizon").
FORECAST_HORIZON_MINUTES = 20

#: Cold-start thresholds (Premise #10): a carpark is `cold_start` if its
#: first sample is younger than this age, OR it has fewer than this many
#: samples in `carpark_history`.
COLD_START_MIN_AGE_HOURS = 72
COLD_START_MIN_SAMPLES = 10

#: A `carpark_momentum` row older than this is treated as missing (Premise
#: #11, amended D5) -- that carpark is served via the baseline path this
#: cycle instead of feeding stale rate-of-change inputs to the model.
MOMENTUM_FRESHNESS_MINUTES = 15

#: Capacity-relative tier thresholds (Design Details: "Availability
#: color-coding" / capacity-relative tiers). ratio = forecast_lots / capacity.
TIER_PLENTY_RATIO = 0.30
TIER_LIMITED_RATIO = 0.10

#: PostgREST page size used when paginating tables that can exceed the
#: server's default max-rows-per-request. `carpark_baseline` can hold up to
#: (carparks x 7 dow x 96 slots) rows once the bootstrap window has elapsed,
#: which is well past PostgREST's common 1000-row default cap.
POSTGREST_PAGE_SIZE = 1000

#: Supabase Storage bucket holding LightGBM model artifacts (see
#: db/schema.sql's Storage bucket registration).
MODEL_STORAGE_BUCKET = "models"

#: Reason strings sent as the healthchecks `/fail` ping body -- kept as
#: constants so tests and code agree on the exact string.
FAIL_REASON_MODEL_ARTIFACT_MISSING = "MODEL_ARTIFACT_MISSING"
FAIL_REASON_SUPABASE_UNAVAILABLE = "SUPABASE_UNAVAILABLE"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables.

    Attributes:
        supabase_url: Base Supabase project URL, no path suffix (e.g.
            "https://xyz.supabase.co").
        supabase_service_role_key: Service-role key; bypasses RLS. Used
            server-side only, never exposed to the frontend.
        batch_shared_secret: Shared secret the poller sends via the
            `x-batch-secret` header to authorize a batch-predict run.
        healthchecks_training_ping_url: Optional base ping URL (no trailing
            path segment) for the training job's healthchecks.io check.
            Reused here for batch predict's `/fail` pings -- see
            `healthchecks.py` for the reasoning and the exact scope.
    """

    supabase_url: str
    supabase_service_role_key: str
    batch_shared_secret: str
    healthchecks_training_ping_url: str | None


def load_settings() -> Settings:
    """Load :class:`Settings` from process environment variables.

    Returns:
        A populated Settings instance.

    Raises:
        RuntimeError: If a required environment variable is missing or
            blank. This indicates a deployment misconfiguration, not a
            normal runtime failure mode -- there is no sensible fallback for
            a missing Supabase URL, service-role key, or shared secret.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    batch_secret = os.environ.get("BATCH_SHARED_SECRET", "").strip()
    healthchecks_url = os.environ.get("HEALTHCHECKS_TRAINING_PING_URL", "").strip() or None

    required = {
        "SUPABASE_URL": supabase_url,
        "SUPABASE_SERVICE_ROLE_KEY": supabase_key,
        "BATCH_SHARED_SECRET": batch_secret,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required environment variable(s): {', '.join(missing)}")

    return Settings(
        supabase_url=supabase_url.rstrip("/"),
        supabase_service_role_key=supabase_key,
        batch_shared_secret=batch_secret,
        healthchecks_training_ping_url=healthchecks_url,
    )
