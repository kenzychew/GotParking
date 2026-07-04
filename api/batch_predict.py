"""Vercel Python serverless function: POST /api/batch_predict.

Secret-gated, poller-triggered batch-predict endpoint (design doc T4,
Premise #9 amended D10). Computes and upserts every active carpark's
forecast for this cycle. All business logic lives in
`_lib/batch_logic.py`; this file is thin glue between Vercel's Python
runtime (a `handler(BaseHTTPRequestHandler)` class per file in `api/`) and
that pure, independently-tested logic. Files under `api/_lib/` are not
routed (leading underscore), per Vercel's Python function convention.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from _lib.batch_logic import BatchDeps, handle_batch_predict
from _lib.config import load_settings
from _lib.healthchecks import fire_fail_ping
from _lib.http_helpers import unexpected_error_response, write_http_response
from _lib.model_cache import get_shared_cache
from _lib.supabase_rest import SupabaseREST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _build_deps() -> BatchDeps:
    """Wire real (env/network) dependencies for one batch-predict request.

    A fresh SupabaseREST/httpx.Client is created per invocation and closed
    at the end of the request (see `handler.do_POST`); the LightGBM model
    cache is the process-wide singleton so warm-instance reuse (Premise
    #9: reload the artifact only when `active_model_version` changes)
    actually persists across invocations of the same warm Vercel instance.

    Returns:
        A populated BatchDeps.

    Raises:
        RuntimeError: If a required environment variable is missing (see
            `_lib.config.load_settings`) -- caught by the caller's
            top-level try/except, which still returns a well-formed JSON
            error response rather than a raw crash.
    """
    settings = load_settings()
    db = SupabaseREST(settings.supabase_url, settings.supabase_service_role_key)

    def fail_ping(reason: str) -> None:
        fire_fail_ping(settings.healthchecks_training_ping_url, reason)

    return BatchDeps(
        db=db,
        batch_shared_secret=settings.batch_shared_secret,
        model_cache=get_shared_cache(),
        fail_ping=fail_ping,
        clock=lambda: datetime.now(timezone.utc),
    )


class handler(BaseHTTPRequestHandler):
    """Vercel entrypoint for POST /api/batch_predict."""

    def do_POST(self) -> None:
        """Handle the poller's secret-gated batch-predict trigger.

        Delegates auth + orchestration entirely to
        `_lib.batch_logic.handle_batch_predict`. The top-level try/except
        here is the last line of defense: even a deployment
        misconfiguration (e.g. a missing environment variable) or a truly
        unexpected exception still yields a well-formed JSON response
        instead of a raw/broken connection.
        """
        deps: BatchDeps | None = None
        try:
            deps = _build_deps()
            response = handle_batch_predict(dict(self.headers.items()), deps)
        except Exception:
            logger.exception("batch_predict: unhandled error")
            response = unexpected_error_response(status=500)
        finally:
            if deps is not None:
                deps.db.close()
        write_http_response(self, response)

    def log_message(self, format: str, *args: object) -> None:
        """Route BaseHTTPRequestHandler's default stderr access log through
        the `logging` module instead of a raw stderr write (per the
        project's global standard: use `logging`, never `print`).

        The `format`/`*args` signature is fixed by the base class being
        overridden here.
        """
        logger.info("%s - %s", self.address_string(), format % args)
