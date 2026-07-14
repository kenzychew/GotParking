// Pins wrangler.toml's crons array to the exported cron constants so a toml
// edit that drifts from src/index.ts fails CI instead of silently routing
// the nightly tick to a poll cycle (or dropping the baseline refresh).
//
// Read via Vite's built-in "?raw" import (Vitest runs on Vite) rather than
// node:fs -- this project has no @types/node, and adding it would pollute
// the whole program's global scope (fetch/Response/etc.) in a way that
// could conflict with @cloudflare/workers-types used by src/index.ts.
import wranglerToml from "../wrangler.toml?raw";
import { describe, expect, it } from "vitest";

import { BASELINE_REFRESH_CRON, POLL_CRON } from "../src/index";

function readCronsFromWranglerToml(): string[] {
  const match = wranglerToml.match(/crons\s*=\s*\[([^\]]*)\]/);
  const arrayContents = match?.[1];
  if (arrayContents === undefined) {
    throw new Error("wrangler.toml: no crons = [...] array found");
  }
  return [...arrayContents.matchAll(/"([^"]*)"/g)].map((m) => {
    const cron = m[1];
    if (cron === undefined) {
      throw new Error("wrangler.toml: unreachable -- capture group always present on a match");
    }
    return cron;
  });
}

describe("wrangler.toml crons <-> src/index.ts constants", () => {
  it("matches [POLL_CRON, BASELINE_REFRESH_CRON] exactly", () => {
    expect(readCronsFromWranglerToml()).toEqual([POLL_CRON, BASELINE_REFRESH_CRON]);
  });
});
