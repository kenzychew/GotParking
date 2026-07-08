"""Full-feed recon: direct-match every remaining LTA carpark (no fuzzy matching).

Second coverage-expansion wave, per TODOS.md's "Remaining ~400 LTA feed carparks" entry.
Unlike `recon_mall_whitelist.py` (which cross-references data.gov.sg's mall-rates dataset via
fuzzy name matching), this wave has no external reference dataset to match against -- every
carpark still in the live LTA feed but not yet onboarded IS itself a direct candidate. There is
no ambiguity to resolve and no `matched_dataset_name` to attribute (kept `None` in the coverage
map for this wave's entries, distinguishing them from the mall wave's fuzzy-matched ones at a
glance).

Feeds directly into `build_mall_whitelist.py`'s existing state machine (`CoverageEntry`,
`write_coverage_map`, `advance_signed_off_entries`, `evaluate_observed_entries`,
`render_sql_inserts`) -- reused unchanged, not reimplemented, so the T7 human sign-off gate,
the observation window, and the verified/rejected variance gate all apply identically to this
wave.

Excludes, deliberately, more than just "already in `carparks`":
  * The 24 carparks already live in production (fetched from Supabase directly -- NOT a
    hardcoded constant, since `recon_mall_whitelist.EXISTING_SEED_CARPARK_IDS` is hardcoded to
    only the original 10 and would otherwise silently let the 14 mall-wave carparks re-enter
    as "new" candidates).
  * Every carpark_id already present in the coverage map regardless of state -- protects the
    mall wave's rejected entries (Bt Panjang Plaza/58, Singapore Flyer/6, Concorde Hotel/22)
    AND, critically, carpark_id 64 (Junction 8), which the mall wave found to be a confirmed
    false-positive fuzzy-match and which must never advance past its current unsigned "matched"
    state, per standing instruction. `merge_matches`-style post-gate protection alone is not
    enough here since Junction 8 is still in a PRE-gate state ("matched", never signed off) --
    only an explicit exclude-set entry guarantees this script can never accidentally sign it
    off as part of a bulk "sign off everything else" pass.

Usage:
    uv run scripts/recon_full_feed.py
    uv run scripts/recon_full_feed.py --coverage-map data/carpark_coverage_map.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mall_whitelist import (  # noqa: E402 (reuse, DRY)
    COVERAGE_MAP_FILE,
    STATE_MATCHED,
    CoverageEntry,
    load_coverage_map,
    write_coverage_map,
)
from recon_mall_whitelist import LTA_API_KEY_ENV, fetch_carpark_availability  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Never advance regardless of coverage-map state -- see module docstring. Junction 8 (64) is
# the load-bearing one: it is still in a pre-gate "matched" state from the mall wave and must
# stay that way.
NEVER_ADVANCE_CARPARK_IDS = {"64"}

# LTA `Development` names are free text from a real government feed, not user input -- garbage
# here means "the feed itself glitched" (blank/whitespace, a placeholder token), not "text a
# human wouldn't recognize as a place." A live scan of the full remaining feed (2026-07-08)
# found zero true hits against this list -- HDB block numbers and "X OFF STREET" URA names are
# real, valid carpark names, not garbage, even though they look terse.
_GARBAGE_TOKENS = {"nil", "n.a", "n.a.", "na", "test", "tbc", "unknown", "-", "n/a", "tba", "xxx"}


def fetch_live_carpark_ids(supabase_url: str, service_role_key: str) -> set[str]:
    """Fetch the current live `carparks.carpark_id` set directly from production.

    Deliberately NOT a hardcoded constant (unlike `recon_mall_whitelist.
    EXISTING_SEED_CARPARK_IDS`, which is frozen at the original 10 and would silently let
    already-onboarded mall-wave carparks re-enter recon as "new").

    Args:
        supabase_url: Supabase project base URL.
        service_role_key: Supabase service-role key (bypasses RLS for the read).

    Returns:
        The full live set of `carpark_id` strings.

    Raises:
        urllib.error.HTTPError: If the REST call fails.
    """
    url = f"{supabase_url.rstrip('/')}/rest/v1/carparks?select=carpark_id"
    request = urllib.request.Request(
        url,
        headers={"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return {str(row["carpark_id"]) for row in rows}


def is_garbage_name(name: str) -> bool:
    """Flag a `Development` name as a likely feed glitch rather than a real carpark.

    Deliberately conservative -- HDB block numbers ("BLK 101 PUNGGOL FIELD") and URA
    off-street names ("AMOY STREET OFF STREET") are real, valid names despite looking terse
    or code-like; this only catches genuine placeholder/blank tokens.

    Args:
        name: Raw LTA `Development` field value.

    Returns:
        True if the name looks like a feed glitch (blank, or an exact placeholder token),
        not a real place name.
    """
    stripped = name.strip()
    if not stripped:
        return True
    if stripped.lower() in _GARBAGE_TOKENS:
        return True
    # A name with no letters at all (pure punctuation/digits) is not a place name.
    if not re.search(r"[A-Za-z]", stripped):
        return True
    return False


def discover_full_feed_candidates(
    lta_records: list[dict[str, str | int]], exclude_ids: set[str]
) -> list[CoverageEntry]:
    """Build direct-match coverage entries for every non-excluded LTA carpark.

    Args:
        lta_records: Raw records from the live LTA feed.
        exclude_ids: CarParkIDs to skip (live carparks + already-decided coverage-map
            entries + `NEVER_ADVANCE_CARPARK_IDS`).

    Returns:
        One `CoverageEntry` per non-excluded carpark, `state=STATE_MATCHED`,
        `match_score=100.0` (direct/self match, not fuzzy), `matched_dataset_name=None`
        (no external reference dataset for this wave).

    Note:
        LTA lists one row per (CarParkID, LotType) -- ~18% of the full feed also reports
        separate "Y"/"H" (motorcycle/heavy-vehicle) rows sharing the same CarParkID with a
        DIFFERENT AvailableLots (found live 2026-07-08). Only "C" (car) rows are considered
        here, matching the same fix applied to `poll_lta_carparks.append_samples` and the
        production poller's `parseSeedRows` -- a carpark reporting ONLY non-"C" LotTypes
        (found live: "42 Defu Lane 7 HVP", heavy-vehicle-only) is correctly excluded
        entirely, not just deduplicated, since it's out of scope for a car-parking product.
    """
    now = datetime.now(timezone.utc).isoformat()
    entries: list[CoverageEntry] = []
    seen_ids: set[str] = set()
    for record in lta_records:
        carpark_id = str(record.get("CarParkID", ""))
        if carpark_id in exclude_ids or carpark_id in seen_ids:
            continue
        if record.get("LotType") != "C":
            continue
        seen_ids.add(carpark_id)
        development = str(record.get("Development", ""))
        entries.append(
            CoverageEntry(
                carpark_id=carpark_id,
                name=development,
                state=STATE_MATCHED,
                match_score=100.0,
                matched_dataset_name=None,
                signed_off=False,
                variance_range=None,
                rejection_reason=None,
                updated_at=now,
            )
        )
    return entries


def merge_full_feed_entries(
    coverage_map: dict[str, Any], entries: list[CoverageEntry]
) -> dict[str, Any]:
    """Merge freshly-discovered full-feed entries into the coverage map.

    Same never-revert-a-post-gate-decision protection as `build_mall_whitelist.merge_matches`
    (entries already in the map with any state are left untouched -- this function only ever
    ADDS brand-new carpark_ids, since `discover_full_feed_candidates`'s `exclude_ids` already
    excludes every carpark_id present in the map, so no existing entry is ever passed in here
    to begin with; the check is kept as defense in depth).

    Args:
        coverage_map: Existing coverage-map dict.
        entries: Fresh `CoverageEntry` list from `discover_full_feed_candidates`.

    Returns:
        The same coverage-map dict, with new entries appended.
    """
    by_id: dict[str, dict[str, Any]] = {e["carpark_id"]: e for e in coverage_map.get("candidates", [])}
    for entry in entries:
        if entry.carpark_id in by_id:
            continue  # defense in depth -- should be unreachable, see docstring
        by_id[entry.carpark_id] = asdict(entry)
    coverage_map["candidates"] = list(by_id.values())
    return coverage_map


def main() -> None:
    """Run full-feed recon: fetch, exclude, direct-match, garbage-screen, merge, report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-map", type=Path, default=COVERAGE_MAP_FILE)
    args = parser.parse_args()

    lta_api_key = os.environ.get(LTA_API_KEY_ENV)
    if not lta_api_key:
        raise RuntimeError(f"{LTA_API_KEY_ENV} not set -- export it or load it from .env first")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set -- load .env first")

    logger.info("Fetching live LTA feed...")
    lta_records = fetch_carpark_availability(lta_api_key)
    logger.info("LTA feed returned %d total carparks", len(lta_records))

    logger.info("Fetching live carparks table (ground truth, not a hardcoded constant)...")
    live_ids = fetch_live_carpark_ids(supabase_url, supabase_key)
    logger.info("%d carparks already live in production", len(live_ids))

    coverage_map = load_coverage_map(args.coverage_map)
    already_decided_ids = {e["carpark_id"] for e in coverage_map.get("candidates", [])}
    exclude_ids = live_ids | already_decided_ids | NEVER_ADVANCE_CARPARK_IDS
    logger.info(
        "Excluding %d ids total (%d live + %d already-decided + %d never-advance, "
        "deduplicated)",
        len(exclude_ids),
        len(live_ids),
        len(already_decided_ids),
        len(NEVER_ADVANCE_CARPARK_IDS),
    )

    entries = discover_full_feed_candidates(lta_records, exclude_ids)
    logger.info("Full-feed recon found %d new candidates", len(entries))

    garbage = [e for e in entries if is_garbage_name(e.name)]
    clean = [e for e in entries if not is_garbage_name(e.name)]

    logger.info("-" * 80)
    if garbage:
        logger.info("GARBAGE-FLAGGED (%d) -- needs human review before sign-off:", len(garbage))
        for e in garbage:
            logger.info("  %-10s %r", e.carpark_id, e.name)
    else:
        logger.info("GARBAGE-FLAGGED: 0 -- every remaining Development name looks like a real place")
    logger.info("-" * 80)
    logger.info("Clean candidates ready for sign-off: %d", len(clean))

    coverage_map = merge_full_feed_entries(coverage_map, entries)
    write_coverage_map(args.coverage_map, coverage_map)
    logger.info("Coverage map updated: %s (%d total candidates across all waves)",
                args.coverage_map, len(coverage_map["candidates"]))


if __name__ == "__main__":
    main()
