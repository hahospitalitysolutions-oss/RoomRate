"""Write synthetic bookings files for «Απόδοση»: the RoomRate template and a Booking.com export.

A 12-room island hotel (4 room types), open May–October, with the last two
seasons complete and the next one partly booked, as it would stand today.
No database and no .env needed. From the project folder:

    python scripts/generate_demo_bookings.py                 # into tmp/performance-demo/
    python scripts/generate_demo_bookings.py --out C:\\temp\\demo --seed 11
    python scripts/generate_demo_bookings.py --as-of 2026-10-20 --with-errors

Files:
    demo_template.xlsx         every channel, in the RoomRate template (with «Δωμάτια», «Κλειστές περίοδοι»)
    demo_booking_export.xlsx   the Booking.com bookings only, as the extranet export lists them
    demo_booking_export.csv    the same, as CSV

``--with-errors`` adds three broken rows to each file (no departure, departure
before arrival, an amount in words) to see the upload's error list.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openpyxl import Workbook  # noqa: E402

from api.services.performance.demo import (  # noqa: E402
    BOOKING_EXPORT_COLUMNS,
    DEFAULT_ROOM_TYPES,
    DemoData,
    booking_export_rows,
    generate_demo_bookings,
    template_rows,
)
from api.services.performance.template import build_template  # noqa: E402

TEMPLATE_FILE = "demo_template.xlsx"
EXPORT_XLSX = "demo_booking_export.xlsx"
EXPORT_CSV = "demo_booking_export.csv"


def default_seasons(as_of: date) -> tuple[int, ...]:
    """The two seasons up to ``as_of`` and the next one."""
    return (as_of.year - 1, as_of.year, as_of.year + 1)


def broken_template_rows(label: str) -> list[dict[str, Any]]:
    arrival = date(2026, 7, 1)
    common = {"room_label": label, "rooms": 1, "adults": 2, "channel": "Απευθείας", "status": "confirmed"}
    return [
        {**common, "booking_ref": "ERR-1", "booked_on": arrival - timedelta(days=30), "arrival": arrival,
         "departure": None, "gross_eur": 300.0},
        {**common, "booking_ref": "ERR-2", "booked_on": arrival - timedelta(days=30), "arrival": arrival,
         "departure": arrival - timedelta(days=2), "gross_eur": 300.0},
        {**common, "booking_ref": "ERR-3", "booked_on": arrival - timedelta(days=30), "arrival": arrival,
         "departure": arrival + timedelta(days=3), "gross_eur": "τριακόσια"},
    ]


def broken_export_rows(label: str) -> list[dict[str, Any]]:
    base = {column: "" for column in BOOKING_EXPORT_COLUMNS}
    base.update({"Status": "ok", "Rooms": 1, "Adults": 2, "Unit type": label, "Booked on": "2026-06-01 10:00:00"})
    return [
        {**base, "Book number": "4999000001", "Check-in": "2026-07-01", "Check-out": "", "Price": "300 EUR"},
        {**base, "Book number": "4999000002", "Check-in": "2026-07-01", "Check-out": "2026-06-29", "Price": "300 EUR"},
        {**base, "Book number": "4999000003", "Check-in": "2026-07-01", "Check-out": "2026-07-04", "Price": "τριακόσια"},
    ]


def write_files(data: DemoData, out: Path, *, with_errors: bool = False) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    label = data.room_types[0].label
    rooms = [{"label": room.label, "rooms": room.rooms} for room in data.room_types]
    bookings = template_rows(data) + (broken_template_rows(label) if with_errors else [])
    paths = {"template": out / TEMPLATE_FILE, "export_xlsx": out / EXPORT_XLSX, "export_csv": out / EXPORT_CSV}
    paths["template"].write_bytes(
        build_template(rooms, property_name="Demo Aigaio Hotel", bookings=bookings, closures=data.closures)
    )

    export = booking_export_rows(data) + (broken_export_rows(label) if with_errors else [])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Reservations"
    sheet.append(list(BOOKING_EXPORT_COLUMNS))
    for row in export:
        sheet.append([row[column] for column in BOOKING_EXPORT_COLUMNS])
    workbook.save(paths["export_xlsx"])
    with paths["export_csv"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(BOOKING_EXPORT_COLUMNS))
        writer.writeheader()
        writer.writerows(export)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "tmp" / "performance-demo")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="the day the files stand on")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--with-errors", action="store_true", help="add three broken rows to each file")
    args = parser.parse_args()

    data = generate_demo_bookings(seasons=default_seasons(args.as_of), as_of=args.as_of, seed=args.seed)
    paths = write_files(data, args.out, with_errors=args.with_errors)
    booking_com = sum(1 for booking in data.bookings if booking["channel"] == "Booking.com")
    print(f"{len(data.bookings)} κρατήσεις ({booking_com} από Booking.com), "
          f"{sum(room.rooms for room in DEFAULT_ROOM_TYPES)} δωμάτια, σεζόν {', '.join(map(str, default_seasons(args.as_of)))}")
    for path in paths.values():
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
