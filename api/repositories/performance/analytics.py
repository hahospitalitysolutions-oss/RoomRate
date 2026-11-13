"""What the figures read: sold and held nights, arriving bookings, and competitors' prices.

Date windows (the period, and last year's dates with it) are one query.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import text

from api.repositories.performance.base import RepositoryBase, fence


class AnalyticsQueries(RepositoryBase):
    def sold_nights(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        windows: Sequence[tuple[date, date]],
        room_type_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Sold nights (confirmed bookings) in the date windows, with their booking's channel and country."""
        return self._in_windows(
            """
            SELECT n.stay_date, n.room_type_id, n.rooms, n.gross_eur, n.commission_eur, n.booking_id,
                   b.channel, b.country_code
            FROM roomrate_performance_booking_nights n
            JOIN roomrate_performance_bookings b ON b.id = n.booking_id AND b.account_id = :account_id
            WHERE n.owned_property_id = :owned_property_id
              AND n.sold
              AND ({windows})
              AND (CAST(:room_type_id AS uuid) IS NULL OR n.room_type_id = CAST(:room_type_id AS uuid))
            ORDER BY n.stay_date
            """,
            "n.stay_date",
            account_id,
            owned_property_id,
            windows,
            room_type_id,
        )

    def held_nights(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        windows: Sequence[tuple[date, date]],
    ) -> list[dict[str, Any]]:
        """Every night in the date windows, cancelled bookings' too, with when its booking was made and cancelled.

        The pace reads from them which nights were on the books on a given day.
        """
        return self._in_windows(
            """
            SELECT n.stay_date, n.rooms, n.room_type_id, n.booking_id, b.status, b.booked_on, b.cancelled_on
            FROM roomrate_performance_booking_nights n
            JOIN roomrate_performance_bookings b ON b.id = n.booking_id AND b.account_id = :account_id
            WHERE n.owned_property_id = :owned_property_id
              AND ({windows})
            ORDER BY n.stay_date
            """,
            "n.stay_date",
            account_id,
            owned_property_id,
            windows,
            None,
        )

    def bookings_arriving(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        windows: Sequence[tuple[date, date]],
        room_type_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Every booking (cancelled too) arriving in the date windows."""
        return self._in_windows(
            """
            SELECT id, status, booked_on, cancelled_on, arrival, departure, rooms, adults, children, channel,
                   room_type_id
            FROM roomrate_performance_bookings
            WHERE account_id = :account_id AND owned_property_id = :owned_property_id
              AND ({windows})
              AND (CAST(:room_type_id AS uuid) IS NULL OR room_type_id = CAST(:room_type_id AS uuid))
            ORDER BY arrival
            """,
            "arrival",
            account_id,
            owned_property_id,
            windows,
            room_type_id,
        )

    def _in_windows(self, sql, column, account_id, owned_property_id, windows, room_type_id) -> list[dict[str, Any]]:
        if not windows:
            return []
        clauses = " OR ".join(f"{column} BETWEEN :from_{i} AND :to_{i}" for i in range(len(windows)))
        params: dict[str, Any] = {
            **fence(account_id, owned_property_id),
            "room_type_id": None if room_type_id is None else str(room_type_id),
        }
        for index, (first, last) in enumerate(windows):
            params[f"from_{index}"], params[f"to_{index}"] = first, last
        with self._engine().connect() as connection:
            rows = connection.execute(text(sql.format(windows=clauses)), params).mappings().all()
        return [dict(row) for row in rows]

    def market_prices(
        self,
        account_id: uuid.UUID,
        canonical_destination: str,
        date_from: date,
        date_to: date,
        categories: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """Competitors' cheapest comparable room per hotel and collection, for check-ins in the period.

        From the account's own collections in the destination; the owner's
        own hotel is left out. ``categories``: the comparable room
        categories (none = every room).
        """
        category_filter = (
            "AND rp.room_type_category = ANY(CAST(:categories AS text[]))" if categories else ""
        )
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT sr.check_in,
                           min(COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur)) AS price_eur
                    FROM roomrate_scrape_runs sr
                    JOIN roomrate_rate_observations ro ON ro.scrape_run_id = sr.id
                    JOIN roomrate_properties p ON p.id = ro.property_id
                    JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
                    WHERE sr.account_id = :account_id
                      AND sr.canonical_destination = :canonical_destination
                      AND sr.status = 'completed'
                      AND sr.check_in BETWEEN :date_from AND :date_to
                      {category_filter}
                      AND NOT EXISTS (
                          SELECT 1
                          FROM roomrate_owned_properties op
                          WHERE op.account_id = :account_id
                            AND op.is_active = true
                            AND lower(trim(op.display_name)) IN (
                                lower(trim(p.display_name)),
                                lower(trim(p.canonical_name))
                            )
                      )
                    GROUP BY sr.id, sr.check_in, ro.property_id
                    """
                ),
                {
                    "account_id": account_id,
                    "canonical_destination": canonical_destination,
                    "date_from": date_from,
                    "date_to": date_to,
                    "categories": list(categories),
                },
            ).mappings().all()
        return [{"check_in": row["check_in"], "price_eur": float(row["price_eur"])} for row in rows]

