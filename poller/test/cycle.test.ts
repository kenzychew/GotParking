import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LTA_ENDPOINT, runCycle } from "../src/index";
import { SEED_CARPARK_ID_LIST } from "../src/carparks";
import {
  DEFAULT_BATCH_PREDICT_URL,
  DEFAULT_HEALTHCHECKS_URL,
  DEFAULT_SUPABASE_URL,
  buildBaseRouter,
  fullLtaRecords,
  jsonResponse,
  makeEnv,
  okResponse,
  textResponse,
} from "./support/harness";

const NOW = new Date("2026-07-03T12:00:00.000Z");
const HISTORY_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/carpark_history`;
const MOMENTUM_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/carpark_momentum`;
const FAIL_PING_URL = `${DEFAULT_HEALTHCHECKS_URL}/fail`;

beforeEach(() => {
  vi.spyOn(console, "log").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("runCycle: happy path", () => {
  it("fetches LTA, writes history + momentum, pings success, and triggers batch-predict", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const ltaCalls = router.calls.filter((c) => c.url === LTA_ENDPOINT);
    expect(ltaCalls).toHaveLength(1);
    expect(ltaCalls[0]?.headers["accountkey"]).toBe(env.LTA_API_KEY);

    const historyCall = router.calls.find((c) => c.url === HISTORY_URL && c.method === "POST");
    const historyBody = JSON.parse(historyCall?.body ?? "[]") as Array<{ carpark_id: string }>;
    expect(historyBody).toHaveLength(SEED_CARPARK_ID_LIST.length);

    const momentumGet = router.calls.find(
      (c) => c.method === "GET" && c.url.startsWith(`${HISTORY_URL}?`),
    );
    expect(momentumGet).toBeDefined();

    const momentumPost = router.calls.find((c) => c.url === MOMENTUM_URL && c.method === "POST");
    expect(momentumPost).toBeDefined();

    expect(router.calls.some((c) => c.url === DEFAULT_HEALTHCHECKS_URL && c.method === "GET")).toBe(
      true,
    );
    expect(router.calls.some((c) => c.url === DEFAULT_BATCH_PREDICT_URL && c.method === "POST")).toBe(
      true,
    );
  });
});

describe("runCycle: LTA fetch retry/failure", () => {
  it("retries once on an LTA network/timeout error, then succeeds", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    router.onUrl(LTA_ENDPOINT, "GET", (_call, count) => {
      if (count === 1) {
        throw new Error("simulated timeout");
      }
      return jsonResponse({ value: fullLtaRecords() });
    });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(router.calls.filter((c) => c.url === LTA_ENDPOINT)).toHaveLength(2);
    expect(router.calls.some((c) => c.url === HISTORY_URL && c.method === "POST")).toBe(true);
    expect(router.calls.some((c) => c.url === DEFAULT_HEALTHCHECKS_URL)).toBe(true);
  });

  it("retries once on an LTA 5xx response, then succeeds", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    router.onUrl(LTA_ENDPOINT, "GET", (_call, count) => {
      if (count === 1) {
        return textResponse("upstream error", 503);
      }
      return jsonResponse({ value: fullLtaRecords() });
    });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(router.calls.filter((c) => c.url === LTA_ENDPOINT)).toHaveLength(2);
    expect(router.calls.some((c) => c.url === HISTORY_URL && c.method === "POST")).toBe(true);
  });

  it("gives up after one retry: persistent LTA failure fails the cycle, pings /fail, never reaches Supabase", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    router.onUrl(LTA_ENDPOINT, "GET", () => {
      throw new Error("simulated persistent timeout");
    });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    // Exactly one retry -- not unbounded retrying.
    expect(router.calls.filter((c) => c.url === LTA_ENDPOINT)).toHaveLength(2);
    expect(router.calls.some((c) => c.url === HISTORY_URL)).toBe(false);
    const failCall = router.calls.find((c) => c.url === FAIL_PING_URL && c.method === "POST");
    expect(failCall?.body).toBe("LTA_FETCH_FAILED");
  });
});

