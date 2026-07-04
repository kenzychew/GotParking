"""Vercel Python serverless function: GET /api/forecast.

Public, cached, parameter-less forecast read (design doc T4, D10) -- the
only public-facing API surface. All business logic lives in
`_lib/read_logic.py`; this file is thin glue between Vercel's Python
runtime (a `handler(BaseHTTPRequestHandler)` class per file in `api/`) and
that pure, independently-tested logic. Files under `api/_lib/` are not
routed (leading underscore), per Vercel's Python function convention.
"""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler

from _lib.config import load_settings
from _lib.http_helpers import write_http_response
from _lib.read_logic import ReadDeps, handle_forecast_read, unavailable_response
from _lib.supabase_rest import SupabaseREST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    """Vercel entrypoint for GET /api/forecast."""

    def do_GET(self) -> None:
        """Serve the cached, whole-payload public forecast read.

        Delegates the actual data-fetch/response-shaping entirely to
        `_lib.read_logic.handle_forecast_read`, which already guarantees a
        typed 503 (never a raw 500) for any failure while talking to
        Supabase. The top-level try/except here additionally covers a
        deployment misconfiguration (e.g. a missing environment variable
        raised by `load_settings`, which happens before a ReadDeps even
        exists to hand to `handle_forecast_read`) with the exact same
        typed-503 contract, so "never a raw 500" holds unconditionally for
        this public endpoint.
        """
        db: SupabaseREST | None = None
        try:
            settings = load_settings()
            db = SupabaseREST(settings.supabase_url, settings.supabase_service_role_key)
            response = handle_forecast_read(ReadDeps(db=db))
        except Exception:
            logger.exception("forecast: unhandled error")
            response = unavailable_response()
        finally:
            if db is not None:
                db.close()
        write_http_response(self, response)

    def log_message(self, format: str, *args: object) -> None:
        """Route BaseHTTPRequestHandler's default stderr access log through
        the `logging` module instead of a raw stderr write (per the
        project's global standard: use `logging`, never `print`).

        The `format`/`*args` signature is fixed by the base class being
        overridden here.
        """
        logger.info("%s - %s", self.address_string(), format % args)
