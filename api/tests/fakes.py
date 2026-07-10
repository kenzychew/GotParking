"""Shared test doubles for the T4 test suite.

`FakeSupabaseDB` is an in-memory stand-in satisfying the same narrow
interface `batch_logic.py`/`read_logic.py` actually call on a Supabase
client (`select`, `select_all`, `upsert`, `download_storage_object`). It
lets orchestration-level tests (state decisions, feature building, error
handling) stay focused on business logic rather than re-asserting
PostgREST wire-format details -- that layer is already covered by
`test_supabase_rest.py`'s `httpx.MockTransport`-based tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from _lib.supabase_rest import SelectResult, SupabaseUnavailableError


def _matches_filter(row: dict[str, Any], key: str, value: str) -> bool:
    """Interpret one PostgREST-style filter (`eq.`/`in.`/`gte.`/`lte.`) against a row."""
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
    if value.startswith("gte.") or value.startswith("lte."):
        op, target = value[:3], value[4:]
        row_value = row.get(key)
        if row_value is None:
            return False
        # Both sides are datetimes in every current caller (polled_at
        # comparisons) -- parse the target the same way the real client
        # does, so a naive-vs-aware mismatch fails loudly in tests too.
        from _lib.supabase_rest import parse_timestamp

        target_dt = parse_timestamp(target)
        row_dt = parse_timestamp(row_value) if isinstance(row_value, str) else row_value
        if op == "gte":
            return bool(row_dt >= target_dt)
        return bool(row_dt <= target_dt)
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
        storage: "bucket/path" -> raw bytes to return, or an Exception
            instance to raise (simulating a missing/corrupt artifact or a
            Storage failure).
        fail_tables: table names that should raise SupabaseUnavailableError
            on the NEXT `select`/`select_all` call (simulating a Supabase
            read failure that survives the client's own retry-once logic,
            which this fake does not separately model -- batch_logic.py
            treats SupabaseUnavailableError identically regardless of which
            attempt inside SupabaseREST produced it).
        fail_upsert: If True, `upsert` raises SupabaseUnavailableError.
        fail_rpc: Function names that should raise SupabaseUnavailableError
            on the next `rpc` call.
        select_calls: Every (table, params) pair passed to `select`, in
            call order -- lets tests assert on request counts/shapes.
        select_all_calls: Same, but for `select_all` calls.
        rpc_calls: Every (function_name, args) pair passed to `rpc`.
    """

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    fail_tables: set[str] = field(default_factory=set)
    fail_upsert: bool = False
    fail_rpc: set[str] = field(default_factory=set)
    select_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    select_all_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    rpc_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    upserted: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    download_calls: list[str] = field(default_factory=list)

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
        params = params or {}
        self.select_all_calls.append((table, dict(params)))
        return self._resolve_rows(table, params)

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

    def rpc(self, function_name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        """Simulate `carpark_history_stats` (the only RPC this fake supports so far).

        Reimplements the Postgres function's own aggregation
        (MIN/COUNT/MAX/latest-by-polled_at, grouped by carpark_id) over
        `self.tables["carpark_history"]`, so tests exercise the same
        filtering semantics `_matches_filter` already provides rather than
        a bespoke shortcut.
        """
        if function_name in self.fail_rpc:
            raise SupabaseUnavailableError(f"rpc {function_name}")
        self.rpc_calls.append((function_name, dict(args)))
        if function_name != "carpark_history_stats":
            raise NotImplementedError(f"FakeSupabaseDB: unsupported rpc {function_name!r}")

        carpark_ids = args["p_carpark_ids"]
        since = args["p_since"]
        rows = self._resolve_rows(
            "carpark_history",
            {
                "carpark_id": f"in.({','.join(carpark_ids)})",
                "polled_at": f"gte.{since}",
            },
        )

        from _lib.supabase_rest import parse_timestamp

        grouped: dict[str, list[tuple[datetime, int]]] = {}
        for row in rows:
            polled_at = row["polled_at"]
            grouped.setdefault(row["carpark_id"], []).append(
                (
                    parse_timestamp(polled_at) if isinstance(polled_at, str) else polled_at,
                    row["available_lots"],
                )
            )

        result: list[dict[str, Any]] = []
        for carpark_id, samples in grouped.items():
            samples.sort(key=lambda sample: sample[0])
            result.append(
                {
                    "carpark_id": carpark_id,
                    "first_polled_at": samples[0][0].isoformat(),
                    "sample_count": len(samples),
                    "capacity": max(available for _, available in samples),
                    "live_lots": samples[-1][1],
                }
            )
        return result

    def download_storage_object(self, bucket: str, path: str) -> bytes:
        key = f"{bucket}/{path}"
        self.download_calls.append(key)
        if key not in self.storage:
            raise SupabaseUnavailableError(f"no such storage object {key}")
        value = self.storage[key]
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        """No-op, matching SupabaseREST.close()'s interface for callers
        (e.g. the Vercel handler files) that unconditionally close the
        client in a `finally` block.
        """


class RecordingFailPing:
    """A `fail_ping` fake that records every (reason) it was called with."""

    def __init__(self) -> None:
        self.reasons: list[str] = []

    def __call__(self, reason: str) -> None:
        self.reasons.append(reason)


def make_clock(fixed_now: object) -> Any:
    """Build a zero-arg clock callable that always returns `fixed_now`."""

    def clock() -> object:
        return fixed_now

    return clock


def make_history_rows(
    carpark_id: str,
    count: int,
    first_at: datetime,
    latest_at: datetime,
    capacity: int,
    live_lots: int,
) -> list[dict[str, Any]]:
    """Generate synthetic `carpark_history` rows for FakeSupabaseDB fixtures.

    Produces `count` rows evenly spaced between `first_at` and `latest_at`
    (inclusive of both endpoints). Every row is set to `available_lots =
    capacity` except the very last one (at `latest_at`), which is set to
    `live_lots` -- this gives deterministic first/count/max/latest
    aggregates without hand-writing hundreds of literal rows.

    Args:
        carpark_id: The carpark these rows belong to.
        count: Total number of rows to generate (must be >= 2).
        first_at: Timestamp of the earliest row.
        latest_at: Timestamp of the most recent row.
        capacity: available_lots value for every row except the last.
        live_lots: available_lots value for the last (most recent) row.

    Returns:
        A list of row dicts shaped like a PostgREST `carpark_history`
        response (carpark_id, polled_at as an ISO string, available_lots).
    """
    if count < 2:
        raise ValueError("need at least 2 rows to have a distinct first and latest")
    span = (latest_at - first_at) / (count - 1)
    rows = []
    for i in range(count):
        polled_at = first_at + span * i
        available = live_lots if i == count - 1 else capacity
        rows.append(
            {
                "carpark_id": carpark_id,
                "polled_at": polled_at.isoformat(),
                "available_lots": available,
            }
        )
    return rows
