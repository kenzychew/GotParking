"""Tests for demo/app.py's FastAPI route.

Mirrors api/tests/test_forecast_handler.py's approach: monkeypatch the
`SUPABASE_URL`/`SUPABASE_DEMO_READER_KEY` env vars and `SupabaseREST` at the
point of use so no real network call or credential is ever needed here,
while still proving the FastAPI route reuses the exact same
`_lib.read_logic.handle_forecast_read` business logic as `api/forecast.py`
-- same pinned payload shape, same typed-503 contract.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_DEMO_DIR = Path(__file__).resolve().parent.parent
_API_DIR = _DEMO_DIR.parent / "api"
for _p in (_API_DIR, _DEMO_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi.testclient import TestClient
from tests.fakes import FakeSupabaseDB

import app as demo_app
from _lib.sg_time import sgt_parts


@pytest.fixture
def client() -> TestClient:
    return TestClient(demo_app.app)


def test_forecast_happy_path_returns_pinned_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FakeSupabaseDB(
        tables={
            "carpark_forecast": [
                {
                    "carpark_id": "1",
                    "state": "ml",
                    "forecast_lots": 50,
                    "tier": "plenty",
                    "live_lots": 60,
                    "model_version": "v1",
                    "generated_at": "2026-07-05T00:00:00+00:00",
                }
            ],
            "carparks": [{"carpark_id": "1", "name": "Suntec City"}],
        }
    )
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_DEMO_READER_KEY", "k")
    monkeypatch.setattr(demo_app, "SupabaseREST", lambda *a, **k: db)

    response = client.get("/api/forecast")

    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"] == "2026-07-05T00:00:00+00:00"
    assert body["carparks"] == [
        {
            "carpark_id": "1",
            "name": "Suntec City",
            "state": "ml",
            "forecast_lots": 50,
            "tier": "plenty",
            "live_lots": 60,
            "model_version": "v1",
        }
    ]
    assert response.headers["cache-control"] == "public, s-maxage=90, stale-while-revalidate=60"


def test_forecast_missing_env_yields_typed_503_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DEMO_READER_KEY", raising=False)

    response = client.get("/api/forecast")

    assert response.status_code == 503
    assert response.json() == {
        "error": "predictions_unavailable",
        "message": "Predictions temporarily unavailable",
    }


def test_static_frontend_is_served_at_root(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "GotParking" in response.text


def test_carparks_geo_happy_path_returns_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FakeSupabaseDB(
        tables={
            "carparks": [
                {
                    "carpark_id": "1",
                    "name": "Suntec City",
                    "latitude": 1.2936,
                    "longitude": 103.8575,
                },
                {
                    "carpark_id": "2",
                    "name": "No Coordinates Carpark",
                    "latitude": None,
                    "longitude": None,
                },
            ]
        }
    )
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_DEMO_READER_KEY", "k")
    monkeypatch.setattr(demo_app, "SupabaseREST", lambda *a, **k: db)

    response = client.get("/api/carparks-geo")

    assert response.status_code == 200
    body = response.json()
    assert body["carparks"] == [
        {"carpark_id": "1", "name": "Suntec City", "latitude": 1.2936, "longitude": 103.8575},
        {"carpark_id": "2", "name": "No Coordinates Carpark", "latitude": None, "longitude": None},
    ]


def test_carparks_geo_missing_env_yields_typed_503_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DEMO_READER_KEY", raising=False)

    response = client.get("/api/carparks-geo")

    assert response.status_code == 503
    assert response.json() == {
        "error": "geo_unavailable",
        "message": "Carpark locations temporarily unavailable",
    }


def test_carparks_geo_supabase_failure_yields_typed_503_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FakeSupabaseDB(fail_tables={"carparks"})
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_DEMO_READER_KEY", "k")
    monkeypatch.setattr(demo_app, "SupabaseREST", lambda *a, **k: db)

    response = client.get("/api/carparks-geo")

    assert response.status_code == 503
    assert response.json() == {
        "error": "geo_unavailable",
        "message": "Carpark locations temporarily unavailable",
    }


def test_carpark_baseline_happy_path_returns_todays_slots_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    today_dow, _ = sgt_parts(datetime.now(timezone.utc))
    other_dow = (today_dow + 1) % 7
    db = FakeSupabaseDB(
        tables={
            "carpark_baseline": [
                {"carpark_id": "1", "dow": today_dow, "slot_of_day": 40, "avg_available_lots": 120.5},
                {"carpark_id": "1", "dow": today_dow, "slot_of_day": 10, "avg_available_lots": 200.0},
                {"carpark_id": "1", "dow": other_dow, "slot_of_day": 10, "avg_available_lots": 999.0},
                {"carpark_id": "2", "dow": today_dow, "slot_of_day": 10, "avg_available_lots": 5.0},
            ]
        }
    )
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_DEMO_READER_KEY", "k")
    monkeypatch.setattr(demo_app, "SupabaseREST", lambda *a, **k: db)

    response = client.get("/api/carpark-baseline/1")

    assert response.status_code == 200
    body = response.json()
    assert body["carpark_id"] == "1"
    assert body["dow"] == today_dow
    assert isinstance(body["current_slot_of_day"], int)
    # Only carpark 1's today-dow rows, ordered by slot_of_day ascending -- the
    # other-dow row and carpark 2's row must not leak in. (FakeSupabaseDB
    # doesn't project the `select` param, unlike real PostgREST, so rows
    # keep every fixture field rather than just slot_of_day/avg_available_lots.)
    assert [(r["slot_of_day"], r["avg_available_lots"]) for r in body["slots"]] == [
        (10, 200.0),
        (40, 120.5),
    ]


def test_carpark_baseline_unknown_carpark_returns_empty_slots_not_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FakeSupabaseDB(tables={"carpark_baseline": []})
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_DEMO_READER_KEY", "k")
    monkeypatch.setattr(demo_app, "SupabaseREST", lambda *a, **k: db)

    response = client.get("/api/carpark-baseline/does-not-exist")

    assert response.status_code == 200
    assert response.json()["slots"] == []


def test_carpark_baseline_missing_env_yields_typed_503_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DEMO_READER_KEY", raising=False)

    response = client.get("/api/carpark-baseline/1")

    assert response.status_code == 503
    assert response.json() == {
        "error": "baseline_unavailable",
        "message": "Typical availability data temporarily unavailable",
    }


def test_carpark_baseline_supabase_failure_yields_typed_503_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates the pre-migration state: the `demo_reader` role has no grant
    on `carpark_baseline` yet (db/schema.sql section 11b not applied), which
    PostgREST would reject as a permission error -- surfaced here the same
    way any other Supabase failure is.
    """
    db = FakeSupabaseDB(fail_tables={"carpark_baseline"})
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_DEMO_READER_KEY", "k")
    monkeypatch.setattr(demo_app, "SupabaseREST", lambda *a, **k: db)

    response = client.get("/api/carpark-baseline/1")

    assert response.status_code == 503
    assert response.json() == {
        "error": "baseline_unavailable",
        "message": "Typical availability data temporarily unavailable",
    }
