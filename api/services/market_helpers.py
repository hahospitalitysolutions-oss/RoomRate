import math
from collections import defaultdict
from typing import Literal

from api.services.room_rates_normalizer import comparable_room_type_categories

EARTH_RADIUS_KM = 6371.0088

CategoryMatch = Literal["same", "similar"]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km, rounded to 1 decimal.

    The ONE implementation for the scraper's radius filter and the read
    API's ``distance_km``, so the scout and the map never disagree.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a)), 1)


def group_by_hotel(rows: list[dict]) -> dict[str, list[dict]]:
    """Group rate rows per property, in first-seen order.

    Keyed on ``property_id`` when the row has one, so two different
    properties sharing a display name (two «Studios Maria» in different
    villages, now common with the nearby-area scouts) stay two competitors
    and two markers. Rows without an id (the legacy ``room_rates`` source)
    fall back to the display name. Nameless rows are skipped as before.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = hotel_group_key(row)
        if key is None:
            continue
        grouped[key].append(row)
    return grouped


def hotel_group_key(row: dict) -> str | None:
    """The per-property identity ``group_by_hotel`` groups on; None for nameless rows."""
    hotel_name = str(row.get("hotel_name") or "").strip()
    if not hotel_name:
        return None
    property_id = row.get("property_id")
    return f"property:{property_id}" if property_id else f"name:{hotel_name}"


def exclude_hotel(rows: list[dict], hotel_name: str | None) -> list[dict]:
    if not hotel_name:
        return rows
    normalized = hotel_name.strip().lower()
    return [row for row in rows if str(row.get("hotel_name") or "").strip().lower() != normalized]


def as_float(value: object) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def as_optional_float(value: object) -> float | None:
    """None for None/unparseable values, else float(value) — callers round.

    Unlike ``as_float`` this never coerces missing data to 0.0, so a genuine
    0.0 stays distinguishable from "no value".
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def effective_price_per_night(row: dict) -> float | None:
    """The EFFECTIVE nightly price of one package row, or None without a price.

    Owner decision 2026-09-30: what an anonymous guest actually pays — the
    discounted price (e.g. «-8% πληρωμή online») when Booking showed one,
    else the base ``price_per_night_eur``. Rows written before migration 0025
    (and legacy-source rows) carry NULL and fall back to base. Python twin of
    ``EFFECTIVE_PRICE_SQL`` in the price-history repository.
    """
    discounted = as_optional_float(row.get("discounted_price_per_night_eur"))
    if discounted is not None and math.isfinite(discounted):
        return discounted
    return as_optional_float(row.get("price_per_night_eur"))


def as_coord(value: object) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def category_match_for(category: str | None, baseline: str | None) -> CategoryMatch:
    """Label one package «ίδια κατηγορία» ("same") or «Παρόμοιο» ("similar").

    With a baseline, "same" means the row's category is in the baseline's
    comparable pool (double and twin are one sellable room); any other
    category, including an unknown (None) one, is "similar". Without a
    baseline there is nothing to compare against, so every row is "same" and
    the «Μόνο ίδια κατηγορία» toggle can never hide rows (Round 6 §3.6).
    """
    pool = comparable_room_type_categories(baseline)
    if not pool:
        return "same"
    return "same" if (category or "").strip() in pool else "similar"


def distance_km_from(origin: tuple[float, float] | None, lat: object, lng: object) -> float | None:
    """km from the owner's property to a competitor, or None when unknown.

    None when there is no origin or either end lacks a coordinate: missing,
    unparseable or 0.0 (how a missing coordinate ends up stored). Otherwise
    the shared ``haversine_km``, so the map and the scraper's radius agree.
    """
    if origin is None:
        return None
    points = (origin[0], origin[1], lat, lng)
    coordinates = [as_optional_float(value) for value in points]
    if any(not coordinate for coordinate in coordinates):
        return None
    origin_lat, origin_lng, target_lat, target_lng = coordinates
    return haversine_km(origin_lat, origin_lng, target_lat, target_lng)
