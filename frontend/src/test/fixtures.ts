// Shared mock data for tests -- built to match THE PINNED PUBLIC CONTRACT
// (src/types.ts / api/_lib/read_logic.py) exactly, covering all three
// carpark states plus all three tiers so individual tests can pick the
// scenario they need without re-deriving payload shape each time.
import type { CarparkForecast, ForecastPayload } from "../types";
import { SEED_CARPARKS } from "../seed/seedCarparks";

export function makeCarparkForecast(
  overrides: Partial<CarparkForecast> & { carpark_id: string },
): CarparkForecast {
  const seed = SEED_CARPARKS.find((c) => c.id === overrides.carpark_id);
  const defaults: CarparkForecast = {
    carpark_id: overrides.carpark_id,
    name: seed?.name ?? overrides.carpark_id,
    state: "baseline",
    forecast_lots: 200,
    tier: "plenty",
    live_lots: 190,
    model_version: null,
  };
  return { ...defaults, ...overrides };
}

export function makeForecastPayload(options?: {
  generatedAt?: string;
  carparks?: CarparkForecast[];
}): ForecastPayload {
  return {
    generated_at: options?.generatedAt ?? new Date().toISOString(),
    carparks:
      options?.carparks ?? SEED_CARPARKS.map((c) => makeCarparkForecast({ carpark_id: c.id })),
  };
}

const FEATURED_OVERRIDES: CarparkForecast[] = [
  makeCarparkForecast({
    carpark_id: "1", // Suntec City
    state: "ml",
    forecast_lots: 120,
    tier: "limited",
    live_lots: 100,
    model_version: "lgbm-2026-07-01",
  }),
  makeCarparkForecast({
    carpark_id: "2", // Marina Square
    state: "baseline",
    forecast_lots: 300,
    tier: "plenty",
    live_lots: 280,
  }),
  makeCarparkForecast({
    carpark_id: "3", // Raffles City
    state: "cold_start",
    forecast_lots: null,
    tier: null,
    live_lots: 50,
  }),
  makeCarparkForecast({
    carpark_id: "11", // Cineleisure
    state: "baseline",
    forecast_lots: 8,
    tier: "very_limited",
    live_lots: 6,
  }),
];

const featuredIds = new Set(FEATURED_OVERRIDES.map((c) => c.carpark_id));

/** A realistic, fresh (not stale) payload covering every state and tier. */
export const MOCK_FRESH_PAYLOAD: ForecastPayload = makeForecastPayload({
  carparks: [
    ...FEATURED_OVERRIDES,
    ...SEED_CARPARKS.filter((c) => !featuredIds.has(c.id)).map((c) =>
      makeCarparkForecast({ carpark_id: c.id }),
    ),
  ],
});

/** Same data, but generated_at is far enough in the past to trip the staleness caveat (>15 min). */
export const MOCK_STALE_PAYLOAD: ForecastPayload = makeForecastPayload({
  generatedAt: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
  carparks: MOCK_FRESH_PAYLOAD.carparks,
});
