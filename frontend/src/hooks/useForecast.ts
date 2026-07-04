import { useCallback, useEffect, useRef, useState } from "react";
import { fetchForecast, readCachedForecast, ServerError, writeCachedForecast } from "../lib/api";
import type { ForecastPayload } from "../types";

export type ForecastStatus = "loading" | "success" | "offline" | "server-error";

export interface ForecastQueryState {
  status: ForecastStatus;
  /**
   * The forecast payload, when available. Populated on success; also
   * populated on "offline" if a previous successful fetch (this session or
   * a prior one, via localStorage) left last-seen data to fall back to --
   * see the design doc's offline copy: "showing last-seen data". Always
   * null for "server-error" (a 503 never implies the client has anything
   * trustworthy to show).
   */
  data: ForecastPayload | null;
  errorMessage: string | null;
}

export interface UseForecastResult extends ForecastQueryState {
  retry: () => void;
}

/**
 * Fetches the whole 10-carpark forecast payload once on mount, exposing a
 * `retry()` for the offline/error states' retry affordance. Distinguishes
 * OfflineError (network never reached a server) from ServerError (a typed
 * 503) per the design doc, since the two require distinct UI copy.
 */
export function useForecast(): UseForecastResult {
  const [state, setState] = useState<ForecastQueryState>({
    status: "loading",
    data: null,
    errorMessage: null,
  });
  // Guards against an in-flight retry's result landing after a newer one
  // (e.g. the user mashes "Retry"): only the most recent request may apply.
  const requestIdRef = useRef(0);

  const load = useCallback((): void => {
    const requestId = ++requestIdRef.current;
    setState((prev) => ({ ...prev, status: "loading" }));

    fetchForecast()
      .then((data) => {
        if (requestIdRef.current !== requestId) return;
        writeCachedForecast(data);
        setState({ status: "success", data, errorMessage: null });
      })
      .catch((cause: unknown) => {
        if (requestIdRef.current !== requestId) return;
        if (cause instanceof ServerError) {
          setState({ status: "server-error", data: null, errorMessage: cause.message });
        } else {
          const message = cause instanceof Error ? cause.message : "Network request failed";
          setState({ status: "offline", data: readCachedForecast(), errorMessage: message });
        }
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { ...state, retry: load };
}
