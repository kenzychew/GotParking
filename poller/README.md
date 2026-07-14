# poller

Cloudflare Worker (TypeScript, cron trigger) with two responsibilities.
The first is polling the LTA DataMall Carpark Availability API every 5 minutes and writing to Supabase `carpark_history` (T3 in the design doc).
After each successful poll cycle, it triggers `POST /api/batch_predict` (secret-gated) so forecasts stay in lockstep with the freshest live data.
The second is a daily refresh of `carpark_baseline`: Premise #11 requires serving to read a small precomputed table, never aggregate the growing `carpark_history` table, so once a day the worker calls the `refresh_carpark_baseline` Supabase RPC to rebuild it from the trailing 28 days of history.

**Live:** `https://gotparking-poller.kenzychew.workers.dev`, cron confirmed running
`*/5 * * * *`. Deploy via `wrangler deploy` from this directory (see the root `CLAUDE.md`
Deploy Configuration section for the full sequence and secret list).

Requires 7 Cloudflare Workers secrets (never committed; see `wrangler.toml`'s header comment for the full list): `LTA_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `BATCH_SHARED_SECRET`, `BATCH_PREDICT_URL`, `HEALTHCHECKS_POLLER_PING_URL`, `HEALTHCHECKS_BASELINE_PING_URL`.
The last one is optional: while it is unset, success pings are skipped silently and failure pings are skipped with a `healthchecks_fail_ping_skipped` log line.

Behavior: LTA fetch retries exactly once on network/timeout errors or a 5xx response (no
backoff) -- a non-5xx HTTP error or malformed JSON gets zero retry (see
`poller/test/cycle.test.ts`); missed-poll alerting via healthchecks.io (>30 min gap);
idempotent writes (`carpark_history`'s composite PK makes a re-poll of the same instant a
no-op, not a duplicate); and a non-fatal batch-predict trigger (a failed trigger logs a
warning and does not fail the poll cycle -- forecast staleness is surfaced elsewhere).

## Daily baseline refresh

The worker's `scheduled` handler dispatches on `controller.cron`, so a single Worker runs both crons defined in `wrangler.toml`'s `[triggers]` array.
`POLL_CRON` (`*/5 * * * *`) runs the poll cycle described above.
`BASELINE_REFRESH_CRON` (`15 19 * * *`, which is 19:15 UTC == 03:15 SGT) runs `runBaselineRefresh`, a single `POST` to the `refresh_carpark_baseline` RPC with a `{}` body (the RPC takes no arguments).
An unrecognized cron string falls back to the poll cycle and logs `unknown_cron`, so a drifted `wrangler.toml` cannot silently drop the baseline refresh.
`test/wranglerConfig.test.ts` pins `wrangler.toml`'s `crons` array to the `POLL_CRON` and `BASELINE_REFRESH_CRON` exported constants, so the two files cannot drift apart.

`refresh_carpark_baseline` (the Postgres function, not this worker) rebuilds the table from the trailing 28 days of `carpark_history`: it upserts fresh cells and prunes cells that fell out of the 28-day window, in a deterministic `ORDER BY` so concurrent runs stay safe.
The RPC call gets a 30-second timeout, larger than the 10-second default used for the 5-minute poll cycle's row writes, since it is one aggregate query over 28 days of history.
It deliberately does not retry on a timeout: a timed-out statement may still be running server-side, and retrying would risk two concurrent heavy aggregations.

The refresh pings its own optional `HEALTHCHECKS_BASELINE_PING_URL` check on success, and `/fail`-pings it with a reason string on any failure.
This is a separate healthchecks.io check from the poller's own 5-minute `HEALTHCHECKS_POLLER_PING_URL` check, on purpose: a once-a-day ping (or fail-ping) landing on the poller's check would corrupt its 5-minute-cadence signal, and vice versa.

Failure semantics: every failure inside `runBaselineRefresh` (a rejected RPC call, a non-JSON response body, anything) is caught, logged as `baseline_refresh_failed`, and `/fail`-pings the baseline check only.
`runBaselineRefresh` never throws out of the handler and never touches the poller check.
Because the refresh runs once a day, a failed run is not retried within the same day; the next day's 03:15 SGT tick retries naturally.

## Commands

```bash
npm install                 # install dependencies
npm run dev                 # wrangler dev --test-scheduled (local, manually-fireable cron)
npm test                    # vitest run --passWithNoTests
npm run typecheck           # tsc --noEmit
npm run deploy               # wrangler deploy
```

Test framework: Vitest, 54/54 passing across 7 test files.

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
