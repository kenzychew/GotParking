#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rapidfuzz"]
# ///
"""Phase 0 recon: count candidate mall carparks in the live LTA feed.

Standalone research script for the carpark coverage-expansion plan's Phase 0 (see
`~/.gstack/projects/gstack-playground/ceo-plans/2026-07-07-carpark-coverage-expansion.md`).
Fetches the live LTA feed, excludes the 10 already-seeded carparks, and fuzzy-matches every
remaining `Development` name against data.gov.sg's "Carpark Rates (Major Shopping Malls,
Attractions and Hotels)" dataset. Prints a candidate count only -- no database writes, no
state machine, no coverage-map artifact (that's the full Phase A script, a separate task).

This script exists purely to answer one question: is the real candidate count small
(roughly 15-30, favoring Approach C -- a CI-generated static list) or large (~50-100+,
favoring Approach B -- a DB-driven poller)?

Usage:
    uv run scripts/recon_mall_whitelist.py
"""

from __future__ import annotations

import logging
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poll_lta_carparks import fetch_carpark_availability  # noqa: E402 (reuse per Decision 5, DRY)

LTA_API_KEY_ENV = "LTA_API_KEY"

# Carpark Rates (Major Shopping Malls, Attractions and Hotels) -- data.gov.sg, dataset
# d_9f6056bdb6b1dfba57f063593e4f34ae. Field "carpark" is the free-text name matched below.
MALL_DATASET_URL = (
    "https://data.gov.sg/api/action/datastore_search"
    "?resource_id=d_9f6056bdb6b1dfba57f063593e4f34ae"
)
MALL_DATASET_PAGE_SIZE = 100

# The 10 T1-validated seed carparks -- must match poller/src/carparks.ts's SEED_CARPARK_NAMES
# exactly (same manual-sync convention already used across this repo). Excluded from matching
# since they're already active/seasoned and would otherwise needlessly re-enter recon.
EXISTING_SEED_CARPARK_IDS = {"1", "2", "3", "11", "13", "15", "16", "21", "24", "50"}

# rapidfuzz token_sort_ratio threshold -- see the CEO plan's Definitions section for the
# rationale (same specificity level as analyze_variance.py's MIN_MEANINGFUL_RANGE).
FUZZY_MATCH_THRESHOLD = 85

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """One LTA carpark's best fuzzy-match outcome against the mall dataset.

    Attributes:
        carpark_id: LTA CarParkID.
        development: LTA's Development name.
        best_match: Best-scoring mall-dataset name, or None if nothing scored >= threshold.
        best_score: Score of best_match (0-100), or None.
        ambiguous: True if a second candidate scored within 1 point of best_score.
    """

    carpark_id: str
    development: str
    best_match: str | None
    best_score: float | None
    ambiguous: bool


def fetch_mall_dataset_names(page_size: int = MALL_DATASET_PAGE_SIZE) -> list[str]:
    """Fetch every "carpark" name from data.gov.sg's Carpark Rates dataset, paginated.

    Args:
        page_size: Rows per page (data.gov.sg's datastore_search default cap is 100).

    Returns:
        List of raw "carpark" name strings, one per dataset row.

    Raises:
        urllib.error.HTTPError: If the API request fails.
        ValueError: If the response is missing the expected fields.
    """
    import json

    names: list[str] = []
    offset = 0
    while True:
        url = f"{MALL_DATASET_URL}&limit={page_size}&offset={offset}"
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result = payload.get("result")
        if result is None:
            raise ValueError(f"Unexpected data.gov.sg response shape: {payload!r}")
        records = result.get("records", [])
        names.extend(str(r["carpark"]) for r in records)
        total = result.get("total", len(names))
        offset += page_size
        if offset >= total:
            break
    return names


def match_candidates(
    lta_records: list[dict[str, str | int]],
    mall_names: list[str],
    exclude_ids: set[str],
    threshold: int,
) -> list[MatchResult]:
    """Fuzzy-match every non-excluded LTA carpark against the mall dataset.

    Args:
        lta_records: Raw records from the live LTA feed.
        mall_names: Reference mall names from data.gov.sg.
        exclude_ids: CarParkIDs to skip (already-seeded carparks).
        threshold: Minimum rapidfuzz token_sort_ratio to count as a match.

    Returns:
        One MatchResult per non-excluded LTA carpark whose best score is >= threshold.
    """
    results: list[MatchResult] = []
    for record in lta_records:
        carpark_id = str(record.get("CarParkID", ""))
        if carpark_id in exclude_ids:
            continue
        development = str(record.get("Development", ""))
        if not development:
            continue

        scored = sorted(
            ((name, fuzz.token_sort_ratio(development, name)) for name in mall_names),
            key=lambda pair: pair[1],
            reverse=True,
        )
        if not scored or scored[0][1] < threshold:
            continue

        best_match, best_score = scored[0]
        ambiguous = len(scored) > 1 and (best_score - scored[1][1]) < 1.0
        results.append(
            MatchResult(
                carpark_id=carpark_id,
                development=development,
                best_match=best_match,
                best_score=best_score,
                ambiguous=ambiguous,
            )
        )
    return results


def main() -> None:
    """Run Phase 0 recon and print the candidate count."""
    import os

    api_key = os.environ.get(LTA_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(f"{LTA_API_KEY_ENV} not set -- export it or load it from .env first")

    logger.info("Fetching live LTA feed...")
    lta_records = fetch_carpark_availability(api_key)
    logger.info("LTA feed returned %d total carparks", len(lta_records))

    logger.info("Fetching data.gov.sg mall dataset...")
    mall_names = fetch_mall_dataset_names()
    logger.info("Mall dataset returned %d reference names", len(mall_names))

    results = match_candidates(
        lta_records, mall_names, EXISTING_SEED_CARPARK_IDS, FUZZY_MATCH_THRESHOLD
    )

    matched = [r for r in results if not r.ambiguous]
    ambiguous = [r for r in results if r.ambiguous]

    logger.info("-" * 80)
    logger.info("%-10s %-30s %-30s %8s", "CarParkID", "LTA Development", "Best mall-dataset match", "Score")
    for r in sorted(results, key=lambda r: r.best_score or 0, reverse=True):
        flag = " [AMBIGUOUS]" if r.ambiguous else ""
        logger.info("%-10s %-30s %-30s %8.1f%s", r.carpark_id, r.development, r.best_match, r.best_score, flag)
    logger.info("-" * 80)
    logger.info("Candidate count (matched, threshold >= %d): %d", FUZZY_MATCH_THRESHOLD, len(matched))
    logger.info("Needs manual disambiguation: %d", len(ambiguous))
    logger.info("Total new candidates found: %d", len(results))


if __name__ == "__main__":
    main()
