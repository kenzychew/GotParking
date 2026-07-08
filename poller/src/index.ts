// GotParking T3 poller: Cloudflare Workers cron cycle that fetches LTA
// DataMall carpark availability, writes carpark_history + carpark_momentum
// rows to Supabase via PostgREST (plain fetch -- no supabase-js), pings
// healthchecks.io, and triggers the batch-predict endpoint. LTA parsing
// mirrors the validated reference script scripts/poll_lta_carparks.py.

import { SEED_CARPARK_ID_LIST, SEED_CARPARK_IDS } from "./carparks";
import { computeMomentum, HISTORY_LOOKBACK_MINUTES, type HistoryRow } from "./momentum";
import { sgtParts } from "./sgt";

export interface Env {
  LTA_API_KEY: string;
  SUPABASE_URL: string;
  SUPABASE_SERVICE_ROLE_KEY: string;
  // Unset until the T1.5 healthchecks.io check exists; pings are skipped.
  HEALTHCHECKS_POLLER_PING_URL?: string;
  // Unset until Lane D deploys; the trigger is skipped with a log line.
  BATCH_PREDICT_URL?: string;
  BATCH_SHARED_SECRET?: string;
}

export const LTA_ENDPOINT =
  "https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2";

const FETCH_TIMEOUT_MS = 10_000;
const RAW_BODY_LOG_LIMIT = 500;

type LogLevel = "info" | "warn" | "error";

function logEvent(level: LogLevel, event: string, fields: Record<string, unknown> = {}): void {
  const line = JSON.stringify({ event, ...fields });
  if (level === "error") {
    console.error(line);
  } else if (level === "warn") {
    console.warn(line);
  } else {
    console.log(line);
  }
}

