"""Build the mall-carpark whitelist: fuzzy-match, human sign-off, observe, verify.

Full Phase A state-machine script for the carpark coverage-expansion plan (see
`~/.gstack/projects/gstack-playground/ceo-plans/2026-07-07-carpark-coverage-expansion.md` and
TODOS.md's "Expand to all/hundreds of SG carparks" entry). Builds on
`scripts/recon_mall_whitelist.py`'s Phase 0 recon (fetch LTA feed, exclude the 10 existing
seeds, fuzzy-match every remaining `Development` name against data.gov.sg's "Carpark Rates"
dataset at `rapidfuzz.fuzz.token_sort_ratio >= 85`) by adding the rest of the pipeline:

State machine (one node per coverage-map entry, `data/carpark_coverage_map.json`)::

    matched -----------------------\\
                                     >-- [T7 human sign-off gate] --> observing --> verified
    needs-manual-disambiguation ---/                                           \\-> rejected

  * matched / needs-manual-disambiguation: fresh fuzzy-match outcome (T5: the mall-dataset
    fetch is hard-failed, never silently empty -- see `fetch_mall_reference_names`).
    "needs-manual-disambiguation" is set when the top-2 candidate mall-dataset scores are
    within 1.0 of each other -- reusing `recon_mall_whitelist.MatchResult.ambiguous` verbatim,
    not reimplemented here.
  * T7 human sign-off gate (mandatory, every run, no exceptions -- there is no CLI flag to
    bypass it): after fuzzy-matching, this script writes the full matched list to the
    coverage-map artifact and STOPS. It does NOT insert into Supabase and does NOT start
    observation for anything a human hasn't explicitly approved.
    SIGN-OFF MECHANISM: each entry in `data/carpark_coverage_map.json` has a `"signed_off"`
    boolean field, defaulting to `false`. To approve an entry sitting in "matched" or
    "needs-manual-disambiguation", a human edits that file directly and sets
    `"signed_off": true` on the entries they approve (for "needs-manual-disambiguation"
    entries, first resolve the ambiguity -- correct `"matched_dataset_name"` if the
    fuzzy-match's top pick was wrong, and change `"state"` to `"matched"`, or leave it
    rejected by simply not signing off). The score alone is NOT sufficient grounds for
    auto-accept: the real Phase 0 recon run caught carpark_id 64 ("Junction 8") fuzzy-matching
    "Junction 10" at 85.7 -- two different, unrelated malls, above the 85 threshold, with no
    second candidate close enough to trip the ambiguity check. Variance validation does not
    catch this either (most active carparks fluctuate, wrong building or not) -- a human
    reading the name pair is the only check that closes this gap. The NEXT invocation of this
    script reads `signed_off` from the artifact and only then advances approved entries.
  * observing: for entries a human has signed off on, this script invokes
    `scripts/poll_lta_carparks.py`'s existing `run()` directly (reused, not reimplemented) with
    `candidate_ids` restricted to exactly the signed-off set, for a configurable
    `--duration-hours` window (default matches `poll_lta_carparks.py`'s own default: 5-minute
    interval, 6-hour duration). Since a real multi-hour run isn't practical during a build/test
    session, the poll call is injectable (`run_full_cycle`'s/`advance_signed_off_entries`'s
    `poll_fn` parameter, same injectable-callable pattern as
    `training/src/gotparking_training/train.py`'s `TrainDeps.load_sinpa`) -- tests inject an
    instant fake instead of actually sleeping for hours.
  * verified / rejected: once an observation window's samples are collected, this script
    reuses `scripts/analyze_variance.py`'s `MIN_MEANINGFUL_RANGE` constant unchanged (imported,
    not duplicated) to decide: range >= threshold -> "verified"; otherwise -> "rejected", with
    a `"rejection_reason"` logged. Rejected entries are NOT auto-requeued.

Coverage-map artifact schema (`data/carpark_coverage_map.json`), updated at every state
transition::

    {
      "schema_version": 1,
      "generated_at": "<UTC ISO-8601>",
      "candidates": [
        {
          "carpark_id": "64",
          "name": "Junction 8",
          "state": "matched" | "needs-manual-disambiguation" | "observing" | "verified" | "rejected",
          "match_score": 85.7,
          "matched_dataset_name": "Junction 10",
          "signed_off": false,
          "variance_range": null,
          "rejection_reason": null,
          "updated_at": "<UTC ISO-8601>"
        }
      ]
    }

`carparks` table insert (T7 item 7): this script does NOT write to Supabase directly (no live
DB credentials assumed available in this build/test session). For every "verified" entry it
instead prints (a) literal SQL `INSERT INTO public.carparks` statements matching
`db/schema.sql`'s table shape, and (b) a structured JSON payload the parallel poller-regen lane
can consume, using the SAME per-entry shape as the coverage-map artifact:
`{"candidates": [{"carpark_id": str, "name": str, "state": str, "match_score": float,
"matched_dataset_name": str | None}, ...]}` (state is always "verified" in this payload). A
human/CI step applies the SQL once ready -- see `render_sql_inserts` / `build_insert_payload`.

Feed-churn check (`check_feed_churn`): documented STUB ONLY. See its docstring -- it depends on
a live Supabase connection this script does not have in this build/test session.

Usage:
    uv run scripts/build_mall_whitelist.py
    uv run scripts/build_mall_whitelist.py --duration-hours 6 --interval-minutes 5
    uv run scripts/build_mall_whitelist.py --coverage-map data/carpark_coverage_map.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_variance import MIN_MEANINGFUL_RANGE, load_samples  # noqa: E402 (reuse, DRY)
from poll_lta_carparks import OUTPUT_FILE as POLL_OUTPUT_FILE  # noqa: E402
from poll_lta_carparks import run as poll_run  # noqa: E402
from recon_mall_whitelist import EXISTING_SEED_CARPARK_IDS  # noqa: E402
from recon_mall_whitelist import FUZZY_MATCH_THRESHOLD  # noqa: E402
from recon_mall_whitelist import LTA_API_KEY_ENV  # noqa: E402
from recon_mall_whitelist import MatchResult  # noqa: E402
from recon_mall_whitelist import fetch_carpark_availability  # noqa: E402
from recon_mall_whitelist import fetch_mall_dataset_names  # noqa: E402
from recon_mall_whitelist import match_candidates  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COVERAGE_MAP_FILE = DATA_DIR / "carpark_coverage_map.json"
COVERAGE_MAP_SCHEMA_VERSION = 1

# poll_lta_carparks.py's own CLI defaults (module docstring's "default matching
# poll_lta_carparks.py's own default" requirement).
DEFAULT_INTERVAL_MINUTES = 5
DEFAULT_DURATION_HOURS = 6.0

# State-machine nodes -- see module docstring's ASCII diagram.
STATE_MATCHED = "matched"
STATE_NEEDS_DISAMBIGUATION = "needs-manual-disambiguation"
STATE_OBSERVING = "observing"
STATE_VERIFIED = "verified"
STATE_REJECTED = "rejected"

# States a human sign-off can promote out of. Anything else (observing,
# verified, rejected) is a post-gate/terminal state a re-run must never touch.
_PRE_GATE_STATES = (STATE_MATCHED, STATE_NEEDS_DISAMBIGUATION)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class CoverageEntry:
    """One candidate carpark's row in the coverage-map artifact.

    Attributes:
        carpark_id: LTA CarParkID (string; matches `carparks.carpark_id`'s type).
        name: LTA `Development` name -- becomes `carparks.name` once inserted.
        state: One of STATE_MATCHED, STATE_NEEDS_DISAMBIGUATION, STATE_OBSERVING,
            STATE_VERIFIED, STATE_REJECTED.
        match_score: Best rapidfuzz token_sort_ratio score (0-100) against the mall
            dataset at match time. None only if never matched (not expected in practice,
            since entries are only created from a positive match).
        matched_dataset_name: The mall-dataset "carpark" name that scored highest, kept
            for auditability by a human reviewer -- never itself written to `carparks`.
        signed_off: Human sign-off flag (T7). The ONLY field a human is expected to hand-edit
            in this file: set true to approve an entry sitting in a pre-gate state to advance
            to "observing" on the next run. Defaults to false.
        variance_range: (max - min) available-lots range observed during "observing",
            once evaluated. None before evaluation.
        rejection_reason: Human/automation-readable reason an entry was rejected. None
            unless state == STATE_REJECTED.
        updated_at: UTC ISO-8601 timestamp of this entry's last state transition.
    """

    carpark_id: str
    name: str
    state: str
    match_score: float | None
    matched_dataset_name: str | None
    signed_off: bool = False
    variance_range: float | None = None
    rejection_reason: str | None = None
    updated_at: str = ""


def classify(match: MatchResult) -> str:
    """Map a fuzzy-match result to its initial state-machine node.

    Args:
        match: One LTA carpark's fuzzy-match outcome against the mall dataset (from
            `recon_mall_whitelist.match_candidates`).

    Returns:
        STATE_NEEDS_DISAMBIGUATION if the top-2 candidate mall-dataset scores were within
        1.0 of each other (`match.ambiguous`, computed by `match_candidates` -- reused
        verbatim, not reimplemented here); otherwise STATE_MATCHED.
    """
    return STATE_NEEDS_DISAMBIGUATION if match.ambiguous else STATE_MATCHED


def fetch_mall_reference_names() -> list[str]:
    """Fetch the data.gov.sg mall-dataset names, hard-failing if empty/broken (T5).

    Never silently proceeds with an empty reference list: that would make every LTA
    carpark look "unmatched" in the coverage map, indistinguishable from a genuine
    "no new candidates this run" outcome, and could mask a broken fetch entirely.

    Returns:
        A non-empty list of mall-dataset "carpark" names.

    Raises:
        RuntimeError: If the fetch raised (HTTP error / malformed response shape) or
            returned zero rows.
    """
    try:
        names = fetch_mall_dataset_names()
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        raise RuntimeError(
            f"Mall-dataset fetch failed ({exc!r}) -- refusing to proceed with an empty "
            "reference list (T5). This would make every LTA carpark look 'unmatched' "
            "rather than surfacing the real fetch failure. Fix the data.gov.sg fetch "
            "and re-run."
        ) from exc
    if not names:
        raise RuntimeError(
            "Mall-dataset fetch returned zero rows -- refusing to proceed with an empty "
            "reference list (T5). This would make every LTA carpark look 'unmatched', "
            "indistinguishable from a real 'no new candidates' run rather than a broken "
            "fetch. Fix the data.gov.sg fetch and re-run."
        )
    return names


def load_coverage_map(path: Path) -> dict[str, Any]:
    """Load the coverage-map artifact, or return an empty skeleton if absent.

    Args:
        path: Path to `data/carpark_coverage_map.json`.

    Returns:
        Parsed JSON dict with "schema_version", "generated_at", "candidates" keys.
    """
    if not path.exists():
        return {"schema_version": COVERAGE_MAP_SCHEMA_VERSION, "generated_at": None, "candidates": []}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_coverage_map(path: Path, coverage_map: dict[str, Any]) -> None:
    """Write the coverage-map artifact to disk, stamping `generated_at`.

    Args:
        path: Path to `data/carpark_coverage_map.json`.
        coverage_map: Full coverage-map dict to serialize (mutated in place with a fresh
            `generated_at` timestamp).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage_map["generated_at"] = datetime.now(timezone.utc).isoformat()
    with path.open("w", encoding="utf-8") as f:
        json.dump(coverage_map, f, indent=2)
        f.write("\n")


