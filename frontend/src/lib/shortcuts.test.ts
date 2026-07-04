import { describe, expect, it, vi } from "vitest";
import {
  getTopShortcuts,
  isLocalStorageAvailable,
  loadShortcutRecords,
  MAX_SHORTCUTS_SHOWN,
  recordPick,
} from "./shortcuts";

describe("recordPick / loadShortcutRecords", () => {
  it("creates a new record with count 1 on the first pick", () => {
    recordPick("1");
    const records = loadShortcutRecords();
    expect(records).toEqual([
      expect.objectContaining({ carparkId: "1", count: 1 }),
    ]);
  });

  it("increments count on repeated picks of the same carpark", () => {
    recordPick("1");
    recordPick("1");
    recordPick("1");
    const records = loadShortcutRecords();
    expect(records).toHaveLength(1);
    expect(records[0].count).toBe(3);
  });

  it("persists across a fresh read (simulated reload)", () => {
    recordPick("2");
    recordPick("2");
    // A fresh call to loadShortcutRecords() re-reads from localStorage --
    // nothing is cached in module state -- simulating a page reload.
    const records = loadShortcutRecords();
    expect(records.find((r) => r.carparkId === "2")?.count).toBe(2);
  });

  it("recovers gracefully from corrupt stored JSON", () => {
    window.localStorage.setItem("gotparking:shortcuts-v1", "not json{{{");
    expect(loadShortcutRecords()).toEqual([]);
    // and a subsequent pick still works, overwriting the corrupt value
    recordPick("1");
    expect(loadShortcutRecords()).toHaveLength(1);
  });
});

describe("getTopShortcuts ranking", () => {
  it("ranks by pick-count descending", () => {
    recordPick("1"); // count 1
    recordPick("2");
    recordPick("2"); // count 2
    const top = getTopShortcuts(loadShortcutRecords());
    expect(top[0].carparkId).toBe("2");
    expect(top[1].carparkId).toBe("1");
  });

  it("uses most-recent pick as the tiebreaker for equal counts", () => {
    recordPick("1"); // count 1, picked first
    recordPick("2"); // count 1, picked second (more recent)
    const top = getTopShortcuts(loadShortcutRecords());
    expect(top[0].carparkId).toBe("2");
    expect(top[1].carparkId).toBe("1");
  });

  it("caps the displayed list at MAX_SHORTCUTS_SHOWN (3)", () => {
    recordPick("1");
    recordPick("2");
    recordPick("3");
    recordPick("11");
    expect(MAX_SHORTCUTS_SHOWN).toBe(3);
    const top = getTopShortcuts(loadShortcutRecords());
    expect(top).toHaveLength(3);
    // most recently picked 3 of the 4 (all tied at count 1) should win
    expect(top.map((r) => r.carparkId)).toEqual(["11", "3", "2"]);
  });

  it("a later pick can overtake the display window once its count rises", () => {
    recordPick("1");
    recordPick("2");
    recordPick("3");
    recordPick("11"); // 4th distinct carpark, all count=1 so far -> "1" is dropped
    recordPick("1"); // now count=2, should re-enter the top 3
    const top = getTopShortcuts(loadShortcutRecords());
    expect(top.map((r) => r.carparkId)).toContain("1");
    expect(top[0].carparkId).toBe("1");
  });
});

describe("isLocalStorageAvailable", () => {
  it("returns true in a normal environment", () => {
    expect(isLocalStorageAvailable()).toBe(true);
  });

  it("returns false when localStorage throws (e.g. some private-browsing modes)", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(isLocalStorageAvailable()).toBe(false);
    spy.mockRestore();
  });
});
