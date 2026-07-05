"""Thin httpx-based client for Supabase PostgREST and Storage REST APIs.

No supabase-py dependency, per the stack decision -- direct httpx calls
against PostgREST (``/rest/v1/<table>``) and Storage
(``/storage/v1/object/<bucket>/<path>``) using the service-role key. This
mirrors `api/_lib/supabase_rest.py`'s design (same retry-once-then-raise
policy, same PostgREST pagination approach) but is training's own copy --
training and api are separate deployables (GitHub Actions vs. Vercel) with
separate uv environments, so the client is duplicated rather than shared
via a cross-project import.

Every call retries exactly once on failure (network error or non-2xx
status) before raising :class:`SupabaseUnavailableError`, matching the
"Supabase read/write failure: retry once, then /fail ping" contract
(design doc Failure Modes registry; Test Requirements training section,
case 6/14).

Tests inject an ``httpx.MockTransport`` instead of hitting the network --
httpx's own built-in test seam, so no extra mocking library is needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import httpx

from gotparking_training.config import POSTGREST_PAGE_SIZE

logger = logging.getLogger(__name__)


class SupabaseUnavailableError(Exception):
    """Raised when a Supabase call fails on both the initial attempt and the
    single retry.

    Callers treat this as the "Supabase read/write failure" case in the
    design doc's Failure Modes registry -- the caller fires a /fail ping
    and surfaces a typed error rather than letting the raw exception
    propagate uncaught.
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
        header_value: The raw header value, e.g. ``"0-0/123"`` or ``"*/0"``.

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
    ``tests.fakes.FakeSupabaseDB`` both satisfy this shape. Pipeline
    modules declare their injected dependency against this narrow Protocol
    rather than the concrete ``SupabaseREST`` class, so tests can supply a
    lightweight in-memory fake without touching a network or a real
    Supabase project.
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

    def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        """See :meth:`SupabaseREST.insert`."""
        ...

    def update(
        self,
        table: str,
        *,
        params: dict[str, Any],
        patch: dict[str, Any],
    ) -> None:
        """See :meth:`SupabaseREST.update`."""
        ...

    def download_storage_object(self, bucket: str, path: str) -> bytes:
        """See :meth:`SupabaseREST.download_storage_object`."""
        ...

    def upload_storage_object(self, bucket: str, path: str, content: bytes) -> None:
        """See :meth:`SupabaseREST.upload_storage_object`."""
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
        timeout: float = 30.0,
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
            timeout: Per-request timeout in seconds. Higher than the api
                lane's default (10s) since training's history load and
                model artifact upload can be larger payloads than a
                per-request batch-predict call.
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
        returned per request; `carpark_history` will exceed that cap well
        before a single weekly training cycle's dataset stops growing, so
        callers must page through results rather than silently truncating.

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

    def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Insert rows via PostgREST (plain POST, no upsert resolution).

        Args:
            table: Table name (e.g. "training_runs").
            rows: List of row dicts to insert.
        """
        url = f"{self.base_url}/rest/v1/{table}"
        headers = dict(self._headers)
        headers["Prefer"] = "return=minimal"

        def do_request() -> httpx.Response:
            resp = self._client.post(url, headers=headers, json=rows)
            resp.raise_for_status()
            return resp

        self._request_with_retry(f"insert {table}", do_request)

    def update(
        self,
        table: str,
        *,
        params: dict[str, Any],
        patch: dict[str, Any],
    ) -> None:
        """PATCH rows matching ``params`` filters with ``patch`` fields.

        Args:
            table: Table name (e.g. "model_config").
            params: PostgREST filter params selecting which row(s) to
                update (e.g. ``{"singleton": "eq.true"}``).
            patch: Fields to set on the matched row(s).
        """
        url = f"{self.base_url}/rest/v1/{table}"
        headers = dict(self._headers)
        headers["Prefer"] = "return=minimal"

        def do_request() -> httpx.Response:
            resp = self._client.patch(url, params=params, headers=headers, json=patch)
            resp.raise_for_status()
            return resp

        self._request_with_retry(f"update {table}", do_request)

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str | None = None,
    ) -> None:
        """Upsert rows via PostgREST (``Prefer: resolution=merge-duplicates``).

        Args:
            table: Table name.
            rows: List of row dicts to upsert.
            on_conflict: Optional comma-separated conflict target column(s).
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

    def download_storage_object(self, bucket: str, path: str) -> bytes:
        """Download a Supabase Storage object.

        Args:
            bucket: Storage bucket name (e.g. "models").
            path: Object path within the bucket (e.g. "lgbm_20260706_050000.txt").

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

    def upload_storage_object(self, bucket: str, path: str, content: bytes) -> None:
        """Upload (upsert) a Supabase Storage object.

        Sends ``x-upsert: true`` so re-running a training job that
        happens to generate the same version string (practically
        impossible given the second-resolution timestamp version format,
        but defensive nonetheless) overwrites rather than errors.

        Args:
            bucket: Storage bucket name (e.g. "models").
            path: Object path within the bucket, exactly
                ``f"{version}.txt"`` per the model artifact contract.
            content: Raw bytes to upload (the LightGBM text-format model
                dump, UTF-8 encoded).
        """
        url = f"{self.base_url}/storage/v1/object/{bucket}/{path}"
        headers = dict(self._headers)
        headers["x-upsert"] = "true"
        headers["Content-Type"] = "text/plain"

        def do_request() -> httpx.Response:
            resp = self._client.put(url, headers=headers, content=content)
            resp.raise_for_status()
            return resp

        self._request_with_retry(f"upload {bucket}/{path}", do_request)
