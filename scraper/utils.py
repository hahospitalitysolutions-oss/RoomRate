"""Pure cleaning, matching and error-log helpers for scraper data."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pandas as pd

from .config import ScraperConfig
from .logging_config import log


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _safe_coord(value, default: float = 0.0) -> float:
    """
    Όπως _safe_float αλλά κρατάει 6 δεκαδικά για συντεταγμένες.
    6dp = ~0.1m ακρίβεια — απαραίτητο για σωστό Haversine.
    Με 2dp όλα τα Faliraki hotels στρογγυλοποιούνται στο ίδιο
    (36.34, 28.20) και η απόσταση βγαίνει 0.0.
    """
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return default


def _fix_encoding(s: str) -> str:
    """
    Διορθώνει text που επέστρεψε ο Apify με λάθος encoding.

    Στρατηγική:
      1. Δοκιμάζει Latin-1 → UTF-8 (διορθώνει το 'Î¿Î´Î¿Ï' pattern)
      2. Αν αποτύχει ή το αποτέλεσμα περιέχει garble chars (π.χ. '¿'),
         επιστρέφει "" — κενό είναι καλύτερο από garbled text στη βάση.

    Γιατί εμφανίζεται το '¿' pattern: το Apify actor επιστρέφει bytes
    που δεν είναι valid UTF-8 start bytes (π.χ. 0xBF = '¿'), οπότε το
    latin-1→utf-8 decode αποτυγχάνει και ο παλιός κώδικας επέστρεφε
    το garbled string αναλλοίωτο.
    """
    if not s or not isinstance(s, str):
        return s or ""

    # Attempt: Latin-1 → UTF-8 (fixes the Î pattern)
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        if any("\u0370" <= c <= "\u03ff" for c in fixed):
            return fixed
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass  # fall through to garble detection

    # If the string contains characters that NEVER appear in valid
    # Greek addresses, it's garbled → return empty string
    _GARBLE = frozenset("¿½¼¾")
    if any(c in _GARBLE for c in s):
        return ""

    return s


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: object) -> str:
    return " ".join(_fix_encoding(str(value or "")).split())


def _normalize_facilities(raw_facilities: list) -> str:
    values: dict[str, str] = {}
    for item in raw_facilities or []:
        label = item.get("name", "") if isinstance(item, dict) else str(item)
        display = _clean_text(label)
        key = display.casefold()
        if display and key:
            values.setdefault(key, display)
    return "|".join(sorted(values.values(), key=str.casefold))


def _make_record_id(property_identity: str, room_name: str, package_id: str, config: ScraperConfig) -> str:
    """Deterministic dedup key for one property × room × option."""
    raw = "|".join(
        [
            property_identity,
            room_name,
            package_id,
            config.check_in.strftime("%Y-%m-%d"),
            config.check_out.strftime("%Y-%m-%d"),
            str(config.adults),
            str(config.children),
            str(config.rooms),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _chunked(lst: list, size: int) -> list[list]:
    """Σπάει μια λίστα σε chunks του `size`."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def _normalize_url(url: str) -> str:
    """
    Αφαιρεί query params και fragment από ένα Booking.com URL.

    Γιατί χρειάζεται:
      Stage 1 (Scout) επιστρέφει:
        https://www.booking.com/hotel/gr/lago-beach-living.el.html

      Stage 2 (Deep Crawl) επιστρέφει:
        https://www.booking.com/hotel/gr/lago-beach-living.el.html
        ?checkin=2026-06-15&checkout=2026-06-20&group_adults=2&...

      Χωρίς normalization το meta_by_url.get(deep_crawl_url) αποτυγχάνει
      πάντα → όλα τα metadata (lat/lng/stars/reviews) βγαίνουν 0.
    """
    if not url:
        return ""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _append_error_log(config: ScraperConfig, errors: list[dict[str, object]]) -> None:
    """Append anonymized record failures to the configured CSV error log.

    Args:
        config: Active scraper configuration.
        errors: Rows containing ``field``, ``issue`` and ``value`` keys.

    Returns:
        None.

    Raises:
        OSError: If the error log cannot be created or appended.
    """
    if not errors or config.dry_run:
        return
    path = Path(config.error_log_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "client_id": str(config.account_id),
            "field": error.get("field", "scrape"),
            "issue": _clean_text(error.get("issue", "unknown error")),
            "value": _clean_text(error.get("value", "")),
            "timestamp": timestamp,
        }
        for error in errors
    ]
    try:
        pd.DataFrame(rows).to_csv(
            path,
            mode="a",
            header=not path.exists(),
            index=False,
            encoding="utf-8-sig",
        )
    except OSError:
        log.exception("Αποτυχία εγγραφής error log στο %s", path)
        raise


