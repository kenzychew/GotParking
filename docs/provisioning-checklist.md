# GotParking -- T1.5 Provisioning Checklist (human-owned)

Task T1.5 from the design doc
(`~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`):
provision every platform account, project, and secret BEFORE the implementation
lanes (T2-T6) launch. This is Lane 0 (finding D11) -- every other lane depends
on it, and it is the only lane a human must drive personally.

Estimated wall clock: 1-2 hours. Do the phases in order -- Supabase (Phase 2)
and healthchecks.io (Phase 3) produce values that the secrets wiring (Phase 6)
consumes.

Ground rules (design doc, Security section):

- Secrets live ONLY in platform-native secret stores (Cloudflare Workers
  secrets, Vercel environment variables, GitHub Actions repository secrets)
  and in your local gitignored `.env`. Never commit a secret, never pass one
  as a plain build arg, never paste one into this file.
- Where a step says "the value of `LTA_API_KEY` from your local `.env`", open
  `C:\Users\kenzy\gstack-playground\.env` and copy the value from there. The
  key's value is intentionally reproduced nowhere in this document.
- Console menu names drift. Where a step is hedged with "(menu names may
  drift...)", trust the named setting/value over the exact click path.

## Phase 0: Before you start

- [ ] Confirm you can sign in to (or are ready to create) free accounts on:
      `github.com`, `supabase.com`, `healthchecks.io`, `dash.cloudflare.com`,
      `vercel.com`. Everything in this checklist fits free tiers.
- [ ] Confirm the local `.env` at `C:\Users\kenzy\gstack-playground\.env`
      contains a line starting with `LTA_API_KEY=`. Do not print or copy the
      value anywhere yet.
- [x] Generate the new `BATCH_SHARED_SECRET` (random, 32+ characters). It
      gates the poller-to-batch-predict endpoint (design doc Premise #9 as
      amended 2026-07-04, D10). Run ONE of:
      - Git Bash: `openssl rand -hex 32`
      - PowerShell:
        `$b = New-Object byte[] 32; (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b); -join ($b | ForEach-Object { $_.ToString("x2") })`
      -- DONE 2026-07-05: generated via `openssl rand -hex 32` (64 hex chars).
- [x] Append the generated value to the local `.env` as a new line:
      `BATCH_SHARED_SECRET=<generated value>`. This is your single local
      reference copy (`.env` is gitignored -- re-verified in Phase 1).
      -- DONE 2026-07-05.
- [ ] Open a scratch note or password-manager entry titled
      `gotparking provisioning` to collect the values in the "Hand-off state"
      list at the bottom of this file as you go.

## Phase 1: GitHub -- create the remote and push `main`

Public vs private -- decide before creating the repo:

- **Public (RECOMMENDED):** portfolio visibility for the GovTech/OGP audience
  this project exists to impress, and GitHub Actions minutes are free for
  public repos. Tradeoff: GitHub auto-disables scheduled workflows in public
  repos after 60 days without repo activity. Our healthchecks.io dead-man's
  switch catches exactly this (a missed weekly training ping emails you --
  design doc Premise #8 / D2), and an optional keepalive (any commit or
  workflow-file touch resets the 60-day clock) can prevent it entirely.
- **Private:** no 60-day auto-disable concern, but Actions runs consume the
  2000 free minutes/month on the free plan -- a weekly training job of a few
  minutes fits easily, so this is not a real constraint either. Tradeoff: no
  portfolio visibility.

- [x] Decide visibility. Recommendation: **Public**. -- DONE 2026-07-04: repo is Public.
- [x] Pre-push safety check, in a terminal at
      `C:\Users\kenzy\gstack-playground`:
      - `git ls-files .env` -- must print nothing (`.env` is not tracked)
      - `git check-ignore .env` -- must print `.env` (it is gitignored)
      If either fails, STOP and fix `.gitignore` before pushing anything.
      -- DONE 2026-07-04: verified clean (no `.env` or `data/` files tracked).
- [x] Confirm repo state: `git status` shows branch `main` and no stray
      uncommitted files you meant to include; `git log --oneline -3` shows the
      scaffold/T1 commits. -- DONE 2026-07-04 (baseline v0.0.1.0, 3 commits).
