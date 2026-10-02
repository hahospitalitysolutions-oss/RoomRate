"""The schedule's due rule on the owner's local clock (Europe/Athens).

Athens changes clocks at 01:00 UTC on the last Sunday of March (03:00 -> 04:00
local, UTC+2 -> UTC+3) and of October (04:00 -> 03:00 local).
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api.services.schedule_timing import (
    is_schedule_due,
    local_hour_for_utc_hour,
    utc_hour_for_local_hour,
)

ATHENS = ZoneInfo("Europe/Athens")
SPRING_CHANGE = datetime(2027, 3, 28, 1, 0, tzinfo=timezone.utc)
AUTUMN_CHANGE = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)


def _athens(*args) -> datetime:
    """A wall-clock time in Athens as a UTC instant."""
    return datetime(*args, tzinfo=ATHENS).astimezone(timezone.utc)


def _due(now, last_run_at=None, *, hour_local=8, frequency_hours=24, timezone_name="Europe/Athens") -> bool:
    return is_schedule_due(
        now=now,
        last_run_at=last_run_at,
        hour_local=hour_local,
        frequency_hours=frequency_hours,
        timezone_name=timezone_name,
    )


def _simulate(start: datetime, days: int, **schedule) -> list[datetime]:
    """Tick every 15 minutes, a few milliseconds late by a varying amount, as APScheduler does."""
    runs: list[datetime] = []
    last_run_at = None
    for step in range(days * 24 * 4):
        now = start + step * timedelta(minutes=15) + timedelta(milliseconds=(7 * step) % 11)
        if _due(now, last_run_at, **schedule):
            runs.append(now)
            last_run_at = now
    return runs


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------


def test_daily_waits_for_the_local_hour_then_runs_once_per_local_date():
    ran = _athens(2026, 6, 12, 8, 7)

    assert not _due(_athens(2026, 6, 13, 7, 59), ran)
    assert _due(_athens(2026, 6, 13, 8, 0), ran)
    assert not _due(_athens(2026, 6, 13, 20, 0), _athens(2026, 6, 13, 8, 7))


def test_daily_never_run_starts_at_the_first_tick_after_the_hour():
    assert not _due(_athens(2026, 6, 13, 7, 30))
    assert _due(_athens(2026, 6, 13, 15, 0))


def test_daily_after_the_spring_change_still_runs_at_08_local():
    """08:00 is 06:00 UTC before the change and 05:00 UTC after: only 23 hours apart."""
    ran = _athens(2027, 3, 27, 8, 7)

    assert _due(_athens(2027, 3, 28, 8, 7), ran)
    assert _athens(2027, 3, 28, 8, 7) - ran == timedelta(hours=23)


def test_daily_after_the_autumn_change_does_not_run_an_hour_early():
    ran = _athens(2026, 10, 24, 8, 7)

    assert not _due(_athens(2026, 10, 25, 7, 7), ran)  # already 24 hours later
    assert _due(_athens(2026, 10, 25, 8, 7), ran)


def test_daily_moving_the_hour_earlier_takes_effect_the_next_day():
    """The old "24 hours since the last run" rule kept an 08:00 schedule at 08:00 forever."""
    ran = _athens(2026, 6, 12, 8, 7)

    assert _due(_athens(2026, 6, 13, 6, 7), ran, hour_local=6)


def test_daily_moving_the_hour_later_does_not_run_twice_that_day():
    ran_this_morning = _athens(2026, 6, 13, 8, 7)

    assert not _due(_athens(2026, 6, 13, 20, 7), ran_this_morning, hour_local=20)
    assert _due(_athens(2026, 6, 14, 20, 7), ran_this_morning, hour_local=20)


@pytest.mark.parametrize("start", [SPRING_CHANGE, AUTUMN_CHANGE], ids=["spring", "autumn"])
def test_daily_ticks_over_two_weeks_run_at_the_first_tick_after_08_local_every_day(start):
    """Across a clock change and tick jitter: no drift, no gap, no double run."""
    runs = _simulate(start - timedelta(days=7) + timedelta(minutes=7), 14, hour_local=8)

    local_runs = [run.astimezone(ATHENS) for run in runs]
    assert len(local_runs) == 14
    assert {(run.hour, run.minute) for run in local_runs} == {(8, 7)}
    dates = [run.date() for run in local_runs]
    assert dates == [dates[0] + timedelta(days=offset) for offset in range(14)]


# ---------------------------------------------------------------------------
# Every N days
# ---------------------------------------------------------------------------


def test_every_two_days_counts_local_dates():
    ran = _athens(2026, 6, 10, 8, 7)

    assert not _due(_athens(2026, 6, 11, 9, 0), ran, frequency_hours=48)
    assert not _due(_athens(2026, 6, 12, 7, 59), ran, frequency_hours=48)
    assert _due(_athens(2026, 6, 12, 8, 0), ran, frequency_hours=48)


def test_a_frequency_between_whole_days_rounds_up_never_running_more_often():
    ran = _athens(2026, 6, 10, 8, 7)

    assert not _due(_athens(2026, 6, 11, 20, 7), ran, frequency_hours=36)
    assert _due(_athens(2026, 6, 12, 8, 7), ran, frequency_hours=36)


# ---------------------------------------------------------------------------
# Under a day
# ---------------------------------------------------------------------------


def test_every_twelve_hours_runs_at_the_hour_and_twelve_hours_later_once_each():
    morning = _athens(2026, 6, 13, 8, 7)

    assert not _due(_athens(2026, 6, 13, 19, 59), morning, frequency_hours=12)
    assert _due(_athens(2026, 6, 13, 20, 0), morning, frequency_hours=12)
    evening = _athens(2026, 6, 13, 20, 7)
    assert not _due(_athens(2026, 6, 13, 23, 30), evening, frequency_hours=12)
    assert not _due(_athens(2026, 6, 14, 7, 59), evening, frequency_hours=12)
    assert _due(_athens(2026, 6, 14, 8, 0), evening, frequency_hours=12)


def test_every_twelve_hours_across_the_spring_change_keeps_08_and_20_local():
    runs = _simulate(SPRING_CHANGE - timedelta(days=3) + timedelta(minutes=7), 6, hour_local=8, frequency_hours=12)

    local_runs = [run.astimezone(ATHENS) for run in runs]
    assert len(local_runs) == 12
    assert {(run.hour, run.minute) for run in local_runs} == {(8, 7), (20, 7)}


def test_missed_slots_collapse_into_one_run():
    """The worker was down from 07:00 to 21:00: one run for the 20:00 slot, not two."""
    ran_yesterday_evening = _athens(2026, 6, 12, 20, 7)
    back_up = _athens(2026, 6, 13, 21, 0)

    assert _due(back_up, ran_yesterday_evening, frequency_hours=12)
    assert not _due(back_up + timedelta(minutes=15), back_up, frequency_hours=12)


# ---------------------------------------------------------------------------
# Hours the clock change skips or repeats, and the zone
# ---------------------------------------------------------------------------


def test_an_hour_skipped_by_the_spring_change_runs_when_the_clocks_jump():
    """03:00 does not exist on 28 March 2027 in Athens: the run waits for 04:00 local."""
    ran = _athens(2027, 3, 27, 3, 7)

    assert not _due(SPRING_CHANGE - timedelta(minutes=1), ran, hour_local=3)
    assert _due(SPRING_CHANGE, ran, hour_local=3)


def test_an_hour_repeated_by_the_autumn_change_runs_once():
    first_three_oclock = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)  # 03:00 EEST
    second_three_oclock = AUTUMN_CHANGE  # 03:00 EET

    assert _due(first_three_oclock, _athens(2026, 10, 24, 3, 7), hour_local=3)
    assert not _due(second_three_oclock + timedelta(minutes=30), first_three_oclock, hour_local=3)


def test_an_unknown_time_zone_falls_back_to_greek_time():
    now = _athens(2026, 6, 13, 8, 7)

    assert _due(now, timezone_name="Mars/Olympus_Mons")
    assert not _due(now - timedelta(hours=1), timezone_name=None)


def test_hour_conversions_for_legacy_clients_use_todays_offset():
    summer = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)

    assert utc_hour_for_local_hour(8, "Europe/Athens", summer) == 5
    assert utc_hour_for_local_hour(8, "Europe/Athens", winter) == 6
    assert utc_hour_for_local_hour(1, "Europe/Athens", summer) == 22
    assert local_hour_for_utc_hour(5, "Europe/Athens", summer) == 8
    assert local_hour_for_utc_hour(5, "Europe/Athens", winter) == 7
    assert local_hour_for_utc_hour(22, "Europe/Athens", summer) == 1
