# poller

Cloudflare Worker (TypeScript, cron trigger) that polls the LTA DataMall Carpark Availability
API every 5 minutes and writes to Supabase `carpark_history` (T3 in the design doc).

Requires: retry-on-failure, missed-poll alerting (>30 min gap), LTA DataMall API key as a
Cloudflare Workers secret (never committed).

Test framework: Vitest.

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
