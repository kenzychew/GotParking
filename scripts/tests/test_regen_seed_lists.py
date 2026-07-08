"""Tests for scripts/regen_seed_lists.py.

Verifies against fixture files under scripts/tests/fixtures/ that the generator correctly
filters "verified" candidates, merges them with the existing seed list, regenerates both
target TS files to an exact expected byte-for-byte match, is idempotent, and hard-fails
(without writing anything) on invalid input. Does not check that the generated TypeScript
compiles -- out of scope for this Python script's own test suite (no TS toolchain needed).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import regen_seed_lists as rsl

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Copy the "before" TS fixtures into an isolated temp directory per test.

    Args:
        tmp_path: Pytest-provided temp directory.

    Returns:
        Path to the temp directory containing fresh copies of the before-fixtures.
    """
    shutil.copy(FIXTURES_DIR / "poller_carparks_before.ts", tmp_path / "carparks.ts")
    shutil.copy(
        FIXTURES_DIR / "frontend_seedCarparks_before.ts", tmp_path / "seedCarparks.ts"
    )
    return tmp_path


def test_load_verified_candidates_filters_by_state() -> None:
    """Only "verified" entries are returned; other states are dropped."""
    verified = rsl.load_verified_candidates(FIXTURES_DIR / "coverage_map_valid.json")
    ids = {c.carpark_id for c in verified}
    assert ids == {"100", "205"}


def test_load_verified_candidates_missing_file_hard_fails() -> None:
    """A nonexistent coverage-map path raises a clear SeedListRegenError."""
    with pytest.raises(rsl.SeedListRegenError, match="not found"):
        rsl.load_verified_candidates(FIXTURES_DIR / "does_not_exist.json")


def test_load_verified_candidates_malformed_json_hard_fails() -> None:
    """Malformed JSON raises a clear SeedListRegenError, not a raw JSONDecodeError."""
    with pytest.raises(rsl.SeedListRegenError, match="Malformed JSON"):
        rsl.load_verified_candidates(FIXTURES_DIR / "coverage_map_malformed.json")


def test_load_verified_candidates_missing_name_hard_fails() -> None:
    """An entry missing the required "name" field raises a clear SeedListRegenError."""
    with pytest.raises(rsl.SeedListRegenError, match="name"):
        rsl.load_verified_candidates(FIXTURES_DIR / "coverage_map_missing_name.json")


def test_parse_existing_carparks_reads_all_ten(workdir: Path) -> None:
    """The canonical existing-carpark parse reads exactly the 10 seed entries."""
    text = (workdir / "carparks.ts").read_text(encoding="utf-8")
    existing = rsl.parse_existing_carparks(text)
    assert len(existing) == 10
    assert existing["1"] == "Suntec City"
    assert existing["50"] == "VivoCity P2"


def test_build_combined_carparks_conflict_hard_fails() -> None:
    """A verified candidate whose carpark_id already exists in the seed list is rejected."""
    existing = {"1": "Suntec City"}
    verified = [rsl.VerifiedCarpark(carpark_id="1", name="Suntec City (dup)")]
    with pytest.raises(rsl.SeedListRegenError, match="already exists"):
        rsl.build_combined_carparks(existing, verified)


def test_sorted_entries_is_numeric_not_lexical() -> None:
    """Carpark IDs sort numerically ("2" before "11"), not lexically ("11" before "2")."""
    combined = {"11": "Cineleisure", "2": "Marina Square", "100": "Test Mall Beta"}
    ids_in_order = [carpark_id for carpark_id, _ in rsl.sorted_entries(combined)]
    assert ids_in_order == ["2", "11", "100"]


def test_sorted_entries_handles_alphanumeric_ids_without_crashing() -> None:
    """Regression: LTA CarParkIDs are not all numeric -- the full-feed coverage-expansion
    wave (2026-07-08) introduced alphanumeric, area-letter-prefixed IDs (e.g. "A0007"). A
    bare int() sort key crashes on these; numeric IDs must still sort numerically among
    themselves, with alphanumeric ones sorted after, alphabetically."""
    combined = {"11": "Cineleisure", "A0007": "Angullia Park", "2": "Marina Square", "B0063": "Bukit Batok"}
    ids_in_order = [carpark_id for carpark_id, _ in rsl.sorted_entries(combined)]
    assert ids_in_order == ["2", "11", "A0007", "B0063"]