def _extract_meta(item: dict) -> dict:
    """
    Εξάγει metadata ξενοδοχείου από Apify voyager/booking-scraper item.

    Πεδία όπως επιβεβαιώθηκαν από το raw actor output:
      location   → {"lat": "36.34...", "lng": "28.20..."}  (strings!)
      rating     → 8.8   (= review score, ΟΧΙ reviewScore)
      reviews    → 185   (= review count, ΟΧΙ reviewCount)
      stars      → None για apartments, αριθμός για hotels
      type       → "apartment" / "hotel" (ΟΧΙ propertyType)
      address    → {"full": "...", "country": "gr", "city": "..."}
      ratingLabel→ "Θαυμάσιο" (κατηγορία rating)
    """
    # --- Coordinates (nested dict, values are strings) ---
    loc = item.get("location") or {}
    lat = _safe_coord(loc.get("lat") or loc.get("latitude"))
    lng = _safe_coord(loc.get("lng") or loc.get("longitude"))

    # --- Address (nested dict) ---
    addr     = item.get("address") or {}
    city     = _fix_encoding(addr.get("city") or addr.get("cityName") or item.get("city") or "")
    address  = _fix_encoding(addr.get("full") or addr.get("address") or "")

    # --- Property type ---
    prop_type = item.get("type") or item.get("propertyType") or "Hotel"

    # --- Review score: field name is "rating" in this actor ---
    review_score = _safe_float(
        item.get("rating") or item.get("reviewScore") or item.get("guestRating")
    )

    # --- Review count: field name is "reviews" in this actor ---
    review_count = _safe_int(
        item.get("reviews") or item.get("reviewCount") or item.get("reviewsCount")
    )

    # --- Stars: None for non-hotels, keep 0.0 as default ---
    stars = _safe_float(item.get("stars") or item.get("starRating") or 0.0)

    # --- Distance: not provided by this actor — computed later via Haversine ---
    return {
        "latitude":      lat,
        "longitude":     lng,
        "stars":         stars,
        "review_score":  review_score,
        "review_count":  review_count,
        "property_type": prop_type.capitalize(),
        "city":          city,
        "address":       address,
    }


_MEAL_KEYWORDS = frozenset([
    "πρωινό", "γεύμα", "ημιδιατροφή", "breakfast",
    "dinner", "lunch", "all inclusive", "διατροφή",
    "half board", "full board",
])

def _detect_meal(choices: list) -> str:
    for c in choices:
        if any(kw in str(c).lower() for kw in _MEAL_KEYWORDS):
            return str(c).capitalize()
    return ""


# Rate plans (spec 2026-09-29 §2): the CSV stores the CANONICAL label, not the
# raw yourChoices entry, so the UI chips never depend on Booking's phrasing.
_PAYMENT_LABELS = ("Πληρωμή online", "Πληρωμή στο κατάλυμα")


def _detect_payment(choices: list) -> str | None:
    """Return the canonical payment label found in yourChoices, else None."""
    for choice in choices or []:
        # _clean_text also repairs the mojibake the actor sometimes returns.
        normalized = _clean_text(choice).casefold()
        for label in _PAYMENT_LABELS:
            if label.casefold() in normalized:
                return label
    return None


# Matches the percent token of the actor's discount text («- 8%», «-12,5 %»).
_DISCOUNT_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def _parse_discount_pct(text: object) -> float | None:
    """Parse «- 8%» → 8.0 (the size of the discount, always positive)."""
    match = _DISCOUNT_PCT_RE.search(str(text or ""))
    if not match:
        return None
    return round(float(match.group(1).replace(",", ".")), 1)


def _clean_discount_label(text: object) -> str | None:
    """Compact the actor's discount text («- 8%» → «-8%»), None when empty."""
    cleaned = re.sub(r"\s+", "", _clean_text(text))
    return cleaned or None


def _normalize_required_amenities(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(
        str(value).strip().lower()
        for value in values
        if str(value).strip()
    )


def _has_required_amenities(facilities: str, required_amenities: tuple[str, ...]) -> bool:
    if not required_amenities:
        return True
    normalized_facilities = facilities.lower()
    return all(amenity in normalized_facilities for amenity in required_amenities)

# Compatibility re-exports for callers that previously used scraper.utils.
from .matching import (
    _filter_by_room_name,
    _matches_required_free_cancellation,
    _matches_required_meal,
)
