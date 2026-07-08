# db

Supabase schema and RLS posture (T2 in the design doc's Implementation Tasks).

`schema.sql` is the whole schema: idempotent (safe to re-run), additive-only per the
rollback posture. Seven tables -- `carparks` (seed whitelist + SINPA mapping),
`carpark_history`, `carpark_baseline`, `carpark_momentum`, `carpark_forecast`,
`model_config`, `training_runs` -- plus the private `models` Storage bucket and seed rows
for the 10 originally-validated carparks (T2, 2026-07-04; see below for the Apply/Verify
steps as they stood then). `carparks` and `training_runs` go beyond the design doc's
literal T2 list; the header comment in `schema.sql` records why (whitelist/FK integrity +
the Observability section's promotion history).

**Schema evolution since T2 (2026-07-08, coverage expansion):** two additive columns --
`carparks.is_original_seed` (`true` for the 10 T2 rows, `false` for anything added later) and
`model_config.first_promotion_at` -- together gate newly-onboarded carparks out of pooled
training until the original 10's first-ever promotion happens. Live production now has 24
`carparks` rows (10 original + 14 verified coverage-expansion candidates), not 10 -- the
Apply/Verify steps below describe T2's original 10-row state and are kept as a historical
record of that exit criteria, not a live row-count claim.

RLS is enabled on every table with NO policies -- deny-by-default for `anon` and
`authenticated`, with the default PostgREST grants revoked on top. Only the service-role
key (poller, training job, batch predict) can touch data. The frontend never talks to
Supabase directly; it reads the cached public forecast endpoint.

## Apply

1. Complete provisioning checklist Phase 2 (project in `ap-southeast-1`).
2. Supabase dashboard -> SQL Editor -> paste all of `schema.sql` -> Run.
3. Table Editor should show 7 tables; `carparks` has 10 rows; `model_config` has 1 row
   with `active_model_version` null (baseline-only serving until the first promotion).

Note: `schema.sql` also creates the private `models` bucket (section 9), so if you ran it
before doing the checklist's manual bucket step, that step is already satisfied --
just confirm Storage shows `models` as Private.

## Verify (T2 exit criteria)

RLS lockout -- run in Git Bash, substituting your project ref and the ANON key
(never the service key):

    curl -s "https://<ref>.supabase.co/rest/v1/carparks?select=*" \
      -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>"

Expected: `[]` or a permission-denied error. Seeing the 10 seed rows = FAIL.

    curl -s -X POST "https://<ref>.supabase.co/rest/v1/carpark_history" \
      -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>" \
      -H "Content-Type: application/json" \
      -d '{"carpark_id":"1","polled_at":"2026-01-01T00:00:00Z","available_lots":1}'

Expected: a 401/403/permission error. A 201 = FAIL.

Same GET with the SERVICE-ROLE key must return all 10 seed rows (service role bypasses
RLS by design).

Duplicate-insert guard (D4) -- run in the SQL Editor:

    insert into public.carpark_history values ('1', '2026-01-01T00:00:00Z', 100)
    on conflict do nothing;
    insert into public.carpark_history values ('1', '2026-01-01T00:00:00Z', 100)
    on conflict do nothing;
    select count(*) from public.carpark_history
    where carpark_id = '1' and polled_at = '2026-01-01T00:00:00Z';
    -- expected: 1
    delete from public.carpark_history
    where carpark_id = '1' and polled_at = '2026-01-01T00:00:00Z';  -- clean up

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
