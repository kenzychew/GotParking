// Client for GET /api/geocode_postal (backs the postal-code proximity search). Mirrors
// api.ts's OfflineError/ServerError distinction, plus a distinct NotFoundError for a
// well-formed request that OneMap simply couldn't resolve -- a real, expected outcome for
// a mistyped or nonexistent postal code, not a server failure the UI should treat the same
// way as an outage.

export const GEOCODE_POSTAL_URL = "/api/geocode_postal";

/** A valid Singapore postal code: exactly 6 digits. */
export const POSTAL_CODE_PATTERN = /^\d{6}$/;

export interface GeocodeResult {
  buildingName: string;
  latitude: number;
  longitude: number;
}

export class OfflineError extends Error {
  constructor(message = "Network request failed") {
    super(message);
    this.name = "OfflineError";
  }
}

export class ServerError extends Error {
  readonly status: number | null;

  constructor(message = "Postal code search unavailable", status: number | null = null) {
    super(message);
    this.name = "ServerError";
    this.status = status;
  }
}

/** The postal code was well-formed but OneMap found no location for it. */
export class NotFoundError extends Error {
  constructor(message = "No location found for that postal code") {
    super(message);
    this.name = "NotFoundError";
  }
}

/**
 * Resolve a postal code to a coordinate via /api/geocode_postal.
 *
 * Throws {@link OfflineError} if the request never reached a server,
 * {@link NotFoundError} on a 404 (postal code well-formed, nothing found), or
 * {@link ServerError} for anything else non-OK (400 bad request, 503 unconfigured/down).
 */
export async function geocodePostalCode(
  postalCode: string,
  signal?: AbortSignal,
): Promise<GeocodeResult> {
  let response: Response;
  try {
    response = await fetch(
      `${GEOCODE_POSTAL_URL}?postal=${encodeURIComponent(postalCode)}`,
      { signal },
    );
  } catch (cause) {
    throw new OfflineError(cause instanceof Error ? cause.message : "Network request failed");
  }

  if (response.status === 404) {
    throw new NotFoundError();
  }
  if (!response.ok) {
    throw new ServerError(`Unexpected response (HTTP ${response.status})`, response.status);
  }

  try {
    const body = (await response.json()) as {
      building_name: string;
      latitude: number;
      longitude: number;
    };
    return { buildingName: body.building_name, latitude: body.latitude, longitude: body.longitude };
  } catch (cause) {
    throw new ServerError(
      cause instanceof Error ? cause.message : "Malformed geocode response",
      response.status,
    );
  }
}
