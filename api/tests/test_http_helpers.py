"""Tests for the HTTP-layer glue helpers (api/_lib/http_helpers.py)."""

from __future__ import annotations

import io
import json
from email.message import Message

from _lib.http_helpers import (
    HttpResponse,
    get_header,
    unexpected_error_response,
    write_http_response,
)


class _FakeHandler:
    """Minimal stand-in for BaseHTTPRequestHandler's response-writing API."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers_sent: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()
        self.ended = False

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.headers_sent.append((key, value))

    def end_headers(self) -> None:
        self.ended = True


class TestGetHeader:
    """Tests for get_header()."""

    def test_exact_case_match(self) -> None:
        assert get_header({"x-batch-secret": "abc"}, "x-batch-secret") == "abc"

    def test_case_insensitive_match(self) -> None:
        assert get_header({"X-Batch-Secret": "abc"}, "x-batch-secret") == "abc"
        assert get_header({"x-batch-secret": "abc"}, "X-BATCH-SECRET") == "abc"

    def test_missing_header_returns_none(self) -> None:
        assert get_header({"content-type": "application/json"}, "x-batch-secret") is None

    def test_works_with_http_message_object(self) -> None:
        # BaseHTTPRequestHandler.headers is an http.client.HTTPMessage,
        # itself a subclass of email.message.Message -- both support
        # .items() the same way a plain dict does.
        message = Message()
        message["X-Batch-Secret"] = "abc123"

        assert get_header(message, "x-batch-secret") == "abc123"


class TestWriteHttpResponse:
    """Tests for write_http_response()."""

    def test_writes_status_headers_and_json_body(self) -> None:
        handler = _FakeHandler()
        response = HttpResponse(200, {"computed": 10, "generated_at": "2026-07-05T00:00:00+00:00"})

        write_http_response(handler, response)

        assert handler.status == 200
        assert handler.ended is True
        assert json.loads(handler.wfile.getvalue()) == {
            "computed": 10,
            "generated_at": "2026-07-05T00:00:00+00:00",
        }

    def test_defaults_content_type_to_json(self) -> None:
        handler = _FakeHandler()
        write_http_response(handler, HttpResponse(200, {}))

        header_dict = dict(handler.headers_sent)
        assert header_dict["Content-Type"] == "application/json"

    def test_custom_headers_are_preserved(self) -> None:
        handler = _FakeHandler()
        response = HttpResponse(
            200,
            {"generated_at": "now"},
            headers={"Cache-Control": "public, s-maxage=90, stale-while-revalidate=60"},
        )

        write_http_response(handler, response)

        header_dict = dict(handler.headers_sent)
        assert header_dict["Cache-Control"] == "public, s-maxage=90, stale-while-revalidate=60"
        assert header_dict["Content-Type"] == "application/json"

    def test_sends_content_length_matching_body(self) -> None:
        handler = _FakeHandler()
        write_http_response(handler, HttpResponse(200, {"a": "b"}))

        header_dict = dict(handler.headers_sent)
        expected_length = len(json.dumps({"a": "b"}).encode("utf-8"))
        assert int(header_dict["Content-Length"]) == expected_length


class TestUnexpectedErrorResponse:
    """Tests for unexpected_error_response()."""

    def test_default_status_is_500(self) -> None:
        response = unexpected_error_response()

        assert response.status == 500
        assert response.body["error"] == "internal_error"

    def test_status_override(self) -> None:
        response = unexpected_error_response(status=503)

        assert response.status == 503
