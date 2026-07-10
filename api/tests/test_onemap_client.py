"""Tests for api/_lib/onemap_client.py. All network I/O mocked via httpx.MockTransport."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from _lib.onemap_client import (
    OneMapAuthError,
    OneMapUnavailableError,
    TokenCache,
    fetch_token,
    search_postal_code,
)
from tests.conftest import RoutedTransportFactory, SequentialTransportFactory

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _client(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport)


class TestFetchToken:
    def test_parses_access_token_and_expiry(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            return httpx.Response(200, json={"access_token": "tok-123", "expiry_timestamp": "1783000000"})

        token, expiry = fetch_token("a@b.com", "secret", _client(make_routed_transport(handler)))

        assert token == "tok-123"
        assert expiry == datetime.fromtimestamp(1783000000, tz=timezone.utc)

    def test_raises_auth_error_on_bad_credentials(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": "Unauthorized"})

        with pytest.raises(OneMapAuthError):
            fetch_token("a@b.com", "wrong", _client(make_routed_transport(handler)))

    def test_raises_auth_error_on_transport_failure(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        transport = make_sequential_transport([httpx.ConnectError("refused")])

        with pytest.raises(OneMapAuthError):
            fetch_token("a@b.com", "secret", _client(transport))


class TestSearchPostalCode:
    def test_parses_a_real_match(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "tok-123"
            assert "searchVal=039593" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "BUILDING": "SUNTEC CITY MALL",
                            "LATITUDE": "1.29350132535558",
                            "LONGITUDE": "103.857307495824",
                        }
                    ]
                },
            )

        result = search_postal_code("tok-123", "039593", _client(make_routed_transport(handler)))

        assert result is not None
        assert result.building_name == "SUNTEC CITY MALL"
        assert result.latitude == pytest.approx(1.29350132535558)

    def test_returns_none_when_nothing_found(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        result = search_postal_code("tok-123", "999999", _client(make_routed_transport(handler)))

        assert result is None

    def test_raises_unavailable_on_transport_failure(
        self, make_sequential_transport: SequentialTransportFactory
    ) -> None:
        transport = make_sequential_transport([httpx.ConnectError("refused")])

        with pytest.raises(OneMapUnavailableError):
            search_postal_code("tok-123", "039593", _client(transport))


class TestTokenCache:
    def test_fetches_once_and_reuses_on_subsequent_calls(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                json={"access_token": "tok-123", "expiry_timestamp": str(int((NOW + timedelta(days=3)).timestamp()))},
            )

        client = _client(make_routed_transport(handler))
        cache = TokenCache()

        token1 = cache.get("a@b.com", "secret", client, NOW)
        token2 = cache.get("a@b.com", "secret", client, NOW + timedelta(minutes=5))

        assert token1 == token2 == "tok-123"
        assert call_count == 1

    def test_refetches_once_near_expiry(self, make_routed_transport: RoutedTransportFactory) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            expiry = NOW + timedelta(hours=2) if call_count == 1 else NOW + timedelta(days=3)
            return httpx.Response(
                200, json={"access_token": f"tok-{call_count}", "expiry_timestamp": str(int(expiry.timestamp()))}
            )

        client = _client(make_routed_transport(handler))
        cache = TokenCache()

        token1 = cache.get("a@b.com", "secret", client, NOW)
        # Within the TOKEN_REFRESH_MARGIN (1h) of the first token's 2h expiry -- must refetch.
        token2 = cache.get("a@b.com", "secret", client, NOW + timedelta(hours=1, minutes=30))

        assert token1 == "tok-1"
        assert token2 == "tok-2"
        assert call_count == 2
