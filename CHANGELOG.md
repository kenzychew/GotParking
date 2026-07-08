# Changelog

## v0.2.0.0 - 2026-07-08

Coverage grows from 10 to 24 Singapore malls — search any of them at
https://gstack-playground.vercel.app and see its current lot count, same as before, now for
more than double the malls.

- **14 new malls verified and live**: Millenia Singapore, Orchard Point, The Heeren, Plaza
  Singapura, The Cathay, Wisma Atria, Harbourfront Centre, Far East Plaza, ION Orchard, Orchard
  Central, Westgate, IMM Building, Tampines Mall, Bedok Mall. Found via a new whitelist-matching
  pipeline (LTA feed × data.gov.sg's mall dataset), validated the same way the original 10 were
  (a real multi-hour variance check, not just a name match) — 3 candidates correctly rejected
  for having too-stable lot counts to forecast usefully (Bt Panjang Plaza, Singapore Flyer,
  Concorde Hotel), and 1 caught and excluded as a genuine false match before it ever reached
  production (a fuzzy name-match scored high enough to pass automatically, but pointed at the
  wrong physical carpark — a human sign-off step catches exactly this, and did).
- **New carparks can't dilute the original 10's first real forecast.** The training pipeline
  pools every carpark into one shared quality check before it ever promotes a model — without a
  guard, a wave of newly-added, still-noisy carparks could drag down that first real check for
  everyone. A new one-time gate keeps new carparks out of that pool until the original 10 have
  already cleared it once.
- **A capacity question got answered with data, not a guess.** Before considering any larger
  expansion, we measured what it would actually cost to serve real ML predictions (not just
  today's placeholder state) at various carpark counts — turns out there's comfortable headroom
  even at 20x today's scale. Full report: `docs/t0-load-test-2026-07-08.md`.
- A much larger expansion (the rest of Singapore's ~500 tracked carparks, mostly HDB estates) is
  planned but deliberately paused — coverage isn't this project's real differentiator yet;
  forecast accuracy is, and that hasn't been proven in production once. See TODOS.md.

### For contributors

- Fixed a real N+1 query pattern in batch-predict's history-stats lookup (3 sequential
  Supabase requests per carpark → 1 batched request total) — the fix that makes the capacity
  headroom above possible; unfixed, it would have been the actual bottleneck at scale, not
  model inference.
- Three new production scripts, each with full test coverage: `recon_mall_whitelist.py` (Phase
  0 capacity recon), `build_mall_whitelist.py` (the full match → human-sign-off → observe →
  verify state machine — the human sign-off gate is mandatory on every run, no bypass flag,
  because fuzzy-matching and variance validation turned out not to be orthogonal checks), and
  `regen_seed_lists.py` (regenerates the poller's and frontend's carpark lists from verified
  output, preserving every existing comment/helper function byte-for-byte).
- Two `/autoplan` review cycles (CEO + Eng, each with an independent second-opinion pass) ran
  against this work before and during implementation — one found and fixed a load-test design
  flaw before it produced a misleading capacity number; the other found the project was about
  to scale the wrong variable (carpark count instead of forecast accuracy) and capped scope
  accordingly. Full artifacts linked from TODOS.md.

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
- Fixed batch-predict's failure alerting: `HEALTHCHECKS_TRAINING_PING_URL` was never wired
  to Vercel, so `/fail` pings were a guaranteed no-op in production. Wired it to all 3
  environments and verified end-to-end via a real forced-failure drill against the live
  healthchecks.io check.

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
