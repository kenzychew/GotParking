"""Tests for recon_full_feed.py: direct-match discovery and garbage-name detection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recon_full_feed import (  # noqa: E402
    discover_full_feed_candidates,
    is_garbage_name,
    merge_full_feed_entries,
)


def test_is_garbage_name_flags_blank_and_placeholder_tokens() -> None:
    assert is_garbage_name("") is True
    assert is_garbage_name("   ") is True
    assert is_garbage_name("NIL") is True
    assert is_garbage_name("N.A.") is True
    assert is_garbage_name("TEST") is True
    assert is_garbage_name("-") is True


def test_is_garbage_name_does_not_flag_real_but_terse_names() -> None:
    """HDB block numbers and URA off-street names are real, valid carpark names despite
    looking terse or code-like -- the whole point of this heuristic is to NOT flag these."""
    assert is_garbage_name("BLK 101 PUNGGOL FIELD") is False
    assert is_garbage_name("AMOY STREET OFF STREET") is False
    assert is_garbage_name("42 DEFU LANE 7 HVP") is False
    assert is_garbage_name("BED0K SOUTH AVENUE 2") is False  # real LTA typo, still a real place


def test_discover_excludes_ids_and_filters_to_car_lot_type() -> None:
    records: list[dict[str, str | int]] = [
        {"CarParkID": "1", "Development": "Already Live", "LotType": "C"},
        {"CarParkID": "2", "Development": "New Candidate", "LotType": "C"},
        {"CarParkID": "3", "Development": "Motorcycle Only", "LotType": "Y"},
    ]
    entries = discover_full_feed_candidates(records, exclude_ids={"1"})
    assert [e.carpark_id for e in entries] == ["2"]
    assert entries[0].name == "New Candidate"
    assert entries[0].match_score == 100.0
    assert entries[0].matched_dataset_name is None
    assert entries[0].state == "matched"
    assert entries[0].signed_off is False


def test_discover_deduplicates_multi_lot_type_carpark_to_the_car_row() -> None:
    records: list[dict[str, str | int]] = [
        {"CarParkID": "1", "Development": "Multi Lot", "AvailableLots": 0, "LotType": "Y"},
        {"CarParkID": "1", "Development": "Multi Lot", "AvailableLots": 224, "LotType": "C"},
    ]
    entries = discover_full_feed_candidates(records, exclude_ids=set())
    assert len(entries) == 1
    assert entries[0].carpark_id == "1"


def test_merge_never_overwrites_an_already_present_entry() -> None:
    from build_mall_whitelist import CoverageEntry

    coverage_map = {
        "candidates": [
            {
                "carpark_id": "5",
                "name": "Existing",
                "state": "verified",
                "match_score": 100.0,
                "matched_dataset_name": "Existing",
                "signed_off": True,
                "variance_range": 200,
                "rejection_reason": None,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    }
    new_entry = CoverageEntry(
        carpark_id="5",
        name="Should Not Appear",
        state="matched",
        match_score=100.0,
        matched_dataset_name=None,
        signed_off=False,
        variance_range=None,
        rejection_reason=None,
        updated_at="2026-01-02T00:00:00+00:00",
    )
    merged = merge_full_feed_entries(coverage_map, [new_entry])
    assert len(merged["candidates"]) == 1
    assert merged["candidates"][0]["name"] == "Existing"
