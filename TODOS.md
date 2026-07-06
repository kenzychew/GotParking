# TODOS

## Infrastructure

### Add a regression test for the 4th training early-exit path

**What:** `training/src/gotparking_training/train.py`'s `run()` has 4 early-exit paths that
each call `_skip_result` to record a `training_runs` audit row: no eligible carparks, no
labeled rows survive the join tolerance, no pre-holdout rows, and "empty holdout window
across all carparks" (~line 284). `training/tests/test_train.py`'s
`TestSkippedCyclesAlwaysRecordAnAuditRow` class only covers the first 3.

**Why:** Found by `/document-release`'s doc review (2026-07-06) auditing README.md's claim
of "three early-exit cycles... with regression tests" against the actual code -- the count
was off by one, and fixing the doc's wording surfaced that the 4th path's test genuinely
doesn't exist yet, not just that it wasn't mentioned.

**Context:** Same category of bug as the one fixed directly earlier in this project (all 4
early-exit paths DO correctly insert the audit row today -- this is purely a missing-test
gap, not a missing-behavior gap).

**Effort:** S (~15 min, following the pattern of the other 3 tests in the same class)
**Priority:** P3
**Depends on:** None

### Explicitly disable Preview URLs on the poller Worker

**What:** Add `preview_urls = false` to `poller/wrangler.toml` (top level, alongside the
existing `[triggers]`/`[observability]` blocks).

**Why:** Enabling the `workers.dev` route (required to make the poller reachable/deployable
at all) turned on Preview URLs by default, since `preview_urls` wasn't explicitly set —
`wrangler deploy` warned about this on the first successful deploy (2026-07-05).

