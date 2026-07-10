# scripts

Standalone research/ops tooling — not deployed anywhere, run manually or via
`.github/workflows/regen-seed-lists.yml`. Three families: the original T1 signal-validation
tools, the coverage-expansion pipeline (mall wave 2026-07-08, full-feed wave 2026-07-09), and
OneMap enrichment (2026-07-10).

## T1 signal validation (original, pre-launch)

- `poll_lta_carparks.py` — polls the live LTA feed on an interval, logs candidate-carpark
  samples to `data/carpark_samples.csv`. Also reused by `build_mall_whitelist.py`'s `observing`
  state (see below), given a `candidate_ids` override. Filters to `LotType=C` (car) only —
  ~18% of the full LTA feed reports separate motorcycle/heavy-vehicle rows sharing the same
  CarParkID with a different AvailableLots (found live 2026-07-09).
- `analyze_variance.py` — reads a samples CSV, flags carparks whose lot-count range clears
  `MIN_MEANINGFUL_RANGE` (20 lots) as worth forecasting. Reused unchanged by every wave's
  `verified`/`rejected` decision.

## Coverage-expansion pipeline

Two waves so far, same downstream steps (3-4 below), different discovery scripts (1-2):

1. `recon_mall_whitelist.py` (mall wave) — fuzzy-matches the live LTA feed's remaining
   `Development` names against data.gov.sg's mall dataset (`rapidfuzz token_sort_ratio >= 85`).
   `recon_full_feed.py` (full-feed wave) — no fuzzy-matching needed; every remaining LTA
   carpark not yet onboarded (and not in `NEVER_ADVANCE_CARPARK_IDS`/already-decided) is its
   own direct candidate. Both are read-only recon passes, no writes.
2. `build_mall_whitelist.py` — the full state machine: `matched`/`needs-manual-disambiguation`
   → **mandatory human sign-off gate** (edit `data/carpark_coverage_map.json` directly, set
   `signed_off: true` — no bypass flag, every run) → `observing` (multi-hour variance window,
   reuses `poll_lta_carparks.run()`) → `verified`/`rejected`. Never writes to Supabase directly.
   `run_full_feed_observation.py` is the full-feed wave's equivalent sign-off-and-observe
   driver (skips the mall-dataset fuzzy-match refresh step, which has no relevance to
   direct-match candidates). See `build_mall_whitelist.py`'s module docstring for the full
   schema and state-machine rationale.
3. `regen_seed_lists.py` — reads `data/carpark_coverage_map.json`'s `verified` entries, merges
   with the existing seed list, regenerates `poller/src/carparks.ts` and
   `frontend/src/seed/seedCarparks.ts` in place (only the data literal changes — every header
   comment, type, and helper function is preserved byte-for-byte). Carpark IDs are not all
   numeric (area-letter-prefixed alphanumeric IDs like `A0007` exist in the full LTA feed) --
   handled throughout, not assumed digits-only. Optionally embeds OneMap enrichment (see
   below) into the frontend output when `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are set.
4. `t0_load_test.py` — capacity load test for a future, larger expansion wave. Read-only against
   production; measures real LightGBM inference cost via a synthetic-but-real trained booster
   (not a dummy stub), never writes to `carpark_forecast`. Writes a report to `docs/`.

## OneMap enrichment (2026-07-10)

Reverse-geocodes carparks to a friendlier building name / address / postal code, backing the
frontend's display names and postal-code proximity search.

- `onemap_client.py` — auth (email+password -> ~72h bearer token), reverse-geocode, and search.
- `onemap_enrich.py` — the batch **and** on-insert-hook script: fetches each carpark's
  coordinate from the live LTA feed's `Location` field, reverse-geocodes it, PATCHes the
  `carparks` row. By default only touches carparks with `onemap_enriched_at IS NULL`, so it's
  safe and cheap to re-run after every future coverage-expansion wave — **this re-run IS the
  "on-insert hook"** for this project's CI-generated-static-list architecture (Approach C):
  there's no live DB trigger to wire into a static poller, so the hook is "run this script
  again," made idempotent enough that doing so is a non-event. After a future wave's `carparks`
  INSERT lands, run `uv run scripts/onemap_enrich.py`, then re-run `regen_seed_lists.py` to
  pick up the new displayName/postalCode/lat/lon into the frontend output.
- "Honest beats invented" (the core guarantee, tested at every layer): a carpark OneMap can't
  resolve keeps `onemap_building_name` null: the frontend's `displayName` field falls back to
  the raw `name`, never a fabricated friendlier name.

## Tests

```bash
uv run pytest tests/       # full suite
uv run ruff check .        # lint
uv run mypy . --ignore-missing-imports   # type check
```

No test touches a real network or a real Supabase/OneMap project — the multi-hour observation
window and every external fetch are dependency-injected (same pattern as `training/`'s
`TrainDeps.load_sinpa`).

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`.
Coverage-expansion plan: `~/.gstack/projects/gstack-playground/ceo-plans/2026-07-07-carpark-coverage-expansion.md`.