- [x] Create the remote at `https://github.com/new`: Repository name
      `gotparking`, visibility per your decision, and do NOT initialize with a
      README, .gitignore, or license (the local repo already has history; an
      initialized remote would diverge). The local folder name
      (`gstack-playground`) not matching the repo name is fine.
      -- DONE 2026-07-04: created as `GotParking` (capitalization differs from
      the suggested name; harmless -- GitHub URLs are case-insensitive here).
- [x] Connect and push, from `C:\Users\kenzy\gstack-playground`:
      - `git remote add origin https://github.com/<your-username>/gotparking.git`
        (if `git remote -v` already shows an `origin`, skip the add)
      - `git push -u origin main`
      (CLI alternative for both steps:
      `gh repo create gotparking --public --source . --remote origin --push`,
      swapping `--public` for `--private` per your decision.)
      -- DONE 2026-07-04: `main` pushed and tracking `origin/main`.
- [x] Verify the repo page shows the pushed tree: `api/`, `db/`, `frontend/`,
      `poller/`, `training/`, `scripts/`, `README.md`, etc.
      -- DONE 2026-07-04: origin/main matches local; repo confirmed via gh.
- [x] Record `GITHUB_REPO_URL` = `https://github.com/kenzychew/GotParking`
      in your scratch note. -- DONE (recorded here).

Note: Actions repository secrets are wired in Phase 6b. No workflow exists yet
(T5 adds `.github/workflows/train.yml`) -- an empty Actions tab is expected.

## Phase 2: Supabase -- project in `ap-southeast-1` + private `models` bucket

WARNING -- create-time-only decision (design doc D7): the project region MUST
be `ap-southeast-1` (shown as `Southeast Asia (Singapore)`). Region cannot be
changed after creation; getting it wrong means migrating the database later.
Double-check the region dropdown before clicking Create.

- [x] Sign in at `https://supabase.com/dashboard` (create the account if
      needed; the default personal organization on the Free plan is fine).
      -- DONE 2026-07-04.