def merge_matches(coverage_map: dict[str, Any], matches: list[MatchResult]) -> dict[str, Any]:
    """Merge a fresh fuzzy-match pass into an existing coverage map.

    Entries already past the T7 gate (STATE_OBSERVING, STATE_VERIFIED, STATE_REJECTED) are
    left completely untouched -- a re-run's fresh fuzzy-match pass must never revert a
    human-reviewed decision. Entries still sitting in a pre-gate state are refreshed with
    the latest score/state (a re-run against the live feed can change scores) while
    preserving any `signed_off` flag a human may have already set ahead of the next run
    actually advancing them. New carpark_ids are appended as new pre-gate entries.

    Args:
        coverage_map: Existing coverage-map dict (as returned by `load_coverage_map`).
        matches: Fresh `match_candidates()` output from this run.

    Returns:
        The same coverage-map dict, with `candidates` replaced by the merged list.
    """
    by_id: dict[str, dict[str, Any]] = {e["carpark_id"]: e for e in coverage_map.get("candidates", [])}
    now = datetime.now(timezone.utc).isoformat()

    for match in matches:
        existing = by_id.get(match.carpark_id)
        if existing is not None and existing["state"] not in _PRE_GATE_STATES:
            continue  # post-gate/terminal -- never reverted by a re-run

        entry = CoverageEntry(
            carpark_id=match.carpark_id,
            name=match.development,
            state=classify(match),
            match_score=match.best_score,
            matched_dataset_name=match.best_match,
            signed_off=bool(existing["signed_off"]) if existing is not None else False,
            variance_range=None,
            rejection_reason=None,
            updated_at=now,
        )
        by_id[match.carpark_id] = asdict(entry)

    coverage_map["candidates"] = list(by_id.values())
    return coverage_map


