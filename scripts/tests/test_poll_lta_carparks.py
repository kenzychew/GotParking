"""Tests for poll_lta_carparks.append_samples, focused on the LotType regression."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from poll_lta_carparks import append_samples  # noqa: E402


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_writes_only_candidate_ids(tmp_path: Path) -> None:
    output = tmp_path / "samples.csv"
    records: list[dict[str, str | int]] = [
        {"CarParkID": "1", "Development": "Suntec City", "AvailableLots": 10, "LotType": "C"},
        {"CarParkID": "999", "Development": "Not Tracked", "AvailableLots": 20, "LotType": "C"},
    ]
    written = append_samples(records, output, candidate_ids={"1": "Suntec City"})
    assert written == 1
    rows = _read_rows(output)
    assert len(rows) == 1
    assert rows[0]["carpark_id"] == "1"
    assert rows[0]["available_lots"] == "10"


def test_regression_picks_only_the_car_lot_type_row(tmp_path: Path) -> None:
    """Real shape found live 2026-07-08: carpark A0007 reported 0 lots on its Y (motorcycle)
    row and 224 on its C (car) row in the same poll. Pre-fix, this function wrote every
    matching record regardless of LotType -- silently mixing car/motorcycle/heavy-vehicle
    readings into one variance series for the same carpark_id."""
    output = tmp_path / "samples.csv"
    records: list[dict[str, str | int]] = [
        {"CarParkID": "1", "Development": "Test Carpark", "AvailableLots": 0, "LotType": "Y"},
        {"CarParkID": "1", "Development": "Test Carpark", "AvailableLots": 224, "LotType": "C"},
        {"CarParkID": "1", "Development": "Test Carpark", "AvailableLots": 5, "LotType": "H"},
    ]
    written = append_samples(records, output, candidate_ids={"1": "Test Carpark"})
    assert written == 1
    rows = _read_rows(output)
    assert len(rows) == 1
    assert rows[0]["available_lots"] == "224"


def test_regression_carpark_with_no_car_row_writes_nothing(tmp_path: Path) -> None:
    """Real shape found live 2026-07-08: "42 Defu Lane 7 HVP" (heavy-vehicle-only lot) has
    zero C rows -- correctly excluded entirely, not fallen back to a Y/H reading."""
    output = tmp_path / "samples.csv"
    records: list[dict[str, str | int]] = [{"CarParkID": "1", "Development": "HVP Only", "AvailableLots": 12, "LotType": "H"}]
    written = append_samples(records, output, candidate_ids={"1": "HVP Only"})
    assert written == 0
    assert not output.exists() or _read_rows(output) == []


def test_multiple_polls_each_dedupe_independently(tmp_path: Path) -> None:
    """The already_written_this_poll guard must reset per call -- a real multi-poll
    observation window should get one row per carpark per poll, not zero after the first."""
    output = tmp_path / "samples.csv"
    records: list[dict[str, str | int]] = [{"CarParkID": "1", "Development": "Suntec City", "AvailableLots": 10, "LotType": "C"}]
    written_1 = append_samples(records, output, candidate_ids={"1": "Suntec City"})
    written_2 = append_samples(records, output, candidate_ids={"1": "Suntec City"})
    assert written_1 == 1
    assert written_2 == 1
    assert len(_read_rows(output)) == 2
