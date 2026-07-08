# scripts

Standalone research/ops tooling — not deployed anywhere, run manually or via
`.github/workflows/regen-seed-lists.yml`. Two families: the original T1 signal-validation
tools, and the 2026-07-08 carpark coverage-expansion pipeline.

## T1 signal validation (original, pre-launch)

- `poll_lta_carparks.py` — polls the live LTA feed on an interval, logs candidate-carpark
  samples to `data/carpark_samples.csv`. Also reused by `build_mall_whitelist.py`'s `observing`
  state (see below), given a `candidate_ids` override.
- `analyze_variance.py` — reads `data/carpark_samples.csv`, flags carparks whose lot-count
  range clears `MIN_MEANINGFUL_RANGE` (20 lots) as worth forecasting. Reused unchanged by the
  coverage-expansion pipeline's `verified`/`rejected` decision.

## Coverage-expansion pipeline (2026-07-08)

Run in this order:

1. `recon_mall_whitelist.py` — Phase 0 recon only. Fetches the live LTA feed, excludes existing
   `carparks` rows, fuzzy-matches remaining `Development` names against data.gov.sg's mall
   dataset (`rapidfuzz token_sort_ratio >= 85`), prints a candidate count. No writes.
2. `build_mall_whitelist.py` — the full state machine: `matched`/`needs-manual-disambiguation`
   → **mandatory human sign-off gate** (edit `data/carpark_coverage_map.json` directly, set
   `signed_off: true` on approved entries — no bypass flag, every run) → `observing`
   (multi-hour variance window, reuses `poll_lta_carparks.run()`) → `verified`/`rejected`.
   Never writes to Supabase directly — prints the `carparks` INSERT SQL for a human/CI step to
   apply. See the module docstring for the full schema and rationale (fuzzy-match and variance
   validation are NOT orthogonal checks — a live run caught a real false-building match that
   only the human gate stopped).
3. `regen_seed_lists.py` — reads `data/carpark_coverage_map.json`'s `verified` entries, merges
   with the existing seed list, regenerates `poller/src/carparks.ts` and
   `frontend/src/seed/seedCarparks.ts` in place (only the data literal changes — every header
   comment, type, and helper function is preserved byte-for-byte).
4. `t0_load_test.py` — capacity load test for a future, larger expansion wave. Read-only against
   production; measures real LightGBM inference cost via a synthetic-but-real trained booster
   (not a dummy stub), never writes to `carpark_forecast`. Writes a report to `docs/`.

Coverage-map schema, sign-off mechanism, and the candidate state machine are documented in
`build_mall_whitelist.py`'s module docstring — read that before running it for a new wave.

## Tests

```bash
uv run pytest tests/       # full suite (35 tests)
uv run ruff check .        # lint
uv run mypy . --ignore-missing-imports   # type check
```

No test touches a real network or a real Supabase project — the multi-hour observation window
and every external fetch are dependency-injected in tests (same pattern as
`training/`'s `TrainDeps.load_sinpa`).

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`.
Coverage-expansion plan: `~/.gstack/projects/gstack-playground/ceo-plans/2026-07-07-carpark-coverage-expansion.md`.
