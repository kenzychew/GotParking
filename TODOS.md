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

### Batch-predict alerting reuses the training check (imprecise, but now present)

**What:** Provision a dedicated healthchecks.io check (e.g. `gotparking-batch-predict`, ~10
min expected period matching the poll cycle) and wire a separate `HEALTHCHECKS_BATCH_PING_URL`
into `api/_lib/config.py` / `healthchecks.py`, so batch-predict failures stop sharing the
training job's check.

**Why:** T4 (Lane D) was wired only the training job's ping URL, but the design doc's batch
predict spec (T4, Failure Modes registry) requires a `/fail` ping on model-artifact failure
AND on Supabase read/write failure. The agent correctly flagged this as a scope gap and
intended the training URL as a stopgap, tagging pings with a `reason` field to disambiguate.

**Status 2026-07-06:** the stopgap is now DONE and live-verified. Until today,
`HEALTHCHECKS_TRAINING_PING_URL` had never actually been wired to Vercel (only to GitHub
Actions, for the training job itself), so `healthchecks.py::fire_fail_ping`'s own
best-effort/skip-when-unset behavior meant batch-predict `/fail` pings were a guaranteed
no-op in production -- not just imprecise, genuinely silent. Fixed: wired
`HEALTHCHECKS_TRAINING_PING_URL` to all 3 Vercel environments and redeployed. Verified
end-to-end for real (not just by reading the code): called the actual unmodified
`fire_fail_ping` against the real URL, confirmed via the healthchecks.io API that
`gotparking-training` flipped to `status=down` with a matching timestamp, then sent a real
success ping to clear it and re-paused the check (the training job itself still hasn't run
for real yet, so leaving it active on the strength of one drill ping would risk a false
alarm before its first real weekly run). The remaining gap is now back to its original,
smaller shape: batch-predict failures alert correctly, just to the shared training check
(imprecise "training is down" framing) rather than a dedicated one.

**Context:** Not a functional bug — the actual fallback-to-baseline behavior on
artifact/Supabase failure is correct and tested (`api/tests/test_batch_predict.py`). This
remaining item is purely about alert precision now. See `docs/provisioning-checklist.md`
Phase 3 for the pattern used for the existing two checks.

**Effort:** S (~15 min: one dashboard check + a few lines of code + one secret to wire)
**Priority:** P2 (lowered from P1 now that the silent-no-op gap is closed)
**Depends on:** None

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
traffic spike are a different failure mode. **Cross-reference (2026-07-08):** the full-feed
carpark-expansion plan's write-volume growth (18x at full ~500 scale, 2-4x even at its capped
first wave of 50-100) is the first real trigger for this item's "10 carparks" scoping, not a
hypothetical future one.

**Effort:** S (a load test, not a code change, unless it fails)
**Priority:** P3
**Depends on:** MVP live and stable.

### `carpark_history` retention/archival policy

**What:** No TTL, partitioning, or cleanup trigger exists on `carpark_history` — at 5-minute
polling it grows unbounded (288 rows/carpark/day). Needs a retention policy (archival job,
partitioning, or a hard TTL) before write volume becomes a real storage/capacity problem.

**Why:** Surfaced during the full-feed carpark-expansion `/autoplan` review's Grounding section
— confirmed via `db/schema.sql` that no retention mechanism exists at all today. At the current
~28 carparks this is ~242K rows/month; even the expansion plan's capped first wave (50-100
carparks) is 2-4x that growth rate, and the deferred remaining ~400 would be ~18x.

**Context:** Deferred at both the mall-wave and full-feed plans' scope, but the growth-rate math
means this should not stay deferred indefinitely — see the trigger condition below.

**Effort:** M (a scheduled archival job or partitioning strategy, not a one-line fix)
**Priority:** P3
**Trigger condition:** revisit once the carpark-expansion plan's first wave (50-100) starts
accumulating real volume, or immediately if Supabase free-tier storage warnings appear.
**Depends on:** None directly.

### Supabase usage dashboard/alert

**What:** No visibility into Supabase free-tier usage (row counts, storage, connection pool)
anywhere in this project's tooling — limits are undocumented and unmonitored.

**Why:** Surfaced during the full-feed carpark-expansion `/autoplan` review. Cheap observability
that would surface capacity problems before they become production incidents, rather than after.

**Context:** Not blocking any current work — the expansion plan's T0 load test and capped scope
already de-risk the immediate capacity question; this is a longer-term observability gap.

**Effort:** S
**Priority:** P3
**Depends on:** None.

### Categorized/filterable frontend browse (beyond search)

**What:** Add a browse-by-area/category view to the frontend, not just search-by-name — useful
once carpark count grows enough that "browse all supported malls" becomes a real use case.

