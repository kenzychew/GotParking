# Changelog

## v0.1.0.0 - 2026-07-06

First complete, live version of the product. Search any of the 10 supported Singapore malls
and see its current lot count and forecast state at https://gstack-playground.vercel.app —
no login needed, works offline as an installable PWA.

- **All four implementation lanes shipped and merged**: the Cloudflare Worker poller (live,
  polling LTA DataMall every 5 minutes), the Vercel Python batch-predict + forecast-read API,
  the weekly GitHub Actions LightGBM training job (SINPA-pretrained, two-phase promotion
  gate), and the React PWA frontend. 382 tests passing across all four, all 49 planned
  test-requirement paths covered.
- **Fully provisioned across 5 platforms** — GitHub, Supabase (ap-southeast-1),
  healthchecks.io, Cloudflare, and Vercel — every secret wired, both dead-man's-switch checks
  live. See `docs/provisioning-checklist.md` for the full trail.
- **QA-verified end to end**: search, carpark selection, no-results state, dark mode, share,
  shortcuts, mobile layout — health score 98/100, zero bugs, via a full browser pass against
  the live deployment.
- Real ML forecasts aren't live yet — the app is honest about it, showing "Collecting data"
  per carpark until each clears the 72-hour/10-sample cold-start threshold, and the first
  weekly training run hasn't fired yet. That's the expected bootstrap window (design doc
  Approach A), not a bug. Expect `1.0.0.0` once a real model has actually promoted and served
  in production.

### For contributors

- Fixed a training bug where early-exit cycles could skip without recording a
  `training_runs` audit row (3 of 4 paths now covered by regression tests; the 4th is
  tracked in TODOS.md).
- Diagnosed and fixed a two-stage Vercel deploy blocker (an auto-detected `framework:
  python` preset forcing single-entrypoint mode, then a 225MB Python bundle cap) via a
  migration to Vercel's `services` model.

## v0.0.1.0 - 2026-07-04

Design-locked baseline (pre-implementation).

- Project scaffolding: db/, poller/, training/, api/, frontend/ per the design doc's
  worktree parallelization plan.
- T1 signal validation complete: scripts/poll_lta_carparks.py and
  scripts/analyze_variance.py. All 10 seed carparks confirmed forecast-worthy (range
  >= 20 lots over the full 3-hour window; 20-min delta analysis added 2026-07-04
  showing median 20-min movement of 4-41 lots with material tails).
- Design doc approved through /office-hours, /plan-eng-review, /autoplan (CEO +
  Design + Eng), and a 2026-07-04 max-effort eng re-review (11 findings, all
  resolved): batch-precompute serving, healthchecks.io dead-man's-switch alerting,
  SGT time-semantics contract, leakage-free dual-benchmark two-phase promotion gate,
  region pinning (Vercel sin1 / Supabase ap-southeast-1), idempotent poller writes,
  and the T1.5 human provisioning task.
