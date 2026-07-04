import { SEED_CARPARKS } from "../seed/seedCarparks";

interface NotCoveredStateProps {
  onSelect: (carparkId: string) => void;
}

/**
 * Explicit "not covered yet" state (design doc Requirement 2 / Failure
 * Modes registry) for a search that matches none of the 10 seed malls --
 * never a blank area. Lists all 10 supported malls, each directly
 * selectable so the user isn't forced to retype a guess.
 */
export function NotCoveredState({ onSelect }: NotCoveredStateProps) {
  return (
    <div className="not-covered" role="status">
      <p>No results - try one of the 10 supported malls:</p>
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
