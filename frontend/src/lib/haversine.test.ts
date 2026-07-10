import { describe, expect, it } from "vitest";
import { haversineDistanceMeters, sortByDistance } from "./haversine";

describe("haversineDistanceMeters", () => {
  it("returns ~0 for the same point", () => {
    expect(haversineDistanceMeters(1.29375, 103.85718, 1.29375, 103.85718)).toBeCloseTo(0, 1);
  });

  it("computes a known real-world distance (Suntec City to Marina Square, ~500m apart)", () => {
    // Suntec City: 1.29375, 103.85718 -- Marina Square: 1.29115, 103.85728
    const distance = haversineDistanceMeters(1.29375, 103.85718, 1.29115, 103.85728);
    expect(distance).toBeGreaterThan(250);
    expect(distance).toBeLessThan(400);
  });

  it("is symmetric", () => {
    const a = haversineDistanceMeters(1.29375, 103.85718, 1.3, 103.9);
    const b = haversineDistanceMeters(1.3, 103.9, 1.29375, 103.85718);
    expect(a).toBeCloseTo(b, 5);
  });
});

describe("sortByDistance", () => {
  const origin = { lat: 1.29375, lon: 103.85718 }; // Suntec City

  it("sorts nearest first", () => {
    const items = [
      { id: "far", latitude: 1.35, longitude: 103.95 },
      { id: "near", latitude: 1.29115, longitude: 103.85728 },
      { id: "medium", latitude: 1.3, longitude: 103.86 },
    ];

    const sorted = sortByDistance(items, origin.lat, origin.lon);

    expect(sorted.map((r) => r.item.id)).toEqual(["near", "medium", "far"]);
    expect(sorted[0].distanceMeters).toBeLessThan(sorted[1].distanceMeters);
    expect(sorted[1].distanceMeters).toBeLessThan(sorted[2].distanceMeters);
  });

  it("excludes items missing a coordinate entirely, rather than treating them as distance 0", () => {
    const items = [
      { id: "known", latitude: 1.29115, longitude: 103.85728 },
      { id: "unresolved", latitude: undefined, longitude: undefined },
      { id: "partial", latitude: 1.3 }, // longitude missing
    ];

    const sorted = sortByDistance(items, origin.lat, origin.lon);

    expect(sorted.map((r) => r.item.id)).toEqual(["known"]);
  });

  it("returns an empty array when nothing has coordinates", () => {
    const items: { id: string; latitude?: number; longitude?: number }[] = [
      { id: "a" },
      { id: "b" },
    ];

    expect(sortByDistance(items, origin.lat, origin.lon)).toEqual([]);
  });
});
