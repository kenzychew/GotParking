import { describe, expect, it } from "vitest";

import { sgtParts } from "../src/sgt";

describe("sgtParts", () => {
  it("maps Fri 18:00 UTC to Sat 02:00 SGT: dow=5 (Saturday, Monday=0), slotOfDay=8", () => {
    // 2026-07-03 is a real-calendar Friday (verified independently); adding the
    // fixed UTC+8 offset crosses midnight into Saturday. This is the exact
    // boundary case named in the design doc's poller Test Requirements row.
    const fridayEighteenUtc = new Date(Date.UTC(2026, 6, 3, 18, 0, 0));
    expect(sgtParts(fridayEighteenUtc)).toEqual({ dow: 5, slotOfDay: 8 });
  });

  it("maps Monday 00:00 SGT (Sun 16:00 UTC) to dow=0, slotOfDay=0", () => {
    const mondayMidnightSgt = new Date(Date.UTC(2026, 6, 5, 16, 0, 0));
    expect(sgtParts(mondayMidnightSgt)).toEqual({ dow: 0, slotOfDay: 0 });
  });

  it("maps Sunday 23:50 SGT (Sun 15:50 UTC) to dow=6, the last slot of the day (95)", () => {
    const sundayLateSgt = new Date(Date.UTC(2026, 6, 5, 15, 50, 0));
    expect(sgtParts(sundayLateSgt)).toEqual({ dow: 6, slotOfDay: 95 });
  });

  it("buckets minutes within a 15-minute slot together", () => {
    const start = sgtParts(new Date(Date.UTC(2026, 6, 5, 16, 0, 0)));
    const midSlot = sgtParts(new Date(Date.UTC(2026, 6, 5, 16, 14, 59)));
    expect(midSlot).toEqual(start);
  });
});
