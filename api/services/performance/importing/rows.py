"""One row of an owner's file → its bookings, or the Greek message that tells the owner what is wrong.

Also the template's «Δωμάτια» and «Κλειστές περίοδοι» rows. A Booking.com
booking of several room types becomes one booking per room (``split_rooms``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from api.services.performance.countries import country_code
from api.services.performance.importing.columns import BOOKING_COM
from api.services.performance.importing.parsed import ParsedRow
from api.services.performance.importing.sheets import cell
from api.services.performance.importing.values import (
    BadValue,
    in_other_currency,
    parse_amount,
    parse_date,
    parse_int,
    parse_label,
    parse_status,
)
from api.services.performance.template import DEFAULT_CHANNEL

MAX_NIGHTS = 365


class RowError(ValueError):
    """The row cannot be imported; the message is Greek, for the owner."""


def _read(parse, value: object, message: str):
    """``parse(value)``, or RowError with ``message`` and the text as found in it."""
    try:
        return parse(value)
    except BadValue as exc:
        raise RowError(message.format(exc)) from exc


def booking_rows(number: int, row: tuple, columns: dict[str, int], fmt: str, reference: str | None) -> list[ParsedRow]:
    """The row's bookings (one per room type of a multi-room Booking.com booking), or one row with its error."""
    label = parse_label(cell(row, columns, "room_label"))
    try:
        booking = _booking(row, columns, fmt, reference, label)
    except RowError as exc:
        return [ParsedRow(number, reference, label, None, str(exc))]
    return [ParsedRow(number, part["booking_ref"], part["room_label"], part) for part in split_rooms(booking)]


def _booking(row: tuple, columns: dict[str, int], fmt: str, reference: str | None, label: str | None) -> dict[str, Any]:
    if reference is None:
        raise RowError("Λείπει ο αριθμός κράτησης.")
    if label is None:
        raise RowError("Λείπει ο τύπος δωματίου.")
    arrival, departure = _stay(row, columns)
    booked_on = _read(parse_date, cell(row, columns, "booked_on"), "Η ημερομηνία κράτησης δεν είναι ημερομηνία («{}»).")
    if booked_on is not None and booked_on > arrival:
        raise RowError("Η ημερομηνία κράτησης είναι μετά την άφιξη.")
    cancelled_on = _read(
        parse_date, cell(row, columns, "cancelled_on"), "Η ημερομηνία ακύρωσης δεν είναι ημερομηνία («{}»)."
    )
    status = _read(
        parse_status, cell(row, columns, "status"), "Άγνωστη κατάσταση («{}»): Επιβεβαιωμένη, Ακυρωμένη ή No-show."
    )
    gross, commission = _amounts(row, columns)
    rooms, adults, children = _people(row, columns)
    channel = BOOKING_COM if fmt == "booking" else (parse_label(cell(row, columns, "channel"), 100) or DEFAULT_CHANNEL)
    return {
        "booking_ref": reference,
        "room_label": label,
        "channel": channel,
        "status": status,
        "booked_on": booked_on,
        "cancelled_on": cancelled_on if status == "cancelled" else None,
        "arrival": arrival,
        "departure": departure,
        "rooms": rooms,
        "adults": adults,
        "children": children,
        "gross_eur": gross,
        "commission_eur": commission,
        "country_code": country_code(cell(row, columns, "country")),
    }


def _stay(row: tuple, columns: dict[str, int]):
    """(arrival, departure); a missing departure comes from the nights column when there is one."""
    arrival = _read(parse_date, cell(row, columns, "arrival"), "Η άφιξη δεν είναι ημερομηνία («{}»).")
    departure = _read(parse_date, cell(row, columns, "departure"), "Η αναχώρηση δεν είναι ημερομηνία («{}»).")
    if arrival is None:
        raise RowError("Λείπει η ημερομηνία άφιξης.")
    if departure is None:
        try:
            nights = parse_int(cell(row, columns, "nights"))
        except BadValue:
            nights = None
        if not nights:
            raise RowError("Λείπει η ημερομηνία αναχώρησης.")
        departure = arrival + timedelta(days=nights)
    if departure < arrival:
        raise RowError("Η αναχώρηση είναι πριν από την άφιξη.")
    if departure == arrival:
        raise RowError("Η αναχώρηση είναι την ίδια μέρα με την άφιξη (καμία νύχτα).")
    nights = (departure - arrival).days
    if nights > MAX_NIGHTS:
        raise RowError(f"Διαμονή {nights} νυχτών· το όριο είναι {MAX_NIGHTS}.")
    return arrival, departure


