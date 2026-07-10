"""Minimal client for OneMap Singapore's auth, reverse-geocode, and search APIs.

OneMap's Search/Reverse-Geocode APIs require a registered account (email + password
exchanged for a short-lived bearer token, currently ~72h per a live check on 2026-07-10) --
not an open/keyless API. Credentials come from ONEMAP_EMAIL / ONEMAP_PASSWORD env vars,
never hardcoded or logged.

Rate limit: 250 requests/minute (https://discuss.onemap.sg/t/maximum-number-of-request/100,
verified live 2026-07-10). Callers doing bulk work (scripts/onemap_enrich.py) should pace
themselves well under that -- this module does not rate-limit internally, since the right
pacing differs between a one-time 268-carpark batch and a single on-insert-hook call.

Usage:
    token, _ = fetch_token(email, password)
    result = reverse_geocode(token, lat=1.29375, lon=103.85718)
    if result is not None:
        print(result.building_name, result.postal_code)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
REVGEOCODE_URL = "https://www.onemap.gov.sg/api/public/revgeocode"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"

# Buffer radius (meters) for reverse-geocode's building match -- a carpark's LTA-reported
# coordinate is rarely the exact building centroid, so a small buffer is needed to resolve to
# the nearest real building rather than returning zero results for an exact-point miss.
REVERSE_GEOCODE_BUFFER_METERS = 40


class OneMapError(Exception):
    """Base class for OneMap client errors."""


class OneMapAuthError(OneMapError):
    """Raised when the email/password token exchange fails."""


class OneMapUnavailableError(OneMapError):
    """Raised when a request fails after exhausting retries."""


@dataclass(frozen=True)
class GeocodeResult:
    """One resolved building match.

    Attributes:
        building_name: OneMap's BUILDINGNAME/BUILDING field, uppercase (OneMap's own
            casing convention -- callers title-case for display if desired).
        address: Full postal address string.
        postal_code: 6-digit Singapore postal code.
        latitude: Resolved latitude (may differ slightly from the query point).
        longitude: Resolved longitude.
    """

    building_name: str
    address: str
    postal_code: str
    latitude: float
    longitude: float


def _http_post_json(url: str, payload: dict[str, str], headers: dict[str, str]) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_token(
    email: str,
    password: str,
    *,
    post_json_fn: Callable[[str, dict[str, str], dict[str, str]], dict] = _http_post_json,
) -> tuple[str, datetime]:
    """Exchange email/password for a bearer token.

    Args:
        email: OneMap account email.
        password: OneMap account password.
        post_json_fn: Injectable POST-JSON transport (tests supply a fake instead of
            touching the real network).

    Returns:
        A (token, expiry) pair. `expiry` is a UTC-aware datetime.

    Raises:
        OneMapAuthError: If the credentials are rejected or the response is malformed.
    """
    try:
        payload = post_json_fn(AUTH_URL, {"email": email, "password": password}, {})
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise OneMapAuthError(f"token request failed: {exc!r}") from exc

    token = payload.get("access_token")
    expiry_raw = payload.get("expiry_timestamp")
    if not token or not expiry_raw:
        raise OneMapAuthError(f"unexpected token response shape: {list(payload.keys())!r}")
    expiry = datetime.fromtimestamp(int(expiry_raw), tz=timezone.utc)
    return token, expiry


def reverse_geocode(
    token: str,
    lat: float,
    lon: float,
    *,
    get_json_fn: Callable[[str, dict[str, str]], dict] = _http_get_json,
) -> GeocodeResult | None:
    """Resolve a coordinate to its nearest building via OneMap's reverse-geocode API.

    Args:
        token: A valid bearer token from `fetch_token`.
        lat: Latitude (WGS84).
        lon: Longitude (WGS84).
        get_json_fn: Injectable GET-JSON transport (tests supply a fake instead of
            touching the real network).

    Returns:
        The nearest match, or None if OneMap has no building within
        REVERSE_GEOCODE_BUFFER_METERS of the given point (a real, expected outcome for
        some carparks -- callers must not fabricate a name in this case).

    Raises:
        OneMapUnavailableError: If the request fails (network error or non-2xx status)
            for reasons other than "no results".
    """
    url = (
        f"{REVGEOCODE_URL}?location={lat},{lon}"
        f"&buffer={REVERSE_GEOCODE_BUFFER_METERS}&addressType=All&otherFeatures=N"
    )
    try:
        payload = get_json_fn(url, {"Authorization": token})
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise OneMapUnavailableError(f"reverse_geocode({lat},{lon}) failed: {exc!r}") from exc

    matches = payload.get("GeocodeInfo") or []
    if not matches:
        return None
    best = matches[0]
    return GeocodeResult(
        building_name=str(best.get("BUILDINGNAME", "")).strip(),
        address=f"{best.get('BLOCK', '')} {best.get('ROAD', '')}".strip(),
        postal_code=str(best.get("POSTALCODE", "")).strip(),
        latitude=float(best["LATITUDE"]),
        longitude=float(best["LONGITUDE"]),
    )


def search_postal_code(
    token: str,
    postal_code: str,
    *,
    get_json_fn: Callable[[str, dict[str, str]], dict] = _http_get_json,
) -> GeocodeResult | None:
    """Resolve a postal code to its building/coordinate via OneMap's search API.

    Args:
        token: A valid bearer token from `fetch_token`.
        postal_code: A Singapore postal code query (need not be exactly 6 digits --
            OneMap's search tolerates partial/free-text queries too).
        get_json_fn: Injectable GET-JSON transport (tests supply a fake instead of
            touching the real network).

    Returns:
        The first (best-ranked) match, or None if nothing was found.

    Raises:
        OneMapUnavailableError: If the request fails.
    """
    url = f"{SEARCH_URL}?searchVal={postal_code}&returnGeom=Y&getAddrDetails=Y&pageNum=1"
    try:
        payload = get_json_fn(url, {"Authorization": token})
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise OneMapUnavailableError(f"search_postal_code({postal_code!r}) failed: {exc!r}") from exc

    results = payload.get("results") or []
    if not results:
        return None
    best = results[0]
    return GeocodeResult(
        building_name=str(best.get("BUILDING", "")).strip(),
        address=str(best.get("ADDRESS", "")).strip(),
        postal_code=str(best.get("POSTAL", "")).strip(),
        latitude=float(best["LATITUDE"]),
        longitude=float(best["LONGITUDE"]),
    )
