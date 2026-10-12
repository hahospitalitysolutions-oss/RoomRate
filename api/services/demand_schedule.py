"""When the worker collects ferries and flights: the owner's hour, or «Συλλογή τώρα».

The worker ticks every few minutes (api/scheduler.py run_demand_tick). A
scope is due once a Greek day: ferries per destination, flights once for
every collected airport. It is due when the hour an enabled property there
chose has come, or when the owner asked for a collection after the scope's
last run. roomrate_demand_runs records the day's run, so a tick never
collects a scope twice a day.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from api.services.flight_demand_service import airport_for
from api.services.greek_islands import island_at

# Ferries and flights are read for Greek islands only.
DEMAND_TIMEZONE = ZoneInfo("Europe/Athens")
FLIGHTS_SCOPE = "all"


@dataclass
class DueScopes:
    ferry_destinations: set[str] = field(default_factory=set)
    flights: bool = False


def greek_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(DEMAND_TIMEZONE)


def _requested(prop: dict[str, Any], last_started: datetime | None) -> bool:
    requested_at = prop.get("collect_requested_at")
    return requested_at is not None and (last_started is None or requested_at > last_started)


def due_scopes(
    properties: Iterable[dict[str, Any]],
    *,
    ran_today: set[tuple[str, str]],
    last_started: dict[tuple[str, str], datetime],
    now_local: datetime,
    ferries_on: bool = True,
    flights_on: bool = True,
    force: bool = False,
) -> DueScopes:
    """The scopes to collect now, from the active properties' settings.

    ``force`` treats every hour as come (a manual run of the scripts).
    """
    due = DueScopes()
    flights_key = ("flights", FLIGHTS_SCOPE)
    for prop in properties:
        hour_reached = force or now_local.hour >= int(prop["hour_local"])
        destination = prop.get("canonical_destination") or ""
        if ferries_on and prop["ferries_enabled"] and destination:
            key = ("ferries", destination)
            if key not in ran_today and (hour_reached or _requested(prop, last_started.get(key))):
                due.ferry_destinations.add(destination)
        if (
            flights_on
            and prop["flights_enabled"]
            and flights_key not in ran_today
            and airport_for(prop.get("latitude"), prop.get("longitude")) is not None
            and (hour_reached or _requested(prop, last_started.get(flights_key)))
        ):
            due.flights = True
    return due


def next_collection_at(hour_local: int, ran_today: bool, now_local: datetime) -> datetime:
    """The next daily collection, Greek time: today at the hour, now when it has passed, else tomorrow."""
    if not ran_today:
        today_at = now_local.replace(hour=hour_local, minute=0, second=0, microsecond=0)
        return max(today_at, now_local.replace(second=0, microsecond=0))
    day = (now_local + timedelta(days=1)).date()
    return datetime(day.year, day.month, day.day, hour_local, tzinfo=DEMAND_TIMEZONE)


def demand_availability(latitude: float | None, longitude: float | None) -> tuple[str | None, str | None]:
    """(island, airport IATA) of a property: None when off an island / without an airport."""
    island = island_at(latitude, longitude)
    airport = airport_for(latitude, longitude)
    return (island.label if island else None, airport[0] if airport else None)

