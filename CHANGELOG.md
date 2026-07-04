# Changelog

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
