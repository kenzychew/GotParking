import { SEED_CARPARKS } from "../seed/seedCarparks";

interface NotCoveredStateProps {
  onSelect: (carparkId: string) => void;
}

/**
 * Explicit "not covered yet" state (design doc Requirement 2 / Failure
 * Modes registry) for a search that matches none of the supported seed
 * malls -- never a blank area. Lists all supported malls, each directly
 * selectable so the user isn't forced to retype a guess. The count is
 * read from SEED_CARPARKS.length, not hardcoded, so it can't drift out of
 * sync with the generated seed list (scripts/regen_seed_lists.py) again.
 */
export function NotCoveredState({ onSelect }: NotCoveredStateProps) {
  return (
    <div className="not-covered" role="status">
      <p>No results - try one of the {SEED_CARPARKS.length} supported malls:</p>
      <ul className="not-covered__list">
        {SEED_CARPARKS.map((carpark) => (
          <li key={carpark.id}>
            <button type="button" onClick={() => onSelect(carpark.id)}>
              {carpark.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
