"""Regenerate the poller and frontend seed-carpark lists from a coverage-map JSON.

Standalone tool for the carpark coverage-expansion plan's Phase A / Approach C (CI-generated
static-list hybrid; see
`~/.gstack/projects/gstack-playground/ceo-plans/2026-07-07-carpark-coverage-expansion.md`).
The poller stays a pure static-config Cloudflare Worker (no new runtime DB dependency) --
instead of hand-editing `poller/src/carparks.ts` and `frontend/src/seed/seedCarparks.ts` in
lockstep (a documented anti-pattern in this codebase today), this script regenerates both
from a single JSON input, so future whitelist updates become "run the generator, review the
diff in a PR" instead of manually keeping two files in sync.

Coverage-map JSON schema (produced by a separate, not-yet-built Phase A whitelist-matching
script -- this script only *consumes* it):

    {
      "candidates": [
        {
          "carpark_id": "64",           # required -- LTA CarParkID. Usually digits-only, but
                                         # not always: the full-feed coverage-expansion wave
                                         # (2026-07-09) introduced alphanumeric, area-letter-
                                         # prefixed IDs (e.g. "A0007") -- any non-empty
                                         # alphanumeric string is accepted.
          "name": "Junction 8",         # required -- display name
          "state": "verified",          # required -- one of the plan's state-machine values:
                                         # "matched" | "observing" | "verified" | "rejected" |
                                         # "needs-manual-disambiguation". Only "verified"
                                         # entries (range >= MIN_MEANINGFUL_RANGE per
                                         # analyze_variance.py) are pulled into the seed lists.
          ...                            # any other fields (development, best_match,
                                         # best_score, reason, observed_at, etc.) are ignored --
                                         # this script only reads carpark_id/name/state.
        },
        ...
      ]
    }

The existing 10 seed carparks are read from `poller/src/carparks.ts` (the canonical source
for *existing* entries -- picked over frontend/src/seed/seedCarparks.ts because it's the
simpler id->name mapping with no extra helper functions to preserve) and merged with every
"verified" candidate from the coverage map, sorted numerically by carpark_id (matching both
files' existing ordering convention). Both target files are then rewritten with only their
data literal replaced -- every header comment, type, and helper function is preserved
verbatim via a narrow regex substitution scoped to just that literal.

Usage:
    uv run python scripts/regen_seed_lists.py [coverage_map_path]

    # coverage_map_path defaults to data/carpark_coverage_map.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERAGE_MAP_PATH = REPO_ROOT / "data" / "carpark_coverage_map.json"
DEFAULT_POLLER_TS_PATH = REPO_ROOT / "poller" / "src" / "carparks.ts"
DEFAULT_FRONTEND_TS_PATH = REPO_ROOT / "frontend" / "src" / "seed" / "seedCarparks.ts"

# Only candidates in this state are pulled into the seed lists (Definitions section of the
# coverage-expansion plan: "verified" = range >= MIN_MEANINGFUL_RANGE per analyze_variance.py).
VERIFIED_STATE = "verified"

# Matches poller/src/carparks.ts's SEED_CARPARK_NAMES object body -- one "id": "name" pair per
# line, preserving the surrounding "export const ... = {" / "};" verbatim.
_POLLER_NAMES_BLOCK_RE = re.compile(
    r"(export const SEED_CARPARK_NAMES: Readonly<Record<string, string>> = \{\n)"
    r"(.*?)"
    r"(\n\};)",
    re.DOTALL,
)

# Matches frontend/src/seed/seedCarparks.ts's SEED_CARPARKS array body, same approach.
_FRONTEND_ARRAY_BLOCK_RE = re.compile(
    r"(export const SEED_CARPARKS: readonly SeedCarpark\[\] = \[\n)"
    r"(.*?)"
    r"(\n\];)",
    re.DOTALL,
)

# One "id": "name" pair per line inside the SEED_CARPARK_NAMES block (mall names in this
# dataset never contain literal double quotes, so a simple non-greedy match is sufficient).
# The id group matches any non-empty run of word characters, not just digits -- LTA
# CarParkIDs include alphanumeric, area-letter-prefixed IDs (e.g. "A0007") since the
# full-feed coverage-expansion wave (2026-07-09).
_NAME_ENTRY_RE = re.compile(r'^\s*"(\w+)":\s*"([^"]*)",?\s*$', re.MULTILINE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class SeedListRegenError(ValueError):
    """Raised on any invalid input -- never write a partial or empty file after this."""


@dataclass(frozen=True)
class VerifiedCarpark:
    """One "verified" candidate pulled from the coverage-map JSON.

    Attributes:
        carpark_id: LTA CarParkID (non-empty alphanumeric string -- usually digits-only, but
            not always; see module docstring).
        name: Display name.
    """

    carpark_id: str
    name: str


def ts_string_literal(value: str) -> str:
    """Render a Python string as a double-quoted TypeScript string literal.

    Args:
        value: Raw string value.

    Returns:
        A double-quoted, escaped string literal (ASCII-only, matching this codebase's
        character-set convention).
    """
    return json.dumps(value, ensure_ascii=True)


def parse_existing_carparks(poller_ts_text: str) -> dict[str, str]:
    """Parse the existing id->name mapping out of poller/src/carparks.ts's text.

    poller/src/carparks.ts is treated as the canonical source for *existing* seed carparks
    (as opposed to frontend/src/seed/seedCarparks.ts, which carries extra helper functions
    this script must not touch).

    Args:
        poller_ts_text: Full text of poller/src/carparks.ts.

    Returns:
        Mapping of carpark_id to name for every currently-seeded carpark.

    Raises:
        SeedListRegenError: If the SEED_CARPARK_NAMES block cannot be located, meaning the
            file's format has changed in a way this script doesn't understand.
    """
    match = _POLLER_NAMES_BLOCK_RE.search(poller_ts_text)
    if match is None:
        raise SeedListRegenError(
            "Could not locate the SEED_CARPARK_NAMES block in poller/src/carparks.ts -- "
            "file format may have changed unexpectedly. Refusing to guess."
        )
    entries = dict(_NAME_ENTRY_RE.findall(match.group(2)))
    if not entries:
        raise SeedListRegenError(
            "SEED_CARPARK_NAMES block in poller/src/carparks.ts parsed to zero entries -- "
            "refusing to proceed (would risk producing an empty seed list)."
        )
    return entries


def load_verified_candidates(coverage_map_path: Path) -> list[VerifiedCarpark]:
    """Load and validate the coverage-map JSON, filtering to "verified" candidates.

    Args:
        coverage_map_path: Path to the coverage-map JSON file (see module docstring for the
            exact expected schema).

    Returns:
        List of verified candidates, in the order they appeared in the input file.

    Raises:
        SeedListRegenError: On a missing file, malformed JSON, a missing/wrong-shaped
            "candidates" array, or any candidate entry missing/misshaping "carpark_id",
            "name", or "state".
    """
    if not coverage_map_path.exists():
        raise SeedListRegenError(
            f"Coverage-map input file not found: {coverage_map_path}. "
            "Expected a JSON file with a top-level \"candidates\" array -- see this "
            "script's module docstring for the exact schema."
        )

    try:
        raw_text = coverage_map_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SeedListRegenError(f"Could not read {coverage_map_path}: {exc}") from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SeedListRegenError(f"Malformed JSON in {coverage_map_path}: {exc}") from exc

    if not isinstance(payload, dict) or "candidates" not in payload:
        raise SeedListRegenError(
            f"{coverage_map_path} is missing a top-level \"candidates\" array."
        )

    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise SeedListRegenError(
            f"{coverage_map_path}'s \"candidates\" field must be a JSON array, "
            f"got {type(candidates).__name__}."
        )

    verified: list[VerifiedCarpark] = []
    for index, entry in enumerate(candidates):
        if not isinstance(entry, dict):
            raise SeedListRegenError(
                f"{coverage_map_path}: candidates[{index}] must be an object, "
                f"got {type(entry).__name__}."
            )

        carpark_id = entry.get("carpark_id")
        name = entry.get("name")
        state = entry.get("state")

        if not isinstance(carpark_id, str) or not carpark_id:
            raise SeedListRegenError(
                f"{coverage_map_path}: candidates[{index}] is missing a valid non-empty "
                f"\"carpark_id\" string (got {carpark_id!r})."
            )
        if not carpark_id.isalnum():
            raise SeedListRegenError(
                f"{coverage_map_path}: candidates[{index}].carpark_id must be a non-empty "
                f"alphanumeric LTA CarParkID (digits-only, or area-letter-prefixed like "
                f"'A0007'), got {carpark_id!r}."
            )
        if not isinstance(name, str) or not name:
            raise SeedListRegenError(
                f"{coverage_map_path}: candidates[{index}] (carpark_id={carpark_id!r}) is "
                f"missing a valid non-empty \"name\" string (got {name!r})."
            )
        if not isinstance(state, str) or not state:
            raise SeedListRegenError(
                f"{coverage_map_path}: candidates[{index}] (carpark_id={carpark_id!r}) is "
                f"missing a valid non-empty \"state\" string (got {state!r})."
            )

        if state == VERIFIED_STATE:
            verified.append(VerifiedCarpark(carpark_id=carpark_id, name=name))

    return verified


def build_combined_carparks(
    existing: dict[str, str],
    verified: list[VerifiedCarpark],
) -> dict[str, str]:
    """Merge existing seed carparks with newly-verified candidates.

    Args:
        existing: Current id->name mapping (from poller/src/carparks.ts).
        verified: Verified candidates loaded from the coverage map.

    Returns:
        Combined id->name mapping (insertion order not meaningful -- callers should sort).

    Raises:
        SeedListRegenError: If a verified candidate's carpark_id already exists in the
            current seed list (per the coverage-expansion plan, existing seed carparks are
            excluded from the candidate-matching pipeline entirely and should never
            re-appear as "verified"), or if two verified candidates share a carpark_id with
            conflicting names.
    """
    combined = dict(existing)
    seen_verified: dict[str, str] = {}

    for candidate in verified:
        if candidate.carpark_id in existing:
            raise SeedListRegenError(
                f"Verified candidate carpark_id={candidate.carpark_id!r} "
                f"({candidate.name!r}) already exists in the current seed list "
                f"({existing[candidate.carpark_id]!r}) -- existing seed carparks should "
                "never re-enter the candidate state machine (see the coverage-expansion "
                "plan's Definitions section)."
            )
        if candidate.carpark_id in seen_verified:
            if seen_verified[candidate.carpark_id] != candidate.name:
                raise SeedListRegenError(
                    f"Conflicting verified candidates for carpark_id="
                    f"{candidate.carpark_id!r}: {seen_verified[candidate.carpark_id]!r} "
                    f"vs {candidate.name!r}."
                )
            continue
        seen_verified[candidate.carpark_id] = candidate.name
        combined[candidate.carpark_id] = candidate.name

    return combined


def sorted_entries(combined: dict[str, str]) -> list[tuple[str, str]]:
    """Sort a combined id->name mapping numerically by carpark_id, alphanumeric IDs last.

    Matches both target files' existing convention (ascending numeric CarParkID order,
    not lexical string order -- e.g. "2" before "11"). LTA CarParkIDs are not all
    numeric -- the full-feed coverage-expansion wave (2026-07-08) introduced alphanumeric
    IDs (e.g. "A0007", area-letter-prefixed, mostly HDB/URA off-street lots) that a bare
    `int()` sort key would crash on. All-numeric IDs still sort numerically among
    themselves (preserving the original convention exactly); any alphanumeric IDs sort
    after them, in plain alphabetical order.

    Args:
        combined: id->name mapping.

    Returns:
        List of (carpark_id, name) tuples: numeric IDs first (ascending numeric order),
        then alphanumeric IDs (ascending alphabetical order).
    """
    return sorted(
        combined.items(),
        key=lambda pair: (0, int(pair[0])) if pair[0].isdigit() else (1, pair[0]),
    )


def render_poller_carparks_ts(original_text: str, combined: dict[str, str]) -> str:
    """Regenerate poller/src/carparks.ts's text with a new SEED_CARPARK_NAMES body.

    Args:
        original_text: Current full text of poller/src/carparks.ts.
        combined: Combined id->name mapping to render.

    Returns:
        New full file text -- identical to original_text except for the
        SEED_CARPARK_NAMES object body.

    Raises:
        SeedListRegenError: If the SEED_CARPARK_NAMES block cannot be located.
    """
    body = "\n".join(
        f'  "{carpark_id}": {ts_string_literal(name)},' for carpark_id, name in sorted_entries(combined)
    )

    def _replace(match: re.Match[str]) -> str:
        return match.group(1) + body + match.group(3)

    new_text, count = _POLLER_NAMES_BLOCK_RE.subn(_replace, original_text, count=1)
    if count == 0:
        raise SeedListRegenError(
            "Could not locate the SEED_CARPARK_NAMES block in poller/src/carparks.ts while "
            "rendering -- refusing to write a partial file."
        )
    return new_text


def render_frontend_seed_carparks_ts(original_text: str, combined: dict[str, str]) -> str:
    """Regenerate frontend/src/seed/seedCarparks.ts's text with a new SEED_CARPARKS body.

    Only the SEED_CARPARKS array literal changes -- the SeedCarpark interface, the
    SEED_CARPARK_IDS set, and every helper function (isKnownCarparkId, getSeedCarparkById,
    searchSeedCarparks) are preserved byte-for-byte.

    Args:
        original_text: Current full text of frontend/src/seed/seedCarparks.ts.
        combined: Combined id->name mapping to render.

    Returns:
        New full file text -- identical to original_text except for the SEED_CARPARKS
        array body.

    Raises:
        SeedListRegenError: If the SEED_CARPARKS block cannot be located.
    """
    body = "\n".join(
        f"  {{ id: {ts_string_literal(carpark_id)}, name: {ts_string_literal(name)} }},"
        for carpark_id, name in sorted_entries(combined)
    )

    def _replace(match: re.Match[str]) -> str:
        return match.group(1) + body + match.group(3)

    new_text, count = _FRONTEND_ARRAY_BLOCK_RE.subn(_replace, original_text, count=1)
    if count == 0:
        raise SeedListRegenError(
            "Could not locate the SEED_CARPARKS block in frontend/src/seed/seedCarparks.ts "
            "while rendering -- refusing to write a partial file."
        )
    return new_text


def regenerate(
    coverage_map_path: Path,
    poller_ts_path: Path,
    frontend_ts_path: Path,
) -> None:
    """Regenerate both seed-list files in-place from a coverage-map JSON.

    All input validation and text rendering happens before either file is written, so any
    input error (missing file, malformed JSON, an invalid candidate entry, a conflicting
    carpark_id, an unparseable target file) aborts before touching disk -- never a silent
    partial write.

    Args:
        coverage_map_path: Path to the coverage-map JSON input.
        poller_ts_path: Path to poller/src/carparks.ts (or a test-fixture stand-in).
        frontend_ts_path: Path to frontend/src/seed/seedCarparks.ts (or a test-fixture
            stand-in).

    Raises:
        SeedListRegenError: On any invalid input, per the functions above.
    """
    poller_original = poller_ts_path.read_text(encoding="utf-8")
    frontend_original = frontend_ts_path.read_text(encoding="utf-8")

    existing = parse_existing_carparks(poller_original)
    verified = load_verified_candidates(coverage_map_path)
    combined = build_combined_carparks(existing, verified)

    new_poller_text = render_poller_carparks_ts(poller_original, combined)
    new_frontend_text = render_frontend_seed_carparks_ts(frontend_original, combined)

    poller_ts_path.write_text(new_poller_text, encoding="utf-8", newline="\n")
    frontend_ts_path.write_text(new_frontend_text, encoding="utf-8", newline="\n")

    logger.info(
        "Regenerated %s and %s -- %d existing + %d newly-verified = %d total seed carparks",
        poller_ts_path,
        frontend_ts_path,
        len(existing),
        len(verified),
        len(combined),
    )


def main() -> None:
    """Parse CLI args and run the regenerator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "coverage_map",
        nargs="?",
        type=Path,
        default=DEFAULT_COVERAGE_MAP_PATH,
        help=f"Path to the coverage-map JSON (default: {DEFAULT_COVERAGE_MAP_PATH})",
    )
    parser.add_argument("--poller-ts", type=Path, default=DEFAULT_POLLER_TS_PATH)
    parser.add_argument("--frontend-ts", type=Path, default=DEFAULT_FRONTEND_TS_PATH)
    args = parser.parse_args()

    try:
        regenerate(args.coverage_map, args.poller_ts, args.frontend_ts)
    except SeedListRegenError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
