# frontend

Mobile-first PWA (T6 in the design doc), deployed on the same Vercel project as `api/`.

Destination search/picker (always user-driven, no auto-guessed "usual carpark") with
top 2-3 most-picked locations surfacing as one-tap shortcuts, stored in localStorage only
(no backend accounts). Degrades gracefully if localStorage is unavailable (private
browsing) - flagged as a critical gap in the design doc's Failure Modes.

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`

## Stack

- Vite + React + TypeScript (strict)
- Vitest + @testing-library/react (jsdom) for tests
- vite-plugin-pwa: installable, service-worker cache of the app shell plus a NetworkFirst
  runtime cache of the last forecast payload (so a genuinely offline visit can still show
  last-seen data, not a blank screen)
- @fontsource/public-sans, self-hosted (no system-font stack as primary, no external font CDN)
- Dark mode: `prefers-color-scheme` live by default, plus a manual toggle persisted in
  localStorage that wins once used

## Commands

```bash
npm install          # install dependencies
npm run dev          # start the Vite dev server
npm test             # run the Vitest suite once (CI mode)
npm run test:watch   # run Vitest in watch mode
npm run build        # tsc -b (typecheck) + vite build -> dist/
npm run preview      # preview the production build locally
npm run icons        # regenerate public/icons/*.png (see scripts/generate-icons.mjs)
```

## The mocked contract

This lane was built against THE PINNED public API contract (see `api/_lib/read_logic.py`
and `api/forecast.py` in the repo root) rather than a live backend. `GET /api/forecast`
takes no parameters and returns:

```json
{
  "generated_at": "<ISO 8601 timestamp>",
  "carparks": [
    {
      "carpark_id": "1",
      "name": "Suntec City",
      "state": "ml",
      "forecast_lots": 120,
      "tier": "limited",
      "live_lots": 100,
      "model_version": "lgbm-2026-07-01"
    }
  ]
}
```

`state` is `"ml" | "baseline" | "cold_start"`; `tier` is
`"plenty" | "limited" | "very_limited" | null` (null only for `cold_start` rows, which also
carry `forecast_lots: null`); `model_version` is a string or `null`.

Errors: a network failure (offline) raises `OfflineError`; an HTTP 503
(`{"error": "predictions_unavailable", "message": "Predictions temporarily unavailable"}`)
raises `ServerError` - the UI renders distinct copy for each. See `src/lib/api.ts`,
`src/hooks/useForecast.ts`, and the "required test slice" section of `src/App.test.tsx`.

Tests mock `fetch` directly (`src/test/fixtures.ts` holds representative payloads covering
all three states and all three tiers) - no live backend is required to run the suite or
the build. `src/types.ts` mirrors the contract by reading `read_logic.py`'s response-shape
docstring directly, so no frontend changes should be needed once Lane D's endpoint is live
and wired up for real; a short manual smoke test against the real deployment is still
worth doing once both lanes are merged (see Known gaps below).

## Vercel build settings (for the orchestrator's root vercel.json merge)

This lane does NOT edit the root `vercel.json` (owned by the api lane, currently
`{"regions": ["sin1"]}`). The orchestrator should merge in:

```json
{
  "buildCommand": "cd frontend && npm ci && npm run build",
  "outputDirectory": "frontend/dist"
}
```

## Seed carpark whitelist

`src/seed/seedCarparks.ts` is the static, client-side list of the 10 supported carparks
(matches `db/schema.sql`'s `carparks` table exactly). It powers local search (no network
round-trip) and share-link (`?carpark=<id>`) client-side whitelist validation. It lives
under `src/seed/`, not `src/data/` - the repo-root `.gitignore`'s bare `data/` pattern
matches a directory named `data` at any depth, which would otherwise silently exclude
`frontend/src/data/` from git.

## Known gaps / deviations

- No live backend was available while building this lane; the contract match was originally
  verified by reading `api/_lib/read_logic.py`'s docstring directly, not by an actual
  round-trip against Lane D's deployed endpoint. **Resolved 2026-07-06:** `/qa` ran a full
  browser pass against the live deployment (`https://gstack-playground.vercel.app`) --
  search, select, no-results state, dark mode, Share-to-clipboard, shortcuts, mobile
  viewport -- health score 98/100, zero bugs, console clean throughout. See
  `.gstack/qa-reports/qa-report-gstack-playground-vercel-app-2026-07-06.md`.
- The "based on N weeks of history" transparency note from the design doc's Design Details
  section is implemented using `model_version`'s presence/absence as the trigger (per the
  design doc's own instruction), but phrased without a fabricated week count - the pinned
  contract has no such field, and inventing one would be dishonest. See `TransparencyNote`
  in `src/components/ForecastCard.tsx`.
- Search is a plain list of real `<button>` elements (full keyboard operability via Tab +
  Enter/Space), not a full ARIA 1.2 combobox/listbox widget with roving
  `aria-activedescendant`. Deliberate scope call for a 10-item static list at MVP size -
  partial ARIA combobox semantics without the full pattern can be worse than none.
- The rapid-double-tap guard (`SELECTION_GUARD_MS` in `src/App.tsx`) treats a second pick
  of the *same* carpark within 400ms as a misfire, not a new pick. A deliberate re-visit to
  the same carpark less than 400ms after the last one (unusual for a human, common in a
  fast automated test) will not increment its shortcut pick-count a second time; see the
  inline comment on the "shortcut add" test in `src/App.test.tsx` for how that test works
  around it (waits past the guard window between deliberately-separate picks).
