"""Synthetic bookings of a seasonal island hotel, for building and showing «Απόδοση» without real data.

Deterministic for a seed. Each room is filled night by night on its own
calendar, so no room is ever sold twice for a night: occupancy follows the
month (May–October, closed in winter), prices the month and the year, and
every booking gets a lead time, a channel with its commission, a booker
country and a guest count. Cancelled bookings come on top (they never held
a room); a few no-shows did. Only what was booked by ``as_of`` exists: the
coming season is partly on the books, as it would be in a real export.

Writers: scripts/generate_demo_bookings.py (the RoomRate template and a
Booking.com export) and scripts/seed_demo_performance.py (a local database).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from api.services.performance_countries import COUNTRIES


@dataclass(frozen=True)
class DemoRoomType:
    label: str
    rooms: int
    # Average price per night over the season, the first season.
    adr_eur: float
    max_adults: int = 2
    family: bool = False


# The 12-room hotel of the page's samples (frontend/public/mock/performance).
DEFAULT_ROOM_TYPES: tuple[DemoRoomType, ...] = (
    DemoRoomType("Double Room with Sea View", 5, 121.0),
    DemoRoomType("Standard Double Room", 4, 96.0),
    DemoRoomType("Family Room", 2, 139.0, max_adults=2, family=True),
    DemoRoomType("Junior Suite", 1, 146.0, max_adults=3),
)

OPEN_MONTH, CLOSE_MONTH = 5, 10
# The first season's occupancy by month; later seasons a few points higher.
MONTH_OCCUPANCY = {5: 0.50, 6: 0.68, 7: 0.86, 8: 0.92, 9: 0.76, 10: 0.55}
YEARLY_OCCUPANCY_GAIN = 0.03
# Price by month around the season's average; later seasons dearer.
MONTH_PRICE = {5: 0.78, 6: 0.92, 7: 1.14, 8: 1.24, 9: 0.98, 10: 0.80}
YEARLY_PRICE_GROWTH = 0.06

# name, share of bookings, commission rate.
CHANNELS: tuple[tuple[str, float, float], ...] = (
    ("Booking.com", 0.58, 0.17),
    ("Απευθείας", 0.27, 0.0),
    ("Expedia", 0.09, 0.18),
    ("Τηλέφωνο / email", 0.06, 0.0),
)
BOOKER_COUNTRIES: tuple[tuple[str, float], ...] = (
    ("GR", 0.22), ("DE", 0.16), ("GB", 0.14), ("IT", 0.10), ("FR", 0.09), ("US", 0.07), ("NL", 0.05),
    ("PL", 0.04), ("IL", 0.03), ("SE", 0.03), ("AT", 0.015), ("BE", 0.015), ("CH", 0.01), ("ES", 0.01),
    ("CZ", 0.01), ("RO", 0.01), ("AU", 0.01),
)
# (nights from, nights to, share): 1, 2-3, 4-5, 6-9, 10+.
LENGTHS: tuple[tuple[int, int, float], ...] = ((1, 1, 0.07), (2, 3, 0.31), (4, 5, 0.33), (6, 9, 0.25), (10, 14, 0.04))
# (days from, days to, share): 0-7, 8-30, 31-60, 61-180, 181-365.
LEADS: tuple[tuple[int, int, float], ...] = (
    (0, 7, 0.13), (8, 30, 0.24), (31, 60, 0.23), (61, 180, 0.26), (181, 365, 0.14),
)
# High summer is booked earlier: the next season is partly on the books in October.
MONTH_LEAD = {5: 0.8, 6: 1.0, 7: 1.25, 8: 1.35, 9: 1.0, 10: 0.8}
CANCELLED_SHARE = 0.09
NO_SHOW_SHARE = 0.01

_REF_PREFIX = {"Booking.com": "", "Απευθείας": "RR-", "Expedia": "EX-", "Τηλέφωνο / email": "TEL-"}


@dataclass
class DemoData:
    room_types: tuple[DemoRoomType, ...]
    # One dict per booking: booking_ref, booked_on, cancelled_on, arrival, departure, room_label, rooms,
    # adults, children, gross_eur, commission_eur, channel, status, country_code, booked_at, cancelled_at.
    bookings: list[dict[str, Any]] = field(default_factory=list)
    # The property closed between seasons: {"date_from", "date_to", "room_label", "rooms", "reason"}.
    closures: list[dict[str, Any]] = field(default_factory=list)


def _weighted(rng: random.Random, choices: tuple[tuple, ...], weight_index: int) -> tuple:
    return rng.choices(choices, weights=[choice[weight_index] for choice in choices])[0]


def _nights(rng: random.Random) -> int:
    low, high, _ = _weighted(rng, LENGTHS, 2)
    return rng.randint(low, high)


def _lead_days(rng: random.Random, nights: int, month: int) -> int:
    # Longer stays are planned earlier.
    weights = [share * (1.0 + 0.04 * nights * index) for index, (_, _, share) in enumerate(LEADS)]
    low, high, _ = rng.choices(LEADS, weights=weights)[0]
    return min(365, round(rng.randint(low, high) * MONTH_LEAD[month]))


def _guests(rng: random.Random, room: DemoRoomType) -> tuple[int, int]:
    if room.family:
        return 2, rng.choice((1, 2, 2))
    if room.max_adults >= 3 and rng.random() < 0.3:
        return 3, 0
    return (1 if rng.random() < 0.12 else 2), 0


def _clock(rng: random.Random, day: date) -> datetime:
    return datetime.combine(day, time(rng.randint(7, 23), rng.randint(0, 59), rng.randint(0, 59)))


def generate_demo_bookings(
    *,
    seasons: tuple[int, ...],
    as_of: date,
    room_types: tuple[DemoRoomType, ...] = DEFAULT_ROOM_TYPES,
    seed: int = 7,
) -> DemoData:
    """Bookings for every season in ``seasons`` (years), as they stand on ``as_of``."""
    rng = random.Random(seed)
    data = DemoData(room_types=room_types)
    first_season = min(seasons)
    serial = {name: 0 for name, _, _ in CHANNELS}
    used: set[str] = set()

    def reference(channel: str) -> str:
        serial[channel] += 1
        if channel != "Booking.com":
            return f"{_REF_PREFIX[channel]}{serial[channel]:05d}"
        number = str(4_000_000_000 + rng.randint(1, 999_999_999))
        while number in used:
            number = str(4_000_000_000 + rng.randint(1, 999_999_999))
        used.add(number)
        return number

    for season in sorted(seasons):
        years = season - first_season
        opens, closes = date(season, OPEN_MONTH, 1), date(season, CLOSE_MONTH, 31)
        for room in room_types:
            for _unit in range(room.rooms):
                day = opens
                while day <= closes:
                    occupancy = min(0.97, MONTH_OCCUPANCY[day.month] + YEARLY_OCCUPANCY_GAIN * years)
                    nights = _nights(rng)
                    # Empty nights before the next stay: on average what makes the month's occupancy.
                    mean_gap = nights * (1 - occupancy) / occupancy
                    day += timedelta(days=round(rng.expovariate(1 / mean_gap)) if mean_gap > 0 else 0)
                    if day > closes:
                        break
                    nights = min(nights, (closes - day).days + 1)
                    booking = _booking(rng, room, day, nights, years, reference)
                    day += timedelta(days=nights)
                    if booking["booked_on"] > as_of:
                        continue  # not booked yet on as_of: the night stays free
                    if booking["status"] == "no_show" and booking["arrival"] >= as_of:
                        booking["status"] = "confirmed"  # a guest not due yet cannot have failed to come
                    data.bookings.append(booking)
                    if rng.random() < CANCELLED_SHARE / (1 - CANCELLED_SHARE):
                        cancelled = _cancelled_twin(rng, booking, opens, closes, reference)
                        if cancelled["cancelled_on"] <= as_of:
                            data.bookings.append(cancelled)
        data.closures.append(
            {
                "date_from": date(season - 1, CLOSE_MONTH, 31) + timedelta(days=1),
                "date_to": opens - timedelta(days=1),
                "room_label": None,
                "rooms": None,
                "reason": "Εκτός σεζόν",
            }
        )
    data.bookings.sort(key=lambda booking: (booking["booked_at"], booking["booking_ref"]))
    return data


def _booking(rng, room: DemoRoomType, arrival: date, nights: int, years: int, reference) -> dict[str, Any]:
    channel, _, rate = _weighted(rng, CHANNELS, 1)
    growth = (1 + YEARLY_PRICE_GROWTH) ** years
    stay = [arrival + timedelta(days=offset) for offset in range(nights)]
    gross = sum(room.adr_eur * MONTH_PRICE[night.month] * growth * rng.gauss(1.0, 0.05) for night in stay)
    if nights >= 7:
        gross *= 0.95
    gross = float(round(gross))
    booked_at = _clock(rng, arrival - timedelta(days=_lead_days(rng, nights, arrival.month)))
    adults, children = _guests(rng, room)
    return {
        "booking_ref": reference(channel),
        "booked_on": booked_at.date(),
        "booked_at": booked_at,
        "cancelled_on": None,
        "cancelled_at": None,
        "arrival": arrival,
        "departure": arrival + timedelta(days=nights),
        "room_label": room.label,
        "rooms": 1,
        "adults": adults,
        "children": children,
        "gross_eur": gross,
        "commission_eur": round(gross * rate, 2),
        "channel": channel,
        "status": "no_show" if rng.random() < NO_SHOW_SHARE else "confirmed",
        "country_code": _weighted(rng, BOOKER_COUNTRIES, 1)[0],
    }


def _cancelled_twin(rng, booking: dict[str, Any], opens: date, closes: date, reference) -> dict[str, Any]:
    """A booking for about the same dates that was cancelled before arrival."""
    nights = (booking["departure"] - booking["arrival"]).days
    shift = rng.randint(-10, 10)
    arrival = min(max(booking["arrival"] + timedelta(days=shift), opens), closes - timedelta(days=nights - 1))
    booked_at = booking["booked_at"] - timedelta(days=rng.randint(0, 20))
    if booked_at.date() >= arrival:
        booked_at = _clock(rng, arrival - timedelta(days=rng.randint(1, 5)))
    window = max(0, (arrival - booked_at.date()).days - 1)
    cancelled_at = _clock(rng, booked_at.date() + timedelta(days=rng.randint(0, window)))
    channel = booking["channel"]
    return {
        **booking,
        "booking_ref": reference(channel),
        "booked_on": booked_at.date(),
        "booked_at": booked_at,
        "cancelled_on": cancelled_at.date(),
        "cancelled_at": cancelled_at,
        "arrival": arrival,
        "departure": arrival + timedelta(days=nights),
        "status": "cancelled",
        "country_code": _weighted(rng, BOOKER_COUNTRIES, 1)[0],
    }


# ---------------------------------------------------------------------------
# The two file formats
# ---------------------------------------------------------------------------

def template_rows(data: DemoData) -> list[dict[str, Any]]:
    """The bookings as rows of the RoomRate template («Κρατήσεις»), country in Greek."""
    return [
        {
            **booking,
            "country": COUNTRIES[booking["country_code"]][0] if booking["country_code"] in COUNTRIES
            else booking["country_code"],
        }
        for booking in data.bookings
    ]


# The reservations export of the Booking.com extranet, as far as known (task B3 confirms it on a real one).
BOOKING_EXPORT_COLUMNS = (
    "Book number", "Booked by", "Guest name(s)", "Check-in", "Check-out", "Booked on", "Status", "Rooms",
    "People", "Adults", "Children", "Children ages", "Price", "Commission %", "Commission amount",
    "Payment status", "Payment method", "Remarks", "Booker group", "Booker country", "Travel purpose",
    "Device", "Unit type", "Duration (nights)", "Cancellation date", "Address", "Phone number",
)
_BOOKING_STATUS = {"confirmed": "ok", "cancelled": "cancelled_by_guest", "no_show": "no_show"}


def booking_export_rows(data: DemoData) -> list[dict[str, Any]]:
    """The Booking.com bookings only, as the extranet export lists them (text values, amounts as «436 EUR»).

    The guest columns hold obvious placeholders: the importer must never read them.
    """
    rows = []
    for index, booking in enumerate(b for b in data.bookings if b["channel"] == "Booking.com"):
        gross, commission = booking["gross_eur"], booking["commission_eur"]
        guest = f"Demo Guest {index + 1:04d}"
        rows.append(
            {
                "Book number": booking["booking_ref"],
                "Booked by": guest,
                "Guest name(s)": guest,
                "Check-in": booking["arrival"].isoformat(),
                "Check-out": booking["departure"].isoformat(),
                "Booked on": booking["booked_at"].strftime("%Y-%m-%d %H:%M:%S"),
                "Status": _BOOKING_STATUS[booking["status"]],
                "Rooms": booking["rooms"],
                "People": booking["adults"] + booking["children"],
                "Adults": booking["adults"],
                "Children": booking["children"],
                "Children ages": ", ".join("7" for _ in range(booking["children"])),
                "Price": f"{gross:.2f} EUR",
                "Commission %": round(100 * commission / gross, 1) if gross else 0,
                "Commission amount": f"{commission:.2f} EUR",
                "Payment status": "Fully paid" if booking["status"] == "confirmed" else "",
                "Payment method": "Online payment",
                "Remarks": "",
                "Booker group": "Couple" if booking["adults"] == 2 and not booking["children"] else "Family",
                "Booker country": booking["country_code"].lower(),
                "Travel purpose": "Leisure",
                "Device": "Mobile",
                "Unit type": booking["room_label"],
                "Duration (nights)": (booking["departure"] - booking["arrival"]).days,
                "Cancellation date": booking["cancelled_at"].strftime("%Y-%m-%d %H:%M:%S")
                if booking["cancelled_at"] else "",
                "Address": "—",
                "Phone number": "+00 000 000 000",
            }
        )
    return rows
