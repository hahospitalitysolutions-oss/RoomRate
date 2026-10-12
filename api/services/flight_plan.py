"""What the flight collection reads on each AeroDataBox plan.

FLIGHT_DEMAND_PLAN picks a preset; FLIGHT_DEMAND_MONTHLY_UNITS,
FLIGHT_DEMAND_WEEKDAYS, FLIGHT_DEMAND_WINDOWS and
FLIGHT_DEMAND_MAX_CALLS_PER_RUN override one part of it. No imports from
the app: api/config.py validates the settings with these parsers.

A call costs 2 units (one airport, one window of at most 12 hours). One
island airport, one 12-hour window, Fridays and Saturdays for 8 weeks,
re-read weekly: about 70 calls (140 units) a month, so the free plan's 400
units cover two airports. Pro (5,000 units) reads every day, both halves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# AeroDataBox answers at most 12 hours per call.
MAX_WINDOW_HOURS = 12


@dataclass(frozen=True)
class FlightPlan:
    name: str
    # Units the collection may spend in a calendar month; the rest is left for
    # manual checks (scripts/probe_aerodatabox.py).
    monthly_units: int
    # Travel weekdays read, 0 = Monday … 6 = Sunday.
    weekdays: tuple[int, ...]
    # Local-time windows (start hour, end hour) read for each travel date.
    windows: tuple[tuple[int, int], ...]
    max_calls_per_run: int


FLIGHT_PLANS: dict[str, FlightPlan] = {
    # 400 units a month: arrivals 08:00–20:00 (most of the day's flights) of
    # Fridays and Saturdays, when stays start.
    "free": FlightPlan("free", monthly_units=380, weekdays=(4, 5), windows=((8, 20),), max_calls_per_run=20),
    # 5,000 units a month ($8): every day, the whole day.
    "pro": FlightPlan(
        "pro", monthly_units=4800, weekdays=(0, 1, 2, 3, 4, 5, 6), windows=((0, 12), (12, 24)), max_calls_per_run=200
    ),
}


def parse_weekdays(value: str) -> tuple[int, ...]:
    """'4,5' → (4, 5); 0 = Monday … 6 = Sunday."""
    days = sorted({int(part) for part in re.split(r"[,\s]+", value.strip()) if part})
    if not days or any(day < 0 or day > 6 for day in days):
        raise ValueError(f"weekdays must be 0 (Monday) … 6 (Sunday), comma separated: {value!r}")
    return tuple(days)


def parse_windows(value: str) -> tuple[tuple[int, int], ...]:
    """'08-20' → ((8, 20),); '00-12,12-24' → ((0, 12), (12, 24)). At most 12 hours each."""
    windows = []
    for part in re.split(r"[,\s]+", value.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", part)
        if not match:
            raise ValueError(f"window must look like 08-20: {part!r}")
        start, end = int(match.group(1)), int(match.group(2))
        if not 0 <= start < end <= 24 or end - start > MAX_WINDOW_HOURS:
            raise ValueError(f"window {part!r} must be within 00-24 and at most {MAX_WINDOW_HOURS} hours")
        windows.append((start, end))
    if not windows:
        raise ValueError("at least one window is needed, e.g. 08-20")
    ordered = tuple(sorted(set(windows)))
    for (_, end), (start, _) in zip(ordered, ordered[1:]):
        if start < end:
            raise ValueError(f"windows overlap: {value!r}")
    return ordered


def resolve_plan(
    name: str,
    *,
    monthly_units: int | None = None,
    weekdays: str = "",
    windows: str = "",
    max_calls_per_run: int | None = None,
) -> FlightPlan:
    """The named preset with the overrides that are set."""
    key = name.strip().lower()
    if key not in FLIGHT_PLANS:
        raise ValueError(f"FLIGHT_DEMAND_PLAN must be one of {', '.join(FLIGHT_PLANS)}: {name!r}")
    plan = FLIGHT_PLANS[key]
    changes: dict[str, object] = {}
    if monthly_units is not None:
        changes["monthly_units"] = monthly_units
    if weekdays.strip():
        changes["weekdays"] = parse_weekdays(weekdays)
    if windows.strip():
        changes["windows"] = parse_windows(windows)
    if max_calls_per_run is not None:
        changes["max_calls_per_run"] = max_calls_per_run
    return replace(plan, **changes) if changes else plan
