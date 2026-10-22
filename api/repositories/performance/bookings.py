"""The owner's stored bookings: write, look up, delete, and what the page's status counts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from api.repositories.performance.base import RepositoryBase, fence
from api.repositories.performance.writers import write_bookings


class BookingQueries(RepositoryBase):
    def save_bookings(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        bookings: Sequence[dict[str, Any]],
        *,
        import_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """Insert new bookings and replace the ones whose ``booking_ref`` is stored already; rebuild their nights.

        ``bookings``: dicts with BOOKING_FIELDS (``room_type_id`` an owned
        room type of this property or None). A reference repeated in the
        batch keeps its last row. Each night gets an equal share of the
        revenue and commission, truncated to the cent, with the remainder on
        the last night so the nights add up to the booking exactly.

        Returns ``{"inserted", "updated"}``.
        """
        if not bookings:
            return {"inserted": 0, "updated": 0}
        with self._engine("writer").begin() as connection:
            return write_bookings(connection, account_id, owned_property_id, bookings, import_id)


    def existing_refs(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, refs: Sequence[str]) -> set[str]:
        """The booking numbers among ``refs`` already stored for the property."""
        if not refs:
            return set()
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT booking_ref
                    FROM roomrate_performance_bookings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                      AND booking_ref = ANY(CAST(:refs AS text[]))
                    """
                ),
                {**fence(account_id, owned_property_id), "refs": list(refs)},
            ).mappings().all()
        return {row["booking_ref"] for row in rows}

    def delete_bookings(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> int:
        """Every booking of the property and its upload history; rooms, closures, mappings and settings stay."""
        with self._engine("writer").begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_bookings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                fence(account_id, owned_property_id),
            )
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_imports
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                fence(account_id, owned_property_id),
            )
        return int(result.rowcount or 0)

    def status_counts(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict[str, Any]:
        """Bookings stored, first and last night held, the room types booked and the seasons (years) sold."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        count(*) AS bookings,
                        min(arrival) FILTER (WHERE status <> 'cancelled') AS first_stay_date,
                        max(departure) FILTER (WHERE status <> 'cancelled') - 1 AS last_stay_date,
                        array_agg(DISTINCT CAST(room_type_id AS text))
                            FILTER (WHERE room_type_id IS NOT NULL) AS room_type_ids,
                        (
                            SELECT array_agg(DISTINCT CAST(extract(year FROM n.stay_date) AS integer))
                            FROM roomrate_performance_booking_nights n
                            JOIN roomrate_performance_bookings nb ON nb.id = n.booking_id
                            WHERE nb.account_id = :account_id
                              AND n.owned_property_id = :owned_property_id
                              AND n.sold
                        ) AS seasons
                    FROM roomrate_performance_bookings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                fence(account_id, owned_property_id),
            ).mappings().one()
        return {
            "bookings": int(row["bookings"] or 0),
            "first_stay_date": row["first_stay_date"],
            "last_stay_date": row["last_stay_date"],
            "room_type_ids": {uuid.UUID(value) for value in row["room_type_ids"] or []},
            "seasons": sorted(row["seasons"] or [], reverse=True),
        }

