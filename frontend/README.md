# frontend

Mobile-first PWA (T6 in the design doc), deployed on the same Vercel project as `api/`.

Destination search/picker (always user-driven, no auto-guessed "usual carpark") with
top 2-3 most-picked locations surfacing as one-tap shortcuts, stored in localStorage only
(no backend accounts). Must degrade gracefully if localStorage is unavailable (private
browsing) — flagged as a critical gap in the design doc's Failure Modes.

Test framework: Vitest.

Design doc: `~/.gstack/projects/gstack-playground/kenzy-unknown-design-20260702-210951.md`
