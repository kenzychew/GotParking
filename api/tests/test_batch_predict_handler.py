"""HTTP-layer smoke tests for the Vercel entrypoint api/batch_predict.py.

The business logic (auth, state decisions, error handling) is already
thoroughly covered by test_batch_predict.py against the pure
`_lib.batch_logic` functions. These tests instead verify the thin
BaseHTTPRequestHandler glue itself: that `do_POST` builds dependencies,
delegates to that logic, and serializes the result onto a real handler
instance -- without a real socket or real network calls.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest

import batch_predict
from _lib.batch_logic import BatchDeps
from _lib.model_cache import ModelCache
from tests.fakes import FakeSupabaseDB, RecordingFailPing, make_clock


def _make_handler_instance(headers: dict[str, str]) -> batch_predict.handler:
    """Build a handler instance without a real socket/connection.

    BaseHTTPRequestHandler normally initializes `request_version`,
    `requestline`, etc. inside `handle_one_request()` while parsing a real
    request off the wire. `__new__` skips `__init__` (which would try to
    read from a socket), so those attributes are set manually here to the
    minimum BaseHTTPRequestHandler needs for `send_response`/`log_message`.
    """
    instance = batch_predict.handler.__new__(batch_predict.handler)
    instance.client_address = ("127.0.0.1", 12345)
    instance.request_version = "HTTP/1.1"
    instance.protocol_version = "HTTP/1.1"
    instance.requestline = "POST /api/batch_predict HTTP/1.1"
    instance.command = "POST"
    instance.headers = headers  # type: ignore[assignment]  # plain dict stands in for HTTPMessage
    instance.rfile = io.BytesIO(b"")
    instance.wfile = io.BytesIO()
    return instance


def _split_response(raw: bytes) -> tuple[bytes, dict[str, object]]:
    status_line, _, rest = raw.partition(b"\r\n")
    _, _, body_bytes = rest.partition(b"\r\n\r\n")
    return status_line, json.loads(body_bytes)


class TestDoPost:
    """Tests for handler.do_POST()."""

    def test_authorized_request_returns_200_with_computed_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = FakeSupabaseDB(
            tables={
                "carparks": [],
                "model_config": [{"active_model_version": None}],
                "carpark_momentum": [],
                "carpark_baseline": [],
            }
        )
        fake_deps = BatchDeps(
            db=db,
            batch_shared_secret="test-secret",
            model_cache=ModelCache(),
            fail_ping=RecordingFailPing(),
            clock=make_clock(datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)),
        )
        monkeypatch.setattr(batch_predict, "_build_deps", lambda: fake_deps)

        instance = _make_handler_instance({"x-batch-secret": "test-secret"})
        instance.do_POST()

        status_line, body = _split_response(instance.wfile.getvalue())  # type: ignore[attr-defined]
        assert b"200" in status_line
        assert body == {"computed": 0, "generated_at": "2026-07-05T00:00:00+00:00"}

    def test_bad_secret_returns_401_with_no_db_queries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = FakeSupabaseDB(tables={"carparks": []})
        fake_deps = BatchDeps(
            db=db,
            batch_shared_secret="correct-secret",
            model_cache=ModelCache(),
            fail_ping=RecordingFailPing(),
            clock=make_clock(datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)),
        )
        monkeypatch.setattr(batch_predict, "_build_deps", lambda: fake_deps)

        instance = _make_handler_instance({"x-batch-secret": "wrong-secret"})
        instance.do_POST()

        status_line, body = _split_response(instance.wfile.getvalue())  # type: ignore[attr-defined]
        assert b"401" in status_line
        assert body == {"error": "unauthorized"}
        assert db.select_calls == []  # no compute happened

    def test_deps_build_failure_yields_typed_500_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_misconfigured() -> BatchDeps:
            raise RuntimeError("missing required environment variable(s): SUPABASE_URL")

        monkeypatch.setattr(batch_predict, "_build_deps", raise_misconfigured)

        instance = _make_handler_instance({"x-batch-secret": "whatever"})
        instance.do_POST()  # must not raise

        status_line, body = _split_response(instance.wfile.getvalue())  # type: ignore[attr-defined]
        assert b"500" in status_line
        assert body["error"] == "internal_error"
