"""Tests for the Supabase PostgREST/Storage client (api/_lib/supabase_rest.py).

All network I/O is replaced with httpx.MockTransport (see conftest.py).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

import httpx
import pytest

from _lib.supabase_rest import (
    SupabaseREST,
    SupabaseUnavailableError,
    parse_timestamp,
)

BASE_URL = "https://example.supabase.co"
SERVICE_KEY = "test-service-role-key"


def _client(transport: httpx.MockTransport) -> SupabaseREST:
    return SupabaseREST(BASE_URL, SERVICE_KEY, transport=transport)


class TestSelect:
    """Tests for SupabaseREST.select()."""

    def test_happy_path_returns_parsed_rows(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        rows = [{"carpark_id": "1", "name": "Suntec City"}]
        transport = make_sequential_transport([httpx.Response(200, json=rows)])
        client = _client(transport)

        result = client.select("carparks", params={"select": "carpark_id,name"})

        assert result.rows == rows
        assert result.total_count is None

    def test_prefer_count_parses_content_range_total(
        self,
        make_routed_transport: Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport],
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["prefer"] == "count=exact"
            return httpx.Response(200, json=[{"polled_at": "2026-07-05T00:00:00+00:00"}],
                                   headers={"content-range": "0-0/42"})

        client = _client(make_routed_transport(handler))

        result = client.select("carpark_history", params={"limit": "1"}, prefer_count=True)

        assert result.total_count == 42

    def test_prefer_count_with_unknown_total_returns_none(
        self,
        make_routed_transport: Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport],
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[], headers={"content-range": "*/*"})

        client = _client(make_routed_transport(handler))

        result = client.select("carpark_history", prefer_count=True)

        assert result.total_count is None

    def test_retries_once_on_5xx_then_succeeds(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        transport = make_sequential_transport(
            [httpx.Response(503, text="service unavailable"), httpx.Response(200, json=[])]
        )
        client = _client(transport)

        result = client.select("carparks")

        assert result.rows == []

    def test_retries_once_on_network_error_then_succeeds(
        self,
        make_sequential_transport: Callable[
            [Sequence[httpx.Response | BaseException]], httpx.MockTransport
        ],
    ) -> None:
        transport = make_sequential_transport(
            [httpx.ConnectError("connection refused"), httpx.Response(200, json=[{"ok": True}])]
        )
        client = _client(transport)

        result = client.select("carparks")

        assert result.rows == [{"ok": True}]

    def test_raises_supabase_unavailable_after_retry_also_fails(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        transport = make_sequential_transport(
            [httpx.Response(500, text="boom"), httpx.Response(500, text="boom again")]
        )
        client = _client(transport)

        with pytest.raises(SupabaseUnavailableError):
            client.select("carparks")

    def test_raises_supabase_unavailable_after_two_network_errors(
        self,
        make_sequential_transport: Callable[
            [Sequence[httpx.Response | BaseException]], httpx.MockTransport
        ],
    ) -> None:
        transport = make_sequential_transport(
            [httpx.ConnectError("down"), httpx.ConnectError("still down")]
        )
        client = _client(transport)

        with pytest.raises(SupabaseUnavailableError):
            client.select("carparks")


class TestSelectAll:
    """Tests for SupabaseREST.select_all() pagination."""

    def test_single_page_when_under_page_size(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        transport = make_sequential_transport([httpx.Response(200, json=[{"i": 1}, {"i": 2}])])
        client = _client(transport)

        rows = client.select_all("carpark_baseline", page_size=10)

        assert rows == [{"i": 1}, {"i": 2}]

    def test_stops_after_partial_page(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        # page_size=2: page 1 full (2 rows) -> fetch page 2; page 2 partial
        # (1 row) -> stop. Total 3 rows across 2 requests.
        transport = make_sequential_transport(
            [
                httpx.Response(200, json=[{"i": 1}, {"i": 2}]),
                httpx.Response(200, json=[{"i": 3}]),
            ]
        )
        client = _client(transport)

        rows = client.select_all("carpark_baseline", page_size=2)

        assert rows == [{"i": 1}, {"i": 2}, {"i": 3}]

    def test_exact_multiple_of_page_size_fetches_trailing_empty_page(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        # Exactly page_size rows on page 1 means the loop cannot tell it was
        # the last page without trying page 2, which comes back empty.
        transport = make_sequential_transport(
            [
                httpx.Response(200, json=[{"i": 1}, {"i": 2}]),
                httpx.Response(200, json=[]),
            ]
        )
        client = _client(transport)

        rows = client.select_all("carpark_baseline", page_size=2)

        assert rows == [{"i": 1}, {"i": 2}]


class TestUpsert:
    """Tests for SupabaseREST.upsert()."""

    def test_sends_merge_duplicates_prefer_header_and_body(
        self,
        make_routed_transport: Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport],
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["prefer"] = request.headers.get("prefer")
            captured["on_conflict"] = dict(request.url.params).get("on_conflict")
            captured["body"] = request.content
            return httpx.Response(201, json=[])

        client = _client(make_routed_transport(handler))
        rows = [{"carpark_id": "1", "state": "cold_start", "live_lots": 10}]

        client.upsert("carpark_forecast", rows, on_conflict="carpark_id")

        assert captured["prefer"] == "resolution=merge-duplicates,return=minimal"
        assert captured["on_conflict"] == "carpark_id"

    def test_raises_supabase_unavailable_when_write_fails_twice(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        transport = make_sequential_transport(
            [httpx.Response(500, text="fail"), httpx.Response(500, text="fail again")]
        )
        client = _client(transport)

        with pytest.raises(SupabaseUnavailableError):
            client.upsert("carpark_forecast", [{"carpark_id": "1"}])


class TestDownloadStorageObject:
    """Tests for SupabaseREST.download_storage_object()."""

    def test_returns_raw_bytes(
        self,
        make_routed_transport: Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport],
    ) -> None:
        model_bytes = b"tree\nversion=v3\n..."

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/storage/v1/object/models/v3.txt"
            return httpx.Response(200, content=model_bytes)

        client = _client(make_routed_transport(handler))

        content = client.download_storage_object("models", "v3.txt")

        assert content == model_bytes

    def test_missing_object_raises_after_retry(
        self,
        make_sequential_transport: Callable[[Sequence[httpx.Response]], httpx.MockTransport],
    ) -> None:
        transport = make_sequential_transport(
            [httpx.Response(404, text="not found"), httpx.Response(404, text="not found")]
        )
        client = _client(transport)

        with pytest.raises(SupabaseUnavailableError):
            client.download_storage_object("models", "missing.txt")


class TestParseTimestamp:
    """Tests for parse_timestamp()."""

    def test_parses_explicit_offset(self) -> None:
        dt = parse_timestamp("2026-07-05T12:30:00+00:00")

        assert dt == datetime(2026, 7, 5, 12, 30, tzinfo=timezone.utc)

    def test_parses_z_suffix(self) -> None:
        dt = parse_timestamp("2026-07-05T12:30:00Z")

        assert dt == datetime(2026, 7, 5, 12, 30, tzinfo=timezone.utc)

    def test_parses_with_fractional_seconds(self) -> None:
        dt = parse_timestamp("2026-07-05T12:30:00.123456+00:00")

        assert dt.microsecond == 123456

    def test_naive_string_assumed_utc(self) -> None:
        dt = parse_timestamp("2026-07-05T12:30:00")

        assert dt == datetime(2026, 7, 5, 12, 30, tzinfo=timezone.utc)

    def test_non_utc_offset_normalized_to_utc(self) -> None:
        dt = parse_timestamp("2026-07-05T20:30:00+08:00")

        assert dt == datetime(2026, 7, 5, 12, 30, tzinfo=timezone.utc)
