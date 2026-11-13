"""Rooms open for sale, day by day: the rooms per room type minus closed periods.

A room type's count that day is its dated inventory row covering the day,
else its undated row, else 0. Closures take out the whole property, a room
type, or some rooms of either. Availability is unknown (None) when a room
type with sold nights (or the filtered one) has no count at all.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class Availability:
    # Rooms open for sale per day; None when a needed room count is missing.
    rooms: dict[date, int | None]
    # Days closed altogether (the property, or the filtered room type).
    closed: set[date]

    def total(self, period: Iterable[date]) -> int | None:
        counts = [self.rooms[day] for day in period]
        return None if any(count is None for count in counts) else sum(counts)


def availability(
    period: Sequence[date],
    room_type_ids: Sequence[uuid.UUID],
    inventory: Sequence[dict[str, Any]],
    closures: Sequence[dict[str, Any]],
    *,
    room_type_id: uuid.UUID | None = None,
    sold_room_types: Iterable[uuid.UUID] = (),
) -> Availability:
    """Rooms per day for the whole property, or for ``room_type_id``."""
    counts: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        if row.get("rooms") is not None:
            counts[row["room_type_id"]].append(row)
    kinds = [room_type_id] if room_type_id else list(room_type_ids)
    sold = set(sold_room_types)
    unknown = (
        any(not counts.get(kind) for kind in kinds if room_type_id or kind in sold)
        or not any(counts.get(kind) for kind in kinds)
    )

    rooms: dict[date, int | None] = {}
    closed: set[date] = set()
    for day in period:
        per_kind = {kind: _count(counts.get(kind, []), day) for kind in kinds}
        today = [closure for closure in closures if closure["date_from"] <= day <= closure["date_to"]]
        open_rooms = _after_closures(per_kind, today, filtered=room_type_id is not None)
        if today and open_rooms == 0 and sum(per_kind.values()) > 0:
            closed.add(day)
        rooms[day] = None if unknown else open_rooms
    return Availability(rooms, closed)


def _count(rows: list[dict[str, Any]], day: date) -> int:
    for row in rows:
        start, end = row.get("valid_from"), row.get("valid_to")
        if (start or end) and (start or date.min) <= day <= (end or date.max):
            return row["rooms"]
    undated = [row for row in rows if not row.get("valid_from") and not row.get("valid_to")]
    return undated[0]["rooms"] if undated else 0


def _after_closures(per_kind: dict[uuid.UUID, int], closures: list[dict[str, Any]], *, filtered: bool) -> int:
    open_rooms = dict(per_kind)
    rooms_out = 0  # rooms closed somewhere in the property, of no named type
    for closure in closures:
        kind, rooms = closure.get("room_type_id"), closure.get("rooms")
        if kind is None and rooms is None:
            return 0
        if kind is None:
            if not filtered:
                rooms_out += rooms
        elif kind in open_rooms:
            open_rooms[kind] = 0 if rooms is None else max(0, open_rooms[kind] - rooms)
    return max(0, sum(open_rooms.values()) - rooms_out)
