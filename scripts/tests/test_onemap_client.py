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
