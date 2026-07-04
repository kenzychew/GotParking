import type { ShortcutItem } from "../hooks/useShortcuts";

interface ShortcutChipsProps {
  available: boolean;
  items: ShortcutItem[];
  selectedCarparkId: string | null;
  onSelect: (carparkId: string) => void;
}

/**
 * One-tap shortcuts (design doc Requirement 4). Three distinct states:
 * unavailable (localStorage blocked, e.g. some private-browsing modes),
 * empty ("Add your first shortcut"), and populated (up to 3 chips, each a
 * real button so Tab + Enter/Space reaches every chip -- full keyboard nav
 * per the Accessibility baseline). Chips are >=44x44px touch targets via
 * the .chip class in index.css.
 */
export function ShortcutChips({ available, items, selectedCarparkId, onSelect }: ShortcutChipsProps) {
  return (
    <section className="shortcuts" aria-label="Shortcuts">
      <h2 className="shortcuts__title">Shortcuts</h2>
      {!available ? (
        <p className="shortcuts__unavailable">Shortcuts unavailable in this browser mode</p>
      ) : items.length === 0 ? (
        <p className="shortcuts__empty">Add your first shortcut</p>
      ) : (
        <ul className="shortcuts__chips">
          {items.map((item) => (
            <li key={item.carparkId}>
              <button
                type="button"
                className="chip"
                aria-current={item.carparkId === selectedCarparkId ? "true" : undefined}
                onClick={() => onSelect(item.carparkId)}
              >
                {item.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
