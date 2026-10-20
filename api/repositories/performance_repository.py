"""PostgreSQL access for «Απόδοση»: the owner's bookings, rooms, closures and net-amount settings.

Migration 20261020_0035. Every read and write is fenced by the caller's
account and owned property. Bookings are written set-based (one statement
over unnest()-ed arrays, as normalized_market_writer does): a file of 20,000
rows is one round trip, not 20,000 against Neon.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine

# A missing settings row is these (migration 0035's server defaults).
DEFAULT_REVENUE_SETTINGS = {"vat_pct": 13.0, "resilience_fee_eur": 0.0, "fee_included_in_price": False}

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
    # UUIDs as text: psycopg2 adapts no uuid.UUID inside an array without register_uuid().
    if _BOOKING_TYPES[field] == "uuid":
        return [None if row.get(field) is None else str(row[field]) for row in rows]
    return [row.get(field) for row in rows]


class PerformanceRepository:
    def __init__(self, engine_factory: Callable[[str], Engine] | None = None):
        self._engine_factory = engine_factory

    def _engine(self, role: str = "api") -> Engine:
        return self._engine_factory(role) if self._engine_factory else get_engine(role=role)

    # ------------------------------------------------------------------
    # Room types and their rooms
    # ------------------------------------------------------------------

    def room_types(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[dict[str, Any]]:
        """The property's active room types, each with its rooms today (None until the owner enters them).

        «Today»: an inventory row covering today wins, then the one that
        starts latest.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT
                        rt.id,
                        rt.room_type AS label,
                        (
                            SELECT inv.rooms
                            FROM roomrate_performance_room_inventory inv
                            WHERE inv.account_id = rt.account_id
                              AND inv.room_type_id = rt.id
                            ORDER BY (
                                COALESCE(inv.valid_from, '-infinity'::date) <= CURRENT_DATE
                                AND COALESCE(inv.valid_to, 'infinity'::date) >= CURRENT_DATE
                            ) DESC, inv.valid_from DESC NULLS LAST
                            LIMIT 1
                        ) AS rooms
                    FROM roomrate_owned_property_room_types rt
                    WHERE rt.account_id = :account_id
                      AND rt.owned_property_id = :owned_property_id
                      AND rt.is_active = true
                    ORDER BY rt.sample_price_per_night_eur ASC NULLS LAST, rt.room_type ASC
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def save_inventory(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        rows: Iterable[dict[str, Any]],
    ) -> None:
        """Replace the property's rooms per room type; a room type not of this property is dropped.

        ``rows``: ``{"room_type_id", "rooms", "valid_from", "valid_to"}``;
        rooms None means «not known» and stores nothing.
        """
        kept = [row for row in rows if row.get("rooms") is not None]
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_room_inventory
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
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
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "room_type_ids": [str(row["room_type_id"]) for row in kept],
                    "rooms": [row["rooms"] for row in kept],
                    "valid_from": [row.get("valid_from") for row in kept],
                    "valid_to": [row.get("valid_to") for row in kept],
                },
            )

    def save_closures(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        rows: Iterable[dict[str, Any]],
    ) -> None:
        """Replace the property's closed periods.

        ``rows``: ``{"date_from", "date_to", "room_type_id", "rooms", "reason"}``;
        a room type not of this property drops the row.
        """
        rows = list(rows)
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_closures
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
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
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "room_type_ids": [
                        None if row.get("room_type_id") is None else str(row["room_type_id"]) for row in rows
                    ],
                    "date_from": [row["date_from"] for row in rows],
                    "date_to": [row["date_to"] for row in rows],
                    "rooms": [row.get("rooms") for row in rows],
                    "reasons": [row.get("reason") for row in rows],
                },
            )

    def save_label_mappings(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        mappings: Iterable[dict[str, Any]],
    ) -> None:
        """Remember file room labels: ``{"label", "room_type_id"}`` maps one, ``{"label", "ignored": True}`` skips it.

        A room type not of this property is not stored.
        """
        by_label = {
            mapping["label"]: mapping for mapping in mappings
            if mapping.get("ignored") or mapping.get("room_type_id") is not None
        }
        mappings = list(by_label.values())
        if not mappings:
            return
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_performance_label_mappings (
                        owned_property_id, label, account_id, room_type_id, ignored
                    )
                    SELECT :owned_property_id, m.label, :account_id,
                           CASE WHEN m.ignored THEN NULL ELSE rt.id END, m.ignored
                    FROM unnest(
                        CAST(:labels AS text[]),
                        CAST(:room_type_ids AS uuid[]),
                        CAST(:ignored AS boolean[])
                    ) AS m(label, room_type_id, ignored)
                    LEFT JOIN roomrate_owned_property_room_types rt
                      ON rt.id = m.room_type_id
                     AND rt.account_id = :account_id
                     AND rt.owned_property_id = :owned_property_id
                    WHERE m.ignored OR rt.id IS NOT NULL
                    ON CONFLICT (owned_property_id, label) DO UPDATE SET
                        room_type_id = EXCLUDED.room_type_id,
                        ignored = EXCLUDED.ignored,
                        updated_at = now()
                    WHERE roomrate_performance_label_mappings.account_id = :account_id
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "labels": [mapping["label"] for mapping in mappings],
                    "room_type_ids": [
                        None if mapping.get("ignored") else str(mapping["room_type_id"]) for mapping in mappings
                    ],
                    "ignored": [bool(mapping.get("ignored")) for mapping in mappings],
                },
            )

    # ------------------------------------------------------------------
    # Net-amount settings
    # ------------------------------------------------------------------

    def revenue_settings(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict[str, Any]:
        """VAT and resilience fee of the property, the defaults when never saved."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT vat_pct, resilience_fee_eur, fee_included_in_price
                    FROM roomrate_performance_revenue_settings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().first()
        if row is None:
            return dict(DEFAULT_REVENUE_SETTINGS)
        return {
            "vat_pct": float(row["vat_pct"]),
            "resilience_fee_eur": float(row["resilience_fee_eur"]),
            "fee_included_in_price": bool(row["fee_included_in_price"]),
        }

    def save_revenue_settings(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        *,
        vat_pct: float,
        resilience_fee_eur: float,
        fee_included_in_price: bool,
    ) -> None:
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_performance_revenue_settings (
                        owned_property_id, account_id, vat_pct, resilience_fee_eur, fee_included_in_price
                    )
                    VALUES (:owned_property_id, :account_id, :vat_pct, :resilience_fee_eur, :fee_included_in_price)
                    ON CONFLICT (owned_property_id) DO UPDATE SET
                        vat_pct = EXCLUDED.vat_pct,
                        resilience_fee_eur = EXCLUDED.resilience_fee_eur,
                        fee_included_in_price = EXCLUDED.fee_included_in_price,
                        updated_at = now()
                    WHERE roomrate_performance_revenue_settings.account_id = :account_id
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "vat_pct": vat_pct,
                    "resilience_fee_eur": resilience_fee_eur,
                    "fee_included_in_price": fee_included_in_price,
                },
            )

    # ------------------------------------------------------------------
    # Bookings
    # ------------------------------------------------------------------

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
        updates = ",\n".join(
            f"{field} = EXCLUDED.{field}" for field in BOOKING_FIELDS if field != "booking_ref"
        )
        params: dict[str, Any] = {field: _column(rows, field) for field in BOOKING_FIELDS}
        params.update(account_id=account_id, owned_property_id=owned_property_id, import_id=import_id)

        with self._engine("writer").begin() as connection:
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
                {"account_id": account_id, "owned_property_id": owned_property_id},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_imports
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            )
        return int(result.rowcount or 0)
