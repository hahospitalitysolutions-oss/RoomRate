"""Run the ferry demand jobs once, now: the port lookup and the day's collection.

The worker runs them by itself while the API is up: it looks up new
destinations within minutes and collects each one daily at the hour its
owner chose (Ρυθμίσεις → Ζήτηση νησιού, 08:00 by default), or on «Συλλογή
τώρα». When the API was closed at that hour, run this from the project folder:

    python scripts/run_ferry_demand.py              # lookup, then today's collection
    python scripts/run_ferry_demand.py --lookup     # port lookup only
    python scripts/run_ferry_demand.py --collect    # today's collection only
    python scripts/run_ferry_demand.py --lookup --refresh   # redo every lookup
    python scripts/run_ferry_demand.py --show       # the demand calendar, no calls
    python scripts/run_ferry_demand.py --day 2026-10-17   # each sailing that day, no calls

Uses DATABASE_URL and the FERRY_* settings from the project's .env and
WRITES to that database. The demand signal compares each sailing with an
earlier look at it, so the collection must run every day; on Windows, with
the API closed, a scheduled task does it (PowerShell, once):

    schtasks /Create /TN "RoomRate ferries" /SC DAILY /ST 08:15 /TR "C:\room_project2\.venv\Scripts\python.exe C:\room_project2\scripts\run_ferry_demand.py"
 A collection makes up to FERRY_DEMAND_MAX_CALLS_PER_RUN calls,
FERRY_DEMAND_CALL_INTERVAL_SECONDS apart (default 200 × 1 s ≈ 3-4 minutes).
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
from api.repositories.ferry_repository import FerryRepository  # noqa: E402
from api.scheduler import run_demand_tick, run_ferry_profile_resolution  # noqa: E402
from api.services.ferry_demand_service import daily_levels, history_window  # noqa: E402
from api.services.ferryhopper_client import is_passenger_accommodation  # noqa: E402

WEEKDAYS = ("Δευ", "Τρί", "Τετ", "Πέμ", "Παρ", "Σάβ", "Κυρ")
LEVELS = {"high": "ΥΨΗΛΗ", "normal": "κανονική", "low": "χαμηλή", "unknown": "—"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookup", action="store_true", help="port lookup only")
    parser.add_argument("--collect", action="store_true", help="collection only")
    parser.add_argument("--refresh", action="store_true", help="redo the lookup of every destination")
    parser.add_argument("--show", action="store_true", help="print the demand calendar, no calls")
    parser.add_argument("--days", type=int, default=21, help="days ahead --show prints")
    parser.add_argument("--day", type=date.fromisoformat, help="print each sailing of that travel date, no calls")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.show:
        show_calendar(args.days)
        return 0
    if args.day:
        show_day(args.day)
        return 0

    if not settings.ferry_demand_enabled:
        print("FERRY_DEMAND_ENABLED=false στο .env: δεν τρέχει τίποτα.")
        return 0
    do_lookup = args.lookup or not args.collect
    do_collect = args.collect or not args.lookup
    today = datetime.now(timezone.utc).date()

    if do_lookup:
        if args.refresh:
            with get_engine(role="writer").begin() as connection:
                connection.execute(
                    text(
                        "UPDATE roomrate_ferry_destination_profiles "
                        "SET resolved_at = now() - interval '400 days'"
                    )
                )
        print("== Εντοπισμός λιμανιών ==")
        # Several rounds: each looks up at most 5 destinations.
        total = 0
        while True:
            resolved = run_ferry_profile_resolution(today=today)
            total += resolved
            if not resolved:
                break
        print(f"Εντοπίστηκαν {total} προορισμοί.")
        print_profiles()

    if do_collect:
        print("\n== Συλλογή δρομολογίων ==")
        # Every destination with ferries on, whatever its owner's hour; one
        # collection a Greek day, as the worker keeps it.
        saved = run_demand_tick(force=True, kinds=("ferries",)).get("ferries", 0)
        if saved:
            print(f"Αποθηκεύτηκαν {saved} παρατηρήσεις (γραμμή × ημερομηνία ταξιδιού).")
        else:
            print("Τίποτα για συλλογή: έγινε ήδη σήμερα, ή κανένας προορισμός με πλοία δεν έχει ακόμη γραμμές.")
    return 0


def ferry_destinations() -> list[dict]:
    with get_engine(role="api").connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT canonical_destination, island_port FROM roomrate_ferry_destination_profiles "
                    "WHERE status IN ('ferry', 'error') ORDER BY canonical_destination"
                )
            ).mappings()
        ]


def show_calendar(days: int) -> None:
    """Per ferry destination: arrival day, sailings, cheapest fare, places on sale, pickup, level."""
    today = datetime.now(timezone.utc).date()
    repository = FerryRepository()
    destinations = ferry_destinations()
    if not destinations:
        print("Κανένας προορισμός με πλοία ακόμη: τρέξε πρώτα χωρίς --show.")
    for destination in destinations:
        rows = repository.observation_history(
            destination["canonical_destination"], **history_window(today, None, settings.ferry_demand_horizon_days)
        )
        print(f"\n== {destination['canonical_destination']} · λιμάνι {destination['island_port']} ==")
        if not rows:
            print("  Δεν υπάρχουν πρόσφατες παρατηρήσεις: τρέξε τη συλλογή.")
            continue
        collected = sorted({row["observed_on"] for row in rows})
        print(f"  Συλλογές: {len(collected)} ({collected[0]:%d/%m}–{collected[-1]:%d/%m})")
        print("  Άφιξη      Δρομ.  Φθηνότερο  Θέσεις  Δ θέσεων  Δ τιμής  Ζήτηση")
        for day in [day for day in daily_levels(rows, today) if day["travel_date"] > today][:days]:
            price = f"{day['min_price_eur']:.0f} €" if day["min_price_eur"] is not None else "—"
            options = day["accommodation_options"] if day["accommodation_options"] is not None else "—"
            options_change = f"{day['options_change_pct']:+.0f}%" if day["options_change_pct"] is not None else "—"
            price_change = f"{day['price_change_pct']:+.0f}%" if day["price_change_pct"] is not None else "—"
            print(
                f"  {WEEKDAYS[day['travel_date'].weekday()]} {day['travel_date']:%d/%m}  {day['sailings']:>5}  "
                f"{price:>9}  {options:>6}  {options_change:>8}  {price_change:>7}  {LEVELS[day['level']]}"
            )
        if len(collected) < 2:
            print("  Δ θέσεων / Δ τιμής: το ίδιο πλοίο σε σχέση με παλιότερη συλλογή (από 3 μέρες πριν και μετά).")


def show_day(travel_date: date) -> None:
    """Each sailing of one travel date, from the stored answers: operator, cheapest accommodations."""
    with get_engine(role="api").connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (o.route_id) r.origin_port, r.destination_port, o.observed_on, o.raw_payload
                FROM roomrate_ferry_observations o
                JOIN roomrate_ferry_routes r ON r.id = o.route_id
                WHERE o.travel_date = :travel_date AND o.raw_payload IS NOT NULL
                ORDER BY o.route_id, o.observed_on DESC
                """
            ),
            {"travel_date": travel_date},
        ).mappings().all()
    if not rows:
        print("Δεν υπάρχουν αποθηκευμένες απαντήσεις για αυτή την ημερομηνία (κρατιούνται 7 μέρες).")
    for row in rows:
        print(f"\n== {row['origin_port']} → {row['destination_port']} (συλλογή {row['observed_on']:%d/%m}) ==")
        payload = row["raw_payload"] or {}
        itineraries = payload.get("foundDirectItinerariesForTrip") if isinstance(payload, dict) else None
        for itinerary in itineraries or []:
            for segment in itinerary.get("segments") or []:
                company = (segment.get("marketingCompany") or segment.get("ownerCompany") or {}).get("name")
                vessel = (segment.get("vessel") or {}).get("name")
                offers = sorted(
                    (
                        ((option.get("expectedPrice") or {}).get("totalPriceInCents", 0) / 100, option.get("type"))
                        for option in segment.get("accommodations") or []
                        if (option.get("expectedPrice") or {}).get("totalPriceInCents") is not None
                    ),
                )
                cheapest = ", ".join(
                    f"{kind} {price:.2f} €" + ("" if is_passenger_accommodation(kind) else " (δεν μετράει)")
                    for price, kind in offers[:4]
                ) or "χωρίς τιμές"
                print(
                    f"  {segment.get('departureDateTime', '?')[11:16]} {company} · {vessel} · "
                    f"{len(offers)} θέσεις · φθηνότερες: {cheapest}"
                )


def print_profiles() -> None:
    with get_engine(role="api").connect() as connection:
        profiles = connection.execute(
            text(
                """
                SELECT p.canonical_destination, p.status, p.island_port, p.detail,
                       string_agg(r.origin_port || ' (' || COALESCE(r.sample_sailings::text, '?') || ')',
                                  ', ' ORDER BY r.origin_port) FILTER (WHERE r.status <> 'disabled') AS routes
                FROM roomrate_ferry_destination_profiles p
                LEFT JOIN roomrate_ferry_routes r ON r.canonical_destination = p.canonical_destination
                GROUP BY p.canonical_destination, p.status, p.island_port, p.detail
                ORDER BY p.canonical_destination
                """
            )
        ).mappings().all()
    for profile in profiles:
        line = f"  {profile['canonical_destination']}: {profile['status']}"
        if profile["island_port"]:
            line += f" · λιμάνι {profile['island_port']}"
        if profile["routes"]:
            line += f" · γραμμές από {profile['routes']}"
        if profile["detail"]:
            line += f" · {profile['detail']}"
        print(line)


if __name__ == "__main__":
    sys.exit(main())
