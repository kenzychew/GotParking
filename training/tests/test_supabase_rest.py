"""Tests for gotparking_training.supabase_rest (httpx.MockTransport-based).

No test in this file makes a real network call -- every scenario is driven
by an `httpx.MockTransport` handler (see conftest.py's
`make_sequential_transport`/`make_routed_transport` fixtures).
"""

from __future__ import annotations

import json

import httpx
import pytest

from gotparking_training.supabase_rest import (
    SupabaseREST,
    SupabaseUnavailableError,
    parse_timestamp,
)
from tests.conftest import RoutedTransportFactory, SequentialTransportFactory


def _json_response(payload: object, *, status: int = 200, headers: dict[str, str] | None = None,
                    ) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {})


class TestParseTimestamp:
    """Tests for the PostgREST timestamptz parser."""

    def test_parses_z_suffix(self) -> None:
        dt = parse_timestamp("2026-07-05T12:00:00Z")
        assert dt.isoformat() == "2026-07-05T12:00:00+00:00"

    def test_parses_explicit_offset(self) -> None:
        dt = parse_timestamp("2026-07-05T20:00:00+08:00")
        assert dt.isoformat() == "2026-07-05T12:00:00+00:00"

    def test_naive_timestamp_assumed_utc(self) -> None:
        dt = parse_timestamp("2026-07-05T12:00:00")
        assert dt.isoformat() == "2026-07-05T12:00:00+00:00"


class TestSelect:
    """Tests for SupabaseREST.select."""

    def test_happy_path_returns_rows(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert "/rest/v1/carparks" in str(request.url)
            return _json_response([{"carpark_id": "1", "name": "Suntec City"}])

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        result = client.select("carparks", params={"select": "carpark_id,name"})

        assert result.rows == [{"carpark_id": "1", "name": "Suntec City"}]
        assert result.total_count is None

    def test_prefer_count_parses_content_range(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("Prefer") == "count=exact"
            return _json_response([{"a": 1}], headers={"content-range": "0-0/42"})

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        result = client.select("carpark_history", prefer_count=True)

        assert result.total_count == 42

    def test_retries_once_then_succeeds(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        transport = make_sequential_transport(
            [httpx.ConnectError("boom"), _json_response([{"ok": True}])]
        )
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        result = client.select("carparks")

        assert result.rows == [{"ok": True}]

    def test_fails_after_retry_raises_supabase_unavailable(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        transport = make_sequential_transport(
            [httpx.ConnectError("boom"), httpx.ConnectError("boom again")]
        )
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        with pytest.raises(SupabaseUnavailableError):
            client.select("carparks")


class TestSelectAll:
    """Tests for SupabaseREST.select_all's pagination loop."""

    def test_paginates_until_short_page(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        page1 = [{"i": i} for i in range(3)]
        page2 = [{"i": i} for i in range(3, 5)]  # short page -> stop
        transport = make_sequential_transport([_json_response(page1), _json_response(page2)])
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        rows = client.select_all("carpark_baseline", page_size=3)

        assert rows == page1 + page2

    def test_single_full_page_still_terminates(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        # Exactly page_size rows on page 1, then an empty page 2 -> stop.
        page1 = [{"i": i} for i in range(3)]
        transport = make_sequential_transport([_json_response(page1), _json_response([])])
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        rows = client.select_all("carpark_baseline", page_size=3)

        assert rows == page1


class TestInsert:
    """Tests for SupabaseREST.insert (plain POST, training_runs)."""

    def test_posts_rows_with_return_minimal(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["prefer"] = request.headers.get("Prefer")
            captured["body"] = json.loads(request.content)
            return httpx.Response(201)

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        client.insert("training_runs", [{"candidate_version": "lgbm_x", "promoted": False}])

        assert captured["method"] == "POST"
        assert captured["prefer"] == "return=minimal"
        assert captured["body"] == [{"candidate_version": "lgbm_x", "promoted": False}]


class TestUpdate:
    """Tests for SupabaseREST.update (PATCH, model_config promotion)."""

    def test_patches_with_filter_params_and_body(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["query"] = str(request.url.params)
            captured["body"] = json.loads(request.content)
            return httpx.Response(204)

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        client.update(
            "model_config",
            params={"singleton": "eq.true"},
            patch={"active_model_version": "lgbm_20260706_050000"},
        )

        assert captured["method"] == "PATCH"
        assert "singleton" in str(captured["query"])
        assert captured["body"] == {"active_model_version": "lgbm_20260706_050000"}


class TestUpsert:
    """Tests for SupabaseREST.upsert."""

    def test_merges_duplicates_with_on_conflict(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("Prefer") == "resolution=merge-duplicates,return=minimal"
            assert "on_conflict=carpark_id" in str(request.url)
            return httpx.Response(201)

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        client.upsert("carpark_forecast", [{"carpark_id": "1"}], on_conflict="carpark_id")


class TestStorage:
    """Tests for download_storage_object / upload_storage_object."""

    def test_download_returns_bytes(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/storage/v1/object/models/lgbm_x.txt" in str(request.url)
            return httpx.Response(200, content=b"tree\ndata")

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        content = client.download_storage_object("models", "lgbm_x.txt")

        assert content == b"tree\ndata"

    def test_upload_sends_put_with_x_upsert_true(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["x_upsert"] = request.headers.get("x-upsert")
            captured["body"] = request.content
            return httpx.Response(200)

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        client.upload_storage_object("models", "lgbm_20260706_050000.txt", b"tree\ndata")

        assert captured["method"] == "PUT"
        assert captured["url"] == (
            "https://xyz.supabase.co/storage/v1/object/models/lgbm_20260706_050000.txt"
        )
        assert captured["x_upsert"] == "true"
        assert captured["body"] == b"tree\ndata"

    def test_upload_retries_once_then_raises(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        transport = make_sequential_transport(
            [httpx.ConnectError("boom"), httpx.ConnectError("boom again")]
        )
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        with pytest.raises(SupabaseUnavailableError):
            client.upload_storage_object("models", "lgbm_x.txt", b"data")

    def test_upload_retries_once_then_succeeds(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        transport = make_sequential_transport([httpx.ConnectError("boom"), httpx.Response(200)])
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        client.upload_storage_object("models", "lgbm_x.txt", b"data")  # must not raise
