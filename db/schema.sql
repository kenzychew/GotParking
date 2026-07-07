-- ============================================================================
-- GotParking Supabase schema (T2)
-- ============================================================================
-- Apply: paste this whole file into the Supabase SQL Editor and Run, after the
-- project exists (provisioning checklist Phase 2). Idempotent: safe to re-run
-- (create table if not exists / on conflict do nothing). Additive-only during
-- MVP per the design doc's rollback posture -- no destructive migrations.
--
-- Time semantics (design doc D3): every timestamp column is timestamptz and
-- stores UTC. All time-derived features (time slots, day-of-week, holidays)
-- are computed in Asia/Singapore by application code via the shared SGT
-- helpers -- NEVER by SQL date functions on these columns.
--
-- Security (design doc, Security section): RLS is enabled on every table with
-- NO policies -- deny-by-default for the anon and authenticated roles. The
-- poller, training job, and batch-predict function all use the service-role
-- key, which bypasses RLS. The frontend never talks to Supabase directly.
--
-- Two tables go beyond the design doc's literal T2 list, each serving a
-- reviewed requirement:
--   * carparks       -- server-side canonical seed whitelist + FK integrity +
--                       the SINPA coordinate-mapping home (D13). The frontend
--                       keeps its own static 10-mall list per Design Details.
--   * training_runs  -- the Observability section promises "promotion
--                       history"; a single-row model_config cannot hold one.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) carparks -- seed whitelist (LTA DataMall CarParkID is a string)
-- ----------------------------------------------------------------------------
create table if not exists public.carparks (
    carpark_id       text primary key,
    name             text not null,
    -- SINPA dataset column index for pretraining (D13); null = absent from
    -- SINPA (Raffles City, VivoCity P2) -> that carpark trains live-only.
    sinpa_index      integer,
    active           boolean not null default true,
    -- One-time training-eligibility gate (T2): true for the original 10
    -- validated seed carparks only -- any carpark added later via the
    -- coverage-expansion whitelist script gets the default false, and is
    -- excluded from the pooled training run until model_config.first_promotion_at
    -- is set (see model_config below).
    is_original_seed boolean not null default false,
    created_at       timestamptz not null default now()
);

comment on table public.carparks is
    'Seed carpark whitelist (server-side canonical copy). Expansion beyond the '
    'initial 10 requires a fresh T1-style signal validation per carpark.';

-- ----------------------------------------------------------------------------
-- 2) carpark_history -- every successful poll lands here
-- ----------------------------------------------------------------------------
create table if not exists public.carpark_history (
    carpark_id     text not null references public.carparks (carpark_id),
    polled_at      timestamptz not null,
    available_lots integer not null check (available_lots >= 0),
    -- Composite PK doubles as the D4 idempotency guard: the poller writes with
    -- ON CONFLICT DO NOTHING, so a retry after an ambiguous timeout can never
    -- double-insert (which would inflate cold-start sample counts).
    primary key (carpark_id, polled_at)
);

comment on table public.carpark_history is
    'Raw polled lot counts, UTC timestamps. The single source of truth for '
    'training data, baselines, and the cold-start sample count (Premise #10 '
    'counts THESE rows only -- never SINPA pretraining data).';

-- ----------------------------------------------------------------------------
-- 3) carpark_baseline -- daily precompute (Premise #11)
-- ----------------------------------------------------------------------------
-- The design doc's "time_slot" is implemented as (dow, slot_of_day) in SGT for
-- debuggability (D3): dow 0=Monday..6=Sunday, slot_of_day 0..95 in 15-minute
-- slots. Weekend-vs-weekday is the strongest mall-parking signal, so the
-- baseline must key on day-of-week, not time-of-day alone. 15-minute
-- granularity pools 3 polls per slot per day (~9 samples per cell after 3
-- weeks) -- fine-grained enough for a 20-minute horizon, coarse enough to
-- average meaningfully.
create table if not exists public.carpark_baseline (
    carpark_id         text not null references public.carparks (carpark_id),
    dow                smallint not null check (dow between 0 and 6),
    slot_of_day        smallint not null check (slot_of_day between 0 and 95),
    avg_available_lots real not null check (avg_available_lots >= 0),
    sample_count       integer not null check (sample_count > 0),
    updated_at         timestamptz not null default now(),
    primary key (carpark_id, dow, slot_of_day)
);

comment on table public.carpark_baseline is
    'Historical-average-by-time-slot baseline, precomputed daily so serving '
    'never aggregates the growing history table (Premise #11). Slots are SGT. '
    'NOTE (D6): promotion backtests must NOT read this table -- the training '
    'job recomputes a leakage-free comparator from pre-holdout data.';

-- ----------------------------------------------------------------------------
-- 4) carpark_momentum -- poller-written rate-of-change inputs (Premises #2/#11)
-- ----------------------------------------------------------------------------
create table if not exists public.carpark_momentum (
    carpark_id   text primary key references public.carparks (carpark_id),
    lots_15m_ago integer check (lots_15m_ago >= 0),
    lots_30m_ago integer check (lots_30m_ago >= 0),
    lots_60m_ago integer check (lots_60m_ago >= 0),
    updated_at   timestamptz not null default now()
);

comment on table public.carpark_momentum is
    'One row per carpark, upserted by the poller each cycle. Freshness guard '
    '(D5): the batch-predict run treats a row with updated_at older than ~15 '
    'minutes as missing and serves that carpark via the baseline path. '
    'Training never reads this table -- it reconstructs momentum from '
    'carpark_history offsets.';

-- ----------------------------------------------------------------------------
-- 5) carpark_forecast -- batch-written serving table (D10)
-- ----------------------------------------------------------------------------
create table if not exists public.carpark_forecast (
    carpark_id    text primary key references public.carparks (carpark_id),
    state         text not null check (state in ('ml', 'baseline', 'cold_start')),
    forecast_lots integer check (forecast_lots >= 0),
    tier          text check (tier in ('plenty', 'limited', 'very_limited')),
    live_lots     integer not null check (live_lots >= 0),
    model_version text,
    generated_at  timestamptz not null default now(),
    -- State invariant: cold_start rows carry no forecast/tier (the UI shows
    -- the live count + "collecting data"); ml/baseline rows carry both.
    constraint carpark_forecast_shape check (
        (state = 'cold_start' and forecast_lots is null and tier is null)
        or (state in ('ml', 'baseline') and forecast_lots is not null and tier is not null)
    )
);

