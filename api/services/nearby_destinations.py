"""Static default neighbouring areas per canonical destination (Round 6 §3.1).

The Booking actor has no radius input, so the map form pre-fills the areas
around the owner's market and the scraper scouts each one. Only Faliraki has
defaults for the demo; every other destination starts with an empty list and
the owner types chips by hand.
"""

from __future__ import annotations

from api.services.destination_aliases import canonical_destination

# Tuples keep the table immutable; default_nearby_destinations hands out a
# fresh list so a caller appending chips can never change the defaults.
DEFAULT_NEARBY_DESTINATIONS: dict[str, tuple[str, ...]] = {
    "faliraki": ("Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"),
}


def default_nearby_destinations(destination: str | None) -> list[str]:
    """Return the default neighbouring areas for a destination (any alias), else []."""
    canonical = canonical_destination(destination)
    return list(DEFAULT_NEARBY_DESTINATIONS.get(canonical, ()))
