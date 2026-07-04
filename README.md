# GotParking

Forecasts Singapore carpark availability ("~12 lots free in 20 min") instead of just showing
today's live lot-count, which is all that currently exists publicly. Classical predictive ML
(LightGBM) trained on LTA DataMall data, built to showcase ML engineering to GovTech/OGP.

Full design doc, dedupe research, architecture, and review findings:
`~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`

## Layout

- `db/` — Supabase schema and RLS policies (ap-southeast-1)
- `poller/` — Cloudflare Worker, polls LTA DataMall every 5 min, triggers batch predictions
- `training/` — weekly GitHub Actions job, trains and promotes LightGBM (two-phase gate,
  benchmarked against historical-average AND persistence)
- `api/` — Vercel Python (sin1): poller-triggered batch forecasts + the public cached read
  endpoint (the only public API surface)
- `frontend/` — mobile-first PWA

## Status

Design locked: approved through /office-hours, /plan-eng-review, /autoplan (CEO + Design +
Eng), and a 2026-07-04 max-effort eng re-review (11 findings, all resolved and folded in —
see the design doc's GSTACK REVIEW REPORT). T1 signal validation is done (all 10 seed
carparks confirmed). Next: T1.5 human provisioning checklist, then T0 SINPA spike / T2
schema — see the design doc's Implementation Tasks.
