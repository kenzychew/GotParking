"""FastAPI demo service: read-only forecast viewer.

A second, portfolio-consistent front end for GotParking's public
`GET /api/forecast` data (the real one is a Vercel Python function; this is
FastAPI, meant to run standalone on Railway -- see demo/README.md). It
reuses `api/_lib/read_logic.py`'s `handle_forecast_read` verbatim, the same
way `api/forecast.py` does, just glued in through FastAPI instead of
Vercel's `BaseHTTPRequestHandler`. Same typed-503-never-500 contract, same
cached whole-payload shape.

Also serves `GET /api/carparks-geo`, a demo-only read (id/name/lat/lng from
`public.carparks`) used for client-side distance sorting in `static/`, and
`GET /api/carpark-baseline/{carpark_id}`, a demo-only read of the
precomputed `public.carpark_baseline` table that backs the detail panel's
trend chart. Neither endpoint is part of the pinned `/api/forecast`
contract, and neither touches `api/_lib/read_logic.py`.

This service only ever reads already-computed forecasts. It must never
import, call, or expose `api/batch_predict.py` or any write path -- see
demo/README.md's "Credentials" section for why the read-only guarantee
currently rests on operator discipline rather than the credential itself.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# api/_lib is not an installable package -- it's a plain directory Vercel's
# Python router ignores because of the leading underscore (see api/README.md).
# Reusing it here (rather than duplicating read_logic.py) means putting
# api/ on sys.path the same way Vercel's per-function root does.
_API_DIR = Path(__file__).resolve().parent.parent / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from _lib.read_logic import ReadDeps, handle_forecast_read, unavailable_response
from _lib.sg_time import sgt_parts
from _lib.supabase_rest import SupabaseREST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="GotParking demo (read-only forecast viewer)")

_GEO_CACHE_CONTROL = "public, s-maxage=300, stale-while-revalidate=60"


def _load_demo_reader_settings() -> tuple[str, str] | None:
    """Read this demo's own Supabase settings, deliberately not via
    `_lib.config.load_settings()` -- that loader is shared with the
    write-capable Vercel API and requires `SUPABASE_SERVICE_ROLE_KEY`, which
    this read-only demo must never use (see demo/README.md's "Credentials"
    section). Returns None (never raises) if `SUPABASE_URL` or
    `SUPABASE_DEMO_READER_KEY` is missing/blank, so the caller falls through
    to the same typed 503 as any other unavailable-credentials case.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    demo_reader_key = os.environ.get("SUPABASE_DEMO_READER_KEY", "").strip()
    if not supabase_url or not demo_reader_key:
        return None
    return supabase_url.rstrip("/"), demo_reader_key


@app.get("/api/forecast")
def get_forecast() -> JSONResponse:
    """Serve the same cached, parameter-less forecast read as the real API.

    Delegates entirely to `_lib.read_logic.handle_forecast_read`, exactly
    like `api/forecast.py`'s handler does -- same typed 503 on any failure
    (missing env vars, empty table, unreachable Supabase), never a raw 500.
    """
    db: SupabaseREST | None = None
    try:
        demo_settings = _load_demo_reader_settings()
        if demo_settings is None:
            response = unavailable_response()
        else:
            supabase_url, demo_reader_key = demo_settings
            db = SupabaseREST(supabase_url, demo_reader_key)
            response = handle_forecast_read(ReadDeps(db=db))
    except Exception:
        logger.exception("demo forecast: unhandled error")
        response = unavailable_response()
    finally:
        if db is not None:
            db.close()
    return JSONResponse(
        status_code=response.status, content=response.body, headers=response.headers
    )


def _geo_unavailable_response() -> JSONResponse:
    """The typed 503 for the geo endpoint, matching get_forecast's contract:
    any failure (missing credentials, unreachable Supabase, anything else)
    returns this instead of a raw 500.
    """
    return JSONResponse(
        status_code=503,
        content={"error": "geo_unavailable", "message": "Carpark locations temporarily unavailable"},
    )


