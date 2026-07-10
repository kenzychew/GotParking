"""Vercel Python serverless function: GET /api/geocode_postal?postal=<code>.

Public, no secret gating (like /api/forecast) -- resolves a postal code to a coordinate
via OneMap, server-side, so the frontend's postal-code search never sees OneMap
credentials. All business logic lives in `_lib/geocode_logic.py`; this file is thin glue,
plus this endpoint's own query-string parsing (the only endpoint in api/ that needs one,
so it isn't in the shared `_lib/http_helpers.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import httpx

from _lib.config import load_settings
from _lib.geocode_logic import GeocodeDeps, handle_geocode_postal
from _lib.http_helpers import unexpected_error_response, write_http_response
from _lib.onemap_client import get_shared_token_cache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    """Vercel entrypoint for GET /api/geocode_postal."""

    def do_GET(self) -> None:
        """Parse the `postal` query param and delegate to handle_geocode_postal.

        Mirrors forecast.py's top-level try/except shape: any unanticipated failure
        (including a missing environment variable from `load_settings`) still produces a
        well-formed typed response, never a raw 500.
        """
        try:
            settings = load_settings()
            query = parse_qs(urlparse(self.path).query)
            postal_code = query.get("postal", [None])[0]

            with httpx.Client(timeout=10.0) as client:
                deps = GeocodeDeps(
                    onemap_email=settings.onemap_email,
                    onemap_password=settings.onemap_password,
                    http_client=client,
                    token_cache=get_shared_token_cache(),
                    clock=lambda: datetime.now(timezone.utc),
                )
                response = handle_geocode_postal(deps, postal_code)
        except Exception:
            logger.exception("geocode_postal: unhandled error")
            response = unexpected_error_response(503)
        write_http_response(self, response)

    def log_message(self, format: str, *args: object) -> None:
        """Route BaseHTTPRequestHandler's default stderr access log through `logging`
        instead of a raw stderr write (per the project's global standard).

        The `format`/`*args` signature is fixed by the base class being overridden here.
        """
        logger.info("%s - %s", self.address_string(), format % args)
