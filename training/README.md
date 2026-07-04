# training

Weekly GitHub Actions job (Python) that trains LightGBM on `carpark_history`, backtests
against the currently-serving model, and promotes via `model_config` only if it wins by
>=10% MAE (T5 in the design doc).

Features: time-of-day, day-of-week, public holiday, per-carpark historical pattern, plus
momentum features (available lots 15/30/60 min ago) so LightGBM has signal the baseline
structurally lacks.

Test framework: pytest. Excludes carparks below the cold-start data threshold (Premise #10).

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