def print_gate_instructions(coverage_map: dict[str, Any], coverage_map_path: Path) -> None:
    """Print the mandatory T7 human sign-off instructions.

    Always called after merging fresh matches, regardless of whether any entry is
    currently awaiting sign-off -- T7 is a standing checkpoint printed every run, not a
    one-time prompt shown only when something happens to be pending.

    Args:
        coverage_map: Coverage-map dict (already written to disk by the caller).
        coverage_map_path: Path it was written to.
    """
    pending = [
        e for e in coverage_map["candidates"] if e["state"] in _PRE_GATE_STATES and not e["signed_off"]
    ]
    logger.info("=" * 88)
    logger.info("HUMAN SIGN-OFF GATE (T7) -- this script will NOT proceed automatically.")
    logger.info("Coverage map written to: %s", coverage_map_path)
    if pending:
        logger.info("%d entr%s awaiting sign-off:", len(pending), "y is" if len(pending) == 1 else "ies are")
        for e in sorted(pending, key=lambda e: e["carpark_id"]):
            logger.info(
                "  carpark_id=%-6s state=%-28s score=%5.1f  %-28s -> %s",
                e["carpark_id"],
                e["state"],
                e["match_score"] or 0.0,
                e["name"],
                e["matched_dataset_name"],
            )
        logger.info(
            "To approve an entry: edit %s directly and set that entry's \"signed_off\" "
            "field to true. For \"needs-manual-disambiguation\" entries, first resolve "
            "the ambiguity by hand (correct \"matched_dataset_name\" if the fuzzy-match's "
            "top pick was wrong, and change \"state\" to \"matched\") before signing off. "
            "Re-run this script afterwards -- only entries with signed_off == true advance "
            "to \"observing\". Score alone is NOT sufficient grounds for auto-accept: the "
            "real recon run matched carpark_id 64 (\"Junction 8\") to \"Junction 10\" at "
            "85.7 -- two different malls -- with no second candidate close enough to trip "
            "ambiguity detection. This gate runs every invocation; there is no bypass flag.",
            coverage_map_path,
        )
    else:
        logger.info(
            "No entries are currently awaiting sign-off in a pre-gate state "
            "(\"matched\"/\"needs-manual-disambiguation\")."
        )
    logger.info("=" * 88)


