// Offline test harness for the poller cycle tests: a scripted fetch replacement
// (no real network) plus small fixtures for Env and LTA records. vi.stubGlobal
// erases the static type of the global `fetch`, so this file is free to use a
// minimal request-init shape rather than the full Workers RequestInit union --
// the source under test only ever passes method/headers/body/signal, and the
// signal is irrelevant to a scripted mock that resolves/rejects immediately.

import type { Env } from "../../src/index";
import { SEED_CARPARK_ID_LIST } from "../../src/carparks";

export const DEFAULT_SUPABASE_URL = "https://example.supabase.co";
export const DEFAULT_HEALTHCHECKS_URL = "https://hc-ping.test/abc-uuid";
export const DEFAULT_BATCH_PREDICT_URL = "https://batch.example.test/predict";

export function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    LTA_API_KEY: "test-lta-key",
    SUPABASE_URL: DEFAULT_SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY: "test-service-role-key",
    HEALTHCHECKS_POLLER_PING_URL: DEFAULT_HEALTHCHECKS_URL,
    BATCH_PREDICT_URL: DEFAULT_BATCH_PREDICT_URL,
    BATCH_SHARED_SECRET: "test-batch-secret",
    ...overrides,
  };
}

/** One plausible LTA record per seed carpark; override specific IDs' lot counts as needed. */
export function fullLtaRecords(
  overrides: Readonly<Record<string, number>> = {},
): Array<{ CarParkID: string; AvailableLots: number }> {
  return SEED_CARPARK_ID_LIST.map((id, index) => ({
    CarParkID: id,
    AvailableLots: overrides[id] ?? 50 + index * 10,
  }));
}

export interface RecordedCall {
  readonly url: string;
  readonly method: string;
  readonly headers: Record<string, string>;
  readonly body: string | undefined;
}

interface MockRequestInit {
  readonly method?: string;
  readonly headers?: Record<string, string>;
  readonly body?: string;
}

type RouteHandler = (call: RecordedCall, callCount: number) => Response | Promise<Response>;

interface Route {
  readonly matches: (call: RecordedCall) => boolean;
  readonly handler: RouteHandler;
  count: number;
}

/**
 * Scripted fetch replacement for offline poller tests. Every invocation is
 * recorded in `calls` regardless of routing outcome, so tests can assert on
 * request shape (headers, body, call count) without a real network or DB.
 * Routes are matched most-recently-registered-first, so a test can layer a
 * one-off override on top of `buildBaseRouter`'s defaults.
 */
export class FetchRouter {
  readonly calls: RecordedCall[] = [];
  private readonly routes: Route[] = [];

  on(matches: (call: RecordedCall) => boolean, handler: RouteHandler): this {
    this.routes.unshift({ matches, handler, count: 0 });
    return this;
  }

  onUrl(url: string, method: string, handler: RouteHandler): this {
    return this.on((call) => call.url === url && call.method === method, handler);
  }

  readonly fetch = async (input: unknown, init?: MockRequestInit): Promise<Response> => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    for (const [key, value] of Object.entries(init?.headers ?? {})) {
      headers[key.toLowerCase()] = value;
    }
    const call: RecordedCall = { url, method, headers, body: init?.body };
    this.calls.push(call);

    for (const route of this.routes) {
      if (route.matches(call)) {
        route.count += 1;
        return route.handler(call, route.count);
      }
    }
    throw new Error(`FetchRouter: no route registered for ${method} ${url}`);
  };
}

export function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function textResponse(text: string, status = 200): Response {
  return new Response(text, { status });
}

export function okResponse(status = 201): Response {
  return new Response(null, { status });
}

export interface BaseRouterOptions {
  readonly ltaValue?: readonly unknown[];
  readonly momentumHistory?: readonly unknown[];
}

/** Wires up a fully-successful default response for every endpoint the poller
 * calls in one cycle. Tests override individual routes afterward to script a
 * specific failure. */
export function buildBaseRouter(env: Env, options: BaseRouterOptions = {}): FetchRouter {
  const router = new FetchRouter();
  const ltaValue = options.ltaValue ?? [];
  const momentumHistory = options.momentumHistory ?? [];

  router.on(
    (call) => call.url === "https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2",
    () => jsonResponse({ value: ltaValue }),
  );
  router.onUrl(`${env.SUPABASE_URL}/rest/v1/carpark_history`, "POST", () => okResponse(201));
  router.on(
    (call) =>
      call.method === "GET" && call.url.startsWith(`${env.SUPABASE_URL}/rest/v1/carpark_history?`),
    () => jsonResponse(momentumHistory),
  );
  router.onUrl(`${env.SUPABASE_URL}/rest/v1/carpark_momentum`, "POST", () => okResponse(200));
  if (env.HEALTHCHECKS_POLLER_PING_URL) {
    router.onUrl(env.HEALTHCHECKS_POLLER_PING_URL, "GET", () => okResponse(200));
    router.onUrl(`${env.HEALTHCHECKS_POLLER_PING_URL}/fail`, "POST", () => okResponse(200));
  }
  if (env.BATCH_PREDICT_URL) {
    router.onUrl(env.BATCH_PREDICT_URL, "POST", () => okResponse(200));
  }
  return router;
}
