import { describe, expect, it } from "vitest";

import { parseSeedRows } from "../src/index";

const POLLED_AT = "2026-07-03T12:00:00.000Z";

describe("parseSeedRows", () => {
  it("keeps only seed-list carparks and drops everything else", () => {
    const records = [
      { CarParkID: "1", AvailableLots: 10, LotType: "C" },
      { CarParkID: "999", AvailableLots: 20, LotType: "C" }, // not one of the seed carparks
    ];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 10 }]);
  });

  it("parses a numeric-string AvailableLots the same as a number", () => {
    const records = [{ CarParkID: "2", AvailableLots: "45", LotType: "C" }];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "2", polled_at: POLLED_AT, available_lots: 45 }]);
  });

  it("skips negative, non-integer, non-numeric, null, or blank lots instead of fabricating a zero", () => {
    const records = [
      { CarParkID: "1", AvailableLots: -5, LotType: "C" },
      { CarParkID: "2", AvailableLots: 12.5, LotType: "C" },
      { CarParkID: "3", AvailableLots: "not-a-number", LotType: "C" },
      { CarParkID: "11", AvailableLots: null, LotType: "C" },
      { CarParkID: "13", AvailableLots: "", LotType: "C" },
    ];
    expect(parseSeedRows(records, POLLED_AT)).toEqual([]);
  });

  it("takes the first valid record when a carpark ID repeats (both LotType=C)", () => {
    const records = [
      { CarParkID: "1", AvailableLots: 10, LotType: "C" },
      { CarParkID: "1", AvailableLots: 999, LotType: "C" },
    ];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 10 }]);
  });

  it("ignores non-object records without throwing", () => {
    const records: unknown[] = [null, "garbage", 42, { CarParkID: "1", AvailableLots: 7, LotType: "C" }];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 7 }]);
  });

  it("returns an empty array for no input", () => {
    expect(parseSeedRows([], POLLED_AT)).toEqual([]);
  });

  it("regression: picks the C (car) row, never Y (motorcycle) or H (heavy vehicle), when a carpark reports multiple LotTypes", () => {
    // Real shape found live 2026-07-08 auditing the full-feed coverage-expansion wave:
    // carpark A0007 reported 0 lots on its Y row and 224 on its C row in the same poll.
    // "First record wins" alone (pre-fix) would have picked whichever LotType happened to
    // come first in LTA's arbitrary JSON order -- wrong for a car-parking product.
    const records = [
      { CarParkID: "1", AvailableLots: 0, LotType: "Y" },
      { CarParkID: "1", AvailableLots: 224, LotType: "C" },
      { CarParkID: "1", AvailableLots: 5, LotType: "H" },
    ];
    const rows = parseSeedRows(records, POLLED_AT);
    expect(rows).toEqual([{ carpark_id: "1", polled_at: POLLED_AT, available_lots: 224 }]);
  });

  it("regression: a carpark with no C row at all is excluded entirely, not fallen back to Y/H", () => {
    // Real shape found live 2026-07-08: "42 Defu Lane 7 HVP" (heavy-vehicle-only lot) has
    // zero C rows -- correctly out of scope for a car-parking forecaster, not a carpark to
    // silently serve motorcycle/heavy-vehicle data for.
    const records = [{ CarParkID: "1", AvailableLots: 12, LotType: "H" }];
    expect(parseSeedRows(records, POLLED_AT)).toEqual([]);
  });
});
