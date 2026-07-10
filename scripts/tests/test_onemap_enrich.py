"""Tests for onemap_enrich.py's orchestration logic (enrich_carparks), all against
injected fakes -- no real network, no real database writes."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onemap_client import GeocodeResult, OneMapUnavailableError  # noqa: E402
from onemap_enrich import enrich_carparks, fetch_live_carpark_coordinates  # noqa: E402

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def test_fetch_live_carpark_coordinates_parses_space_separated_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {"CarParkID": "1", "Location": "1.29375 103.85718"},
        {"CarParkID": "2", "Location": "malformed"},
        {"CarParkID": "3", "Location": ""},
    ]

    def fake_fetch(api_key: str):
        return records

    import onemap_enrich

    monkeypatch.setattr(onemap_enrich, "fetch_carpark_availability", fake_fetch)

    coords = fetch_live_carpark_coordinates("fake-key")

    assert coords == {"1": (1.29375, 103.85718)}


def test_enrich_resolved_carpark_writes_all_onemap_fields() -> None:
    patched: list[tuple[str, dict]] = []

    def fake_reverse_geocode(token: str, lat: float, lon: float):
        return GeocodeResult(
            building_name="SUNTEC CITY MALL",
            address="1 RAFFLES BOULEVARD",
            postal_code="039593",
            latitude=lat,
            longitude=lon,
        )

    def fake_patch(supabase_url: str, supabase_key: str, carpark_id: str, fields: dict) -> None:
        patched.append((carpark_id, fields))

    resolved, unresolvable, missing = enrich_carparks(
        [{"carpark_id": "1", "name": "Suntec City"}],
        {"1": (1.29375, 103.85718)},
        "tok", "https://x.supabase.co", "key", NOW,
        reverse_geocode_fn=fake_reverse_geocode,
        patch_fn=fake_patch,
        pacing_seconds=0,
    )

    assert resolved == 1
    assert unresolvable == 0
    assert missing == []
    assert len(patched) == 1
    carpark_id, fields = patched[0]
    assert carpark_id == "1"
    assert fields["onemap_building_name"] == "SUNTEC CITY MALL"
    assert fields["onemap_postal_code"] == "039593"
    assert fields["latitude"] == 1.29375
    assert fields["onemap_enriched_at"] == NOW.isoformat()


def test_enrich_unresolvable_carpark_saves_coordinates_but_not_a_fabricated_name() -> None:
    """The core "honest beats invented" guarantee, exercised at the orchestration level."""
    patched: list[tuple[str, dict]] = []

    def fake_reverse_geocode(token: str, lat: float, lon: float):
        return None

    def fake_patch(supabase_url: str, supabase_key: str, carpark_id: str, fields: dict) -> None:
        patched.append((carpark_id, fields))

    resolved, unresolvable, missing = enrich_carparks(
        [{"carpark_id": "99", "name": "BLK 101 SOMEWHERE"}],
        {"99": (1.4, 103.8)},
        "tok", "https://x.supabase.co", "key", NOW,
        reverse_geocode_fn=fake_reverse_geocode,
        patch_fn=fake_patch,
        pacing_seconds=0,
    )

    assert resolved == 0
    assert unresolvable == 1
    assert len(patched) == 1
    _, fields = patched[0]
    assert "onemap_building_name" not in fields
    assert "onemap_address" not in fields
    assert "onemap_postal_code" not in fields
    assert fields["latitude"] == 1.4
    assert fields["onemap_enriched_at"] == NOW.isoformat()


def test_enrich_carpark_missing_from_live_feed_is_skipped_not_written() -> None:
    patched: list[tuple[str, dict]] = []

    resolved, unresolvable, missing = enrich_carparks(
        [{"carpark_id": "404", "name": "Ghost Carpark"}],
        {},  # no coordinates at all
        "tok", "https://x.supabase.co", "key", NOW,
        reverse_geocode_fn=lambda *a, **kw: None,
        patch_fn=lambda *a, **kw: patched.append((a[2], a[3])),
        pacing_seconds=0,
    )

    assert resolved == 0
    assert unresolvable == 0
    assert missing == ["404"]
    assert patched == []


def test_enrich_continues_past_a_transport_failure_for_one_carpark() -> None:
    patched: list[str] = []

    def flaky_reverse_geocode(token: str, lat: float, lon: float):
        if lat == 1.0:
            raise OneMapUnavailableError("simulated failure")
        return GeocodeResult("OK BUILDING", "addr", "123456", lat, lon)

    def fake_patch(supabase_url: str, supabase_key: str, carpark_id: str, fields: dict) -> None:
        patched.append(carpark_id)

    resolved, unresolvable, missing = enrich_carparks(
        [
            {"carpark_id": "1", "name": "Fails"},
            {"carpark_id": "2", "name": "Succeeds"},
        ],
        {"1": (1.0, 103.0), "2": (1.5, 103.5)},
        "tok", "https://x.supabase.co", "key", NOW,
        reverse_geocode_fn=flaky_reverse_geocode,
        patch_fn=fake_patch,
        pacing_seconds=0,
    )

    assert resolved == 1
    assert patched == ["2"]  # carpark "1" failed and was skipped, not written
