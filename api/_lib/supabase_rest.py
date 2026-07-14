"""Thin httpx-based client for Supabase PostgREST and Storage REST APIs.

No supabase-py dependency, per the stack decision -- direct httpx calls
against PostgREST (``/rest/v1/<table>``) and Storage
(``/storage/v1/object/<bucket>/<path>``) using the service-role key. Every
call retries exactly once on failure (network error or non-2xx status)
before raising :class:`SupabaseUnavailableError`, matching the "Supabase
read/write failure: retry once, then /fail ping" contract in the design doc
(batch predict step 8; Test Requirements batch-predict section, case 6).

Tests inject an ``httpx.MockTransport`` instead of hitting the network --
this is httpx's own built-in test seam, so no extra mocking library is
needed beyond httpx itself (already a runtime dependency).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar, runtime_checkable

import httpx

from _lib.config import POSTGREST_PAGE_SIZE

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SupabaseUnavailableError(Exception):
    """Raised when a Supabase call fails on both the initial attempt and the
    single retry.

    Callers treat this as the "Supabase read/write failure" case in the
    design doc's Failure Modes registry -- the caller fires a /fail ping
    and surfaces a typed error rather than letting the raw exception
    propagate.
    """


@dataclass(frozen=True)
class SelectResult:
    """Result of a PostgREST select call.

    Attributes:
        rows: Parsed JSON rows returned by PostgREST.
        total_count: The total matching row count parsed from the
            ``Content-Range`` response header, when ``prefer_count=True``
            was requested; None otherwise.
    """

    rows: list[dict[str, Any]]
    total_count: int | None


def parse_timestamp(value: str) -> datetime:
    """Parse a PostgREST-returned ``timestamptz`` string into an aware datetime.

    Args:
        value: An ISO 8601 timestamp string as returned by PostgREST for a
            ``timestamptz`` column -- either with an explicit numeric offset
            or a trailing "Z".

    Returns:
        A timezone-aware datetime, normalized to UTC.
    """
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_content_range(header_value: str | None) -> int | None:
    """Parse the PostgREST ``Content-Range`` header into a total row count.

    Args:
        header_value: The raw header value, e.g. ``"0-0/123"`` or
            ``"*/0"``.

    Returns:
        The integer total (the part after "/"), or None if the header is
        absent or its total is unknown (``"*"``).
    """
    if not header_value or "/" not in header_value:
        return None
    total = header_value.rsplit("/", 1)[-1]
    if total == "*":
        return None
    try:
        return int(total)
    except ValueError:
        return None


@runtime_checkable
class SupabaseClient(Protocol):
    """Structural interface for a Supabase client.

    :class:`SupabaseREST` (production) and test doubles such as
    ``tests.fakes.FakeSupabaseDB`` both satisfy this shape. Business-logic
    modules (``batch_logic.py``, ``read_logic.py``) declare their injected
    dependency against this narrow Protocol rather than the concrete
    ``SupabaseREST`` class, so tests can supply a lightweight in-memory
    fake without subclassing or monkeypatching the real HTTP client --
    honest dependency injection against an interface, not a concrete type.
    """

    def select(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        prefer_count: bool = False,
    ) -> SelectResult:
        """See :meth:`SupabaseREST.select`."""
        ...

    def select_all(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = POSTGREST_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """See :meth:`SupabaseREST.select_all`."""
        ...

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str | None = None,
    ) -> None:
        """See :meth:`SupabaseREST.upsert`."""
        ...

    def rpc(self, function_name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        """See :meth:`SupabaseREST.rpc`."""
        ...

    def download_storage_object(self, bucket: str, path: str) -> bytes:
        """See :meth:`SupabaseREST.download_storage_object`."""
        ...

    def close(self) -> None:
        """See :meth:`SupabaseREST.close`."""
        ...


class SupabaseREST:
    """Minimal httpx-based Supabase PostgREST + Storage client.

    Attributes:
        base_url: The Supabase project base URL (no trailing slash, no
            path suffix).
    """

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: Supabase project base URL, e.g.
                "https://xyz.supabase.co" (no trailing slash).
            service_role_key: Service-role API key; sent as both the
                ``apikey`` header and a bearer ``Authorization`` header,
                bypassing RLS.
            transport: Optional httpx transport override. Tests pass an
                ``httpx.MockTransport`` here; production leaves this None
                to use the real network transport.
            timeout: Per-request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> SupabaseREST:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request_with_retry(
        self, what: str, do_request: Callable[[], httpx.Response]
    ) -> httpx.Response:
        """Run an HTTP call, retrying exactly once on failure.

        Args:
            what: A short human-readable label for logging (e.g.
                "select carpark_history").
            do_request: A zero-arg callable that performs the request and
                calls ``raise_for_status()`` on the response.

        Returns:
            The successful httpx.Response (from the first or retry
            attempt).

        Raises:
            SupabaseUnavailableError: If both the initial attempt and the
                single retry fail.
        """
        try:
            return do_request()
        except httpx.HTTPError as exc:
            logger.warning("%s failed (%s); retrying once", what, exc)
        try:
            return do_request()
        except httpx.HTTPError as exc:
            logger.error("%s failed again after retry: %s", what, exc)
            raise SupabaseUnavailableError(what) from exc

    def select(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        prefer_count: bool = False,
    ) -> SelectResult:
        """Run a single PostgREST GET (select) request.

        Args:
            table: Table name (e.g. "carparks").
            params: PostgREST query params (select, filters, order, limit,
                offset, etc.), passed through as-is.
            prefer_count: If True, sends ``Prefer: count=exact`` and parses
                the total row count from the ``Content-Range`` response
                header (independent of any ``limit``/``offset``).

        Returns:
            A SelectResult with the parsed rows and, if requested, the
            total matching count.
        """
        url = f"{self.base_url}/rest/v1/{table}"
        headers = dict(self._headers)
        if prefer_count:
            headers["Prefer"] = "count=exact"

        def do_request() -> httpx.Response:
            resp = self._client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp

        resp = self._request_with_retry(f"select {table}", do_request)
        rows = resp.json()
        total_count = (
            _parse_content_range(resp.headers.get("content-range")) if prefer_count else None
        )
        return SelectResult(rows=rows, total_count=total_count)

    def select_all(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = POSTGREST_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Select every row matching ``params``, paginating as needed.

        PostgREST (and Supabase's hosted config) commonly caps the rows
        returned per request; any table that can grow past that cap must
        page through results rather than silently truncating. No
        production caller needs this today -- the `carpark_baseline`
        read, once the motivating example, is now filtered server-side
        to a single (dow, slot) slice via the non-paginated `select`
        instead -- but this stays as test-covered infrastructure for
        any future read that outgrows the page cap.

        Args:
            table: Table name.
            params: PostgREST query params (filters, select, order). Any
                `limit`/`offset` keys are overwritten by the pagination
                loop.
            page_size: Rows requested per page.

        Returns:
            The concatenation of every page's rows.
        """
        all_rows: list[dict[str, Any]] = []
        offset = 0
        base_params = dict(params or {})
        while True:
            page_params = dict(base_params)
            page_params["limit"] = page_size
            page_params["offset"] = offset
            result = self.select(table, params=page_params)
            all_rows.extend(result.rows)
            if len(result.rows) < page_size:
                break
            offset += page_size
        return all_rows

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str | None = None,
    ) -> None:
        """Upsert rows via PostgREST (``Prefer: resolution=merge-duplicates``).

        Args:
            table: Table name (e.g. "carpark_forecast").
            rows: List of row dicts to upsert.
            on_conflict: Optional comma-separated conflict target column(s),
                passed as the ``on_conflict`` query param.
        """
        url = f"{self.base_url}/rest/v1/{table}"
        params = {"on_conflict": on_conflict} if on_conflict else None
        headers = dict(self._headers)
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

        def do_request() -> httpx.Response:
            resp = self._client.post(url, params=params, headers=headers, json=rows)
            resp.raise_for_status()
            return resp

        self._request_with_retry(f"upsert {table}", do_request)

    def rpc(self, function_name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        """Call a Postgres function via PostgREST's ``/rpc/<function_name>`` endpoint.

        Used for server-side aggregation that would otherwise require
        fetching every matching raw row into Python just to reduce it to
        a handful of numbers (see ``carpark_history_stats`` in
        ``db/schema.sql`` and ``batch_logic._load_history_stats``, its
        only caller as of this writing) -- the function itself returns
        one row per group, not one row per underlying table row.

        Args:
            function_name: The Postgres function name (must already be
                exposed via PostgREST, i.e. defined with the anon/service
                role granted EXECUTE).
            args: Named arguments, JSON-encoded as the POST body -- must
                match the function's parameter names exactly.

        Returns:
            The function's result rows.
        """
        url = f"{self.base_url}/rest/v1/rpc/{function_name}"

        def do_request() -> httpx.Response:
            resp = self._client.post(url, headers=self._headers, json=args)
            resp.raise_for_status()
            return resp

        resp = self._request_with_retry(f"rpc {function_name}", do_request)
        result: list[dict[str, Any]] = resp.json()
        return result

    def download_storage_object(self, bucket: str, path: str) -> bytes:
        """Download a Supabase Storage object.

        Args:
            bucket: Storage bucket name (e.g. "models").
            path: Object path within the bucket (e.g. "v3.txt").

        Returns:
            The raw object bytes.
        """
        url = f"{self.base_url}/storage/v1/object/{bucket}/{path}"

        def do_request() -> httpx.Response:
            resp = self._client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp

        resp = self._request_with_retry(f"download {bucket}/{path}", do_request)
        return resp.content
