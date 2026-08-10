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
cp .env.example .env   # fill in SUPABASE_URL / SUPABASE_DEMO_READER_KEY -- see Credentials below
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
the Supabase env vars/`SupabaseREST` with the shared `api/tests/fakes.FakeSupabaseDB`
test double, so the suite proves the FastAPI route wiring end-to-end without
any real network call or credential.

## Credentials -- read this before deploying anywhere real

`db/README.md` and `db/schema.sql` are explicit: RLS is deny-by-default on
`carpark_forecast`/`carparks`, with grants revoked for `anon`/`authenticated`
on top -- "Only the service-role key ... can touch data." That means the
*only* credential able to perform this demo's read out of the box is the
**service-role key**, which bypasses RLS entirely and can write to every
table (`carpark_history`, `model_config`, `training_runs`, ...), not a
read-only-safe key. Handing that key to a second deployment would give it
de facto write access to production data, which contradicts this service's
whole point (read-only, no write path). This was flagged rather than
guessed at, per the task brief.

**Chosen approach (captain's decision): a dedicated read-only Postgres
role**, separate from both `anon` and `service_role`. The migration is
`db/schema.sql` section 11 (`demo_reader`): `SELECT`-only grants plus RLS
policies scoped to exactly `carpark_forecast` and `carparks`, granted to
`authenticator` so PostgREST can switch into it via a JWT's `role` claim --
same mechanism Supabase uses for `anon`/`authenticated` themselves. That
section only creates the role and its grants; it does **not** mint the key.
Applying it is a manual step for whoever runs it against the live project
(paste into the Supabase SQL Editor, same as the rest of `schema.sql`) --
this PR does not execute it.

`demo/app.py` reads its Supabase credentials directly from
`SUPABASE_URL`/**`SUPABASE_DEMO_READER_KEY`** (not through
`_lib.config.load_settings()`, which is shared with the write-capable
Vercel API and requires the service-role key). If either var is
missing/blank, `GET /api/forecast` falls straight through to the typed 503
rather than crashing -- so the app runs fine before the role/key exist,
just without live data.

After applying section 11, two things need to happen before this demo can
actually use `demo_reader` for real:

1. Generate a JWT signed with the project's JWT secret (Project Settings ->
   API) whose payload includes `"role": "demo_reader"` -- there's no
   dashboard button for this, it's hand-crafted the same way the
   anon/service_role keys themselves are. `db/schema.sql`'s section 11
   comment has the details.
2. Set that JWT as `SUPABASE_DEMO_READER_KEY` wherever this demo runs
   (Railway env vars for a real deploy, or a local `.env` for testing
   against your own project).

Until both of those exist, run this demo locally only, against your own
Supabase project (pointing `SUPABASE_DEMO_READER_KEY` at any key with read
access there, never production's service-role key), or with `SUPABASE_URL`
pointed at a local PostgREST-compatible stub.

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