def advance_signed_off_entries(
    coverage_map: dict[str, Any],
    poll_output_file: Path,
    interval_minutes: int,
    duration_hours: float,
    poll_fn: Callable[..., None] = poll_run,
) -> list[dict[str, Any]]:
    """Advance every signed-off pre-gate entry to "observing" and run its observation window.

    Only entries with `signed_off == True` AND `state` in a pre-gate state are eligible --
    this is the ONLY path past the T7 human sign-off gate. Entries without `signed_off ==
    True` are left exactly as they are.

    Args:
        coverage_map: Coverage-map dict (mutated in place -- eligible entries' state is
            flipped to STATE_OBSERVING *before* polling starts, so a crash mid-poll still
            leaves an accurate on-disk record of what was being observed).
        poll_output_file: CSV file the observation poll appends samples to.
        interval_minutes: Minutes between polls, forwarded to `poll_fn`.
        duration_hours: Total observation window length in hours, forwarded to `poll_fn`.
        poll_fn: Injectable replacement for `poll_lta_carparks.run` (same injectable-callable
            pattern as `training/src/gotparking_training/train.py`'s `TrainDeps.load_sinpa`)
            -- defaults to the real, long-running poller. Tests inject a fake that returns
            instantly instead of actually sleeping for hours.

    Returns:
        The list of entries (as dicts, same objects mutated into `coverage_map`) that were
        advanced to "observing" this call. Empty if nothing was eligible.
    """
    eligible = [
        e for e in coverage_map["candidates"] if e.get("signed_off") and e["state"] in _PRE_GATE_STATES
    ]
    if not eligible:
        return []

    now = datetime.now(timezone.utc).isoformat()
    for entry in eligible:
        entry["state"] = STATE_OBSERVING
        entry["updated_at"] = now

    candidate_ids = {e["carpark_id"]: e["name"] for e in eligible}
    poll_fn(
        interval_minutes=interval_minutes,
        duration_hours=duration_hours,
        output_file=poll_output_file,
        candidate_ids=candidate_ids,
    )
    return eligible


