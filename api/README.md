# api

Vercel Python serverless functions for carpark predictions (T4 in the design doc), re-specced
under the 2026-07-04 D10 batch-precompute amendment. Three endpoints as of 2026-07-10 (each
its own `vercel.json` service, per the `services` model -- see the root README's Vercel deploy
section for why):

- `batch_predict.py` -- `POST /api/batch_predict`. Secret-gated (`x-batch-secret` header),
  triggered by the poller after each successful poll cycle. Computes every active carpark's
  forecast (LightGBM where eligible, historical-average baseline otherwise, cold-start below
  the data threshold) and upserts the whole set into `carpark_forecast`.
- `forecast.py` -- `GET /api/forecast`. The public-facing forecast read: a parameter-less,
  edge-cached read of the whole `carpark_forecast` payload. Never touches the model directly.
- `geocode_postal.py` -- `GET /api/geocode_postal?postal=<code>` (2026-07-10, OneMap
  enrichment feature). Public, no secret gating. Resolves a Singapore postal code to a
  coordinate via OneMap's search API server-side, so the frontend's postal-code proximity
  search never sees OneMap credentials -- the actual nearest-carpark distance sort happens
  client-side (haversine) against each carpark's already-embedded lat/lon, not another
  round-trip here.

Shared business logic lives under `_lib/` (Vercel's Python function router ignores any
directory starting with an underscore, so nothing in there is independently routable):

- `sg_time.py` -- the SGT (fixed UTC+8) day-of-week/slot-of-day helper and the SG public
  holiday table, sourced from MOM's consolidated Public Holidays dataset on data.gov.sg.
- `config.py` -- environment-variable settings and tunable constants (cold-start thresholds,
  momentum freshness window, tier ratios, forecast horizon). OneMap credentials
  (`onemap_email`/`onemap_password`) are optional here -- unlike the required fields, their
  absence doesn't break `/api/forecast`/`/api/batch_predict`, only `geocode_postal.py`.
- `supabase_rest.py` -- an httpx-based PostgREST/Storage client (no supabase-py dependency),
  with a retry-once policy applied uniformly to every call, plus `rpc()` for calling Postgres
  functions directly (added 2026-07-10 for `carpark_history_stats`'s server-side aggregation).
- `onemap_client.py` -- a small httpx-based OneMap search-API client + warm-instance token
  cache (mirrors `model_cache.py`'s pattern: a Vercel function's warm instance keeps a valid
  ~72h token alive across invocations instead of re-authenticating every request). Not shared
  with `scripts/onemap_client.py` -- api/ and scripts/ are separate deployments with separate
  dependency sets, so this is duplication with a documented reason (same convention as
  poller/training's independently-tested SGT/feature-contract copies).
- `model_cache.py` -- the warm-instance LightGBM booster cache, keyed by `model_config`'s
  `active_model_version`; reloads only on a version change, and tracks the last-known-good
  booster for the missing/corrupt-artifact fallback path.
- `features.py` / `tiers.py` -- the LightGBM feature-vector contract and the capacity-relative
  availability tiers (plenty/limited/very_limited).
- `healthchecks.py` -- the `/fail` ping helper (reuses the training job's healthchecks.io
  check, since model-artifact problems are conceptually a training-pipeline concern).
- `http_helpers.py` -- thin glue between `BaseHTTPRequestHandler` and the logic below.
- `batch_logic.py` / `read_logic.py` / `geocode_logic.py` -- the actual per-carpark state
  decision, the public read's response shaping, and the postal-code resolution, each
  independently testable without a socket or a real Supabase/OneMap project.

Responsibilities: check `model_config.active_model_version`, cache the loaded model in
warm-instance memory, cold-start fallback for carparks below the data threshold, and explicit
error handling for missing/corrupt model artifacts (falls back to the last-known-good model,
then to baseline-for-everyone plus a `/fail` ping -- `carpark_forecast` is never left empty
and neither endpoint raises a raw 500).

Test framework: pytest, run locally via `uv run pytest` from this directory. `api/pyproject.toml`
holds the dev-only pytest dependency; the runtime deps actually deployed to Vercel live in
`api/requirements.txt` (`lightgbm`, `numpy`, `httpx` only -- Vercel's per-service builds read
this copy; the root `requirements.txt` is a human-facing mirror kept in lockstep).

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