**Context:** Low-stakes today: the poller has no `fetch()` handler (it's cron-only, per
Premise #9's workload-shape split), so there's nothing meaningful for a stray preview URL to
expose. Worth closing anyway for explicitness/defense-in-depth before this becomes a habit
across other Workers.

**Effort:** S (one config line)
**Priority:** P4
**Depends on:** None

### Batch-predict failures currently alert nobody (not just imprecisely — silently)

**What:** Provision a third healthchecks.io check (e.g. `gotparking-batch-predict`, ~10 min
expected period matching the poll cycle) and wire a dedicated
`HEALTHCHECKS_BATCH_PING_URL` into `api/_lib/config.py` / `healthchecks.py`. At minimum,
wire the existing `HEALTHCHECKS_TRAINING_PING_URL` value to Vercel as a stopgap — right now
it isn't wired there at all (see Context).

**Why:** T4 (Lane D) was wired only the training job's ping URL, but the design doc's batch
predict spec (T4, Failure Modes registry) requires a `/fail` ping on model-artifact failure
AND on Supabase read/write failure. The agent correctly flagged this as a scope gap and
intended the training URL as a stopgap, tagging pings with a `reason` field to disambiguate.

**Corrected 2026-07-06 (found by /document-release's doc review):** this was previously
described here as "imprecise reuse of the training check" — that's no longer accurate.
`api/_lib/config.py:90` reads `HEALTHCHECKS_TRAINING_PING_URL` from the environment, and
Vercel's actual wired env vars (`docs/provisioning-checklist.md` Phase 6c) are only
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `BATCH_SHARED_SECRET` — that ping URL was never
wired to Vercel at all. `healthchecks.py::fire_fail_ping` does exactly what its own docstring
promises (best-effort, skips silently and logs when the URL is unset) — so batch-predict
`/fail` pings are currently a guaranteed no-op in production, not a misdirected alert. The
"stays down for up to a week" scenario this entry used to describe can't happen today because
no ping fires at all.

**Context:** This is still an alerting gap, not a functional bug — the actual
fallback-to-baseline behavior on artifact/Supabase failure is correct and tested
(`api/tests/test_batch_predict.py`). Fix is contained: wire
`HEALTHCHECKS_TRAINING_PING_URL` to Vercel now (cheap, restores the originally-intended
imprecise-but-present alerting immediately) and/or provision the dedicated check later for
precision. See `docs/provisioning-checklist.md` Phase 3 for the pattern used for the
existing two checks.

**Effort:** S (~2 min for the stopgap wiring; ~15 min for the full dedicated-check fix)
**Priority:** P1 (raised from P2 — this is a live gap in production alerting, not a future
precision nice-to-have)
**Depends on:** None — the stopgap can be done immediately.

### Revisit connection-pool/thundering-herd behavior under real traffic

**What:** Load-test Supabase free-tier connection pool limits and Vercel cold-instance
Storage-read behavior under a traffic spike (many cold instances re-fetching the model
artifact simultaneously).

**Why:** Surfaced during `/autoplan` Eng review — Premise #6's capacity check only covers
write volume (poller), not read concurrency under a spike. Not a real risk at expected MVP
traffic (10 carparks, small user base), but worth checking before any traffic-driving event
(e.g., a GovTech demo going semi-viral).

**Context:** The model-caching design (Premise #9) already mitigates most of this — a warm
instance only re-fetches on version change, not per-request — but cold-start storms during a
traffic spike are a different failure mode.

**Effort:** S (a load test, not a code change, unless it fails)
**Priority:** P3
**Depends on:** MVP live and stable.

### Revisit rate-limiting approach for SG mobile/CGNAT traffic

**What:** The current plan uses IP-based rate limiting, which can false-positive-block
legitimate users behind carrier-grade NAT (common for SG mobile carriers) while barely
slowing a real abuser spoofing IPs.

**Why:** Surfaced during `/autoplan` Eng review. Not urgent for MVP scale/audience, but a
known weakness worth revisiting if abuse becomes real or the user base grows.

**Context:** Alternatives worth considering later: per-session tokens, request signing, or
Vercel's more sophisticated bot-detection tiers (paid).

**Effort:** S
**Priority:** P4
**Depends on:** Evidence of actual abuse, or MVP traffic growing enough to matter.

### Revisit Cloudflare D1 as a Supabase replacement

**What:** Re-evaluate consolidating storage onto Cloudflare D1 (same platform as the poller)
instead of Supabase, if vendor count/cost becomes a real concern.

**Why:** The outside-voice review during `/plan-eng-review` flagged 4-vendor complexity
(Cloudflare Workers, GitHub Actions, Vercel, Supabase) for a small-scale MVP. Resolved to keep
Supabase for now since its Storage is already required for the LightGBM model artifact — but
that reasoning could change if Supabase costs scale up or D1 matures further.

**Context:** Full reasoning is in the design doc's Security section (vendor-choice note),
`~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`. Not urgent —
purely opportunistic if the tradeoff shifts.

**Effort:** S
**Priority:** P4
**Depends on:** None

### Add NEA rainfall as a model feature

**What:** Add NEA rainfall data as a LightGBM feature once the baseline-vs-LightGBM comparison
is running and stable.

**Why:** Deferred from MVP to avoid an extra external dependency before the core forecasting
pipeline is proven. Rain plausibly affects carpark demand (people drive instead of walk/transit)
so it's a real future accuracy lever — NEA's rainfall API is also free.

**Context:** Full rationale is in the design doc's Constraints and Premise #2. Unclear how much
it actually moves prediction accuracy until measured against the LightGBM pipeline once it's
live.

**Effort:** M
**Priority:** P3
**Depends on:** T5 (weekly training job) from the design doc's Implementation Tasks, being live
and measurable first.

## Product

### Push notifications for favorited carpark availability

**What:** Notify users when a favorited carpark is predicted to fill up soon ("leave now or
your usual carpark fills in 10 min").

**Why:** Surfaced during `/autoplan` CEO review's expansion scan as a genuine 10x delight
opportunity, but requires new infrastructure (push service, permission UX) outside the
current MVP's blast radius.

**Context:** Depends on the core forecasting pipeline being live and trustworthy first. Needs
its own design pass (permission prompt UX, notification timing/frequency to avoid being
annoying).

**Effort:** M
**Priority:** P3
**Depends on:** T4/T5 (inference + training) live and validated.

### Public API endpoint (data-as-a-product)

**What:** Expose carpark predictions via a public API for other SG civic-tech builders to
consume.

**Why:** office-hours identified the core insight as "productization gap, not technical gap"
— exposing this as infrastructure other builders can use extends that thesis. Surfaced during
CEO review's expansion scan.

**Context:** Needs its own API contract, versioning, docs, and auth/rate-limiting story
distinct from the PWA's internal use of the inference function — meaningfully more scope than
the current MVP.

**Effort:** L
**Priority:** P3
**Depends on:** Core pipeline live and stable.

### Expand to all/hundreds of SG carparks

**What:** Grow the seed list from the initial 10 validated carparks to most/all public SG
carparks.

**Why:** Natural next phase once the 10-carpark MVP proves the pipeline works end-to-end.
Surfaced during CEO review's expansion scan.

**Context:** Each new carpark needs its own signal-strength validation (repeat of T1) before
being added — not just a config change.

**Effort:** M-L (per batch of carparks)
**Priority:** P3
**Depends on:** MVP launched and stable.

### Multi-modal trip advice (carpark + bus/MRT combined forecast)

**What:** Combine carpark availability forecasts with bus/MRT crowding forecasts into unified
trip advice.

**Why:** Surfaced during CEO review's expansion scan as a 10x idea, but re-opens a decision
office-hours already made (bus/MRT crowding was rejected as the primary product due to the
LTA "load" field being a coarse 3-level category, not a continuous signal).

**Context:** Would require solving the bus/MRT data-quality problem first — a real, separate
R&D effort, not a quick add-on.

**Effort:** L
**Priority:** P4
**Depends on:** A resolution to the bus/MRT crowding data-quality gap (currently unsolved).

### Generalize into a reusable "SG Civic Forecast" platform

**What:** Extract the poller/training/inference architecture pattern into a reusable
template for forecasting other SG civic time-series (bus crowding, taxi availability, etc.),
not just carpark availability.

**Why:** Surfaced during CEO review's expansion scan (Section 10: platform potential) as a
genuinely exciting long-term strategic direction — the architecture is already
workload-shape-generic, not carpark-specific.

**Context:** This is a multi-quarter idea, not a near-term task. Worth remembering and
revisiting once the carpark MVP has proven the pattern works in production.

**Effort:** XL
**Priority:** P4
**Depends on:** Carpark MVP live, stable, and validated as a template worth generalizing.

## Completed
