"""The property's set-up for «Απόδοση»: rooms per room type, closed periods, net-amount settings."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import text

from api.repositories.performance.base import RepositoryBase, fence
from api.repositories.performance.writers import write_closures, write_inventory

# A missing settings row is these (migration 0035's server defaults).
DEFAULT_REVENUE_SETTINGS = {"vat_pct": 13.0, "resilience_fee_eur": 0.0, "fee_included_in_price": False}


def room_types_from(inventory: Sequence[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """The room types of ``inventory`` rows, in their order, each with its rooms today (None until entered).

    «Today»: a dated count covering today wins over the undated one; with
    none covering today, the count that starts latest.
    """
    today = today or date.today()
    types: dict[uuid.UUID, dict[str, Any]] = {}
    counts: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in inventory:
        kind = row["room_type_id"]
        types.setdefault(kind, {"id": kind, "label": row["label"], "category": row.get("category"), "rooms": None})
        if row.get("rooms") is not None:
            counts.setdefault(kind, []).append(row)
    for kind, rows in counts.items():
        types[kind]["rooms"] = max(rows, key=lambda row: _today_rank(row, today))["rooms"]
    return list(types.values())


def _today_rank(row: dict[str, Any], today: date) -> tuple:
    start, end = row.get("valid_from"), row.get("valid_to")
    covers = (start or date.min) <= today <= (end or date.max)
    return covers, start is not None, start or date.min


class SetupQueries(RepositoryBase):
    def inventory(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[dict[str, Any]]:
        """Every active room type with each of its room counts (one row with rooms None when not entered).

        Cheapest room type first, its counts by start date.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT rt.id AS room_type_id, rt.room_type AS label, rt.room_type_category AS category,
                           inv.rooms, inv.valid_from, inv.valid_to
                    FROM roomrate_owned_property_room_types rt
                    LEFT JOIN roomrate_performance_room_inventory inv
                      ON inv.room_type_id = rt.id
                     AND inv.account_id = rt.account_id
                    WHERE rt.account_id = :account_id
                      AND rt.owned_property_id = :owned_property_id
                      AND rt.is_active = true
                    ORDER BY rt.sample_price_per_night_eur ASC NULLS LAST, rt.room_type ASC,
                             inv.valid_from ASC NULLS FIRST
                    """
                ),
                fence(account_id, owned_property_id),
            ).mappings().all()
        return [dict(row) for row in rows]

    def room_types(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[dict[str, Any]]:
        """The property's active room types: ``id``, ``label``, ``category`` and ``rooms`` today."""
        return room_types_from(self.inventory(account_id, owned_property_id))

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
        with self._engine("writer").begin() as connection:
            write_inventory(connection, account_id, owned_property_id, rows)

    def closures(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT date_from, date_to, room_type_id, rooms, reason
                    FROM roomrate_performance_closures
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    ORDER BY date_from, date_to
                    """
                ),
                fence(account_id, owned_property_id),
            ).mappings().all()
        return [dict(row) for row in rows]

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
        with self._engine("writer").begin() as connection:
            write_closures(connection, account_id, owned_property_id, rows)


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
                fence(account_id, owned_property_id),
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
                    **fence(account_id, owned_property_id),
                    "vat_pct": vat_pct,
                    "resilience_fee_eur": resilience_fee_eur,
                    "fee_included_in_price": fee_included_in_price,
                },
            )

