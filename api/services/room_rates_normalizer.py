from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from api.services.destination_aliases import canonical_destination


class RoomRateNormalizationError(ValueError):
    """Raised when a legacy room_rates row cannot be normalized safely."""


@dataclass(frozen=True)
class NormalizedRoomRate:
    provider: str
    source_record_id: str
    source_run_key: str
    source_property_key: str
    canonical_name: str
    display_name: str
    destination: str
    city: str
    country: str | None
    address: str | None
    property_type: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    stars: Decimal | None
    check_in: date
    check_out: date
    nights: int
    guests: int
    adults: int
    children: int
    rooms: int
    observed_at: datetime
    review_score: Decimal | None
    review_count: int | None
    room_type: str
    room_type_category: str
    meals: str | None
    free_cancellation: str | None
    price_per_night_eur: Decimal
    price_total_eur: Decimal
    rooms_left: int | None
    amenities: tuple[str, ...]
    raw_payload: dict[str, Any]
    payload_hash: str
    raw_destination: str | None = None
    canonical_destination: str | None = None
    # Lean JSON dict from room_matching.attributes_to_dict; None when nothing
    # could be extracted, so sparse rows stay lean in storage.
    room_attributes: dict[str, Any] | None = None
    # Round 6: the Booking listing URL (scraper CSV ``hotel_url``) for the map
    # popup link. Deliberately NOT part of source_property_key: changing the
    # key would give every stored property a new identity.
    booking_url: str | None = None
    # Rate plan (spec 2026-09-29 §3): when the columns are absent from a
    # legacy CSV, ALL fields default to NULL — has_genius_discount too (None,
    # not False, review 2026-09-29) — so a legacy re-ingest keeps
    # ``rate_plan: null`` end-to-end. False is stored only when the actor
    # explicitly said false.
    discounted_price_per_night_eur: Decimal | None = None
    discount_pct: Decimal | None = None
    discount_label: str | None = None
    has_genius_discount: bool | None = None
    cancellation_type: str | None = None
    payment_label: str | None = None
    rate_block_id: str | None = None


def normalize_text(value: object) -> str:
    """Normalize text for stable matching keys."""
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


