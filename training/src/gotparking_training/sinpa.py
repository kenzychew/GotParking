"""SINPA historical dataset pretraining loader (design doc Premise #1, D13).

Source: HuggingFace `Huaiwu/SINPA` (NTU's DeepPA/SINPA research dataset,
15-minute resampled carpark availability, 2020-07-01 to 2021-06-30,
Singapore Open Data Licence 1.0). Full feasibility spike:
`docs/t0-sinpa-spike.md`.

Memory-pragmatic design (GitHub free runners have ~7GB RAM; the train split
alone is 5.93GB): this module NEVER loads `data/train.npz` at all -- only
the smaller val.npz (593MB) and test.npz (594MB) splits are used as the
pretraining corpus. More importantly, `download_sinpa_npz` immediately
slices out only the (up to 8) carparks' lot-count columns it needs from the
full `(N, 12, 1687, 12)` tensor and returns those tiny per-carpark
`(N, 12)` arrays -- the full tensor and the npz file handle go out of scope
at the end of that function, so this process never holds more than one
split's full tensor in memory at a time, and never holds it for longer
than the single slicing step.

De-windowing: the release ships samples as overlapping sliding windows
(12 input timesteps per sample, stride 1) rather than one continuous
series (docs/t0-sinpa-spike.md: "the release is windowed samples rather
than one continuous series, so T5 needs a small de-windowing step"). Since
consecutive samples' windows overlap in all but their last timestep,
`dewindow_series` reconstructs the underlying continuous series by taking
the first sample's full window plus one new reading (the last timestep)
per subsequent sample.

Known limitation, documented deliberately rather than silently assumed:
the released files carry no ground-truth timestamps. This module
approximates each split's start instant from the documented chronological
10:1:1 train/val/test split fractions over the known year-long span
(2020-07-01 to 2021-06-30) rather than parsing the auxiliary time-of-day/
weekday/holiday feature dimensions (dims 1-11), which would require
reverse-engineering their exact encoding -- out of scope for a pretraining-
only, never-gates-promotion data source (docs/t0-sinpa-spike.md: "validate
exclusively on live 2026 data, never on SINPA held-out data"). A few days
of timestamp drift only blurs the weekday/holiday prior this data
contributes; live fine-tuning on exact 2026 timestamps is what the
promotion gate actually evaluates.

Grid/tolerance mismatch (discovered while writing this module's tests,
fixed here rather than left as a silent zero-row bug): the live join
contract (`config.JOIN_TOLERANCE_MINUTES` = +/-2.5 min) assumes data on a
5-minute native grid, where the 20-minute label horizon lands exactly on a
tick. SINPA's 15-minute grid does NOT evenly divide 20 minutes (20 % 15 ==
5), so the nearest grid tick to any label target is always exactly 5
minutes away -- a tight 2.5-minute tolerance would silently drop EVERY
SINPA row's label. `load_sinpa_training_rows` therefore joins SINPA rows
with a wider, grid-appropriate tolerance (`SINPA_JOIN_TOLERANCE_MINUTES`,
see its docstring below) -- this only affects SINPA rows; live data's
tolerance is untouched.

ANY failure here (download, parse, de-window, mapping) raises
:class:`SinpaUnavailableError`; callers (train.py) MUST catch this and
degrade to live-only training rather than letting the weekly run crash
over optional pretraining data (Test Requirements case 18).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from numpy.typing import NDArray

from gotparking_training.series import TimedSample, TrainingRow, build_rows_from_series

logger = logging.getLogger(__name__)

#: HuggingFace dataset repo per docs/t0-sinpa-spike.md.
SINPA_REPO_ID = "Huaiwu/SINPA"
SINPA_REPO_TYPE = "dataset"

#: Deliberately excludes "data/train.npz" (5.93GB) -- see module docstring.
SINPA_SPLIT_FILES: tuple[str, ...] = ("data/val.npz", "data/test.npz")

#: SINPA's full covered span (docs/t0-sinpa-spike.md), used to approximate
#: each split's start instant per the module docstring's documented
#: limitation.
_SINPA_SPAN_START = datetime(2020, 7, 1, tzinfo=timezone.utc)
_SINPA_SPAN_END = datetime(2021, 6, 30, tzinfo=timezone.utc)
_SINPA_TOTAL_DAYS = (_SINPA_SPAN_END - _SINPA_SPAN_START).days

#: Chronological 10:1:1 train/val/test split (docs/t0-sinpa-spike.md).
SINPA_VAL_START = _SINPA_SPAN_START + timedelta(days=_SINPA_TOTAL_DAYS * 10 // 12)
SINPA_TEST_START = _SINPA_SPAN_START + timedelta(days=_SINPA_TOTAL_DAYS * 11 // 12)

#: Resampled granularity of the released data (docs/t0-sinpa-spike.md).
SINPA_SLOT_MINUTES = 15

#: Join tolerance used ONLY for SINPA rows -- deliberately wider than the
#: live join tolerance (config.JOIN_TOLERANCE_MINUTES, 2.5 min). Live data
#: is polled every 5 minutes, so the 20-minute label horizon lands exactly
#: on-grid (4 ticks ahead) and a tight +/-2.5 min tolerance is appropriate.
#: SINPA was resampled by its authors to a 15-minute grid
#: (docs/t0-sinpa-spike.md), on which 20 minutes ahead is NOT a whole
#: number of ticks (20 % 15 == 5) -- the nearest grid tick to any 20-minute
#: target is always exactly 5 minutes away, which would miss a 2.5-minute
#: tolerance for every single row. Half the grid spacing (7.5 min) is the
#: natural, unambiguous tolerance for a 15-minute grid: it always resolves
#: to the single nearest tick (5 min < 7.5 min) without ever being equidistant
#: between two candidates, and it does not affect live data's tolerance at
#: all (this constant is only ever passed to `build_rows_from_series` from
#: within THIS module).
SINPA_JOIN_TOLERANCE_MINUTES = 7.5


class SinpaUnavailableError(Exception):
    """Raised when SINPA pretraining data cannot be obtained or used for
    any reason (download failure, unexpected shape, missing mapping).
    Callers MUST catch this and fall back to live-only training -- never
    let it crash the weekly run.
    """


@dataclass(frozen=True)
class SinpaCarparkMapping:
    """One carpark's SINPA coordinate mapping (from `carparks.sinpa_index`).

    Attributes:
        carpark_id: The LTA DataMall carpark ID.
        sinpa_index: The carpark's column index into SINPA's 1687-lot
            array (docs/t0-sinpa-spike.md's exact-match table).
    """

    carpark_id: str
    sinpa_index: int


def download_sinpa_npz(filename: str, lot_indices: Sequence[int]) -> dict[int, NDArray[np.float64]]:
    """Download one SINPA split and slice out only the requested lot columns.

    Args:
        filename: The split file to fetch, e.g. "data/val.npz" (one of
            `SINPA_SPLIT_FILES`).
        lot_indices: The SINPA column indices to slice out of the full
            `(N, 12, 1687, 12)` tensor. Only these columns' `(N, 12)`
            lot-count slices are retained in memory -- see module
            docstring.

    Returns:
        A dict from `lot_index` to its `(N, 12)` float64 lot-count array
        (dim 0 of SINPA's feature axis, per docs/t0-sinpa-spike.md).

    Raises:
        Exception: Any failure from `huggingface_hub.hf_hub_download` or
            `numpy.load` propagates as-is -- callers
            (`load_sinpa_training_rows`) are responsible for wrapping this
            into :class:`SinpaUnavailableError`, this function does not
            catch anything itself so its own errors stay unambiguous in
            isolation/unit tests.
    """
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(repo_id=SINPA_REPO_ID, filename=filename, repo_type=SINPA_REPO_TYPE)
    with np.load(path) as data:
        x = data["x"]  # shape (N, 12, 1687, 12); dim -1 index 0 is lot count.
        return {
            lot_index: np.array(x[:, :, lot_index, 0], dtype=np.float64, copy=True)
            for lot_index in lot_indices
        }
    # `x` and the underlying npz file handle go out of scope here -- only
    # the small per-carpark slices returned above are retained.


def dewindow_series(sliced: NDArray[np.float64], start_time: datetime) -> list[TimedSample]:
    """Reconstruct a continuous per-carpark series from overlapping windows.

    Args:
        sliced: Shape `(N, T_in)` -- N overlapping windows of `T_in`
            timesteps each (SINPA: T_in=12), at stride 1, for one carpark's
            lot-count series (the output of one `lot_index` entry from
            `download_sinpa_npz`).
        start_time: The instant the FIRST timestep of the FIRST window
            represents. Ticks are spaced `SINPA_SLOT_MINUTES` apart.

    Returns:
        `N + T_in - 1` TimedSamples: the first window's full `T_in`
        readings, then one new reading (each subsequent window's last
        timestep) per remaining window -- this reconstructs the underlying
        series without duplicating the overlapped readings.

    Raises:
        ValueError: If `sliced` is not 2-dimensional, or has zero rows.
    """
    if sliced.ndim != 2:
        raise ValueError(f"expected a 2D (N, T_in) array, got shape {sliced.shape}")
    n_samples, t_in = sliced.shape
    if n_samples == 0:
        raise ValueError("cannot de-window zero samples")

    values: list[float] = [float(v) for v in sliced[0, :]]
    values.extend(float(sliced[i, -1]) for i in range(1, n_samples))

    return [
        TimedSample(start_time + timedelta(minutes=SINPA_SLOT_MINUTES * i), value)
        for i, value in enumerate(values)
    ]


def load_sinpa_training_rows(
    mappings: Sequence[SinpaCarparkMapping],
    *,
    download_fn: Callable[
        [str, Sequence[int]], dict[int, NDArray[np.float64]]
    ] = download_sinpa_npz,
) -> dict[str, list[TrainingRow]]:
    """Download, de-window, and label SINPA pretraining rows for each mapping.

    Args:
        mappings: Carparks to load (only those with a non-null
            `sinpa_index`; callers are responsible for excluding
            SINPA-absent carparks like Raffles City/VivoCity P2 before
            calling this).
        download_fn: Injectable download function, `(filename, lot_indices)
            -> {lot_index: (N, T_in) array}`. Defaults to
            `download_sinpa_npz` (the real HuggingFace download); tests
            inject a fake returning small synthetic arrays so this whole
            pipeline is exercisable fully offline.

    Returns:
        A dict from carpark_id to its labeled TrainingRows, built via the
        exact same `series.build_rows_from_series` join logic live data
        uses (same momentum/label tolerance semantics). Empty dict if
        `mappings` is empty (no SINPA-mapped carparks this cycle).

    Raises:
        SinpaUnavailableError: On ANY failure -- download, parsing,
            unexpected shape, or a requested lot_index missing from a
            split's response. Callers MUST catch this and fall back to
            live-only training (Test Requirements case 18); this function
            never lets a raw exception type escape, so callers only ever
            need to handle this one error class.
    """
    if not mappings:
        return {}

    lot_indices = [mapping.sinpa_index for mapping in mappings]
    index_to_carpark = {mapping.sinpa_index: mapping.carpark_id for mapping in mappings}

    try:
        val_slices = download_fn("data/val.npz", lot_indices)
        test_slices = download_fn("data/test.npz", lot_indices)
    except Exception as exc:
        raise SinpaUnavailableError(f"SINPA download/parse failed: {exc}") from exc

    try:
        rows_by_carpark: dict[str, list[TrainingRow]] = {}
        for split_slices, split_start in (
            (val_slices, SINPA_VAL_START),
            (test_slices, SINPA_TEST_START),
        ):
            for lot_index, carpark_id in index_to_carpark.items():
                if lot_index not in split_slices:
                    raise SinpaUnavailableError(
                        f"lot_index {lot_index} (carpark {carpark_id}) missing from split "
                        f"response"
                    )
                series = dewindow_series(split_slices[lot_index], split_start)
                rows = build_rows_from_series(
                    carpark_id, series, tolerance_minutes=SINPA_JOIN_TOLERANCE_MINUTES
                )
                rows_by_carpark.setdefault(carpark_id, []).extend(rows)
        total_rows = sum(len(rows) for rows in rows_by_carpark.values())
        logger.info(
            "load_sinpa_training_rows: built %d rows across %d carparks",
            total_rows, len(rows_by_carpark),
        )
        return rows_by_carpark
    except SinpaUnavailableError:
        raise
    except Exception as exc:
        raise SinpaUnavailableError(f"SINPA de-window/mapping failed: {exc}") from exc
