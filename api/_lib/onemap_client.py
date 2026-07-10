"""Minimal OneMap Singapore client for geocode_postal.py: search API only.

api/'s own client, not a shared import from scripts/onemap_client.py -- api/ and scripts/
are separate deployments with separate dependency sets (api/ ships to Vercel via
api/requirements.txt; scripts/ is a local-only uv project), so this project's established
convention (poller/training both keep independent copies of shared logic, cross-checked by
tests -- see training/README.md's "CRITICAL INTEGRATION CONTRACT" notes) is duplication with
a documented reason, not accidental drift.

Only search_postal_code is needed here (geocode_postal.py resolves a postal code to
lat/lon for the frontend's distance search) -- reverse-geocode lives in scripts/ only,
since that's a batch/on-insert-hook concern, not a per-request one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"

# Refresh this long before the token's real ~72h expiry (verified live 2026-07-10) --
# purely a safety margin against clock skew and this being called right at the boundary.
TOKEN_REFRESH_MARGIN = timedelta(hours=1)


class OneMapError(Exception):
    """Base class for OneMap client errors."""


class OneMapAuthError(OneMapError):
    """Raised when the email/password token exchange fails."""


class OneMapUnavailableError(OneMapError):
    """Raised when a search request fails after the client's retry."""


@dataclass(frozen=True)
class PostalSearchResult:
    """One resolved postal-code match.

    Attributes:
        building_name: OneMap's BUILDING field.
        latitude: WGS84 latitude.
        longitude: WGS84 longitude.
    """

    building_name: str
    latitude: float
    longitude: float


def fetch_token(email: str, password: str, client: httpx.Client) -> tuple[str, datetime]:
    """Exchange email/password for a bearer token.

    Args:
        email: OneMap account email.
        password: OneMap account password.
        client: An httpx.Client (injected so tests can supply a MockTransport).

    Returns:
        A (token, expiry) pair. `expiry` is UTC-aware.

    Raises:
        OneMapAuthError: If the credentials are rejected or the response is malformed.
    """
    try:
        response = client.post(AUTH_URL, json={"email": email, "password": password})
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise OneMapAuthError(f"token request failed: {exc!r}") from exc

    token = payload.get("access_token")
    expiry_raw = payload.get("expiry_timestamp")
    if not token or not expiry_raw:
        raise OneMapAuthError(f"unexpected token response shape: {list(payload.keys())!r}")
    return token, datetime.fromtimestamp(int(expiry_raw), tz=timezone.utc)


def search_postal_code(token: str, postal_code: str, client: httpx.Client) -> PostalSearchResult | None:
    """Resolve a postal code to a building/coordinate via OneMap's search API.

    Args:
        token: A valid bearer token.
        postal_code: The postal code query.
        client: An httpx.Client (injected so tests can supply a MockTransport).

    Returns:
        The first (best-ranked) match, or None if nothing was found -- a real, expected
        outcome for a malformed/nonexistent postal code, not an error. `building_name`
        is cleaned to an empty string if OneMap returns the literal "NIL" (its own
        convention for "no value", found live 2026-07-10 in scripts/onemap_client.py's
        reverse_geocode -- present here for consistency even though this endpoint
        doesn't currently expose building_name to the frontend). The coordinate stays
        valid regardless -- it's this function's real payload, used for distance search.

    Raises:
        OneMapUnavailableError: If the request fails.
    """
    try:
        response = client.get(
            SEARCH_URL,
            params={"searchVal": postal_code, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": "1"},
            headers={"Authorization": token},
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise OneMapUnavailableError(f"search_postal_code({postal_code!r}) failed: {exc!r}") from exc

    results = payload.get("results") or []
    if not results:
        return None
    best = results[0]
    building_name = str(best.get("BUILDING", "")).strip()
    if building_name.upper() == "NIL":
        building_name = ""
    return PostalSearchResult(
        building_name=building_name,
        latitude=float(best["LATITUDE"]),
        longitude=float(best["LONGITUDE"]),
    )


class TokenCache:
    """Warm-instance OneMap token cache, mirroring model_cache.ModelCache's pattern.

    A Vercel Python function's warm instance keeps this module's state alive across
    invocations, so a new token is fetched at most once per ~72h validity window rather
    than once per request (which would double every geocode_postal.py request's latency
    with an extra auth round-trip).
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._expiry: datetime | None = None

    def get(self, email: str, password: str, client: httpx.Client, now: datetime) -> str:
        """Return a valid cached token, fetching a fresh one if missing/near-expiry.

        Args:
            email: OneMap account email.
            password: OneMap account password.
            client: An httpx.Client (injected for testability).
            now: The current instant, used to check staleness.

        Returns:
            A bearer token valid for at least TOKEN_REFRESH_MARGIN longer.

        Raises:
            OneMapAuthError: If a fresh fetch is needed and fails.
        """
        if self._token is None or self._expiry is None or now >= self._expiry - TOKEN_REFRESH_MARGIN:
            logger.info("onemap token cache: fetching a fresh token")
            self._token, self._expiry = fetch_token(email, password, client)
        return self._token


_SHARED_TOKEN_CACHE = TokenCache()


def get_shared_token_cache() -> TokenCache:
    """Return the process-wide TokenCache singleton used in production."""
    return _SHARED_TOKEN_CACHE
