import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useForecast } from "./useForecast";
import { MOCK_FRESH_PAYLOAD } from "../test/fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("useForecast", () => {
  it("starts loading, then resolves to success with the fetched payload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(MOCK_FRESH_PAYLOAD)));

    const { result } = renderHook(() => useForecast());
    expect(result.current.status).toBe("loading");

    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(result.current.data).toEqual(MOCK_FRESH_PAYLOAD);
  });

  it("network failure -> offline status, retry() re-fetches and recovers", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse(MOCK_FRESH_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useForecast());
    await waitFor(() => expect(result.current.status).toBe("offline"));
    expect(result.current.data).toBeNull();

    act(() => result.current.retry());

    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(result.current.data).toEqual(MOCK_FRESH_PAYLOAD);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("503 -> server-error status with the pinned message", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ error: "predictions_unavailable" }, 503),
        ),
    );

    const { result } = renderHook(() => useForecast());
    await waitFor(() => expect(result.current.status).toBe("server-error"));
    expect(result.current.errorMessage).toBe("Predictions temporarily unavailable");
    expect(result.current.data).toBeNull();
  });

  it("only the latest retry's result is applied when retries overlap", async () => {
    let resolveFirst: (value: Response) => void = () => {};
    const firstPromise = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(firstPromise)
      .mockResolvedValueOnce(jsonResponse(MOCK_FRESH_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useForecast());
    expect(result.current.status).toBe("loading");

    // Fire a second load before the first one resolves.
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("success"));

    // The first (stale) request resolves after the second already won --
    // it must not clobber the newer successful state.
    act(() => resolveFirst(jsonResponse({ generated_at: "stale", carparks: [] })));
    expect(result.current.data).toEqual(MOCK_FRESH_PAYLOAD);
  });
});
