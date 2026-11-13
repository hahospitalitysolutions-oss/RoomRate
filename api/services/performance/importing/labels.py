"""Which owned room type each room label of an upload means.

A label goes where the owner sent it in an earlier upload (mapped, or
ignored); otherwise to the room type of the same name, or one clearly
closest by its words (order, accents and case aside).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from api.services.performance.importing.values import plain_text

# token_sort_ratio: «Standard Double Room» = «Double Room Standard», but «Double Room» ≠ «Double Room with Sea View».
NEAR_MATCH = 90


def suggest_room_type(label: str, room_types: list[dict[str, Any]]) -> uuid.UUID | None:
    """The owned room type a file label names: the same name, else one clearly closest (≥ 90, no tie)."""
    plain = plain_text(label)
    for room_type in room_types:
        if plain_text(room_type["label"]) == plain:
            return room_type["id"]
    scored = sorted(
        ((fuzz.token_sort_ratio(plain, plain_text(room_type["label"])), room_type["id"]) for room_type in room_types),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= NEAR_MATCH and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


def label_counts(bookings: Iterable[dict[str, Any]], other_labels: Iterable[str] = ()) -> dict[str, int]:
    """Each room label with its bookings, most first; ``other_labels`` (a template's «Δωμάτια») with 0."""
    counts: dict[str, int] = {}
    for booking in bookings:
        counts[booking["room_label"]] = counts.get(booking["room_label"], 0) + 1
    for label in other_labels:
        counts.setdefault(label, 0)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


@dataclass
class LabelMap:
    counts: dict[str, int]
    # label → owned room type, None when not mapped (or ignored).
    mapped: dict[str, uuid.UUID | None] = field(default_factory=dict)
    ignored: set[str] = field(default_factory=set)

    @property
    def ready(self) -> bool:
        """Every label with bookings mapped or ignored; a label with no booking never holds the upload back."""
        return all(self.mapped.get(label) or label in self.ignored for label, count in self.counts.items() if count)

    def rows(self) -> list[dict[str, Any]]:
        """The preview's ``mapping``."""
        return [
            {
                "label": label,
                "bookings": count,
                "room_type_id": self.mapped.get(label),
                "ignored": label in self.ignored,
            }
            for label, count in self.counts.items()
        ]


def map_labels(
    counts: dict[str, int],
    room_types: list[dict[str, Any]],
    remembered: dict[str, dict[str, Any]],
) -> LabelMap:
    """Where each label goes now: the owner's earlier choice, else the room type of its name."""
    owned = {room["id"] for room in room_types}
    labels = LabelMap(counts)
    for label in counts:
        choice = remembered.get(label)
        if choice and choice["ignored"]:
            labels.ignored.add(label)
            labels.mapped[label] = None
        elif choice and choice["room_type_id"] in owned:
            labels.mapped[label] = choice["room_type_id"]
        else:
            labels.mapped[label] = suggest_room_type(label, room_types)
    return labels
