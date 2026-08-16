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

    def test_page_survives_transient_failure_via_backoff_retry(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        # A deep-offset page: both of select()'s own attempts hit a
        # transient 500 (matching the real carpark_history failure), which
        # would normally propagate as SupabaseUnavailableError. select_all's
        # page-level backoff retry gets a fresh pair of attempts and
        # succeeds on the first one.
        page1 = [{"i": i} for i in range(3)]
        page2 = [{"i": 3}]  # short page -> stop
        transport = make_sequential_transport(
            [
                _json_response({"error": "server error"}, status=500),
                _json_response({"error": "server error"}, status=500),
                _json_response(page1),
                _json_response(page2),
            ]
        )
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)
        sleeps: list[float] = []

        rows = client.select_all("carpark_history", page_size=3, sleep=sleeps.append)

        assert rows == page1 + page2
        assert sleeps == [1.0]

    def test_page_retry_budget_exhausted_raises_supabase_unavailable(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        # page_max_attempts=2 page-level attempts, each burning select()'s
        # own built-in retry (2 raw requests) -> 4 straight 500s exhausts
        # the whole budget and the error propagates, exactly like the
        # crash in the real failed training runs.
        transport = make_sequential_transport(
            [_json_response({"error": "server error"}, status=500)] * 4
        )
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        with pytest.raises(SupabaseUnavailableError):
            client.select_all(
                "carpark_history", page_size=3, page_max_attempts=2, sleep=lambda _: None
            )


class TestSelectAllKeyset:
    """Tests for SupabaseREST.select_all's keyset (seek) pagination path,
    used for `carpark_history` (see `data_loading.load_carpark_history`)
    instead of LIMIT/OFFSET -- OFFSET cost is proportional to depth, which
    made a full walk of a multi-million-row table roughly O(n^2) in
    scanned rows and pushed the weekly job past its timeout."""

    def test_first_page_has_no_or_filter_and_orders_on_both_columns(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["query"] = str(request.url.params)
            return _json_response([{"polled_at": "t1", "carpark_id": "1"}])

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        rows = client.select_all(
            "carpark_history", page_size=5, keyset_columns=("polled_at", "carpark_id"),
        )

        assert rows == [{"polled_at": "t1", "carpark_id": "1"}]
        query = str(captured["query"])
        assert "order=polled_at.asc%2Ccarpark_id.asc" in query or (
            "order=polled_at.asc,carpark_id.asc" in query
        )
        assert "or=" not in query
        assert "offset" not in query

    def test_second_page_seeks_strictly_after_last_row_via_or_and_filter(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        page1 = [
            {"polled_at": "2026-07-05T00:00:00+00:00", "carpark_id": "1"},
            {"polled_at": "2026-07-05T00:00:00+00:00", "carpark_id": "2"},
        ]
        page2 = [{"polled_at": "2026-07-05T00:05:00+00:00", "carpark_id": "1"}]  # short -> stop
        captured_filters: list[str | None] = []
        pages = [page1, page2]

        def handler(request: httpx.Request) -> httpx.Response:
            captured_filters.append(request.url.params.get("or"))
            return _json_response(pages.pop(0))

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )

        rows = client.select_all(
            "carpark_history", page_size=2, keyset_columns=("polled_at", "carpark_id"),
        )

        assert rows == page1 + page2
        # First page: no seek filter at all.
        assert captured_filters[0] is None
        # Second page: seeks strictly after the last row of page 1
        # (polled_at="...00:00:00", carpark_id="2") -- neither re-fetching
        # carpark "2" at that timestamp nor skipping a carpark that could
        # share the exact same polled_at.
        assert captured_filters[1] == (
            "(polled_at.gt.2026-07-05T00:00:00+00:00,"
            "and(polled_at.eq.2026-07-05T00:00:00+00:00,carpark_id.gt.2))"
        )

    def test_resuming_from_a_cursor_seeks_from_there_not_the_beginning(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["or"] = request.url.params.get("or")
            return _json_response([])

        client = SupabaseREST(
            "https://xyz.supabase.co", "key", transport=make_routed_transport(handler)
        )
        client.select_all(
            "carpark_history",
            page_size=1000,
            keyset_columns=("polled_at", "carpark_id"),
            keyset_cursor=("2026-07-05T00:00:00+00:00", "42"),
        )

        assert captured["or"] == (
            "(polled_at.gt.2026-07-05T00:00:00+00:00,"
            "and(polled_at.eq.2026-07-05T00:00:00+00:00,carpark_id.gt.42))"
        )

    def test_no_skip_or_double_process_across_the_resume_boundary(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        """A resumed walk (keyset_cursor set) must pick up exactly where a
        prior walk's on_page callback last recorded, not skip a row or
        reprocess the cursor row itself."""
        all_rows = [
            {"polled_at": f"2026-07-05T00:0{i}:00+00:00", "carpark_id": "1"} for i in range(5)
        ]
        # Simulate: a first "attempt" already processed rows 0-2 (cursor is
        # row 2's key); the resumed attempt should fetch exactly rows 3-4.
        remaining = all_rows[3:]
        transport = make_sequential_transport([_json_response(remaining)])
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        rows = client.select_all(
            "carpark_history",
            page_size=10,
            keyset_columns=("polled_at", "carpark_id"),
            keyset_cursor=(all_rows[2]["polled_at"], all_rows[2]["carpark_id"]),
        )

        assert rows == all_rows[3:]  # neither row 2 (double-process) nor a gap (skip)

    def test_on_page_callback_fires_with_each_page_for_cursor_persistence(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        page1 = [
            {"polled_at": "2026-07-05T00:00:00+00:00", "carpark_id": "1"},
            {"polled_at": "2026-07-05T00:01:00+00:00", "carpark_id": "1"},
        ]
        page2 = [{"polled_at": "2026-07-05T00:02:00+00:00", "carpark_id": "1"}]  # short -> stop
        transport = make_sequential_transport([_json_response(page1), _json_response(page2)])
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)
        seen_pages: list[list[dict[str, object]]] = []

        client.select_all(
            "carpark_history",
            page_size=2,
            keyset_columns=("polled_at", "carpark_id"),
            on_page=seen_pages.append,
        )

        assert seen_pages == [page1, page2]

    def test_default_offset_pagination_is_unchanged_when_keyset_columns_omitted(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        """Backward-compat guard: callers that don't opt into keyset
        pagination (every table but `carpark_history`) must keep getting
        the exact same LIMIT/OFFSET behavior as before."""
        page1 = [{"i": i} for i in range(3)]
        page2 = [{"i": i} for i in range(3, 5)]
        transport = make_sequential_transport([_json_response(page1), _json_response(page2)])
        client = SupabaseREST("https://xyz.supabase.co", "key", transport=transport)

        rows = client.select_all("carpark_baseline", page_size=3)

        assert rows == page1 + page2


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
