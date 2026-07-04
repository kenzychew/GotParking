// Dark mode: prefers-color-scheme by default, plus a manual toggle
// persisted in localStorage that overrides the system preference once used
// (design doc / REQUIRED BEHAVIOR 9). Pure, DOM-light functions here; the
// React wiring (state + the matchMedia change listener) lives in
// src/hooks/useTheme.ts so this module stays trivially unit-testable.

export type ThemePreference = "light" | "dark" | "system";
export type EffectiveTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "gotparking:theme-v1";

/** Reads the persisted manual override, or "system" if none/unavailable. */
export function getStoredThemePreference(): ThemePreference {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "light" || raw === "dark") return raw;
    return "system";
  } catch {
    return "system";
  }
}

/** Persists a manual override, or clears it when set back to "system". Never throws. */
export function storeThemePreference(preference: ThemePreference): void {
  try {
    if (preference === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    }
  } catch {
    // Non-fatal: the toggle still works for the current session via React
    // state, it just won't survive a reload if storage is unavailable.
  }
}

export function getSystemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Resolves a preference (which may be "system") down to a concrete light/dark. */
export function resolveEffectiveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): EffectiveTheme {
  if (preference === "system") return systemPrefersDark ? "dark" : "light";
  return preference;
}

/** Flips light<->dark; from "system", flips relative to the current system value. */
export function toggleThemePreference(
  current: ThemePreference,
  systemPrefersDark: boolean,
): ThemePreference {
  if (current === "dark") return "light";
  if (current === "light") return "dark";
  return systemPrefersDark ? "light" : "dark";
}
