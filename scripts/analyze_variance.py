"""Analyze carpark lot-count variance from polled samples (Implementation Task T1).

Answers the outside-voice review's core question: do candidate seed carparks actually
fluctuate enough for a 20-minute forecast to have any edge over reading the live number?

Usage:
    uv run python scripts/analyze_variance.py
"""

from __future__ import annotations

import csv
import logging
import statistics
from collections import defaultdict
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "carpark_samples.csv"

# Below this range (max - min available lots observed), a carpark is judged too stable
# for a 20-minute forecast to add value over just reading the live count.
MIN_MEANINGFUL_RANGE = 20

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def load_samples(data_file: Path) -> dict[str, list[int]]:
    """Load polled samples grouped by development name.

    Args:
        data_file: CSV written by scripts/poll_lta_carparks.py.

    Returns:
        Mapping of development name to the list of available-lots readings collected.

    Raises:
        FileNotFoundError: If no samples have been collected yet.
    """
    if not data_file.exists():
        raise FileNotFoundError(
            f"No samples at {data_file} yet — run scripts/poll_lta_carparks.py first "
            "and let it collect data for a few hours."
        )

    by_development: dict[str, list[int]] = defaultdict(list)
    with data_file.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_development[row["development"]].append(int(row["available_lots"]))
    return by_development


def summarize(by_development: dict[str, list[int]]) -> None:
    """Print a variance summary per carpark and flag which ones are worth forecasting.

    Args:
        by_development: Mapping of development name to available-lots readings.
    """
    logger.info("%-20s %8s %8s %8s %10s %s", "Development", "samples", "min", "max", "range", "verdict")
    logger.info("-" * 80)

    worth_forecasting = []
    too_stable = []

    for development, readings in sorted(by_development.items()):
        if len(readings) < 2:
            logger.info("%-20s %8d %8s %8s %10s not enough samples yet", development, len(readings), "-", "-", "-")
            continue

        lo, hi = min(readings), max(readings)
        rng = hi - lo
        stdev = statistics.pstdev(readings)
        verdict = "WORTH FORECASTING" if rng >= MIN_MEANINGFUL_RANGE else "too stable — reconsider"
        (worth_forecasting if rng >= MIN_MEANINGFUL_RANGE else too_stable).append(development)

        logger.info(
            "%-20s %8d %8d %8d %10d %s (stdev=%.1f)", development, len(readings), lo, hi, rng, verdict, stdev
        )

    logger.info("-" * 80)
    logger.info("Worth forecasting (range >= %d lots): %s", MIN_MEANINGFUL_RANGE, worth_forecasting or "none yet")
    logger.info("Too stable, reconsider for seed list: %s", too_stable or "none")


def main() -> None:
    """Load samples and print the variance summary."""
    by_development = load_samples(DATA_FILE)
    summarize(by_development)


if __name__ == "__main__":
    main()