def evaluate_observed_entries(
    observed_entries: list[dict[str, Any]],
    poll_output_file: Path,
    load_samples_fn: Callable[[Path], dict[str, list[int]]] = load_samples,
) -> None:
    """Classify just-observed entries as "verified" or "rejected".

    Reuses `analyze_variance.MIN_MEANINGFUL_RANGE` and its (max - min) range logic
    unchanged -- imported, not duplicated.

    Args:
        observed_entries: Entries just advanced through "observing" by
            `advance_signed_off_entries` (mutated in place to their terminal state).
        poll_output_file: CSV the observation window wrote samples to.
        load_samples_fn: Injectable replacement for `analyze_variance.load_samples`, so
            tests can supply in-memory readings without writing a real CSV. Defaults to
            the real function.
    """
    if not observed_entries:
        return

    by_development = load_samples_fn(poll_output_file)
    now = datetime.now(timezone.utc).isoformat()

    for entry in observed_entries:
        readings = by_development.get(entry["name"], [])
        if len(readings) < 2:
            entry["state"] = STATE_REJECTED
            entry["rejection_reason"] = (
                f"fewer than 2 samples collected ({len(readings)}) during the observation "
                "window -- not auto-requeued"
            )
            entry["updated_at"] = now
            continue

        rng = max(readings) - min(readings)
        entry["variance_range"] = rng
        if rng >= MIN_MEANINGFUL_RANGE:
            entry["state"] = STATE_VERIFIED
            entry["rejection_reason"] = None
        else:
            entry["state"] = STATE_REJECTED
            entry["rejection_reason"] = f"range {rng} < MIN_MEANINGFUL_RANGE ({MIN_MEANINGFUL_RANGE})"
        entry["updated_at"] = now


def check_feed_churn(coverage_map: dict[str, Any]) -> list[str]:
    """STUB: detect previously-"verified" carparks whose live feed has gone stale.

    NOT IMPLEMENTED. This script has no live Supabase connection in this build/test
    session (no DB credentials assumed available), so this is a documented stub rather
    than a wired implementation. The intended logic, once a real Supabase client is
    available:

      1. For every coverage-map entry with `state == "verified"`, look up its row in
         `public.carparks` (should have `active = true` once the SQL/JSON insert output
         below has actually been applied by a human/CI step).
      2. Query `public.carpark_history` for that `carpark_id`'s most recent `polled_at`.
      3. If `active = true` and no `carpark_history` row exists within roughly the last
         24-48 hours, flag the carpark_id as "churned" -- the mall likely closed, was
         renamed in a way that broke the poller's static config, or LTA stopped reporting
         it -- and surface it for human review rather than silently leaving a dead entry
         in the poller's static list.

    Args:
        coverage_map: Coverage-map dict, read-only here.

    Returns:
        Always an empty list in this stub. A real implementation would return the
        carpark_ids flagged as churned.
    """
    logger.info(
        "check_feed_churn() is a documented stub (no live Supabase connection in this "
        "build/test session) -- see its docstring for the intended query logic."
    )
    return []


def build_insert_payload(coverage_map: dict[str, Any]) -> dict[str, Any]:
    """Build the structured JSON payload for verified entries.

    Schema matches the coverage-map artifact's own per-entry shape, per the coordination
    contract with the parallel poller-regen lane: a top-level dict with a single
    "candidates" array, each entry `{"carpark_id": str, "name": str, "state": str,
    "match_score": float, "matched_dataset_name": str | None}` (state is always
    STATE_VERIFIED in this payload).

    Args:
        coverage_map: Coverage-map dict.

    Returns:
        JSON-serializable dict, ready for `json.dumps`. `candidates` is empty if there are
        no verified entries yet.
    """
    return {
        "candidates": [
            {
                "carpark_id": e["carpark_id"],
                "name": e["name"],
                "state": e["state"],
                "match_score": e["match_score"],
                "matched_dataset_name": e["matched_dataset_name"],
            }
            for e in coverage_map["candidates"]
            if e["state"] == STATE_VERIFIED
        ]
    }


