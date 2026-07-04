import { describe, expect, it } from "vitest";
import { isStalePayload, minutesSince, STALE_THRESHOLD_MINUTES } from "./staleness";

const NOW = new Date("2026-07-05T12:00:00Z");

describe("minutesSince", () => {
  it("computes whole minutes elapsed", () => {
    const generatedAt = new Date(NOW.getTime() - 5 * 60_000).toISOString();
    expect(minutesSince(generatedAt, NOW)).toBe(5);
  });

  it("floors partial minutes", () => {
    const generatedAt = new Date(NOW.getTime() - 5.9 * 60_000).toISOString();
    expect(minutesSince(generatedAt, NOW)).toBe(5);
  });

  it("never returns a negative number for a generatedAt in the future", () => {
    const generatedAt = new Date(NOW.getTime() + 60_000).toISOString();
    expect(minutesSince(generatedAt, NOW)).toBe(0);
  });
});

describe("isStalePayload", () => {
  it(`is not stale at exactly the ${STALE_THRESHOLD_MINUTES}-minute threshold`, () => {
    const generatedAt = new Date(NOW.getTime() - STALE_THRESHOLD_MINUTES * 60_000).toISOString();
    expect(isStalePayload(generatedAt, NOW)).toBe(false);
  });

  it("is stale just past the threshold", () => {
    const generatedAt = new Date(
      NOW.getTime() - (STALE_THRESHOLD_MINUTES + 1) * 60_000,
    ).toISOString();
    expect(isStalePayload(generatedAt, NOW)).toBe(true);
  });

  it("is not stale for a fresh payload", () => {
    const generatedAt = new Date(NOW.getTime() - 60_000).toISOString();
    expect(isStalePayload(generatedAt, NOW)).toBe(false);
  });
});
