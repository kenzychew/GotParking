"""Tests for the healthchecks.io /fail ping helper (api/_lib/healthchecks.py)."""

from __future__ import annotations

import httpx
import pytest

from _lib.healthchecks import fire_fail_ping
from tests.conftest import RoutedTransportFactory, SequentialTransportFactory


class TestFireFailPing:
    """Tests for fire_fail_ping()."""

    @pytest.mark.parametrize("missing_url", [None, ""])
    def test_skips_silently_when_url_unset(
        self,
        missing_url: str | None,
        make_routed_transport: RoutedTransportFactory,
    ) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200)

        transport = make_routed_transport(handler)
        fire_fail_ping(missing_url, "MODEL_ARTIFACT_MISSING", transport=transport)

        assert calls == []  # never attempted a request

    def test_posts_reason_as_body_to_fail_path(
        self,
        make_routed_transport: RoutedTransportFactory,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["body"] = request.content
            return httpx.Response(200)

        fire_fail_ping(
            "https://hc-ping.com/abc-123",
            "MODEL_ARTIFACT_MISSING",
            transport=make_routed_transport(handler),
        )

        assert captured["method"] == "POST"
        assert captured["url"] == "https://hc-ping.com/abc-123/fail"
        assert captured["body"] == b"MODEL_ARTIFACT_MISSING"

    def test_strips_trailing_slash_before_appending_fail(
        self,
        make_routed_transport: RoutedTransportFactory,
    ) -> None:
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(200)

        fire_fail_ping(
            "https://hc-ping.com/abc-123/",
            "SUPABASE_UNAVAILABLE",
            transport=make_routed_transport(handler),
        )

        assert captured_urls == ["https://hc-ping.com/abc-123/fail"]

    def test_network_failure_is_swallowed_not_raised(
        self,
        make_sequential_transport: SequentialTransportFactory,
    ) -> None:
        transport = make_sequential_transport([httpx.ConnectError("unreachable")])

        # Must not raise.
        fire_fail_ping("https://hc-ping.com/abc-123", "SUPABASE_UNAVAILABLE", transport=transport)

    def test_error_status_response_is_swallowed_not_raised(
        self,
        make_routed_transport: RoutedTransportFactory,
    ) -> None:
        # fire_fail_ping does not call raise_for_status(), so a non-2xx
        # response from healthchecks.io itself must not raise either.
        transport = make_routed_transport(lambda request: httpx.Response(500))

        fire_fail_ping("https://hc-ping.com/abc-123", "MODEL_ARTIFACT_MISSING", transport=transport)
