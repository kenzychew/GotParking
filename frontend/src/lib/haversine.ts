// Great-circle distance between two WGS84 coordinates, used for the postal-code search's
// nearest-carpark sort. Client-side because the input set is tiny (a few hundred carparks,
// all already embedded in seedCarparks.ts) -- no reason to round-trip a distance
// calculation the browser can do in microseconds.

const EARTH_RADIUS_METERS = 6_371_000;

function toRadians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

/** Great-circle distance in meters between two WGS84 points (haversine formula). */
export function haversineDistanceMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const dLat = toRadians(lat2 - lat1);
  const dLon = toRadians(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRadians(lat1)) * Math.cos(toRadians(lat2)) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return EARTH_RADIUS_METERS * c;
}

export interface WithCoordinates {
  latitude?: number;
  longitude?: number;
}

export interface WithDistance<T> {
  item: T;
  distanceMeters: number;
}

/**
 * Sort `items` by distance from (originLat, originLon), nearest first. Items missing a
 * coordinate (OneMap couldn't resolve them, or they were never enriched) are excluded
 * entirely -- a carpark with no known location can't meaningfully be "near" anything, and
 * silently treating it as distance-0 or distance-Infinity would both be misleading.
 */
export function sortByDistance<T extends WithCoordinates>(
  items: readonly T[],
  originLat: number,
  originLon: number,
): WithDistance<T>[] {
  const withCoords = items.filter(
    (item): item is T & { latitude: number; longitude: number } =>
      item.latitude !== undefined && item.longitude !== undefined,
  );
  return withCoords
    .map((item) => ({
      item,
      distanceMeters: haversineDistanceMeters(originLat, originLon, item.latitude, item.longitude),
    }))
    .sort((a, b) => a.distanceMeters - b.distanceMeters);
}
