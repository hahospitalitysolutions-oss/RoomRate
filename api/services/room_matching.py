"""Room matching v2: attribute extraction + fuzzy 0-100 match scoring.

Scoring formula (see :func:`compute_match_score`):

    base  = 0.6 * rapidfuzz.fuzz.token_set_ratio(
                canonicalize_name(owned_room_name),
                canonicalize_name(candidate_room_name),
            )
    +20   when same_category is True
    +15   when both capacities are known and equal
    +10   when both views are known and equal
    +5    when both rooms mention a balcony (extraction yields True or
          None, never False, so the bonus only ever fires on True/True)
    +10   when both sizes are known and within ±20% of the owned size

    Hard conflicts CAP the final score at 40:
      * capacities known on both sides and differ by more than 1
      * views known on both sides and different

    The result is clamped to [0, 100].

Attribute bonuses apply ONLY when both sides know the attribute — a missing
attribute is never a penalty, so sparse scraper labels stay comparable.

Greek keyword handling mirrors ``room_rates_normalizer``: the normalizer does
NOT strip accents, it lists accented and unaccented keyword variants side by
side (δίκλινο/δικλινο), so this module does the same.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from api.services.room_rates_normalizer import canonicalize_name, normalize_text

# Score weights and caps (documented in the module docstring).
_NAME_WEIGHT = 0.6
_SAME_CATEGORY_BONUS = 20.0
_CAPACITY_BONUS = 15.0
_VIEW_BONUS = 10.0
_BALCONY_BONUS = 5.0
_SIZE_BONUS = 10.0
_SIZE_TOLERANCE = 0.20
_HARD_CONFLICT_CAP = 40.0
_CAPACITY_CONFLICT_GAP = 1

# Sanity bounds so junk payloads never produce absurd attributes.
_MAX_CAPACITY = 20
_MAX_SIZE_SQM = 1_000.0

# Payload keys for capacity/size hints. `max_persons` is the key our own
# scraper actually emits (scraper.transform flattens the Apify option-level
# `persons` field into it); the remaining keys are forward-compat guesses for
# other payload shapes. The scraper currently produces NO size field — the
# Apify room dicts it handles carry no roomSize/size key — so every size key
# below is forward-compat only.
_PAYLOAD_GUEST_KEYS = ("max_persons", "maxGuests", "max_guests", "persons", "occupancy")
_PAYLOAD_SIZE_KEYS = ("roomSize", "room_size_sqm", "size")

# EN + GR keywords. Greek entries carry accented and unaccented variants,
# consistent with normalize_room_type_category in room_rates_normalizer.
_CAPACITY_KEYWORDS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (4, ("quadruple", "quad", "τετράκλινο", "τετρακλινο")),
    (3, ("triple", "τρίκλινο", "τρικλινο")),
    (2, ("double", "twin", "δίκλινο", "δικλινο")),
    (1, ("single room", "μονόκλινο", "μονοκλινο")),
)
# "Room for 4 people" / "Δωμάτιο για 3 άτομα". A person word is required so
# offer phrasing like "Special offer for 2 nights" / "για 2 βράδια" does not
# false-positive as capacity.
_CAPACITY_PATTERNS = (
    re.compile(r"\bfor\s+(\d{1,2})\s+(?:people|persons?|adults?|guests?)\b"),
    # [ςσ]: names are casefolded before matching and casefold() maps the
    # Greek final sigma "ς" to "σ", so both spellings must be accepted.
    re.compile(r"\bγια\s+(\d{1,2})\s+(?:άτομα|ατομα|ενήλικε[ςσ]|ενηλικε[ςσ]|επισκέπτε[ςσ]|επισκεπτε[ςσ])\b"),
)

_VIEW_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sea", ("sea view", "θέα θάλασσα", "θεα θαλασσα", "θάλασσα", "θαλασσα")),
    ("garden", ("garden view", "κήπο", "κηπο")),
    ("pool", ("pool view", "θέα στην πισίνα", "θεα στην πισινα")),
    ("mountain", ("mountain view", "βουνό", "βουνο")),
    ("city", ("city view", "πόλη", "πολη")),
)

_BALCONY_KEYWORDS = ("balcony", "μπαλκόνι", "μπαλκονι")

# "25 m²" / "40 sqm" / "32.5 m2" / "28 τ.μ."
_SIZE_PATTERN = re.compile(
    r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:m²|m2|sqm|sq\.?\s?m|τ\.?\s?μ\.?)",
    flags=re.IGNORECASE,
)

# Order matters: specific bed words win over generic double/single.
_BED_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("king", ("king",)),
    ("queen", ("queen",)),
    ("twin", ("twin", "2 single", "μονά κρεβάτια", "μονα κρεβατια")),
    ("sofa", ("sofa", "καναπέ", "καναπε")),
    ("double", ("double", "dbl", "δίκλινο", "δικλινο", "διπλό", "διπλο")),
    ("single", ("single", "μονόκλινο", "μονοκλινο")),
)


@dataclass(frozen=True)
class RoomAttributes:
    """Structured room facts extracted from noisy labels and payloads."""

    capacity: int | None
    view: str | None
    has_balcony: bool | None
    size_sqm: float | None
    bed_hint: str | None


_EMPTY_ATTRIBUTES = RoomAttributes(capacity=None, view=None, has_balcony=None, size_sqm=None, bed_hint=None)


def _contains_keyword(normalized_name: str, keyword: str) -> bool:
    """Whole-word keyword check, so e.g. 'Booking' never matches 'king'."""
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized_name) is not None


def _parse_capacity(value: object) -> int | None:
    """Parse a plausible guest count from arbitrary payload data."""
    try:
        capacity = int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None
    if 1 <= capacity <= _MAX_CAPACITY:
        return capacity
    return None


def _parse_size(value: object) -> float | None:
    """Parse a plausible room size in m² from arbitrary payload data."""
    try:
        size = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if math.isnan(size) or math.isinf(size) or not 0 < size <= _MAX_SIZE_SQM:
        return None
    return size


def _capacity_from_name(normalized_name: str) -> int | None:
    for capacity, keywords in _CAPACITY_KEYWORDS:
        if any(_contains_keyword(normalized_name, keyword) for keyword in keywords):
            return capacity
    for pattern in _CAPACITY_PATTERNS:
        match = pattern.search(normalized_name)
        if match:
            return _parse_capacity(match.group(1))
    return None


def _view_from_name(normalized_name: str) -> str | None:
    for view, keywords in _VIEW_KEYWORDS:
        if any(_contains_keyword(normalized_name, keyword) for keyword in keywords):
            return view
    return None


def _size_from_name(normalized_name: str) -> float | None:
    match = _SIZE_PATTERN.search(normalized_name)
    return _parse_size(match.group(1)) if match else None


def _bed_hint_from_name(normalized_name: str) -> str | None:
    for bed, keywords in _BED_KEYWORDS:
        if any(_contains_keyword(normalized_name, keyword) for keyword in keywords):
            return bed
    return None


def extract_room_attributes(room_type: str, package_payload: dict | None = None) -> RoomAttributes:
    """Extract comparable room attributes from a room label and raw payload.

    The room label wins over payload hints (a "Triple Room" stays capacity 3
    even when the search asked for 2 guests). Payload parsing is defensive:
    unknown shapes and junk values are silently ignored, never raised.
    """
    # Casefold + collapse whitespace, but keep accents: keyword tables list
    # accented and unaccented Greek variants explicitly (normalizer style).
    normalized_name = normalize_text(room_type).casefold()

    capacity = _capacity_from_name(normalized_name)
    size_sqm = _size_from_name(normalized_name)

    if isinstance(package_payload, dict):
        if capacity is None:
            for key in _PAYLOAD_GUEST_KEYS:
                if key in package_payload:
                    capacity = _parse_capacity(package_payload.get(key))
                    if capacity is not None:
                        break
        if size_sqm is None:
            for key in _PAYLOAD_SIZE_KEYS:
                if key in package_payload:
                    size_sqm = _parse_size(package_payload.get(key))
                    if size_sqm is not None:
                        break

    return RoomAttributes(
        capacity=capacity,
        view=_view_from_name(normalized_name),
        # Absence of the word "balcony" means unknown (None), never False —
        # labels routinely omit real amenities, so we only assert presence.
        has_balcony=True if any(_contains_keyword(normalized_name, keyword) for keyword in _BALCONY_KEYWORDS) else None,
        size_sqm=size_sqm,
        bed_hint=_bed_hint_from_name(normalized_name),
    )


def attributes_to_dict(attrs: RoomAttributes) -> dict:
    """Serialize attributes to a lean JSON dict, omitting unknown (None) fields."""
    payload = {
        "capacity": attrs.capacity,
        "view": attrs.view,
        "has_balcony": attrs.has_balcony,
        "size_sqm": attrs.size_sqm,
        "bed_hint": attrs.bed_hint,
    }
    return {key: value for key, value in payload.items() if value is not None}


def attributes_from_dict(data: dict | None) -> RoomAttributes:
    """Rebuild attributes from stored JSON, tolerating junk and missing keys."""
    if not isinstance(data, dict):
        return _EMPTY_ATTRIBUTES
    view = data.get("view")
    bed_hint = data.get("bed_hint")
    has_balcony = data.get("has_balcony")
    return RoomAttributes(
        capacity=_parse_capacity(data.get("capacity")) if data.get("capacity") is not None else None,
        view=view if isinstance(view, str) and view else None,
        has_balcony=has_balcony if isinstance(has_balcony, bool) else None,
        size_sqm=_parse_size(data.get("size_sqm")) if data.get("size_sqm") is not None else None,
        bed_hint=bed_hint if isinstance(bed_hint, str) and bed_hint else None,
    )


def compute_match_score(
    owned_room_name: str,
    owned_attrs: RoomAttributes,
    candidate_room_name: str,
    candidate_attrs: RoomAttributes,
    same_category: bool,
) -> float:
    """Score how comparable a competitor room is to the user's own room (0-100).

    See the module docstring for the full formula: 60% fuzzy name similarity,
    +20 same category, attribute bonuses only when both sides know the
    attribute, and hard conflicts (capacity gap > 1, different known views)
    capping the result at 40.
    """
    base = _NAME_WEIGHT * fuzz.token_set_ratio(
        canonicalize_name(owned_room_name),
        canonicalize_name(candidate_room_name),
    )
    score = base
    if same_category:
        score += _SAME_CATEGORY_BONUS

    capacity_conflict = False
    if owned_attrs.capacity is not None and candidate_attrs.capacity is not None:
        capacity_gap = abs(owned_attrs.capacity - candidate_attrs.capacity)
        if capacity_gap == 0:
            score += _CAPACITY_BONUS
        capacity_conflict = capacity_gap > _CAPACITY_CONFLICT_GAP

    view_conflict = False
    if owned_attrs.view is not None and candidate_attrs.view is not None:
        if owned_attrs.view == candidate_attrs.view:
            score += _VIEW_BONUS
        else:
            view_conflict = True

    if owned_attrs.has_balcony is not None and candidate_attrs.has_balcony is not None:
        if owned_attrs.has_balcony == candidate_attrs.has_balcony:
            score += _BALCONY_BONUS

    if owned_attrs.size_sqm and candidate_attrs.size_sqm:
        if abs(owned_attrs.size_sqm - candidate_attrs.size_sqm) <= _SIZE_TOLERANCE * owned_attrs.size_sqm:
            score += _SIZE_BONUS

    if capacity_conflict or view_conflict:
        score = min(score, _HARD_CONFLICT_CAP)
    return max(0.0, min(100.0, score))
