"""Writes on an open transaction, shared by the single saves and the upload commit.

Bookings are written set-based (one statement over unnest()-ed arrays, as
normalized_market_writer does): a file of 20,000 rows is one round trip,
not 20,000 against Neon. A room type that is not the property's is never
stored against it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.repositories.performance.base import fence, uuid_texts

BOOKING_FIELDS = (
    "booking_ref",
    "room_label",
    "room_type_id",
    "channel",
    "status",
    "booked_on",
    "cancelled_on",
    "arrival",
    "departure",
    "rooms",
    "adults",
    "children",
    "gross_eur",
    "commission_eur",
    "country_code",
)
_BOOKING_TYPES = {
    "booking_ref": "text",
    "room_label": "text",
    "room_type_id": "uuid",
    "channel": "text",
    "status": "text",
    "booked_on": "date",
    "cancelled_on": "date",
    "arrival": "date",
    "departure": "date",
    "rooms": "integer",
    "adults": "integer",
    "children": "integer",
    "gross_eur": "numeric",
    "commission_eur": "numeric",
    "country_code": "text",
}


def _column(rows: Sequence[dict[str, Any]], field: str) -> list[Any]:
    if _BOOKING_TYPES[field] == "uuid":
        return uuid_texts(row.get(field) for row in rows)
    return [row.get(field) for row in rows]


def write_inventory(
    connection: Connection,
    account_id: uuid.UUID,
    owned_property_id: uuid.UUID,
    rows: Iterable[dict[str, Any]],
    *,
    only_room_types: Sequence[Any] | None = None,
) -> None:
    kept = [row for row in rows if row.get("rooms") is not None]
    scope = "" if only_room_types is None else "AND room_type_id = ANY(CAST(:only AS uuid[]))"
    connection.execute(
        text(
            f"""
            DELETE FROM roomrate_performance_room_inventory
            WHERE account_id = :account_id AND owned_property_id = :owned_property_id {scope}
            """
        ),
        {**fence(account_id, owned_property_id), "only": uuid_texts(only_room_types or [])},
    )
    if not kept:
        return
    connection.execute(
        text(
            """
            INSERT INTO roomrate_performance_room_inventory (
                id, account_id, owned_property_id, room_type_id, rooms, valid_from, valid_to
            )
            SELECT gen_random_uuid(), :account_id, :owned_property_id, rt.id, r.rooms, r.valid_from, r.valid_to
            FROM unnest(
                CAST(:room_type_ids AS uuid[]),
                CAST(:rooms AS integer[]),
                CAST(:valid_from AS date[]),
                CAST(:valid_to AS date[])
            ) AS r(room_type_id, rooms, valid_from, valid_to)
            JOIN roomrate_owned_property_room_types rt
              ON rt.id = r.room_type_id
             AND rt.account_id = :account_id
             AND rt.owned_property_id = :owned_property_id
            """
        ),
        {
            **fence(account_id, owned_property_id),
            "room_type_ids": uuid_texts(row["room_type_id"] for row in kept),
            "rooms": [row["rooms"] for row in kept],
            "valid_from": [row.get("valid_from") for row in kept],
            "valid_to": [row.get("valid_to") for row in kept],
        },
    )


def write_closures(
    connection: Connection,
    account_id: uuid.UUID,
    owned_property_id: uuid.UUID,
    rows: Iterable[dict[str, Any]],
) -> None:
    rows = list(rows)
    connection.execute(
        text(
            """
            DELETE FROM roomrate_performance_closures
            WHERE account_id = :account_id AND owned_property_id = :owned_property_id
            """
        ),
        fence(account_id, owned_property_id),
    )
    if not rows:
        return
    connection.execute(
        text(
            """
            INSERT INTO roomrate_performance_closures (
                id, account_id, owned_property_id, room_type_id, date_from, date_to, rooms, reason
            )
            SELECT gen_random_uuid(), :account_id, :owned_property_id, r.room_type_id,
                   r.date_from, r.date_to, r.rooms, r.reason
            FROM unnest(
                CAST(:room_type_ids AS uuid[]),
                CAST(:date_from AS date[]),
                CAST(:date_to AS date[]),
                CAST(:rooms AS integer[]),
                CAST(:reasons AS text[])
            ) AS r(room_type_id, date_from, date_to, rooms, reason)
            WHERE r.room_type_id IS NULL OR EXISTS (
                SELECT 1 FROM roomrate_owned_property_room_types rt
                WHERE rt.id = r.room_type_id
                  AND rt.account_id = :account_id
                  AND rt.owned_property_id = :owned_property_id
            )
            """
        ),
        {
            **fence(account_id, owned_property_id),
            "room_type_ids": uuid_texts(row.get("room_type_id") for row in rows),
            "date_from": [row["date_from"] for row in rows],
            "date_to": [row["date_to"] for row in rows],
            "rooms": [row.get("rooms") for row in rows],
            "reasons": [row.get("reason") for row in rows],
        },
    )


def write_bookings(
    connection: Connection,
    account_id: uuid.UUID,
    owned_property_id: uuid.UUID,
    bookings: Sequence[dict[str, Any]],
    import_id: uuid.UUID | None,
) -> dict[str, int]:
    latest: dict[str, dict[str, Any]] = {}
    for booking in bookings:
        latest[str(booking["booking_ref"])] = booking
    rows = list(latest.values())
    if not rows:
        return {"inserted": 0, "updated": 0}

    columns = ",\n".join(f"CAST(:{field} AS {_BOOKING_TYPES[field]}[])" for field in BOOKING_FIELDS)
    names = ", ".join(BOOKING_FIELDS)
    # A room type that is not this property's is stored as none.
    selected = ", ".join("rt.id" if field == "room_type_id" else f"r.{field}" for field in BOOKING_FIELDS)
    updates = ",\n".join(f"{field} = EXCLUDED.{field}" for field in BOOKING_FIELDS if field != "booking_ref")
    params: dict[str, Any] = {field: _column(rows, field) for field in BOOKING_FIELDS}
    params.update(account_id=account_id, owned_property_id=owned_property_id, import_id=import_id)

    written = connection.execute(
        text(
            f"""
            INSERT INTO roomrate_performance_bookings (
                id, account_id, owned_property_id, import_id, {names}
            )
            SELECT gen_random_uuid(), :account_id, :owned_property_id, :import_id, {selected}
            FROM unnest(
                {columns}
            ) AS r({names})
            LEFT JOIN roomrate_owned_property_room_types rt
              ON rt.id = r.room_type_id
             AND rt.account_id = :account_id
             AND rt.owned_property_id = :owned_property_id
            ON CONFLICT (owned_property_id, booking_ref) DO UPDATE SET
                {updates},
                import_id = EXCLUDED.import_id,
                updated_at = now()
            WHERE roomrate_performance_bookings.account_id = :account_id
            RETURNING id, (xmax = 0) AS inserted
            """
        ),
        params,
    ).mappings().all()
    ids = [str(row["id"]) for row in written]
    connection.execute(
        text("DELETE FROM roomrate_performance_booking_nights WHERE booking_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": ids},
    )
    connection.execute(
        text(
            """
            INSERT INTO roomrate_performance_booking_nights (
                booking_id, stay_date, owned_property_id, room_type_id, rooms,
                gross_eur, commission_eur, sold
            )
            SELECT
                b.id,
                night.stay_date,
                b.owned_property_id,
                b.room_type_id,
                b.rooms,
                CASE WHEN night.stay_date = b.departure - 1
                    THEN b.gross_eur - trunc(b.gross_eur / (b.departure - b.arrival), 2)
                         * (b.departure - b.arrival - 1)
                    ELSE trunc(b.gross_eur / (b.departure - b.arrival), 2)
                END,
                CASE WHEN night.stay_date = b.departure - 1
                    THEN b.commission_eur - trunc(b.commission_eur / (b.departure - b.arrival), 2)
                         * (b.departure - b.arrival - 1)
                    ELSE trunc(b.commission_eur / (b.departure - b.arrival), 2)
                END,
                b.status = 'confirmed'
            FROM roomrate_performance_bookings b
            CROSS JOIN LATERAL (
                SELECT CAST(day AS date) AS stay_date
                FROM generate_series(
                    CAST(b.arrival AS timestamp), CAST(b.departure - 1 AS timestamp), interval '1 day'
                ) AS day
            ) AS night
            WHERE b.id = ANY(CAST(:ids AS uuid[]))
              AND b.account_id = :account_id
            """
        ),
        {"ids": ids, "account_id": account_id},
    )
    inserted = sum(1 for row in written if row["inserted"])
    return {"inserted": inserted, "updated": len(written) - inserted}
