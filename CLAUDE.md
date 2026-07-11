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
