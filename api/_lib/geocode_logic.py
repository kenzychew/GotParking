"""Business logic for the postal-code-to-coordinate geocode endpoint.

Resolves a Singapore postal code to (latitude, longitude, building_name) via OneMap's
search API, server-side -- the frontend never sees OneMap credentials, only calls this
endpoint. Backs the frontend's postal-code proximity search: once a coordinate comes back
here, the actual nearest-carpark distance sort happens client-side (haversine against the
already-embedded per-carpark lat/lon in seedCarparks.ts), not another server round-trip.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from _lib.http_helpers import HttpResponse
from _lib.onemap_client import (
    OneMapAuthError,
    OneMapUnavailableError,
    TokenCache,
    search_postal_code,
)

logger = logging.getLogger(__name__)

# Public, cacheable at the edge -- a given postal code's coordinate never changes minute to
# minute, so a longer cache window than /api/forecast's is appropriate (mirrors the
# CACHE_CONTROL convention in read_logic.py).
_CACHE_CONTROL = "public, s-maxage=86400, stale-while-revalidate=3600"


@dataclass
class GeocodeDeps:
    """Injectable dependencies for handle_geocode_postal().

    Attributes:
        onemap_email: OneMap account email, or None if not configured.
        onemap_password: OneMap account password, or None if not configured.
        http_client: An httpx.Client (tests inject a MockTransport-backed one).
        token_cache: Warm-instance token cache (production uses the shared singleton).
        clock: Zero-arg callable returning the current instant (tests inject a fixed time).
    """

    onemap_email: str | None
    onemap_password: str | None
    http_client: httpx.Client
    token_cache: TokenCache
    clock: Callable[[], datetime]


def _not_configured_response() -> HttpResponse:
    return HttpResponse(
        503,
        {"error": "geocoding_unavailable", "message": "Postal code search is not configured"},
        {"Content-Type": "application/json"},
    )


def _not_found_response() -> HttpResponse:
    return HttpResponse(
        404,
        {"error": "postal_code_not_found", "message": "No location found for that postal code"},
        {"Content-Type": "application/json"},
    )


def _bad_request_response(message: str) -> HttpResponse:
    return HttpResponse(400, {"error": "bad_request", "message": message}, {"Content-Type": "application/json"})


def handle_geocode_postal(deps: GeocodeDeps, postal_code: str | None) -> HttpResponse:
    """Resolve a postal code query param to a coordinate.

    Args:
        deps: Injected dependencies.
        postal_code: The `postal` query-string value, or None if absent.

    Returns:
        200 with `{"building_name", "latitude", "longitude"}` on success; 400 if the
        `postal` param is missing/blank; 404 if OneMap found nothing; 503 if OneMap
        credentials aren't configured or the request fails. Never a raw 500 -- matches
        this project's "typed error, never a raw 500" convention (read_logic.py,
        batch_logic.py).
    """
    if not postal_code or not postal_code.strip():
        return _bad_request_response("missing required query parameter: postal")

    if not deps.onemap_email or not deps.onemap_password:
        logger.warning("geocode_postal: ONEMAP_EMAIL/ONEMAP_PASSWORD not configured")
        return _not_configured_response()

    now = deps.clock()
    try:
        token = deps.token_cache.get(deps.onemap_email, deps.onemap_password, deps.http_client, now)
        result = search_postal_code(token, postal_code.strip(), deps.http_client)
    except (OneMapAuthError, OneMapUnavailableError) as exc:
        logger.error("geocode_postal: OneMap request failed: %s", exc)
        return _not_configured_response()

    if result is None:
        return _not_found_response()

    body = {
        "building_name": result.building_name,
        "latitude": result.latitude,
        "longitude": result.longitude,
    }
    headers = {"Content-Type": "application/json", "Cache-Control": _CACHE_CONTROL}
    return HttpResponse(200, body, headers)
