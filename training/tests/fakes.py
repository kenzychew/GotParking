"""Shared test doubles for the T5 training test suite.

`FakeSupabaseDB` is an in-memory stand-in satisfying the same narrow
interface training's pipeline modules actually call on a Supabase client
(`select`, `select_all`, `insert`, `update`, `upsert`, `download_storage_object`,
`upload_storage_object`). It lets pipeline-level tests stay focused on
business logic rather than re-asserting PostgREST wire-format details --
that layer is already covered by `test_supabase_rest.py`'s
`httpx.MockTransport`-based tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gotparking_training.supabase_rest import SelectResult, SupabaseUnavailableError


def _matches_filter(row: dict[str, Any], key: str, value: str) -> bool:
    """Interpret one PostgREST-style filter (`eq.`/`in.`) against a row."""
    if value.startswith("eq."):
        target = value[len("eq."):]
        row_value = row.get(key)
        if isinstance(row_value, bool):
            return str(row_value).lower() == target.lower()
        return str(row_value) == target
    if value.startswith("in."):
        inner = value[len("in."):].strip("()")
        allowed = set(inner.split(",")) if inner else set()
        return str(row.get(key)) in allowed
    raise NotImplementedError(f"FakeSupabaseDB: unsupported filter {key}={value!r}")


def _apply_order(rows: list[dict[str, Any]], order_param: str) -> list[dict[str, Any]]:
    """Interpret a PostgREST-style `order=<field>.<asc|desc>` param."""
    field_name, _, direction = order_param.partition(".")
    return sorted(rows, key=lambda r: r[field_name], reverse=(direction == "desc"))


@dataclass
class FakeSupabaseDB:
    """In-memory fake for SupabaseREST's narrow call surface.

    Attributes:
        tables: table name -> list of row dicts (the fake's full dataset).
            Mutated in place by `insert`/`update`/`upsert` so tests can
            assert on post-call state via `tables[...]` directly.
        storage: "bucket/path" -> raw bytes to return, or an Exception
            instance to raise (simulating a missing/corrupt artifact).
            Populated by `upload_storage_object` too, so an upload
            followed by a download round-trips.
        fail_tables: table names that should raise SupabaseUnavailableError
            on the NEXT `select`/`select_all`/`insert`/`update` call
            touching them.
        fail_upsert: If True, `upsert` raises SupabaseUnavailableError.
        fail_upload: If True, `upload_storage_object` raises
            SupabaseUnavailableError (simulating an upload failure
            surviving the client's own retry-once logic).
    """

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    fail_tables: set[str] = field(default_factory=set)
    fail_upsert: bool = False
    fail_upload: bool = False
    select_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    inserted: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    updated: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    upserted: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    download_calls: list[str] = field(default_factory=list)
    upload_calls: list[tuple[str, str, bytes]] = field(default_factory=list)

    def _resolve_rows(self, table: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = list(self.tables.get(table, []))
        for key, value in params.items():
            if key in ("select", "limit", "offset", "order"):
                continue
            rows = [r for r in rows if _matches_filter(r, key, value)]
        if "order" in params:
            rows = _apply_order(rows, params["order"])
        return rows

    def select(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        prefer_count: bool = False,
    ) -> SelectResult:
        if table in self.fail_tables:
            raise SupabaseUnavailableError(f"select {table}")
        params = params or {}
        self.select_calls.append((table, dict(params)))

        matching = self._resolve_rows(table, params)
        paged = matching
        if "offset" in params:
            paged = paged[int(params["offset"]):]
        if "limit" in params:
            paged = paged[: int(params["limit"])]

        total_count = len(matching) if prefer_count else None
        return SelectResult(rows=paged, total_count=total_count)

    def select_all(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = 1000,
    ) -> list[dict[str, Any]]:
        if table in self.fail_tables:
            raise SupabaseUnavailableError(f"select_all {table}")
        self.select_calls.append((table, dict(params or {})))
        return self._resolve_rows(table, params or {})

    def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        if table in self.fail_tables:
            raise SupabaseUnavailableError(f"insert {table}")
        self.inserted.setdefault(table, []).extend(rows)
        self.tables.setdefault(table, []).extend(rows)

    def update(
        self,
        table: str,
        *,
        params: dict[str, Any],
        patch: dict[str, Any],
    ) -> None:
        if table in self.fail_tables:
            raise SupabaseUnavailableError(f"update {table}")
        self.updated.append((table, dict(params), dict(patch)))
        matching_rows = self._resolve_rows(table, params)
        for row in matching_rows:
            row.update(patch)

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str | None = None,
    ) -> None:
        if self.fail_upsert:
            raise SupabaseUnavailableError(f"upsert {table}")
        self.upserted[table] = rows

    def download_storage_object(self, bucket: str, path: str) -> bytes:
        key = f"{bucket}/{path}"
        self.download_calls.append(key)
        if key not in self.storage:
            raise SupabaseUnavailableError(f"no such storage object {key}")
        value = self.storage[key]
        if isinstance(value, BaseException):
            raise value
        return value

    def upload_storage_object(self, bucket: str, path: str, content: bytes) -> None:
        self.upload_calls.append((bucket, path, content))
        if self.fail_upload:
            raise SupabaseUnavailableError(f"upload {bucket}/{path}")
        self.storage[f"{bucket}/{path}"] = content

    def close(self) -> None:
        """No-op, matching SupabaseREST.close()'s interface for callers
        that unconditionally close the client in a `finally` block.
        """


class RecordingFailPing:
    """A `fail_ping` fake that records every reason it was called with."""

    def __init__(self) -> None:
        self.reasons: list[str] = []

    def __call__(self, reason: str) -> None:
        self.reasons.append(reason)


def make_clock(fixed_now: datetime) -> Any:
    """Build a zero-arg clock callable that always returns `fixed_now`."""

    def clock() -> datetime:
        return fixed_now

    return clock


def make_history_rows(
    carpark_id: str,
    count: int,
    first_at: datetime,
    step: Any,
    base_value: float = 100.0,
) -> list[dict[str, Any]]:
    """Generate synthetic `carpark_history` rows for FakeSupabaseDB fixtures.

    Produces `count` rows starting at `first_at`, `step` apart, with
    `available_lots = base_value + index` -- a simple deterministic ramp so
    tests can assert exact expected momentum/label values.

    Args:
        carpark_id: The carpark these rows belong to.
        count: Total number of rows to generate.
        first_at: Timestamp of the earliest row.
        step: A `timedelta` between consecutive rows.
        base_value: `available_lots` value for the first row; each
            subsequent row increments by 1.

    Returns:
        A list of row dicts shaped like a PostgREST `carpark_history`
        response (carpark_id, polled_at as an ISO string, available_lots).
    """
    return [
        {
            "carpark_id": carpark_id,
            "polled_at": (first_at + step * i).isoformat(),
            "available_lots": base_value + i,
        }
        for i in range(count)
    ]
