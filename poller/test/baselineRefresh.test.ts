import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import worker, {
  BASELINE_REFRESH_CRON,
  LTA_ENDPOINT,
  POLL_CRON,
  runBaselineRefresh,
} from "../src/index";
import {
  DEFAULT_BASELINE_HEALTHCHECKS_URL,
  DEFAULT_BATCH_PREDICT_URL,
  DEFAULT_HEALTHCHECKS_URL,
  DEFAULT_SUPABASE_URL,
  buildBaseRouter,
  jsonResponse,
  makeEnv,
  textResponse,
} from "./support/harness";

const NOW = new Date("2026-07-11T19:15:00.000Z");
const RPC_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/rpc/refresh_carpark_baseline`;
const HISTORY_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/carpark_history`;
const MOMENTUM_URL = `${DEFAULT_SUPABASE_URL}/rest/v1/carpark_momentum`;
const BASELINE_FAIL_PING_URL = `${DEFAULT_BASELINE_HEALTHCHECKS_URL}/fail`;

beforeEach(() => {
  vi.spyOn(console, "log").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("runBaselineRefresh: happy path", () => {
  it("makes exactly one RPC call with service-role headers and body '{}', pings the baseline check, and touches nothing else", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    vi.stubGlobal("fetch", router.fetch);

    await runBaselineRefresh(env);

    const rpcCalls = router.calls.filter((c) => c.url === RPC_URL);
    expect(rpcCalls).toHaveLength(1);
    expect(rpcCalls[0]?.method).toBe("POST");
    expect(rpcCalls[0]?.headers["apikey"]).toBe(env.SUPABASE_SERVICE_ROLE_KEY);
    expect(rpcCalls[0]?.headers["authorization"]).toBe(`Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`);
    expect(rpcCalls[0]?.body).toBe("{}");

    expect(
      router.calls.some((c) => c.url === DEFAULT_BASELINE_HEALTHCHECKS_URL && c.method === "GET"),
    ).toBe(true);

    // Zero calls to anything the poll cycle touches.
    expect(router.calls.some((c) => c.url === LTA_ENDPOINT)).toBe(false);
    expect(router.calls.some((c) => c.url === HISTORY_URL)).toBe(false);
    expect(router.calls.some((c) => c.url === MOMENTUM_URL)).toBe(false);
    expect(router.calls.some((c) => c.url === DEFAULT_HEALTHCHECKS_URL)).toBe(false);
    expect(router.calls.some((c) => c.url === DEFAULT_BATCH_PREDICT_URL)).toBe(false);
  });
});

describe("runBaselineRefresh: RPC retry/failure", () => {
  it("retries once on a 5xx RPC response, then succeeds", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    router.onUrl(RPC_URL, "POST", (_call, count) => {
      if (count === 1) {
        return textResponse("upstream error", 503);
      }
      return jsonResponse(123);
    });
    vi.stubGlobal("fetch", router.fetch);

    await runBaselineRefresh(env);

    expect(router.calls.filter((c) => c.url === RPC_URL)).toHaveLength(2);
    expect(
      router.calls.some((c) => c.url === DEFAULT_BASELINE_HEALTHCHECKS_URL && c.method === "GET"),
    ).toBe(true);
  });

  it("retries once on a plain network error, then succeeds", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    router.onUrl(RPC_URL, "POST", (_call, count) => {
      if (count === 1) {
        throw new Error("simulated network error");
      }
      return jsonResponse(123);
    });
    vi.stubGlobal("fetch", router.fetch);

    await runBaselineRefresh(env);

    expect(router.calls.filter((c) => c.url === RPC_URL)).toHaveLength(2);
    expect(
      router.calls.some((c) => c.url === DEFAULT_BASELINE_HEALTHCHECKS_URL && c.method === "GET"),
    ).toBe(true);
  });

  it("gives up after one retry: persistent 5xx resolves without throwing, /fail-pings BASELINE_REFRESH_FAILED, never touches the poller check", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    router.onUrl(RPC_URL, "POST", () => textResponse("db is down", 500));
    vi.stubGlobal("fetch", router.fetch);

    await expect(runBaselineRefresh(env)).resolves.toBeUndefined();

    expect(router.calls.filter((c) => c.url === RPC_URL)).toHaveLength(2);
    const failCall = router.calls.find((c) => c.url === BASELINE_FAIL_PING_URL && c.method === "POST");
    expect(failCall?.body).toBe("BASELINE_REFRESH_FAILED");
    expect(router.calls.some((c) => c.url.startsWith(DEFAULT_HEALTHCHECKS_URL))).toBe(false);
    expect(
      router.calls.some((c) => c.url === DEFAULT_BASELINE_HEALTHCHECKS_URL && c.method === "GET"),
    ).toBe(false);
  });

  it.each([401, 403])(
    "on a %d response (RLS rejection), fails immediately with no retry and pings RLS_REJECTED",
    async (status) => {
      const env = makeEnv();
      const router = buildBaseRouter(env);
      router.onUrl(RPC_URL, "POST", () => textResponse('{"message":"permission denied"}', status));
      vi.stubGlobal("fetch", router.fetch);

      await runBaselineRefresh(env);

      expect(router.calls.filter((c) => c.url === RPC_URL)).toHaveLength(1);
      const failCall = router.calls.find((c) => c.url === BASELINE_FAIL_PING_URL && c.method === "POST");
      expect(failCall?.body).toBe("RLS_REJECTED");
    },
  );

  it("does not retry a timeout (retryOnTimeout=false): exactly one attempt, /fail-pings BASELINE_REFRESH_FAILED", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    router.onUrl(RPC_URL, "POST", () => {
      const error = new Error("simulated abort timeout");
      error.name = "TimeoutError";
      throw error;
    });
    vi.stubGlobal("fetch", router.fetch);

    await runBaselineRefresh(env);

    // A timed-out statement may still be running server-side -- retrying
    // would run two concurrent heavy aggregations, so this must NOT retry.
    expect(router.calls.filter((c) => c.url === RPC_URL)).toHaveLength(1);
    const failCall = router.calls.find((c) => c.url === BASELINE_FAIL_PING_URL && c.method === "POST");
    expect(failCall?.body).toBe("BASELINE_REFRESH_FAILED");
  });

  it("treats a non-JSON RPC body as the catch-all UNEXPECTED_ERROR path", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    router.onUrl(RPC_URL, "POST", () => textResponse("not json", 200));
    vi.stubGlobal("fetch", router.fetch);

    await expect(runBaselineRefresh(env)).resolves.toBeUndefined();

    const failCall = router.calls.find((c) => c.url === BASELINE_FAIL_PING_URL && c.method === "POST");
    expect(failCall?.body).toBe("UNEXPECTED_ERROR");
  });
});

