// Share link (design doc Security section + Requirement 6): a `?carpark=<id>`
// deep link selects that carpark. The id is validated CLIENT-SIDE against
// the static seed whitelist BEFORE anything renders based on it -- defense
// in depth on top of the server-side validation that used to exist before
// D10 removed the server's carpark_id parameter entirely. Garbage/unknown
// values must produce a clear error state, never a crash.
import { isKnownCarparkId } from "../seed/seedCarparks";

export type ShareLinkResult =
  | { kind: "none" }
  | { kind: "valid"; carparkId: string }
  | { kind: "invalid"; rawValue: string };

const CARPARK_PARAM = "carpark";

/** Reads and whitelist-validates the ?carpark= param from a location.search string. */
export function resolveShareLinkCarpark(search: string): ShareLinkResult {
  const params = new URLSearchParams(search);
  if (!params.has(CARPARK_PARAM)) return { kind: "none" };

  const rawValue = params.get(CARPARK_PARAM) ?? "";
  if (rawValue !== "" && isKnownCarparkId(rawValue)) {
    return { kind: "valid", carparkId: rawValue };
  }
  return { kind: "invalid", rawValue };
}

/** Builds an absolute, shareable deep link for a given (already-valid) carpark id. */
export function buildShareUrl(
  carparkId: string,
  location: Pick<Location, "origin" | "pathname"> = window.location,
): string {
  const url = new URL(location.pathname, location.origin);
  url.searchParams.set(CARPARK_PARAM, carparkId);
  return url.toString();
}