def render_sql_inserts(coverage_map: dict[str, Any]) -> str:
    """Render `INSERT INTO public.carparks` statements for every verified entry.

    Matches `db/schema.sql`'s `carparks` table exactly (`carpark_id`, `name`,
    `sinpa_index`; `active`/`created_at` take their column defaults). `sinpa_index` is
    always NULL here -- newly-discovered mall carparks have no SINPA pretraining mapping
    (that was a one-time, T0-era exact-coordinate match against only the original 10; see
    `db/schema.sql`'s comment on the `carparks` table). Idempotent
    (`on conflict (carpark_id) do nothing`), matching every other insert in that file.

    Args:
        coverage_map: Coverage-map dict.

    Returns:
        SQL text, one statement per verified entry, newline-separated (empty string if
        there are no verified entries yet). NOT executed by this script -- no live DB
        credentials are assumed available in this build/test session; a human/CI step
        applies it once ready.
    """
    lines: list[str] = []
    for e in coverage_map["candidates"]:
        if e["state"] != STATE_VERIFIED:
            continue
        safe_name = e["name"].replace("'", "''")
        lines.append(
            "insert into public.carparks (carpark_id, name, sinpa_index) values "
            f"('{e['carpark_id']}', '{safe_name}', null) on conflict (carpark_id) do nothing;"
        )
    return "\n".join(lines)


def build_and_gate(
    coverage_map_path: Path,
    api_key: str | None = None,
    fetch_carpark_availability_fn: Callable[[str], list[dict[str, str | int]]] = fetch_carpark_availability,
    fetch_mall_reference_names_fn: Callable[[], list[str]] = fetch_mall_reference_names,
) -> dict[str, Any]:
    """Fetch, fuzzy-match, merge into the coverage map, write it, and stop at the T7 gate.

    This function never advances anything to "observing" -- that only happens in a later,
    separate call to `advance_signed_off_entries` (see `run_full_cycle`), reading
    `signed_off` flags a human set after reviewing THIS run's output.

    Args:
        coverage_map_path: Path to the coverage-map artifact.
        api_key: LTA DataMall AccountKey. Defaults to the `LTA_API_KEY` env var.
        fetch_carpark_availability_fn: Injectable replacement for
            `recon_mall_whitelist.fetch_carpark_availability` (tests avoid real network
            calls). Defaults to the real function.
        fetch_mall_reference_names_fn: Injectable replacement for this module's
            `fetch_mall_reference_names` (T5 hard-fail wrapper). Defaults to the real
            function.

    Returns:
        The updated coverage-map dict (already written to `coverage_map_path`).

    Raises:
        RuntimeError: If `LTA_API_KEY` is unset, or the mall-dataset fetch is empty/broken
            (T5, raised by `fetch_mall_reference_names_fn`).
    """
    key = api_key or os.environ.get(LTA_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"{LTA_API_KEY_ENV} not set -- export it or load it from .env first")

    logger.info("Fetching live LTA feed...")
    lta_records = fetch_carpark_availability_fn(key)
    logger.info("LTA feed returned %d total carparks", len(lta_records))

    logger.info("Fetching data.gov.sg mall dataset...")
    mall_names = fetch_mall_reference_names_fn()
    logger.info("Mall dataset returned %d reference names", len(mall_names))

    matches = match_candidates(lta_records, mall_names, EXISTING_SEED_CARPARK_IDS, FUZZY_MATCH_THRESHOLD)
    logger.info("Fuzzy-match found %d candidates (threshold >= %d)", len(matches), FUZZY_MATCH_THRESHOLD)

    coverage_map = load_coverage_map(coverage_map_path)
    coverage_map = merge_matches(coverage_map, matches)
    write_coverage_map(coverage_map_path, coverage_map)
    print_gate_instructions(coverage_map, coverage_map_path)
    return coverage_map


