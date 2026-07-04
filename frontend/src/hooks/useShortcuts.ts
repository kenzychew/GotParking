import { useCallback, useMemo, useState } from "react";
import {
  getTopShortcuts,
  isLocalStorageAvailable,
  loadShortcutRecords,
  recordPick,
} from "../lib/shortcuts";
import { getSeedCarparkById } from "../seed/seedCarparks";

export interface ShortcutItem {
  carparkId: string;
  name: string;
}

export interface UseShortcutsResult {
  /** False when localStorage is unavailable (e.g. some private-browsing modes). */
  available: boolean;
  /** Up to 3 shortcuts, ranked by pick-count with most-recent as tiebreaker. */
  items: ShortcutItem[];
  pick: (carparkId: string) => void;
}

/** One-tap shortcuts (design doc Requirement 4): localStorage-only, max 3, ranked by pick-count. */
export function useShortcuts(): UseShortcutsResult {
  const [available] = useState<boolean>(() => isLocalStorageAvailable());
  const [records, setRecords] = useState(() => (available ? loadShortcutRecords() : []));

  const pick = useCallback(
    (carparkId: string): void => {
      if (!available) return;
      setRecords(recordPick(carparkId));
    },
    [available],
  );

  const items = useMemo<ShortcutItem[]>(
    () =>
      getTopShortcuts(records).map((record) => ({
        carparkId: record.carparkId,
        name: getSeedCarparkById(record.carparkId)?.name ?? record.carparkId,
      })),
    [records],
  );

  return { available, items, pick };
}
