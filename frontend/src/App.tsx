import { useCallback, useRef, useState } from "react";
import { AttributionFooter } from "./components/AttributionFooter";
import { ForecastCard } from "./components/ForecastCard";
import { SearchPanel } from "./components/SearchPanel";
import { ShortcutChips } from "./components/ShortcutChips";
import { ThemeToggle } from "./components/ThemeToggle";
import { useForecast } from "./hooks/useForecast";
import { useShortcuts } from "./hooks/useShortcuts";
import { useTheme } from "./hooks/useTheme";
import { resolveShareLinkCarpark } from "./lib/shareLink";

/**
 * Rapid double-tap on the same suggestion/chip must not fire duplicate
 * selections (Requirement 2 / Test Requirements). A repeat call for the
 * SAME carpark within this window is treated as the same tap, not a new one.
 */
const SELECTION_GUARD_MS = 400;

export function App() {
  const [shareLinkResult] = useState(() => resolveShareLinkCarpark(window.location.search));
  // NO auto-selected carpark on a normal landing (Requirement 1) -- the one
  // exception is an explicit, already-whitelist-validated share link, which
  // is a deliberate external action, not an app-side guess.
  const [selectedCarparkId, setSelectedCarparkId] = useState<string | null>(
    shareLinkResult.kind === "valid" ? shareLinkResult.carparkId : null,
  );

  const forecastQuery = useForecast();
  const { available: shortcutsAvailable, items: shortcutItems, pick } = useShortcuts();
  const { effectiveTheme, toggle } = useTheme();

  const lastSelectionRef = useRef<{ id: string; at: number } | null>(null);

  const handleSelectCarpark = useCallback(
    (carparkId: string): void => {
      const now = Date.now();
      const last = lastSelectionRef.current;
      if (last && last.id === carparkId && now - last.at < SELECTION_GUARD_MS) {
        return; // Rapid double-tap on the same target -- ignore the repeat.
      }
      lastSelectionRef.current = { id: carparkId, at: now };
      setSelectedCarparkId(carparkId);
      pick(carparkId);
    },
    [pick],
  );

  const hasMlModel = forecastQuery.data?.carparks.some((c) => c.state === "ml") ?? false;

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1 className="app-header__title">GotParking</h1>
        <ThemeToggle effectiveTheme={effectiveTheme} onToggle={toggle} />
      </header>

      <main style={{ display: "contents" }}>
        {shareLinkResult.kind === "invalid" ? (
          <div className="error-banner" role="alert">
            <p>Carpark not found - this link may be outdated or invalid.</p>
          </div>
        ) : null}

        <SearchPanel onSelect={handleSelectCarpark} />

        <ShortcutChips
          available={shortcutsAvailable}
          items={shortcutItems}
          selectedCarparkId={selectedCarparkId}
          onSelect={handleSelectCarpark}
        />

        {selectedCarparkId === null ? (
          <p className="landing-prompt">Search a mall or tap a shortcut to see its forecast</p>
        ) : (
          <ForecastCard
            carparkId={selectedCarparkId}
            forecastQuery={forecastQuery}
            theme={effectiveTheme}
          />
        )}
      </main>

      <AttributionFooter hasMlModel={hasMlModel} />
    </div>
  );
}