def run_full_cycle(
    coverage_map_path: Path = COVERAGE_MAP_FILE,
    poll_output_file: Path = POLL_OUTPUT_FILE,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    duration_hours: float = DEFAULT_DURATION_HOURS,
    poll_fn: Callable[..., None] = poll_run,
    api_key: str | None = None,
    fetch_carpark_availability_fn: Callable[[str], list[dict[str, str | int]]] = fetch_carpark_availability,
    fetch_mall_reference_names_fn: Callable[[], list[str]] = fetch_mall_reference_names,
    load_samples_fn: Callable[[Path], dict[str, list[int]]] = load_samples,
) -> dict[str, Any]:
    """Run one full invocation of the whitelist-building state machine.

    Steps: fetch/match/merge (T5-guarded) -> write coverage map + print the mandatory T7
    gate instructions -> advance any ALREADY-signed-off entries to "observing" and run
    their poll window -> evaluate those entries to "verified"/"rejected" -> run the
    (stub) feed-churn check. Always writes the coverage map back to disk if anything
    changed past the gate.

    Args:
        coverage_map_path: Path to the coverage-map artifact.
        poll_output_file: CSV file the observation poll appends samples to.
        interval_minutes: Minutes between polls during "observing" (only affects entries
            already signed off; has no effect on the fetch/match/gate steps).
        duration_hours: Total observation window length in hours (same caveat).
        poll_fn: Injectable replacement for `poll_lta_carparks.run` (see
            `advance_signed_off_entries`).
        api_key: LTA DataMall AccountKey. Defaults to the `LTA_API_KEY` env var.
        fetch_carpark_availability_fn: Injectable LTA-feed fetch (see `build_and_gate`).
        fetch_mall_reference_names_fn: Injectable mall-dataset fetch (see `build_and_gate`).
        load_samples_fn: Injectable variance-samples loader (see `evaluate_observed_entries`).

    Returns:
        The final coverage-map dict for this invocation.
    """
    coverage_map = build_and_gate(
        coverage_map_path,
        api_key=api_key,
        fetch_carpark_availability_fn=fetch_carpark_availability_fn,
        fetch_mall_reference_names_fn=fetch_mall_reference_names_fn,
    )

    observed = advance_signed_off_entries(
        coverage_map, poll_output_file, interval_minutes, duration_hours, poll_fn
    )
    if observed:
        evaluate_observed_entries(observed, poll_output_file, load_samples_fn)
        write_coverage_map(coverage_map_path, coverage_map)
        logger.info(
            "Observation window complete for %d entries: %s",
            len(observed),
            ", ".join(f"{e['carpark_id']}={e['state']}" for e in observed),
        )

    check_feed_churn(coverage_map)  # stub -- informational only, see docstring
    return coverage_map


def main() -> None:
    """Parse CLI args and run one full `build_mall_whitelist` cycle.

    See the module docstring for the full state machine and sign-off mechanism.
    `--interval-minutes`/`--duration-hours` only take effect for entries already signed
    off ("observing" -- state machine step 4); they have no effect on the fetch/match/gate
    steps, which always run.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--coverage-map",
        type=Path,
        default=COVERAGE_MAP_FILE,
        help="Path to the coverage-map artifact (default: data/carpark_coverage_map.json). "
        "Human sign-off is recorded here via each entry's 'signed_off' field.",
    )
    parser.add_argument(
        "--poll-output-file",
        type=Path,
        default=POLL_OUTPUT_FILE,
        help="CSV file the observation window appends samples to (default: "
        "data/carpark_samples.csv, shared with scripts/poll_lta_carparks.py).",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=DEFAULT_INTERVAL_MINUTES,
        help=f"Minutes between polls during 'observing' (default: {DEFAULT_INTERVAL_MINUTES}, "
        "matching poll_lta_carparks.py's own default).",
    )
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=DEFAULT_DURATION_HOURS,
        help=f"Observation window length in hours (default: {DEFAULT_DURATION_HOURS}, matching "
        "poll_lta_carparks.py's own default).",
    )
    args = parser.parse_args()

    coverage_map = run_full_cycle(
        coverage_map_path=args.coverage_map,
        poll_output_file=args.poll_output_file,
        interval_minutes=args.interval_minutes,
        duration_hours=args.duration_hours,
    )

    sql = render_sql_inserts(coverage_map)
    if sql:
        logger.info("SQL inserts for verified entries (NOT executed -- apply manually/via CI):\n%s", sql)
    payload = build_insert_payload(coverage_map)
    if payload["candidates"]:
        logger.info("JSON payload for the poller-regen lane:\n%s", json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