def _amounts(row: tuple, columns: dict[str, int]) -> tuple[float, float]:
    """(gross, commission) in euros; the commission from its percentage when only that is given."""
    raw_gross = cell(row, columns, "gross_eur")
    if in_other_currency(raw_gross):
        raise RowError(f"Το ποσό δεν είναι σε ευρώ («{raw_gross.strip()}»).")
    gross = _read(parse_amount, raw_gross, "Το ποσό δεν είναι αριθμός («{}»).")
    if gross is None:
        raise RowError("Λείπει το ποσό.")
    commission = _read(parse_amount, cell(row, columns, "commission_eur"), "Η προμήθεια δεν είναι αριθμός («{}»).")
    percent = _read(parse_amount, cell(row, columns, "commission_pct"), "Η προμήθεια δεν είναι αριθμός («{}»).")
    if commission is None and percent is not None:
        commission = round(gross * percent / 100, 2)
    commission = commission or 0.0
    if gross < 0 or commission < 0:
        raise RowError("Αρνητικό ποσό.")
    return gross, commission


def _people(row: tuple, columns: dict[str, int]) -> tuple[int, int | None, int | None]:
    """(rooms, adults, children); adults from «People» minus children when only that is given."""
    message = "Τα δωμάτια και τα άτομα πρέπει να είναι ακέραιοι («{}»)."
    rooms = _read(parse_int, cell(row, columns, "rooms"), message) or 1
    adults = _read(parse_int, cell(row, columns, "adults"), message)
    children = _read(parse_int, cell(row, columns, "children"), message)
    people = _read(parse_int, cell(row, columns, "people"), message)
    if adults is None and people is not None:
        adults = max(people - (children or 0), 0)
    return rooms, adults, children


def split_rooms(booking: dict[str, Any]) -> list[dict[str, Any]]:
    """A Booking.com booking of several room types («Double Room, Twin Room», 2 rooms) → one booking per room.

    Only when the label lists exactly as many names as rooms; the same name
    repeated is one booking of that many rooms. Amounts and guests are
    shared evenly, the remainder on the last room. References get «-1», «-2».
    """
    rooms = booking["rooms"]
    parts = [part.strip() for part in booking["room_label"].split(",")]
    if rooms < 2 or len(parts) != rooms or not all(parts):
        return [booking]
    if len(set(parts)) == 1:
        return [{**booking, "room_label": parts[0]}]
    return [
        {
            **booking,
            "booking_ref": f"{booking['booking_ref']}-{index + 1}",
            "room_label": part,
            "rooms": 1,
            "gross_eur": _share(booking["gross_eur"], rooms, index, cents=True),
            "commission_eur": _share(booking["commission_eur"], rooms, index, cents=True),
            "adults": _share(booking["adults"], rooms, index, cents=False),
            "children": _share(booking["children"], rooms, index, cents=False),
        }
        for index, part in enumerate(parts)
    ]


def _share(total: float | int | None, parts: int, index: int, *, cents: bool) -> float | int | None:
    """Part ``index`` of ``total`` split evenly, the remainder on the last part."""
    if total is None:
        return None
    units = int(round(total * 100)) if cents else total
    each = units // parts
    part = units - each * (parts - 1) if index == parts - 1 else each
    return part / 100 if cents else part


# ---------------------------------------------------------------------------
# The template's «Δωμάτια» and «Κλειστές περίοδοι»
# ---------------------------------------------------------------------------


def room_row(values: dict[str, object]) -> dict[str, Any]:
    """{"room_label", "rooms", "valid_from", "valid_to"}; BadValue with the reason otherwise."""
    label = parse_label(values.get("room_label"))
    if label is None:
        raise BadValue("λείπει ο τύπος δωματίου.")
    try:
        rooms = parse_int(values.get("rooms"))
        valid_from, valid_to = parse_date(values.get("valid_from")), parse_date(values.get("valid_to"))
    except BadValue as exc:
        raise BadValue(f"«{label}»: μη έγκυρη τιμή («{exc}»).") from exc
    if rooms is None:
        raise BadValue(f"«{label}»: λείπει ο αριθμός δωματίων.")
    if valid_from and valid_to and valid_to < valid_from:
        raise BadValue(f"«{label}»: το «Ισχύει έως» είναι πριν από το «Ισχύει από».")
    return {"room_label": label, "rooms": rooms, "valid_from": valid_from, "valid_to": valid_to}


def closure_row(values: dict[str, object]) -> dict[str, Any]:
    """{"date_from", "date_to", "room_label", "rooms", "reason"}; BadValue with the reason otherwise."""
    try:
        date_from, date_to = parse_date(values.get("date_from")), parse_date(values.get("date_to"))
        rooms = parse_int(values.get("rooms"))
    except BadValue as exc:
        raise BadValue(f"μη έγκυρη τιμή («{exc}»).") from exc
    if date_from is None or date_to is None:
        raise BadValue("λείπει το «Από» ή το «Έως».")
    if date_to < date_from:
        raise BadValue("το «Έως» είναι πριν από το «Από».")
    return {
        "date_from": date_from,
        "date_to": date_to,
        "room_label": parse_label(values.get("room_label")),
        "rooms": rooms or None,
        "reason": parse_label(values.get("reason")),
    }
