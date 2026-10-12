"""Run the flight demand collection once, now, or print what was collected.

The worker runs it by itself while the API is up (daily at 05:30 UTC =
08:30 Greek time). It needs AERODATABOX_API_KEY in the project's .env; the
key is never printed. From the project folder:

    python scripts/run_flight_demand.py               # today's share of the month's units
    python scripts/run_flight_demand.py --calls 16    # a first fill: up to 16 calls now
    python scripts/run_flight_demand.py --show        # arrivals by date, units spent, no calls
    python scripts/run_flight_demand.py --day 2026-10-17   # each flight that day, no calls

Every call costs AeroDataBox units (2 each). FLIGHT_DEMAND_PLAN=free keeps
the month within 380 of the free plan's 400 units: Fridays and Saturdays,
arrivals 08:00–20:00, 8 weeks ahead, each re-read weekly. With the API
closed, a scheduled task runs it daily on Windows (PowerShell, once):

    schtasks /Create /TN "RoomRate flights" /SC DAILY /ST 08:35 /TR "C:\\RoomRate\\.venv\\Scripts\\python.exe C:\\RoomRate\\scripts\\run_flight_demand.py"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# The settings read ./.env: a scheduled task starts elsewhere (System32).
os.chdir(PROJECT_ROOT)

from sqlalchemy import text  # noqa: E402

from api.config import settings  # noqa: E402
from api.db import get_engine  # noqa: E402
from api.repositories.flight_repository import FlightRepository  # noqa: E402
from api.scheduler import run_flight_demand_collection  # noqa: E402
from api.services.flight_demand_service import (  # noqa: E402
    WEEKDAYS,
    airport_for,
    flight_daily_levels,
    flight_history_window,
    month_start,
)

LEVELS = {"high": "ΥΨΗΛΗ", "normal": "κανονική", "low": "χαμηλή", "unknown": "—"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calls", type=int, help="calls now instead of the day's share (within the month's budget)")
    parser.add_argument("--show", action="store_true", help="print arrivals by date and the units spent, no calls")
    parser.add_argument("--day", type=date.fromisoformat, help="print each flight of that travel date, no calls")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    today = datetime.now(timezone.utc).date()
    if args.show:
        show_calendar(today)
        return 0
    if args.day:
        show_day(args.day)
        return 0

    if not settings.flight_demand_enabled:
        print("FLIGHT_DEMAND_ENABLED=false στο .env: δεν τρέχει τίποτα.")
        return 0
    if not settings.aerodatabox_api_key.strip():
        print("Λείπει το AERODATABOX_API_KEY στο .env: δεν τρέχει τίποτα.")
        return 2
    plan = settings.flight_plan
    print(
        f"== Συλλογή πτήσεων · πλάνο {plan.name}: {plan.monthly_units} μονάδες/μήνα, "
        f"μέρες {','.join(WEEKDAYS[day] for day in plan.weekdays)}, "
        f"ώρες {', '.join(f'{start:02d}-{end:02d}' for start, end in plan.windows)} =="
    )
    saved = run_flight_demand_collection(today=today, max_calls=args.calls)
    print(f"Αποθηκεύτηκαν {saved} παρατηρήσεις (αεροδρόμιο × ημερομηνία × παράθυρο).")
    print_budget(today)
    return 0


def print_budget(today: date) -> None:
    repository = FlightRepository()
    plan = settings.flight_plan
    calls = repository.calls_since(month_start(today))
    units, when = repository.latest_units_remaining()
    line = (
        f"Μονάδες αυτόν τον μήνα: {calls * settings.flight_demand_units_per_call} "
        f"από {plan.monthly_units} του πλάνου {plan.name}"
    )
    if units is not None:
        line += f" · το AeroDataBox ανέφερε {units} διαθέσιμες στις {when:%d/%m %H:%M} UTC"
    print(line)


def collected_airports() -> list[tuple[str, str]]:
    """(IATA, island) of the airports of the islands active properties sit on."""
    found = {
        airport
        for point in FlightRepository().property_points()
        if (airport := airport_for(point["latitude"], point["longitude"])) is not None
    }
    return sorted(found)


def show_calendar(today: date) -> None:
    """Per island airport: collected travel dates, arrivals, pickup and level."""
    repository = FlightRepository()
    max_age = settings.flight_signal_max_age_days
    airports = collected_airports()
    if not airports:
        print("Κανένα ενεργό κατάλυμα σε νησί με αεροδρόμιο.")
    for iata, island in airports:
        rows = repository.observation_history(
            iata, **flight_history_window(today, None, settings.flight_demand_horizon_days, max_age)
        )
        print(f"\n== {island} · αεροδρόμιο {iata} ==")
        if not rows:
            print("  Δεν υπάρχουν πρόσφατες παρατηρήσεις: τρέξε τη συλλογή.")
            continue
        collected = sorted({row["observed_on"] for row in rows})
        print(f"  Συλλογές: {len(collected)} ({collected[0]:%d/%m}–{collected[-1]:%d/%m})")
        print("  Άφιξη      Πτήσεις  Δ πτήσεων  Από     Ζήτηση    Κυρίως από")
        for day in flight_daily_levels(rows, today, airport_iata=iata, island=island, max_age_days=max_age):
            if day["travel_date"] <= today:
                continue
            added = f"{day['flights_added']:+d}" if day["flights_added"] is not None else "—"
            change = f" ({day['arrivals_change_pct']:+.0f}%)" if day["arrivals_change_pct"] is not None else ""
            since = f"{day['compared_with']:%d/%m}" if day["compared_with"] else "—"
            print(
                f"  {WEEKDAYS[day['travel_date'].weekday()]} {day['travel_date']:%d/%m}  {day['arrivals']:>7}  "
                f"{added + change:>10}  {since:>5}  {LEVELS[day['level']]:<8}  {', '.join(day['top_origins'])}"
            )
    print()
    print_budget(today)


def show_day(travel_date: date) -> None:
    """Each flight of one travel date, from the stored answers."""
    with get_engine(role="api").connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (airport_iata, window_start_hour)
                       airport_iata, window_start_hour, window_end_hour, observed_on, raw_payload
                FROM roomrate_flight_observations
                WHERE travel_date = :travel_date AND raw_payload IS NOT NULL
                ORDER BY airport_iata, window_start_hour, observed_on DESC
                """
            ),
            {"travel_date": travel_date},
        ).mappings().all()
    if not rows:
        print("Δεν υπάρχουν αποθηκευμένες απαντήσεις για αυτή την ημερομηνία (κρατιούνται 7 μέρες).")
    for row in rows:
        payload = row["raw_payload"] or {}
        flights = payload.get("arrivals") if isinstance(payload, dict) else None
        print(
            f"\n== {row['airport_iata']} {row['window_start_hour']:02d}:00–{row['window_end_hour']:02d}:00 "
            f"(συλλογή {row['observed_on']:%d/%m}) · {len(flights or [])} πτήσεις =="
        )
        for item in flights or []:
            origin = item.get("movement") or item.get("departure") or {}
            airport = origin.get("airport") or {}
            scheduled = (origin.get("scheduledTime") or {}).get("local") or "?"
            print(
                f"  {scheduled[11:16]}  {item.get('number', '?'):<9} {(item.get('airline') or {}).get('name', '?'):<22} "
                f"από {airport.get('iata') or '?'} {airport.get('name') or ''}"
                f"{'' if item.get('status') in (None, 'Expected', 'Unknown') else ' · ' + str(item.get('status'))}"
            )


if __name__ == "__main__":
    sys.exit(main())
