# gstack

- Use the `/browse` skill from gstack for all web browsing.
- Never use `mcp__claude-in-chrome__*` tools.
- Available gstack skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/setup-gbrain`, `/retro`, `/investigate`, `/document-release`, `/document-generate`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`

Install gstack (one-time, per machine):

```bash
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup
```

Note for Windows: skills install as file copies (no symlinks), so re-run `./setup` after every `git pull` in the gstack repo, or use `/gstack-upgrade`.

## Local status doc

If `STATUS.md` exists at the repo root (local-only, gitignored), read it for the current
implementation status narrative before starting substantive work, and update it when shipping
meaningful changes. Committed sources of truth remain `TODOS.md`, `CHANGELOG.md`,
`docs/provisioning-checklist.md`, and git history; `README.md` is public-facing and
carries no status detail beyond the dated test-count line and carpark count, which must be
refreshed when they change.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec

## Deploy Configuration (configured by /setup-deploy)

GotParking has THREE independent deploy surfaces, not one. `/land-and-deploy`'s
single-URL health-check model only fits the first; the other two are documented
here so they aren't silently dropped, but are NOT automated by /land-and-deploy.

- Platform: Vercel (primary — the only surface /land-and-deploy manages)
- Production URL: `https://gotparking.vercel.app` (Vercel project `gotparking`,
  renamed from `gstack-playground` on 2026-07-11; the legacy domain
  `https://gstack-playground.vercel.app` is still assigned to the project and
  serves identically, so old links keep working — live since 2026-07-05 after
  the services-model migration in `vercel.json`; see README and
  `docs/provisioning-checklist.md` Phase 5 for the story)
- Deploy workflow: auto-deploy on push to `main` (Vercel's default GitHub
  integration — no explicit GitHub Actions workflow needed for this surface);
  `npx vercel --prod` from the repo root deploys the local tree directly
