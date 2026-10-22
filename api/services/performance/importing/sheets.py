"""An uploaded file → its sheets' rows, and where in a sheet the header row is.

.xlsx is read with openpyxl (values, not formulas); .csv in UTF-8, Windows
Greek or Latin-1, comma, semicolon or tab separated. An old .xls or any
other file is refused with a message the owner can act on.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from openpyxl import load_workbook

from api.services.performance.importing.columns import FIELD_NAMES, Fields
from api.services.performance.importing.parsed import ImportFileError
from api.services.performance.importing.values import plain_text

# The header may sit below a title: it is looked for in the first rows.
HEADER_SEARCH_ROWS = 10
NOT_A_SPREADSHEET = "Το αρχείο δεν είναι .xlsx ή .csv."


def read_sheets(content: bytes, filename: str) -> dict[str, list[tuple]]:
    """Every sheet's rows as tuples, by sheet title (a .csv is one sheet, «csv»)."""
    name = filename.lower()
    if content[:4] == b"PK\x03\x04":
        return _workbook_rows(content)
    if content[:4] == b"\xd0\xcf\x11\xe0" or name.endswith(".xls"):
        raise ImportFileError(
            "Το αρχείο είναι παλιού τύπου Excel (.xls). Ανοίξτε το στο Excel και αποθηκεύστε το ως .xlsx ή .csv."
        )
    if name.endswith((".csv", ".txt")):
        return {"csv": _csv_rows(content)}
    raise ImportFileError(NOT_A_SPREADSHEET)


def _workbook_rows(content: bytes) -> dict[str, list[tuple]]:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # a damaged or password-protected workbook
        raise ImportFileError("Το αρχείο Excel δεν διαβάζεται. Ανοίξτε το και αποθηκεύστε το ξανά ως .xlsx.") from exc
    try:
        return {sheet.title: list(sheet.iter_rows(values_only=True)) for sheet in workbook.worksheets}
    finally:
        workbook.close()


def _csv_rows(content: bytes) -> list[tuple]:
    text = next(decoded for decoded in _decodings(content) if decoded is not None)
    if "\x00" in text:
        raise ImportFileError(NOT_A_SPREADSHEET)
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    return [tuple(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _decodings(content: bytes):
    for encoding in ("utf-8-sig", "cp1253", "latin-1"):  # latin-1 always decodes
        try:
            yield content.decode(encoding)
        except UnicodeDecodeError:
            yield None


def header_columns(header: Sequence[object], fields: Fields) -> dict[str, int]:
    """Field → index of the first column whose header names it."""
    plain = [plain_text(cell) if cell is not None else "" for cell in header]
    found: dict[str, int] = {}
    for key, names in fields.items():
        for name in names:
            wanted = plain_text(name)
            if wanted in plain:
                found[key] = plain.index(wanted)
                break
    return found


def find_header(rows: Sequence[tuple], fields: Fields, required: Sequence[str]) -> tuple[int, dict[str, int]]:
    """(index of the header row, its columns); columns {} when no early row has every required field."""
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        columns = header_columns(row, fields)
        if all(key in columns for key in required):
            return index, columns
    return 0, {}


def missing_columns(rows: Sequence[tuple], fields: Fields, required: Sequence[str]) -> list[str]:
    """The required columns the closest header row lacks, as the owner calls them."""
    best: dict[str, int] = {}
    for row in rows[:HEADER_SEARCH_ROWS]:
        columns = header_columns(row, fields)
        if len(columns) > len(best):
            best = columns
    return [FIELD_NAMES.get(key, key) for key in required if key not in best]


def cell(row: tuple, columns: dict[str, int], key: str) -> object:
    """The row's value for a field; None when the file has no such column."""
    index = columns.get(key)
    if index is None or index >= len(row):
        return None
    return row[index]
