import { getTierColor, type EffectiveTheme, TIER_LABEL } from "../lib/colorTokens";
import type { Tier } from "../types";

interface TierBadgeProps {
  tier: Tier;
  theme: EffectiveTheme;
}

/**
 * Tier is ALWAYS paired with its text label, never color alone (design doc
 * Accessibility section: colorblind users must not depend on hue to read
 * status). The dot is decorative (aria-hidden); the label text carries the
 * actual meaning for assistive tech.
 */
export function TierBadge({ tier, theme }: TierBadgeProps) {
  const color = getTierColor(theme, tier);
  return (
    <span className="tier-badge" style={{ color }}>
      <span className="tier-dot" style={{ backgroundColor: color }} aria-hidden="true" />
      {TIER_LABEL[tier]}
    </span>
  );
}
