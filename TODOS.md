# TODOS

## Infrastructure

### Add a dedicated healthchecks.io check for batch-predict failures

**What:** Provision a third healthchecks.io check (e.g. `gotparking-batch-predict`, ~10 min
expected period matching the poll cycle) and wire a dedicated
`HEALTHCHECKS_BATCH_PING_URL` into `api/_lib/config.py` / `healthchecks.py`, replacing the
current reuse of `HEALTHCHECKS_TRAINING_PING_URL` for batch predict's `/fail` pings.

**Why:** T4 (Lane D) was wired only the training job's ping URL, but the design doc's batch
predict spec (T4, Failure Modes registry) requires a `/fail` ping on model-artifact failure
AND on Supabase read/write failure. The agent correctly flagged this as a scope gap and used
the training URL as a stopgap, tagging pings with a `reason` field to disambiguate. The real
problem: the training check has a weekly expected period + 24h grace (Premise #8/T1.5). A
single batch-predict failure pings that check's `/fail` endpoint, which then shows "down"
and alerts — and stays down until the NEXT weekly training success ping clears it (batch
predict never pings success on this URL), i.e. up to a week of a misleading "training is
down" alert state for what might have been one transient batch hiccup.

**Context:** This is an alerting-precision gap, not a functional bug — the actual
fallback-to-baseline behavior on artifact/Supabase failure is correct and tested
(`api/tests/test_batch_predict.py`). Fix is contained: one more healthchecks.io check
(manual, ~2 min) plus a new env var threaded through `config.py`/`healthchecks.py` and the
three platforms' secret stores (Cloudflare doesn't need it; Vercel does). See
`docs/provisioning-checklist.md` Phase 3 for the pattern used for the existing two checks.

**Effort:** S (~15 min: one dashboard check + a few lines of code + one secret to wire)
**Priority:** P2
**Depends on:** None — can be done any time before the batch-predict endpoint sees real
production failures.

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
