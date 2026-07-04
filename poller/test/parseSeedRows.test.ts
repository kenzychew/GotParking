import { describe, expect, it } from "vitest";

import { parseSeedRows } from "../src/index";

const POLLED_AT = "2026-07-03T12:00:00.000Z";

describe("parseSeedRows", () => {
  it("keeps only seed-list carparks and drops everything else", () => {
    const records = [
      { CarParkID: "1", AvailableLots: 10 },
      { CarParkID: "999", AvailableLots: 20 }, // not one of the 10 seed carparks
    ];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 10 }]);
  });

  it("parses a numeric-string AvailableLots the same as a number", () => {
    const records = [{ CarParkID: "2", AvailableLots: "45" }];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "2", polled_at: POLLED_AT, available_lots: 45 }]);
  });

  it("skips negative, non-integer, non-numeric, null, or blank lots instead of fabricating a zero", () => {
    const records = [
      { CarParkID: "1", AvailableLots: -5 },
      { CarParkID: "2", AvailableLots: 12.5 },
      { CarParkID: "3", AvailableLots: "not-a-number" },
      { CarParkID: "11", AvailableLots: null },
      { CarParkID: "13", AvailableLots: "" },
    ];
    expect(parseSeedRows(records, POLLED_AT)).toEqual([]);
  });

  it("takes the first valid record when a carpark ID repeats", () => {
    const records = [
      { CarParkID: "1", AvailableLots: 10 },
      { CarParkID: "1", AvailableLots: 999 },
    ];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 10 }]);
  });

  it("ignores non-object records without throwing", () => {
    const records: unknown[] = [null, "garbage", 42, { CarParkID: "1", AvailableLots: 7 }];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 7 }]);
  });

  it("returns an empty array for no input", () => {
    expect(parseSeedRows([], POLLED_AT)).toEqual([]);
  });
});
