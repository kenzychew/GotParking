// Forecast staleness caveat (design doc D10): if the payload's generated_at
// is older than ~15 minutes, the pipeline has likely stalled -- each card
// should show "Data delayed - updated Xm ago" and lean on the live-count
// presentation instead of presenting a stale forecast as fresh.

export const STALE_THRESHOLD_MINUTES = 15;

/** Whole minutes elapsed since generatedAt, floored, never negative. */
export function minutesSince(generatedAt: string, now: Date = new Date()): number {
  const generatedMs = new Date(generatedAt).getTime();
  const diffMs = now.getTime() - generatedMs;
  return Math.max(0, Math.floor(diffMs / 60_000));
}

export function isStalePayload(generatedAt: string, now: Date = new Date()): boolean {
  return minutesSince(generatedAt, now) > STALE_THRESHOLD_MINUTES;
}
