# poller

Cloudflare Worker (TypeScript, cron trigger) that polls the LTA DataMall Carpark Availability
API every 5 minutes and writes to Supabase `carpark_history` (T3 in the design doc). After
each successful poll cycle, it triggers `POST /api/batch_predict` (secret-gated) so forecasts
stay in lockstep with the freshest live data.

**Live:** `https://gotparking-poller.kenzychew.workers.dev`, cron confirmed running
`*/5 * * * *`. Deploy via `wrangler deploy` from this directory (see the root `CLAUDE.md`
Deploy Configuration section for the full sequence and secret list).

Requires 6 Cloudflare Workers secrets (never committed; see `wrangler.toml`'s header comment
for the full list): `LTA_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`BATCH_SHARED_SECRET`, `BATCH_PREDICT_URL`, `HEALTHCHECKS_POLLER_PING_URL`.

Behavior: retry-on-failure (LTA fetch), missed-poll alerting via healthchecks.io (>30 min
gap), idempotent writes (`carpark_history`'s composite PK makes a re-poll of the same instant
a no-op, not a duplicate), and a non-fatal batch-predict trigger (a failed trigger logs a
warning and does not fail the poll cycle -- forecast staleness is surfaced elsewhere).

Test framework: Vitest, 38/38 passing (`npx vitest run`).

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
