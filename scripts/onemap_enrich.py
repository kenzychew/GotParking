"""Enrich carparks with OneMap building name / address / postal code + coordinates.

One-time batch (all currently-live carparks) and the reusable per-wave step for future
coverage-expansion onboarding (see the module docstring's "on-insert" note below). Reads
each carpark's coordinate from the live LTA feed's `Location` field (never previously
captured anywhere in this project -- carparks.latitude/longitude are new columns as of
2026-07-10), reverse-geocodes it via OneMap, and writes the result straight to production
via the Supabase REST API (PATCH per carpark_id, not a bulk upsert, since every row's
target values differ and a carpark OneMap can't resolve must get ONLY its coordinates
written, not a null-stomped onemap_building_name overwriting a previously-good value from
an earlier partial run).

"Honest beats invented" (explicit design constraint): a carpark reverse-geocode can't
resolve keeps onemap_building_name/address/postal_code as SQL NULL. Every consumer
(regen_seed_lists.py, the frontend) must fall back to the carpark's raw `name` in that
case -- never fabricate a friendlier name than the source data actually supports.

On-insert hook: this script is the reusable enrichment step for FUTURE coverage-expansion
waves too, not just this one-time run -- see enrich_carparks()'s docstring. After a future
wave's carparks INSERT (build_mall_whitelist.py's render_sql_inserts / build_insert_payload
output applied to production), re-run this script; it only touches carparks with
onemap_enriched_at IS NULL by default, so it's safe and cheap to re-run after every wave
without re-processing already-enriched carparks.

Usage:
    uv run scripts/onemap_enrich.py
    uv run scripts/onemap_enrich.py --carpark-ids 4,12,17   # a specific new wave only
    uv run scripts/onemap_enrich.py --force                 # re-enrich everything
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onemap_client import GeocodeResult, OneMapUnavailableError, fetch_token, reverse_geocode  # noqa: E402
from poll_lta_carparks import fetch_carpark_availability  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Paced well under OneMap's documented 250 requests/minute (verified live 2026-07-10) --
# this is a one-time-per-carpark batch, not a latency-sensitive path, so there is no reason
# to run close to the limit.
PACING_SECONDS_BETWEEN_CALLS = 0.35


def fetch_live_carpark_coordinates(lta_api_key: str) -> dict[str, tuple[float, float]]:
    """Fetch every LTA carpark's (lat, lon) from its `Location` field.

    Args:
        lta_api_key: LTA DataMall AccountKey.

    Returns:
        Mapping of carpark_id -> (latitude, longitude), for every carpark present in the
        live feed (not filtered to onboarded carparks -- callers filter afterward).
    """
    records = fetch_carpark_availability(lta_api_key)
    coords: dict[str, tuple[float, float]] = {}
    for record in records:
        carpark_id = str(record.get("CarParkID", ""))
        location = str(record.get("Location", ""))
        parts = location.split()
        if len(parts) != 2:
            continue
        try:
            coords[carpark_id] = (float(parts[0]), float(parts[1]))
        except ValueError:
            continue
    return coords


def fetch_carparks_to_enrich(
    supabase_url: str, supabase_key: str, *, force: bool, only_ids: set[str] | None
) -> list[dict[str, Any]]:
    """Fetch the live `carparks` rows that need enrichment.

    Args:
        supabase_url: Supabase project base URL.
        supabase_key: Supabase service-role key.
        force: If True, re-enrich every carpark regardless of onemap_enriched_at.
        only_ids: If given, restrict to exactly these carpark_ids (a specific wave).

    Returns:
        List of `{"carpark_id": ..., "name": ...}` dicts needing enrichment.
    """
    url = f"{supabase_url.rstrip('/')}/rest/v1/carparks?select=carpark_id,name,onemap_enriched_at"
    request = urllib.request.Request(
        url, headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        rows: list[dict[str, Any]] = json.loads(response.read().decode("utf-8"))

    if only_ids is not None:
        rows = [r for r in rows if r["carpark_id"] in only_ids]
    if not force:
        rows = [r for r in rows if r.get("onemap_enriched_at") is None]
    return rows


def patch_carpark(
    supabase_url: str, supabase_key: str, carpark_id: str, fields: dict[str, Any]
) -> None:
    """PATCH one carpark row via the Supabase REST API.

    Args:
        supabase_url: Supabase project base URL.
        supabase_key: Supabase service-role key.
        carpark_id: The row to update.
        fields: Column -> new value.

    Raises:
        urllib.error.HTTPError: If the PATCH fails.
    """
    url = f"{supabase_url.rstrip('/')}/rest/v1/carparks?carpark_id=eq.{carpark_id}"
    body = json.dumps(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="PATCH",
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(request, timeout=15):
        pass


def enrich_carparks(
    carparks: list[dict[str, Any]],
    coordinates: dict[str, tuple[float, float]],
    token: str,
    supabase_url: str,
    supabase_key: str,
    now: datetime,
    *,
    reverse_geocode_fn=reverse_geocode,
    patch_fn=patch_carpark,
    pacing_seconds: float = PACING_SECONDS_BETWEEN_CALLS,
) -> tuple[int, int, list[str]]:
    """Reverse-geocode and PATCH every given carpark.

    Reusable for both the one-time full batch and a single new wave's on-insert
    enrichment step (same function, different `carparks` input) -- this is what "on-insert
    hook" means in practice for a CI-generated-static-list project (Approach C, per the
    coverage-expansion plan): there is no live event bus to hook into, so the hook is "call
    this function again after inserting a new wave," not an automatic DB trigger.

    Args:
        carparks: Rows needing enrichment (carpark_id, name).
        coordinates: carpark_id -> (lat, lon), from `fetch_live_carpark_coordinates`.
        token: A valid OneMap bearer token.
        supabase_url: Supabase project base URL.
        supabase_key: Supabase service-role key.
        now: Stamped as onemap_enriched_at for every processed carpark (including
            unresolvable ones -- "we tried and it didn't resolve" is itself worth
            recording, distinct from "never attempted").
        reverse_geocode_fn: Injectable (tests avoid the real network).
        patch_fn: Injectable (tests avoid writing to a real database).
        pacing_seconds: Delay between OneMap calls.

    Returns:
        (resolved_count, unresolvable_count, carpark_ids_missing_coordinates).
    """
    resolved = 0
    unresolvable = 0
    missing_coords: list[str] = []

    for carpark in carparks:
        carpark_id = carpark["carpark_id"]
        coord = coordinates.get(carpark_id)
        if coord is None:
            missing_coords.append(carpark_id)
            logger.warning(
                "carpark_id=%s (%s) has no coordinate in the live LTA feed -- skipping, "
                "not present in the current feed snapshot", carpark_id, carpark["name"],
            )
            continue

        lat, lon = coord
        try:
            result: GeocodeResult | None = reverse_geocode_fn(token, lat, lon)
        except OneMapUnavailableError as exc:
            logger.error("carpark_id=%s reverse-geocode failed: %s", carpark_id, exc)
            time.sleep(pacing_seconds)
            continue

        fields: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "onemap_enriched_at": now.isoformat(),
        }
        if result is not None:
            fields.update(
                {
                    "onemap_building_name": result.building_name,
                    "onemap_address": result.address,
                    "onemap_postal_code": result.postal_code,
                }
            )
            resolved += 1
        else:
            unresolvable += 1
            logger.info(
                "carpark_id=%s (%s) unresolvable -- coordinates saved, "
                "onemap_building_name stays null (raw name is authoritative)",
                carpark_id, carpark["name"],
            )

        patch_fn(supabase_url, supabase_key, carpark_id, fields)
        time.sleep(pacing_seconds)

    return resolved, unresolvable, missing_coords


def main() -> None:
    """Run the enrichment batch against production."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--carpark-ids", type=str, default=None,
        help="Comma-separated carpark_ids to restrict to (default: all needing enrichment).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-enrich every carpark, including ones already enriched.",
    )
    args = parser.parse_args()
    only_ids = set(args.carpark_ids.split(",")) if args.carpark_ids else None

    lta_api_key = os.environ.get("LTA_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    onemap_email = os.environ.get("ONEMAP_EMAIL")
    onemap_password = os.environ.get("ONEMAP_PASSWORD")
    missing = [
        name
        for name, value in [
            ("LTA_API_KEY", lta_api_key), ("SUPABASE_URL", supabase_url),
            ("SUPABASE_SERVICE_ROLE_KEY", supabase_key), ("ONEMAP_EMAIL", onemap_email),
            ("ONEMAP_PASSWORD", onemap_password),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing required env var(s): {', '.join(missing)} -- load .env first")
    assert lta_api_key and supabase_url and supabase_key and onemap_email and onemap_password

    logger.info("Fetching OneMap token...")
    token, expiry = fetch_token(onemap_email, onemap_password)
    logger.info("Token valid until %s", expiry.isoformat())

    logger.info("Fetching carparks needing enrichment...")
    carparks = fetch_carparks_to_enrich(supabase_url, supabase_key, force=args.force, only_ids=only_ids)
    logger.info("%d carparks to enrich", len(carparks))
    if not carparks:
        return

    logger.info("Fetching live LTA feed for coordinates...")
    coordinates = fetch_live_carpark_coordinates(lta_api_key)

    now = datetime.now(timezone.utc)
    resolved, unresolvable, missing_coords = enrich_carparks(
        carparks, coordinates, token, supabase_url, supabase_key, now
    )
    logger.info(
        "Done: %d resolved, %d unresolvable (raw name kept), %d missing coordinates: %s",
        resolved, unresolvable, len(missing_coords), missing_coords,
    )


if __name__ == "__main__":
    main()
