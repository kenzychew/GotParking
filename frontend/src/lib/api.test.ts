import { describe, expect, it, vi } from "vitest";
import {
  fetchForecast,
  OfflineError,
  readCachedForecast,
  ServerError,
  writeCachedForecast,
} from "./api";
import { MOCK_FRESH_PAYLOAD } from "../test/fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("fetchForecast", () => {
  it("returns the parsed payload on a 200 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(MOCK_FRESH_PAYLOAD)),
    );

    const result = await fetchForecast();

    expect(result).toEqual(MOCK_FRESH_PAYLOAD);
  });

  it("throws OfflineError when the network request itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    await expect(fetchForecast()).rejects.toBeInstanceOf(OfflineError);
  });

  it("throws ServerError with the pinned copy on a 503", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            { error: "predictions_unavailable", message: "Predictions temporarily unavailable" },
            503,
          ),
        ),
    );

    await expect(fetchForecast()).rejects.toMatchObject({
      name: "ServerError",
      message: "Predictions temporarily unavailable",
    });
  });

  it("throws ServerError (not OfflineError) for other non-OK statuses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 500)));

    await expect(fetchForecast()).rejects.toBeInstanceOf(ServerError);
  });

  it("OfflineError and ServerError produce distinct messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    let offlineMessage = "";
    try {
      await fetchForecast();
    } catch (err) {
      offlineMessage = err instanceof Error ? err.message : "";
    }

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ error: "predictions_unavailable" }, 503)),
    );
    let serverMessage = "";
    try {
      await fetchForecast();
    } catch (err) {
      serverMessage = err instanceof Error ? err.message : "";
    }

    expect(offlineMessage).not.toBe(serverMessage);
  });
});

describe("forecast cache (readCachedForecast / writeCachedForecast)", () => {
  it("round-trips a payload through localStorage", () => {
    expect(readCachedForecast()).toBeNull();
    writeCachedForecast(MOCK_FRESH_PAYLOAD);
    expect(readCachedForecast()).toEqual(MOCK_FRESH_PAYLOAD);
  });

  it("returns null (never throws) when the stored value is corrupt JSON", () => {
    window.localStorage.setItem("gotparking:last-forecast-v1", "{not json");
    expect(readCachedForecast()).toBeNull();
  });

  it("writeCachedForecast never throws even if localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => writeCachedForecast(MOCK_FRESH_PAYLOAD)).not.toThrow();
    spy.mockRestore();
  });
});
