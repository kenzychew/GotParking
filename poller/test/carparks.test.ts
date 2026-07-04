import { describe, expect, it } from "vitest";

import { SEED_CARPARK_ID_LIST, SEED_CARPARK_IDS, SEED_CARPARK_NAMES } from "../src/carparks";

describe("seed carpark list", () => {
  it("contains exactly the 10 T1-validated seed carpark IDs", () => {
    const expected = ["1", "2", "3", "11", "13", "15", "16", "21", "24", "50"];
    expect(new Set(SEED_CARPARK_ID_LIST)).toEqual(new Set(expected));
    expect(SEED_CARPARK_ID_LIST.length).toBe(10);
  });

  it("keeps SEED_CARPARK_IDS and SEED_CARPARK_NAMES in sync with the ID list", () => {
    for (const id of SEED_CARPARK_ID_LIST) {
      expect(SEED_CARPARK_IDS.has(id)).toBe(true);
      expect(SEED_CARPARK_NAMES[id]).toBeTruthy();
    }
    expect(SEED_CARPARK_IDS.size).toBe(SEED_CARPARK_ID_LIST.length);
  });
});
