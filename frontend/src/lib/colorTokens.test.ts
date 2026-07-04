import { describe, expect, it } from "vitest";
import { contrastRatio, WCAG_AA_NORMAL_TEXT } from "./contrast";
import { PAGE_BACKGROUND, TIER_TEXT_COLOR, type EffectiveTheme } from "./colorTokens";
import type { Tier } from "../types";

const THEMES: EffectiveTheme[] = ["light", "dark"];
const TIERS: Tier[] = ["plenty", "limited", "very_limited"];

// Required by the design doc's Accessibility section: "tier colors must meet
// contrast in BOTH themes ... this is a required check, not an assumption."
// This computes real WCAG contrast ratios (see lib/contrast.ts) rather than
// eyeballing hex values, so a future edit to colorTokens.ts that regresses
// contrast fails this test immediately.
describe("tier color tokens meet WCAG AA contrast in both themes", () => {
  for (const theme of THEMES) {
    for (const tier of TIERS) {
      it(`${theme} theme: ${tier} text on the page background is >= AA (4.5:1)`, () => {
        const ratio = contrastRatio(TIER_TEXT_COLOR[theme][tier], PAGE_BACKGROUND[theme]);
        expect(ratio).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT);
      });
    }
  }

  it("no tier reuses the exact same hex across themes (each theme is deliberately tuned)", () => {
    for (const tier of TIERS) {
      expect(TIER_TEXT_COLOR.light[tier]).not.toBe(TIER_TEXT_COLOR.dark[tier]);
    }
  });

  it("the three tiers remain visually distinct within each theme", () => {
    for (const theme of THEMES) {
      const hexValues = TIERS.map((tier) => TIER_TEXT_COLOR[theme][tier]);
      expect(new Set(hexValues).size).toBe(TIERS.length);
    }
  });
});
