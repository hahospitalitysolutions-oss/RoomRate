"""Which header names each booking field, in the RoomRate template and in the Booking.com export.

Headers are compared as plain_text (no case, accents or trailing «*»).
Only these columns are ever read: guests' names, emails, phones and
addresses are not among them.
"""

from __future__ import annotations

from api.services.performance.template import BOOKING_COLUMNS

BOOKING_COM = "Booking.com"

Fields = dict[str, tuple[str, ...]]

TEMPLATE_FIELDS: Fields = {column.key: (column.header,) for column in BOOKING_COLUMNS}
TEMPLATE_REQUIRED = tuple(column.key for column in BOOKING_COLUMNS if column.required)

# The extranet export in English, the names it has had, and what a Greek extranet may use
# (to be confirmed against a real export).
BOOKING_FIELDS: Fields = {
    "booking_ref": ("Book number", "Booking number", "Reservation number", "Αριθμός κράτησης"),
    "booked_on": ("Booked on", "Booking date", "Reservation date", "Ημερομηνία κράτησης", "Κρατήθηκε στις"),
    "arrival": ("Check-in", "Arrival", "Άφιξη", "Check-in date"),
    "departure": ("Check-out", "Departure", "Αναχώρηση", "Check-out date"),
    "room_label": ("Unit type", "Room type", "Room name", "Rooms type", "Τύπος δωματίου", "Τύπος μονάδας"),
    "rooms": ("Rooms", "Number of rooms", "Δωμάτια"),
    "adults": ("Adults", "Ενήλικες"),
    "children": ("Children", "Παιδιά"),
    "people": ("People", "Persons", "Guests", "Άτομα"),
    "gross_eur": ("Price", "Total price", "Total payment", "Τιμή", "Συνολική τιμή"),
    "commission_eur": ("Commission amount", "Commission", "Ποσό προμήθειας", "Προμήθεια"),
    "commission_pct": ("Commission %", "Commission percentage", "Προμήθεια %", "Ποσοστό προμήθειας"),
    "status": ("Status", "Κατάσταση"),
    "cancelled_on": ("Cancellation date", "Cancelled on", "Ημερομηνία ακύρωσης"),
    "country": ("Booker country", "Country", "Χώρα", "Χώρα κράτησης"),
    "nights": ("Duration (nights)", "Nights", "Length of stay", "Νύχτες", "Διάρκεια (νύχτες)"),
}
BOOKING_REQUIRED = ("booking_ref", "arrival", "room_label", "gross_eur")

# A missing required column, as the owner is told.
FIELD_NAMES = {
    "booking_ref": "Αριθμός κράτησης",
    "arrival": "Άφιξη",
    "departure": "Αναχώρηση",
    "room_label": "Τύπος δωματίου",
    "gross_eur": "Ποσό",
}


def columns_of(fmt: str) -> tuple[Fields, tuple[str, ...]]:
    """(fields, required fields) of a format: "template" or "booking"."""
    return (TEMPLATE_FIELDS, TEMPLATE_REQUIRED) if fmt == "template" else (BOOKING_FIELDS, BOOKING_REQUIRED)
