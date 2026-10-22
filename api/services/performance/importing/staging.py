"""An upload waits for commit as JSON (roomrate_performance_import_rows.data, imports.extras): to it and back."""

from __future__ import annotations

from datetime import date
from typing import Any

from api.services.performance.importing.parsed import ParsedFile

_DATE_KEYS = ("booked_on", "cancelled_on", "arrival", "departure", "date_from", "date_to", "valid_from", "valid_to")


def to_json(record: dict[str, Any]) -> dict[str, Any]:
    """A booking, room or closure with its dates as ISO text."""
    return {key: value.isoformat() if isinstance(value, date) else value for key, value in record.items()}


def from_json(data: dict[str, Any]) -> dict[str, Any]:
    """The record back, with its dates as dates."""
    record = dict(data)
    for key in _DATE_KEYS:
        if record.get(key):
            record[key] = date.fromisoformat(record[key])
    return record


def staged_rows(parsed: ParsedFile) -> list[dict[str, Any]]:
    """Every read row as kept until commit: ``row_number``, ``booking_ref``, ``room_label``, ``data``, ``error``."""
    return [
        {
            "row_number": row.row,
            "booking_ref": row.booking_ref,
            "room_label": row.room_label,
            "data": to_json(row.booking) if row.booking else None,
            "error": row.error,
        }
        for row in parsed.rows
    ]


def staged_extras(parsed: ParsedFile) -> dict[str, Any]:
    """A template's «Δωμάτια» and «Κλειστές περίοδοι», applied on commit; nothing for an export."""
    if parsed.format != "template":
        return {}
    return {"rooms": [to_json(room) for room in parsed.rooms], "closures": [to_json(row) for row in parsed.closures]}


def staged_bookings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The bookings of the rows that will be imported."""
    return [from_json(row["data"]) for row in rows if row["data"] and not row["error"]]
