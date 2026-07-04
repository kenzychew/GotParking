// Types mirroring THE PINNED PUBLIC API CONTRACT (design doc D10), verified
// against the real implementation at api/_lib/read_logic.py in the repo
// root. GET /api/forecast takes no parameters and returns this shape:
//
//   {
//     "generated_at": "<ISO>",
//     "carparks": [
//       {
//         "carpark_id": "1", "name": "Suntec City",
//         "state": "ml" | "baseline" | "cold_start",
//         "forecast_lots": <int|null>, "tier": "plenty"|"limited"|"very_limited"|null,
//         "live_lots": <int>, "model_version": <string|null>
//       }, ...
//     ]
//   }
//
// Do not drift this file from the pinned contract without re-reading
// api/_lib/read_logic.py first.

/** Which forecasting path produced this carpark's row. */
export type CarparkState = "ml" | "baseline" | "cold_start";

/** Capacity-relative availability tier (Design Details: Plenty/Limited/Very limited). */
export type Tier = "plenty" | "limited" | "very_limited";

/** One carpark's forecast row, as served by GET /api/forecast. */
export interface CarparkForecast {
  carpark_id: string;
  name: string;
  state: CarparkState;
  forecast_lots: number | null;
  tier: Tier | null;
  live_lots: number;
  model_version: string | null;
}

/** The whole-payload public forecast read response body. */
export interface ForecastPayload {
  generated_at: string;
  carparks: CarparkForecast[];
}
