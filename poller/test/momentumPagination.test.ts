// Regression tests for the momentum-read pagination (found live 2026-07-15):
// PostgREST silently truncates responses at the server's max-rows setting
// (Supabase default 1000). At 268 carparks the 65-minute lookback holds
// ~3,500 rows, so the unpaginated read returned only the newest ~15 minutes
// and every carpark's lots_30m_ago/lots_60m_ago stayed null -- the promoted
// model never served because batch-predict's momentum-usable gate failed for
// all 268 carparks. These tests drive runCycle end-to-end with scripted
// paginated responses and assert the momentum upsert sees ALL pages.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { runCycle } from "../src/index";
import { SEED_CARPARK_ID_LIST } from "../src/carparks";
import {
  DEFAULT_SUPABASE_URL,
  buildBaseRouter,
  fullLtaRecords,
  jsonResponse,
  makeEnv,
} from "./support/harness";

const NOW = new Date("2026-07-03T12:00:00.000Z");
const HISTORY_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/carpark_history`;
const MOMENTUM_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/carpark_momentum`;
const PAGE_SIZE = 1000;

interface MomentumUpsertRow {
  carpark_id: string;
  lots_15m_ago: number | null;
  lots_30m_ago: number | null;
  lots_60m_ago: number | null;
}

function historyRow(carparkId: string, minutesAgo: number, lots: number) {
  return {
    carpark_id: carparkId,
    polled_at: new Date(NOW.getTime() - minutesAgo * 60_000).toISOString(),
    available_lots: lots,
  };
}

/** Filler rows for a carpark far from any 15/30/60m offset (never within tolerance). */
function fillerRows(count: number): unknown[] {
  return Array.from({ length: count }, (_, i) => historyRow("2", 44 + (i % 3) / 10, 1));
}

function momentumUpsertRows(router: { calls: ReadonlyArray<{ url: string; method: string; body: string | undefined }> }): MomentumUpsertRow[] {
  const post = router.calls.find((c) => c.url === MOMENTUM_URL && c.method === "POST");
  return JSON.parse(post?.body ?? "[]") as MomentumUpsertRow[];
}

function momentumGetCalls(router: { calls: ReadonlyArray<{ url: string; method: string }> }) {
  return router.calls.filter((c) => c.method === "GET" && c.url.startsWith(`${HISTORY_URL}?`));
}

beforeEach(() => {
  vi.spyOn(console, "log").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("momentum read pagination", () => {
  it("requests pages with limit/offset and deterministic ascending order", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, {
      ltaValue: fullLtaRecords(),
      momentumHistory: [historyRow("1", 15, 111)],
    });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const gets = momentumGetCalls(router);
    expect(gets).toHaveLength(1);
    expect(gets[0]?.url).toContain("order=polled_at.asc,carpark_id.asc");
    expect(gets[0]?.url).toContain(`limit=${PAGE_SIZE}`);
    expect(gets[0]?.url).toContain("offset=0");
  });

  it("fetches every page when the first page is exactly max-rows full", async () => {
    // Page 0: exactly 1000 filler rows (simulating PostgREST's cap kicking
    // in) -- the 60m reading for carpark "1" only exists on page 1. Before
    // pagination, that reading was silently dropped and lots_60m_ago was null.
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    const pages: unknown[][] = [
      fillerRows(PAGE_SIZE),
      [historyRow("1", 60, 600), historyRow("1", 30, 300), historyRow("1", 15, 150)],
    ];
    router.on(
      (call) => call.method === "GET" && call.url.startsWith(`${HISTORY_URL}?`),
      (call) => {
        const offset = Number(new URL(call.url).searchParams.get("offset"));
        return jsonResponse(pages[offset / PAGE_SIZE] ?? []);
      },
    );
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const gets = momentumGetCalls(router);
    expect(gets).toHaveLength(2);
    expect(gets[0]?.url).toContain("offset=0");
    expect(gets[1]?.url).toContain(`offset=${PAGE_SIZE}`);

    const row = momentumUpsertRows(router).find((r) => r.carpark_id === "1");
    expect(row).toEqual({
      carpark_id: "1",
      lots_15m_ago: 150,
      lots_30m_ago: 300,
      lots_60m_ago: 600,
      updated_at: NOW.toISOString(),
    });
  });

  it("stops at the page cap with a warning instead of looping forever", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    // Every page comes back completely full -- a pathological response that
    // must trip the backstop, not hang the cron invocation.
    router.on(
      (call) => call.method === "GET" && call.url.startsWith(`${HISTORY_URL}?`),
      () => jsonResponse(fillerRows(PAGE_SIZE)),
    );
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(momentumGetCalls(router)).toHaveLength(20);
    const warns = vi.mocked(console.warn).mock.calls.map((args) => String(args[0]));
    expect(warns.some((w) => w.includes("momentum_history_page_cap"))).toBe(true);
    // Still upserts what it got -- degraded, never fatal.
    expect(momentumUpsertRows(router)).toHaveLength(SEED_CARPARK_ID_LIST.length);
  });

  it("a short first page still completes in one request (pre-expansion behavior intact)", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, {
      ltaValue: fullLtaRecords(),
      momentumHistory: [historyRow("1", 15, 111), historyRow("1", 30, 222)],
    });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(momentumGetCalls(router)).toHaveLength(1);
    const row = momentumUpsertRows(router).find((r) => r.carpark_id === "1");
    expect(row?.lots_15m_ago).toBe(111);
    expect(row?.lots_30m_ago).toBe(222);
    expect(row?.lots_60m_ago).toBeNull();
  });
});