describe("runBaselineRefresh: healthchecks ping URL unset", () => {
  it("still completes the happy path and makes no request to any healthchecks host", async () => {
    const env = makeEnv({ HEALTHCHECKS_BASELINE_PING_URL: undefined });
    const router = buildBaseRouter(env);
    vi.stubGlobal("fetch", router.fetch);

    await runBaselineRefresh(env);

    expect(router.calls.filter((c) => c.url === RPC_URL)).toHaveLength(1);
    expect(router.calls.some((c) => c.url.includes("hc-ping"))).toBe(false);
  });
});

describe("worker.scheduled: cron dispatch", () => {
  function fakeCtx(): { ctx: ExecutionContext; waited: Promise<unknown>[] } {
    const waited: Promise<unknown>[] = [];
    const ctx = {
      waitUntil: (promise: Promise<unknown>) => {
        waited.push(promise);
      },
    } as unknown as ExecutionContext;
    return { ctx, waited };
  }

  it("cron === BASELINE_REFRESH_CRON runs only the baseline refresh (RPC called, LTA never called)", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    vi.stubGlobal("fetch", router.fetch);
    const { ctx, waited } = fakeCtx();
    const controller = { cron: BASELINE_REFRESH_CRON, scheduledTime: NOW.getTime() } as ScheduledController;

    await worker.scheduled(controller, env, ctx);
    await Promise.all(waited);

    expect(router.calls.some((c) => c.url === RPC_URL)).toBe(true);
    expect(router.calls.some((c) => c.url === LTA_ENDPOINT)).toBe(false);
  });

  it("cron === POLL_CRON runs the poll cycle (LTA called) and never the RPC", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    vi.stubGlobal("fetch", router.fetch);
    const { ctx, waited } = fakeCtx();
    const controller = { cron: POLL_CRON, scheduledTime: NOW.getTime() } as ScheduledController;

    await worker.scheduled(controller, env, ctx);
    await Promise.all(waited);

    expect(router.calls.some((c) => c.url === LTA_ENDPOINT)).toBe(true);
    expect(router.calls.some((c) => c.url === RPC_URL)).toBe(false);
  });

  it("an unrecognized cron logs unknown_cron and falls back to the poll cycle", async () => {
    const env = makeEnv();
    const router = buildBaseRouter(env);
    const warnSpy = vi.spyOn(console, "warn");
    vi.stubGlobal("fetch", router.fetch);
    const { ctx, waited } = fakeCtx();
    const controller = { cron: "0 0 * * *", scheduledTime: NOW.getTime() } as ScheduledController;

    await worker.scheduled(controller, env, ctx);
    await Promise.all(waited);

    expect(router.calls.some((c) => c.url === LTA_ENDPOINT)).toBe(true);
    expect(router.calls.some((c) => c.url === RPC_URL)).toBe(false);
    expect(
      warnSpy.mock.calls.map((args) => String(args[0])).some((line) => line.includes("unknown_cron")),
    ).toBe(true);
  });
});
