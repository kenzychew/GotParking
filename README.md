# GotParking

**Live at [gotparking.vercel.app](https://gotparking.vercel.app)**

GotParking forecasts Singapore carpark availability ("~12 lots free in 20 min") instead of just showing the current lot count, which is all that exists publicly today.
It is a free public product for drivers, built entirely on open government data, currently covering 268 carparks across the island.

The forecasting model is classical predictive ML: a LightGBM regressor pretrained on the SINPA research dataset, then fine-tuned weekly on live 2026 availability polls.
The first promoted model had to beat both a historical-average baseline and a persistence baseline by a wide margin; every retrain since is gated against the currently-serving model instead, promoting unless it regresses MAE by more than 2%.
The first promoted model went live on 2026-07-11 and every covered carpark now serves real ML forecasts; during the initial cold-start window the app served live counts with forecasts marked as warming up, never a fabricated number.

## Data sources

- [LTA DataMall](https://datamall.lta.gov.sg/) - live carpark availability, polled every 5 minutes.
- [data.gov.sg](https://data.gov.sg/) - carpark and mall metadata used for onboarding and matching.
- [SINPA](https://github.com/yoshall/SINPA) research dataset (Singapore Open Data Licence) - historical availability used to pretrain the first model so it is useful before months of live history accumulate.

## How it works

```
LTA DataMall
     |  poll every 5 min
     v
poller (Cloudflare Worker) ----> Supabase Postgres (ap-southeast-1)
     |                                ^          |
     |  trigger                       | write    | read (cached)
     v                                |          v
/api/batch_predict (Vercel, sin1) ----+     /api/forecast ----> frontend (PWA)

training (GitHub Actions, weekly) --> trains LightGBM on accumulated polls
                                      --> two-phase gate (baselines first time, incumbent after) --> promotes or holds
```

- A Cloudflare Worker polls LTA DataMall every 5 minutes, stores availability in Supabase, and triggers batch prediction.
- Batch prediction runs LightGBM inference for all carparks in one Vercel Python function pinned to Singapore (`sin1`) and writes forecasts back.
- `/api/forecast` and `/api/geocode_postal` are the two public endpoints: a cached read of the latest forecasts, and a server-side postal-code lookup that keeps OneMap credentials off the client. `/api/batch_predict` is secret-gated and only callable by the poller.
- A weekly GitHub Actions job retrains on the growing live history and only promotes models that clear the gate: baselines for the first promotion, the currently-serving incumbent for every retrain after.
- The poller, batch prediction, and the training job all report to healthchecks.io dead-man's-switch checks, so silent failure pages a human.

## Repository layout

- `db/` - Supabase schema and RLS policies.
- `poller/` - Cloudflare Worker: 5-minute LTA DataMall polling, batch-prediction trigger.
- `training/` - weekly GitHub Actions training job: SINPA-pretrained LightGBM, two-phase promotion gate (first promotion benchmarked against historical-average and persistence baselines, later retrains gated against the incumbent).
- `api/` - Vercel Python functions: poller-triggered batch forecasts, the public cached read endpoint, and the postal-code geocoding proxy.
- `frontend/` - mobile-first installable PWA with offline cache.
- `scripts/` - carpark coverage-expansion tooling: LTA-feed whitelist matching with a mandatory human sign-off gate, variance validation, and seed-list regeneration.
- `docs/` - decision records: the SINPA pretraining spike, the capacity load test, and the full provisioning trail.

## Development

Each lane has its own test suite (528 tests total across the five lanes as of 2026-07-17, all green, ruff and mypy clean on the Python lanes):

```bash
(cd poller && npx vitest run)
(cd api && uv run pytest -q)
(cd training && uv run pytest -q)
(cd frontend && npx vitest run)
(cd scripts && uv run pytest -q)
```

Python lanes use `uv` (Python 3.11+); JS/TS lanes use npm.
Deploys: Vercel auto-deploys `main` (frontend + api); the poller deploys via `wrangler deploy` from `poller/`; training runs on its own GitHub Actions schedule.

## Development process

This project is built with an agentic engineering workflow ([gstack](https://github.com/garrytan/gstack)) with a human making every decision that matters.
The tooling does not lower the bar, it raises it: every feature passed staged product, engineering, and design plan reviews before implementation, the coverage expansion went through a 3-iteration adversarial review, and all 49 test paths planned in the internal design doc are covered.
Shipped work is verified against production, not just CI: failure alerting was proven with a real forced-failure drill, and every lane's tests were re-run from a fresh checkout before merge.

## Project docs

- `CHANGELOG.md` - release history.
- `TODOS.md` - tracked follow-ups.
- `docs/provisioning-checklist.md` - the full infrastructure provisioning trail.
- `docs/t0-sinpa-spike.md` - why SINPA pretraining got a GO.
- `docs/t0-load-test-2026-07-08.md` - capacity headroom for scaling to the full LTA feed.