function truncate(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}...[truncated]`;
}

// A failed cycle carries the short reason string that becomes the
// healthchecks /fail ping body.
class CycleError extends Error {
  constructor(
    readonly reason: string,
    message: string,
  ) {
    super(message);
    this.name = "CycleError";
  }
}

// --------------------------------------------------------------------------
// LTA DataMall fetch + parse
// --------------------------------------------------------------------------

interface LtaRecord {
  CarParkID?: unknown;
  AvailableLots?: unknown;
  LotType?: unknown;
}

async function fetchLtaSnapshot(env: Env): Promise<unknown[]> {
  // Retry ONCE on timeout/network error/5xx. Other HTTP errors (e.g. 401 on
  // a bad AccountKey) are not retried -- they will not heal within a cycle.
  for (let attempt = 1; ; attempt++) {
    let response: Response;
    try {
      response = await fetch(LTA_ENDPOINT, {
        headers: { AccountKey: env.LTA_API_KEY, accept: "application/json" },
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
    } catch (error) {
      if (attempt === 1) {
        logEvent("warn", "lta_fetch_retry", { attempt, error: String(error) });
        continue;
      }
      throw new CycleError("LTA_FETCH_FAILED", `LTA fetch failed after retry: ${String(error)}`);
    }

    if (response.status >= 500) {
      if (attempt === 1) {
        logEvent("warn", "lta_fetch_retry", { attempt, status: response.status });
        continue;
      }
      throw new CycleError("LTA_FETCH_FAILED", `LTA responded ${response.status} after retry`);
    }
    if (!response.ok) {
      throw new CycleError("LTA_FETCH_FAILED", `LTA responded ${response.status}`);
    }

    const raw = await response.text();
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch {
      logEvent("error", "lta_malformed_json", { rawBody: truncate(raw, RAW_BODY_LOG_LIMIT) });
      throw new CycleError("LTA_MALFORMED_JSON", "LTA response body was not valid JSON");
    }

    // Reference semantics: payload.get("value", []).
    const value = (payload as { value?: unknown }).value;
    if (!Array.isArray(value)) {
      logEvent("warn", "lta_missing_value", { payloadType: typeof payload });
      return [];
    }
    return value;
  }
}

export interface HistoryInsertRow {
  carpark_id: string;
  polled_at: string;
  available_lots: number;
}

function parseLots(raw: unknown): number | null {
  // Number(null) and Number("") are 0 -- never fabricate a zero reading.
  if (typeof raw === "number") {
    return Number.isInteger(raw) && raw >= 0 ? raw : null;
  }
  if (typeof raw === "string" && raw.trim() !== "") {
    const parsed = Number(raw);
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
  }
  return null;
}

export function parseSeedRows(records: readonly unknown[], polledAtIso: string): HistoryInsertRow[] {
  // LTA lists one row per (CarParkID, LotType) -- most carparks report a single
  // "C" (car) row, but ~18% of the full LTA feed also reports separate "Y"
  // (motorcycle) and/or "H" (heavy vehicle) rows sharing the SAME CarParkID
  // with a DIFFERENT AvailableLots (found live 2026-07-08 auditing the
  // full-feed coverage-expansion wave -- e.g. carpark A0007 reports 0 lots on
  // its "Y" row and 224 on its "C" row for the same poll). This product is a
  // car-parking forecaster, so only the "C" row is ever the right one --
  // taking "whichever record comes first in the feed" would silently pick a
  // motorcycle/heavy-vehicle reading depending on LTA's arbitrary JSON
  // ordering. Never true for the 10 original seed malls (verified in T1, all
  // single-LotType), which is exactly why this went uncaught until the
  // full-feed wave's candidate pool actually included multi-LotType carparks.
  const rows = new Map<string, HistoryInsertRow>();
  for (const record of records) {
    if (typeof record !== "object" || record === null) {
      continue;
    }
    const carparkId = String((record as LtaRecord).CarParkID ?? "");
    if (!SEED_CARPARK_IDS.has(carparkId) || rows.has(carparkId)) {
      continue;
    }
    if ((record as LtaRecord).LotType !== "C") {
      continue;
    }
    const lots = parseLots((record as LtaRecord).AvailableLots);
    if (lots === null) {
      // carpark_history CHECKs available_lots >= 0; one glitched record must
      // not poison the whole batch insert.
      logEvent("warn", "invalid_lots_skipped", {
        carparkId,
        availableLots: (record as LtaRecord).AvailableLots ?? null,
      });
      continue;
    }
    rows.set(carparkId, {
      carpark_id: carparkId,
      polled_at: polledAtIso,
      available_lots: lots,
    });
  }
  return [...rows.values()];
}

// --------------------------------------------------------------------------
// Supabase PostgREST access (plain fetch)
// --------------------------------------------------------------------------

interface SupabaseRequest {
  method: "GET" | "POST";
  /** Path with query string, starting at /rest/v1/... */
  path: string;
  prefer?: string;
  body?: unknown;
  /** For logs, e.g. "history_write". */
  context: string;
  /** /fail ping reason if the request fails after the retry. */
  failReason: string;
}

async function supabaseFetch(env: Env, request: SupabaseRequest): Promise<Response> {
  const url = `${env.SUPABASE_URL.replace(/\/+$/, "")}${request.path}`;
  const headers: Record<string, string> = {
    apikey: env.SUPABASE_SERVICE_ROLE_KEY,
    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
  };
  if (request.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (request.prefer !== undefined) {
    headers["Prefer"] = request.prefer;
  }
  const body = request.body !== undefined ? JSON.stringify(request.body) : undefined;

  for (let attempt = 1; ; attempt++) {
    let response: Response | undefined;
    let failure: string;
    try {
      response = await fetch(url, {
        method: request.method,
        headers,
        body,
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
    } catch (error) {
      failure = String(error);
    }

    if (response !== undefined) {
      if (response.ok) {
        return response;
      }
      const errorBody = truncate(await response.text().catch(() => ""), RAW_BODY_LOG_LIMIT);
      if (response.status === 401 || response.status === 403) {
        // Service-role requests must never hit RLS/permission walls; a
        // rejection means the key rotated or policies changed. CRITICAL --
        // alert immediately, never burn the retry on it.
        logEvent("error", "supabase_rls_rejected", {
          context: request.context,
          status: response.status,
          body: errorBody,
        });
        throw new CycleError(
          "RLS_REJECTED",
          `Supabase ${request.context} rejected with ${response.status}`,
        );
      }
      failure = `status ${response.status}: ${errorBody}`;
    } else {
      failure = failure!;
    }

    if (attempt === 1) {
      logEvent("warn", "supabase_retry", { context: request.context, failure });
      continue;
    }
    throw new CycleError(
      request.failReason,
      `Supabase ${request.context} failed after retry: ${failure}`,
    );
  }
}

async function writeHistory(env: Env, rows: readonly HistoryInsertRow[]): Promise<void> {
  if (rows.length === 0) {
    logEvent("warn", "history_write_skipped_empty", {});
    return;
  }
  // resolution=ignore-duplicates -> INSERT ... ON CONFLICT DO NOTHING against
  // the (carpark_id, polled_at) PK, so a retry after an ambiguous timeout can
  // never double-insert (design doc D4).
  await supabaseFetch(env, {
    method: "POST",
    path: "/rest/v1/carpark_history",
    prefer: "resolution=ignore-duplicates",
    body: rows,
    context: "history_write",
    failReason: "HISTORY_WRITE_FAILED",
  });
  logEvent("info", "history_written", { rows: rows.length });
}

async function updateMomentum(env: Env, now: Date): Promise<void> {
  const sinceIso = new Date(now.getTime() - HISTORY_LOOKBACK_MINUTES * 60_000).toISOString();
  const query = [
    "select=carpark_id,polled_at,available_lots",
    `polled_at=gte.${sinceIso}`,
    `carpark_id=in.(${SEED_CARPARK_ID_LIST.join(",")})`,
    "order=polled_at.desc",
  ].join("&");
  const response = await supabaseFetch(env, {
    method: "GET",
    path: `/rest/v1/carpark_history?${query}`,
    context: "momentum_read",
    failReason: "MOMENTUM_UPDATE_FAILED",
  });

  const payload: unknown = await response.json();
  let history: HistoryRow[];
  if (Array.isArray(payload)) {
    history = payload as HistoryRow[];
  } else {
    logEvent("warn", "momentum_history_shape", { payloadType: typeof payload });
    history = [];
  }

  const updatedAtIso = now.toISOString();
  const momentumRows = computeMomentum(history, SEED_CARPARK_ID_LIST, now).map((values) => ({
    ...values,
    updated_at: updatedAtIso,
  }));

  // merge-duplicates -> upsert on the carpark_id PK; updated_at is the D5
  // freshness signal the batch-predict run gates on.
  await supabaseFetch(env, {
    method: "POST",
    path: "/rest/v1/carpark_momentum",
    prefer: "resolution=merge-duplicates",
    body: momentumRows,
    context: "momentum_write",
    failReason: "MOMENTUM_UPDATE_FAILED",
  });
  logEvent("info", "momentum_written", { rows: momentumRows.length });
}

// --------------------------------------------------------------------------
// healthchecks.io pings + batch-predict trigger
// --------------------------------------------------------------------------

async function pingSuccess(env: Env): Promise<void> {
  if (!env.HEALTHCHECKS_POLLER_PING_URL) {
    return;
  }
  try {
    await fetch(env.HEALTHCHECKS_POLLER_PING_URL, {
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    logEvent("info", "healthchecks_success_ping_sent", {});
  } catch (error) {
    // A missed ping only risks a false "poller silent" alert; never fail an
    // otherwise-good cycle over it.
    logEvent("warn", "healthchecks_ping_failed", { error: String(error) });
  }
}

async function pingFail(env: Env, reason: string): Promise<void> {
  if (!env.HEALTHCHECKS_POLLER_PING_URL) {
    logEvent("warn", "healthchecks_fail_ping_skipped", { reason });
    return;
  }
  try {
    await fetch(`${env.HEALTHCHECKS_POLLER_PING_URL}/fail`, {
      method: "POST",
      body: reason,
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    logEvent("info", "healthchecks_fail_ping_sent", { reason });
  } catch (error) {
    logEvent("warn", "healthchecks_ping_failed", { error: String(error) });
  }
}

async function triggerBatchPredict(env: Env): Promise<void> {
  if (!env.BATCH_PREDICT_URL || !env.BATCH_SHARED_SECRET) {
    // Expected until Lane D deploys and T1.5 wires the secret.
    logEvent("info", "batch_predict_skipped", {
      hasUrl: Boolean(env.BATCH_PREDICT_URL),
      hasSecret: Boolean(env.BATCH_SHARED_SECRET),
    });
    return;
  }
  try {
    const response = await fetch(env.BATCH_PREDICT_URL, {
      method: "POST",
      headers: { "x-batch-secret": env.BATCH_SHARED_SECRET },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      logEvent("warn", "batch_predict_trigger_failed", { status: response.status });
      return;
    }
    logEvent("info", "batch_predict_triggered", { status: response.status });
  } catch (error) {
    // Forecast staleness is surfaced by the frontend's generated_at caveat
    // (D10); a failed trigger must not fail the poll cycle.
    logEvent("warn", "batch_predict_trigger_failed", { error: String(error) });
  }
}

// --------------------------------------------------------------------------
// Cycle orchestration
// --------------------------------------------------------------------------

export async function runCycle(env: Env, now: Date): Promise<void> {
  const polledAtIso = now.toISOString();
  const sgt = sgtParts(now);
  logEvent("info", "poll_cycle_start", {
    polledAt: polledAtIso,
    sgtDow: sgt.dow,
    sgtSlotOfDay: sgt.slotOfDay,
  });

  try {
    const records = await fetchLtaSnapshot(env);
    const rows = parseSeedRows(records, polledAtIso);
    if (rows.length < SEED_CARPARK_ID_LIST.length) {
      logEvent("warn", "seed_rows_missing", {
        expected: SEED_CARPARK_ID_LIST.length,
        parsed: rows.length,
      });
    }
    await writeHistory(env, rows);
    await updateMomentum(env, now);
  } catch (error) {
    const reason = error instanceof CycleError ? error.reason : "UNEXPECTED_ERROR";
    logEvent("error", "poll_cycle_failed", { reason, error: String(error) });
    await pingFail(env, reason);
    return;
  }

  await pingSuccess(env);
  await triggerBatchPredict(env);
  logEvent("info", "poll_cycle_complete", { polledAt: polledAtIso });
}

const worker = {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(runCycle(env, new Date(controller.scheduledTime)));
  },
} satisfies ExportedHandler<Env>;

export default worker;
