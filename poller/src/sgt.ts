// Shared SGT time helper (design doc D3). Singapore is fixed UTC+8 with no
// DST, so plain offset arithmetic is correct and no tz database is needed.
// The Python twin lives with the training job; both pin the same boundary
// case (Sat 02:00 SGT == Fri 18:00 UTC) in unit tests.

const SGT_OFFSET_MS = 8 * 60 * 60 * 1000;
const SLOT_MINUTES = 15;

export interface SgtParts {
  /** Day of week in SGT: 0=Monday .. 6=Sunday. */
  dow: number;
  /** 15-minute slot of the SGT day: 0 .. 95. */
  slotOfDay: number;
}

export function sgtParts(dateUtc: Date): SgtParts {
  const shifted = new Date(dateUtc.getTime() + SGT_OFFSET_MS);
  // getUTCDay is 0=Sunday .. 6=Saturday; rotate to 0=Monday .. 6=Sunday.
  const dow = (shifted.getUTCDay() + 6) % 7;
  const slotOfDay =
    shifted.getUTCHours() * (60 / SLOT_MINUTES) +
    Math.floor(shifted.getUTCMinutes() / SLOT_MINUTES);
  return { dow, slotOfDay };
}
