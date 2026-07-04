// Momentum feature computation (Premises #2/#11): for each seed carpark,
// the available-lots reading nearest 15/30/60 minutes ago, taken only if it
// falls within +/-2.5 minutes of the offset -- the same tolerance the
// training job uses for label joins, so a poll gap yields null instead of a
// misleadingly fresh-looking value.

export interface HistoryRow {
  carpark_id: string;
  polled_at: string;
  available_lots: number;
}

export interface MomentumValues {
  carpark_id: string;
  lots_15m_ago: number | null;
  lots_30m_ago: number | null;
  lots_60m_ago: number | null;
}

// 60-minute offset + 2.5-minute tolerance = 62.5 minutes of lookback needed;
// 65 leaves margin without pulling meaningfully more rows.
export const HISTORY_LOOKBACK_MINUTES = 65;

const OFFSET_TOLERANCE_MS = 2.5 * 60_000;

interface Reading {
  at: number;
  lots: number;
}

function nearestWithinTolerance(
  readings: readonly Reading[],
  now: Date,
  offsetMinutes: number,
): number | null {
  const target = now.getTime() - offsetMinutes * 60_000;
  let best: Reading | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const reading of readings) {
    const distance = Math.abs(reading.at - target);
    if (distance < bestDistance) {
      best = reading;
      bestDistance = distance;
    }
  }
  return best !== null && bestDistance <= OFFSET_TOLERANCE_MS ? best.lots : null;
}

export function computeMomentum(
  history: readonly HistoryRow[],
  seedIds: readonly string[],
  now: Date,
): MomentumValues[] {
  const byCarpark = new Map<string, Reading[]>();
  for (const row of history) {
    const at = Date.parse(row.polled_at);
    if (Number.isNaN(at)) {
      continue;
    }
    let readings = byCarpark.get(row.carpark_id);
    if (!readings) {
      readings = [];
      byCarpark.set(row.carpark_id, readings);
    }
    readings.push({ at, lots: row.available_lots });
  }

  return seedIds.map((carparkId) => {
    const readings = byCarpark.get(carparkId) ?? [];
    return {
      carpark_id: carparkId,
      lots_15m_ago: nearestWithinTolerance(readings, now, 15),
      lots_30m_ago: nearestWithinTolerance(readings, now, 30),
      lots_60m_ago: nearestWithinTolerance(readings, now, 60),
    };
  });
}
