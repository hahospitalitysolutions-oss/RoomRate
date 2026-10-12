"""Flight demand for island destinations, from AeroDataBox's scheduled arrivals.

Airport-level (shared by every account on the island):

1. Airports: the island each active property sits on (greek_islands, by its
   own coordinates) and that island's airport. Islands without one, the
   mainland and road-reached islands have no flight signal.
2. Collection: daily, the plan's travel weekdays and windows over the next
   FLIGHT_DEMAND_HORIZON_DAYS, each re-read every FLIGHT_DEMAND_REFRESH_DAYS,
   nearest dates first. AeroDataBox bills units, so the month's budget is
   spread over its remaining days, and the run stops when the plan reports
   too few units left.
3. Signal: pickup, the same travel day's arrivals now against an earlier
   look. Airlines add frequencies (and charters) where seats sell and drop
   them where they do not; a schedule read once says only how busy the
   airport is, which is the season, not demand.

Rules, not a trained model; the counts are kept for one later.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from api.schemas.demand import FlightDemandSignal
from api.services.aerodatabox_client import (
    AeroDataBoxAuthError,
    AeroDataBoxError,
    Arrival,
    ArrivalsWindow,
    QuotaExhausted,
    slim_payload,
)
from api.services.ferry_demand_service import trimmed_payload
from api.services.flight_plan import FlightPlan
from api.services.greek_islands import island_at

logger = logging.getLogger(__name__)

# Bump when parsing changes: observations keep the version they were read with.
PARSER_VERSION = "adb-2026-10-12"

# Failed calls in a row after which a run stops: the rest would fail alike.
MAX_CONSECUTIVE_FAILURES = 3

# A check-in that is not a collected day (the free plan reads Fridays and
# Saturdays) is read from the nearest collected days this close.
SIGNAL_REACH_DAYS = 3
# Pickup: the same travel day and window, now against the oldest look this
# many days before it.
PICKUP_MIN_DAYS = 3
PICKUP_MAX_DAYS = 21
# Flights added or dropped since the earlier look: a share and at least two
# flights, so one charter more on a quiet day is not a demand change.
HIGH_PICKUP_PCT = 8.0
LOW_PICKUP_PCT = -8.0
MIN_FLIGHTS_CHANGED = 2
TOP_ORIGINS = 3

WEEKDAYS = ("Δευ", "Τρί", "Τετ", "Πέμ", "Παρ", "Σάβ", "Κυρ")
LEVEL_LABELS = {"high": "Υψηλή", "normal": "Κανονική", "low": "Χαμηλή"}


class FlightClient(Protocol):
    def arrivals(self, iata: str, travel_date: date, start_hour: int, end_hour: int) -> ArrivalsWindow: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class PlannedCall:
    airport_iata: str
    travel_date: date
    window: tuple[int, int]


@dataclass
class FlightCollectionReport:
    airports: list[str]
    due: int = 0
    allowance: int = 0
    used_before: int = 0
    calls: int = 0
    saved: int = 0
    failed: int = 0
    aborted: bool = False
    # The plan reported (429) or showed (header) no units left for a call.
    out_of_units: bool = False
    units_remaining: int | None = None


def airport_for(latitude: float | None, longitude: float | None) -> tuple[str, str] | None:
    """(IATA, island) of the airport serving a point; None off an island with one."""
    island = island_at(latitude, longitude)
    if island is None or island.airport is None:
        return None
    return island.airport, island.label


def month_start(today: date) -> date:
    return today.replace(day=1)


def days_left_in_month(today: date) -> int:
    """Days from ``today`` to the month's end, today included."""
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (next_month - today).days