# Cached: pure str -> str and called per candidate row in match scoring (the
# owned name is re-canonicalized inside compute_match_score for every row).
@lru_cache(maxsize=4096)
def canonicalize_name(value: object) -> str:
    """Build a canonical property name without losing the display name."""
    text = normalize_text(value).casefold()
    text = re.sub(r"[^\w\s&'-]+", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_amenity(value: object) -> str:
    """Normalize one amenity label for deduplication."""
    return canonicalize_name(value)


# Characters that survive matching-key compaction/tokenization: Latin and
# Greek lowercase letters (accented Greek vowels listed explicitly — this
# module never strips accents) plus digits. Shared by the scraper's room-name
# filtering and onboarding's candidate auto-selection so both sides build
# byte-identical keys.
_MATCHING_TOKEN_RE = re.compile(r"[a-z0-9α-ωάέήίόύώϊϋΐΰ]+")


def compact_matching_key(value: object) -> str:
    """Casefolded value reduced to its Latin/Greek letters and digits."""
    return "".join(_MATCHING_TOKEN_RE.findall(normalize_text(value).casefold()))


def matching_tokens(value: object) -> set[str]:
    """Set of Latin/Greek letter/digit tokens from the casefolded value."""
    return set(_MATCHING_TOKEN_RE.findall(normalize_text(value).casefold()))


# Cached: pure str -> str, recomputed per row/option in scraping and scoring.
@lru_cache(maxsize=4096)
def normalize_room_type_category(value: object) -> str:
    """Map noisy Booking room labels to a stable comparison category.

    Args:
        value: Raw room/package label from the scraper.

    Returns:
        Stable room category used for fair competitor matching.
    """
    normalized = canonicalize_name(value)
    if not normalized:
        return "other"

    category_keywords = (
        ("suite", ("suite", "suita", "σουίτα", "σουιτα")),
        ("studio", ("studio", "studios", "στούντιο", "στουντιο")),
        ("apartment", ("apartment", "apartments", "apt", "διαμέρισμα", "διαμερισμα")),
        ("family", ("family", "familial", "οικογενειακό", "οικογενειακο")),
        ("triple", ("triple", "τρίκλινο", "τρικλινο")),
        ("quadruple", ("quadruple", "quad", "τετράκλινο", "τετρακλινο")),
        ("twin", ("twin", "2 single", "2 μονά", "2 μονα", "δύο μονά", "δυο μονα", "μονά κρεβάτια", "μονα κρεβατια")),
        ("double", ("double", "dbl", "δίκλινο", "δικλινο", "διπλό", "διπλο")),
        ("single", ("single", "μονόκλινο", "μονοκλινο")),
        ("standard", ("standard", "classic", "basic", "room", "δωμάτιο", "δωματιο")),
    )
    for category, keywords in category_keywords:
        if any(keyword in normalized for keyword in keywords):
            return category
    return "other"


# Categories that describe the SAME sellable room and must stay comparable.
#
# Booking's Greek listings overwhelmingly read «Δίκλινο Δωμάτιο με 1 Διπλό ή 2
# Μονά Κρεβάτια» — ONE physical room offered with either bed layout — but
# normalize_room_type_category has to pick a single bucket for storage, and the
# twin keywords above win over the double ones. A category filter comparing for
# equality therefore threw away every such competitor room whenever the hotel's
# own room landed in the other bucket (live 2026-08-11: "Room type filter
# 'double': 29 -> 4 records"). Pools widen the *comparison*, never the stored
# vocabulary: rows and tracking write keys keep their own category. Comparison,
# tracked preselection, market-history reads and alert evaluation widen only at
# read time, so no stored category is renamed.
_COMPARABLE_CATEGORY_POOLS: tuple[tuple[str, ...], ...] = (
    ("double", "twin"),
)


def comparable_room_type_categories(value: str | None) -> tuple[str, ...]:
    """Return every stored category comparable with the given one.

    Args:
        value: A stored room category (e.g. ``"double"``), or None.

    Returns:
        The category's whole comparable pool in a stable order; ``(value,)``
        when the category is in no pool, and ``()`` for None/blank input.
    """
    category = (value or "").strip()
    if not category:
        return ()
    for pool in _COMPARABLE_CATEGORY_POOLS:
        if category in pool:
            return pool
    return (category,)


def normalize_room_type_category_key(value: str | None) -> str | None:
    """Normalize a user-facing room category label to its stable slug key.

    Shared by every API boundary that accepts a room category so the slug
    rules ("Double Room" -> "double_room") live in exactly one place.

    Args:
        value: Raw category label, or None.

    Returns:
        The slug key, or None when the input is None or blank.
    """
    if value is None:
        return None
    normalized = value.strip().lower().replace(" ", "_")
    return normalized or None


def split_facilities(value: object) -> tuple[str, ...]:
    """Split the legacy pipe-separated facilities field into unique labels."""
    facilities: dict[str, str] = {}
    for item in str(value or "").split("|"):
        display = normalize_text(item)
        normalized = normalize_amenity(display)
        if display and normalized:
            facilities.setdefault(normalized, display)
    return tuple(sorted(facilities.values(), key=str.casefold))


def parse_date(value: object, field_name: str) -> date:
    """Parse a date-like value from scraper output."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_text(value)
    if not text:
        raise RoomRateNormalizationError(f"{field_name} is required")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise RoomRateNormalizationError(f"{field_name} must use YYYY-MM-DD format") from exc


def parse_datetime(value: object, field_name: str) -> datetime:
    """Parse a datetime-like scraper timestamp."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = normalize_text(value)
    if not text:
        raise RoomRateNormalizationError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RoomRateNormalizationError(f"{field_name} must be an ISO datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_decimal(value: object, field_name: str, required: bool = False) -> Decimal | None:
    """Parse money, score and coordinate values into fixed precision Decimal."""
    text = normalize_text(value).replace(",", ".")
    if not text:
        if required:
            raise RoomRateNormalizationError(f"{field_name} is required")
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise RoomRateNormalizationError(f"{field_name} must be numeric") from exc


def parse_coordinate(value: object, field_name: str) -> Decimal | None:
    """Parse coordinates with six decimal places."""
    text = normalize_text(value).replace(",", ".")
    if not text:
        return None
    try:
        decimal_value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise RoomRateNormalizationError(f"{field_name} must be numeric") from exc
    coordinate = decimal_value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if field_name == "latitude" and not Decimal("-90") <= coordinate <= Decimal("90"):
        raise RoomRateNormalizationError("latitude out of range")
    if field_name == "longitude" and not Decimal("-180") <= coordinate <= Decimal("180"):
        raise RoomRateNormalizationError("longitude out of range")
    return coordinate


def parse_int(value: object, field_name: str, required: bool = False) -> int | None:
    """Parse non-negative integer fields."""
    text = normalize_text(value)
    if not text:
        if required:
            raise RoomRateNormalizationError(f"{field_name} is required")
        return None
    try:
        parsed = int(float(text.replace(",", ".")))
    except ValueError as exc:
        raise RoomRateNormalizationError(f"{field_name} must be an integer") from exc
    if parsed < 0:
        raise RoomRateNormalizationError(f"{field_name} cannot be negative")
    return parsed


def parse_optional_bool_flag(value: object) -> bool | None:
    """Parse writer bools plus their CSV round-trip spellings; absent stays None.

    Review 2026-09-29 (spec §3): a legacy CSV without the column keeps the
    field NULL end-to-end, so only a present value parses to a bool — False
    comes back only when the actor explicitly said false.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = normalize_text(value).casefold()
    if not text:
        return None
    return text in {"true", "1", "yes", "ναι"}


def parse_plan_label(value: object, max_length: int) -> str | None:
    """Rate-plan label clipped to its column length (spec §3 sizes).

    Defensive: one runaway actor string must not fail the batch INSERT of a
    whole scrape with StringDataRightTruncation.
    """
    text = normalize_text(value)
    return text[:max_length] or None


def parse_booking_url(value: object) -> str | None:
    """Return a plain http(s) listing URL, else None.

    The map popup links straight to this value in a new tab, so anything that
    is not a web URL (blank, pandas NaN rendered as "nan", a ``javascript:``
    scheme, a scheme-less host) is dropped instead of stored.
    """
    text = normalize_text(value)
    if not text.casefold().startswith(("https://", "http://")):
        return None
    return text


def hash_payload(payload: dict[str, Any]) -> str:
    """Create a deterministic hash for idempotent raw ingestion storage."""
    serialized = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_source_record_id(row: dict[str, Any]) -> str:
    """Return legacy record_id or a deterministic replacement when missing."""
    record_id = normalize_text(row.get("record_id"))
    if record_id:
        return record_id
    material = {
        "hotel_name": row.get("hotel_name"),
        "check_in": row.get("check_in"),
        "check_out": row.get("check_out"),
        "room_type": row.get("room_type"),
        "price_total_eur": row.get("price_total_eur"),
    }
    return hash_payload(material)[:24]


def normalize_room_rate_row(row: dict[str, Any], provider: str = "booking_com") -> NormalizedRoomRate:
    """Normalize one legacy room_rates row into production-ready fields.

    Args:
        row: Legacy room_rates row as a mapping.
        provider: External data provider key.

    Returns:
        Normalized room rate DTO.

    Raises:
        RoomRateNormalizationError: If required market fields are missing or invalid.
    """
    # DataFrame rows carry float NaN for missing optionals (the rate-plan
    # discount_pct is None-valued in the transform, and CSV round-trips leave
    # empty numeric cells as NaN). NaN would poison Decimal parsing, turn
    # label fields into the literal "nan", and break the JSONB payload write
    # (PostgreSQL rejects the NaN token) — so it becomes None up front, for
    # the parsed fields AND for raw_payload.
    row = {
        key: None if isinstance(value, float) and value != value else value
        for key, value in row.items()
    }
    display_name = normalize_text(row.get("hotel_name"))
    canonical_name = canonicalize_name(display_name)
    city = normalize_text(row.get("city"))
    if not display_name:
        raise RoomRateNormalizationError("hotel_name is required")
    if not canonical_name:
        raise RoomRateNormalizationError("canonical hotel name is required")
    if not city:
        raise RoomRateNormalizationError("city is required")

    check_in = parse_date(row.get("check_in"), "check_in")
    check_out = parse_date(row.get("check_out"), "check_out")
    if check_out <= check_in:
        raise RoomRateNormalizationError("check_out must be after check_in")

    observed_at = parse_datetime(row.get("scraped_at"), "scraped_at")
    nights = parse_int(row.get("nights"), "nights", required=True)
    guests = parse_int(row.get("guests"), "guests", required=True)
    adults_raw = parse_int(row.get("adults"), "adults")
    children_raw = parse_int(row.get("children"), "children")
    rooms_raw = parse_int(row.get("rooms"), "rooms")
    adults = adults_raw if adults_raw is not None else guests or 1
    children = children_raw if children_raw is not None else 0
    rooms = rooms_raw if rooms_raw is not None else 1
    if guests is not None and guests < 1:
        raise RoomRateNormalizationError("guests must be at least 1")
    if adults < 1:
        raise RoomRateNormalizationError("adults must be at least 1")
    if rooms < 1:
        raise RoomRateNormalizationError("rooms must be at least 1")
    price_per_night = parse_decimal(row.get("price_per_night_eur"), "price_per_night_eur", required=True)
    price_total = parse_decimal(row.get("price_total_eur"), "price_total_eur", required=True)
    if price_per_night is None or price_total is None:
        raise RoomRateNormalizationError("price fields are required")
    if price_per_night < 0 or price_total < 0:
        raise RoomRateNormalizationError("price fields cannot be negative")

    # Rate plan (spec 2026-09-29 §3): every field optional with a NULL
    # default, so old CSVs without the columns normalize exactly as before
    # and their re-ingest keeps rate_plan null (review 2026-09-29).
    discounted_price = parse_decimal(
        row.get("discounted_price_per_night_eur"), "discounted_price_per_night_eur"
    )
    if discounted_price is not None and discounted_price < 0:
        raise RoomRateNormalizationError("discounted_price_per_night_eur cannot be negative")
    discount_pct = parse_decimal(row.get("discount_pct"), "discount_pct")

    latitude = parse_coordinate(row.get("latitude"), "latitude")
    longitude = parse_coordinate(row.get("longitude"), "longitude")
    source_record_id = build_source_record_id(row)
    property_key_parts = [
        provider,
        canonical_name,
        city.casefold(),
        str(latitude or ""),
        str(longitude or ""),
    ]
    run_key_parts = [
        provider,
        city.casefold(),
        check_in.isoformat(),
        check_out.isoformat(),
        str(adults),
        str(children),
        str(rooms),
        observed_at.isoformat(),
    ]

    room_type = normalize_text(row.get("room_type")) or "Unknown room"

    # Local import: room_matching reuses canonicalize_name/normalize_text from
    # this module, so a top-level import here would be circular.
    from api.services.room_matching import attributes_to_dict, extract_room_attributes

    raw_payload = dict(row)
    room_attributes = attributes_to_dict(extract_room_attributes(room_type, raw_payload))

    return NormalizedRoomRate(
        provider=provider,
        source_record_id=source_record_id,
        source_run_key="|".join(run_key_parts),
        source_property_key="|".join(property_key_parts),
        canonical_name=canonical_name,
        display_name=display_name,
        destination=city,
        raw_destination=city,
        canonical_destination=canonical_destination(city),
        city=city,
        country=None,
        address=normalize_text(row.get("address")) or None,
        property_type=normalize_text(row.get("property_type")) or None,
        latitude=latitude,
        longitude=longitude,
        stars=parse_decimal(row.get("stars"), "stars"),
        check_in=check_in,
        check_out=check_out,
        nights=nights or 0,
        guests=guests or 0,
        adults=adults,
        children=children,
        rooms=rooms,
        observed_at=observed_at,
        review_score=parse_decimal(row.get("review_score"), "review_score"),
        review_count=parse_int(row.get("review_count"), "review_count"),
        room_type=room_type,
        room_type_category=normalize_room_type_category(room_type),
        meals=normalize_text(row.get("meals")) or None,
        free_cancellation=normalize_text(row.get("free_cancellation")) or None,
        price_per_night_eur=price_per_night,
        price_total_eur=price_total,
        rooms_left=parse_int(row.get("rooms_left"), "rooms_left"),
        amenities=split_facilities(row.get("facilities")),
        raw_payload=raw_payload,
        payload_hash=hash_payload(raw_payload),
        room_attributes=room_attributes or None,
        booking_url=parse_booking_url(row.get("hotel_url")),
        discounted_price_per_night_eur=discounted_price,
        discount_pct=discount_pct,
        discount_label=parse_plan_label(row.get("discount_label"), 40),
        has_genius_discount=parse_optional_bool_flag(row.get("has_genius_discount")),
        cancellation_type=parse_plan_label(row.get("cancellation_type"), 40),
        payment_label=parse_plan_label(row.get("payment_label"), 60),
        rate_block_id=parse_plan_label(row.get("rate_block_id"), 80),
    )
