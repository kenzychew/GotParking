"""Tests for gotparking_training.healthchecks (success + /fail pings)."""

from __future__ import annotations

import httpx

from gotparking_training.healthchecks import ping_fail, ping_success
from tests.conftest import RoutedTransportFactory


class TestPingSuccess:
    """Tests for the weekly-completion success ping."""

    def test_sends_bare_get_to_base_url(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            return httpx.Response(200)

        ping_success(
            "https://hc-ping.com/abc-123", transport=make_routed_transport(handler)
        )

        assert captured["method"] == "GET"
        assert captured["url"] == "https://hc-ping.com/abc-123"

    def test_skipped_when_url_unset(self) -> None:
        # Must not raise even though no transport is configured -- if it
        # tried to make a real request this would hang/fail in CI.
        ping_success(None)
        ping_success("")

    def test_network_failure_is_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        # Must not raise.
        ping_success("https://hc-ping.com/abc-123", transport=httpx.MockTransport(handler))


class TestPingFail:
    """Tests for the /fail ping."""

    def test_posts_reason_to_fail_path(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["body"] = request.content
            return httpx.Response(200)

        ping_fail(
            "https://hc-ping.com/abc-123",
            "MODEL_UPLOAD_FAILED",
            transport=make_routed_transport(handler),
        )

        assert captured["method"] == "POST"
        assert captured["url"] == "https://hc-ping.com/abc-123/fail"
        assert captured["body"] == b"MODEL_UPLOAD_FAILED"

    def test_strips_trailing_slash_before_appending_fail(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200)

        ping_fail(
            "https://hc-ping.com/abc-123/",
            "TRAINING_CRASH",
            transport=make_routed_transport(handler),
        )

        assert captured["url"] == "https://hc-ping.com/abc-123/fail"

    def test_skipped_when_url_unset(self) -> None:
        ping_fail(None, "TRAINING_CRASH")
        ping_fail("", "TRAINING_CRASH")

    def test_network_failure_is_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        ping_fail(
            "https://hc-ping.com/abc-123", "TRAINING_CRASH",
            transport=httpx.MockTransport(handler),
        )
