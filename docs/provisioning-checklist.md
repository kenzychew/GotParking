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
- [ ] Generate the new `BATCH_SHARED_SECRET` (random, 32+ characters). It
      gates the poller-to-batch-predict endpoint (design doc Premise #9 as
      amended 2026-07-04, D10). Run ONE of:
      - Git Bash: `openssl rand -hex 32`
      - PowerShell:
        `$b = New-Object byte[] 32; (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b); -join ($b | ForEach-Object { $_.ToString("x2") })`
- [ ] Append the generated value to the local `.env` as a new line:
      `BATCH_SHARED_SECRET=<generated value>`. This is your single local
      reference copy (`.env` is gitignored -- re-verified in Phase 1).
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

- [ ] Sign in at `https://supabase.com/dashboard` (create the account if
      needed; the default personal organization on the Free plan is fine).
- [ ] Click `New project` and fill in exactly:
      - Organization: your personal org
      - Project name: `gotparking`
      - Database password: use the Generate button and SAVE the value to your
        password manager as `SUPABASE_DB_PASSWORD` (needed later for direct
        database/migration access in T2)
      - Region: `Southeast Asia (Singapore)` = `ap-southeast-1`  <-- the
        create-time-only decision
      - Plan/instance size: Free
- [ ] Click Create and wait for provisioning to finish (a few minutes).
- [ ] Verify the region IMMEDIATELY: `Project Settings` > `General` (or
      `Infrastructure`; menu names may drift -- the setting to find is the
      project's Region) must read `ap-southeast-1` / Southeast Asia
      (Singapore). If it shows anything else, DELETE the project and recreate
      it now -- deletion is cheap today; a database migration later is not.
- [ ] Copy the Project URL -> record as `SUPABASE_URL`. Found under
      `Project Settings` > `Data API` (or `API`), field `Project URL`, format
      `https://<project-ref>.supabase.co`.
- [ ] Copy the service-role key -> record as `SUPABASE_SERVICE_ROLE_KEY`.
      Found under `Project Settings` > `API Keys`. (Menu names may drift; the
      key to find is the SERVER-SIDE key that BYPASSES Row Level Security --
      current dashboards show it either as the legacy `service_role` key or
      as a new-style secret key (`sb_secret_...`). Either works; store it
      under the name `SUPABASE_SERVICE_ROLE_KEY`. NEVER use the
      `anon`/publishable key for this -- the whole RLS design in the Security
      section assumes only this key can write.)
- [ ] Create the model-artifact bucket: `Storage` > `New bucket`, name exactly
      `models`, and leave the `Public bucket` toggle OFF -- the bucket must be
      PRIVATE (it will hold LightGBM model artifacts written by the training
      job and read by the batch-predict function, both using the service-role
      key; design doc Premise #9).
- [ ] Confirm the bucket list shows `models` marked Private.

Notes:
- Do NOT create any tables now -- the schema (`carpark_history`,
  `carpark_baseline`, `carpark_momentum`, `carpark_forecast`, `model_config`,
  RLS policies) is T2's job.
- Free-tier projects pause after ~7 days without database activity (design
  doc Premise #8 rationale). If the code lanes have not landed within a week
  of provisioning, expect to click Restore in the dashboard before T2/T3 work
  begins.

## Phase 3: healthchecks.io -- two dead-man's-switch checks

Alerting is absence-based (design doc Premise #8): jobs prove liveness by
pinging; when pings stop, healthchecks.io emails you. Free tier: 20 checks,
email alerts included. Failure signaling needs no extra setup -- jobs append
`/fail` to the same ping URL to report hard failures with a reason.

- [ ] Create a free account at `https://healthchecks.io` using the email
      address where you want alerts delivered (you are the alert owner --
      solo project, no on-call rotation; design doc Security section).
- [ ] Create check 1: `Add Check`, then set:
      - Name: `gotparking-poller`
      - Schedule (Simple): Period = `5 minutes`, Grace = `30 minutes`
        (matches Premise #8: pings stop for >30 minutes -> email)
- [ ] Copy check 1's ping URL (format `https://hc-ping.com/<uuid>`) -> record
      as `HEALTHCHECKS_POLLER_PING_URL`.
- [ ] Create check 2: `Add Check`, then set:
      - Name: `gotparking-training`
      - Schedule (Simple): Period = `7 days` (1 week), Grace = `24 hours`
        (catches both training crashes and the job never running at all,
        e.g. GitHub's 60-day auto-disable -- Premise #8 / D2)
- [ ] Copy check 2's ping URL -> record as `HEALTHCHECKS_TRAINING_PING_URL`.
- [ ] Confirm email alerting is enabled on BOTH checks: each check's detail
      page lists the `Email to <your address>` integration as on (usually on
      by default for new checks; if missing, enable it under `Integrations`).
      (Menu names may drift; the setting to find is which notification
      channels each check uses.)
- [ ] Test the alert path for check 1:
      `curl -fsS <HEALTHCHECKS_POLLER_PING_URL>` (Git Bash; use `curl.exe` in
      PowerShell) -- expect the response `OK` and the check flipping to "up".
- [ ] Same test ping for `<HEALTHCHECKS_TRAINING_PING_URL>` -- expect `OK`.
- [ ] Now PAUSE both checks (Pause button on each check's page) so they do
      not fire DOWN emails while no poller or training job exists yet. A
      paused check resumes monitoring automatically when the next real ping
      arrives, so nothing needs to be done here when T3/T5 go live.

## Phase 4: Cloudflare -- Workers project + 5-minute cron

- [ ] Sign up / sign in at `https://dash.cloudflare.com` (Free plan; no
      domain required for Workers).
- [ ] If prompted, register your `workers.dev` subdomain (any handle you
      like; it only affects the dev URL).
- [ ] Create the Worker: `Workers & Pages` (may appear as `Compute (Workers)`)
      > `Create` > `Workers` > start from the `Hello World` template > name it
      exactly `gotparking-poller` > `Deploy`. (Menu names may drift; the goal
      is a deployed hello-world Worker named `gotparking-poller`.)
- [ ] Verify it responds: open
      `https://gotparking-poller.<your-subdomain>.workers.dev` -- expect the
      template's `Hello World!` response.
- [ ] Add the cron trigger: on the worker, `Settings` > `Triggers` (may
      appear as `Trigger Events`) > `Cron Triggers` > `Add`, expression
      exactly `*/5 * * * *` (every 5 minutes; supported on the free tier).
      If the dashboard warns the Worker has no scheduled handler, that is
      expected until T3 lands -- add the trigger anyway.
- [ ] Confirm the trigger list shows `*/5 * * * *`.

Notes:
- The Worker name matters: T3's wrangler deploy must target the same name
  `gotparking-poller` so it replaces the hello-world code while keeping this
  cron trigger and the Phase 6a secrets.
- Until T3 deploys real poller code, the cron invokes a no-op hello-world and
  pings nothing; healthchecks stays paused per Phase 3, so no false alarms.
- Worker secrets are wired in Phase 6a.

## Phase 5: Vercel -- project import + `sin1` region pin

Vercel's default serverless-function region is `iad1` (Washington, D.C.) --
it MUST be changed to `sin1` (Singapore). The Hobby plan allows pinning
exactly one region, and the pin lives in `vercel.json` in the repo (design
doc D7 / Premise #9 as amended).

- [ ] FIRST, pin the region in the repo so Vercel's very first deploy already
      carries it. In `C:\Users\kenzy\gstack-playground` on `main`, create the
      file `vercel.json` at the repo root with exactly this content:

      ```json
      {
        "regions": ["sin1"]
      }
      ```

- [ ] Commit and push it:
      - `git add vercel.json`
      - `git commit -m "chore: pin Vercel functions to sin1 (T1.5, D7)"`
      - `git push`
- [ ] Sign up / sign in at `https://vercel.com` using your GitHub account
      (Hobby plan, free) and grant it access to the `gotparking` repo when
      asked.
- [ ] `Add New...` > `Project` > Import `gotparking`.
- [ ] Configure the import: Framework Preset `Other`; leave Root Directory
      (`./`), Build Command, Output Directory, and Install Command at their
      defaults (T4/T6 will set real build config later). Click `Deploy`.
- [ ] Wait for the deployment to reach status `READY`. A 404 at the root URL
      is fine at this stage -- no frontend exists until T6 and no functions
      until T4; a READY deployment is the hello-world-level proof for this
      platform.
- [ ] Record the production URL shown on the project page (format
      `https://gotparking.vercel.app`, possibly with a suffix if the name was
      taken) -> record as `VERCEL_PROD_URL`. The future `BATCH_PREDICT_URL`
      will be `<VERCEL_PROD_URL>` plus the batch endpoint path once T4 lands
      (path is T4's to name, e.g. `/api/batch_predict`).
- [ ] Optional cross-check: `Project Settings` > `Functions` > set the
      function region to `Singapore (sin1)` so the dashboard agrees with the
      repo. (Menu names may drift; the committed `vercel.json` is the
      authoritative pin per the design doc, and it overrides the dashboard.)

Note: environment variables are wired in Phase 6c.

## Phase 6: Secrets wiring

The full matrix -- exact names, one row per secret/value:

| Secret / value                   | Cloudflare Worker secrets | GitHub Actions repo secrets | Vercel env vars |
|----------------------------------|---------------------------|-----------------------------|-----------------|
| `LTA_API_KEY`                    | x                         |                             |                 |
| `SUPABASE_URL`                   | x                         | x                           | x               |
| `SUPABASE_SERVICE_ROLE_KEY`      | x                         | x                           | x               |
| `BATCH_SHARED_SECRET`            | x                         |                             | x               |
| `HEALTHCHECKS_POLLER_PING_URL`   | x                         |                             |                 |
| `HEALTHCHECKS_TRAINING_PING_URL` |                           | x                           |                 |
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
Worker's encrypted environment variables.) CLI alternative:
`npx wrangler secret put <NAME> --name gotparking-poller` after
`npx wrangler login`.

- [ ] `LTA_API_KEY` = the value of `LTA_API_KEY` from your local `.env`
- [ ] `SUPABASE_URL` = the recorded value
- [ ] `SUPABASE_SERVICE_ROLE_KEY` = the recorded value
- [ ] `BATCH_SHARED_SECRET` = the value of `BATCH_SHARED_SECRET` from your
      local `.env`
- [ ] `HEALTHCHECKS_POLLER_PING_URL` = the recorded value
- [ ] Click `Deploy` / apply if the dashboard asks to deploy the changes.
- [ ] Deferred -- after T4's first deploy: `BATCH_PREDICT_URL` =
      `<VERCEL_PROD_URL>` + the batch endpoint path. Leave it unset today; do
      NOT block T1.5 completion on it. (Tracked again in the Hand-off list.)

### Phase 6b: GitHub Actions repository secrets (3)

Path: `github.com/<your-username>/gotparking` > `Settings` >
`Secrets and variables` > `Actions` > `New repository secret`.

- [ ] `SUPABASE_URL` = the recorded value
- [ ] `SUPABASE_SERVICE_ROLE_KEY` = the recorded value
- [ ] `HEALTHCHECKS_TRAINING_PING_URL` = the recorded value

### Phase 6c: Vercel environment variables (3)

Path: project `gotparking` > `Settings` > `Environment Variables` > `Add`.
Apply each to all environments (Production, Preview, Development); mark
`SUPABASE_SERVICE_ROLE_KEY` and `BATCH_SHARED_SECRET` as Sensitive if the
toggle is offered.

- [ ] `SUPABASE_URL` = the recorded value
- [ ] `SUPABASE_SERVICE_ROLE_KEY` = the recorded value
- [ ] `BATCH_SHARED_SECRET` = the value from your local `.env`

Note: Vercel env vars take effect from the NEXT deployment -- no redeploy is
needed today; T4's first real deploy will pick them up.

## Phase 7: Verification (T1.5 exit criteria)

Design doc Verify line for T1.5: "each platform reachable with its secret
from its runtime; healthchecks shows both checks; Supabase project region
reads ap-southeast-1; vercel.json pins sin1". Hello-world-level deploys are
acceptable proof.

- [ ] GitHub: the repo page shows the pushed tree AND the Phase 5
      `vercel.json` commit (proves you can push with your credentials).
- [ ] Supabase region: `Project Settings` > `General` (or `Infrastructure`)
      reads `ap-southeast-1` / `Southeast Asia (Singapore)`.
- [ ] Supabase reachable with its secret -- run in Git Bash, substituting
      your recorded values (never paste them into a file):

      ```
      curl -s -o /dev/null -w "%{http_code}\n" \
        -H "apikey: <SUPABASE_SERVICE_ROLE_KEY>" \
        -H "Authorization: Bearer <SUPABASE_SERVICE_ROLE_KEY>" \
        "<SUPABASE_URL>/rest/v1/"
      ```

      Expect `200`. A 401/403 means the wrong key was copied -- go back to
      Phase 2 and re-copy the service-role key.
- [ ] Supabase Storage: bucket `models` exists and is marked Private.
- [ ] healthchecks.io: the dashboard lists exactly two checks --
      `gotparking-poller` (period 5 minutes, grace 30 minutes) and
      `gotparking-training` (period 7 days, grace 24 hours) -- both received
      one manual `OK` test ping, and both are currently Paused.
- [ ] Cloudflare: `https://gotparking-poller.<your-subdomain>.workers.dev`
      returns the hello-world response (platform reachable, deploy works).
- [ ] Cloudflare: the worker's cron trigger list shows `*/5 * * * *`.
- [ ] Cloudflare: `Variables and Secrets` lists exactly these 5 names (values
      hidden): `LTA_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `BATCH_SHARED_SECRET`, `HEALTHCHECKS_POLLER_PING_URL`.
      (`BATCH_PREDICT_URL` intentionally absent until T4.)
- [ ] GitHub: `Settings` > `Secrets and variables` > `Actions` lists exactly
      these 3 names: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `HEALTHCHECKS_TRAINING_PING_URL`.
- [ ] Vercel: the latest deployment status is `READY` and the project is
      linked to the `gotparking` GitHub repo.
- [ ] Vercel: `Settings` > `Environment Variables` lists exactly these 3
      names: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `BATCH_SHARED_SECRET`.
- [ ] `vercel.json` at the repo root on `main` contains exactly
      `{"regions": ["sin1"]}` -- check with `git show origin/main:vercel.json`
      or view the file on GitHub.
- [ ] Local `.env` is still untracked: `git ls-files .env` prints nothing.
- [ ] Every value in the Hand-off state list below is recorded in your
      scratch note / password manager.

If every box above is checked, T1.5 is complete: Lane A (T2, Supabase schema)
is unblocked, and Lanes B/C/D/E can launch after it.

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
8.  `VERCEL_PROD_URL` -- base of the future `BATCH_PREDICT_URL`.
9.  `BATCH_PREDICT_URL` -- PENDING: `<VERCEL_PROD_URL>` + batch endpoint
    path, known only after T4's first deploy; when known, set it as the 6th
    Cloudflare Worker secret (the Phase 6a deferred item).

Platform state at hand-off: GitHub repo `gotparking` pushed with `main`;
Supabase project `gotparking` in `ap-southeast-1` with private Storage bucket
`models` (no tables yet -- T2's job); healthchecks.io checks
`gotparking-poller` and `gotparking-training` created, tested, and paused
(they auto-resume on the first real ping); Cloudflare Worker
`gotparking-poller` (hello-world) with cron `*/5 * * * *`; Vercel project
linked to the repo with `sin1` pinned via `vercel.json`; 11 secret/value
wirings in place (5 Cloudflare + 3 GitHub + 3 Vercel), with
`BATCH_PREDICT_URL` as the single deferred wiring.
