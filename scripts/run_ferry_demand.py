"""Run the ferry demand jobs once, now: the port lookup and the day's collection.

The worker runs them by itself while the API is up (lookup hourly,
collection daily at 05:10 UTC = 08:10 Greek time). When the API was closed
at that hour, run this from the project folder:

    python scripts/run_ferry_demand.py              # lookup, then collection
    python scripts/run_ferry_demand.py --lookup     # port lookup only
    python scripts/run_ferry_demand.py --collect    # collection only
    python scripts/run_ferry_demand.py --lookup --refresh   # redo every lookup

Uses DATABASE_URL and the FERRY_* settings from .env and WRITES to that
database. A collection makes up to FERRY_DEMAND_MAX_CALLS_PER_RUN calls,
FERRY_DEMAND_CALL_INTERVAL_SECONDS apart (default 200 × 1 s ≈ 3-4 minutes).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from api.config import settings  # noqa: E402
from api.db import get_engine  # noqa: E402
from api.scheduler import run_ferry_demand_collection, run_ferry_profile_resolution  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookup", action="store_true", help="port lookup only")
    parser.add_argument("--collect", action="store_true", help="collection only")
    parser.add_argument("--refresh", action="store_true", help="redo the lookup of every destination")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

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
        saved = run_ferry_demand_collection(today=today)
        print(f"Αποθηκεύτηκαν {saved} παρατηρήσεις (γραμμή × ημερομηνία ταξιδιού).")
    return 0


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
