"""Singapore time (SGT) helpers shared by the batch-predict feature contract.

Design doc reference: Constraints (time semantics, D3), Premise #2 (feature
contract), Premise #10 (holiday cold-start note). Every timestamp stored in
Supabase is UTC (``timestamptz``); this module is the single place that
converts a UTC instant into the SGT-calendar values used as model features.

Singapore observes a single fixed offset, UTC+8, with no daylight saving
time. Per the design doc's explicit instruction, this is implemented as
plain ``timedelta`` arithmetic on the UTC value rather than via the IANA
timezone database (``zoneinfo``) -- this keeps the contract simple, avoids a
runtime dependency on tzdata being installed in the Vercel Python runtime,
and matches the TypeScript poller's equivalent helper exactly (both sides of
the training/serving contract must agree bit-for-bit on slot boundaries).

The public-holiday feature is sourced from MOM's "Singapore Public Holidays
(consolidated)" dataset on data.gov.sg (dataset ID
``d_8ef23381f9417e4d4254ee8b4dcdb176``, fetched 2026-07-05). Every gazetted
public holiday is included, including the "(Observed)" substitute holidays
Singapore grants when a holiday falls on a Sunday, and the ad hoc "Polling
Day" holidays declared for general elections -- all of these are non-working
days that shift real-world carpark demand the same way a "normal" holiday
does, and all are part of the named source dataset. The dict covers
2020-2026 as required (the 2020-2021 years matter because the SINPA
pretraining data spike, T0, uses that period, and this module's contract is
shared with training per the design doc); 2027-01-01 is also included so a
batch run in the last 20 minutes of 2026 SGT still resolves the holiday
feature correctly for its target time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Fixed Singapore offset. Singapore has used UTC+8 with no DST since 1982.
_SGT_OFFSET = timedelta(hours=8)

# Slot width for `slot_of_day` (15-minute buckets, 96 per day). Must match
# the poller's TS helper and the training job's Python helper exactly.
_SLOT_MINUTES = 15
_SLOTS_PER_DAY = (24 * 60) // _SLOT_MINUTES  # 96

# ---------------------------------------------------------------------------
# HOLIDAYS: Singapore gazetted public holidays, keyed by SGT calendar date.
# Source: MOM "Singapore Public Holidays (consolidated)", data.gov.sg,
# dataset d_8ef23381f9417e4d4254ee8b4dcdb176 (fetched 2026-07-05).
# ---------------------------------------------------------------------------
HOLIDAYS: dict[date, str] = {
    # 2020
    date(2020, 1, 1): "New Year's Day",
    date(2020, 1, 25): "Chinese New Year",
    date(2020, 1, 26): "Chinese New Year",
    date(2020, 1, 27): "Chinese New Year (Observed)",
    date(2020, 4, 10): "Good Friday",
    date(2020, 5, 1): "Labour Day",
    date(2020, 5, 7): "Vesak Day",
    date(2020, 5, 24): "Hari Raya Puasa",
    date(2020, 5, 25): "Hari Raya Puasa (Observed)",
    date(2020, 7, 10): "Polling Day",
    date(2020, 7, 31): "Hari Raya Haji",
    date(2020, 8, 9): "National Day",
    date(2020, 8, 10): "National Day (Observed)",
    date(2020, 11, 14): "Deepavali",
    date(2020, 12, 25): "Christmas Day",
    # 2021
    date(2021, 1, 1): "New Year's Day",
    date(2021, 2, 12): "Chinese New Year",
    date(2021, 2, 13): "Chinese New Year",
    date(2021, 4, 2): "Good Friday",
    date(2021, 5, 1): "Labour Day",
    date(2021, 5, 13): "Hari Raya Puasa",
    date(2021, 5, 26): "Vesak Day",
    date(2021, 7, 20): "Hari Raya Haji",
    date(2021, 8, 9): "National Day",
    date(2021, 11, 4): "Deepavali",
    date(2021, 12, 25): "Christmas Day",
    # 2022
    date(2022, 1, 1): "New Year's Day",
    date(2022, 2, 1): "Chinese New Year",
    date(2022, 2, 2): "Chinese New Year",
    date(2022, 4, 15): "Good Friday",
    date(2022, 5, 1): "Labour Day",
    date(2022, 5, 2): "Hari Raya Puasa",
    date(2022, 5, 3): "Labour Day (Observed)",
    date(2022, 5, 15): "Vesak Day",
    date(2022, 7, 10): "Hari Raya Haji",
    date(2022, 8, 9): "National Day",
    date(2022, 10, 24): "Deepavali",
    date(2022, 12, 25): "Christmas Day",
    date(2022, 12, 26): "Christmas Day (Observed)",
    # 2023
    date(2023, 1, 1): "New Year's Day",
    date(2023, 1, 2): "New Year's Day (Observed)",
    date(2023, 1, 22): "Chinese New Year",
    date(2023, 1, 23): "Chinese New Year",
    date(2023, 1, 24): "Chinese New Year (Observed)",
    date(2023, 4, 7): "Good Friday",
    date(2023, 4, 22): "Hari Raya Puasa",
    date(2023, 5, 1): "Labour Day",
    date(2023, 6, 2): "Vesak Day",
    date(2023, 6, 29): "Hari Raya Haji",
    date(2023, 8, 9): "National Day",
    date(2023, 9, 1): "Polling Day",
    date(2023, 11, 12): "Deepavali",
    date(2023, 11, 13): "Deepavali (Observed)",
    date(2023, 12, 25): "Christmas Day",
    # 2024
    date(2024, 1, 1): "New Year's Day",
    date(2024, 2, 10): "Chinese New Year",
    date(2024, 2, 11): "Chinese New Year",
    date(2024, 2, 12): "Chinese New Year (Observed)",
    date(2024, 3, 29): "Good Friday",
    date(2024, 4, 10): "Hari Raya Puasa",
    date(2024, 5, 1): "Labour Day",
    date(2024, 5, 22): "Vesak Day",
    date(2024, 6, 17): "Hari Raya Haji",
    date(2024, 8, 9): "National Day",
    date(2024, 10, 31): "Deepavali",
    date(2024, 12, 25): "Christmas Day",
    # 2025
    date(2025, 1, 1): "New Year's Day",
    date(2025, 1, 29): "Chinese New Year",
    date(2025, 1, 30): "Chinese New Year",
    date(2025, 3, 31): "Hari Raya Puasa",
    date(2025, 4, 18): "Good Friday",
    date(2025, 5, 1): "Labour Day",
    date(2025, 5, 3): "Polling Day",
    date(2025, 5, 12): "Vesak Day",
    date(2025, 6, 7): "Hari Raya Haji",
    date(2025, 8, 9): "National Day",
    date(2025, 10, 20): "Deepavali",
    date(2025, 12, 25): "Christmas Day",
    # 2026
    date(2026, 1, 1): "New Year's Day",
    date(2026, 2, 17): "Chinese New Year",
    date(2026, 2, 18): "Chinese New Year",
    date(2026, 3, 21): "Hari Raya Puasa",
    date(2026, 4, 3): "Good Friday",
    date(2026, 5, 1): "Labour Day",
    date(2026, 5, 27): "Hari Raya Haji",
    date(2026, 5, 31): "Vesak Day",
    date(2026, 6, 1): "Vesak Day (Observed)",
    date(2026, 8, 9): "National Day",
    date(2026, 8, 10): "National Day (Observed)",
    date(2026, 11, 8): "Deepavali",
    date(2026, 11, 9): "Deepavali (Observed)",
    date(2026, 12, 25): "Christmas Day",
    # 2027 (New Year's Day only -- covers the batch run's t+20min target time
    # for invocations in the last 20 minutes of 2026 SGT).
    date(2027, 1, 1): "New Year's Day",
}


def _to_sgt_naive(dt_utc: datetime) -> datetime:
    """Shift a UTC instant by the fixed SGT offset.

    Args:
        dt_utc: A UTC instant. Timezone-aware datetimes are converted to UTC
            first; naive datetimes are assumed to already be UTC (this
            matches every timestamp this codebase produces, e.g.
            ``datetime.now(timezone.utc)`` or a parsed Supabase
            ``timestamptz`` value).

    Returns:
        The same instant shifted by +8 hours. The returned datetime keeps
        whatever tzinfo the input had (still UTC-labelled) -- only the wall-
        clock fields (hour, minute, weekday) are meant to be read as SGT.
        This is intentional: Singapore has no DST and no need for a real
        ``zoneinfo`` object, just an arithmetic shift.
    """
    if dt_utc.tzinfo is not None:
        dt_utc = dt_utc.astimezone(timezone.utc)
    return dt_utc + _SGT_OFFSET


def sgt_parts(dt_utc: datetime) -> tuple[int, int]:
    """Compute the (day-of-week, slot-of-day) SGT feature pair for an instant.

    This is the exact feature-computation contract shared with training: the
    same UTC instant must always map to the same (dow, slot_of_day) pair
    regardless of which codebase (this one, or the training job) computes it.

    Args:
        dt_utc: The UTC instant to convert (typically the batch run's target
            time, ``now + 20 minutes``, per the forecast horizon commitment).

    Returns:
        A ``(dow, slot_of_day)`` tuple where ``dow`` is 0=Monday..6=Sunday
        and ``slot_of_day`` is 0..95, one per 15-minute bucket of the SGT day.

    Boundary example (the mandatory pinned test case): Sat 02:00 SGT is
    Fri 18:00 UTC, and must resolve to dow=5 (Saturday) -- Python's
    ``datetime.weekday()`` already numbers Monday=0..Sunday=6, so this falls
    out of ``sgt.weekday()`` directly without any extra remapping.
    """
    sgt = _to_sgt_naive(dt_utc)
    dow = sgt.weekday()
    slot_of_day = sgt.hour * (60 // _SLOT_MINUTES) + sgt.minute // _SLOT_MINUTES
    return dow, slot_of_day


def is_holiday(dt_utc: datetime) -> bool:
    """Return whether an instant's SGT calendar date is a public holiday.

    Args:
        dt_utc: The UTC instant to check (the batch run's target time).

    Returns:
        True if the SGT calendar date of ``dt_utc`` is a key in
        :data:`HOLIDAYS`, else False. A carpark's cold-start window (before
        enough holiday-observations exist to train on) means this feature is
        expected to show no measurable effect on the first promoted model --
        that is normal, not a bug (Premise #10, extended).

    Note the mandatory join-across-midnight test case: an SGT date whose UTC
    instant falls on the *previous* calendar day (e.g. SGT 2026-01-01 00:30,
    a holiday, is UTC 2025-12-31 16:30) must still resolve to True -- the
    lookup always happens on the shifted SGT date, never the raw UTC date.
    """
    sgt_date = _to_sgt_naive(dt_utc).date()
    return sgt_date in HOLIDAYS
