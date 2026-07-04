// WCAG 2.x relative-luminance contrast ratio calculator. Pure math, no DOM
// dependency, so it can validate color tokens as plain unit-tested data
// (see colorTokens.test.ts) instead of relying on a browser to "eyeball" it.
// Reference: https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const r = parseInt(clean.substring(0, 2), 16);
  const g = parseInt(clean.substring(2, 4), 16);
  const b = parseInt(clean.substring(4, 6), 16);
  return [r, g, b];
}

function channelLuminance(c: number): number {
  const cs = c / 255;
  return cs <= 0.03928 ? cs / 12.92 : Math.pow((cs + 0.055) / 1.055, 2.4);
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  return 0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
}

/** Contrast ratio between two sRGB hex colors, in the WCAG-defined [1, 21] range. */
export function contrastRatio(hexA: string, hexB: string): number {
  const lA = relativeLuminance(hexA);
  const lB = relativeLuminance(hexB);
  const lighter = Math.max(lA, lB);
  const darker = Math.min(lA, lB);
  return (lighter + 0.05) / (darker + 0.05);
}

/** WCAG AA minimum for normal-size text. */
export const WCAG_AA_NORMAL_TEXT = 4.5;

/** WCAG AA minimum for large text (>=18pt/24px, or >=14pt/18.66px bold) and UI components. */
export const WCAG_AA_LARGE_TEXT = 3.0;
