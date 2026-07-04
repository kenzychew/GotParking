"""Poll LTA DataMall carpark availability and log samples for signal-strength validation.

Standalone research script for Implementation Task T1 (design doc: validate that candidate
seed carparks actually have meaningful lot-count variance before committing to a seed list).
Not part of the shipped app — the real poller (poller/) will be a Cloudflare Worker.

Usage:
    uv run python scripts/poll_lta_carparks.py --interval-minutes 5 --duration-hours 6
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LTA_ENDPOINT = "https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "carpark_samples.csv"

# Candidate "major mall" carparks identified from a live API pull — these are real LTA
# CarParkIDs matched to well-known Development names, not guesses.
CANDIDATE_CARPARK_IDS = {
    "2": "Marina Square",
    "3": "Raffles City",
    "1": "Suntec City",
    "16": "VivoCity P3",
    "50": "VivoCity P2",
    "13": "Ngee Ann City",
    "24": "313@Somerset",
    "21": "Centrepoint",
    "15": "Wheelock Place",
    "11": "Cineleisure",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_carpark_availability(api_key: str) -> list[dict[str, str | int]]:
    """Fetch the current carpark availability snapshot from LTA DataMall.

    Args:
        api_key: LTA DataMall AccountKey.

    Returns:
        List of carpark records as returned by the API.

    Raises:
        urllib.error.HTTPError: If the API request fails.
    """
    request = urllib.request.Request(
        LTA_ENDPOINT,
        headers={"AccountKey": api_key, "accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("value", [])


def append_samples(records: list[dict[str, str | int]], output_file: Path) -> int:
    """Append candidate-carpark samples from one poll to the CSV log.

    Args:
        records: Raw carpark records from the LTA API.
        output_file: CSV file to append to (created with a header if missing).

    Returns:
        Number of candidate-carpark rows written this poll.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output_file.exists()
    timestamp = datetime.now(timezone.utc).isoformat()

    written = 0
    with output_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp_utc", "carpark_id", "development", "available_lots"])
        for record in records:
            carpark_id = str(record.get("CarParkID", ""))
            if carpark_id not in CANDIDATE_CARPARK_IDS:
                continue
            writer.writerow(
                [timestamp, carpark_id, CANDIDATE_CARPARK_IDS[carpark_id], record.get("AvailableLots")]
            )
            written += 1
    return written


def run(interval_minutes: int, duration_hours: float, output_file: Path) -> None:
    """Poll on a fixed interval for a fixed duration, logging progress.

    Args:
        interval_minutes: Minutes between polls.
        duration_hours: Total duration to run for, in hours.
        output_file: CSV file to append samples to.
    """
    api_key = os.environ.get("LTA_API_KEY")
    if not api_key:
        raise RuntimeError("LTA_API_KEY not set — export it or load it from .env first")

    end_time = time.time() + duration_hours * 3600
    poll_count = 0

    while time.time() < end_time:
        try:
            records = fetch_carpark_availability(api_key)
            written = append_samples(records, output_file)
            poll_count += 1
            logger.info("Poll %d: wrote %d candidate-carpark rows to %s", poll_count, written, output_file)
        except urllib.error.HTTPError as exc:
            logger.warning("Poll failed (HTTP %s): %s", exc.code, exc.reason)
        except Exception:
            logger.exception("Poll failed with an unexpected error")

        time.sleep(interval_minutes * 60)

    logger.info("Done — %d polls completed over %.1f hours", poll_count, duration_hours)


def main() -> None:
    """Parse CLI args and run the poller."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--duration-hours", type=float, default=6.0)
    parser.add_argument("--output-file", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()

    run(args.interval_minutes, args.duration_hours, args.output_file)


if __name__ == "__main__":
    main()
