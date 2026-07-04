import { useState, type KeyboardEvent } from "react";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { searchSeedCarparks } from "../seed/seedCarparks";
import { NotCoveredState } from "./NotCoveredState";

interface SearchPanelProps {
  onSelect: (carparkId: string) => void;
}

const DEBOUNCE_MS = 250;

/**
 * Live-filter-as-you-type against the static 10-mall list (design doc
 * Design Details: "explicitly a live-filter-as-you-type ... not a
 * submit-and-fetch pattern"). No network call here at all -- search is
 * always local. Debounced 250ms per Requirement 2.
 */
export function SearchPanel({ onSelect }: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, DEBOUNCE_MS);
  const trimmed = debouncedQuery.trim();
  const suggestions = trimmed === "" ? [] : searchSeedCarparks(trimmed);
  const showNotCovered = trimmed !== "" && suggestions.length === 0;

  const handleSelect = (carparkId: string): void => {
    onSelect(carparkId);
    setQuery("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter" && suggestions.length > 0) {
      event.preventDefault();
      handleSelect(suggestions[0].id);
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
        placeholder="e.g. Suntec City"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={handleKeyDown}
        autoComplete="off"
      />
      {suggestions.length > 0 && (
        <ul className="search-suggestions">
          {suggestions.map((carpark) => (
            <li key={carpark.id} className="search-suggestions__item">
              <button type="button" onClick={() => handleSelect(carpark.id)}>
                {carpark.name}
              </button>
            </li>
          ))}
        </ul>
      )}
      {showNotCovered && <NotCoveredState onSelect={handleSelect} />}
    </div>
  );
}
