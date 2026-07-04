"""HTTP-layer smoke tests for the Vercel entrypoint api/forecast.py.

The business logic (payload shape, cache headers, 503 fallback) is
already thoroughly covered by test_forecast.py against the pure
`_lib.read_logic` functions. These tests instead verify the thin
BaseHTTPRequestHandler glue itself, without a real socket or network call.
"""

from __future__ import annotations

import io
import json

import pytest

import forecast
from _lib.read_logic import ReadDeps
from tests.fakes import FakeSupabaseDB


def _make_handler_instance() -> forecast.handler:
    """Build a handler instance without a real socket/connection (see the
    equivalent helper in test_batch_predict_handler.py for why the extra
    attributes below are set manually).
    """
    instance = forecast.handler.__new__(forecast.handler)
    instance.client_address = ("127.0.0.1", 12345)
    instance.request_version = "HTTP/1.1"
    instance.protocol_version = "HTTP/1.1"
    instance.requestline = "GET /api/forecast HTTP/1.1"
    instance.command = "GET"
    instance.headers = {}  # type: ignore[assignment]  # plain dict stands in for HTTPMessage
    instance.rfile = io.BytesIO(b"")
    instance.wfile = io.BytesIO()
    return instance


def _split_response(raw: bytes) -> tuple[bytes, dict[str, object], bytes]:
    status_line, _, rest = raw.partition(b"\r\n")
    headers_blob, _, body_bytes = rest.partition(b"\r\n\r\n")
    return status_line, json.loads(body_bytes), headers_blob


class TestDoGet:
    """Tests for handler.do_GET()."""

    def test_happy_path_returns_200_with_cache_headers(
        self, monkeypatch: pytest.MonkeyPatch
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

        def fake_build_settings_and_db() -> ReadDeps:
            return ReadDeps(db=db)

        # Patch at the point of use: handler.do_GET calls load_settings()
        # then constructs a SupabaseREST directly, so the simplest seam is
        # to monkeypatch SupabaseREST's constructor call site indirectly
        # via load_settings + a stubbed SupabaseREST class.
        fake_settings = type(
            "S", (), {"supabase_url": "https://x", "supabase_service_role_key": "k"}
        )()
        monkeypatch.setattr(forecast, "load_settings", lambda: fake_settings)
        monkeypatch.setattr(forecast, "SupabaseREST", lambda *a, **k: db)

        instance = _make_handler_instance()
        instance.do_GET()

        raw = instance.wfile.getvalue()  # type: ignore[attr-defined]
        status_line, body, headers_blob = _split_response(raw)
        assert b"200" in status_line
        assert body["generated_at"] == "2026-07-05T00:00:00+00:00"
        assert b"Cache-Control: public, s-maxage=90, stale-while-revalidate=60" in headers_blob

    def test_settings_failure_yields_typed_503_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_misconfigured() -> None:
            raise RuntimeError("missing required environment variable(s): SUPABASE_URL")

        monkeypatch.setattr(forecast, "load_settings", raise_misconfigured)

        instance = _make_handler_instance()
        instance.do_GET()  # must not raise

        raw = instance.wfile.getvalue()  # type: ignore[attr-defined]
        status_line, body, _ = _split_response(raw)
        assert b"503" in status_line
        assert body == {
            "error": "predictions_unavailable",
            "message": "Predictions temporarily unavailable",
        }
