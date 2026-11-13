"""What reading a bookings file gives: bookings, the rows that cannot be used, a template's extras."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ImportFileError(ValueError):
    """The file as a whole cannot be read; the message is Greek, for the owner (HTTP 400)."""


@dataclass
class ParsedRow:
    # The row number the owner sees in Excel.
    row: int
    booking_ref: str | None
    room_label: str | None
    # The booking (PerformanceRepository.save_bookings fields without room_type_id); None on error.
    booking: dict[str, Any] | None
    error: str | None = None


@dataclass
class ParsedFile:
    format: str
    rows: list[ParsedRow] = field(default_factory=list)
    # Template only: «Δωμάτια» and «Κλειστές περίοδοι», with labels still unmapped.
    rooms: list[dict[str, Any]] = field(default_factory=list)
    closures: list[dict[str, Any]] = field(default_factory=list)
    # Rows of «Δωμάτια» / «Κλειστές περίοδοι» that cannot be used: {"row", "booking_ref": None, "message"}.
    sheet_errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def bookings(self) -> list[dict[str, Any]]:
        return [row.booking for row in self.rows if row.booking is not None]

    @property
    def errors(self) -> list[dict[str, Any]]:
        """Every row that will not be imported, bookings first: the preview's ``errors``."""
        return [
            {"row": row.row, "booking_ref": row.booking_ref, "message": row.error}
            for row in self.rows
            if row.error
        ] + self.sheet_errors
