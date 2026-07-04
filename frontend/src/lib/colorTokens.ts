// Tier color tokens (design doc: "Availability color-coding" + Accessibility
// section). These are the single source of truth for the three-tier
// Plenty/Limited/Very limited colors, used directly (inline) by
// TierBadge so the rendered color always matches what colorTokens.test.ts
// verifies -- no risk of the CSS and the tested values drifting apart.
//
// Colors are chosen per-theme (not one hex reused in both) specifically so
// each meets WCAG AA contrast (>=4.5:1) as *text* against that theme's page
// background -- a hue that passes on white can fail once inverted for dark
// mode, per the design doc's explicit callout. Verified in colorTokens.test.ts.
import type { Tier } from "../types";

export type EffectiveTheme = "light" | "dark";

export const PAGE_BACKGROUND: Record<EffectiveTheme, string> = {
  light: "#ffffff",
  dark: "#0d1117",
};

export const TIER_TEXT_COLOR: Record<EffectiveTheme, Record<Tier, string>> = {
  light: {
    plenty: "#1a7f37",
    limited: "#9a6700",
    very_limited: "#cf222e",
  },
  dark: {
    plenty: "#3fb950",
    limited: "#e3b341",
    very_limited: "#f85149",
  },
};

export const TIER_LABEL: Record<Tier, string> = {
  plenty: "Plenty",
  limited: "Limited",
  very_limited: "Very limited",
};

export function getTierColor(theme: EffectiveTheme, tier: Tier): string {
  return TIER_TEXT_COLOR[theme][tier];
}
