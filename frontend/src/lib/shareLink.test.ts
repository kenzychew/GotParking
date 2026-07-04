import { describe, expect, it } from "vitest";
import { buildShareUrl, resolveShareLinkCarpark } from "./shareLink";

describe("resolveShareLinkCarpark", () => {
  it('returns "none" when there is no carpark param', () => {
    expect(resolveShareLinkCarpark("")).toEqual({ kind: "none" });
    expect(resolveShareLinkCarpark("?other=1")).toEqual({ kind: "none" });
  });

  it("validates a known carpark id from the whitelist", () => {
    expect(resolveShareLinkCarpark("?carpark=1")).toEqual({ kind: "valid", carparkId: "1" });
    expect(resolveShareLinkCarpark("?carpark=50")).toEqual({ kind: "valid", carparkId: "50" });
  });

  it("rejects an id that is not in the whitelist, without crashing", () => {
    expect(resolveShareLinkCarpark("?carpark=999")).toEqual({
      kind: "invalid",
      rawValue: "999",
    });
  });

  it("rejects garbage / injection-shaped values safely", () => {
    expect(resolveShareLinkCarpark("?carpark=<script>alert(1)</script>")).toEqual({
      kind: "invalid",
      rawValue: "<script>alert(1)</script>",
    });
    expect(resolveShareLinkCarpark("?carpark=")).toEqual({ kind: "invalid", rawValue: "" });
    expect(resolveShareLinkCarpark("?carpark= ")).toEqual({ kind: "invalid", rawValue: " " });
  });

  it("ignores extraneous params alongside a valid carpark id", () => {
    expect(resolveShareLinkCarpark("?utm_source=test&carpark=2")).toEqual({
      kind: "valid",
      carparkId: "2",
    });
  });
});

describe("buildShareUrl", () => {
  it("builds an absolute URL with the carpark query param set", () => {
    const url = buildShareUrl("1", { origin: "https://gotparking.example", pathname: "/" });
    expect(url).toBe("https://gotparking.example/?carpark=1");
  });

  it("preserves a non-root pathname", () => {
    const url = buildShareUrl("16", {
      origin: "https://gotparking.example",
      pathname: "/app",
    });
    expect(url).toBe("https://gotparking.example/app?carpark=16");
  });
});
