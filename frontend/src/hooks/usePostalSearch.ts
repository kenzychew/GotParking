import { useEffect, useRef, useState } from "react";
import type { SeedCarpark } from "../seed/seedCarparks";
import { sortByDistance, type WithDistance } from "../lib/haversine";
import {
  geocodePostalCode,
  NotFoundError,
  POSTAL_CODE_PATTERN,
  ServerError,
} from "../lib/postalGeocode";

export type PostalSearchStatus = "idle" | "loading" | "success" | "not-found" | "error";

export interface PostalSearchState {
  status: PostalSearchStatus;
  results: WithDistance<SeedCarpark>[];
  errorMessage: string | null;
}

const NEAREST_RESULT_COUNT = 5;

/**
 * Resolves a 6-digit postal-code query to the nearest N carparks (haversine, client-side,
 * against every carpark's already-embedded lat/lon). "idle" whenever `query` doesn't look
 * like a postal code (POSTAL_CODE_PATTERN) -- SearchPanel falls back to name search in
 * that case, this hook does nothing and fires no request.
 *
 * Debouncing is the CALLER's responsibility (SearchPanel already debounces the raw input
 * for name search; this hook is given the same already-debounced value) -- this hook only
 * adds request-id guarding against a stale in-flight response landing after a newer query.
 */
export function usePostalSearch(
  query: string,
  carparks: readonly SeedCarpark[],
): PostalSearchState {
  const [state, setState] = useState<PostalSearchState>({
    status: "idle",
    results: [],
    errorMessage: null,
  });
  const requestIdRef = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (!POSTAL_CODE_PATTERN.test(trimmed)) {
      setState({ status: "idle", results: [], errorMessage: null });
      return;
    }

    const requestId = ++requestIdRef.current;
    setState({ status: "loading", results: [], errorMessage: null });

    geocodePostalCode(trimmed)
      .then((geocode) => {
        if (requestIdRef.current !== requestId) return;
        const nearest = sortByDistance(carparks, geocode.latitude, geocode.longitude).slice(
          0,
          NEAREST_RESULT_COUNT,
        );
        setState({ status: "success", results: nearest, errorMessage: null });
      })
      .catch((cause: unknown) => {
        if (requestIdRef.current !== requestId) return;
        if (cause instanceof NotFoundError) {
          setState({ status: "not-found", results: [], errorMessage: cause.message });
        } else {
          const message =
            cause instanceof ServerError || cause instanceof Error
              ? cause.message
              : "Postal code search failed";
          setState({ status: "error", results: [], errorMessage: message });
        }
      });
  }, [query, carparks]);

  return state;
}