def test_regenerate_matches_expected_fixtures(workdir: Path) -> None:
    """End-to-end: regenerate() output matches the hand-verified "after" fixtures exactly."""
    poller_ts = workdir / "carparks.ts"
    frontend_ts = workdir / "seedCarparks.ts"

    rsl.regenerate(FIXTURES_DIR / "coverage_map_valid.json", poller_ts, frontend_ts)

    expected_poller = (FIXTURES_DIR / "poller_carparks_after.ts").read_text(encoding="utf-8")
    expected_frontend = (FIXTURES_DIR / "frontend_seedCarparks_after.ts").read_text(
        encoding="utf-8"
    )

    assert poller_ts.read_text(encoding="utf-8") == expected_poller
    assert frontend_ts.read_text(encoding="utf-8") == expected_frontend


def test_regenerate_preserves_helper_functions_verbatim(workdir: Path) -> None:
    """The frontend helper functions are untouched -- only the SEED_CARPARKS array changes."""
    frontend_ts = workdir / "seedCarparks.ts"
    original = frontend_ts.read_text(encoding="utf-8")

    rsl.regenerate(FIXTURES_DIR / "coverage_map_valid.json", workdir / "carparks.ts", frontend_ts)

    regenerated = frontend_ts.read_text(encoding="utf-8")
    for snippet in (
        "export interface SeedCarpark {",
        "function isKnownCarparkId(id: string): boolean {",
        "function getSeedCarparkById(id: string): SeedCarpark | undefined {",
        "function searchSeedCarparks(query: string): SeedCarpark[] {",
        "const SEED_CARPARK_IDS: ReadonlySet<string> = new Set(SEED_CARPARKS.map((c) => c.id));",
    ):
        assert snippet in original
        assert snippet in regenerated


def test_regenerate_is_idempotent(tmp_path: Path) -> None:
    """Running the full pipeline twice on fresh copies of the same input is byte-identical."""
    run_a = tmp_path / "run_a"
    run_b = tmp_path / "run_b"
    run_a.mkdir()
    run_b.mkdir()

    for run_dir in (run_a, run_b):
        shutil.copy(FIXTURES_DIR / "poller_carparks_before.ts", run_dir / "carparks.ts")
        shutil.copy(
            FIXTURES_DIR / "frontend_seedCarparks_before.ts", run_dir / "seedCarparks.ts"
        )

    coverage_map = FIXTURES_DIR / "coverage_map_valid.json"
    rsl.regenerate(coverage_map, run_a / "carparks.ts", run_a / "seedCarparks.ts")
    rsl.regenerate(coverage_map, run_b / "carparks.ts", run_b / "seedCarparks.ts")

    assert (run_a / "carparks.ts").read_bytes() == (run_b / "carparks.ts").read_bytes()
    assert (run_a / "seedCarparks.ts").read_bytes() == (run_b / "seedCarparks.ts").read_bytes()


def test_regenerate_missing_coverage_map_writes_nothing(workdir: Path) -> None:
    """A missing coverage-map input aborts before either target file is touched."""
    poller_ts = workdir / "carparks.ts"
    frontend_ts = workdir / "seedCarparks.ts"
    poller_before = poller_ts.read_text(encoding="utf-8")
    frontend_before = frontend_ts.read_text(encoding="utf-8")

    with pytest.raises(rsl.SeedListRegenError):
        rsl.regenerate(FIXTURES_DIR / "does_not_exist.json", poller_ts, frontend_ts)

    assert poller_ts.read_text(encoding="utf-8") == poller_before
    assert frontend_ts.read_text(encoding="utf-8") == frontend_before


def test_regenerate_conflicting_candidate_writes_nothing(workdir: Path) -> None:
    """A verified candidate that collides with an existing seed id aborts before writing."""
    poller_ts = workdir / "carparks.ts"
    frontend_ts = workdir / "seedCarparks.ts"
    poller_before = poller_ts.read_text(encoding="utf-8")
    frontend_before = frontend_ts.read_text(encoding="utf-8")

    with pytest.raises(rsl.SeedListRegenError, match="already exists"):
        rsl.regenerate(FIXTURES_DIR / "coverage_map_conflict.json", poller_ts, frontend_ts)

    assert poller_ts.read_text(encoding="utf-8") == poller_before
    assert frontend_ts.read_text(encoding="utf-8") == frontend_before


def test_ts_string_literal_is_ascii_double_quoted() -> None:
    """String literals render as double-quoted, ASCII-safe TS strings."""
    assert rsl.ts_string_literal("313@Somerset") == '"313@Somerset"'