**Why:** Surfaced during the full-feed carpark-expansion `/autoplan` review's CEO cherry-pick
scan. Search-with-a-cap (already in that plan's baseline scope) is sufficient for launch; this
is a genuine enhancement once more carparks exist.

**Effort:** M
**Priority:** P3
**Depends on:** More carparks onboarded (the expansion plan's first wave or later).

### Fully generated seed-list pipeline (replace hand-maintained static files)

**Status 2026-07-08:** both the TS-seed-list gap AND the schema-reproducibility gap are now
CLOSED. `SEED_CARPARKS`/`SEED_CARPARK_NAMES` are no longer hand-edited —
`scripts/regen_seed_lists.py` has been run for real against the mall wave's 14 verified
candidates. `db/schema.sql` now also has a second seed INSERT block for the 14 (`is_original_seed
=false`, `sinpa_index=null`), matching exactly what was applied to production — a fresh apply
now correctly reproduces all 24 rows, not just the original 10. What remains is the BIGGER
version below: removing hand-maintenance as a *concept* everywhere (both the TS files and
`schema.sql` are still manually-updated-per-wave, not auto-synced from `carparks` at apply time
or CI time), not just these two specific file-pairs having caught up for wave 1.

**What:** `carparks` (the DB table) is the intended single source of truth, but nothing
auto-syncs `schema.sql`'s seed INSERTs or the TS seed-list files FROM it — each coverage
wave requires a human/script to update both by hand (even though the update mechanism itself,
`regen_seed_lists.py`, is now real and tested). A fully generated pipeline would remove the
"remember to update N places" step entirely, not just make updating them fast.

**Why:** Surfaced during the full-feed carpark-expansion `/autoplan` review — the Eng subagent
flagged this as "a recurring unpaid debt this plan is about to compound a second time," having
already gone unpaid once in the mall wave (before Approach C landed). The schema-reproducibility
half of the gap was found independently during a post-ship `/document-release` audit, and closed
the same day.

**Context:** Both wave-1-specific gaps are done. What's left is generalizing so wave 2 (and
every wave after) doesn't require remembering to touch `schema.sql` by hand again.

**Effort:** L
**Priority:** P3
**Depends on:** The carpark-expansion plan's first wave proving the direct-onboard + regen
pattern works end-to-end.

### Remaining ~400 LTA feed carparks (held pending accuracy validation)

**Status 2026-07-08:** T0 (the load test gating this wave's capacity decision) is DONE — see
`docs/t0-load-test-2026-07-08.md`. Result: comfortable headroom (LightGBM inference cost is
negligible; the dominant cost, one Supabase read, stays flat regardless of carpark count thanks
to the N+1 fix). Capacity is no longer the blocker for the 50-100 first sub-wave — the
accuracy-validation gate (below) still is, unchanged. No carparks from this wave have been
onboarded yet; only T0 has run.

**What:** The full-feed carpark-expansion plan was capped at CEO review to a first sub-wave of
50-100 carparks. The remaining ~400 (the rest of the LTA `CarParkAvailabilityv2` feed, mostly
HDB) are explicitly deferred, not rejected.

**Why:** An independent CEO review flagged that scaling carpark COUNT before the model has
produced a single validated forecast (all 24 carparks are still `cold_start`) optimizes a
variable a technical evaluator (GovTech/OGP, per this project's own stated purpose) doesn't
actually judge — coverage breadth isn't the differentiator, forecast accuracy is. The user
agreed and capped scope rather than proceeding with the full feed or pausing entirely.

**Context:** Full reasoning in
`~/.gstack/projects/gstack-playground/kenzy-main-plan-20260707-231353.md`'s CEO Dual-Voice
Review section and Strategic Gate resolution.

**Trigger condition (not just "someday"):** revisit once the existing 24 carparks (10 original
seed + 14 verified mall candidates) have produced real, benchmarked accuracy numbers (forecast
vs. actual, vs. historical-average and persistence baselines) from a completed first training
promotion.

**Effort:** L (per remaining batch, same pattern as the first wave)
**Priority:** P3
**Depends on:** First training promotion happening and producing measurable accuracy results.

### Revisit rate-limiting approach for SG mobile/CGNAT traffic

**Status 2026-07-08 (corrected phrasing):** the original wording here ("the current plan uses
IP-based rate limiting") described the design doc's intent, not anything actually implemented —
`grep` across `api/`, `poller/`, `frontend/`, `vercel.json` finds zero rate-limiting/throttle
implementation today. Corrected so this entry doesn't read as a shipped mitigation.

**What:** No rate limiting is implemented today. If/when it is, IP-based limiting can
false-positive-block legitimate users behind carrier-grade NAT (common for SG mobile carriers)
while barely slowing a real abuser spoofing IPs — worth designing around from the start rather
than retrofitting.

**Why:** Surfaced during `/autoplan` Eng review. Not urgent for MVP scale/audience, but a
known weakness worth revisiting if abuse becomes real or the user base grows.

**Context:** Alternatives worth considering later: per-session tokens, request signing, or
Vercel's more sophisticated bot-detection tiers (paid). **Cross-reference (2026-07-08):** the
full-feed carpark-expansion plan (`~/.gstack/projects/gstack-playground/kenzy-main-plan-20260707-231353.md`)
is the first real trigger for revisiting this item's "10 carparks, small user base" scoping —
even its capped first wave (50-100 carparks) meaningfully changes write volume and public-read
traffic patterns versus the assumption this item was originally scoped against.

**Effort:** S
**Priority:** P4
**Depends on:** Evidence of actual abuse, or MVP traffic growing enough to matter -- or the
carpark-expansion plan's first wave shipping, whichever comes first.

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

**Status 2026-07-08: mall wave (first wave) DONE and live.** Full plan + 3-iteration adversarial
spec review + Eng dual-voice review:
`~/.gstack/projects/gstack-playground/ceo-plans/2026-07-07-carpark-coverage-expansion.md` and
`~/.gstack/projects/gstack-playground/kenzy-main-plan-20260707-133103.md`.

**Final outcome (all 18 mall candidates resolved):**
- **14 verified and LIVE**: Millenia Singapore, Orchard Point, The Heeren, Plaza Singapura, The
  Cathay, Wisma Atria, Harbourfront Centre, Far East Plaza, ION Orchard, Orchard Central,
  Westgate, IMM Building, Tampines Mall, Bedok Mall — inserted into `carparks`
  (`is_original_seed=false`), seed lists regenerated (`scripts/regen_seed_lists.py`), poller
  and frontend redeployed. Confirmed live: `/api/forecast` returns all 24 carparks.
- **3 rejected** (insufficient lot-count variance, correctly caught by the T1 gate): Bt Panjang
  Plaza (range 0, flat the entire 6-hour window), Singapore Flyer (range 7), Concorde Hotel
  (range 13, resolved as a genuine dual-listing in the source dataset, not two carparks — signed
  off, then rejected by variance, not by the disambiguation question).
- **1 excluded, never advanced**: Junction 8 (carpark_id 64) — a confirmed live false positive
  (fuzzy-matched "Junction 10" at 85.7%, two different, unrelated malls, above the 85 threshold,
  with no second candidate close enough to trip ambiguity detection). Concrete, non-hypothetical
  proof the human sign-off gate (not just variance validation) is load-bearing — fuzzy-match and
  variance validation are NOT orthogonal checks; neither alone nor combined catches a
  confidently-wrong-building match.

**Architecture decision, made with real data:** Approach C (CI-generated static list) over
Approach B (DB-driven poller) — the real recon count (18) was too small to justify a new
poller-side runtime Supabase dependency. The poller stays a pure static-config Cloudflare
Worker; `scripts/regen_seed_lists.py` regenerates `poller/src/carparks.ts` and
`frontend/src/seed/seedCarparks.ts` from `data/carpark_coverage_map.json`, preserving every
existing comment/type/helper function byte-for-byte (only the data literal changes).

**Training-eligibility gate is live and verified end-to-end**: `carparks.is_original_seed` /
`model_config.first_promotion_at` (`db/schema.sql`) ship real production values — all 10
original seeds `true`, all 14 new carparks `false` (confirmed via a manual training-job trigger,
`gh workflow run train.yml`, whose logs show both new columns read correctly against production
schema with zero errors). The gate protects the original 10's first-ever promotion from being
diluted by the new carparks' noise; it will open once, system-wide, the moment that promotion
happens.

**What:** Grow the seed list from the initial 10 validated carparks to every mall carpark
visible in the LTA feed (bounded by LTA's own feed coverage, not literally "all SG malls" —
private-operator carparks that don't report to HDB/LTA/URA won't appear regardless).

**Why:** Natural next phase once the 10-carpark MVP proves the pipeline works end-to-end.
Surfaced during CEO review's expansion scan; now fully planned per above.

**Context:** Each new carpark needs its own signal-strength validation (repeat of T1) before
being added — not just a config change. The fully-planned version also found and closed a real
gap: the training promotion gate pools MAE across ALL carparks globally, so new carparks
clearing cold-start could dilute the original 10's model quality without an explicit gate —
see the "training-eligibility maturity gate" TODO below for the residual (intentionally
out-of-scope-for-now) risk this doesn't fully close.

**Effort:** M-L (per batch of carparks) — **mall wave (18 candidates) DONE, ~5 hours end to
end** including 2 full review cycles, implementation, and two 6-hour observation windows.
**Priority:** P3 — mall wave complete; remaining scope now lives entirely in "Remaining ~400 LTA
feed carparks" below (separately tracked, held pending accuracy validation).
**Depends on:** MVP launched and stable (was already true). Mall wave's own dependency (T1.5
provisioning) is satisfied; DONE.

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

### Recurring per-carpark training-eligibility/maturity gate

**What:** `model_config.first_promotion_at` (planned as part of the carpark coverage expansion
above) only protects the ORIGINAL 10 carparks' first-ever promotion — once it's set, every
subsequently-verified new carpark is immediately eligible for pooled training with zero
onboarding/seasoning period of its own. This item is a per-onboarding-wave (not just
one-time-system-wide) eligibility gate, or per-carpark MAE visibility in `training_runs`.

**Why:** The training promotion gate computes ONE pooled MAE across every eligible carpark for
ONE global model — no per-carpark quality visibility exists anywhere in the audit trail. A large
future onboarding wave (a dozen+ newly-warmed, still-noisy carparks at once) could dilute the
pooled MAE for every carpark's forecast quality, not just its own, with nothing in
`training_runs` able to distinguish "the model got worse" from "a batch of new carparks pooled
in were unusually noisy this cycle."

**Context:** Explicitly identified during the coverage-expansion `/autoplan` review (both the
Premise Gate and an independent CEO subagent review flagged this). Deliberately NOT fully solved
by that plan — the user explicitly declined "fix the promotion gate now" at the Premise Gate,
since it would mean touching `gate.py`'s promotion/MAE internals, a bigger and riskier change
than the coverage expansion itself needed. This TODO exists so the decision to defer doesn't
quietly become permanent.

**Trigger condition (not just "someday"):** revisit before the 3rd wave of new carparks is
onboarded, or immediately if any single onboarding wave is a dozen-plus carparks relative to the
existing pool.

**Effort:** L (schema change for per-carpark MAE tracking + `gate.py` logic changes)
**Priority:** P3
**Depends on:** The coverage-expansion plan above shipping first (need real onboarding waves to
size the actual risk against, not just the original 10).

### "Vote for your mall" request feature

**What:** Let users request a mall carpark not yet covered, feeding a prioritized backlog for
future coverage-expansion waves.

**Why:** Surfaced during the coverage-expansion `/autoplan` CEO review's cherry-pick scan as a
genuine engagement/prioritization idea — lets real user demand (not just LTA feed availability)
drive which malls get added next.

**Context:** Needs its own design pass (new UI state, lightweight backend to collect/tally
requests) — deferred as outside the coverage-expansion plan's blast radius, not rejected.

**Effort:** M
**Priority:** P3
**Depends on:** The coverage-expansion plan above shipping first (there needs to be a visible
"not covered" state for users to vote from, which already exists via `NotCoveredState.tsx`).

### "Closest known mall" fuzzy suggestion in NotCoveredState

**What:** When a search doesn't match any supported carpark, suggest the closest known
supported mall instead of a flat "not covered" message.

**Why:** Surfaced during the coverage-expansion `/autoplan` CEO review's cherry-pick scan as a
UX delight opportunity — softens the not-covered dead end into a helpful redirect.

**Context:** Needs its own UX design pass (what "closest" means — name-similarity? geographic
proximity, which the app doesn't currently model at all?) — deferred as a real feature decision,
not a mechanical widening.

**Effort:** S-M
**Priority:** P3
**Depends on:** None directly, though more useful once coverage widens (more candidates to
suggest from).

### Recurring auto-discovery pipeline for new mall carparks

**What:** Instead of the coverage-expansion plan's one-shot whitelist script, a recurring weekly
job that watches the live LTA feed for newly-appearing carparks, auto-matches them, and
auto-queues verified candidates for T1-style variance validation — turning "expand coverage"
into a self-sustaining pipeline rather than a manual, repeat-when-remembered exercise.

**Why:** Surfaced as the coverage-expansion `/autoplan` CEO review's own "10x check" — the
ambitious version of the one-shot script that ships first.

**Context:** Connects directly to the existing "Generalize into a reusable SG Civic Forecast
platform" TODO below — the same "verify a candidate entity, then onboard it" shape would apply
to bus/taxi forecasting too, so this is worth building with that reuse in mind once it's
prioritized.

**Effort:** L
**Priority:** P3
**Depends on:** The one-shot coverage-expansion plan above shipping and proving out the
match/validate/insert pipeline manually first.

## Completed
