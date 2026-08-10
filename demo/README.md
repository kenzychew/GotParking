# demo

A second, portfolio-consistent front end for GotParking's public forecast
data: FastAPI instead of Vercel, meant to run as one Railway service later.
This reuses `api/_lib/read_logic.py`'s `handle_forecast_read` (and
`_lib/config.py` / `_lib/supabase_rest.py`) verbatim through a FastAPI route
in `app.py` -- same business logic, same pinned payload shape, same typed
503-never-500 contract as the real `GET /api/forecast` on
[gotparking.vercel.app](https://gotparking.vercel.app). `static/` is a
minimal list-view frontend (`index.html` + `app.js` + `style.css`) that
fetches that route.

This service is read-only by construction: it never imports, calls, or
routes to `api/batch_predict.py` or any write path.

## Run locally

```bash
cd demo
uv venv .venv && uv pip install -p .venv -r requirements-demo.txt -r requirements-dev.txt
cp .env.example .env   # fill in SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY -- see Credentials below
set -a && source .env && set +a
.venv/bin/uvicorn app:app --reload
```

Visit `http://127.0.0.1:8000/`. Without valid Supabase env vars set,
`GET /api/forecast` still responds correctly with the typed 503 (proving
the "never a raw 500" contract holds here too) rather than erroring.

## Tests

```bash
cd demo
.venv/bin/python -m pytest tests/ -v
```

Follows the same pattern as `api/tests/test_forecast_handler.py`: monkeypatches
`load_settings`/`SupabaseREST` with the shared `api/tests/fakes.FakeSupabaseDB`
test double, so the suite proves the FastAPI route wiring end-to-end without
any real network call or credential.

## Credentials -- read this before deploying anywhere real

**Needs a decision before this gets a real Supabase key wired into Railway.**
`db/README.md` and `db/schema.sql` are explicit: RLS is deny-by-default on
`carpark_forecast`/`carparks`, with grants revoked for `anon`/`authenticated`
on top -- "Only the service-role key ... can touch data." That means the
*only* credential that can currently perform this demo's read is the
**service-role key**, which bypasses RLS entirely and can write to every
table (`carpark_history`, `model_config`, `training_runs`, ...), not a
read-only-safe key. Handing that key to a second deployment would give it
de facto write access to production data, which contradicts this service's
whole point (read-only, no write path).

This was flagged rather than guessed at, per the task brief. Two ways to
close the gap before a real Railway deploy uses a real key:

1. Add a narrow RLS policy granting `SELECT` on just `carpark_forecast` and
   `carparks` to the `anon` role (this is already public data served
   unauthenticated via `/api/forecast`), and mint an anon/publishable key
   scoped by that policy for this demo to use.
2. Provision a dedicated Postgres role with `SELECT`-only grants on those
   two tables and a key for it, separate from both `anon` and
   `service_role`.

Until one of those exists, run this demo locally only, against your own
Supabase project's service-role key (never production's), or with
`SUPABASE_URL` pointed at a local PostgREST-compatible stub. Railway wiring
itself (and picking one of the above) is an explicit follow-up, not part of
this task.

## Layout

```
demo/
  app.py                  FastAPI: GET /api/forecast (reuses _lib/read_logic.py), mounts static/
  static/                 minimal list-view frontend
  requirements-demo.txt   pinned runtime deps
  requirements-dev.txt    pinned test-only deps (pytest)
  railway.toml            Railway build/deploy config (see its header comment on root-directory assumptions)
  .env.example            local env var template
  tests/                  FastAPI TestClient tests against the reused read logic
```
