import { useCallback, useEffect, useState } from "react";
import {
  type EffectiveTheme,
  getSystemPrefersDark,
  getStoredThemePreference,
  resolveEffectiveTheme,
  storeThemePreference,
  type ThemePreference,
  toggleThemePreference,
} from "../lib/theme";

export interface UseThemeResult {
  preference: ThemePreference;
  effectiveTheme: EffectiveTheme;
  toggle: () => void;
}

/**
 * Dark mode: follows prefers-color-scheme live until the user manually
 * toggles, at which point the explicit choice is persisted and wins
 * regardless of system preference (design doc Requirement 9).
 */
export function useTheme(): UseThemeResult {
  const [preference, setPreference] = useState<ThemePreference>(() => getStoredThemePreference());
  const [systemPrefersDark, setSystemPrefersDark] = useState<boolean>(() => getSystemPrefersDark());

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (event: MediaQueryListEvent): void => {
      setSystemPrefersDark(event.matches);
    };
    mql.addEventListener("change", handleChange);
    return () => mql.removeEventListener("change", handleChange);
  }, []);

  const effectiveTheme = resolveEffectiveTheme(preference, systemPrefersDark);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", effectiveTheme);
  }, [effectiveTheme]);

  const toggle = useCallback(() => {
    setPreference((prev) => {
      const next = toggleThemePreference(prev, systemPrefersDark);
      storeThemePreference(next);
      return next;
    });
  }, [systemPrefersDark]);

  return { preference, effectiveTheme, toggle };
}
