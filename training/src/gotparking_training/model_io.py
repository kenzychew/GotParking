"""Model artifact versioning, upload, and incumbent download.

CRITICAL INTEGRATION CONTRACT (verified against `api/_lib/batch_logic.py`):
serving does
``download_storage_object(MODEL_STORAGE_BUCKET, f"{active_version}.txt")``
then ``lightgbm.Booster(model_str=<decoded utf-8 text>)``. This module is
the write-side counterpart: it must upload to the SAME bucket, at exactly
``f"{version}.txt"``, as LightGBM's text format, and the version string
`model_config.active_model_version` is set to must be the BARE version
(no ".txt" suffix) -- Test Requirements case 19 asserts both of these
exactly.
"""

from __future__ import annotations

import logging
from datetime import datetime

import lightgbm

from gotparking_training.config import MODEL_STORAGE_BUCKET
from gotparking_training.supabase_rest import SupabaseClient

logger = logging.getLogger(__name__)

#: strftime format for the version string: `lgbm_YYYYMMDD_HHMMSS`, UTC.
_VERSION_FORMAT = "lgbm_%Y%m%d_%H%M%S"


def make_version(now: datetime) -> str:
    """Build the version string for a new model artifact.

    Args:
        now: The current UTC instant (the training run's start time).

    Returns:
        A version string of the form ``lgbm_YYYYMMDD_HHMMSS`` (UTC, bare,
        no file extension) -- e.g. ``lgbm_20260706_050000``.
    """
    return now.strftime(_VERSION_FORMAT)


def upload_model_artifact(db: SupabaseClient, version: str, booster: lightgbm.Booster) -> None:
    """Upload a trained booster to Supabase Storage in LightGBM text format.

    Args:
        db: Supabase client. Its `upload_storage_object` already retries
            once internally (see `supabase_rest.SupabaseREST`); this
            function does not add a second retry layer -- a failure here
            propagates as `SupabaseUnavailableError` for the caller
            (`train.py`) to handle per the design doc's "retry once, then
            /fail ping, abort promotion" contract.
        version: The bare version string (from `make_version`).
        booster: The trained candidate model.

    Raises:
        SupabaseUnavailableError: If the upload fails after its retry.
    """
    model_str = booster.model_to_string()
    path = f"{version}.txt"
    db.upload_storage_object(MODEL_STORAGE_BUCKET, path, model_str.encode("utf-8"))
    logger.info(
        "model artifact uploaded: bucket=%s path=%s bytes=%d",
        MODEL_STORAGE_BUCKET, path, len(model_str),
    )


def download_incumbent_booster(db: SupabaseClient, active_version: str) -> lightgbm.Booster:
    """Download and parse the currently-serving model artifact.

    Uses the exact same download path serving uses
    (`download_storage_object(MODEL_STORAGE_BUCKET, f"{version}.txt")` then
    `lightgbm.Booster(model_str=...)`), so the retrain-phase incumbent
    evaluation is guaranteed to load the same artifact serving would load
    for the same version.

    Args:
        db: Supabase client.
        active_version: `model_config.active_model_version` (bare, no
            ".txt" suffix).

    Returns:
        The incumbent Booster, ready to predict on the holdout window.

    Raises:
        SupabaseUnavailableError: If the download fails after its retry.
    """
    path = f"{active_version}.txt"
    model_str = db.download_storage_object(MODEL_STORAGE_BUCKET, path).decode("utf-8")
    return lightgbm.Booster(model_str=model_str)
