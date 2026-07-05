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
5 minutes, 4 of its 6 secrets wired. Phase 5 (Vercel) done — the site is **live** at
`https://gstack-playground.vercel.app` (see below for what the blocker turned out to be).
`HEALTHCHECKS_POLLER_PING_URL` still pends Phase 3 (healthchecks.io, not started);
`BATCH_PREDICT_URL` is now known (`https://gstack-playground.vercel.app/api/batch_predict`)
but not yet set as a Worker secret. See `docs/provisioning-checklist.md`.

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

**Vercel deploy: fixed 2026-07-05.** The "No python entrypoint found in default locations"
error was NOT caused by the repo's file layout — the linked Vercel project had Framework
Preset `python` (auto-detected at import time from the root `requirements.txt`), and under
that preset Vercel treats the whole repo as ONE Python app needing a single entrypoint; the
per-file `api/` convention is never consulted, which is why `.vercelignore` and `functions`
tweaks changed nothing. `"framework": null` in `vercel.json` cleared that error but then hit
a hard 225 MB Python bundle cap (both functions bundle all of `lightgbm`+`scipy`+`numpy` from
one shared `requirements.txt`, ~228 MB even after Vercel's own optimization). The working fix
is Vercel's `services` model: three services (`frontend`; `batch_predict` and `forecast` each
rooted at `api/`, installing from `api/requirements.txt`, each with its own per-service
bundle) plus top-level rewrites preserving the original `/api/batch_predict` and
`/api/forecast` paths. Two implementation gotchas, both confirmed against the
`@vercel/python` builder source: service entrypoints must be file-form (`"forecast.py"`) —
the `module:variable` form triggers a validator that rejects `handler` *classes* (it only
accepts functions, though detection itself understands classes) — and `lightgbm` needs a
one-line service `buildCommand` copying `libgomp.so.1` into `lib/`, because the wheel doesn't
bundle it and the function runtime image lacks it (`/var/task/lib` is on the runtime's
library path). `regions: ["sin1"]` stays top-level and held: live responses show
`X-Vercel-Id: sin1::`.

Remaining before full go-live: Phase 3 (healthchecks.io), wire the 2 remaining poller
secrets (`HEALTHCHECKS_POLLER_PING_URL` after Phase 3; `BATCH_PREDICT_URL`, value now
known), set the 3 Vercel env vars (Phase 6c — until then `/api/forecast` returns its typed
503), then the post-deploy verification checklist in the design doc's Observability section.
