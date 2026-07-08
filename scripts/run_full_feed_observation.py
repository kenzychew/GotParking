"""Sign off every clean full-feed candidate and run its observation window.

Second coverage-expansion wave's T7-gate-to-observation step. Deliberately does NOT call
`build_mall_whitelist.build_and_gate`/`run_full_cycle` -- those re-run a fresh fuzzy-match pass
against data.gov.sg's MALL dataset, which has no relevance to this wave's direct-match
candidates and risks silently re-attributing an entry (e.g. a named landmark like "Funan Mall"
that also happens to fuzzy-match the mall-rates dataset) from "direct full-feed match" to "mall
fuzzy match" mid-flight, corrupting the audit trail even though sign-off state itself would
survive. This script goes straight to `advance_signed_off_entries` / `evaluate_observed_entries`
(reused unchanged from build_mall_whitelist.py), skipping the irrelevant mall-dataset refresh.

Per-run CSV output: writes samples to a dedicated file (not the shared
data/carpark_samples.csv used by the mall wave and carpark 22's individual run), so this run's
readings never commingle with prior waves'.

Usage:
    uv run scripts/run_full_feed_observation.py
    uv run scripts/run_full_feed_observation.py --duration-hours 6 --interval-minutes 5
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mall_whitelist import (  # noqa: E402
    COVERAGE_MAP_FILE,
    STATE_MATCHED,
    advance_signed_off_entries,
    evaluate_observed_entries,
    load_coverage_map,
    write_coverage_map,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Carpark 64 (Junction 8) is a confirmed false-positive fuzzy-match from the mall wave and must
# never be signed off by this or any bulk pass, regardless of its coverage-map state.
NEVER_SIGN_OFF_CARPARK_IDS = {"64"}


def sign_off_full_feed_candidates(coverage_map: dict) -> list[str]:
    """Set signed_off=True on every pre-gate, direct-match (matched_dataset_name is None)
    entry -- i.e. every candidate this wave's recon_full_feed.py discovered, and nothing the
    mall wave discovered (those all have a non-None matched_dataset_name, or are already
    resolved past the gate).

    Args:
        coverage_map: Coverage-map dict, mutated in place.

    Returns:
        The list of carpark_ids signed off by this call.
    """
    signed: list[str] = []
    for entry in coverage_map["candidates"]:
        if entry["carpark_id"] in NEVER_SIGN_OFF_CARPARK_IDS:
            continue
        if entry["state"] != STATE_MATCHED:
            continue
        if entry["matched_dataset_name"] is not None:
            continue  # a mall-wave fuzzy-match entry, not this wave's -- leave alone
        if entry["signed_off"]:
            continue  # already signed off (idempotent re-run)
        entry["signed_off"] = True
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        signed.append(entry["carpark_id"])
    return signed


def main() -> None:
    """Sign off all clean full-feed candidates and run their observation window."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-map", type=Path, default=COVERAGE_MAP_FILE)
    parser.add_argument(
        "--poll-output-file",
        type=Path,
        default=DATA_DIR / "carpark_samples_full_feed_wave1.csv",
        help="Dedicated CSV for this run's samples (default: "
        "data/carpark_samples_full_feed_wave1.csv, separate from the mall wave's).",
    )
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--duration-hours", type=float, default=6.0)
    args = parser.parse_args()

    coverage_map = load_coverage_map(args.coverage_map)
    signed = sign_off_full_feed_candidates(coverage_map)
    write_coverage_map(args.coverage_map, coverage_map)
    # LTA CarParkIDs are not all numeric (alphanumeric area-letter-prefixed IDs exist, e.g.
    # "A0007") -- sort as plain strings for this log line, purely cosmetic either way.
    logger.info("Signed off %d full-feed candidates: %s", len(signed), ", ".join(sorted(signed)))

    if NEVER_SIGN_OFF_CARPARK_IDS & {e["carpark_id"] for e in coverage_map["candidates"] if e["signed_off"]}:
        raise RuntimeError(
            "A carpark in NEVER_SIGN_OFF_CARPARK_IDS ended up signed_off=true -- refusing to "
            "proceed. This must never happen; investigate before running again."
        )

    logger.info(
        "Starting observation window: %d carparks, %d-minute interval, %.1f hours, output=%s",
        len(signed),
        args.interval_minutes,
        args.duration_hours,
        args.poll_output_file,
    )
    observed = advance_signed_off_entries(
        coverage_map, args.poll_output_file, args.interval_minutes, args.duration_hours
    )
    if observed:
        evaluate_observed_entries(observed, args.poll_output_file)
        write_coverage_map(args.coverage_map, coverage_map)
        verified = [e for e in observed if e["state"] == "verified"]
        rejected = [e for e in observed if e["state"] == "rejected"]
        logger.info(
            "Observation window complete: %d verified, %d rejected (of %d observed)",
            len(verified),
            len(rejected),
            len(observed),
        )


if __name__ == "__main__":
    main()
