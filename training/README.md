# training

Weekly GitHub Actions job (Python) that trains a LightGBM carpark-availability forecaster,
backtests it leakage-free against two comparators, and promotes it via `model_config` only if
it clears the two-phase gate (T5 in the design doc). Workflow file:
`.github/workflows/train.yml` (cron `0 21 * * 6` == Sun 05:00 SGT, plus `workflow_dispatch`).

Entry point: `python -m gotparking_training` (see `pyproject.toml`'s `packages =
["src/gotparking_training"]` -- this is a real installable package, not a loose script).

## Pipeline (`train.py::run`)

1. Load the active-carpark whitelist and all of `carpark_history` (paginated), excluding
   carparks below the cold-start threshold (Premise #10: first sample <72h old, or fewer than
   10 live samples -- SINPA rows never count toward this).
2. Build momentum/label rows: for each reading, join the 15/30/60-min-ago momentum offsets and
   the t+20min label via nearest-within-+/-2.5min lookups, dropping the row if any join misses
   (a poll gap must never fabricate a value).
3. SINPA pretraining (best-effort, D13): downloads HuggingFace `Huaiwu/SINPA`'s val/test
   splits (never `train.npz`, to fit GitHub runners' ~7GB RAM), de-windows the 8 mapped seed
   carparks into continuous series, and builds rows via the SAME join logic as live data. ANY
   failure (download/parse/shape/mapping) degrades to live-only training with a log line --
   never crashes the run.
4. Trains the candidate strictly on pre-holdout data: pretrains on SINPA then fine-tunes on
   live rows via `init_model` chaining when SINPA is available, live-only otherwise.
5. Gate (leakage-free): holdout = the most recent 3 days of live data. Comparators
   (historical-average by carpark/dow/slot, and persistence = lots_now) are recomputed from
   pre-holdout rows only -- this job never reads the live `carpark_baseline` table. First
   promotion requires beating BOTH comparators by >=10% MAE; later retrains promote unless
   worse than the incumbent by >2% MAE.
6. On promotion: uploads the LightGBM text-format artifact to Storage bucket `models` at
   exactly `{version}.txt`, retrying once, then flips `model_config.active_model_version` to
   the bare version string (`lgbm_YYYYMMDD_HHMMSS`, UTC). An upload failure aborts the
   promotion and fires a `/fail` ping -- never a silent skip.
7. Always inserts one `training_runs` audit row (candidate version, phase, all four MAEs,
   `used_sinpa`, `promoted`, notes), win or lose.
8. Pings healthchecks.io on success; `/fail` (with a reason) on crash, config error, or upload
   failure.

## Module layout (`src/gotparking_training/`)

- `sg_time.py` -- **CRITICAL INTEGRATION CONTRACT**: a deliberate byte-for-byte copy of
  `api/_lib/sg_time.py` (the serving side's SGT/holiday helper), with a "must stay in sync"
  header. `tests/test_sg_time.py` additionally cross-checks both copies agree bit-for-bit by
  loading api's module directly from disk across a dense grid of instants and every holiday +/-
  1 day -- not just a one-off pinned case.
- `features.py` -- the same cross-check treatment for the 7-column feature vector contract
  (`[dow, slot_of_day, is_holiday, lots_now, lots_15m_ago, lots_30m_ago, lots_60m_ago]`).
- `config.py` -- tunable constants; cold-start/storage-bucket/horizon constants are
  cross-checked against `api/_lib/config.py` too.
- `supabase_rest.py` -- httpx-based PostgREST + Storage client (no supabase-py), mirroring
  `api/_lib/supabase_rest.py`'s retry-once policy, plus the operations api doesn't need:
  `insert` (training_runs), `update`/PATCH (model_config), `upload_storage_object`.
- `healthchecks.py` -- success (bare GET) and `/fail` (POST + reason) pings.
- `cold_start.py` -- the Premise #10 exclusion rule, cross-checked against
  `api/_lib/batch_logic.py`'s private `_is_cold_start`.
- `series.py` -- the momentum/label nearest-within-tolerance join, generic over any
  chronological series so live history and de-windowed SINPA data share identical semantics.
- `repository.py` / `data_loading.py` -- Supabase access for carparks/model_config/
  training_runs, and paginated `carpark_history` loading + cold-start filtering.
- `comparators.py` -- the leakage-free historical-average and persistence benchmarks (pure
  functions over in-memory pre-holdout rows; no Supabase dependency at all).
- `gate.py` -- the two-phase promotion decision (`first_promotion` vs `retrain`).
- `modeling.py` -- LightGBM train/predict/MAE helpers, including the pretrain-then-fine-tune
  `init_model` chain.
- `model_io.py` -- version formatting, artifact upload, and incumbent download (the exact
  bucket/path/`Booster(model_str=...)` contract `api/_lib/batch_logic.py` reads from).
- `sinpa.py` -- the SINPA loader: memory-pragmatic slicing (only ever holds the ~8 needed
  carparks' `(N, 12)` arrays in memory, never the full `(N, 12, 1687, 12)` tensor), de-windowing
  of the overlapping samples, and a widened join tolerance for SINPA's 15-minute grid (see the
  module docstring -- the live 2.5-minute tolerance would silently drop every SINPA row's
  label, since 20 minutes is not a whole multiple of 15).
- `train.py` -- orchestrates the whole pipeline (`run()`) and the process entry point
  (`main()`), including exception routing between an already-alerted `TrainingJobError` and an
  unexpected crash.

## Tests

Pytest, fully offline and deterministic: every Supabase call goes through an in-memory
`FakeSupabaseDB` (`tests/fakes.py`) or, for the HTTP client itself, `httpx.MockTransport`; the
SINPA loader's HuggingFace download is dependency-injected in tests. No test touches a real
network or a real Supabase/HuggingFace project.

```bash
uv run pytest                              # full suite
uv run ruff check .                        # lint
uv run mypy . --ignore-missing-imports     # type check
```

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`.
SINPA feasibility spike: `docs/t0-sinpa-spike.md`.
