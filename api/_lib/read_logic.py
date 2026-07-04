"""Business logic for the public cached forecast-read endpoint.

Returns the whole carpark-forecast payload with no user-supplied
parameters (design doc D10) -- this is THE PINNED PUBLIC CONTRACT the
frontend lane codes against verbatim, so its shape must not drift:

    {
      "generated_at": "<max generated_at ISO>",
      "carparks": [
        {
          "carpark_id": "1", "name": "Suntec City",
          "state": "ml" | "baseline" | "cold_start",
          "forecast_lots": <int|null>, "tier": "plenty"|"limited"|"very_limited"|null,
          "live_lots": <int>, "model_version": <string|null>
        }, ...
      ]
    }
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from _lib.http_helpers import HttpResponse
from _lib.supabase_rest import SupabaseREST, parse_timestamp

logger = logging.getLogger(__name__)

_CACHE_CONTROL = "public, s-maxage=90, stale-while-revalidate=60"


@dataclass
class ReadDeps:
    """Injectable dependencies for handle_forecast_read().

    Attributes:
        db: Supabase REST client (or a test fake with the same interface:
            a `select(table, params=...)` method).
    """

    db: SupabaseREST


def _unavailable_response() -> HttpResponse:
    """The typed 503 for an empty table or an unreachable Supabase.

    Never a raw 500 -- see the design doc's Failure Modes registry, public
    read row: "Typed 503 'predictions temporarily unavailable' -- never a
    raw 500."
    """
    return HttpResponse(
        503,
        {"error": "predictions_unavailable", "message": "Predictions temporarily unavailable"},
        {"Content-Type": "application/json"},
    )


def handle_forecast_read(deps: ReadDeps) -> HttpResponse:
    """Serve the whole-payload public forecast read.

    Args:
        deps: Injected dependencies.

    Returns:
        200 with the pinned payload and edge-cache headers on success; a
        typed 503 if `carpark_forecast` is empty, Supabase is unreachable,
        or literally anything else about building the response goes wrong.
        The broad exception catch here is deliberate: this is a public,
        no-parameters, read-only endpoint, and the contract is explicit
        that it must NEVER surface a raw 500 under any circumstance.
    """
    try:
        forecast_result = deps.db.select("carpark_forecast", params={"select": "*"})
        carparks_result = deps.db.select("carparks", params={"select": "carpark_id,name"})

        if not forecast_result.rows:
            logger.warning("forecast read: carpark_forecast table is empty")
            return _unavailable_response()

        name_by_id = {row["carpark_id"]: row["name"] for row in carparks_result.rows}

        carparks_payload = []
        for row in forecast_result.rows:
            carpark_id = row["carpark_id"]
            if carpark_id not in name_by_id:
                logger.warning(
                    "forecast read: carpark_id=%s has a forecast row but no "
                    "matching carparks row; falling back to carpark_id as name",
                    carpark_id,
                )
            carparks_payload.append(
                {
                    "carpark_id": carpark_id,
                    "name": name_by_id.get(carpark_id, carpark_id),
                    "state": row["state"],
                    "forecast_lots": row["forecast_lots"],
                    "tier": row["tier"],
                    "live_lots": row["live_lots"],
                    "model_version": row["model_version"],
                }
            )

        generated_at = max(
            parse_timestamp(row["generated_at"]) for row in forecast_result.rows
        ).isoformat()

        body = {"generated_at": generated_at, "carparks": carparks_payload}
        headers = {"Content-Type": "application/json", "Cache-Control": _CACHE_CONTROL}
        return HttpResponse(200, body, headers)
    except Exception:
        logger.exception("forecast read: failed to build response")
        return _unavailable_response()
