"""Tests for gotparking_training.sinpa (SINPA pretraining loader).

Covers Test Requirements case 16 (de-window + 8-seed mapping, synthetic
fixture) and case 18 (SINPA failure wraps into SinpaUnavailableError, the
signal train.py's fallback-to-live-only path depends on). No test in this
file makes a real network call -- `download_sinpa_npz`'s HuggingFace call
is either bypassed via dependency injection (`load_sinpa_training_rows`'s
`download_fn` parameter) or exercised against a locally-written temp .npz
file with `huggingface_hub.hf_hub_download` monkeypatched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from gotparking_training.sinpa import (
    SINPA_TEST_START,
    SINPA_VAL_START,
    SinpaCarparkMapping,
    SinpaUnavailableError,
    dewindow_series,
    download_sinpa_npz,
    load_sinpa_training_rows,
)

_START = datetime(2020, 7, 1, tzinfo=timezone.utc)


class TestDewindowSeries:
    def test_reconstructs_continuous_series_from_overlapping_windows(self) -> None:
        # N=4 windows of T_in=3, stride 1: rows overlap by 2.
        sliced = np.array(
            [[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [3.0, 4.0, 5.0], [4.0, 5.0, 6.0]]
        )

        series = dewindow_series(sliced, _START)

        values = [s.value for s in series]
        assert values == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]  # N + T_in - 1 == 6
        assert len(series) == 6

    def test_ticks_are_15_minutes_apart(self) -> None:
        sliced = np.array([[1.0, 2.0], [2.0, 3.0]])

        series = dewindow_series(sliced, _START)

        assert series[1].at - series[0].at == timedelta(minutes=15)
        assert series[0].at == _START

    def test_rejects_non_2d_input(self) -> None:
        with pytest.raises(ValueError, match="2D"):
            dewindow_series(np.zeros((2, 3, 4)), _START)

    def test_rejects_zero_samples(self) -> None:
        with pytest.raises(ValueError, match="zero samples"):
            dewindow_series(np.zeros((0, 3)), _START)


class TestDownloadSinpaNpz:
    """Exercises the real slicing logic against a locally-written temp .npz
    file, with huggingface_hub.hf_hub_download monkeypatched to avoid any
    network access.
    """

    def test_slices_only_requested_lot_indices(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        n, t_in, n_lots, n_features = 5, 3, 10, 2
        rng = np.random.default_rng(0)
        x = rng.random((n, t_in, n_lots, n_features))
        npz_path = tmp_path / "val.npz"
        np.savez(npz_path, x=x, y=rng.random((n, t_in, n_lots, 1)))

        def fake_hf_hub_download(*, repo_id: str, filename: str, repo_type: str) -> str:
            assert repo_id == "Huaiwu/SINPA"
            assert repo_type == "dataset"
            return str(npz_path)

        monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_hf_hub_download)

        result = download_sinpa_npz("data/val.npz", [2, 7])

        assert set(result) == {2, 7}
        np.testing.assert_array_equal(result[2], x[:, :, 2, 0])
        np.testing.assert_array_equal(result[7], x[:, :, 7, 0])
        assert result[2].dtype == np.float64

    def test_propagates_download_errors_unwrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_hf_hub_download(*, repo_id: str, filename: str, repo_type: str) -> str:
            raise OSError("network unreachable")

        monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_hf_hub_download)

        with pytest.raises(OSError, match="network unreachable"):
            download_sinpa_npz("data/val.npz", [0])


def _fake_download_fn(splits: dict[str, dict[int, np.ndarray]]):
    def download_fn(filename: str, lot_indices):
        return {idx: splits[filename][idx] for idx in lot_indices}

    return download_fn


class TestLoadSinpaTrainingRowsHappyPath:
    """Test Requirements case 16: de-window + 8-seed mapping (synthetic)."""

    def test_maps_eight_seed_carparks_and_builds_rows(self) -> None:
        # 8 mapped carparks, matching the real seed-list mapping count
        # (docs/t0-sinpa-spike.md: 8 of 10 seeds are SINPA-mapped).
        mappings = [
            SinpaCarparkMapping(carpark_id=str(i), sinpa_index=1580 + i) for i in range(8)
        ]
        n, t_in = 40, 12  # enough de-windowed ticks (51) for a full join window
        rng = np.random.default_rng(0)
        per_split_arrays = {
            m.sinpa_index: rng.random((n, t_in)) * 100 for m in mappings
        }
        download_fn = _fake_download_fn(
            {"data/val.npz": per_split_arrays, "data/test.npz": per_split_arrays}
        )

        rows_by_carpark = load_sinpa_training_rows(mappings, download_fn=download_fn)

        assert set(rows_by_carpark) == {m.carpark_id for m in mappings}
        # Both splits contribute rows (val + test), and de-windowing expands
        # each split's n=40 windows into 40 + 12 - 1 = 51 ticks.
        for carpark_id, rows in rows_by_carpark.items():
            assert len(rows) > 0
            assert all(row.carpark_id == carpark_id for row in rows)

    def test_empty_mappings_returns_empty_dict(self) -> None:
        assert load_sinpa_training_rows([], download_fn=_fake_download_fn({})) == {}

    def test_rows_from_val_and_test_splits_are_both_included(self) -> None:
        mapping = SinpaCarparkMapping(carpark_id="1", sinpa_index=1584)
        rng = np.random.default_rng(1)
        val_array = rng.random((40, 12)) * 100
        test_array = rng.random((40, 12)) * 100
        download_fn = _fake_download_fn(
            {"data/val.npz": {1584: val_array}, "data/test.npz": {1584: test_array}}
        )

        rows_by_carpark = load_sinpa_training_rows([mapping], download_fn=download_fn)

        # Rows should span both SINPA_VAL_START and SINPA_TEST_START epochs.
        base_times = [row.base_time for row in rows_by_carpark["1"]]
        assert any(bt < SINPA_TEST_START for bt in base_times)
        assert any(bt >= SINPA_TEST_START for bt in base_times)


class TestLoadSinpaTrainingRowsFailureModes:
    """Test Requirements case 18: any SINPA failure wraps into
    SinpaUnavailableError so train.py can catch exactly one error class.
    """

    def test_download_failure_wraps_into_sinpa_unavailable(self) -> None:
        def failing_download_fn(filename: str, lot_indices):
            raise OSError("connection reset")

        mappings = [SinpaCarparkMapping(carpark_id="1", sinpa_index=1584)]

        with pytest.raises(SinpaUnavailableError, match="download/parse failed"):
            load_sinpa_training_rows(mappings, download_fn=failing_download_fn)

    def test_missing_lot_index_in_response_wraps_into_sinpa_unavailable(self) -> None:
        mapping = SinpaCarparkMapping(carpark_id="1", sinpa_index=1584)

        def download_fn(filename: str, lot_indices):
            return {}  # never returns the requested index

        with pytest.raises(SinpaUnavailableError, match="missing from split"):
            load_sinpa_training_rows([mapping], download_fn=download_fn)

    def test_malformed_shape_wraps_into_sinpa_unavailable(self) -> None:
        mapping = SinpaCarparkMapping(carpark_id="1", sinpa_index=1584)

        def download_fn(filename: str, lot_indices):
            return {1584: np.zeros((3, 3, 3))}  # wrong ndim -> dewindow_series raises

        with pytest.raises(SinpaUnavailableError, match="de-window/mapping failed"):
            load_sinpa_training_rows([mapping], download_fn=download_fn)


class TestSpanConstants:
    def test_val_and_test_start_are_chronologically_ordered_within_span(self) -> None:
        from gotparking_training.sinpa import _SINPA_SPAN_END, _SINPA_SPAN_START

        assert _SINPA_SPAN_START < SINPA_VAL_START < SINPA_TEST_START < _SINPA_SPAN_END


class TestGridToleranceMismatch:
    """Regression test locking in a real bug found while writing this
    module's tests: SINPA's 15-minute grid does not evenly divide the
    20-minute label horizon (20 % 15 == 5), so the live join tolerance
    (+/-2.5 min) would silently drop every SINPA row's label. This pins
    both halves of that finding so neither can silently regress.
    """

    def test_default_live_tolerance_yields_zero_rows_on_a_15min_grid(self) -> None:
        from gotparking_training.config import JOIN_TOLERANCE_MINUTES
        from gotparking_training.series import build_rows_from_series

        rng = np.random.default_rng(0)
        sliced = rng.random((40, 12)) * 100
        series = dewindow_series(sliced, _START)

        rows = build_rows_from_series("1", series, tolerance_minutes=JOIN_TOLERANCE_MINUTES)

        assert rows == []

    def test_sinpa_tolerance_recovers_rows_on_the_same_grid(self) -> None:
        from gotparking_training.sinpa import SINPA_JOIN_TOLERANCE_MINUTES
        from gotparking_training.series import build_rows_from_series

        rng = np.random.default_rng(0)
        sliced = rng.random((40, 12)) * 100
        series = dewindow_series(sliced, _START)

        rows = build_rows_from_series(
            "1", series, tolerance_minutes=SINPA_JOIN_TOLERANCE_MINUTES
        )

        assert len(rows) > 0
