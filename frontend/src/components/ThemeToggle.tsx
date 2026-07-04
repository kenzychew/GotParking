import type { EffectiveTheme } from "../lib/theme";

interface ThemeToggleProps {
  effectiveTheme: EffectiveTheme;
  onToggle: () => void;
}

/**
 * The visible label always names the action (switch to the OTHER mode),
 * which doubles as the accessible name -- no separate aria-label needed,
 * and no icon glyph (project charset rule: ASCII only, no unicode/emoji).
 */
export function ThemeToggle({ effectiveTheme, onToggle }: ThemeToggleProps) {
  const isDark = effectiveTheme === "dark";
  return (
    <button type="button" className="theme-toggle" onClick={onToggle}>
      {isDark ? "Light mode" : "Dark mode"}
    </button>
  );
}
