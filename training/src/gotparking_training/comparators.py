"""Leakage-free comparators: historical-average and persistence.

Design doc reference: Premise #7, amended (D6 leakage-free comparator, D8
dual benchmark). Both comparators are recomputed here from PRE-HOLDOUT
`TrainingRow`s only -- callers MUST NEVER pass holdout rows to
`build_historical_average`/`build_carpark_mean`, and this module NEVER
reads the live `carpark_baseline` table (it has no Supabase dependency at
all -- everything here is a pure function over in-memory rows), which is
exactly what makes the comparator leakage-free: the live `carpark_baseline`
table's daily aggregation spans the entire history including the holdout
window, and reading it here would hand the incumbent an in-sample
advantage that could block promotion indefinitely (Test Requirements
case 6: "comparator recomputed pre-holdout only").
"""

from __future__ import annotations

from collections.abc import Sequence

from gotparking_training.series import TrainingRow
from gotparking_training.sg_time import sgt_parts

#: (carpark_id, dow, slot_of_day) -> mean label value, built from
#: pre-holdout rows only.
HistoricalAverageTable = dict[tuple[str, int, int], float]

#: carpark_id -> mean label value across ALL of that carpark's pre-holdout
#: rows -- a coarser fallback for (carpark, dow, slot) cells with no
#: pre-holdout observations.
CarparkMeanTable = dict[str, float]


def build_historical_average(pre_holdout_rows: Sequence[TrainingRow]) -> HistoricalAverageTable:
    """Build the (carpark_id, dow, slot_of_day) -> mean(label) table.

    Keyed by each row's OWN target time's (dow, slot_of_day) -- the same
    (dow, slot) key the live `carpark_baseline` table would use, but
    computed here purely from the rows passed in, so callers control
    exactly which data contributes (pre-holdout only).

    Args:
        pre_holdout_rows: Training rows strictly before the holdout cutoff.
            Passing holdout rows here would leak the holdout window into
            the comparator -- this is the caller's responsibility to avoid,
            since this function has no notion of "holdout" itself.

    Returns:
        A dict from (carpark_id, dow, slot_of_day) to the mean label value
        observed in `pre_holdout_rows` for that cell. A cell with zero
        pre-holdout observations simply does not appear as a key.
    """
    sums: dict[tuple[str, int, int], tuple[float, int]] = {}
    for row in pre_holdout_rows:
        dow, slot = sgt_parts(row.target_time)
        key = (row.carpark_id, dow, slot)
        total, n = sums.get(key, (0.0, 0))
        sums[key] = (total + row.label, n + 1)
    return {key: total / n for key, (total, n) in sums.items()}


def build_carpark_mean(pre_holdout_rows: Sequence[TrainingRow]) -> CarparkMeanTable:
    """Build the carpark_id -> mean(label) fallback table.

    Args:
        pre_holdout_rows: Training rows strictly before the holdout cutoff.

    Returns:
        A dict from carpark_id to the mean label value across all of that
        carpark's pre-holdout rows.
    """
    sums: dict[str, tuple[float, int]] = {}
    for row in pre_holdout_rows:
        total, n = sums.get(row.carpark_id, (0.0, 0))
        sums[row.carpark_id] = (total + row.label, n + 1)
    return {carpark_id: total / n for carpark_id, (total, n) in sums.items()}


def predict_historical_average(
    table: HistoricalAverageTable,
    carpark_mean: CarparkMeanTable,
    row: TrainingRow,
) -> float:
    """Predict a row's label via the historical-average comparator.

    Args:
        table: The fine-grained (carpark, dow, slot) -> mean table (from
            `build_historical_average`).
        carpark_mean: The coarser per-carpark fallback table (from
            `build_carpark_mean`), used when the fine cell has no
            pre-holdout observations (a sparse (dow, slot) cell).
        row: The row to predict (only its carpark_id and target_time are
            used -- this comparator never looks at the row's own features
            or label).

    Returns:
        The historical average for this row's (carpark_id, dow, slot) of
        its target time, falling back to the carpark-wide mean, and
        finally to the row's own `lots_now` if even that is unavailable
        (a carpark with literally zero pre-holdout rows should not reach
        this function at all in practice, since it would also have no
        holdout rows to evaluate -- this is a last-resort guard, not an
        expected path).
    """
    dow, slot = sgt_parts(row.target_time)
    key = (row.carpark_id, dow, slot)
    if key in table:
        return table[key]
    if row.carpark_id in carpark_mean:
        return carpark_mean[row.carpark_id]
    return row.lots_now


def predict_persistence(row: TrainingRow) -> float:
    """Predict a row's label via the persistence comparator ("no change").

    Args:
        row: The row to predict.

    Returns:
        `row.lots_now` -- the persistence comparator's forecast is always
        "whatever the most recent reading was", by definition.
    """
    return row.lots_now