- [x] Click `New project` and fill in exactly:
      - Organization: your personal org
      - Project name: `gotparking`
      - Database password: use the Generate button and SAVE the value to your
        password manager as `SUPABASE_DB_PASSWORD` (needed later for direct
        database/migration access in T2)
      - Region: `Southeast Asia (Singapore)` = `ap-southeast-1`  <-- the
        create-time-only decision
      - Plan/instance size: Free
      -- DONE 2026-07-04: project ref `rkxarvcbzlutruadonge`. GitHub
      integration skipped (not in architecture); security options left at
      defaults with Data API enabled on `public` (lockdown comes from the
      schema's RLS + revoked grants, verified below).
- [x] Click Create and wait for provisioning to finish (a few minutes).
      -- DONE 2026-07-04.
- [x] Verify the region IMMEDIATELY: `Project Settings` > `General` (or
      `Infrastructure`; menu names may drift -- the setting to find is the
      project's Region) must read `ap-southeast-1` / Southeast Asia
      (Singapore). If it shows anything else, DELETE the project and recreate
      it now -- deletion is cheap today; a database migration later is not.
      -- DONE 2026-07-04: confirmed empirically -- warm REST round trips from
      a Singapore machine measure ~110ms (a US-region database would be
      250ms+ on network alone), consistent only with ap-southeast-1.
- [x] Copy the Project URL -> record as `SUPABASE_URL`. Found under
      `Project Settings` > `Data API` (or `API`), field `Project URL`, format
      `https://<project-ref>.supabase.co`.
      -- DONE 2026-07-04: saved to local .env. Gotcha hit and fixed: the
      dashboard's REST URL (`.../rest/v1/`) was copied first; the correct
      value is the BASE url with no path -- client libraries append
      `/rest/v1/` themselves.
- [x] Copy the service-role key -> record as `SUPABASE_SERVICE_ROLE_KEY`.
      Found under `Project Settings` > `API Keys`. (Menu names may drift; the
      key to find is the SERVER-SIDE key that BYPASSES Row Level Security --
      current dashboards show it either as the legacy `service_role` key or
      as a new-style secret key (`sb_secret_...`). Either works; store it
      under the name `SUPABASE_SERVICE_ROLE_KEY`. NEVER use the
      `anon`/publishable key for this -- the whole RLS design in the Security
      section assumes only this key can write.)
      -- DONE 2026-07-04: saved to local .env (never pasted in chat).
- [x] Create the model-artifact bucket: `Storage` > `New bucket`, name exactly
      `models`, and leave the `Public bucket` toggle OFF -- the bucket must be
      PRIVATE (it will hold LightGBM model artifacts written by the training
      job and read by the batch-predict function, both using the service-role
      key; design doc Premise #9).
      -- DONE 2026-07-04: created by `db/schema.sql` section 9 (T2 landed
      before this manual step), with `public = false`.
- [x] Confirm the bucket list shows `models` marked Private.
      -- DONE 2026-07-04 via `select id, name, public from storage.buckets`.

T2 schema applied and verified 2026-07-04 (see `db/README.md` for the
procedure): 7 tables present, 10 seed carparks with SINPA indices,
model_config singleton (baseline-only), duplicate-insert guard proven
(same reading twice -> exactly one row), anon publishable key locked out
(42501 permission denied on read AND write across tables), service-role
access working.

Notes:
- Do NOT create any tables now -- the schema (`carpark_history`,
  `carpark_baseline`, `carpark_momentum`, `carpark_forecast`, `model_config`,
  RLS policies) is T2's job.
- Free-tier projects pause after ~7 days without database activity (design
  doc Premise #8 rationale). If the code lanes have not landed within a week
  of provisioning, expect to click Restore in the dashboard before T2/T3 work
  begins.

## Phase 3: healthchecks.io -- two dead-man's-switch checks

-- DONE 2026-07-06, via the API (`POST /api/v3/checks/` with an `X-Api-Key`
read-write key from Project Settings) rather than the dashboard click-path
below, since by this point the poller was already live (T3/Phase 4 done) and
using the API avoided guessing at dashboard field names for schedule/grace
(the pattern that went wrong twice on Cloudflare's dashboard earlier in this
project). The account already existed with a default "My First Check" demo
check (1 day/1 hour schedule) from signup -- harmless, left alone.

Alerting is absence-based (design doc Premise #8): jobs prove liveness by
pinging; when pings stop, healthchecks.io emails you. Free tier: 20 checks,
email alerts included. Failure signaling needs no extra setup -- jobs append
`/fail` to the same ping URL to report hard failures with a reason.

- [x] Create a free account at `https://healthchecks.io` using the email
      address where you want alerts delivered (you are the alert owner --
      solo project, no on-call rotation; design doc Security section).
      -- DONE (kenzychew@gmail.com).
- [x] Create check 1: `Add Check`, then set:
      - Name: `gotparking-poller`
      - Schedule (Simple): Period = `5 minutes`, Grace = `30 minutes`
        (matches Premise #8: pings stop for >30 minutes -> email)
      -- DONE 2026-07-06 via API (`timeout=300, grace=1800` -- API fields are
      in seconds, not the dashboard's minutes/hours units).
- [x] Copy check 1's ping URL (format `https://hc-ping.com/<uuid>`) -> record
      as `HEALTHCHECKS_POLLER_PING_URL`. -- DONE, saved to `.env` (API
      response included it directly, no dashboard copy step needed).
- [x] Create check 2: `Add Check`, then set:
      - Name: `gotparking-training`
      - Schedule (Simple): Period = `7 days` (1 week), Grace = `24 hours`
        (catches both training crashes and the job never running at all,
        e.g. GitHub's 60-day auto-disable -- Premise #8 / D2)
      -- DONE 2026-07-06 via API (`timeout=604800, grace=86400`).
- [x] Copy check 2's ping URL -> record as `HEALTHCHECKS_TRAINING_PING_URL`.
      -- DONE, saved to `.env`.
- [x] Confirm email alerting is enabled on BOTH checks -- DONE: created both
      with `"channels":"*"` (attaches every available integration on the
      account, which for a fresh account is just the default email
      integration); verified via API afterward that both checks show
      `channels_count=1`.
- [x] Test the alert path for check 1: `curl -fsS <HEALTHCHECKS_POLLER_PING_URL>`
      -- DONE, returned `OK`; check flipped to `up` (confirmed via API).
- [x] Same test ping for `<HEALTHCHECKS_TRAINING_PING_URL>` -- DONE, returned
      `OK`.
- [x] Now PAUSE both checks -- **amended**: only check 2 (`gotparking-training`)
      was paused (via `POST /api/v3/checks/<uuid>/pause`), since the training
      job's ping URL isn't wired into GitHub Actions secrets yet (Phase 6b,
      still open) and the workflow only runs weekly -- a real gap of up to 7
      days before it could ping for real. Check 1 (`gotparking-poller`) was
      deliberately left ACTIVE, not paused: the poller was already live and
      `HEALTHCHECKS_POLLER_PING_URL` was wired to its Cloudflare secret in the
      same session, so a real ping arrives within 5 minutes -- pausing it
      would have been pure ceremony for a false-alarm window that was never
      going to open.

## Phase 4: Cloudflare -- Workers project + 5-minute cron

-- DONE 2026-07-05, via CLI rather than the dashboard click-path originally
sketched below (T3's real poller code already existed by this point, so this
phase and T3's actual deploy happened together). Actual sequence, for
reference: `npx wrangler login` (OAuth, browser-completed), then
`npx wrangler deploy` from `poller/` -- this reads the worker name
(`gotparking-poller`) and the cron trigger (`*/5 * * * *`) directly from
`poller/wrangler.toml`, so there was no separate "create a Hello World
Worker, then add a cron trigger" dashboard flow to do by hand.

- [x] Sign up / sign in at `https://dash.cloudflare.com` (Free plan; no
      domain required for Workers). -- DONE via `wrangler login` (browser
      OAuth), no dashboard visit needed for this step.
- [x] Register your `workers.dev` subdomain. **Correction (found live,
      2026-07-05): this is NOT a signup-time prompt** -- it's a per-worker
      toggle. After the first `wrangler deploy`, go to
      `Workers & Pages > gotparking-poller > Domains`, find the "Worker URL"
      section, and toggle the `Production` switch ON next to
      `gotparking-poller.<your-subdomain>.workers.dev` (the subdomain itself,
      e.g. `kenzychew.workers.dev`, may already be assigned to your account
      without any separate claim step). `wrangler deploy` fails with
      "You need to register a workers.dev subdomain" until this toggle is
      on, and this cannot be done non-interactively -- wrangler's own prompt
      for it does not work in a scripted/CI context either (a documented
      wrangler limitation, not a bug in this project).
- [x] Create the Worker -- superseded: `wrangler deploy` created/updated the
      Worker directly from `poller/wrangler.toml` and `poller/src/index.ts`;
      no dashboard "start from Hello World" step was needed.
- [x] Verify it responds -- DONE:
      `https://gotparking-poller.kenzychew.workers.dev` deployed successfully
      after the Domains toggle above.
- [x] Add the cron trigger -- superseded: already declared in
      `poller/wrangler.toml`'s `[triggers]` block and applied automatically
      by `wrangler deploy`; no manual dashboard step needed.
- [x] Confirm the trigger list shows `*/5 * * * *` -- confirmed in the
      deploy output: `schedule: */5 * * * *`.

Notes:
- The Worker name matters: T3's wrangler deploy must target the same name
  `gotparking-poller` so it replaces the hello-world code while keeping this
  cron trigger and the Phase 6a secrets.
- Until T3 deploys real poller code, the cron invokes a no-op hello-world and
  pings nothing; healthchecks stays paused per Phase 3, so no false alarms.
- Worker secrets are wired in Phase 6a.
- **New finding (2026-07-05):** enabling the workers.dev route also turns on
  Preview URLs by default (a wrangler warning noted this: `preview_urls` was
  not explicitly set). Low-stakes for this worker (cron-only, no public
  `fetch()` handler to expose), but worth an explicit `preview_urls = false`
  in `wrangler.toml` later if that default is ever undesirable -- tracked in
  TODOS.md rather than fixed now.

## Phase 5: Vercel -- project import + `sin1` region pin

-- DONE 2026-07-05, via CLI rather than the dashboard import flow sketched
below (all four code lanes had already merged, so Phase 5 and the first real
deploy happened together, mirroring how Phase 4 played out). Actual sequence:
the repo was linked to a CLI-created project named `gstack-playground` (named
after the local folder, NOT `gotparking` as assumed below), and
`npx vercel --prod` from the repo root deploys it. The first deploys failed
on a real platform issue -- import auto-detected Framework Preset `python`
from the root `requirements.txt`, which makes Vercel demand a single Python
entrypoint for the whole repo and ignore the per-file `api/` convention
entirely -- and the working fix was migrating `vercel.json` to Vercel's
`services` model (three services: `frontend`, plus `batch_predict` and
`forecast` rooted at `api/` with file-form entrypoints and deps from
`api/requirements.txt`, exposed via top-level rewrites at the unchanged
paths `/api/batch_predict` and `/api/forecast`), plus a `buildCommand` on
the batch service copying `libgomp.so.1` into `lib/` for lightgbm. Full
story in the README's "Vercel deploy: fixed" paragraph.

Vercel's default serverless-function region is `iad1` (Washington, D.C.) --
it MUST be changed to `sin1` (Singapore). The Hobby plan allows pinning
exactly one region, and the pin lives in `vercel.json` in the repo (design
doc D7 / Premise #9 as amended).

- [x] FIRST, pin the region in the repo so Vercel's very first deploy already
      carries it. **Correction (found live, 2026-07-05):** the file no longer
      contains ONLY the region pin -- it now carries the full `services`
      config -- but `"regions": ["sin1"]` remains at the top level, which in
      services mode still applies to every Python service.
- [x] Commit and push it -- DONE (the evolving `vercel.json` has been
      committed throughout; the final services-model version landed
      2026-07-05).
- [x] Sign up / sign in -- DONE 2026-07-05: CLI session as `kenzychew-1249`
      (Hobby plan) with the GitHub integration active (pushes to `main`
      auto-deploy).
- [x] Import the project -- superseded: created/linked via CLI as
      `gstack-playground` instead of a dashboard import named `gotparking`.
      Harmless beyond the URL differing from the one this checklist guessed.
- [x] Configure the import: Framework Preset `Other` -- **Correction (this
      assumption was the deploy blocker):** the import auto-detected
      Framework Preset `python` (from the root `requirements.txt`), and under
      that preset Vercel requires ONE Python entrypoint for the whole repo --
      the documented "each file in api/ becomes its own function" convention
      is never consulted. Resolved in-repo by the `services` migration above
      (`vercel.json` is authoritative; the dashboard preset no longer
      matters in services mode).
- [x] Wait for the deployment to reach status `READY` -- DONE 2026-07-05:
      production deploy READY on the first successful services build; the
      root URL serves the T6 PWA (the "404 is fine" hedge below was written
      for a pre-T6 hello-world deploy that never needed to exist).
- [x] Record the production URL -> `VERCEL_PROD_URL` =
      `https://gstack-playground.vercel.app`. `BATCH_PREDICT_URL` =
      `https://gstack-playground.vercel.app/api/batch_predict` (T4 kept the
      guessed path). Verified live: `GET /` returns the PWA shell (200);
      `GET /api/forecast` returns the typed 503 (`predictions_unavailable`)
      until Phase 6c wires the env vars -- exactly the designed
      missing-config behavior, and proof the function runs.
- [x] Optional cross-check: dashboard region -- superseded: the committed
      `vercel.json` is the authoritative pin, and live responses from both
      Python functions carry `X-Vercel-Id: sin1::...`, confirming the pin
      empirically.

Note: environment variables were wired in Phase 6c on 2026-07-06 -- DONE, see
below (this note was written before that happened and was left stale).

## Phase 6: Secrets wiring

The full matrix -- exact names, one row per secret/value:

| Secret / value                   | Cloudflare Worker secrets | GitHub Actions repo secrets | Vercel env vars |
|----------------------------------|---------------------------|-----------------------------|-----------------|
| `LTA_API_KEY`                    | x                         |                             |                 |
| `SUPABASE_URL`                   | x                         | x                           | x               |
| `SUPABASE_SERVICE_ROLE_KEY`      | x                         | x                           | x               |
| `BATCH_SHARED_SECRET`            | x                         |                             | x               |
| `HEALTHCHECKS_POLLER_PING_URL`   | x                         |                             |                 |
| `HEALTHCHECKS_TRAINING_PING_URL` |                           | x                           | x               |
| `BATCH_PREDICT_URL`              | x (later)                 |                             |                 |

When each value exists:

| Value                            | Source                                   | Available                        |
|----------------------------------|------------------------------------------|----------------------------------|
| `LTA_API_KEY`                    | already in local `.env`                  | now                              |
| `SUPABASE_URL`                   | Phase 2                                  | now                              |
| `SUPABASE_SERVICE_ROLE_KEY`      | Phase 2                                  | now                              |
| `BATCH_SHARED_SECRET`            | generated in Phase 0                     | now                              |
| `HEALTHCHECKS_POLLER_PING_URL`   | Phase 3                                  | now                              |
| `HEALTHCHECKS_TRAINING_PING_URL` | Phase 3                                  | now                              |
| `BATCH_PREDICT_URL`              | `VERCEL_PROD_URL` + T4's endpoint path   | later -- after T4's first deploy |

Use the exact names above -- they are what the T3/T4/T5 code will read. Copy
values from your scratch note and local `.env`, never from a committed file.

### Phase 6a: Cloudflare Worker secrets (5 now, 1 later)

Path: worker `gotparking-poller` > `Settings` > `Variables and Secrets` >
`Add`, Type `Secret`. (Menu names may drift; the setting to find is the
Worker's encrypted environment variables.) CLI alternative used in practice:
`printf '%s' "$VALUE" | npx wrangler secret put <NAME>` from `poller/` (run
from a shell that already sourced `.env`, e.g. `set -a; source ../.env; set
+a` -- `printf` avoids a trailing newline in the secret; piping via stdin
means the value is never a literal command-line argument or echoed to any
log).

- [x] `LTA_API_KEY` = the value of `LTA_API_KEY` from your local `.env`
      -- DONE 2026-07-05, `wrangler secret list` confirms it's set.
- [x] `SUPABASE_URL` = the recorded value -- DONE 2026-07-05.
- [x] `SUPABASE_SERVICE_ROLE_KEY` = the recorded value -- DONE 2026-07-05.
- [x] `BATCH_SHARED_SECRET` = the value of `BATCH_SHARED_SECRET` from your
      local `.env` -- DONE 2026-07-05.
- [x] `HEALTHCHECKS_POLLER_PING_URL` = the recorded value -- DONE 2026-07-06.
      `wrangler secret list` confirms it's set -- this was the 6th and final
      poller secret; all six are now wired.
- [x] Click `Deploy` / apply if the dashboard asks to deploy the changes --
      N/A via CLI; `wrangler secret put` applies immediately, no separate
      deploy step needed for a secret change.
- [x] `BATCH_PREDICT_URL` = `<VERCEL_PROD_URL>` + the batch endpoint path --
      DONE 2026-07-05/06: value is
      `https://gstack-playground.vercel.app/api/batch_predict` (Phase 5
      done), wired via `wrangler secret put BATCH_PREDICT_URL`.

### Phase 6b: GitHub Actions repository secrets (3) -- DONE 2026-07-06

-- Corrected 2026-07-06 (found stale by /document-release's doc review): this
section's own checkboxes were never updated when this work was actually
done; Phase 7 and the Hand-off list below were updated but this source
section was missed. Fixed now to match.

Path: `github.com/<your-username>/gotparking` > `Settings` >
`Secrets and variables` > `Actions` > `New repository secret`. Done via
`gh secret set <NAME> --repo kenzychew/GotParking` (reads from stdin) in
practice, not the dashboard.

- [x] `SUPABASE_URL` = the recorded value -- DONE, confirmed via `gh secret list`.
- [x] `SUPABASE_SERVICE_ROLE_KEY` = the recorded value -- DONE, confirmed via `gh secret list`.
- [x] `HEALTHCHECKS_TRAINING_PING_URL` = the recorded value -- DONE. The
      training check is still PAUSED on healthchecks.io (by design) until
      `.github/workflows/train.yml` fires on its own schedule and sends the
      first real ping.

### Phase 6c: Vercel environment variables (4) -- DONE 2026-07-06

Path: project `gstack-playground` > `Settings` > `Environment Variables` >
`Add`. Done via `vercel env add <NAME> <environment>` (once per environment
-- omitting the environment only works in interactive mode) in practice, not
the dashboard. Applied to all 3 environments (Production, Preview,
Development); `SUPABASE_SERVICE_ROLE_KEY` and `BATCH_SHARED_SECRET` are
Sensitive on Production/Preview -- Vercel does not allow Sensitive on
Development at all (confirmed live, not a gap here), so those two are
Non-sensitive there, which is also what makes `vercel env pull` work locally.

- [x] `SUPABASE_URL` = the recorded value -- DONE, confirmed via `vercel env ls`.
- [x] `SUPABASE_SERVICE_ROLE_KEY` = the recorded value -- DONE, confirmed via `vercel env ls`.
- [x] `BATCH_SHARED_SECRET` = the value from your local `.env` -- DONE, confirmed via `vercel env ls`.
- [x] `HEALTHCHECKS_TRAINING_PING_URL` = the recorded value -- DONE 2026-07-06
      (added after the rest of this phase; batch-predict's `/fail` ping was
      found to be a silent no-op in production without it -- see TODOS.md).
      Sensitive on Production/Preview, Non-sensitive on Development (same
      reasoning as the other two secrets above).

Note: Vercel env vars take effect from the NEXT deployment. `HEALTHCHECKS_TRAINING_PING_URL`
required an explicit `npx vercel --prod` redeploy after adding it (2026-07-06) --
confirmed live via a real forced-failure drill: called the actual `fire_fail_ping`
against the real URL, watched `gotparking-training` flip to `status=down` via the
healthchecks.io API, then cleared it with a real success ping and re-paused the check.

## Phase 7: Verification (T1.5 exit criteria)

Design doc Verify line for T1.5: "each platform reachable with its secret
from its runtime; healthchecks shows both checks; Supabase project region
reads ap-southeast-1; vercel.json pins sin1". Hello-world-level deploys are
acceptable proof.

-- T1.5 is now fully complete (2026-07-06) -- every item below verified.

- [x] GitHub: the repo page shows the pushed tree AND the Phase 5
      `vercel.json` commit (proves you can push with your credentials).
      -- DONE, confirmed throughout Phases 1-6.
- [x] Supabase region: `Project Settings` > `General` (or `Infrastructure`)
      reads `ap-southeast-1` / `Southeast Asia (Singapore)`. -- DONE
      2026-07-04, confirmed in Phase 2 (via SG-latency measurement, ~110ms
      warm round trips, consistent only with ap-southeast-1).
- [x] Supabase reachable with its secret -- DONE 2026-07-04 (Phase 2/T2
      verification): service-role reads returned the 10 seed carparks;
      anon/publishable key correctly got 401 (RLS + revoked grants).
- [x] Supabase Storage: bucket `models` exists and is marked Private. --
      DONE 2026-07-04, confirmed via `select id, name, public from
      storage.buckets` (`public = false`).
- [x] healthchecks.io: the dashboard lists (at least) two checks --
      `gotparking-poller` (period 5 minutes, grace 30 minutes) and
      `gotparking-training` (period 7 days, grace 24 hours) -- both received
      one manual `OK` test ping. **Amended:** only `gotparking-training` is
      Paused; `gotparking-poller` is deliberately ACTIVE (status `up`) since
      the poller was already live and pinging for real by the time this
      check was created -- see Phase 3's amended note.
- [x] Cloudflare: `https://gotparking-poller.kenzychew.workers.dev` is live
      -- superseded by T3 landing for real (not a hello-world placeholder);
      confirmed via `wrangler deployments list` and the poller's own
      healthchecks `up` status above.
- [x] Cloudflare: the worker's cron trigger list shows `*/5 * * * *` --
      confirmed in the `wrangler deploy` output: `schedule: */5 * * * *`.
- [x] Cloudflare: `Variables and Secrets` lists all 6 names (values hidden):
      `LTA_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `BATCH_SHARED_SECRET`, `HEALTHCHECKS_POLLER_PING_URL`,
      `BATCH_PREDICT_URL` -- DONE 2026-07-06, confirmed via
      `wrangler secret list`. (Superseded: T4 landed, so `BATCH_PREDICT_URL`
      is no longer "intentionally absent" -- all 6 are present.)
- [x] GitHub: `Settings` > `Secrets and variables` > `Actions` lists exactly
      these 3 names: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `HEALTHCHECKS_TRAINING_PING_URL` -- DONE 2026-07-06, confirmed via
      `gh secret list`.
- [x] Vercel: the latest deployment status is `READY` and the project is
      linked to the GitHub repo -- DONE 2026-07-05 (project is named
      `gstack-playground`, not `gotparking`; linked to
      `kenzychew/GotParking`, auto-deploys on push to `main`).
- [x] Vercel: `Settings` > `Environment Variables` lists exactly these 4
      names: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `BATCH_SHARED_SECRET`, `HEALTHCHECKS_TRAINING_PING_URL` -- DONE
      2026-07-06 (4th added same day, see Phase 6c), confirmed via `vercel
      env ls` (all 4 present across Production/Preview/Development -- 12
      rows).
      `SUPABASE_SERVICE_ROLE_KEY`/`BATCH_SHARED_SECRET` are Sensitive on
      Production/Preview; Development doesn't support Sensitive vars at all
      (a real Vercel platform constraint, not a gap here) so those two are
      Non-sensitive there, which is also what makes `vercel env pull` work
      locally.
- [x] `vercel.json` at the repo root on `main` pins `sin1` -- **Correction
      (2026-07-05):** the file now carries the full `services` config rather
      than exactly `{"regions": ["sin1"]}`, but `"regions": ["sin1"]` is
      still its top-level pin, and live function responses carry
      `X-Vercel-Id: sin1::...`.
- [x] Local `.env` is still untracked: `git ls-files .env` prints nothing --
      reconfirmed 2026-07-06.
- [x] Every value in the Hand-off state list below is recorded -- in local
      `.env` (gitignored); `SUPABASE_DB_PASSWORD` and the healthchecks.io
      account password remain password-manager-only as designed.

T1.5 is complete: Lane A (T2, Supabase schema) was unblocked long ago, and
Lanes B/C/D/E (T3/T4/T5/T6) have all since landed and are live in
production.

## Hand-off state (what you should have collected by the end)

These are the values the next tasks consume. Names are exact. Store them in
your password manager and/or the gitignored local `.env`; never commit any of
them.

1.  `GITHUB_REPO_URL` -- `https://github.com/<your-username>/gotparking`
    (visibility per your Phase 1 decision). Consumed by: T5's workflow lives
    here; Vercel deploys from it.
2.  `SUPABASE_URL` -- `https://<project-ref>.supabase.co`. Wired into:
    Cloudflare, GitHub Actions, Vercel. Consumed by: T2-T5.
3.  `SUPABASE_SERVICE_ROLE_KEY` -- the server-side key that bypasses RLS.
    Wired into: Cloudflare, GitHub Actions, Vercel. Consumed by: T2 (RLS
    verification), T3 (poller writes), T4 (batch predict/read), T5 (training).
4.  `SUPABASE_DB_PASSWORD` -- database password from project creation.
    Password manager only (not wired anywhere). Consumed by: T2 migrations if
    run over a direct database connection.
5.  `BATCH_SHARED_SECRET` -- generated in Phase 0; in local `.env` and wired
    into: Cloudflare, Vercel. Consumed by: T3 (sends the header), T4
    (verifies the header, 401 without it).
6.  `HEALTHCHECKS_POLLER_PING_URL` -- wired into: Cloudflare. Consumed by:
    T3 (success ping per cycle, `/fail` on hard failures).
7.  `HEALTHCHECKS_TRAINING_PING_URL` -- wired into: GitHub Actions. Consumed
    by: T5 (weekly completion ping, `/fail` on crash/upload failure).
8.  `VERCEL_PROD_URL` -- `https://gstack-playground.vercel.app` (recorded
    2026-07-05, Phase 5 done).
9.  `BATCH_PREDICT_URL` -- `https://gstack-playground.vercel.app/api/batch_predict`
    -- DONE 2026-07-06: wired as the 6th and final Cloudflare Worker secret.

Platform state at hand-off (2026-07-06, T1.5 fully complete): GitHub repo
`GotParking` pushed, all four lanes (T2-T6) merged to `main`; Supabase
project `gotparking` live in `ap-southeast-1`, schema applied and verified;
healthchecks.io checks `gotparking-poller` (ACTIVE, `up`, receiving real
pings every 5 minutes) and `gotparking-training` (PAUSED, live-verified
2026-07-06 via a real forced-failure drill -- see TODOS.md) both created and
tested; Cloudflare Worker `gotparking-poller` running the real T3 poller
code live, cron `*/5 * * * *` confirmed, all 6 secrets wired; Vercel project
`gstack-playground` live at `https://gstack-playground.vercel.app` via the
`services` model, `sin1` pinned, all 4 env vars set across all 3
environments (3 from Phase 6c plus `HEALTHCHECKS_TRAINING_PING_URL`, added
2026-07-06); GitHub Actions has all 3 of its repository secrets set. Every
platform, every secret, every value from this checklist is now wired
somewhere -- nothing is deferred anymore. The one thing left is time, not a
task: `gotparking-training`'s healthchecks check stays Paused (by design)
until `.github/workflows/train.yml` actually fires on its own weekly
schedule and sends its first real ping -- that's expected, not a gap.
