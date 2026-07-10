import { describe, expect, it, vi } from "vitest";
import {
  geocodePostalCode,
  NotFoundError,
  OfflineError,
  POSTAL_CODE_PATTERN,
  ServerError,
} from "./postalGeocode";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("POSTAL_CODE_PATTERN", () => {
  it("matches exactly 6 digits", () => {
    expect(POSTAL_CODE_PATTERN.test("039593")).toBe(true);
  });

  it("rejects non-6-digit input", () => {
    expect(POSTAL_CODE_PATTERN.test("39593")).toBe(false);
    expect(POSTAL_CODE_PATTERN.test("0395931")).toBe(false);
    expect(POSTAL_CODE_PATTERN.test("Suntec City")).toBe(false);
    expect(POSTAL_CODE_PATTERN.test("")).toBe(false);
  });
});

describe("geocodePostalCode", () => {
  it("returns the parsed coordinate on a 200 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ building_name: "SUNTEC CITY MALL", latitude: 1.2935, longitude: 103.8573 }),
      ),
    );

    const result = await geocodePostalCode("039593");

    expect(result).toEqual({ buildingName: "SUNTEC CITY MALL", latitude: 1.2935, longitude: 103.8573 });
  });

  it("throws OfflineError when the network request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(geocodePostalCode("039593")).rejects.toBeInstanceOf(OfflineError);
  });

  it("throws NotFoundError on a 404", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: "postal_code_not_found", message: "No location found" }, 404),
      ),
    );

    await expect(geocodePostalCode("999999")).rejects.toBeInstanceOf(NotFoundError);
  });

  it("throws ServerError (not NotFoundError) for a 503", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ error: "geocoding_unavailable" }, 503)),
    );

    await expect(geocodePostalCode("039593")).rejects.toBeInstanceOf(ServerError);
  });

  it("throws ServerError for a 400", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ error: "bad_request" }, 400)),
    );

    await expect(geocodePostalCode("")).rejects.toBeInstanceOf(ServerError);
  });
});
