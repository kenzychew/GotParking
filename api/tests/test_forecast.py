"""Tests for the public forecast-read endpoint (api/_lib/read_logic.py).

Covers every case in the design doc's Test Requirements public-read
section:
  7. returns the pinned payload shape + cache headers
  8. generated_at passed through for staleness UI
  9. forecast table empty/unreachable -> typed 503, never a raw 500
"""

from __future__ import annotations

from _lib.read_logic import ReadDeps, handle_forecast_read
from tests.fakes import FakeSupabaseDB

CARPARKS = [
    {"carpark_id": "1", "name": "Suntec City"},
    {"carpark_id": "2", "name": "Marina Square"},
]


def _db(
    forecast_rows: list[dict[str, object]],
    carparks_rows: list[dict[str, object]] | None = None,
) -> FakeSupabaseDB:
    return FakeSupabaseDB(
        tables={
            "carpark_forecast": forecast_rows,
            "carparks": carparks_rows if carparks_rows is not None else CARPARKS,
        }
    )


class TestPinnedPayloadShape:
    """Case 7: returns the pinned payload shape + cache headers."""

    def test_full_payload_shape_and_field_names(self) -> None:
        db = _db(
            [
                {
                    "carpark_id": "1",
                    "state": "ml",
                    "forecast_lots": 120,
                    "tier": "plenty",
                    "live_lots": 130,
                    "model_version": "v3",
                    "generated_at": "2026-07-05T12:00:00+00:00",
                },
                {
                    "carpark_id": "2",
                    "state": "cold_start",
                    "forecast_lots": None,
                    "tier": None,
                    "live_lots": 40,
                    "model_version": None,
                    "generated_at": "2026-07-05T12:00:00+00:00",
                },
            ]
        )

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.status == 200
        assert set(response.body.keys()) == {"generated_at", "carparks"}
        assert len(response.body["carparks"]) == 2

        ml_entry = next(c for c in response.body["carparks"] if c["carpark_id"] == "1")
        assert ml_entry == {
            "carpark_id": "1",
            "name": "Suntec City",
            "state": "ml",
            "forecast_lots": 120,
            "tier": "plenty",
            "live_lots": 130,
            "model_version": "v3",
        }

        cold_entry = next(c for c in response.body["carparks"] if c["carpark_id"] == "2")
        assert cold_entry["state"] == "cold_start"
        assert cold_entry["forecast_lots"] is None
        assert cold_entry["tier"] is None
        assert cold_entry["model_version"] is None

    def test_cache_control_and_content_type_headers(self) -> None:
        db = _db(
            [
                {
                    "carpark_id": "1",
                    "state": "baseline",
                    "forecast_lots": 10,
                    "tier": "limited",
                    "live_lots": 10,
                    "model_version": None,
                    "generated_at": "2026-07-05T12:00:00+00:00",
                }
            ]
        )

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.headers["Cache-Control"] == "public, s-maxage=90, stale-while-revalidate=60"
        assert response.headers["Content-Type"] == "application/json"

    def test_missing_carpark_name_falls_back_to_carpark_id(self) -> None:
        db = _db(
            [
                {
                    "carpark_id": "99",
                    "state": "baseline",
                    "forecast_lots": 5,
                    "tier": "very_limited",
                    "live_lots": 5,
                    "model_version": None,
                    "generated_at": "2026-07-05T12:00:00+00:00",
                }
            ],
            carparks_rows=[],  # no matching carparks row for "99"
        )

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.status == 200
        assert response.body["carparks"][0]["name"] == "99"


class TestGeneratedAtPassthrough:
    """Case 8: generated_at passed through for staleness UI."""

    def test_returns_the_max_generated_at_across_rows(self) -> None:
        db = _db(
            [
                {
                    "carpark_id": "1",
                    "state": "ml",
                    "forecast_lots": 10,
                    "tier": "limited",
                    "live_lots": 10,
                    "model_version": "v1",
                    "generated_at": "2026-07-05T12:00:00+00:00",
                },
                {
                    "carpark_id": "2",
                    "state": "ml",
                    "forecast_lots": 10,
                    "tier": "limited",
                    "live_lots": 10,
                    "model_version": "v1",
                    "generated_at": "2026-07-05T12:05:00+00:00",  # later
                },
            ]
        )

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.body["generated_at"] == "2026-07-05T12:05:00+00:00"

    def test_generated_at_normalizes_z_suffix_to_utc_offset(self) -> None:
        db = _db(
            [
                {
                    "carpark_id": "1",
                    "state": "ml",
                    "forecast_lots": 10,
                    "tier": "limited",
                    "live_lots": 10,
                    "model_version": "v1",
                    "generated_at": "2026-07-05T12:00:00Z",
                }
            ]
        )

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.body["generated_at"] == "2026-07-05T12:00:00+00:00"


class TestUnavailable:
    """Case 9: forecast table empty/unreachable -> typed 503, never a raw 500."""

    def test_empty_table_returns_typed_503(self) -> None:
        db = _db([])

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.status == 503
        assert response.body == {
            "error": "predictions_unavailable",
            "message": "Predictions temporarily unavailable",
        }

    def test_supabase_unreachable_returns_typed_503(self) -> None:
        db = _db([])
        db.fail_tables.add("carpark_forecast")

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.status == 503
        assert response.body["error"] == "predictions_unavailable"

    def test_carparks_join_table_unreachable_also_returns_typed_503(self) -> None:
        # Even a failure fetching the (non-critical-looking) names table
        # must still degrade to the typed 503, never a raw crash/500.
        db = _db(
            [
                {
                    "carpark_id": "1",
                    "state": "ml",
                    "forecast_lots": 10,
                    "tier": "limited",
                    "live_lots": 10,
                    "model_version": "v1",
                    "generated_at": "2026-07-05T12:00:00+00:00",
                }
            ]
        )
        db.fail_tables.add("carparks")

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.status == 503

    def test_malformed_row_never_raises_raw_500(self) -> None:
        # A row missing a required key would raise KeyError while building
        # the response -- the broad catch must still produce a typed 503,
        # not propagate an unhandled exception.
        db = _db([{"carpark_id": "1", "generated_at": "2026-07-05T12:00:00+00:00"}])

        response = handle_forecast_read(ReadDeps(db=db))

        assert response.status == 503
        assert response.body["error"] == "predictions_unavailable"
