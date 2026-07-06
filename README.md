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
verified live.

**Provisioning (T1.5) is fully complete** — every phase, every platform, every secret. The
poller is **live** at `https://gotparking-poller.kenzychew.workers.dev` (cron confirmed every
5 minutes, all 6 secrets wired) and the site is **live** at
`https://gstack-playground.vercel.app` (independently re-verified: `200` on `/`, a correct
typed `503` on `/api/forecast`, `X-Vercel-Id: sin1::` confirming the region pin). Both
healthchecks.io dead-man's-switch checks exist — `gotparking-poller` is already `up`
(receiving real pings); `gotparking-training` stays intentionally paused until
`.github/workflows/train.yml` fires on its own weekly schedule and sends its first real ping.
GitHub Actions and Vercel each have their full set of secrets/env vars wired. See
`docs/provisioning-checklist.md` for the full trail, including two dashboard-vs-reality
corrections found along the way (Cloudflare's workers.dev toggle location; Vercel's actual
deploy blocker, below).

**All four implementation lanes are done and merged.** T3 (poller): `poller/`, 38/38 tests
green. T4 (api): `api/`, 113/113 tests green, ruff + mypy clean. T6 (frontend): `frontend/`,
70/70 tests green, production build clean (installable PWA, offline cache, Public Sans
self-hosted). T5 (training): `training/`, 161/161 tests green, ruff + mypy clean —
SGT/holiday logic is cross-checked against `api/`'s copy by a dedicated test
(`training/tests/test_sg_time.py` loads `api/_lib/sg_time.py` directly and diffs both across
a dense grid of instants), run manually via `uv run pytest` — `.github/workflows/train.yml`
only runs the production training job itself, it does not run any lane's test suite. Test
Requirements coverage: **49/49 planned paths (100%)** — see the design doc.

Every lane was independently re-verified (tests re-run from a fresh `main` checkout, not
just the build worktree) before merging. Two real gaps were found this way and handled
openly rather than swept aside: batch predict's failure alerting had no ping URL wired in
production at all (found 2026-07-06; fixed same day — `HEALTHCHECKS_TRAINING_PING_URL` wired
to Vercel and live-verified end-to-end via a real forced-failure drill, see TODOS.md); and a
training bug where early-exit cycles skipped without recording a `training_runs` audit row
was found and fixed directly, with regression tests covering 3 of the 4 early-exit paths,
before merge.

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

**Nothing is deferred anymore.** The only thing left before the app shows real forecasts
instead of its honest "predictions temporarily unavailable" 503 is data: the poller has been
live only since 2026-07-05/06, so T5's training job (weekly, next fires on its own schedule)
hasn't had ~2-3 weeks of history to train against yet — exactly the bootstrap window the
design doc's Approach A always expected. The post-deploy verification checklist in the
design doc's Observability section is the next thing worth running once that data exists.

**QA: 2026-07-06, health score 98/100, zero bugs.** A full `/qa` browser pass against the
live production site — search, carpark selection, no-results state, dark mode, Share, the
shortcuts quick-access chip, mobile viewport — everything worked correctly, console clean
throughout. Full report: `.gstack/qa-reports/qa-report-gstack-playground-vercel-app-2026-07-06.md`.

See also: `CHANGELOG.md` for release history, `TODOS.md` for tracked follow-ups (all
low/medium priority, none blocking).
