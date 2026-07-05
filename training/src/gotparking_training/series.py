"""Generic chronological-series join logic: momentum offsets + label.

This is the single place that turns a per-carpark chronological sequence
of (timestamp, available_lots) readings into labeled training rows. It is
deliberately generic over the SOURCE of the series -- both the live loader
(`data_loading.py`, reading `carpark_history`) and the SINPA pretraining
loader (`sinpa.py`, reading de-windowed HuggingFace arrays) funnel through
`build_rows_from_series`, so the momentum/label join semantics can never
drift between the two data sources.

Design doc reference: Premise #2 (momentum features), Premise #7 amended
(label construction: "supervised pairs join features at time t to the
observed count at the sample nearest t+20min within +/-2.5 min, else the
row is dropped"). The same +/-2.5 min tolerance is applied to the momentum
offsets (15/30/60 min before t) -- this module's docstring choice (see
`build_rows_from_series`) is to DROP a row if ANY momentum offset is
missing, rather than filling with NaN, so training never sees a partial
momentum vector -- deliberately matching `api/_lib/batch_logic.py`'s
`_is_momentum_usable`, which also refuses to serve the ML path unless all
three lag readings are present. This keeps train-time and serve-time
momentum completeness semantics identical.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from gotparking_training.config import (
    FORECAST_HORIZON_MINUTES,
    JOIN_TOLERANCE_MINUTES,
    MOMENTUM_OFFSETS_MINUTES,
)
from gotparking_training.features import build_feature_vector


@dataclass(frozen=True)
class TimedSample:
    """One (timestamp, value) reading in a chronological series.

    Attributes:
        at: The UTC instant of this reading.
        value: The reading's value (available lots, as a float).
    """

    at: datetime
    value: float


@dataclass(frozen=True)
class TrainingRow:
    """One labeled training row, ready to feed into a LightGBM dataset.

    Attributes:
        carpark_id: Which carpark this row belongs to. NOT part of the
            feature vector itself (the model is a single global model
            across all carparks -- see `features.FEATURE_NAMES`, which has
            no carpark identifier column); kept here purely for bookkeeping
            (holdout splitting, comparator lookups, per-carpark diagnostics).
        base_time: The observation instant t (when `lots_now` was read).
        target_time: t + FORECAST_HORIZON_MINUTES -- the instant being
            predicted, and the instant the time/holiday features describe.
        features: The 7-element feature vector, in `FEATURE_NAMES` order.
        label: The observed available-lots value nearest `target_time`
            (within tolerance) -- the supervised target.
        lots_now: The `lots_now` reading, kept as its own field (in
            addition to being `features[3]`) purely for readability at
            call sites that need it directly (e.g. the persistence
            comparator, which by definition predicts `lots_now`).
    """

    carpark_id: str
    base_time: datetime
    target_time: datetime
    features: list[float]
    label: float
    lots_now: float


def _nearest_value(
    sorted_series: Sequence[TimedSample],
    sorted_epochs: Sequence[float],
    target: datetime,
    tolerance: timedelta,
) -> float | None:
    """Find the value of the sample nearest `target`, within `tolerance`.

    Args:
        sorted_series: The series, already sorted ascending by `.at`.
        sorted_epochs: `[s.at.timestamp() for s in sorted_series]`, passed
            in precomputed so a caller joining many targets against the
            same series (the common case) does not repeat this O(n)
            conversion per target.
        target: The instant to find the nearest reading for.
        tolerance: Maximum allowed distance between `target` and the
            nearest reading's timestamp.

    Returns:
        The nearest sample's value if within `tolerance`, else None.
    """
    if not sorted_epochs:
        return None
    target_epoch = target.timestamp()
    idx = bisect.bisect_left(sorted_epochs, target_epoch)
    candidates = [i for i in (idx - 1, idx) if 0 <= i < len(sorted_epochs)]
    if not candidates:
        return None
    best = min(candidates, key=lambda i: abs(sorted_epochs[i] - target_epoch))
    if abs(sorted_epochs[best] - target_epoch) > tolerance.total_seconds():
        return None
    return sorted_series[best].value


def build_rows_from_series(
    carpark_id: str,
    series: Sequence[TimedSample],
    *,
    momentum_offsets_minutes: Sequence[int] = MOMENTUM_OFFSETS_MINUTES,
    horizon_minutes: int = FORECAST_HORIZON_MINUTES,
    tolerance_minutes: float = JOIN_TOLERANCE_MINUTES,
) -> list[TrainingRow]:
    """Build every labeled training row derivable from one carpark's series.

    For each reading in `series` (treated as a candidate base time t),
    attempts to join:
      * each momentum offset (default 15/30/60 min before t) to the
        nearest reading within tolerance;
      * the label, the nearest reading to t + horizon_minutes within
        tolerance.

    A row is DROPPED (not emitted) if any of these joins fail -- a poll gap
    must never fabricate a momentum value or a label. This also means a
    row is only ever emitted when every momentum offset is genuinely
    available, matching the serving side's "any missing lag reading makes
    the whole row unusable for the ML path" rule (api/_lib/batch_logic.py's
    `_is_momentum_usable`).

    Args:
        carpark_id: The carpark this series belongs to.
        series: The carpark's chronological (timestamp, available_lots)
            readings. Need not be pre-sorted -- sorted internally.
        momentum_offsets_minutes: Override for the momentum lookback
            offsets (minutes before t), in feature-vector order. Defaults
            to the production (15, 30, 60) contract; overridable for tests.
        horizon_minutes: Override for the forecast horizon (minutes after
            t). Defaults to the production 20-minute contract.
        tolerance_minutes: Override for the nearest-sample join tolerance.
            Defaults to the production +/-2.5 minute contract.

    Returns:
        One TrainingRow per base time where every join succeeded, in the
        same order as the (sorted) input series.
    """
    sorted_series = sorted(series, key=lambda s: s.at)
    sorted_epochs = [s.at.timestamp() for s in sorted_series]
    tolerance = timedelta(minutes=tolerance_minutes)

    rows: list[TrainingRow] = []
    for sample in sorted_series:
        base_time = sample.at
        lots_now = sample.value

        momentum_values: list[float] = []
        missing_momentum = False
        for offset_minutes in momentum_offsets_minutes:
            offset_time = base_time - timedelta(minutes=offset_minutes)
            value = _nearest_value(sorted_series, sorted_epochs, offset_time, tolerance)
            if value is None:
                missing_momentum = True
                break
            momentum_values.append(value)
        if missing_momentum:
            continue

        target_time = base_time + timedelta(minutes=horizon_minutes)
        label = _nearest_value(sorted_series, sorted_epochs, target_time, tolerance)
        if label is None:
            continue

        lots_15m_ago, lots_30m_ago, lots_60m_ago = momentum_values
        features = build_feature_vector(
            target_time,
            lots_now=lots_now,
            lots_15m_ago=lots_15m_ago,
            lots_30m_ago=lots_30m_ago,
            lots_60m_ago=lots_60m_ago,
        )
        rows.append(
            TrainingRow(
                carpark_id=carpark_id,
                base_time=base_time,
                target_time=target_time,
                features=features,
                label=label,
                lots_now=lots_now,
            )
        )
    return rows