@app.get("/api/carparks-geo")
def get_carparks_geo() -> JSONResponse:
    """Serve carpark id/name/coordinates for the demo's map, demo-only.

    Never touches `api/_lib/read_logic.py`'s pinned `/api/forecast` contract
    -- this is a separate, demo-only read against `public.carparks` using
    the same `demo_reader` credentials, joined client-side against
    `/api/forecast` by `carpark_id`. Same typed-503-never-500 contract:
    missing credentials or any Supabase failure returns the typed
    unavailable response, never a crash.
    """
    db: SupabaseREST | None = None
    try:
        demo_settings = _load_demo_reader_settings()
        if demo_settings is None:
            return _geo_unavailable_response()
        supabase_url, demo_reader_key = demo_settings
        db = SupabaseREST(supabase_url, demo_reader_key)
        result = db.select(
            "carparks", params={"select": "carpark_id,name,latitude,longitude"}
        )
        return JSONResponse(
            status_code=200,
            content={"carparks": result.rows},
            headers={"Cache-Control": _GEO_CACHE_CONTROL},
        )
    except Exception:
        logger.exception("carparks-geo: unhandled error")
        return _geo_unavailable_response()
    finally:
        if db is not None:
            db.close()


_BASELINE_CACHE_CONTROL = "public, s-maxage=90, stale-while-revalidate=60"


def _baseline_unavailable_response() -> JSONResponse:
    """The typed 503 for the baseline endpoint, matching the other demo-only
    endpoints' contract: missing credentials, permission denied (e.g. the
    `carpark_baseline` grant in `db/schema.sql` section 11b not yet applied
    to the live project), or any other Supabase failure returns this instead
    of a raw 500.
    """
    return JSONResponse(
        status_code=503,
        content={
            "error": "baseline_unavailable",
            "message": "Typical availability data temporarily unavailable",
        },
    )


@app.get("/api/carpark-baseline/{carpark_id}")
def get_carpark_baseline(carpark_id: str) -> JSONResponse:
    """Serve one carpark's typical-availability-by-time-of-day curve.

    Backs the detail panel's trend chart in `static/`: the 96 15-minute
    `slot_of_day` rows of `public.carpark_baseline` for today's SGT
    day-of-week, plus the current SGT slot index so the frontend can mark
    "now" on the curve. `sgt_parts` (shared with the batch-predict feature
    contract) is the single source of truth for the SGT (dow, slot_of_day)
    computation -- see `api/_lib/sg_time.py`.

    An unknown carpark_id, or one with no baseline rows yet, is not a
    failure: PostgREST simply returns zero rows, so this responds 200 with
    an empty `slots` array rather than a 503 -- the frontend distinguishes
    "no data for this carpark" from "the endpoint is down". Same typed-503-
    never-500 contract as `get_forecast`/`get_carparks_geo` for actual
    failures (missing credentials, permission denied, unreachable Supabase,
    anything else).
    """
    db: SupabaseREST | None = None
    try:
        demo_settings = _load_demo_reader_settings()
        if demo_settings is None:
            return _baseline_unavailable_response()
        supabase_url, demo_reader_key = demo_settings
        db = SupabaseREST(supabase_url, demo_reader_key)
        dow, current_slot_of_day = sgt_parts(datetime.now(timezone.utc))
        result = db.select(
            "carpark_baseline",
            params={
                "select": "slot_of_day,avg_available_lots",
                "carpark_id": f"eq.{carpark_id}",
                "dow": f"eq.{dow}",
                "order": "slot_of_day.asc",
            },
        )
        return JSONResponse(
            status_code=200,
            content={
                "carpark_id": carpark_id,
                "dow": dow,
                "current_slot_of_day": current_slot_of_day,
                "slots": result.rows,
            },
            headers={"Cache-Control": _BASELINE_CACHE_CONTROL},
        )
    except Exception:
        logger.exception("carpark-baseline: unhandled error")
        return _baseline_unavailable_response()
    finally:
        if db is not None:
            db.close()


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True),
    name="static",
)
