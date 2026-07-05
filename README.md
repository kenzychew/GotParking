# GotParking

Forecasts Singapore carpark availability ("~12 lots free in 20 min") instead of just showing
today's live lot-count, which is all that currently exists publicly. Classical predictive ML
(LightGBM) trained on LTA DataMall data, built to showcase ML engineering to GovTech/OGP.

Full design doc, dedupe research, architecture, and review findings:
`~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`

## Layout

- `db/` — Supabase schema and RLS policies (ap-southeast-1)
- `poller/` — Cloudflare Worker, polls LTA DataMall every 5 min, triggers batch predictions
- `training/` — weekly GitHub Actions job, trains and promotes LightGBM (SINPA-pretrained
  first candidate, two-phase gate, benchmarked against historical-average AND persistence)
- `api/` — Vercel Python (sin1): poller-triggered batch forecasts + the public cached read
  endpoint (the only public API surface)
- `frontend/` — mobile-first PWA

## Status

Design locked: approved through /office-hours, /plan-eng-review, /autoplan (CEO + Design +
Eng), and a 2026-07-04 max-effort eng re-review (11 findings, all resolved and folded in —
see the design doc's GSTACK REVIEW REPORT). T1 signal validation done (all 10 seed carparks
confirmed). T0 SINPA spike done: GO — the first LightGBM pretrains on the SINPA historical
dataset and fine-tunes on live 2026 data (`docs/t0-sinpa-spike.md`). T2 schema applied and
verified live. Provisioning: Phases 1-2 done; Phase 4 (Cloudflare) done — the poller is
**live** at `https://gotparking-poller.kenzychew.workers.dev`, cron confirmed running every
5 minutes, 4 of its 6 secrets wired (`HEALTHCHECKS_POLLER_PING_URL` and `BATCH_PREDICT_URL`
still pending Phases 3 and 5). Phase 5 (Vercel) is blocked on a real platform issue — see
below. Phase 3 (healthchecks.io) not started yet. See `docs/provisioning-checklist.md`.

**All four implementation lanes are done and merged.** T3 (poller): `poller/`, 38/38 tests
green. T4 (api): `api/`, 113/113 tests green, ruff + mypy clean. T6 (frontend): `frontend/`,
70/70 tests green, production build clean (installable PWA, offline cache, Public Sans
self-hosted). T5 (training): `training/`, 161/161 tests green, ruff + mypy clean —
SGT/holiday logic is cross-checked against `api/`'s copy in CI so the two can never silently
drift. Test Requirements coverage: **49/49 planned paths (100%)** — see the design doc.

Every lane was independently re-verified (tests re-run from a fresh `main` checkout, not
just the build worktree) before merging. Two real gaps were found this way and handled
openly rather than swept aside: batch predict's failure alerting currently reuses the
training job's healthchecks check (imprecise but not a functional bug — tracked in
TODOS.md); and a training bug where three early-exit cycles skipped without recording a
`training_runs` audit row was found and fixed directly, with regression tests, before merge.

**Vercel deploy is currently blocked.** `vercel --prod` fails with "No python entrypoint
found in default locations" despite `api/batch_predict.py` and `api/forecast.py` each
correctly defining their own `handler` class — the documented "each file becomes its own
function" convention doesn't seem to hold when a top-level `buildCommand` (for the frontend)
also exists in the same `vercel.json`. Three fixes tried and failed (excluding
`api/pyproject.toml`; an empty `functions` block; `functions` with `maxDuration` set).
Leading theory: this project's shape (frontend + independent Python functions in one
project) needs Vercel's newer `services` model instead of the plain `api/` convention —
untried as of this writing. See the design doc for full details once resolved.

Remaining before a live deploy: finish provisioning (Phase 3 healthchecks, Phase 5 Vercel —
blocked, see above), wire the 2 remaining poller secrets once their dependencies exist, then
the post-deploy verification checklist in the design doc's Observability section.
