// Client for GET /api/forecast (THE PINNED PUBLIC CONTRACT -- see
// src/types.ts). Distinguishes two failure modes per the design doc's
// Failure Modes registry, which the UI must render with distinct copy:
//   - OfflineError: the request never reached a server (network failure,
//     e.g. `fetch` rejecting -- offline, DNS failure, etc).
//   - ServerError: the server responded but with a typed 503
//     ({"error": "predictions_unavailable", ...}) or any other non-2xx --
//     the server is reachable but degraded.
import type { ForecastPayload } from "../types";

export const FORECAST_URL = "/api/forecast";

/** Network never reached a server (offline, DNS failure, timeout, ...). */
export class OfflineError extends Error {
  constructor(message = "Network request failed") {
    super(message);
    this.name = "OfflineError";
  }
}

/** Server reachable but degraded (typed 503) or returned something unusable. */
export class ServerError extends Error {
  readonly status: number | null;

  constructor(message = "Predictions temporarily unavailable", status: number | null = null) {
    super(message);
    this.name = "ServerError";
    this.status = status;
  }
}

/**
 * Fetch the whole 10-carpark forecast payload.
 *
 * Throws {@link OfflineError} when the request never reached a server, or
 * {@link ServerError} when the server responded with a typed 503 (or any
 * other non-OK status / an unparseable body).
 */
export async function fetchForecast(signal?: AbortSignal): Promise<ForecastPayload> {
  let response: Response;
  try {
    response = await fetch(FORECAST_URL, { signal });
  } catch (cause) {
    throw new OfflineError(cause instanceof Error ? cause.message : "Network request failed");
  }

  if (response.status === 503) {
    throw new ServerError("Predictions temporarily unavailable", 503);
  }
  if (!response.ok) {
    throw new ServerError(`Unexpected response (HTTP ${response.status})`, response.status);
  }

  try {
    return (await response.json()) as ForecastPayload;
  } catch (cause) {
    throw new ServerError(
      cause instanceof Error ? cause.message : "Malformed forecast payload",
      response.status,
    );
  }
}

const LAST_FORECAST_KEY = "gotparking:last-forecast-v1";

/**
 * Best-effort read of the last successfully fetched payload, used so the
 * offline state can show "last-seen data" instead of a blank screen (design
 * doc: "No internet connection - showing last-seen data"). Returns null if
 * nothing is cached or localStorage is unavailable -- never throws.
 */
export function readCachedForecast(): ForecastPayload | null {
  try {
    const raw = window.localStorage.getItem(LAST_FORECAST_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as ForecastPayload;
  } catch {
    return null;
  }
}

/** Best-effort write-through cache of the last successful payload. Never throws. */
export function writeCachedForecast(payload: ForecastPayload): void {
  try {
    window.localStorage.setItem(LAST_FORECAST_KEY, JSON.stringify(payload));
  } catch {
    // Quota exceeded or localStorage unavailable (private browsing) -- the
    // cache is a nice-to-have, not a hard requirement, so fail silently.
  }
}
