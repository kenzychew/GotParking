import { describe, expect, it } from "vitest";

import { computeMomentum, type HistoryRow } from "../src/momentum";

const NOW = new Date("2026-07-03T12:00:00.000Z");
const MIN_MS = 60_000;

function reading(carparkId: string, minutesAgo: number, lots: number): HistoryRow {
  return {
    carpark_id: carparkId,
    polled_at: new Date(NOW.getTime() - minutesAgo * MIN_MS).toISOString(),
    available_lots: lots,
  };
}

describe("computeMomentum", () => {
  it("finds the exact reading at each of the 15/30/60 min offsets when present", () => {
    const history: HistoryRow[] = [reading("A", 15, 50), reading("A", 30, 80), reading("A", 60, 120)];
    const [result] = computeMomentum(history, ["A"], NOW);
    expect(result).toEqual({
      carpark_id: "A",
      lots_15m_ago: 50,
      lots_30m_ago: 80,
      lots_60m_ago: 120,
    });
  });

  it("returns null for every offset when a carpark has no history rows", () => {
    const [result] = computeMomentum([], ["B"], NOW);
    expect(result).toEqual({
      carpark_id: "B",
      lots_15m_ago: null,
      lots_30m_ago: null,
      lots_60m_ago: null,
    });
  });

  it("accepts a reading exactly 2.5 minutes off an offset (inclusive tolerance boundary)", () => {
    const history: HistoryRow[] = [reading("C", 17.5, 77)];
    const [result] = computeMomentum(history, ["C"], NOW);
    expect(result?.lots_15m_ago).toBe(77);
    expect(result?.lots_30m_ago).toBeNull();
    expect(result?.lots_60m_ago).toBeNull();
  });

  it("rejects a reading 1ms beyond the 2.5 minute tolerance", () => {
    const justOutside = new Date(NOW.getTime() - 15 * MIN_MS - 2.5 * MIN_MS - 1).toISOString();
    const history: HistoryRow[] = [{ carpark_id: "D", polled_at: justOutside, available_lots: 99 }];
    const [result] = computeMomentum(history, ["D"], NOW);
    expect(result?.lots_15m_ago).toBeNull();
  });

  it("picks the nearest candidate to an offset, not just the first one within tolerance", () => {
    // 13 min ago is within tolerance of the 15-min target (2 min off) but listed
    // first; 14.5 min ago is closer (0.5 min off) but listed second. A "first
    // match" implementation would wrongly return the farther one.
    const history: HistoryRow[] = [reading("E", 13, 111), reading("E", 14.5, 222)];
    const [result] = computeMomentum(history, ["E"], NOW);
    expect(result?.lots_15m_ago).toBe(222);
  });

  it("keeps each carpark's readings independent of the others", () => {
    const history: HistoryRow[] = [reading("A", 15, 50), reading("B", 15, 999)];
    const results = computeMomentum(history, ["A", "B"], NOW);
    expect(results.find((r) => r.carpark_id === "A")?.lots_15m_ago).toBe(50);
    expect(results.find((r) => r.carpark_id === "B")?.lots_15m_ago).toBe(999);
  });

  it("ignores rows with an unparseable polled_at instead of throwing", () => {
    const history: HistoryRow[] = [{ carpark_id: "F", polled_at: "not-a-date", available_lots: 1 }];
    const [result] = computeMomentum(history, ["F"], NOW);
    expect(result).toEqual({
      carpark_id: "F",
      lots_15m_ago: null,
      lots_30m_ago: null,
      lots_60m_ago: null,
    });
  });

  it("returns one row per requested seed ID, in the requested order", () => {
    const results = computeMomentum([], ["X", "Y", "Z"], NOW);
    expect(results.map((r) => r.carpark_id)).toEqual(["X", "Y", "Z"]);
  });
});
