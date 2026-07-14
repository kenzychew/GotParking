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
  // Unset until the daily baseline-refresh healthchecks.io check exists;
  // pings are skipped. Deliberately separate from HEALTHCHECKS_POLLER_PING_URL
  // (see wrangler.toml).
  HEALTHCHECKS_BASELINE_PING_URL?: string;
}

export const LTA_ENDPOINT =
  "https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2";

// Must byte-match wrangler.toml's [triggers] crons array -- pinned by
// test/wranglerConfig.test.ts.
export const POLL_CRON = "*/5 * * * *";
export const BASELINE_REFRESH_CRON = "17 19 * * *";

const FETCH_TIMEOUT_MS = 10_000;
// The baseline refresh runs one aggregate RPC over 28 days of history, so it
// gets a larger budget than the 10s row-write default.
const BASELINE_REFRESH_TIMEOUT_MS = 30_000;
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
  /** Per-request override of FETCH_TIMEOUT_MS. */
  timeoutMs?: number;
  /**
   * When false (default true), a fetch failure whose error is a timeout
   * (AbortSignal.timeout throws an Error named "TimeoutError") is NOT
   * retried: a timed-out statement may still be running server-side, and
   * retrying would run two concurrent heavy aggregations. Non-timeout
   * network errors and 5xx responses still retry as before.
   */
  retryOnTimeout?: boolean;
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
  const timeoutMs = request.timeoutMs ?? FETCH_TIMEOUT_MS;
  const retryOnTimeout = request.retryOnTimeout ?? true;

  for (let attempt = 1; ; attempt++) {
    let response: Response | undefined;
    let failure: string;
    try {
      response = await fetch(url, {
        method: request.method,
        headers,
        body,
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      // AbortSignal.timeout rejects with an Error named "TimeoutError". A
      // timed-out statement may still be running server-side, so a caller
      // that opted out of retryOnTimeout must fail fast here rather than
      // risk two concurrent heavy aggregations.
      if (!retryOnTimeout && error instanceof Error && error.name === "TimeoutError") {
        throw new CycleError(
          request.failReason,
          `Supabase ${request.context} timed out (no retry): ${String(error)}`,
        );
      }
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

// PostgREST silently truncates any response at the server's max-rows setting
// (Supabase default: 1000 rows) -- a limit above it in the query does NOT
// raise the ceiling, only offset pagination gets past it. Found live
// 2026-07-15: at 268 carparks the 65-minute lookback holds ~3,500 rows, so
// the unpaginated read returned only the newest ~15 minutes, every carpark's
// lots_30m_ago/lots_60m_ago stayed null, and the promoted model never served
// (batch-predict's momentum-usable gate failed for all 268).
const MOMENTUM_PAGE_SIZE = 1000;
// 20 pages = 20k rows, far above any sane 65-minute window (268 carparks x 13
// polls is ~3.5k) -- purely a runaway-loop backstop, warned on if ever hit.
const MOMENTUM_MAX_PAGES = 20;

async function fetchMomentumHistory(env: Env, sinceIso: string): Promise<HistoryRow[]> {
  const history: HistoryRow[] = [];
  for (let page = 0; ; page++) {
    if (page >= MOMENTUM_MAX_PAGES) {
      logEvent("warn", "momentum_history_page_cap", {
        pages: MOMENTUM_MAX_PAGES,
        rows: history.length,
      });
      break;
    }
    // Ascending with a carpark_id tiebreak: fully deterministic order, and
    // rows written between page fetches land at the tail of the ordering, so
    // they can never shift earlier pages' offsets underneath the loop.
    const query = [
      "select=carpark_id,polled_at,available_lots",
      `polled_at=gte.${sinceIso}`,
      `carpark_id=in.(${SEED_CARPARK_ID_LIST.join(",")})`,
      "order=polled_at.asc,carpark_id.asc",
      `limit=${MOMENTUM_PAGE_SIZE}`,
      `offset=${page * MOMENTUM_PAGE_SIZE}`,
    ].join("&");
    const response = await supabaseFetch(env, {
      method: "GET",
      path: `/rest/v1/carpark_history?${query}`,
      context: "momentum_read",
      failReason: "MOMENTUM_UPDATE_FAILED",
    });
    const payload: unknown = await response.json();
    if (!Array.isArray(payload)) {
      logEvent("warn", "momentum_history_shape", { payloadType: typeof payload, page });
      break;
    }
    history.push(...(payload as HistoryRow[]));
    if (payload.length < MOMENTUM_PAGE_SIZE) {
      break;
    }
  }
  return history;
}

async function updateMomentum(env: Env, now: Date): Promise<void> {
  const sinceIso = new Date(now.getTime() - HISTORY_LOOKBACK_MINUTES * 60_000).toISOString();
  const history = await fetchMomentumHistory(env, sinceIso);

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

async function pingSuccess(url: string | undefined): Promise<void> {
  if (!url) {
    return;
  }
  try {
    await fetch(url, {
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    logEvent("info", "healthchecks_success_ping_sent", {});
  } catch (error) {
    // A missed ping only risks a false "silent" alert; never fail an
    // otherwise-good run over it.
    logEvent("warn", "healthchecks_ping_failed", { error: String(error) });
  }
}

async function pingFail(url: string | undefined, reason: string): Promise<void> {
  if (!url) {
    logEvent("warn", "healthchecks_fail_ping_skipped", { reason });
    return;
  }
  try {
    await fetch(`${url}/fail`, {
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
    await pingFail(env.HEALTHCHECKS_POLLER_PING_URL, reason);
    return;
  }

  await pingSuccess(env.HEALTHCHECKS_POLLER_PING_URL);
  await triggerBatchPredict(env);
  logEvent("info", "poll_cycle_complete", { polledAt: polledAtIso });
}

// Daily Premise #11 refresh: rebuilds carpark_baseline from the trailing 28
// days of history via the refresh_carpark_baseline() Postgres function. Body
// is {} because the RPC takes no arguments; the response is the upserted row
// count (logged, not otherwise used).
export async function runBaselineRefresh(env: Env): Promise<void> {
  try {
    const response = await supabaseFetch(env, {
      method: "POST",
      path: "/rest/v1/rpc/refresh_carpark_baseline",
      body: {},
      context: "baseline_refresh",
      failReason: "BASELINE_REFRESH_FAILED",
      timeoutMs: BASELINE_REFRESH_TIMEOUT_MS,
      retryOnTimeout: false,
    });
    const upserted: unknown = await response.json();
    logEvent("info", "baseline_refresh_complete", { upserted });
    await pingSuccess(env.HEALTHCHECKS_BASELINE_PING_URL);
  } catch (error) {
    // Mirrors runCycle's catch: EVERY failure (CycleError or not -- e.g. a
    // non-JSON RPC body) is caught, logged, and /fail-pinged; the handler
    // never rejects into ctx.waitUntil.
    const reason = error instanceof CycleError ? error.reason : "UNEXPECTED_ERROR";
    logEvent("error", "baseline_refresh_failed", { reason, error: String(error) });
    await pingFail(env.HEALTHCHECKS_BASELINE_PING_URL, reason);
  }
}

const worker = {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    if (controller.cron === BASELINE_REFRESH_CRON) {
      ctx.waitUntil(runBaselineRefresh(env));
      return;
    }
    if (controller.cron !== POLL_CRON) {
      // Unknown cron (e.g. a dashboard-triggered test event) preserves the
      // old behavior -- run a poll cycle -- but loudly, so a drifted
      // wrangler.toml cron string cannot silently kill the baseline refresh.
      logEvent("warn", "unknown_cron", { cron: controller.cron });
    }
    ctx.waitUntil(runCycle(env, new Date(controller.scheduledTime)));
  },
} satisfies ExportedHandler<Env>;

export default worker;
