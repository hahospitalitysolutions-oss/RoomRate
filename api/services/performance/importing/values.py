"""One cell of an owner's file → a value: dates, amounts, whole numbers, booking numbers, statuses.

Dates come as Excel dates, serial numbers or text (14/07/2026, 2026-07-14,
14.07.2026, «14 Jul 2026», with or without a time); amounts as numbers or
text with either decimal mark and a currency («1.234,50 €», «436 EUR»).
A value that cannot be read raises BadValue with the text as found.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

from api.services.performance.template import STATUSES

_EXCEL_EPOCH = date(1899, 12, 30)
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%y",
    "%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%y",
    "%d-%m-%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
    "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
)
_EURO = re.compile(r"(?i)\s*(€|euros?|eur|ευρώ|ευρω)\s*")
_OTHER_CURRENCY = re.compile(r"[A-Za-z]{3}|[$£¥₺]")


class BadValue(ValueError):
    """A cell that cannot be read; ``str()`` is the text as found."""


def is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def plain_text(value: object) -> str:
    """Lower case, no accents, single spaces, no trailing «*»: how headers and words are compared."""
    text = unicodedata.normalize("NFD", str(value)).strip().rstrip("*").strip()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.casefold().split())


# Every way a status is written → confirmed / cancelled / no_show, compared as plain_text.
_STATUS_WORDS = {
    plain_text(word): code
    for word, code in {
        **STATUSES,
        "ok": "confirmed", "confirmed": "confirmed", "επιβεβαιωμενο": "confirmed",
        "cancelled": "cancelled", "canceled": "cancelled", "ακυρωση": "cancelled",
        "no_show": "no_show", "no show": "no_show", "noshow": "no_show",
    }.items()
}


def parse_date(value: object) -> date | None:
    """A date from an Excel cell or text; None for an empty cell."""
    if is_empty(value):
        return None
    if isinstance(value, datetime):
        result = value.date()
    elif isinstance(value, date):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not 20_000 <= value <= 80_000:  # an Excel serial date between 1954 and 2119
            raise BadValue(str(value))
        result = _EXCEL_EPOCH + timedelta(days=int(value))
    else:
        result = _date_from_text(" ".join(str(value).split()))
    if not 2000 <= result.year <= 2100:
        raise BadValue(str(value))
    return result


def _date_from_text(text: str) -> date:
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise BadValue(text)


def parse_amount(value: object) -> float | None:
    """Euros from a number or text («1.234,50 €», «1,234.50», «436 EUR»); None for an empty cell."""
    if is_empty(value):
        return None
    if isinstance(value, bool):
        raise BadValue(str(value))
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = _EURO.sub("", str(value)).replace(" ", "").replace(" ", "")
    if not re.fullmatch(r"-?[\d.,]+", text) or not re.search(r"\d", text):
        raise BadValue(str(value).strip())
    if "," in text and "." in text:
        decimal_mark = "," if text.rfind(",") > text.rfind(".") else "."
    elif text.count(",") == 1 or text.count(".") == 1:
        # One mark followed by exactly three digits groups thousands («1.234», «1,234»).
        decimal_mark = None if re.search(r"\d[.,]\d{3}$", text) else ("," if "," in text else ".")
    else:
        decimal_mark = None  # «1.234.567» or no mark at all
    for mark in {",", "."} - {decimal_mark}:
        text = text.replace(mark, "")
    return round(float(text.replace(",", ".")), 2)


def in_other_currency(value: object) -> bool:
    """Text with digits and a currency other than the euro («300 USD», «$300»)."""
    return (
        isinstance(value, str)
        and bool(re.search(r"\d", value))
        and bool(_OTHER_CURRENCY.search(_EURO.sub(" ", value)))
    )


def parse_int(value: object) -> int | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        raise BadValue(str(value))
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d+([.,]0+)?", text):
        return int(re.split(r"[.,]", text)[0])
    raise BadValue(text)


def parse_reference(value: object) -> str | None:
    """A booking number as text: 4018833201 stays «4018833201», never «4018833201.0»."""
    if is_empty(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())[:100]


def parse_status(value: object) -> str:
    """confirmed / cancelled / no_show from the template's Greek words or the export's codes."""
    if is_empty(value):
        return "confirmed"
    word = plain_text(value)
    if word in _STATUS_WORDS:
        return _STATUS_WORDS[word]
    if word.startswith("cancel") or word.startswith("ακυρ"):
        return "cancelled"  # cancelled_by_guest, cancelled_by_hotel, …
    raise BadValue(str(value).strip())


def parse_label(value: object, limit: int = 255) -> str | None:
    """Text with single spaces, cut at ``limit``; None for an empty cell."""
    if is_empty(value):
        return None
    return " ".join(str(value).split())[:limit]
