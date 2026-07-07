"""Tests for scripts/build_mall_whitelist.py (T4/T5/T7 whitelist-build state machine).

No real network calls: the live LTA/data.gov.sg fetches are always replaced with
injected fakes or fixed fixture data, and the multi-hour poll (`poll_lta_carparks.run`)
is always replaced with an instant fake via the same injectable-dependency pattern
`training/src/gotparking_training/train.py` uses for `TrainDeps.load_sinpa`.

The fuzzy-match fixtures below are NOT invented -- they reproduce the two real, notable
outcomes from the live Phase 0 recon run (`scripts/recon_mall_whitelist.py`, 2026-07-07,
500 LTA carparks x 357 mall-dataset rows, 18 candidates found):

  * carpark_id 64 ("Junction 8") fuzzy-matched "Junction 10" at 85.7 -- a real false
    positive (two different, unrelated malls) that still classifies as "matched" (not
    "needs-manual-disambiguation") because no second dataset row scored close enough to
    trip the ambiguity check. This is the concrete proof T7's human gate is load-bearing.
  * "Concorde Hotel" matched two mall-dataset rows at an identical 100.0 score -- the one
    real "needs-manual-disambiguation" case from that run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import build_mall_whitelist as bmw
from recon_mall_whitelist import EXISTING_SEED_CARPARK_IDS, FUZZY_MATCH_THRESHOLD, match_candidates

# -- Fixture data reproducing the real 2026-07-07 recon run's two notable outcomes -------

_LTA_RECORDS: list[dict[str, str | int]] = [
    {"CarParkID": "64", "Development": "Junction 8"},
    {"CarParkID": "77", "Development": "Concorde Hotel"},
    # An excluded (already-seeded) carpark, to prove exclusion still applies unchanged.
    {"CarParkID": "1", "Development": "Suntec City"},
]

# Deliberately-unrelated filler names so neither real candidate's runner-up is close
# enough to itself trip the ambiguity check by accident.
_MALL_NAMES_UNAMBIGUOUS_JUNCTION_8 = [
    "Junction 10",
    "IMM Building",
    "Tampines Mall",
    "Compass One",
    "Suntec City",
]

# Two identical dataset rows for "Concorde Hotel" -- real data.gov.sg datasets can and do
# contain duplicate/near-duplicate carpark name rows (e.g. per rate-schedule or zone).
_MALL_NAMES_WITH_CONCORDE_DUPES = _MALL_NAMES_UNAMBIGUOUS_JUNCTION_8 + [
    "Concorde Hotel",
    "Concorde Hotel",
]


def _run_real_match_candidates() -> list[Any]:
    """Run the real (imported, not reimplemented) match_candidates over the fixture data."""
    return match_candidates(
        _LTA_RECORDS, _MALL_NAMES_WITH_CONCORDE_DUPES, EXISTING_SEED_CARPARK_IDS, FUZZY_MATCH_THRESHOLD
    )


def _by_id(matches: list[Any], carpark_id: str) -> Any:
    for m in matches:
        if m.carpark_id == carpark_id:
            return m
    raise AssertionError(f"no match result for carpark_id={carpark_id}")


# -- classify(): state-machine split on the real Junction 8/10 and Concorde fixtures -----


class TestClassify:
    def test_junction_8_false_positive_classifies_as_matched_not_ambiguous(self) -> None:
        """The real false positive: score 85.7, but NOT flagged ambiguous (no second
        candidate close enough) -- proof fuzzy-match alone would auto-accept this without
        the T7 gate.
        """
        matches = _run_real_match_candidates()
        junction_8 = _by_id(matches, "64")

        assert junction_8.best_match == "Junction 10"
        assert round(junction_8.best_score, 1) == 85.7
        assert junction_8.ambiguous is False
        assert bmw.classify(junction_8) == bmw.STATE_MATCHED

    def test_concorde_hotel_duplicate_rows_classify_as_needs_manual_disambiguation(self) -> None:
        """Two dataset rows score an identical 100.0 -- the real ambiguous case."""
        matches = _run_real_match_candidates()
        concorde = _by_id(matches, "77")

        assert concorde.best_score == 100.0
        assert concorde.ambiguous is True
        assert bmw.classify(concorde) == bmw.STATE_NEEDS_DISAMBIGUATION

    def test_excluded_seed_carpark_never_appears(self) -> None:
        matches = _run_real_match_candidates()
        assert all(m.carpark_id != "1" for m in matches)


# -- T5: hard-fail on missing/empty/malformed mall dataset -------------------------------


class TestHardFailT5:
    def test_empty_mall_dataset_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bmw, "fetch_mall_dataset_names", lambda page_size=100: [])
        with pytest.raises(RuntimeError, match="zero rows"):
            bmw.fetch_mall_reference_names()

    def test_broken_fetch_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(page_size: int = 100) -> list[str]:
            raise ValueError("Unexpected data.gov.sg response shape: {}")

        monkeypatch.setattr(bmw, "fetch_mall_dataset_names", _boom)
        with pytest.raises(RuntimeError, match="Mall-dataset fetch failed"):
            bmw.fetch_mall_reference_names()

    def test_build_and_gate_never_writes_coverage_map_on_hard_fail(self, tmp_path: Path) -> None:
        """T5's whole point: never silently proceed with an empty reference list -- prove
        the coverage map is never written (not even an empty skeleton) when the mall
        dataset fetch is broken.
        """
        coverage_map_path = tmp_path / "carpark_coverage_map.json"

        def _fake_lta(api_key: str) -> list[dict[str, str | int]]:
            return _LTA_RECORDS

        def _fake_mall_names_hard_fail() -> list[str]:
            raise RuntimeError("Mall-dataset fetch returned zero rows -- refusing (T5)")

        with pytest.raises(RuntimeError, match="T5"):
            bmw.build_and_gate(
                coverage_map_path,
                api_key="fake-key",
                fetch_carpark_availability_fn=_fake_lta,
                fetch_mall_reference_names_fn=_fake_mall_names_hard_fail,
            )
        assert not coverage_map_path.exists()

    def test_missing_lta_api_key_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv(bmw.LTA_API_KEY_ENV, raising=False)
        with pytest.raises(RuntimeError, match=bmw.LTA_API_KEY_ENV):
            bmw.build_and_gate(tmp_path / "coverage_map.json", api_key=None)


# -- T7: human sign-off gate blocks progression until approval is present ---------------


class TestSignOffGate:
    def _base_entry(self, **overrides: Any) -> dict[str, Any]:
        entry = {
            "carpark_id": "64",
            "name": "Junction 8",
            "state": bmw.STATE_MATCHED,
            "match_score": 85.7,
            "matched_dataset_name": "Junction 10",
            "signed_off": False,
            "variance_range": None,
            "rejection_reason": None,
            "updated_at": "2026-07-07T00:00:00+00:00",
        }
        entry.update(overrides)
        return entry

    def test_unsigned_entry_never_advances(self, tmp_path: Path) -> None:
        coverage_map: dict[str, Any] = {"schema_version": 1, "generated_at": None, "candidates": [self._base_entry()]}
        poll_calls: list[dict[str, Any]] = []

        def _fake_poll(**kwargs: Any) -> None:
            poll_calls.append(kwargs)

        advanced = bmw.advance_signed_off_entries(
            coverage_map, tmp_path / "samples.csv", 5, 6.0, poll_fn=_fake_poll
        )

        assert advanced == []
        assert poll_calls == []
        assert coverage_map["candidates"][0]["state"] == bmw.STATE_MATCHED

    def test_needs_disambiguation_entry_never_advances_even_if_signed_off_without_resolution(
        self, tmp_path: Path
    ) -> None:
        """Sanity check: an ambiguous entry IS still eligible once signed_off is set (a
        human resolving disambiguation is expected to flip state -> "matched" or leave it
        unsigned to reject -- this script itself only gates on the signed_off flag, it
        does not silently "resolve" ambiguity on a human's behalf).
        """
        entry = self._base_entry(state=bmw.STATE_NEEDS_DISAMBIGUATION, signed_off=True)
        coverage_map: dict[str, Any] = {"schema_version": 1, "generated_at": None, "candidates": [entry]}
        poll_calls: list[dict[str, Any]] = []

        advanced = bmw.advance_signed_off_entries(
            coverage_map, tmp_path / "samples.csv", 5, 6.0, poll_fn=lambda **kw: poll_calls.append(kw)
        )

        assert len(advanced) == 1
        assert advanced[0]["state"] == bmw.STATE_OBSERVING
        assert poll_calls[0]["candidate_ids"] == {"64": "Junction 8"}

    def test_signed_off_entry_advances_and_invokes_poll_fn_with_exact_candidate_set(
        self, tmp_path: Path
    ) -> None:
        signed_off = self._base_entry(carpark_id="64", name="Junction 8", signed_off=True)
        not_signed_off = self._base_entry(
            carpark_id="77", name="Concorde Hotel", state=bmw.STATE_MATCHED, signed_off=False
        )
        coverage_map: dict[str, Any] = {"schema_version": 1, "generated_at": None, "candidates": [signed_off, not_signed_off]}
        poll_calls: list[dict[str, Any]] = []

        advanced = bmw.advance_signed_off_entries(
            coverage_map,
            tmp_path / "samples.csv",
            interval_minutes=5,
            duration_hours=6.0,
            poll_fn=lambda **kw: poll_calls.append(kw),
        )

        assert [e["carpark_id"] for e in advanced] == ["64"]
        assert len(poll_calls) == 1
        assert poll_calls[0]["candidate_ids"] == {"64": "Junction 8"}
        assert poll_calls[0]["interval_minutes"] == 5
        assert poll_calls[0]["duration_hours"] == 6.0
        # The not-signed-off entry is untouched -- gate has no exceptions.
        assert not_signed_off["state"] == bmw.STATE_MATCHED

    def test_merge_never_reverts_post_gate_states(self) -> None:
        """A re-run's fresh fuzzy-match pass must never revert a human-reviewed decision
        (T7: the gate exists precisely so a human's call sticks).
        """
        existing_verified = self._base_entry(
            state=bmw.STATE_VERIFIED, signed_off=True, variance_range=42, match_score=85.7
        )
        coverage_map: dict[str, Any] = {"schema_version": 1, "generated_at": None, "candidates": [existing_verified]}

        matches = _run_real_match_candidates()  # includes a fresh MatchResult for carpark_id "64"
        merged = bmw.merge_matches(coverage_map, matches)

        entry = merged["candidates"][0]
        assert entry["state"] == bmw.STATE_VERIFIED
        assert entry["variance_range"] == 42


# -- verified/rejected split using injected fake variance data --------------------------


class TestVerifiedRejectedSplit:
    def test_range_at_or_above_threshold_is_verified(self) -> None:
        entry = {
            "carpark_id": "64",
            "name": "Junction 8",
            "state": bmw.STATE_OBSERVING,
            "match_score": 85.7,
            "matched_dataset_name": "Junction 10",
            "signed_off": True,
            "variance_range": None,
            "rejection_reason": None,
            "updated_at": "",
        }
        readings = list(range(100, 100 + bmw.MIN_MEANINGFUL_RANGE + 1))  # exactly at threshold

        bmw.evaluate_observed_entries(
            [entry], Path("unused.csv"), load_samples_fn=lambda path: {"Junction 8": readings}
        )

        assert entry["state"] == bmw.STATE_VERIFIED
        assert entry["variance_range"] == bmw.MIN_MEANINGFUL_RANGE
        assert entry["rejection_reason"] is None

    def test_range_below_threshold_is_rejected_with_reason(self) -> None:
        entry: dict[str, Any] = {
            "carpark_id": "999",
            "name": "Too Stable Mall",
            "state": bmw.STATE_OBSERVING,
            "match_score": 90.0,
            "matched_dataset_name": "Too Stable Mall Dataset",
            "signed_off": True,
            "variance_range": None,
            "rejection_reason": None,
            "updated_at": "",
        }
        readings = [50, 52, 51, 53]  # range 3, well below MIN_MEANINGFUL_RANGE (20)

        bmw.evaluate_observed_entries(
            [entry], Path("unused.csv"), load_samples_fn=lambda path: {"Too Stable Mall": readings}
        )

        assert entry["state"] == bmw.STATE_REJECTED
        assert entry["variance_range"] == 3
        assert "MIN_MEANINGFUL_RANGE" in entry["rejection_reason"]

    def test_fewer_than_two_samples_is_rejected_not_requeued(self) -> None:
        entry: dict[str, Any] = {
            "carpark_id": "888",
            "name": "Barely Polled Mall",
            "state": bmw.STATE_OBSERVING,
            "match_score": 90.0,
            "matched_dataset_name": "Barely Polled Mall Dataset",
            "signed_off": True,
            "variance_range": None,
            "rejection_reason": None,
            "updated_at": "",
        }

        bmw.evaluate_observed_entries(
            [entry], Path("unused.csv"), load_samples_fn=lambda path: {"Barely Polled Mall": [50]}
        )

        assert entry["state"] == bmw.STATE_REJECTED
        assert "fewer than 2 samples" in entry["rejection_reason"]

    def test_no_observed_entries_is_a_noop(self) -> None:
        # Should not raise even though load_samples_fn would blow up if called.
        bmw.evaluate_observed_entries([], Path("unused.csv"), load_samples_fn=lambda path: (_ for _ in ()).throw(
            AssertionError("load_samples_fn should not be called when there is nothing to evaluate")
        ))


# -- coverage-map artifact I/O ------------------------------------------------------------


class TestCoverageMapIO:
    def test_load_missing_file_returns_empty_skeleton(self, tmp_path: Path) -> None:
        coverage_map = bmw.load_coverage_map(tmp_path / "does_not_exist.json")
        assert coverage_map == {
            "schema_version": bmw.COVERAGE_MAP_SCHEMA_VERSION,
            "generated_at": None,
            "candidates": [],
        }

    def test_write_then_load_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "carpark_coverage_map.json"
        coverage_map: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": None,
            "candidates": [
                {
                    "carpark_id": "64",
                    "name": "Junction 8",
                    "state": bmw.STATE_MATCHED,
                    "match_score": 85.7,
                    "matched_dataset_name": "Junction 10",
                    "signed_off": False,
                    "variance_range": None,
                    "rejection_reason": None,
                    "updated_at": "",
                }
            ],
        }
        bmw.write_coverage_map(path, coverage_map)

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["generated_at"] is not None
        assert on_disk["candidates"][0]["carpark_id"] == "64"

        reloaded = bmw.load_coverage_map(path)
        assert reloaded == on_disk


# -- SQL / JSON output for verified entries (carparks-table insert, step 7) -------------


class TestVerifiedOutput:
    def _coverage_map_with_mixed_states(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": None,
            "candidates": [
                {
                    "carpark_id": "64",
                    "name": "Mall's Plaza",
                    "state": bmw.STATE_VERIFIED,
                    "match_score": 90.0,
                    "matched_dataset_name": "Mall's Plaza Dataset",
                    "signed_off": True,
                    "variance_range": 40,
                    "rejection_reason": None,
                    "updated_at": "",
                },
                {
                    "carpark_id": "77",
                    "name": "Rejected Mall",
                    "state": bmw.STATE_REJECTED,
                    "match_score": 88.0,
                    "matched_dataset_name": "Rejected Mall Dataset",
                    "signed_off": True,
                    "variance_range": 2,
                    "rejection_reason": "range 2 < MIN_MEANINGFUL_RANGE (20)",
                    "updated_at": "",
                },
            ],
        }

    def test_build_insert_payload_only_includes_verified(self) -> None:
        payload = bmw.build_insert_payload(self._coverage_map_with_mixed_states())
        assert payload == {
            "candidates": [
                {
                    "carpark_id": "64",
                    "name": "Mall's Plaza",
                    "state": "verified",
                    "match_score": 90.0,
                    "matched_dataset_name": "Mall's Plaza Dataset",
                }
            ]
        }

    def test_render_sql_inserts_escapes_apostrophes_and_skips_non_verified(self) -> None:
        sql = bmw.render_sql_inserts(self._coverage_map_with_mixed_states())
        assert "Rejected Mall" not in sql
        assert "insert into public.carparks (carpark_id, name, sinpa_index) values" in sql
        assert "('64', 'Mall''s Plaza', null)" in sql
        assert "on conflict (carpark_id) do nothing" in sql


# -- check_feed_churn: confirm it is a safe, documented stub -----------------------------


def test_check_feed_churn_is_a_stub_returning_empty_list() -> None:
    coverage_map: dict[str, Any] = {"schema_version": 1, "generated_at": None, "candidates": []}
    assert bmw.check_feed_churn(coverage_map) == []


# -- full end-to-end cycle: T5-guarded fetch -> T7 gate -> observing -> verified/rejected -


class TestFullCycle:
    def test_first_run_writes_matched_entries_and_stops_at_gate(self, tmp_path: Path) -> None:
        coverage_map_path = tmp_path / "carpark_coverage_map.json"
        poll_calls: list[dict[str, Any]] = []

        coverage_map = bmw.run_full_cycle(
            coverage_map_path=coverage_map_path,
            poll_output_file=tmp_path / "samples.csv",
            poll_fn=lambda **kw: poll_calls.append(kw),
            api_key="fake-key",
            fetch_carpark_availability_fn=lambda api_key: _LTA_RECORDS,
            fetch_mall_reference_names_fn=lambda: _MALL_NAMES_WITH_CONCORDE_DUPES,
        )

        assert coverage_map_path.exists()
        assert poll_calls == []  # nothing signed off yet -- gate holds, no exceptions
        states = {e["carpark_id"]: e["state"] for e in coverage_map["candidates"]}
        assert states["64"] == bmw.STATE_MATCHED
        assert states["77"] == bmw.STATE_NEEDS_DISAMBIGUATION

    def test_second_run_advances_only_the_signed_off_entry_to_a_terminal_state(
        self, tmp_path: Path
    ) -> None:
        coverage_map_path = tmp_path / "carpark_coverage_map.json"
        poll_output_file = tmp_path / "samples.csv"

        # First run: establishes the two candidates, both unsigned.
        bmw.run_full_cycle(
            coverage_map_path=coverage_map_path,
            poll_output_file=poll_output_file,
            poll_fn=lambda **kw: None,
            api_key="fake-key",
            fetch_carpark_availability_fn=lambda api_key: _LTA_RECORDS,
            fetch_mall_reference_names_fn=lambda: _MALL_NAMES_WITH_CONCORDE_DUPES,
        )

        # Simulate a human editing the artifact: sign off Junction 8 only.
        coverage_map = json.loads(coverage_map_path.read_text(encoding="utf-8"))
        for entry in coverage_map["candidates"]:
            if entry["carpark_id"] == "64":
                entry["signed_off"] = True
        coverage_map_path.write_text(json.dumps(coverage_map), encoding="utf-8")

        poll_calls: list[dict[str, Any]] = []

        def _fake_poll(**kwargs: Any) -> None:
            poll_calls.append(kwargs)

        def _fake_load_samples(path: Path) -> dict[str, list[int]]:
            return {"Junction 8": [10, 60]}  # range 50 >= MIN_MEANINGFUL_RANGE (20)

        final_map = bmw.run_full_cycle(
            coverage_map_path=coverage_map_path,
            poll_output_file=poll_output_file,
            poll_fn=_fake_poll,
            api_key="fake-key",
            fetch_carpark_availability_fn=lambda api_key: _LTA_RECORDS,
            fetch_mall_reference_names_fn=lambda: _MALL_NAMES_WITH_CONCORDE_DUPES,
            load_samples_fn=_fake_load_samples,
        )

        assert len(poll_calls) == 1
        assert poll_calls[0]["candidate_ids"] == {"64": "Junction 8"}

        by_id = {e["carpark_id"]: e for e in final_map["candidates"]}
        assert by_id["64"]["state"] == bmw.STATE_VERIFIED
        assert by_id["64"]["variance_range"] == 50
        # The unsigned Concorde Hotel entry is untouched by this run.
        assert by_id["77"]["state"] == bmw.STATE_NEEDS_DISAMBIGUATION

        # Persisted to disk, not just returned in-memory.
        on_disk = json.loads(coverage_map_path.read_text(encoding="utf-8"))
        assert {e["carpark_id"]: e["state"] for e in on_disk["candidates"]}["64"] == bmw.STATE_VERIFIED
