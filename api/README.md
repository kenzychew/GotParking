# api

Vercel Python serverless function serving carpark predictions (T4 in the design doc). The
only public-facing API surface — rate limited.

Responsibilities: check `model_config.active_model_version`, cache the loaded model in
warm-instance memory, cold-start fallback for carparks below the data threshold, explicit
error handling for missing/corrupt model artifacts and unknown carpark IDs (flagged as a
critical gap in the design doc's Failure Modes — must not fail silently).

Test framework: pytest.

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
