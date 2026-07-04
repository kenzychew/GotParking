"""Tests for the SGT time helper (api/_lib/sg_time.py).

Covers the two mandatory pinned cases from the design doc's Test
Requirements (SGT boundary + holiday join-across-midnight), plus
supporting coverage for slot-of-day arithmetic and naive-datetime input.
"""

from __future__ import annotations

from datetime import datetime, timezone

from _lib.sg_time import HOLIDAYS, is_holiday, sgt_parts


class TestSgtParts:
    """Tests for sgt_parts (day-of-week, slot-of-day)."""

    def test_sat_0200_sgt_equals_fri_1800_utc_boundary(self) -> None:
        """Mandatory boundary case: Fri 18:00 UTC is Sat 02:00 SGT (dow=5)."""
        fri_1800_utc = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)
        dow, slot_of_day = sgt_parts(fri_1800_utc)

        assert dow == 5  # Saturday (Python: Monday=0 .. Sunday=6)
        assert slot_of_day == 8  # 02:00 -> hour 2 * 4 slots/hour

    def test_monday_midnight_sgt(self) -> None:
        """Sun 16:00 UTC is Mon 00:00 SGT -- dow rolls over to Monday."""
        sun_1600_utc = datetime(2026, 7, 5, 16, 0, tzinfo=timezone.utc)
        dow, slot_of_day = sgt_parts(sun_1600_utc)

        assert dow == 0  # Monday
        assert slot_of_day == 0

    def test_slot_of_day_quarter_hour_buckets(self) -> None:
        """slot_of_day advances by 1 per 15-minute bucket, 96 per day."""
        # 2026-07-06 is a Monday; 10:45 SGT -> UTC 02:45 same day.
        dt_utc = datetime(2026, 7, 6, 2, 45, tzinfo=timezone.utc)
        dow, slot_of_day = sgt_parts(dt_utc)

        assert dow == 0
        assert slot_of_day == 10 * 4 + 3  # 10:45 -> slot 43

    def test_last_slot_of_day(self) -> None:
        """23:45 SGT is the last (96th) slot of the day, index 95."""
        # 2026-07-06 23:45 SGT -> UTC 15:45 same day.
        dt_utc = datetime(2026, 7, 6, 15, 45, tzinfo=timezone.utc)
        _, slot_of_day = sgt_parts(dt_utc)

        assert slot_of_day == 95

    def test_naive_datetime_treated_as_utc(self) -> None:
        """A naive datetime (no tzinfo) is assumed to already be UTC."""
        naive = datetime(2026, 7, 3, 18, 0)  # no tzinfo
        aware = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)

        assert sgt_parts(naive) == sgt_parts(aware)

    def test_non_utc_tzinfo_is_normalized(self) -> None:
        """A tz-aware datetime in a non-UTC offset is normalized correctly."""
        from datetime import timedelta

        plus_five = timezone(timedelta(hours=5))
        # 13:00 UTC+5 == 08:00 UTC == 16:00 SGT.
        dt = datetime(2026, 7, 6, 13, 0, tzinfo=plus_five)

        dow, slot_of_day = sgt_parts(dt)

        assert dow == 0  # Monday, unchanged date
        assert slot_of_day == 16 * 4


class TestIsHoliday:
    """Tests for is_holiday, including the mandatory join-across-midnight case."""

    def test_holiday_join_utc_datetime_falls_day_before(self) -> None:
        """SGT 2026-01-01 (New Year's Day) at 00:30 is UTC 2025-12-31 16:30.

        The lookup must key off the SHIFTED SGT calendar date, not the raw
        UTC date -- a UTC-date-only join would miss this holiday entirely.
        """
        new_years_eve_utc = datetime(2025, 12, 31, 16, 30, tzinfo=timezone.utc)

        assert is_holiday(new_years_eve_utc) is True

    def test_non_holiday_returns_false(self) -> None:
        """An ordinary weekday is not a holiday."""
        ordinary_day = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)  # Mon 11:00 SGT

        assert is_holiday(ordinary_day) is False

    def test_holiday_at_utc_date_matching_sgt_date(self) -> None:
        """A holiday well within the SGT day (not near the UTC boundary)."""
        national_day_afternoon_sgt = datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc)  # 14:00 SGT

        assert is_holiday(national_day_afternoon_sgt) is True

    def test_day_after_holiday_is_not_a_holiday(self) -> None:
        """The calendar day immediately after a holiday is not itself one."""
        day_after_christmas_sgt = datetime(2026, 12, 26, 4, 0, tzinfo=timezone.utc)  # 12:00 SGT

        assert is_holiday(day_after_christmas_sgt) is False

    def test_holidays_dict_covers_2020_through_2026(self) -> None:
        """The static table covers every required year (2020-2026 minimum)."""
        years_present = {d.year for d in HOLIDAYS}

        assert set(range(2020, 2027)).issubset(years_present)

    def test_holidays_dict_is_ascii_only(self) -> None:
        """Holiday name strings must stay within the project's ASCII-only rule."""
        for name in HOLIDAYS.values():
            assert name.isascii(), f"non-ASCII character in holiday name: {name!r}"