- Deploy status command: `npx vercel ls` (CLI available via npx, logged in)
- Merge method: N/A — this repo commits straight to `main` (solo project, no PR
  flow yet; see the design doc's Distribution Plan)
- Project type: web app (PWA frontend) + its serving API, one combined Vercel
  project using the `services` model in `vercel.json`: four services
  (`frontend` rooted at `frontend/`; `batch_predict`, `forecast`, and
  `geocode_postal` each rooted at `api/` with file-form Python entrypoints),
  top-level rewrites exposing them at `/api/batch_predict`, `/api/forecast`,
  `/api/geocode_postal`, and `/(.*)`; `regions: ["sin1"]` stays top-level and
  applies to the Python services.
  Python deps come from `api/requirements.txt`; the `batch_predict` service
  has a `buildCommand` that copies `libgomp.so.1` into `lib/` (lightgbm's
  wheel does not bundle it and the function runtime image lacks it)
- Post-deploy health check: `GET /api/forecast` should return 200 with the
  pinned `{"generated_at", "carparks": [...]}` shape (see `api/_lib/read_logic.py`)
  once the project is live — a 503 with `{"error": "predictions_unavailable"}`
  means Supabase/data issues, not a bad deploy; a raw 500 means something the
  design doc says should never happen

### Custom deploy hooks
- Pre-merge: run each lane's test suite before pushing —
  `(cd poller && npx vitest run)`, `(cd api && uv run pytest -q)`,
  `(cd training && uv run pytest -q)`, `(cd frontend && npx vitest run)`,
  `(cd scripts && uv run pytest -q)` (added 2026-07-08, coverage-expansion tooling)
- Deploy trigger: automatic on push to `main` (Vercel only)
- Deploy status: `npx vercel ls` (see Deploy status command above)
- Health check: `GET {production-url}/api/forecast` (see above)

### Other deploy surfaces (NOT managed by /land-and-deploy)

- **Cloudflare Worker (`poller/`):** deploys via `wrangler deploy` from inside
  `poller/`, run manually — no CI automation exists for this yet (a real gap;
  consider a GitHub Actions workflow triggered on `poller/**` changes if this
  becomes a recurring manual step). Requires `wrangler secret put` for the six
  bindings documented in `poller/wrangler.toml`'s header comment. Depends on
  provisioning checklist Phase 4 (Cloudflare project creation).
- **GitHub Actions cron (`training/`):** not a "deploy" in the health-check
  sense — `.github/workflows/train.yml` runs on its own weekly schedule
  (`0 21 * * 6`) once pushed to `main`; there's nothing to health-check beyond
  the healthchecks.io training ping (Premise #8) and the `training_runs` table.
- **GitHub Actions workflow_dispatch (`.github/workflows/regen-seed-lists.yml`,
  added 2026-07-08):** manually triggered only (no schedule) — runs
  `scripts/regen_seed_lists.py` and opens a PR via `peter-evans/create-pull-request`
  if it produces a diff. The workflow's own inline comment flags real
  uncertainty about whether this repo's Actions permissions are configured to
  let it actually open a PR (vs. silently just pushing the branch) — verify
  before relying on it unattended.

## demo/ (FastAPI on Railway, not yet deployed)

`demo/` is a second, portfolio-consistent front end for the same public
`/api/forecast` data, built with FastAPI (see `demo/README.md`) instead of
Vercel — reuses `api/_lib/read_logic.py`/`supabase_rest.py` verbatim (not
`config.py`'s `load_settings()` — see below); never touches
`api/batch_predict.py` or any write path. Proven locally only; Railway
wiring is an explicit, not-yet-done follow-up.

**Credential decision made, applied.** The only Supabase
credential able to read `carpark_forecast`/`carparks`/`carpark_baseline`
out of the box is the service-role key (RLS is deny-by-default with
`anon`/`authenticated` grants revoked, per `db/README.md`, `db/schema.sql`).
That key bypasses RLS and can write anywhere, so it is not safe to hand to
a second deployment. Captain's decision: a dedicated `demo_reader` Postgres
role, SELECT-only on those three tables (`db/schema.sql` section 11 for
`carpark_forecast`/`carparks`, section 11b for `carpark_baseline`, added
for the destination-search redesign's trend chart), authenticated via a
hand-signed JWT with a `role: demo_reader` claim.

A hand-signed role JWT is not, by itself, enough to talk to Supabase's
REST API: Supabase's Kong gateway checks the `apikey` header against its
own list of project keys *before* the request reaches PostgREST's role
switching, and a hand-signed JWT is not on that list, so it gets a 401
`Invalid API key` from Kong even though the JWT's signature and claims are
correct. The fix is `SupabaseREST`'s optional `apikey` keyword (`api/_lib/
supabase_rest.py`): when omitted, the `apikey` header defaults to the same
value as the `Authorization` bearer token (correct for a real Supabase-
issued key, e.g. the service-role key, which is simultaneously a
Kong-recognized key and a role-bearing JWT); `demo/app.py` passes the
project's public anon/publishable key (`SUPABASE_ANON_KEY` env var) as
`apikey=` on all three of its `SupabaseREST(...)` sites, decoupling it
from the `demo_reader` JWT used as the bearer token. `demo/app.py` reads
`SUPABASE_URL` / `SUPABASE_DEMO_READER_KEY` / `SUPABASE_ANON_KEY` directly
from the environment (not via `_lib.config.load_settings()`, which stays
required-service-role-key-only for the real `api/forecast.py` path). Any
of the three missing/blank, or a permission-denied error because a
migration section hasn't been applied yet, all fall through to a typed 503
rather than crashing. See `demo/README.md`'s Credentials section for the
step-by-step (JWT minting instructions live as a comment in `db/schema.sql`
section 11 itself).

`demo/static/` is a destination-search-led flow (search box, a selected
carpark's forecast/tier plus nearby alternatives ranked by client-side
haversine distance, and a trend chart from `GET
/api/carpark-baseline/{carpark_id}`), not a map-first browse view: vanilla
JS/CDN, no build step, styled to match `portfolio-hub`'s design tokens
(`app/globals.css`: Fraunces / Public Sans / JetBrains Mono, warm
off-white/near-black palette; that project lives at a sibling checkout,
not inside this repo). The original Leaflet map still exists behind a "Show
map" toggle, not the default view. Nearby-alternatives distance sorting and
the map both use a demo-only `GET /api/carparks-geo` in `demo/app.py`
(id/name/lat/lng from `public.carparks`); the trend chart uses the
demo-only `GET /api/carpark-baseline/{carpark_id}` (today's SGT
day-of-week curve from `public.carpark_baseline`, current slot computed via
`api/_lib/sg_time.py`'s `sgt_parts`). Both are the same `demo_reader`
credentials and typed 503-never-500 contract as `/api/forecast`, and
neither is part of the pinned `/api/forecast` contract or touches
`api/_lib/read_logic.py`. Tier marker/pill colors are the same hex values
as `frontend/src/lib/colorTokens.ts`'s light theme, keep them in sync if
that file's values ever change.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
