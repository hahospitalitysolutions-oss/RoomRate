"""Load synthetic bookings into a local account, to see «Απόδοση» with data before the real files exist.

It uses an owned property you already claimed (with its discovered room
types): up to four of its room types get demo rooms (5, 4, 2, 1), the last
two seasons of bookings and the next one partly booked, the winters as
closed periods, VAT 13% and a €1.50 resilience fee not in the price. The
page's «Διαγραφή όλων των κρατήσεων» (or ``--reset``) removes the bookings.
From the project folder, with DATABASE_URL in .env:

    python scripts/seed_demo_performance.py                    # the only owned property
    python scripts/seed_demo_performance.py --owned-property-id <uuid>
    python scripts/seed_demo_performance.py --reset            # delete its bookings first

A database that is not on this computer (e.g. Neon) needs ``--yes``: demo
bookings do not belong next to real ones.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from api.config import settings  # noqa: E402
from api.db import get_engine  # noqa: E402
from api.repositories.onboarding_repository import OnboardingRepository  # noqa: E402
from api.repositories.performance import PerformanceRepository  # noqa: E402
from api.services.performance.demo import (  # noqa: E402
    DEFAULT_ROOM_TYPES,
    DemoRoomType,
    generate_demo_bookings,
)

LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}
DEMO_REVENUE_SETTINGS = {"vat_pct": 13.0, "resilience_fee_eur": 1.5, "fee_included_in_price": False}


def database_host() -> str | None:
    return make_url(settings.database_url).host


def owned_properties() -> list[dict]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, account_id, display_name
                FROM roomrate_owned_properties
                WHERE is_active = true
                ORDER BY created_at DESC
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def demo_room_types(room_types: list[dict]) -> list[tuple[uuid.UUID, DemoRoomType]]:
    """The property's (up to four) room types, cheapest first, as the demo hotel's, priced from the scrape."""
    ranked = sorted(
        room_types,
        key=lambda room: (room.get("sample_price_per_night_eur") is None, room.get("sample_price_per_night_eur") or 0),
    )[: len(DEFAULT_ROOM_TYPES)]
    defaults = sorted(DEFAULT_ROOM_TYPES, key=lambda room: room.adr_eur)
    result = []
    for room, demo in zip(ranked, defaults):
        price = room.get("sample_price_per_night_eur")
        adr = min(600.0, max(40.0, float(price))) if price else demo.adr_eur
        result.append(
            (room["id"], DemoRoomType(room["room_type"], demo.rooms, adr, max_adults=demo.max_adults, family=demo.family))
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owned-property-id", type=uuid.UUID)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--reset", action="store_true", help="delete the property's bookings first")
    parser.add_argument("--yes", action="store_true", help="allow a database that is not on this computer")
    args = parser.parse_args()

    if not settings.database_url:
        print("Λείπει το DATABASE_URL στο .env.")
        return 1
    host = database_host()
    if host not in LOCAL_HOSTS and not args.yes:
        print(f"Η βάση δεν είναι τοπική ({host}). Αν είναι βάση δοκιμών, ξανατρέξτε με --yes.")
        return 1

    properties = owned_properties()
    if args.owned_property_id:
        properties = [prop for prop in properties if prop["id"] == args.owned_property_id]
    if len(properties) != 1:
        print("Διαλέξτε κατάλυμα με --owned-property-id:" if properties else "Δεν βρέθηκε κατάλυμα.")
        for prop in properties:
            print(f"  {prop['id']}  {prop['display_name']}")
        return 1
    owned = properties[0]
    account_id, property_id = owned["account_id"], owned["id"]

    room_types = demo_room_types(OnboardingRepository().list_room_types(account_id, property_id))
    if not room_types:
        print(f"Το «{owned['display_name']}» δεν έχει ακόμη τύπους δωματίων: ολοκληρώστε πρώτα το setup.")
        return 1

    seasons = (args.as_of.year - 1, args.as_of.year, args.as_of.year + 1)
    data = generate_demo_bookings(
        seasons=seasons, as_of=args.as_of, room_types=tuple(room for _, room in room_types), seed=args.seed
    )
    ids = {room.label: room_type_id for room_type_id, room in room_types}
    repository = PerformanceRepository()
    if args.reset:
        print(f"Διαγράφηκαν {repository.delete_bookings(account_id, property_id)} κρατήσεις.")
    repository.save_inventory(
        account_id, property_id, [{"room_type_id": ids[room.label], "rooms": room.rooms} for _, room in room_types]
    )
    repository.save_closures(account_id, property_id, data.closures)
    repository.save_revenue_settings(account_id, property_id, **DEMO_REVENUE_SETTINGS)
    repository.save_label_mappings(
        account_id, property_id, [{"label": label, "room_type_id": room_type_id} for label, room_type_id in ids.items()]
    )
    written = repository.save_bookings(
        account_id, property_id, [{**booking, "room_type_id": ids[booking["room_label"]]} for booking in data.bookings]
    )

    print(f"«{owned['display_name']}»: {written['inserted']} νέες και {written['updated']} ενημερωμένες κρατήσεις, "
          f"σεζόν {', '.join(map(str, seasons))}.")
    for _, room in room_types:
        print(f"  {room.rooms} × {room.label} (μέση τιμή {room.adr_eur:.0f} €)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
