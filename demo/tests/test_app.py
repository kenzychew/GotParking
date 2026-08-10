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
