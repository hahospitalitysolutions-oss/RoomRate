"""When a recurring scrape is due, on the owner's local wall clock.

The owner picks the hour in their own time (Greek time for every account
today). The schedule used to store it as a UTC hour and fire once
``frequency_hours`` had passed since the last run, which could only ever move
the run later: the spring daylight-saving change left 23 hours between two
08:00s, so the run slid to 09:00 until October; an hour moved earlier never
took effect; and a tick a few milliseconds earlier than the day before
waited for the next tick. The rules here read the local calendar instead:

* A daily schedule (``frequency_hours`` 24) runs once per local date, at the
  first tick at or after the chosen hour.
* Every N days (``frequency_hours`` 24·N; a frequency that is not a whole
  number of days rounds UP, so a schedule never runs more often than asked)
  runs on the first local date at least N days after the last run's.
* Under 24 hours, it runs at the chosen hour and every ``frequency_hours``
  after it on the same local day (hour 8, every 12 hours: 08:00 and 20:00),
  once per slot. Slots missed while the worker was down collapse into one run.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Athens"
# 08:00 local; the old default, 05:00 UTC, was 08:00 in a Greek summer.
DEFAULT_HOUR_LOCAL = 8


def schedule_zone(name: str | None) -> ZoneInfo:
    """The schedule's time zone; an unknown name falls back to Greek time."""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown schedule time zone %r; using %s", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_slot(day: date, hour: int, zone: ZoneInfo) -> datetime:
    """``hour``:00 on ``day`` in ``zone``, as a UTC instant.

    An hour the spring change skips (03:00 in Athens) resolves to the moment
    the clocks jump (04:00 local); an hour the autumn change repeats resolves
    to its first occurrence.
    """
    return datetime(day.year, day.month, day.day, hour, tzinfo=zone).astimezone(timezone.utc)


def local_date(moment: datetime, zone: ZoneInfo) -> date:
    """The calendar date in ``zone`` at an aware instant."""
    return moment.astimezone(zone).date()


def utc_hour_for_local_hour(hour_local: int, timezone_name: str | None, now: datetime) -> int:
    """The UTC hour that is ``hour_local`` in the zone today.

    Only for API clients and database readers that still speak ``hour_utc``;
    the due check never uses it.
    """
    zone = schedule_zone(timezone_name)
    return local_slot(local_date(now, zone), hour_local, zone).hour


def local_hour_for_utc_hour(hour_utc: int, timezone_name: str | None, now: datetime) -> int:
    """The local hour that ``hour_utc`` is in the zone today (legacy clients)."""
    zone = schedule_zone(timezone_name)
    today_utc = now.astimezone(timezone.utc).date()
    return datetime(today_utc.year, today_utc.month, today_utc.day, hour_utc, tzinfo=timezone.utc).astimezone(zone).hour


def is_schedule_due(
    *,
    now: datetime,
    last_run_at: datetime | None,
    hour_local: int,
    frequency_hours: int,
    timezone_name: str | None,
) -> bool:
    """True when the schedule should run at ``now`` (see the module docstring)."""
    zone = schedule_zone(timezone_name)
    today = local_date(now, zone)
    first_slot = local_slot(today, hour_local, zone)
    if now < first_slot:
        return False
    if frequency_hours >= 24:
        if last_run_at is None:
            return True
        days = -(-frequency_hours // 24)
        return today - local_date(last_run_at, zone) >= timedelta(days=days)
    latest_slot = first_slot
    hour = hour_local + frequency_hours
    while hour < 24:
        slot = local_slot(today, hour, zone)
        if slot > now:
            break
        latest_slot = slot
        hour += frequency_hours
    return last_run_at is None or last_run_at < latest_slot
