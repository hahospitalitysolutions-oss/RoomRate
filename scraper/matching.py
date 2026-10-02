"""Room-name and rate-option matching helpers."""

from __future__ import annotations

import pandas as pd

from api.services.room_rates_normalizer import compact_matching_key, matching_tokens, normalize_text

def _normalize_matching_text(value: object) -> str:
    return normalize_text(value).casefold()


_ROOM_NAME_STOP_WORDS = {
    "a", "an", "and", "for", "in", "of", "or", "the", "to", "with",
    "room", "rooms", "bed", "beds",
    "η", "ή", "και", "με", "σε", "στο", "στη", "στην", "το", "τον", "του", "τη", "την", "της",
    "δωματιο", "δωμάτιο", "δωματια", "δωμάτια",
}


def _room_name_tokens(value: object) -> set[str]:
    return {
        token
        for token in matching_tokens(value)
        if len(token) > 1 and token not in _ROOM_NAME_STOP_WORDS
    }


def _filter_by_room_name(df: pd.DataFrame, room_name_query: str | None) -> pd.DataFrame:
    """Keep each hotel's strongest available room-name match tier.

    ``hotel_url`` is the strongest property identity available in the
    flattened scraper frame. Rows without one fall back to the normalized
    hotel name; generated anonymous-name sentinels remain positional so two
    unrelated unknown listings cannot suppress each other.

    The output preserves input order, indices and duplicate rate offers.
    """
    if not room_name_query or df.empty:
        return df
    query_key = compact_matching_key(room_name_query)
    if not query_key:
        return df

    required_columns = ("hotel_name", "room_type")
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise ValueError(
            "Room-name filtering requires DataFrame columns: "
            + ", ".join(missing_columns)
        )

    room_keys = [compact_matching_key(value) for value in df["room_type"].fillna("")]
    query_tokens = _room_name_tokens(room_name_query)
    match_tiers: list[int] = []
    for room_key, room_name in zip(room_keys, df["room_type"].fillna(""), strict=True):
        if room_key == query_key:
            match_tiers.append(0)
        elif room_key and (query_key in room_key or room_key in query_key):
            match_tiers.append(1)
        elif len(query_tokens) >= 2:
            score = len(query_tokens & _room_name_tokens(room_name)) / len(query_tokens)
            match_tiers.append(2 if score >= 0.45 else 3)
        else:
            match_tiers.append(3)

    # Το URL είναι σταθερότερο από το εμφανιζόμενο όνομα και ξεχωρίζει ακόμη
    # και δύο καταλύματα με ίδιο fallback όνομα. Χωρίς URL, οι ανώνυμες
    # γραμμές παίρνουν key ανά θέση: δεν γνωρίζουμε ότι είναι το ίδιο property.
    property_keys: list[tuple[str, object]] = []
    hotel_urls = df["hotel_url"] if "hotel_url" in df.columns else [None] * len(df)
    anonymous_names = {"άγνωστο κατάλυμα", "unknown property"}
    for position, (hotel_name, hotel_url) in enumerate(
        zip(df["hotel_name"], hotel_urls, strict=True)
    ):
        normalized_url = "" if pd.isna(hotel_url) else _normalize_matching_text(hotel_url)
        normalized_name = "" if pd.isna(hotel_name) else _normalize_matching_text(hotel_name)
        if normalized_url:
            property_keys.append(("url", normalized_url))
        elif not normalized_name or normalized_name in anonymous_names:
            property_keys.append(("anonymous", position))
        else:
            property_keys.append(("hotel", normalized_name))

    matching_rows = pd.DataFrame(
        {
            "_property_key": property_keys,
            "_match_tier": match_tiers,
        }
    )
    best_tier_per_property = matching_rows.groupby(
        "_property_key",
        sort=False,
    )["_match_tier"].transform("min")
    keep_positions = matching_rows["_match_tier"].eq(best_tier_per_property).to_numpy()
    return df.iloc[keep_positions].copy()



def _matches_required_meal(meals: object, required_meal: str | None) -> bool:
    if not required_meal:
        return True
    current = _normalize_matching_text(meals)
    required = _normalize_matching_text(required_meal)
    if not required:
        return True
    if required in current or (current and current in required):
        return True
    meal_groups = (
        ("breakfast", "πρωιν"),
        ("dinner", "δείπνο", "βραδιν"),
        ("lunch", "μεσημεριαν"),
        ("half board", "ημιδιατρο"),
        ("full board", "πλήρης διατρο", "πληρης διατρο"),
        ("all inclusive",),
    )
    for group in meal_groups:
        if any(token in required for token in group):
            return any(token in current for token in group)
    return False


def _normalize_cancellation_flag(value: object) -> str:
    normalized = _normalize_matching_text(value)
    if normalized in {"yes", "true", "1", "free"} or "free cancellation" in normalized:
        return "yes"
    if normalized in {"no", "false", "0"} or "non-refundable" in normalized or "non refundable" in normalized:
        return "no"
    return normalized


def _matches_required_free_cancellation(value: object, required_free_cancellation: str | None) -> bool:
    if not required_free_cancellation:
        return True
    required = _normalize_cancellation_flag(required_free_cancellation)
    if not required:
        return True
    return _normalize_cancellation_flag(value) == required


# ===========================================================
# APIFY: actor call με retries
# ===========================================================
