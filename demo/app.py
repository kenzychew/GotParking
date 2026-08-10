"""FastAPI demo service: read-only forecast viewer.

A second, portfolio-consistent front end for GotParking's public
`GET /api/forecast` data (the real one is a Vercel Python function; this is
FastAPI, meant to run standalone on Railway -- see demo/README.md). It
reuses `api/_lib/read_logic.py`'s `handle_forecast_read` verbatim, the same
way `api/forecast.py` does, just glued in through FastAPI instead of
Vercel's `BaseHTTPRequestHandler`. Same typed-503-never-500 contract, same
cached whole-payload shape.

This service only ever reads already-computed forecasts. It must never
import, call, or expose `api/batch_predict.py` or any write path -- see
demo/README.md's "Credentials" section for why the read-only guarantee
currently rests on operator discipline rather than the credential itself.
"""

from __future__ import annotations

import logging
import os
import sys
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
from _lib.supabase_rest import SupabaseREST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="GotParking demo (read-only forecast viewer)")


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


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True),
    name="static",
)
