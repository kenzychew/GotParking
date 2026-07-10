"""Tests for onemap_client.py, all against injected fake transports (no real network)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onemap_client import (  # noqa: E402
    OneMapAuthError,
    OneMapUnavailableError,
    fetch_token,
    reverse_geocode,
    search_postal_code,
)


def test_fetch_token_parses_access_token_and_expiry() -> None:
    def fake_post(url: str, payload: dict, headers: dict) -> dict:
        assert payload == {"email": "a@b.com", "password": "secret"}
        return {"access_token": "tok-123", "expiry_timestamp": "1783000000"}

    token, expiry = fetch_token("a@b.com", "secret", post_json_fn=fake_post)

    assert token == "tok-123"
    assert expiry == datetime.fromtimestamp(1783000000, tz=timezone.utc)


def test_fetch_token_raises_auth_error_on_malformed_response() -> None:
    def fake_post(url: str, payload: dict, headers: dict) -> dict:
        return {"message": "Unauthorized"}

    with pytest.raises(OneMapAuthError):
        fetch_token("a@b.com", "wrong", post_json_fn=fake_post)


def test_reverse_geocode_parses_a_real_match() -> None:
    def fake_get(url: str, headers: dict) -> dict:
        assert headers == {"Authorization": "tok-123"}
        assert "location=1.29375,103.85718" in url
        return {
            "GeocodeInfo": [
                {
                    "BUILDINGNAME": "SUNTEC SINGAPORE CONVENTION & EXHIBITION CENTRE",
                    "BLOCK": "1",
                    "ROAD": "RAFFLES BOULEVARD",
                    "POSTALCODE": "039593",
                    "LATITUDE": "1.2935036238646977",
                    "LONGITUDE": "103.85719484066904",
                }
            ]
        }

    result = reverse_geocode(
        "tok-123", 1.29375, 103.85718, get_json_fn=fake_get
    )

    assert result is not None
    assert result.building_name == "SUNTEC SINGAPORE CONVENTION & EXHIBITION CENTRE"
    assert result.postal_code == "039593"
    assert result.address == "1 RAFFLES BOULEVARD"
    assert result.latitude == pytest.approx(1.2935036238646977)


def test_reverse_geocode_returns_none_for_unresolvable_coordinate() -> None:
    """The core "honest beats invented" guarantee: an empty GeocodeInfo (real OneMap
    behavior for a coordinate with no building within the buffer) must return None,
    never fabricate a result."""

    def fake_get(url: str, headers: dict) -> dict:
        return {"GeocodeInfo": []}

    result = reverse_geocode("tok-123", 0.0, 0.0, get_json_fn=fake_get)

    assert result is None


def test_reverse_geocode_returns_none_when_onemap_matches_a_road_with_no_building_name() -> None:
    """Regression, found live 2026-07-10: a NON-empty GeocodeInfo whose BUILDINGNAME is
    the literal string "NIL" (OneMap's own convention for "matched a road segment, no
    specific building here" -- NOT the same as an empty GeocodeInfo array) must ALSO
    return None, not the literal string "NIL" as if it were a real building name. This
    affected 99 of the first 267 carparks enriched before the fix -- mostly HDB/URA
    off-street coordinates with no named building nearby."""

    def fake_get(url: str, headers: dict) -> dict:
        return {
            "GeocodeInfo": [
                {
                    "BUILDINGNAME": "NIL",
                    "BLOCK": "NIL",
                    "ROAD": "ARAB STREET",
                    "POSTALCODE": "NIL",
                    "LATITUDE": "1.30294184422517",
                    "LONGITUDE": "103.857025273117",
                }
            ]
        }

    result = reverse_geocode("tok-123", 1.30294184422517, 103.857025273117, get_json_fn=fake_get)

    assert result is None


def test_reverse_geocode_cleans_nil_block_and_road_independently_of_building_name() -> None:
    """Regression, found live 2026-07-10: Sentosa's coordinate resolved a REAL building
    name ("SENTOSA") but BLOCK and ROAD were independently "NIL" -- a fix that only
    checked BUILDINGNAME left address constructed as the literal string "NIL" (block=""
    + road="NIL" -> " NIL".strip() == "NIL"). Every field must be cleaned
    independently, not just the one that gates the overall unresolvable decision."""

    def fake_get(url: str, headers: dict) -> dict:
        return {
            "GeocodeInfo": [
                {
                    "BUILDINGNAME": "SENTOSA",
                    "BLOCK": "NIL",
                    "ROAD": "NIL",
                    "POSTALCODE": "NIL",
                    "LATITUDE": "1.25017",
                    "LONGITUDE": "103.83126",
                }
            ]
        }

    result = reverse_geocode("tok-123", 1.25017, 103.83126, get_json_fn=fake_get)

    assert result is not None
    assert result.building_name == "SENTOSA"
    assert result.address == ""
    assert result.postal_code == ""


def test_reverse_geocode_raises_unavailable_on_transport_failure() -> None:
    import urllib.error

    def fake_get(url: str, headers: dict) -> dict:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(OneMapUnavailableError):
        reverse_geocode("tok-123", 1.0, 103.0, get_json_fn=fake_get)


def test_search_postal_code_parses_a_real_match() -> None:
    def fake_get(url: str, headers: dict) -> dict:
        assert "searchVal=039593" in url
        return {
            "results": [
                {
                    "BUILDING": "SUNTEC CITY MALL",
                    "ADDRESS": "1 RAFFLES BOULEVARD SUNTEC CITY MALL SINGAPORE 039593",
                    "POSTAL": "039593",
                    "LATITUDE": "1.29350132535558",
                    "LONGITUDE": "103.857307495824",
                }
            ]
        }

    result = search_postal_code("tok-123", "039593", get_json_fn=fake_get)

    assert result is not None
    assert result.building_name == "SUNTEC CITY MALL"
    assert result.postal_code == "039593"


def test_search_postal_code_returns_none_when_nothing_found() -> None:
    def fake_get(url: str, headers: dict) -> dict:
        return {"results": []}

    result = search_postal_code("tok-123", "999999", get_json_fn=fake_get)

    assert result is None


def test_search_postal_code_cleans_nil_fields_but_keeps_the_coordinate() -> None:
    """Unlike reverse_geocode, a "NIL" building_name does NOT make this return None --
    the coordinate is this function's real payload (postal-code distance search) and
    stays valid even when OneMap's metadata doesn't resolve to a specific building."""

    def fake_get(url: str, headers: dict) -> dict:
        return {
            "results": [
                {
                    "BUILDING": "NIL",
                    "ADDRESS": "NIL",
                    "POSTAL": "560101",
                    "LATITUDE": "1.37026158388486",
                    "LONGITUDE": "103.839467898898",
                }
            ]
        }

    result = search_postal_code("tok-123", "560101", get_json_fn=fake_get)

    assert result is not None
    assert result.building_name == ""
    assert result.address == ""
    assert result.postal_code == "560101"
    assert result.latitude == pytest.approx(1.37026158388486)