def daily_allowance(today: date, plan: FlightPlan, used_calls: int, units_per_call: int) -> int:
    """Calls today: the month's remaining budget over its remaining days, rounded up.

    Rounded up so leftover units are spent before the month ends; never more
    than the remaining budget (ceil(x / d) <= x) or the plan's per-run cap.
    """
    remaining = max(plan.monthly_units // max(units_per_call, 1) - used_calls, 0)
    return min(math.ceil(remaining / days_left_in_month(today)), plan.max_calls_per_run)


def travel_dates(today: date, plan: FlightPlan, horizon_days: int) -> list[date]:
    """The plan's weekdays from tomorrow to the horizon."""
    days = (today + timedelta(days=offset) for offset in range(1, max(horizon_days, 0) + 1))
    return [day for day in days if day.weekday() in plan.weekdays]


def plan_calls(
    today: date,
    airports: Sequence[str],
    plan: FlightPlan,
    *,
    horizon_days: int,
    refresh_days: int,
    newest_looks: dict[tuple[str, date, int], date],
) -> list[PlannedCall]:
    """Windows not read in ``refresh_days``, nearest travel date first."""
    due = []
    for travel_date in travel_dates(today, plan, horizon_days):
        for airport in airports:
            for window in plan.windows:
                newest = newest_looks.get((airport, travel_date, window[0]))
                if newest is None or (today - newest).days >= refresh_days:
                    due.append(PlannedCall(airport, travel_date, window))
    return due


def aggregate_arrivals(arrivals: Sequence[Arrival]) -> dict[str, Any]:
    """Counts of one window: flights, airlines, and where they come from."""
    origins: dict[str, dict[str, Any]] = {}
    for arrival in arrivals:
        key = arrival.origin_iata or arrival.origin_name or "?"
        entry = origins.setdefault(key, {"name": arrival.origin_name or key, "flights": 0})
        entry["flights"] += 1
    return {
        "arrivals": len(arrivals),
        "airlines": len({arrival.airline for arrival in arrivals if arrival.airline}),
        "origins": origins,
    }


WindowKey = tuple[date, int]


def _newest(rows: Iterable[dict[str, Any]], today: date, max_age_days: int) -> dict[WindowKey, dict[str, Any]]:
    """The newest look at each (travel date, window), if recent enough.

    A window overlapping one read more recently on the same day is left out:
    after FLIGHT_DEMAND_WINDOWS changes (08-20 to 00-12,12-24 on Pro) the old
    window's last look would count the same flights twice.
    """
    newest: dict[WindowKey, dict[str, Any]] = {}
    for row in rows:
        key = (row["travel_date"], row["window_start_hour"])
        if key not in newest or row["observed_on"] > newest[key]["observed_on"]:
            newest[key] = row
    kept: dict[WindowKey, dict[str, Any]] = {}
    for key, row in sorted(newest.items(), key=lambda item: item[1]["observed_on"], reverse=True):
        if (today - row["observed_on"]).days > max_age_days:
            continue
        overlaps = any(
            other_date == key[0]
            and row["window_start_hour"] < other["window_end_hour"]
            and other["window_start_hour"] < row["window_end_hour"]
            for (other_date, _), other in kept.items()
        )
        if not overlaps:
            kept[key] = row
    return kept


def _earlier(rows: Iterable[dict[str, Any]], newest: dict[WindowKey, dict[str, Any]]) -> dict[WindowKey, dict[str, Any]]:
    """Per (travel date, window): the oldest look PICKUP_MIN_DAYS..PICKUP_MAX_DAYS before the newest."""
    earlier: dict[WindowKey, dict[str, Any]] = {}
    for row in rows:
        key = (row["travel_date"], row["window_start_hour"])
        if key not in newest:
            continue
        age = (newest[key]["observed_on"] - row["observed_on"]).days
        if PICKUP_MIN_DAYS <= age <= PICKUP_MAX_DAYS and (
            key not in earlier or row["observed_on"] < earlier[key]["observed_on"]
        ):
            earlier[key] = row
    return earlier


def read_dates(collected: Iterable[date], check_in: date) -> list[date]:
    """The check-in when collected, else the nearest collected days within SIGNAL_REACH_DAYS."""
    collected = set(collected)
    if check_in in collected:
        return [check_in]
    near = [day for day in collected if abs((day - check_in).days) <= SIGNAL_REACH_DAYS]
    if not near:
        return []
    closest = min(abs((day - check_in).days) for day in near)
    return sorted(day for day in near if abs((day - check_in).days) == closest)


def _top_origins(rows: Iterable[dict[str, Any]]) -> list[str]:
    flights: Counter[str] = Counter()
    for row in rows:
        for key, entry in (row.get("origins") or {}).items():
            if isinstance(entry, dict):
                flights[str(entry.get("name") or key)] += int(entry.get("flights") or 0)
    return [name for name, _ in flights.most_common(TOP_ORIGINS)]


def _hours(windows: Iterable[tuple[int, int]]) -> str:
    """'08:00–20:00'; empty when the windows cover the whole day."""
    ordered = sorted(set(windows))
    if ordered and ordered[0][0] == 0 and ordered[-1][1] == 24 and all(
        end == start for (_, end), (start, _) in zip(ordered, ordered[1:])
    ):
        return ""
    return ", ".join(f"{start:02d}:00–{end:02d}:00" for start, end in ordered)


def build_flight_demand_signal(
    rows: Sequence[dict[str, Any]],
    check_in: date,
    today: date,
    *,
    airport_iata: str,
    island: str,
    max_age_days: int,
) -> FlightDemandSignal:
    """Demand level from the scheduled arrivals at the island's airport around ``check_in``.

    ``rows`` are every look at the airport's travel dates over the pickup
    look-back (FlightRepository.observation_history). ``unknown`` until the
    same days have been read twice, PICKUP_MIN_DAYS or more apart.
    """
    base = {"check_in": check_in, "airport_iata": airport_iata, "island": island}
    newest = _newest(rows, today, max_age_days)
    dates = read_dates({travel_date for travel_date, _ in newest}, check_in)
    if not dates:
        return FlightDemandSignal(
            level="unknown",
            **base,
            summary="Δεν υπάρχουν ακόμη πρόσφατα δεδομένα πτήσεων για αυτές τις ημερομηνίες.",
        )
    in_window = [row for (travel_date, _), row in sorted(newest.items()) if travel_date in dates]
    earlier = _earlier(rows, newest)
    arrivals = sum(row["arrivals"] for row in in_window)
    paired = [
        (row, earlier[(row["travel_date"], row["window_start_hour"])])
        for row in in_window
        if (row["travel_date"], row["window_start_hour"]) in earlier
    ]
    now = sum(row["arrivals"] for row, _ in paired)
    before = sum(old["arrivals"] for _, old in paired)
    since = min((old["observed_on"] for _, old in paired), default=None)
    added = now - before if paired else None
    change = round(100 * (now / before - 1), 1) if paired and before else None

    if change is None:
        level = "unknown"
    elif change >= HIGH_PICKUP_PCT and added >= MIN_FLIGHTS_CHANGED:
        level = "high"
    elif change <= LOW_PICKUP_PCT and added <= -MIN_FLIGHTS_CHANGED:
        level = "low"
    else:
        level = "normal"

    origins = _top_origins(in_window)
    airlines = max((row["airlines"] for row in in_window), default=0)
    days_label = "–".join(f"{WEEKDAYS[day.weekday()]} {day:%d/%m}" for day in dates)
    hours = _hours((row["window_start_hour"], row["window_end_hour"]) for row in in_window)
    summary = f"Αφίξεις στο αεροδρόμιο {airport_iata} ({island}) {days_label}"
    summary += f" {hours}: " if hours else ": "
    summary += f"{arrivals} πτήσεις"
    if len(in_window) == 1 and airlines:
        summary += f" από {airlines} αεροπορικές"
    if origins:
        summary += f", κυρίως από {', '.join(origins)}"
    summary += "."
    if check_in not in dates:
        summary += f" Η {check_in:%d/%m} δεν συλλέγεται: διαβάστηκε η κοντινότερη μέρα."
    if paired and since is not None:
        summary += f" Από τις {since:%d/%m}: {added:+d} πτήσεις" + (f" ({change:+.0f}%)." if change is not None else ".")
    if level == "unknown":
        summary += (
            " Δεν υπήρχαν πτήσεις στην προηγούμενη ματιά."
            if paired
            else " Η σύγκριση με προηγούμενη ματιά φαίνεται όταν ξαναδιαβαστούν οι ίδιες μέρες (σε περίπου μία εβδομάδα)."
        )
    else:
        summary += f" Ζήτηση πτήσεων: {LEVEL_LABELS[level]}."
    return FlightDemandSignal(
        level=level,
        **base,
        travel_dates=dates,
        arrivals=arrivals,
        airlines=airlines,
        arrivals_before=before if paired else None,
        flights_added=added,
        arrivals_change_pct=change,
        compared_with=since,
        top_origins=origins,
        observed_on=max(row["observed_on"] for row in in_window),
        summary=summary,
    )


def flight_daily_levels(
    rows: Sequence[dict[str, Any]], today: date, *, airport_iata: str, island: str, max_age_days: int
) -> list[dict[str, Any]]:
    """Per collected travel date: arrivals over its windows, pickup and the level of a check-in that day."""
    days = []
    for travel_date in sorted({travel_date for travel_date, _ in _newest(rows, today, max_age_days)}):
        signal = build_flight_demand_signal(
            rows, travel_date, today, airport_iata=airport_iata, island=island, max_age_days=max_age_days
        )
        days.append(
            {
                "travel_date": travel_date,
                "arrivals": signal.arrivals,
                "airlines": signal.airlines,
                "arrivals_change_pct": signal.arrivals_change_pct,
                "flights_added": signal.flights_added,
                "compared_with": signal.compared_with,
                "top_origins": signal.top_origins,
                "level": signal.level,
            }
        )
    return days


def flight_history_window(today: date, check_in: date | None, horizon_days: int, max_age_days: int) -> dict[str, date]:
    """The observation_history arguments: around the check-in, or the whole horizon."""
    return {
        "date_from": check_in - timedelta(days=SIGNAL_REACH_DAYS) if check_in else today,
        "date_to": check_in + timedelta(days=SIGNAL_REACH_DAYS) if check_in else today + timedelta(days=horizon_days),
        "observed_since": today - timedelta(days=PICKUP_MAX_DAYS + max_age_days),
    }


def load_flight_signal(
    repository: Any,
    latitude: float | None,
    longitude: float | None,
    check_in: date,
    *,
    today: date,
    horizon_days: int,
    max_age_days: int,
) -> FlightDemandSignal | None:
    """The flight signal for a check-in; None off an island airport or before any collection."""
    airport = airport_for(latitude, longitude)
    if airport is None:
        return None
    iata, island = airport
    rows = repository.observation_history(iata, **flight_history_window(today, check_in, horizon_days, max_age_days))
    if not rows:
        return None
    return build_flight_demand_signal(
        rows, check_in, today, airport_iata=iata, island=island, max_age_days=max_age_days
    )


class FlightDemandService:
    def __init__(
        self,
        repository: Any,
        client_factory: Callable[[], FlightClient],
        plan: FlightPlan,
        *,
        horizon_days: int = 56,
        refresh_days: int = 7,
        units_per_call: int = 2,
    ):
        self.repository = repository
        self.client_factory = client_factory
        self.plan = plan
        self.horizon_days = horizon_days
        self.refresh_days = refresh_days
        self.units_per_call = units_per_call

    def airports(self) -> list[str]:
        """The airports of the islands active properties sit on."""
        found = {airport_for(point["latitude"], point["longitude"]) for point in self.repository.property_points()}
        return sorted({airport[0] for airport in found if airport is not None})

    def collect(self, today: date, *, max_calls: int | None = None) -> FlightCollectionReport:
        """Read the due windows within today's share of the month's units.

        ``max_calls`` replaces the day's share (a manual first fill), never
        the month's budget.
        """
        report = FlightCollectionReport(airports=self.airports())
        if not report.airports:
            return report
        report.used_before = self.repository.calls_since(month_start(today))
        if max_calls is None:
            report.allowance = daily_allowance(today, self.plan, report.used_before, self.units_per_call)
        else:
            budget = max(self.plan.monthly_units // max(self.units_per_call, 1) - report.used_before, 0)
            report.allowance = min(max(max_calls, 0), budget)
        due = plan_calls(
            today,
            report.airports,
            self.plan,
            horizon_days=self.horizon_days,
            refresh_days=self.refresh_days,
            newest_looks=self.repository.newest_looks(report.airports, today + timedelta(days=1)),
        )
        report.due = len(due)
        if not due or report.allowance <= 0:
            return report

        client = self.client_factory()
        consecutive_failures = 0
        try:
            for call in due[: report.allowance]:
                report.calls += 1
                start, end = call.window
                try:
                    window = client.arrivals(call.airport_iata, call.travel_date, start, end)
                except QuotaExhausted as exc:
                    report.out_of_units = True
                    logger.warning("Flight collection stopped: %s", exc)
                    break
                except AeroDataBoxAuthError as exc:
                    report.failed += 1
                    report.aborted = True
                    logger.error("Flight collection stopped: %s", exc)
                    break
                except AeroDataBoxError as exc:
                    report.failed += 1
                    consecutive_failures += 1
                    logger.warning("Flight arrivals %s on %s failed: %s", call.airport_iata, call.travel_date, exc)
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        report.aborted = True
                        logger.error("Flight collection stopped after %s failures in a row", consecutive_failures)
                        break
                    continue
                consecutive_failures = 0
                report.units_remaining = window.units_remaining
                self.repository.upsert_observation(
                    airport_iata=call.airport_iata,
                    travel_date=call.travel_date,
                    window_start_hour=start,
                    window_end_hour=end,
                    observed_on=today,
                    units_remaining=window.units_remaining,
                    raw_payload=trimmed_payload(slim_payload(window.payload)),
                    parser_version=PARSER_VERSION,
                    **aggregate_arrivals(window.arrivals),
                )
                report.saved += 1
                if window.units_remaining is not None and window.units_remaining < self.units_per_call:
                    report.out_of_units = True
                    logger.warning("Flight collection stopped: %s unit(s) left on the plan", window.units_remaining)
                    break
        finally:
            client.close()
        return report
