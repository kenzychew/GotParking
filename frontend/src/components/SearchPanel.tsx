import { useState, type KeyboardEvent } from "react";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { usePostalSearch } from "../hooks/usePostalSearch";
import { POSTAL_CODE_PATTERN } from "../lib/postalGeocode";
import { SEED_CARPARKS, searchSeedCarparks } from "../seed/seedCarparks";
import { NotCoveredState } from "./NotCoveredState";

interface SearchPanelProps {
  onSelect: (carparkId: string) => void;
}

const DEBOUNCE_MS = 250;

/**
 * Live-filter-as-you-type against the supported-carpark list (design doc Design
 * Details: "explicitly a live-filter-as-you-type ... not a submit-and-fetch
 * pattern"), debounced 250ms per Requirement 2. Name search stays fully local, no
 * network call. A 6-digit query is treated as a postal code instead (2026-07-10):
 * resolved via /api/geocode_postal, then sorted to the nearest carparks client-side
 * (usePostalSearch) -- the only path in this component that touches the network.
 */
export function SearchPanel({ onSelect }: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, DEBOUNCE_MS);
  const trimmed = debouncedQuery.trim();
  const isPostalQuery = POSTAL_CODE_PATTERN.test(trimmed);

  const nameSuggestions = !isPostalQuery && trimmed !== "" ? searchSeedCarparks(trimmed) : [];
  const postalSearch = usePostalSearch(trimmed, SEED_CARPARKS);

  const showNotCovered = !isPostalQuery && trimmed !== "" && nameSuggestions.length === 0;

  const handleSelect = (carparkId: string): void => {
    onSelect(carparkId);
    setQuery("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter" && !isPostalQuery && nameSuggestions.length > 0) {
      event.preventDefault();
      handleSelect(nameSuggestions[0].id);
    }
  };

  return (
    <div className="search-field">
      <label htmlFor="carpark-search" className="search-field__label">
        Search for a mall
      </label>
      <input
        id="carpark-search"
        type="search"
        className="search-field__input"
        placeholder="e.g. Suntec City or a 6-digit postal code"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={handleKeyDown}
        autoComplete="off"
      />

      {isPostalQuery && postalSearch.status === "loading" && (
        <p className="search-field__status" role="status">
          Finding carparks near {trimmed}&hellip;
        </p>
      )}
      {isPostalQuery && postalSearch.status === "not-found" && (
        <p className="search-field__status" role="status">
          No location found for postal code {trimmed}.
        </p>
      )}
      {isPostalQuery && postalSearch.status === "error" && (
        <p className="search-field__status" role="alert">
          Postal code search is unavailable right now - try searching by name instead.
        </p>
      )}
      {isPostalQuery && postalSearch.status === "success" && postalSearch.results.length === 0 && (
        // A resolved postal code with zero results means no carpark currently has a known
        // coordinate (OneMap enrichment hasn't run yet, or genuinely nothing nearby) --
        // distinct from "not-found" (the postal code ITSELF didn't resolve).
        <p className="search-field__status" role="status">
          No carparks with a known location near {trimmed} yet.
        </p>
      )}
      {isPostalQuery && postalSearch.status === "success" && postalSearch.results.length > 0 && (
        <ul className="search-suggestions" aria-label={`Carparks nearest ${trimmed}`}>
          {postalSearch.results.map(({ item: carpark, distanceMeters }) => (
            <li key={carpark.id} className="search-suggestions__item">
              <button type="button" onClick={() => handleSelect(carpark.id)}>
                {carpark.displayName}
                <span className="search-suggestions__distance">
                  {" "}
                  ({(distanceMeters / 1000).toFixed(1)} km)
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {!isPostalQuery && nameSuggestions.length > 0 && (
        <ul className="search-suggestions">
          {nameSuggestions.map((carpark) => (
            <li key={carpark.id} className="search-suggestions__item">
              <button type="button" onClick={() => handleSelect(carpark.id)}>
                {carpark.displayName}
              </button>
            </li>
          ))}
        </ul>
      )}
      {showNotCovered && <NotCoveredState onSelect={handleSelect} />}
    </div>
  );
}
