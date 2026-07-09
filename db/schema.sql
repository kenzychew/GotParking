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

-- Coverage-expansion wave 1 (2026-07-08): 14 mall carparks verified via
-- scripts/build_mall_whitelist.py's T1-style variance validation (18
-- candidates found, 14 verified, 3 rejected for insufficient lot-count
-- variance, 1 excluded as a confirmed fuzzy-match false positive -- see
-- TODOS.md's "Expand to all/hundreds of SG carparks" entry for the full
-- breakdown). None have a SINPA mapping (that was a one-time, T0-era
-- exact-coordinate match against only the original 10) -> sinpa_index
-- null for all. is_original_seed defaults to false, set explicitly here
-- for clarity. Applied to production via this same INSERT shape on
-- 2026-07-08 (scripts/build_mall_whitelist.py's render_sql_inserts); this
-- block closes the reproducibility gap that left them un-captured in a
-- fresh schema.sql apply until now.
insert into public.carparks (carpark_id, name, sinpa_index, is_original_seed) values
    ('5',  'Millenia Singapore',   null, false),
    ('7',  'Orchard Point',        null, false),
    ('8',  'The Heeren',           null, false),
    ('9',  'Plaza Singapura',      null, false),
    ('10', 'The Cathay',           null, false),
    ('14', 'Wisma Atria',          null, false),
    ('19', 'Harbourfront Centre',  null, false),
    ('20', 'Far East Plaza',       null, false),
    ('23', 'ION Orchard',          null, false),
    ('27', 'Orchard Central',      null, false),
    ('43', 'Westgate',             null, false),
    ('53', 'IMM Building',         null, false),
    ('63', 'Tampines Mall',        null, false),
    ('65', 'Bedok Mall',           null, false)
on conflict (carpark_id) do nothing;

-- Coverage-expansion wave 2 (2026-07-09): 244 carparks verified from the full LTA feed (no
-- fuzzy-matching needed for this wave -- every remaining LTA carpark is its own direct
-- candidate; see scripts/recon_full_feed.py). Of 359 candidates observed over a 6-hour
-- window, 244 verified and 115 rejected for insufficient lot-count variance -- see TODOS.md's
-- "Expand to all/hundreds of SG carparks" entry for the full breakdown. Carpark IDs are not
-- all numeric here -- LTA's real CarParkID scheme also uses area-letter-prefixed alphanumeric
-- IDs (e.g. "A0007"), which `carparks.carpark_id text` already supported natively; only
-- downstream tooling (scripts/regen_seed_lists.py) had to be updated to stop assuming
-- digits-only. No SINPA mapping (same as wave 1) -> sinpa_index null for all.
insert into public.carparks (carpark_id, name, sinpa_index, is_original_seed) values
    ('4', 'Esplanade', null, false),
    ('12', 'Hilton Orchard', null, false),
    ('17', 'Sentosa', null, false),
    ('18', 'Tang Plaza', null, false),
    ('26', 'Resorts World', null, false),
    ('52', 'Orchard Gateway', null, false),
    ('55', 'Paragon', null, false),
    ('56', 'National Gallery', null, false),
    ('59', 'CQ @ Clarke Quay', null, false),
    ('61', 'Bugis+', null, false),
    ('62', 'Lot One', null, false),
    ('66', 'Funan Mall', null, false),
    ('A0017', 'ARAB STREET - QUEEN STREET OFF STREET', null, false),
    ('A0024', 'ADAM ROAD FOOD CENTRE OFF STREET', null, false),
    ('A0035', 'ANTHONY ROAD OFF STREET', null, false),
    ('A11', 'BLK 223/226/226A-226D ANG MO KIO STREET 22', null, false),
    ('A12', 'BLK 229/230 ANG MO KIO STREET 22', null, false),
    ('A13', 'BLK 232/233 ANG MO KIO STREET 22', null, false),
    ('A15', 'BLK 226E-226H ANG MO KIO STREET 22', null, false),
    ('A24', 'BLK 338/340 ANG MO KIO STREET 32', null, false),
    ('A25', 'BLK 330/337 ANG MO KIO AVENUE 8', null, false),
    ('A26', 'BLK 113/114/118 ANG MO KIO AVENUE 4', null, false),
    ('A27', 'BLK 108/109/110 ANG MO KIO STREET 11', null, false),
    ('A28', 'BLK 103/105/107 ANG MO KIO STREET 11', null, false),
    ('A31', 'BLK 119/128 ANG MO KIO STREET 12', null, false),
    ('A34', 'BLK 422/425 ANG MO KIO STREET 42', null, false),
    ('A35', 'BLK 426/428/435 ANG MO KIO STREET 43', null, false),
    ('A36', 'BLK 436/443/445 ANG MO KIO STREET 43', null, false),
    ('A37', 'BLK 446/449/453 ANG MO KIO STREET 43', null, false),
    ('A38', 'BLK 407/410/421 ANG MO KIO AVENUE 10', null, false),
    ('A39', 'BLK 401/405 ANG MO KIO AVENUE 10', null, false),
    ('A4', 'BLK 217/220 ANG MO KIO AVENUE 1', null, false),
    ('A40', 'BLK 471/476 ANG MO KIO STREET 44', null, false),
    ('A41', 'BLK 466/470 ANG MO KIO STREET 44', null, false),
    ('A43', 'BLK 457/458/460 ANG MO KIO STREET 44', null, false),
    ('A44', 'BLK 459/456 ANG MO KIO STREET 44', null, false),
    ('A45', 'BLK 570/578 ANG MO KIO STREET 51', null, false),
    ('A47', 'BLK 562/565/560 ANG MO KIO STREET 54', null, false),
    ('A49', 'BLK 555/559 ANG MO KIO STREET 54', null, false),
    ('A50', 'BLK 548/552/556 ANG MO KIO STREET 54', null, false),
    ('A52', 'BLK 701/716 ANG MO KIO AVENUE 3/6', null, false),
    ('A53', 'BLK 712A ANG MO KIO AVENUE 3/6', null, false),
    ('A55', 'BLK 729/730 ANG MO KIO AVENUE 6/8', null, false),
    ('A59', 'BLK 547/551 ANG MO KIO STREET 54', null, false),
    ('A60', 'BLK 540/546 ANG MO KIO STREET 54', null, false),
    ('A61', 'BLK 520/534/529 ANG MO KIO AVENUE 5/10', null, false),
    ('A64', 'BLK 584/586 ANG MO KIO STREET 51', null, false),
    ('A66', 'BLK 601/604/603 ANG MO KIO AVENUE 5', null, false),
    ('A67', 'BLK 605/612 ANG MO KIO AVENUE 4/5', null, false),
    ('A68', 'BLK 620/624/626 ANG MO KIO STREET 61', null, false),
    ('A69', 'BLK 623/625/627 ANG MO KIO STREET 61', null, false),
    ('A7', 'BLK 212/213 ANG MO KIO STREET 23', null, false),
    ('A70', 'BLK 629/626 ANG MO KIO AVENUE 4', null, false),
    ('A71', 'BLK 628/632 ANG MO KIO STREET 61', null, false),
    ('A72', 'BLK 633/640 ANG MO KIO STREET 61', null, false),
    ('A73', 'BLK 641/645 ANG MO KIO STREET 61', null, false),
    ('A74', 'BLK 646/649 ANG MO KIO STREET 61', null, false),
    ('A75', 'BLK 177/182 ANG MO KIO AVENUE 4', null, false),
    ('A76', 'BLK 613A ANG MO KIO AVENUE 4', null, false),
    ('A8', 'BLK 209/210 ANG MO KIO STREET 22', null, false),
    ('A81', 'BLK 170/172 ANG MO KIO STREET 13', null, false),
    ('A82', 'BLK 173/176 ANG MO KIO AVENUE 4', null, false),
    ('A85', 'BLK 340 ANG MO KIO STREET 32', null, false),
    ('A9', 'BLK 202/203 ANG MO KIO STREET 22', null, false),
    ('A94', 'BLK 104C ANG MO KIO STREET 11', null, false),
    ('ACB', 'BLK 270/271 ALBERT CENTRE BASEMENT CAR PARK', null, false),
    ('AH1', 'BLK 101 JALAN DUSUN', null, false),
    ('AV1', 'BLK 120/120A/121-127 ALEXANDRA VILLAGE', null, false),
    ('B10', 'BLK 404/413 BEDOK NORTH AVENUE 3', null, false),
    ('B11', 'BLK 416/418 BEDOK NORTH AVENUE 2', null, false),
    ('B16', 'BLKS 59, 62-65 NEW UPPER CHANGI ROAD', null, false),
    ('B17', 'BLK 52/57 NEW UPPER CHANGI ROAD', null, false),
    ('B19', 'BLK 36-44, 60 BEDOK SOUTH ROAD', null, false),
    ('B20', 'BLK 67/73 BEDOK SOUTH ROAD', null, false),
    ('B21', 'BLK 74/82 BEDOK NORTH ROAD', null, false),
    ('B25', 'BLK 91/97 BEDOK NORTH AVENUE 4', null, false),
    ('B26', 'BLK 98/100 BEDOK NORTH AVENUE 4', null, false),
    ('B27', 'BLK 101/106 BEDOK NORTH AVENUE 4', null, false),
    ('B28', 'BLK 107/110 BEDOK NORTH ROAD', null, false),
    ('B30', 'BLK 119/123 BEDOK NORTH STREET 2', null, false),
    ('B31', 'BLK 124/129 BEDOK NORTH STREET 2', null, false),
    ('B32', 'BLK 130/132 BEDOK NORTH STREET 2', null, false),
    ('B33', 'BLK 131/133 BEDOK NORTH AVENUE 3', null, false),
    ('B34', 'BLK 134/136 BEDOK NORTH AVENUE 3', null, false),
    ('B35', 'BLK 137/140 BEDOK NORTH AVENUE 3', null, false),
    ('B40', 'BLK 504/508 BEDOK NORTH STREET 3', null, false),
    ('B42', 'BLK 509/511 BEDOK NORTH STREET 3', null, false),
    ('B43', 'BLK 519/522 553 BEDOK NORTH AVENUE 1/2', null, false),
    ('B44', 'BLK 528/536 BEDOK NORTH STREET 3', null, false),
    ('B45', 'BLK 537/539 BEDOK NORTH STREET 3', null, false),
    ('B46', 'BLK 537 BEDOK NORTH STREET 3', null, false),
    ('B47', 'BLK 540/542 BEDOK NORTH STREET 3', null, false),
    ('B48', 'BLK 543/547 BEDOK NORTH STREET 3', null, false),
    ('B49', 'BLK 549/551 BEDOK NORTH AVENUE 1', null, false),
    ('B50', 'BLK 601/605 BEDOK RESERVOIR ROAD', null, false),
    ('B51', 'BLK 611/616 BEDOK RESERVOIR ROAD', null, false),
    ('B52', 'BLK 617/624 BEDOK RESERVOIR ROAD', null, false),
    ('B53', 'BLK 625/629 BEDOK RESERVOIR ROAD', null, false),
    ('B54', 'BLK 630/632 BEDOK RESERVOIR ROAD', null, false),
    ('B57', 'BLK 701/708 BEDOK RESERVOIR ROAD', null, false),
    ('B59', 'BLK 716/718/721 BEDOK RESERVOIR ROAD', null, false),
    ('B6', 'BLK 204/209 NEW UPPER CHANGI ROAD', null, false),
    ('B60', 'BLK 722/725 BEDOK RESERVOIR ROAD', null, false),
    ('B65', 'BLK 155/172 BEDOK SOUTH ROAD/AVENUE 3', null, false),
    ('B66', 'BLK 637A BEDOK RESERVOIR ROAD', null, false),
    ('B67', 'BLK 649A JALAN TENAGA', null, false),
    ('B68', 'BLK 10A BEDOK SOUTH AVENUE 2', null, false),
    ('B69', 'BLK 94A BEDOK NORTH AVENUE 4', null, false),
    ('B7', 'BLK 211/218 BEDOK NORTH STREET 1', null, false),
    ('B71', 'BLK 29C CHAI CHEE AVENUE', null, false),
    ('B73', 'BLK 651A JALAN TENAGA', null, false),
    ('B79', 'BLK 772A BEDOK RESERVOIR VIEW', null, false),
    ('B7A', 'BLK 216/218 BEDOK NORTH STREET 1', null, false),
    ('B8', 'BLK 201/203 BEDOK NORTH STREET 1', null, false),
    ('B81', 'BLK 30A NEW UPPER CHANGI ROAD', null, false),
    ('B84', 'BLK 215A BEDOK CENTRAL', null, false),
    ('B85', 'BLK 184 BEDOK NORTH ROAD', null, false),
    ('B86', 'BLK 114A BEDOK NORTH STREET 2', null, false),
    ('B88', 'BLK 34A BED0K SOUTH AVENUE 2', null, false),
    ('B89', 'BLK 116A BEDOK NORTH ROAD', null, false),
    ('B8B', 'BLK 222 BEDOK NORTH DRIVE', null, false),
    ('B9', 'BLK 402/403 BEDOK NORTH AVENUE 3', null, false),
    ('B91', 'BLK 630 BEDOK RESERVOIR ROAD', null, false),
    ('B92', 'BLK 2A BEDOK SOUTH AVENUE 1', null, false),
    ('B94', 'BLK 513A BEDOK NORTH AVENUE 2', null, false),
    ('B95', 'BLK 220 BEDOK CENTRAL', null, false),
    ('B96', 'BLK 219 BEDOK CENTRAL', null, false),
    ('B97', 'BLK 714A BEDOK RESERVOIR ROAD', null, false),
    ('B98', 'BLK 748 BEDOK RESERVOIR CRESCENT', null, false),
    ('BA1', 'BLK 106 BIDADARI PARK DRIVE', null, false),
    ('BA2', 'BLK 117 ALKAFF CRESCENT', null, false),
    ('BA3', 'BLK 101 BIDADARI PARK DRIVE', null, false),
    ('BA4', 'BLK 113 ALKAFF CRESCENT', null, false),
    ('BA6', 'BLK 201 WOODLEIGH LINK', null, false),
    ('BA7', 'BLK 206 WOODLEIGH LINK', null, false),
    ('BA8', 'BLK 207 WOODLEIGH LINK', null, false),
    ('BA9', 'BLK 212 BIDADARI PARK DRIVE', null, false),
    ('BBB', 'BLK 231 BRAS BASAH BASEMENT CAR PARK', null, false),
    ('BE3', 'BLK 401-408 SIN MING AVENUE', null, false),
    ('BE4', 'BLK 101-116 BISHAN STREET 12', null, false),
    ('BE5', 'BLK 117-134 BISHAN STREET 12', null, false),
    ('BE6', 'BLK 135/138 BISHAN STREET 12', null, false),
    ('BE7', 'BLK 139-144 BISHAN STREET 12', null, false),
    ('BE8', 'BLK 145-150A, 151 BISHAN STREET 11', null, false),
    ('BE9', 'BLK 153-167 BISHAN STREET 13', null, false),
    ('BH1', 'BLK 1 THOMSON ROAD', null, false),
    ('BH2', 'BLK 2 BALESTIER ROAD', null, false),
    ('BJ1', 'BLK 130-140 CASHEW ROAD', null, false),
    ('BJ2', 'BLK 141-151 GANGSA ROAD/PETIR ROAD', null, false),
    ('BJ3', 'BLK 101/129 GANGSA ROAD/PENDING ROAD', null, false),
    ('BJ4', 'BLK 219/233 PETIR ROAD', null, false),
    ('BJ8', 'BLK 201/218 PETIR ROAD', null, false),
    ('BL3', 'BLK 174/179 BOON LAY DRIVE', null, false),
    ('BL8', 'BLK 221 BOON LAY PLACE', null, false),
    ('BLM', 'BLK 10 BENDEMEER ROAD', null, false),
    ('BM1', 'BLK 28 JALAN BUKIT MERAH', null, false),
    ('BM4', 'BLK 35A JALAN RUMAH TINGGI', null, false),
    ('BP1', 'BLK 101/109 BUKIT PURMEI ROAD', null, false),
    ('BP2', 'BLK 110/115 BUKIT PURMEI ROAD', null, false),
    ('BR4', 'BLK 81/89 WHAMPOA DRIVE', null, false),
    ('BR6', 'BLK 92 WHAMPOA DRIVE', null, false),
    ('BR8', 'BLK 76/77 LORONG LIMAU', null, false),
    ('BRM', 'BLK 39 BENDEMEER ROAD', null, false),
    ('BWM', 'BLK 2A JALAN BUKIT MERAH', null, false),
    ('C11', 'BLK 321-322,324-326 CLEMENTI AVENUE 5', null, false),
    ('C16', 'BLK 430-435 CLEMENTI AVENUE 3', null, false),
    ('C18', 'BLK 426/428 CLEMENTI AVENUE 3', null, false),
    ('C20', 'BLK 449-451 CLEMENTI AVENUE 3', null, false),
    ('C6', 'BLK 328-334 CLEMENTI AVENUE 2', null, false),
    ('C7', 'BLK 349-355 CLEMENTI AVENUE 2', null, false),
    ('C8', 'BLK 335/338 CLEMENTI AVENUE 2', null, false),
    ('E0024', 'EAST COAST PARK E2 OFF STREET', null, false),
    ('E0027', 'EAST COAST PARK E1 OFF STREET', null, false),
    ('H4', 'BLK 22 HAVELOCK ROAD', null, false),
    ('H6', 'BLK 77/79 GANGES AVENUE', null, false),
    ('J0017', 'JOO CHIAT ROAD OFF STREET', null, false),
    ('J0055', 'JALAN SEH CHUAN OFF STREET', null, false),
    ('J0100', 'JALAN PELEPAH OFF STREET', null, false),
    ('J0122', 'JALAN KAYU OFF STREET', null, false),
    ('J1', 'BLK 101/107 JURONG EAST STREET 13', null, false),
    ('J2', 'BLK 108/110 JURONG EAST STREET 13', null, false),
    ('J3', 'BLK 111/116 JURONG EAST STREET 13', null, false),
    ('J4', 'BLK 201/206 JURONG EAST STREET 21', null, false),
    ('J5', 'BLK 207/208 JURONG EAST STREET 21', null, false),
    ('J6', 'BLK 209/214 JURONG EAST STREET 21', null, false),
    ('J7', 'BLK 215A/231 JURONG EAST STREET 21', null, false),
    ('J8', 'BLK 232/240 JURONG EAST STREET 21', null, false),
    ('J9', 'BLK 241/245 JURONG EAST STREET 24', null, false),
    ('K0039', 'KING GEORGES AVENUE- HORNE ROAD OFF STREET', null, false),
    ('K0111', 'KINTA ROAD OFF STREET', null, false),
    ('L0078', 'LORONG MYDIN OFF STREET', null, false),
    ('L0117', 'LORONG 25-25A GEYLANG OFF STREET', null, false),
    ('L0123', 'LORONG 9-11 GEYLANG OFF STREET', null, false),
    ('L0125', 'LORONG 31 GEYLANG OFF STREET', null, false),
    ('L1', 'BLK 415-420 LORONG LEW LIAN', null, false),
    ('M0076', 'MINBU ROAD OFF STREET', null, false),
    ('M0078', 'MARITIME SQUARE D OFF STREET', null, false),
    ('M3', 'BLK 19 TO 21/23/24/30/32 BALAM ROAD', null, false),
    ('M4', 'BLK 22/34/36 CIRCUIT ROAD', null, false),
    ('P0054', 'PASIR PANJANG FOOD CENTRE OFF STREET', null, false),
    ('P0106', 'PASIR PANJANG ROAD- CLEMENTI ROAD OFF ST', null, false),
    ('P0113', 'PASIR PANJANG FOOD CENTRE (TEMP) OFF ST', null, false),
    ('P0117', 'PECK SEAH STREET OFF STREET', null, false),
    ('P1', 'BLK 196 PUNGGOL FIELD', null, false),
    ('P2', 'BLK 199 PUNGGOL FIELD', null, false),
    ('P3', 'BLK 101 PUNGGOL FIELD', null, false),
    ('P4', 'BLK 105 EDGEFIELD PLAINS', null, false),
    ('P5', 'BLK 107 PUNGGOL FIELD', null, false),
    ('P6', 'BLK 109 PUNGGOL FIELD', null, false),
    ('P7', 'BLK 119 EDGEFIELD PLAINS', null, false),
    ('P8', 'BLK 126 EDGEDALE PLAINS', null, false),
    ('P9', 'BLK 128 PUNGGOL FIELD WALK', null, false),
    ('S0055', 'SERANGOON GARDEN MARKET OFF STREET', null, false),
    ('S0112', 'SEAH IM ROAD OFF STREET', null, false),
    ('S0166', 'SEMBAWANG RD-ADMIRALTY RD EAST OFF STREET', null, false),
    ('T0103', 'TEMBELING ROAD OFF STREET', null, false),
    ('T0140', 'TANJONG KATONG ROAD OFF STREET', null, false),
    ('T1', 'BLK 101/108 TAMPINES STREET 11', null, false),
    ('T3', 'BLK 109/114 TAMPINES STREET 11', null, false),
    ('T4', 'BLK 124/127 TAMPINES STREET 11', null, false),
    ('T7', 'BLK 136/138 TAMPINES STREET 11', null, false),
    ('T8', 'BLK 137/139 TAMPINES STREET 11', null, false),
    ('T9', 'BLK 140/148 TAMPINES STREET 12', null, false),
    ('U0036', 'UPPER EAST COAST ROAD OFF STREET', null, false),
    ('U0042', 'UPPER CHANGI ROAD - BEDOK ROAD OFF STREET', null, false),
    ('U1', 'BLK 101/110 BUKIT BATOK WEST AVENUE 6', null, false),
    ('U2', 'BLK 111/132 BUKIT BATOK WEST AVENUE 6', null, false),
    ('U3', 'BLK 133/139 BUKIT BATOK WEST AVENUE 6', null, false),
    ('U4', 'BLK 140/143 BUKIT BATOK STREET 11', null, false),
    ('U7', 'BLK 159/164 BUKIT BATOK STREET 11', null, false),
    ('U8', 'BLK 165/168 BUKIT BATOK WEST AVENUE 8', null, false),
    ('U9', 'BLK 169/177 BUKIT BATOK WEST AVENUE 8', null, false),
    ('W5', 'BLK 10/13 MARSILING LANE', null, false),
    ('W6', 'BLK 15/16 MARSILING LANE', null, false),
    ('W7', 'BLK 17/21 MARSILING LANE', null, false),
    ('Y1', 'BLK 101/102 YISHUN AVENUE 5', null, false),
    ('Y2', 'BLK 103/111 YISHUN RING ROAD', null, false),
    ('Y3', 'BLK 112/120 YISHUN RING ROAD', null, false),
    ('Y4', 'BLK 121/143 YISHUN STREET 11/RING ROAD', null, false),
    ('Y5', 'BLK 144/149 YISHUN STREET 11', null, false),
    ('Y6', 'BLK 150/161 YISHUN STREET 11', null, false),
    ('Y7', 'BLK 701/716 & 701B YISHUN AVENUE 5', null, false),
    ('Y8', 'BLK 731/746 YISHUN STREET 71/72/AVENUE 5', null, false),
    ('Y9', 'BLK 747/752 YISHUN STREET 72', null, false)
on conflict (carpark_id) do nothing;

-- Singleton config row: no promoted model yet -> baseline-only serving.
insert into public.model_config (singleton, active_model_version)
values (true, null)
on conflict (singleton) do nothing;