describe("runCycle: malformed LTA JSON", () => {
  it("treats malformed JSON as a failed poll: no retry, truncated raw body logged, /fail ping fires", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    const garbage = "not json ".repeat(80); // > 500 chars
    router.onUrl(LTA_ENDPOINT, "GET", () => textResponse(garbage, 200));
    const errorSpy = vi.spyOn(console, "error");
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    // A 200 with a bad body is not a timeout/5xx, so it must NOT be retried.
    expect(router.calls.filter((c) => c.url === LTA_ENDPOINT)).toHaveLength(1);
    expect(router.calls.some((c) => c.url === HISTORY_URL)).toBe(false);

    const loggedLine = errorSpy.mock.calls.map((args) => String(args[0])).find((line) =>
      line.includes("lta_malformed_json"),
    );
    expect(loggedLine).toBeDefined();
    const logged = JSON.parse(loggedLine ?? "{}") as { rawBody: string };
    expect(logged.rawBody.length).toBeLessThanOrEqual(500 + "...[truncated]".length);
    expect(logged.rawBody.endsWith("...[truncated]")).toBe(true);

    const failCall = router.calls.find((c) => c.url === FAIL_PING_URL && c.method === "POST");
    expect(failCall?.body).toBe("LTA_MALFORMED_JSON");
  });
});

describe("runCycle: history write to Supabase", () => {
  it("happy path sends the idempotent Prefer header and service-role auth headers", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const historyCall = router.calls.find((c) => c.url === HISTORY_URL && c.method === "POST");
    expect(historyCall?.headers["prefer"]).toBe("resolution=ignore-duplicates");
    expect(historyCall?.headers["apikey"]).toBe(env.SUPABASE_SERVICE_ROLE_KEY);
    expect(historyCall?.headers["authorization"]).toBe(`Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`);
  });

  it("resends an identical, idempotent payload when a write must be retried (ambiguous timeout)", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    router.onUrl(HISTORY_URL, "POST", (_call, count) => {
      if (count === 1) {
        // The request timed out client-side, but the server may already have
        // committed the write -- the retry must be safe to send regardless.
        throw new Error("simulated ambiguous timeout");
      }
      return okResponse(201);
    });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const historyCalls = router.calls.filter((c) => c.url === HISTORY_URL && c.method === "POST");
    expect(historyCalls).toHaveLength(2);
    expect(historyCalls[0]?.body).toBe(historyCalls[1]?.body);
    for (const call of historyCalls) {
      expect(call.headers["prefer"]).toBe("resolution=ignore-duplicates");
    }
    // The cycle still completes successfully once the retry lands.
    expect(router.calls.some((c) => c.url === DEFAULT_HEALTHCHECKS_URL && c.method === "GET")).toBe(
      true,
    );
  });

  it("fails the cycle with HISTORY_WRITE_FAILED when the write fails twice", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    router.onUrl(HISTORY_URL, "POST", () => textResponse("db is down", 500));
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(router.calls.filter((c) => c.url === HISTORY_URL && c.method === "POST")).toHaveLength(2);
    const failCall = router.calls.find((c) => c.url === FAIL_PING_URL && c.method === "POST");
    expect(failCall?.body).toBe("HISTORY_WRITE_FAILED");
    // Momentum must never be updated after a failed history write.
    expect(router.calls.some((c) => c.url.includes("carpark_momentum"))).toBe(false);
  });

  it.each([401, 403])(
    "on a %d response (RLS rejection), fails immediately with no retry and pings RLS_REJECTED",
    async (status) => {
      const env = makeEnv();
      const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
      router.onUrl(HISTORY_URL, "POST", () => textResponse('{"message":"permission denied"}', status));
      const errorSpy = vi.spyOn(console, "error");
      vi.stubGlobal("fetch", router.fetch);

      await runCycle(env, NOW);

      // Distinct from a routine retry: exactly one attempt is made, never two.
      expect(router.calls.filter((c) => c.url === HISTORY_URL && c.method === "POST")).toHaveLength(1);

      const failCall = router.calls.find((c) => c.url === FAIL_PING_URL && c.method === "POST");
      expect(failCall?.body).toBe("RLS_REJECTED");

      const loggedRlsLine = errorSpy.mock.calls
        .map((args) => String(args[0]))
        .find((line) => line.includes("supabase_rls_rejected"));
      expect(loggedRlsLine).toBeDefined();
    },
  );
});

