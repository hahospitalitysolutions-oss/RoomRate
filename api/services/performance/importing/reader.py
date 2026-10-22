"""An owner's whole bookings file → ParsedFile: bookings, unusable rows, and a template's rooms and closures.

The header row may sit below a title; a file whose headers are the other
format's is read as that format. The template's grey «ΠΑΡΑΔΕΙΓΜΑ» rows are
skipped; a booking number repeated in the file keeps its last row.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from api.services.performance.importing.columns import columns_of
from api.services.performance.importing.parsed import ImportFileError, ParsedFile, ParsedRow
from api.services.performance.importing.rows import booking_rows, closure_row, room_row
from api.services.performance.importing.sheets import cell, find_header, missing_columns, read_sheets
from api.services.performance.importing.values import BadValue, is_empty, parse_reference, plain_text
from api.services.performance.template import (
    BOOKINGS_SHEET,
    CLOSURE_COLUMNS,
    CLOSURES_SHEET,
    EXAMPLE_PREFIX,
    MAX_ROWS,
    ROOM_COLUMNS,
    ROOMS_SHEET,
)

_EXAMPLE = plain_text(EXAMPLE_PREFIX)


def read_bookings_file(content: bytes, filename: str, requested_format: str) -> ParsedFile:
    """Every booking of the file, or ImportFileError when the file itself cannot be used."""
    if not content:
        raise ImportFileError("Το αρχείο είναι άδειο.")
    sheets = read_sheets(content, filename)
    # The bookings sheet of a template first.
    ordered = sorted(sheets.items(), key=lambda item: item[0] != BOOKINGS_SHEET)
    other = "booking" if requested_format == "template" else "template"
    for fmt in (requested_format, other):
        fields, required = columns_of(fmt)
        for _title, rows in ordered:
            header_index, columns = find_header(rows, fields, required)
            if columns:
                parsed = ParsedFile(format=fmt, rows=list(_bookings(rows, header_index, columns, fmt)))
                if fmt == "template":
                    _template_extras(sheets, parsed)
                return parsed
    fields, required = columns_of(requested_format)
    missing = missing_columns(next(iter(sheets.values()), []), fields, required)
    hint = (
        "Κατεβάστε το πρότυπο του RoomRate και συμπληρώστε το."
        if requested_format == "template"
        else "Ανεβάστε το export κρατήσεων του extranet όπως κατέβηκε."
    )
    raise ImportFileError(f"Δεν βρέθηκαν οι στήλες: {', '.join(missing)}. {hint}")


def _bookings(rows: Sequence[tuple], header_index: int, columns: dict[str, int], fmt: str) -> Iterator[ParsedRow]:
    filled = [
        (header_index + 2 + offset, row)
        for offset, row in enumerate(rows[header_index + 1:])
        if any(not is_empty(cell(row, columns, key)) for key in columns)
    ]
    if len(filled) > MAX_ROWS:
        count, limit = f"{len(filled):,}".replace(",", "."), f"{MAX_ROWS:,}".replace(",", ".")
        raise ImportFileError(f"Το αρχείο έχει {count} κρατήσεις· το όριο είναι {limit}.")
    kept: dict[str, ParsedRow] = {}
    for number, row in filled:
        reference = parse_reference(cell(row, columns, "booking_ref"))
        if fmt == "template" and reference and plain_text(reference).startswith(_EXAMPLE):
            continue
        for parsed in booking_rows(number, row, columns, fmt, reference):
            if parsed.booking is not None:
                earlier = kept.get(parsed.booking_ref)
                if earlier is not None:
                    earlier.booking = None
                    earlier.error = f"Ο ίδιος αριθμός κράτησης υπάρχει και στη γραμμή {number}· κρατήθηκε εκείνη."
                kept[parsed.booking_ref] = parsed
            yield parsed


def _template_extras(sheets: dict[str, list[tuple]], parsed: ParsedFile) -> None:
    """«Δωμάτια» and «Κλειστές περίοδοι» of a template, when present."""
    for title, spec, read_row, target in (
        (ROOMS_SHEET, ROOM_COLUMNS, room_row, parsed.rooms),
        (CLOSURES_SHEET, CLOSURE_COLUMNS, closure_row, parsed.closures),
    ):
        rows = sheets.get(title) or []
        fields = {column.key: (column.header,) for column in spec}
        header_index, columns = find_header(rows, fields, [column.key for column in spec if column.required])
        if not columns:
            continue
        for offset, row in enumerate(rows[header_index + 1:]):
            values = {key: cell(row, columns, key) for key in columns}
            if all(is_empty(value) for value in values.values()):
                continue
            try:
                target.append(read_row(values))
            except BadValue as exc:
                parsed.sheet_errors.append(
                    {"row": header_index + 2 + offset, "booking_ref": None, "message": f"Φύλλο «{title}»: {exc}"}
                )
