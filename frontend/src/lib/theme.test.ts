import { describe, expect, it } from "vitest";
import {
  getStoredThemePreference,
  resolveEffectiveTheme,
  storeThemePreference,
  toggleThemePreference,
} from "./theme";

describe("getStoredThemePreference / storeThemePreference", () => {
  it('defaults to "system" when nothing is stored', () => {
    expect(getStoredThemePreference()).toBe("system");
  });

  it("round-trips an explicit light/dark preference", () => {
    storeThemePreference("dark");
    expect(getStoredThemePreference()).toBe("dark");
    storeThemePreference("light");
    expect(getStoredThemePreference()).toBe("light");
  });

  it('clears the stored key when set back to "system"', () => {
    storeThemePreference("dark");
    storeThemePreference("system");
    expect(getStoredThemePreference()).toBe("system");
    expect(window.localStorage.getItem("gotparking:theme-v1")).toBeNull();
  });

  it("ignores a corrupt stored value", () => {
    window.localStorage.setItem("gotparking:theme-v1", "purple");
    expect(getStoredThemePreference()).toBe("system");
  });
});

describe("resolveEffectiveTheme", () => {
  it("passes through an explicit preference", () => {
    expect(resolveEffectiveTheme("dark", false)).toBe("dark");
    expect(resolveEffectiveTheme("light", true)).toBe("light");
  });

  it("resolves system based on the OS preference", () => {
    expect(resolveEffectiveTheme("system", true)).toBe("dark");
    expect(resolveEffectiveTheme("system", false)).toBe("light");
  });
});

describe("toggleThemePreference", () => {
  it("flips light -> dark and dark -> light", () => {
    expect(toggleThemePreference("light", false)).toBe("dark");
    expect(toggleThemePreference("dark", false)).toBe("light");
  });

  it("from system, flips relative to the current system preference", () => {
    expect(toggleThemePreference("system", true)).toBe("light");
    expect(toggleThemePreference("system", false)).toBe("dark");
  });
});
