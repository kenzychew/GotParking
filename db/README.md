# db

Supabase schema and RLS policies (T2 in the design doc's Implementation Tasks).

Tables: `carpark_history`, `carpark_baseline`, `model_config`, plus a Storage bucket for
model artifacts. RLS locked to service-role writes only — see the design doc's Security
section for the full rationale.

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
