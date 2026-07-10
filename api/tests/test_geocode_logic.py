"""Tests for api/_lib/geocode_logic.py (handle_geocode_postal)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from _lib.geocode_logic import GeocodeDeps, handle_geocode_postal
from _lib.onemap_client import TokenCache
from tests.conftest import RoutedTransportFactory

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _deps(transport: httpx.MockTransport, *, email: str | None = "a@b.com", password: str | None = "secret") -> GeocodeDeps:
    return GeocodeDeps(
        onemap_email=email,
        onemap_password=password,
        http_client=httpx.Client(transport=transport),
        token_cache=TokenCache(),
        clock=lambda: NOW,
    )


class TestHandleGeocodePostal:
    def test_missing_postal_param_returns_400(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should never reach OneMap without a postal param")

        response = handle_geocode_postal(_deps(make_routed_transport(handler)), None)

        assert response.status == 400
        assert response.body["error"] == "bad_request"

    def test_blank_postal_param_returns_400(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should never reach OneMap without a postal param")

        response = handle_geocode_postal(_deps(make_routed_transport(handler)), "   ")

        assert response.status == 400

    def test_missing_credentials_returns_503_without_calling_onemap(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should never reach OneMap without configured credentials")

        response = handle_geocode_postal(
            _deps(make_routed_transport(handler), email=None, password=None), "039593"
        )

        assert response.status == 503
        assert response.body["error"] == "geocoding_unavailable"

    def test_resolved_postal_code_returns_200_with_coordinate(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "getToken" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "tok-123", "expiry_timestamp": "1900000000"}
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"BUILDING": "SUNTEC CITY MALL", "LATITUDE": "1.2935", "LONGITUDE": "103.8573"}
                    ]
                },
            )

        response = handle_geocode_postal(_deps(make_routed_transport(handler)), "039593")

        assert response.status == 200
        assert response.body == {
            "building_name": "SUNTEC CITY MALL",
            "latitude": pytest.approx(1.2935),
            "longitude": pytest.approx(103.8573),
        }
        assert "Cache-Control" in response.headers

    def test_unresolvable_postal_code_returns_404(self, make_routed_transport: RoutedTransportFactory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "getToken" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "tok-123", "expiry_timestamp": "1900000000"}
                )
            return httpx.Response(200, json={"results": []})

        response = handle_geocode_postal(_deps(make_routed_transport(handler)), "999999")

        assert response.status == 404
        assert response.body["error"] == "postal_code_not_found"

    def test_onemap_transport_failure_returns_503_not_a_crash(
        self, make_routed_transport: RoutedTransportFactory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        response = handle_geocode_postal(_deps(make_routed_transport(handler)), "039593")

        assert response.status == 503
