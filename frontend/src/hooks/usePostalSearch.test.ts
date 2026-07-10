import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { usePostalSearch } from "./usePostalSearch";
import type { SeedCarpark } from "../seed/seedCarparks";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CARPARKS: SeedCarpark[] = [
  { id: "1", name: "Suntec City", displayName: "Suntec City", latitude: 1.29375, longitude: 103.85718 },
  { id: "2", name: "Marina Square", displayName: "Marina Square", latitude: 1.29115, longitude: 103.85728 },
  { id: "99", name: "Unresolved Carpark", displayName: "Unresolved Carpark" }, // no coordinates
];

describe("usePostalSearch", () => {
  it("stays idle and fires no request for a non-postal-code query", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => usePostalSearch("Suntec", CARPARKS));

    expect(result.current.status).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("resolves a 6-digit query to nearest carparks sorted by distance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ building_name: "SUNTEC CITY MALL", latitude: 1.2935, longitude: 103.8572 }),
      ),
    );

    const { result } = renderHook(() => usePostalSearch("039593", CARPARKS));
    expect(result.current.status).toBe("loading");

    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(result.current.results.map((r) => r.item.id)).toEqual(["1", "2"]);
    // The unresolved carpark (no coordinates) never appears in results.
    expect(result.current.results.some((r) => r.item.id === "99")).toBe(false);
  });

  it("not-found status when OneMap resolves nothing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: "postal_code_not_found", message: "No location found" }, 404),
      ),
    );

    const { result } = renderHook(() => usePostalSearch("999999", CARPARKS));

    await waitFor(() => expect(result.current.status).toBe("not-found"));
    expect(result.current.results).toEqual([]);
  });

  it("error status on a server failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ error: "geocoding_unavailable" }, 503)),
    );

    const { result } = renderHook(() => usePostalSearch("039593", CARPARKS));

    await waitFor(() => expect(result.current.status).toBe("error"));
  });

  it("reverts to idle when the query changes from a postal code back to text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ building_name: "SUNTEC CITY MALL", latitude: 1.2935, longitude: 103.8572 }),
      ),
    );

    const { result, rerender } = renderHook(({ query }) => usePostalSearch(query, CARPARKS), {
      initialProps: { query: "039593" },
    });
    await waitFor(() => expect(result.current.status).toBe("success"));

    rerender({ query: "Suntec" });

    expect(result.current.status).toBe("idle");
    expect(result.current.results).toEqual([]);
  });
});
