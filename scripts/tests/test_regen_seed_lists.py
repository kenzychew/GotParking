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


def test_load_verified_candidates_accepts_alphanumeric_carpark_id(tmp_path: Path) -> None:
    """Regression: LTA CarParkIDs are not all numeric (e.g. "A0007", area-letter-prefixed,
    introduced by the full-feed coverage-expansion wave 2026-07-09) -- these must be
    accepted, not rejected as invalid."""
    import json

    coverage_map = tmp_path / "coverage_map.json"
    coverage_map.write_text(
        json.dumps(
            {"candidates": [{"carpark_id": "A0007", "name": "Angullia Park", "state": "verified"}]}
        ),
        encoding="utf-8",
    )
    verified = rsl.load_verified_candidates(coverage_map)
    assert [c.carpark_id for c in verified] == ["A0007"]


def test_load_verified_candidates_rejects_non_alphanumeric_carpark_id(tmp_path: Path) -> None:
    """A carpark_id with punctuation/whitespace is genuinely malformed, not a valid LTA ID
    shape -- must still hard-fail."""
    import json

    coverage_map = tmp_path / "coverage_map.json"
    coverage_map.write_text(
        json.dumps(
            {"candidates": [{"carpark_id": "64!", "name": "Bad ID", "state": "verified"}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(rsl.SeedListRegenError, match="alphanumeric"):
        rsl.load_verified_candidates(coverage_map)


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
    """A verified candidate whose carpark_id already exists under a DIFFERENT name is
    rejected -- a genuine conflict."""
    existing = {"1": "Suntec City"}
    verified = [rsl.VerifiedCarpark(carpark_id="1", name="Suntec City (dup)")]
    with pytest.raises(rsl.SeedListRegenError, match="DIFFERENT name"):
        rsl.build_combined_carparks(existing, verified)


def test_build_combined_carparks_same_name_already_applied_is_a_noop() -> None:
    """Regression: a verified candidate whose carpark_id already exists with the SAME name
    is a harmless no-op, not an error. The coverage map accumulates every wave's "verified"
    entries -- a second wave's regen run legitimately re-sees the first wave's
    already-applied carparks (2026-07-09: this exact scenario tripped the pre-fix hard-fail
    on the full-feed wave's first real regeneration attempt, since the mall wave's 14
    carparks were already in poller/src/carparks.ts from an earlier run)."""
    existing = {"5": "Millenia Singapore"}
    verified = [rsl.VerifiedCarpark(carpark_id="5", name="Millenia Singapore")]
    combined = rsl.build_combined_carparks(existing, verified)
    assert combined == {"5": "Millenia Singapore"}


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


class TestOnemapEnrichmentRender:
    """render_frontend_seed_carparks_ts with real OnemapFields data (2026-07-10)."""

    def test_resolved_carpark_uses_onemap_display_name_and_fields(self) -> None:
        combined = {"1": "Suntec City"}
        onemap_data = {
            "1": rsl.OnemapFields(
                display_name="SUNTEC SINGAPORE CONVENTION & EXHIBITION CENTRE",
                postal_code="039593",
                latitude=1.2935,
                longitude=103.8572,
            )
        }
        text = "export const SEED_CARPARKS: readonly SeedCarpark[] = [\n\n];"

        rendered = rsl.render_frontend_seed_carparks_ts(text, combined, onemap_data)

        assert 'name: "Suntec City"' in rendered
        assert 'displayName: "SUNTEC SINGAPORE CONVENTION & EXHIBITION CENTRE"' in rendered
        assert 'postalCode: "039593"' in rendered
        assert "latitude: 1.2935" in rendered
        assert "longitude: 103.8572" in rendered

    def test_unresolvable_carpark_falls_back_to_raw_name_never_fabricates(self) -> None:
        """The core "honest beats invented" guarantee, at the render layer: a carpark
        with a None display_name (OneMap couldn't resolve it) must render displayName
        equal to the raw name, and must NOT emit a postalCode/lat/lon key at all."""
        combined = {"99": "BLK 101 SOMEWHERE"}
        onemap_data = {
            "99": rsl.OnemapFields(
                display_name=None, postal_code=None, latitude=1.3, longitude=103.8
            )
        }
        text = "export const SEED_CARPARKS: readonly SeedCarpark[] = [\n\n];"

        rendered = rsl.render_frontend_seed_carparks_ts(text, combined, onemap_data)

        assert 'displayName: "BLK 101 SOMEWHERE"' in rendered
        assert "postalCode" not in rendered
        # latitude/longitude ARE still written -- coordinates were saved even though
        # the building name itself was unresolvable (see onemap_enrich.py).
        assert "latitude: 1.3" in rendered

    def test_carpark_absent_from_onemap_data_entirely_still_renders_with_fallback(self) -> None:
        """A carpark never enriched at all (not in the dict) behaves identically to one
        enriched-but-unresolvable -- displayName falls back to name, no crash."""
        combined = {"1": "Suntec City"}
        text = "export const SEED_CARPARKS: readonly SeedCarpark[] = [\n\n];"

        rendered = rsl.render_frontend_seed_carparks_ts(text, combined, onemap_data={})

        assert 'displayName: "Suntec City"' in rendered
        assert "postalCode" not in rendered
        assert "latitude" not in rendered


class TestFetchOnemapEnrichment:
    """fetch_onemap_enrichment against a fake urllib transport (no real network)."""

    def test_parses_carparks_response_into_onemap_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import json as json_module
        import urllib.request

        captured_url: dict[str, str] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json_module.dumps(
                    [
                        {
                            "carpark_id": "1",
                            "latitude": 1.2935,
                            "longitude": 103.8572,
                            "onemap_building_name": "SUNTEC CITY MALL",
                            "onemap_postal_code": "039593",
                        },
                        {
                            "carpark_id": "99",
                            "latitude": None,
                            "longitude": None,
                            "onemap_building_name": None,
                            "onemap_postal_code": None,
                        },
                    ]
                ).encode("utf-8")

        def fake_urlopen(request: object, timeout: float = 15) -> FakeResponse:
            captured_url["url"] = request.full_url  # type: ignore[attr-defined]
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        result = rsl.fetch_onemap_enrichment("https://x.supabase.co", "key")

        assert "carparks?select=" in captured_url["url"]
        assert result["1"] == rsl.OnemapFields(
            display_name="SUNTEC CITY MALL", postal_code="039593",
            latitude=1.2935, longitude=103.8572,
        )
        assert result["99"] == rsl.OnemapFields(
            display_name=None, postal_code=None, latitude=None, longitude=None
        )


def test_regenerate_round_trips_an_alphanumeric_carpark_id(tmp_path: Path, workdir: Path) -> None:
    """Regression: end-to-end, an alphanumeric-ID candidate (e.g. "A0007") is written into
    poller/src/carparks.ts by one regenerate() call, then correctly RE-PARSED back out by
    parse_existing_carparks on a second call -- the exact round-trip that would have
    silently dropped or crashed on this entry before both _NAME_ENTRY_RE and the
    isalnum() validation were widened past digits-only."""
    import json

    poller_ts = workdir / "carparks.ts"
    frontend_ts = workdir / "seedCarparks.ts"

    coverage_map = tmp_path / "coverage_map.json"
    coverage_map.write_text(
        json.dumps(
            {"candidates": [{"carpark_id": "A0007", "name": "Angullia Park", "state": "verified"}]}
        ),
        encoding="utf-8",
    )
    rsl.regenerate(coverage_map, poller_ts, frontend_ts)

    existing_after_first_run = rsl.parse_existing_carparks(poller_ts.read_text(encoding="utf-8"))
    assert existing_after_first_run["A0007"] == "Angullia Park"

    # Second run with an EMPTY coverage map -- re-parses the now-alphanumeric-containing
    # file as the "existing" set and must reproduce it unchanged (true idempotency check).
    empty_coverage_map = tmp_path / "empty_coverage_map.json"
    empty_coverage_map.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    rsl.regenerate(empty_coverage_map, poller_ts, frontend_ts)

    existing_after_second_run = rsl.parse_existing_carparks(poller_ts.read_text(encoding="utf-8"))
    assert existing_after_second_run["A0007"] == "Angullia Park"
    assert existing_after_second_run == existing_after_first_run


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
    """A verified candidate that collides with an existing seed id under a DIFFERENT name
    aborts before writing (a same-name collision is a harmless no-op -- see
    test_build_combined_carparks_same_name_already_applied_is_a_noop)."""
    poller_ts = workdir / "carparks.ts"
    frontend_ts = workdir / "seedCarparks.ts"
    poller_before = poller_ts.read_text(encoding="utf-8")
    frontend_before = frontend_ts.read_text(encoding="utf-8")

    with pytest.raises(rsl.SeedListRegenError, match="DIFFERENT name"):
        rsl.regenerate(FIXTURES_DIR / "coverage_map_conflict.json", poller_ts, frontend_ts)

    assert poller_ts.read_text(encoding="utf-8") == poller_before
    assert frontend_ts.read_text(encoding="utf-8") == frontend_before


def test_ts_string_literal_is_ascii_double_quoted() -> None:
    """String literals render as double-quoted, ASCII-safe TS strings."""
    assert rsl.ts_string_literal("313@Somerset") == '"313@Somerset"'