describe("runCycle: momentum upsert", () => {
  it("sends resolution=merge-duplicates and a fresh updated_at on every row", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const momentumPost = router.calls.find((c) => c.url === MOMENTUM_URL && c.method === "POST");
    expect(momentumPost?.headers["prefer"]).toBe("resolution=merge-duplicates");
    const rows = JSON.parse(momentumPost?.body ?? "[]") as Array<{ updated_at: string }>;
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row) => row.updated_at === NOW.toISOString())).toBe(true);
  });
});

describe("runCycle: healthchecks success ping", () => {
  it("sends exactly one success ping per cycle when configured", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(
      router.calls.filter((c) => c.url === DEFAULT_HEALTHCHECKS_URL && c.method === "GET"),
    ).toHaveLength(1);
  });

  it("skips the success ping when HEALTHCHECKS_POLLER_PING_URL is unset", async () => {
    const env = makeEnv({ HEALTHCHECKS_POLLER_PING_URL: undefined });
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(
      router.calls.some(
        (c) => c.url === DEFAULT_HEALTHCHECKS_URL || c.url === FAIL_PING_URL,
      ),
    ).toBe(false);
  });
});

describe("runCycle: batch-predict trigger", () => {
  it("triggers batch-predict with the x-batch-secret header when configured", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    const batchCall = router.calls.find(
      (c) => c.url === DEFAULT_BATCH_PREDICT_URL && c.method === "POST",
    );
    expect(batchCall?.headers["x-batch-secret"]).toBe(env.BATCH_SHARED_SECRET);
  });

  it("skips the trigger (with a log line) when BATCH_PREDICT_URL is unset", async () => {
    const env = makeEnv({ BATCH_PREDICT_URL: undefined });
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    const logSpy = vi.spyOn(console, "log");
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(router.calls.some((c) => c.url === DEFAULT_BATCH_PREDICT_URL)).toBe(false);
    expect(
      logSpy.mock.calls.map((args) => String(args[0])).some((line) => line.includes("batch_predict_skipped")),
    ).toBe(true);
  });

  it("skips the trigger when BATCH_SHARED_SECRET is unset", async () => {
    const env = makeEnv({ BATCH_SHARED_SECRET: undefined });
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    vi.stubGlobal("fetch", router.fetch);

    await runCycle(env, NOW);

    expect(router.calls.some((c) => c.url === DEFAULT_BATCH_PREDICT_URL)).toBe(false);
  });

  it("logs a warning and still completes the cycle when the trigger call itself fails", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env, { ltaValue: fullLtaRecords() });
    router.onUrl(DEFAULT_BATCH_PREDICT_URL, "POST", () => textResponse("bad gateway", 502));
    const warnSpy = vi.spyOn(console, "warn");
    vi.stubGlobal("fetch", router.fetch);

    await expect(runCycle(env, NOW)).resolves.toBeUndefined();

    expect(
      warnSpy.mock.calls.map((args) => String(args[0])).some((line) => line.includes("batch_predict_trigger_failed")),
    ).toBe(true);
    // The cycle is still successful -- the success ping already fired before the trigger.
    expect(router.calls.some((c) => c.url === DEFAULT_HEALTHCHECKS_URL && c.method === "GET")).toBe(
      true,
    );
  });
});