comment on table public.carpark_forecast is
    'Current forecast per carpark, upserted by the secret-gated batch-predict '
    'run after each poll cycle (D10). The public read endpoint serves this '
    'whole table as one cached payload. Tier thresholds are capacity-relative '
    'per Design Details. Frontend shows a "data delayed" caveat when '
    'generated_at exceeds ~15 minutes.';

-- ----------------------------------------------------------------------------
-- 6) model_config -- single-row pointer to the serving model (Premise #7)
-- ----------------------------------------------------------------------------
create table if not exists public.model_config (
    singleton            boolean primary key default true check (singleton),
    -- null = no promoted model yet -> batch predict serves baseline-only.
    active_model_version text,
    promoted_at          timestamptz,
    -- Tracks the first-ever system-wide promotion (T2); NULL means no
    -- promotion has happened yet. Once set, it is never cleared/overwritten
    -- by later retrains -- it opens the training-eligibility gate for every
    -- non-seed carpark permanently, it does not re-track "most recent".
    first_promotion_at   timestamptz,
    updated_at           timestamptz not null default now()
);

comment on table public.model_config is
    'Single row (PK forces it). Flipping active_model_version is the entire '
    'promotion mechanism -- the batch-predict run reloads the artifact from '
    'Storage when the version changes; no redeploy (Premise #7).';

-- ----------------------------------------------------------------------------
-- 7) training_runs -- weekly-cycle audit log (Observability: promotion history)
-- ----------------------------------------------------------------------------
create table if not exists public.training_runs (
    id                bigint generated always as identity primary key,
    ran_at            timestamptz not null default now(),
    candidate_version text not null,
    phase             text not null check (phase in ('first_promotion', 'retrain')),
    mae_candidate     real,
    mae_baseline      real,
    mae_persistence   real,
    mae_incumbent     real,
    used_sinpa        boolean not null default false,
    promoted          boolean not null,
    notes             text
);

comment on table public.training_runs is
    'One row per weekly training cycle, promoted or not. MAE-vs-persistence '
    'is the tracked demo-facing metric (D8); phase records which gate applied '
    '(D9: 10% for first_promotion, worse-than-2%-rejection for retrain).';

-- ----------------------------------------------------------------------------
-- 8) Row Level Security -- deny-by-default (no policies on purpose)
-- ----------------------------------------------------------------------------
alter table public.carparks         enable row level security;
alter table public.carpark_history  enable row level security;
alter table public.carpark_baseline enable row level security;
alter table public.carpark_momentum enable row level security;
alter table public.carpark_forecast enable row level security;
alter table public.model_config     enable row level security;
alter table public.training_runs    enable row level security;

-- Belt and braces on top of RLS: strip the default PostgREST role grants so
-- anon/authenticated cannot touch these tables even if a permissive policy is
-- ever added by mistake. service_role is unaffected (bypasses RLS, has its
-- own grants).
revoke all on table public.carparks         from anon, authenticated;
revoke all on table public.carpark_history  from anon, authenticated;
revoke all on table public.carpark_baseline from anon, authenticated;
revoke all on table public.carpark_momentum from anon, authenticated;
revoke all on table public.carpark_forecast from anon, authenticated;
revoke all on table public.model_config     from anon, authenticated;
revoke all on table public.training_runs    from anon, authenticated;
revoke usage, select on all sequences in schema public from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 9) Storage bucket for model artifacts (private; Premise #9)
-- ----------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('models', 'models', false)
on conflict (id) do nothing;

-- ----------------------------------------------------------------------------
-- 10) Seed data
-- ----------------------------------------------------------------------------
-- The 10 validated seed carparks (T1) with their SINPA column indices (T0/D13;
-- exact 0.0m coordinate matches). Raffles City and VivoCity P2 are absent from
-- SINPA -> sinpa_index null -> live-only training.
insert into public.carparks (carpark_id, name, sinpa_index, is_original_seed) values
    ('1',  'Suntec City',    1584, true),
    ('2',  'Marina Square',  1593, true),
    ('3',  'Raffles City',   null, true),
    ('11', 'Cineleisure',    1585, true),
    ('13', 'Ngee Ann City',  1587, true),
    ('15', 'Wheelock Place', 1589, true),
    ('16', 'VivoCity P3',    1590, true),
    ('21', 'Centrepoint',    1595, true),
    ('24', '313@Somerset',   1597, true),
    ('50', 'VivoCity P2',    null, true)
on conflict (carpark_id) do nothing;

-- Singleton config row: no promoted model yet -> baseline-only serving.
insert into public.model_config (singleton, active_model_version)
values (true, null)
on conflict (singleton) do nothing;
