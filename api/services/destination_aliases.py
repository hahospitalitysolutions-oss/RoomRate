from __future__ import annotations

import re
import unicodedata


DESTINATION_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Faliraki", "faliraki", "Φαληράκι", "Φαληρακι"),
    ("Rhodes", "Rodos", "Ρόδος", "Ροδος"),
    ("Santorini", "Σαντορίνη", "Σαντορινη"),
    ("Athens", "Athina", "Αθήνα", "Αθηνα"),
)


def normalize_destination_key(value: object) -> str:
    """Return an accent-insensitive key for destination alias lookup."""
    text = str(value or "").strip().casefold()
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[\W_]+", "", without_marks, flags=re.UNICODE)


def destination_variants(destination: str | None) -> tuple[str, ...]:
    """Return known English/Greek variants for a user-provided destination."""
    value = str(destination or "").strip()
    if not value:
        return ()

    aliases = _ALIASES_BY_KEY.get(normalize_destination_key(value))
    if not aliases:
        return (value,)

    variants: dict[str, None] = {}
    for alias in (value, *aliases):
        stripped = alias.strip()
        variants.setdefault(stripped, None)
        variants.setdefault(stripped.casefold(), None)
    return tuple(variants)


def canonical_destination(destination: str | None) -> str:
    """Return the stable destination key used for grouping jobs and runs."""
    value = str(destination or "").strip()
    if not value:
        return ""

    key = normalize_destination_key(value)
    aliases = _ALIASES_BY_KEY.get(key)
    if not aliases:
        return key
    return normalize_destination_key(aliases[0])


def _build_alias_lookup() -> dict[str, tuple[str, ...]]:
    lookup: dict[str, tuple[str, ...]] = {}
    for alias_group in DESTINATION_ALIAS_GROUPS:
        unique_group = tuple(dict.fromkeys(alias.strip() for alias in alias_group if alias.strip()))
        for alias in unique_group:
            lookup[normalize_destination_key(alias)] = unique_group
    return lookup


_ALIASES_BY_KEY = _build_alias_lookup()
