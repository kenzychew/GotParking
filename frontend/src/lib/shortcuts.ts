// One-tap shortcuts (design doc Design Details + Requirement 4): localStorage
// only, no backend accounts. Ranked by pick-count (most-picked first), with
// most-recent pick as the tiebreaker for equal counts. Degrades gracefully
// (feature-detected) when localStorage is unavailable, e.g. some private
// browsing modes.

const SHORTCUTS_KEY = "gotparking:shortcuts-v1";
const PROBE_KEY = "gotparking:__storage_probe__";
export const MAX_SHORTCUTS_SHOWN = 3;

export interface ShortcutRecord {
  carparkId: string;
  count: number;
  /** Epoch ms of the most recent pick -- informational/display only. */
  lastPickedAt: number;
  /**
   * Strictly increasing per pick, scoped to this record set. Used as the
   * recency tiebreaker instead of lastPickedAt's wall-clock value, because
   * two picks that land in the same millisecond (common in fast/automated
   * flows) would otherwise tie unpredictably.
   */
  sequence: number;
}

/** Feature-detects localStorage (throws in some private-browsing modes). */
export function isLocalStorageAvailable(): boolean {
  try {
    window.localStorage.setItem(PROBE_KEY, "1");
    window.localStorage.removeItem(PROBE_KEY);
    return true;
  } catch {
    return false;
  }
}

function readShortcutRecords(): ShortcutRecord[] {
  try {
    const raw = window.localStorage.getItem(SHORTCUTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (r): r is ShortcutRecord =>
        typeof r === "object" &&
        r !== null &&
        typeof (r as ShortcutRecord).carparkId === "string" &&
        typeof (r as ShortcutRecord).count === "number",
    );
  } catch {
    return [];
  }
}

function writeShortcutRecords(records: ShortcutRecord[]): void {
  try {
    window.localStorage.setItem(SHORTCUTS_KEY, JSON.stringify(records));
  } catch {
    // Already feature-detected by isLocalStorageAvailable(); this guards
    // against a mid-session quota error, non-fatal either way.
  }
}

/** Reads the currently persisted shortcut records (all picks, not just top 3). */
export function loadShortcutRecords(): ShortcutRecord[] {
  return readShortcutRecords();
}

/** Records a pick for carparkId, incrementing its count, and persists the result. */
export function recordPick(carparkId: string): ShortcutRecord[] {
  const records = readShortcutRecords();
  const now = Date.now();
  const nextSequence = records.reduce((max, r) => Math.max(max, r.sequence), 0) + 1;
  const idx = records.findIndex((r) => r.carparkId === carparkId);

  if (idx >= 0) {
    records[idx] = {
      ...records[idx],
      count: records[idx].count + 1,
      lastPickedAt: now,
      sequence: nextSequence,
    };
  } else {
    records.push({ carparkId, count: 1, lastPickedAt: now, sequence: nextSequence });
  }

  writeShortcutRecords(records);
  return records;
}

/**
 * Ranks records by pick-count (descending), tiebreaking on the most recent
 * pick (descending sequence), and returns at most `limit` (default 3, the
 * design doc's cap).
 */
export function getTopShortcuts(
  records: ShortcutRecord[],
  limit: number = MAX_SHORTCUTS_SHOWN,
): ShortcutRecord[] {
  return [...records]
    .sort((a, b) => b.count - a.count || b.sequence - a.sequence)
    .slice(0, limit);
}
