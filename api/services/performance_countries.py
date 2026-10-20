"""Booker countries for «Απόδοση»: ISO 3166-1 alpha-2 codes, Greek names for the page.

The bookings store the code (``country_code``); the Booking.com export gives
a code ("de"), the RoomRate template a Greek or English name or a code. A
country outside this list keeps its code and is shown as the code.
"""

from __future__ import annotations

import unicodedata

UNKNOWN_COUNTRY = "Άγνωστη"
OTHER_COUNTRIES = "Λοιπές"

# code: (Greek, English)
COUNTRIES: dict[str, tuple[str, str]] = {
    "GR": ("Ελλάδα", "Greece"),
    "CY": ("Κύπρος", "Cyprus"),
    "DE": ("Γερμανία", "Germany"),
    "GB": ("Ηνωμένο Βασίλειο", "United Kingdom"),
    "IE": ("Ιρλανδία", "Ireland"),
    "IT": ("Ιταλία", "Italy"),
    "FR": ("Γαλλία", "France"),
    "ES": ("Ισπανία", "Spain"),
    "PT": ("Πορτογαλία", "Portugal"),
    "NL": ("Ολλανδία", "Netherlands"),
    "BE": ("Βέλγιο", "Belgium"),
    "LU": ("Λουξεμβούργο", "Luxembourg"),
    "AT": ("Αυστρία", "Austria"),
    "CH": ("Ελβετία", "Switzerland"),
    "DK": ("Δανία", "Denmark"),
    "SE": ("Σουηδία", "Sweden"),
    "NO": ("Νορβηγία", "Norway"),
    "FI": ("Φινλανδία", "Finland"),
    "IS": ("Ισλανδία", "Iceland"),
    "PL": ("Πολωνία", "Poland"),
    "CZ": ("Τσεχία", "Czech Republic"),
    "SK": ("Σλοβακία", "Slovakia"),
    "HU": ("Ουγγαρία", "Hungary"),
    "RO": ("Ρουμανία", "Romania"),
    "BG": ("Βουλγαρία", "Bulgaria"),
    "RS": ("Σερβία", "Serbia"),
    "HR": ("Κροατία", "Croatia"),
    "SI": ("Σλοβενία", "Slovenia"),
    "AL": ("Αλβανία", "Albania"),
    "MK": ("Βόρεια Μακεδονία", "North Macedonia"),
    "TR": ("Τουρκία", "Turkey"),
    "UA": ("Ουκρανία", "Ukraine"),
    "LT": ("Λιθουανία", "Lithuania"),
    "LV": ("Λετονία", "Latvia"),
    "EE": ("Εσθονία", "Estonia"),
    "MT": ("Μάλτα", "Malta"),
    "IL": ("Ισραήλ", "Israel"),
    "LB": ("Λίβανος", "Lebanon"),
    "EG": ("Αίγυπτος", "Egypt"),
    "AE": ("Ηνωμένα Αραβικά Εμιράτα", "United Arab Emirates"),
    "SA": ("Σαουδική Αραβία", "Saudi Arabia"),
    "US": ("Η.Π.Α.", "United States"),
    "CA": ("Καναδάς", "Canada"),
    "MX": ("Μεξικό", "Mexico"),
    "BR": ("Βραζιλία", "Brazil"),
    "AR": ("Αργεντινή", "Argentina"),
    "AU": ("Αυστραλία", "Australia"),
    "NZ": ("Νέα Ζηλανδία", "New Zealand"),
    "ZA": ("Νότια Αφρική", "South Africa"),
    "CN": ("Κίνα", "China"),
    "JP": ("Ιαπωνία", "Japan"),
    "KR": ("Νότια Κορέα", "South Korea"),
    "IN": ("Ινδία", "India"),
    "SG": ("Σιγκαπούρη", "Singapore"),
    "TW": ("Ταϊβάν", "Taiwan"),
    "HK": ("Χονγκ Κονγκ", "Hong Kong"),
}

# Other spellings owners and exports use.
_ALIASES = {
    "UK": "GB",
    "ENGLAND": "GB",
    "GREAT BRITAIN": "GB",
    "ΑΓΓΛΙΑ": "GB",
    "ΜΕΓΑΛΗ ΒΡΕΤΑΝΙΑ": "GB",
    "ΒΡΕΤΑΝΙΑ": "GB",
    "USA": "US",
    "UNITED STATES OF AMERICA": "US",
    "ΗΠΑ": "US",
    "ΑΜΕΡΙΚΗ": "US",
    "HOLLAND": "NL",
    "THE NETHERLANDS": "NL",
    "CZECHIA": "CZ",
    "TURKIYE": "TR",
    "ΤΟΥΡΚΙΑ": "TR",
    "ΣΚΟΠΙΑ": "MK",
    "ΕΛΛΑΣ": "GR",
}


def _key(value: str) -> str:
    """Upper case without accents or dots: «Η.Π.Α.» → «ΗΠΑ», «Γερμανία» → «ΓΕΡΜΑΝΙΑ»."""
    decomposed = unicodedata.normalize("NFD", value.strip())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.replace(".", "").upper().split())


_BY_NAME: dict[str, str] = {**_ALIASES}
for _code, (_greek, _english) in COUNTRIES.items():
    _BY_NAME[_key(_greek)] = _code
    _BY_NAME[_key(_english)] = _code


def country_code(value: object) -> str | None:
    """The ISO code of a code or a Greek/English name; None for an empty cell or a name not known.

    A two-letter value is taken as a code even outside the list ("is" → "IS").
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = _key(text)
    if key in _BY_NAME:
        return _BY_NAME[key]
    if len(key) == 2 and key.isascii() and key.isalpha():
        return key
    return None


def country_name(code: str | None) -> str:
    """The Greek name of a stored code; «Άγνωστη» without one."""
    if not code:
        return UNKNOWN_COUNTRY
    known = COUNTRIES.get(code.upper())
    return known[0] if known else code.upper()
