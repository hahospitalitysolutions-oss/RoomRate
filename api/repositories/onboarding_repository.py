from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine
from api.schemas.onboarding import OwnedPropertyOnboardingCreate


class OnboardingRepository:
    """PostgreSQL repository for owned property onboarding."""

    def __init__(self, engine_factory: Callable[[], Engine] | None = None):
        # Optional engine seam (same design as the injected connection factory
        # in RoomRatesRepository/PriceHistoryRepository). None falls back to
        # the module-level get_engine inside _engine().
        self._engine_factory = engine_factory

    def _engine(self) -> Engine:
        """Injected factory when present, else the module-level get_engine.

        get_engine is deliberately resolved as a module global at call time
        (not bound at import/def time) so tests that monkeypatch it keep
        working.
        """
        return self._engine_factory() if self._engine_factory else get_engine()

    def create_owned_property(
        self,
        account_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> dict[str, Any]:
        """Persist the selected property for one account."""
        owned_property_id = uuid.uuid4()
        with self._engine().begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO roomrate_owned_properties (
                        id,
                        account_id,
                        display_name,
                        booking_url,
                        address,
                        city,
                        raw_destination,
                        canonical_destination,
                        country,
                        property_type,
                        latitude,
                        longitude,
                        location_source,
                        location_confidence,
                        is_active
                    )
                    VALUES (
                        :id,
                        :account_id,
                        :display_name,
                        :booking_url,
                        :address,
                        :city,
                        :raw_destination,
                        :canonical_destination,
                        :country,
                        :property_type,
                        :latitude,
                        :longitude,
                        'booking_search',
                        0.90,
                        true
                    )
                    RETURNING id
                    """
                ),
                {
                    "id": owned_property_id,
                    "account_id": account_id,
                    "display_name": request.display_name,
                    "booking_url": str(request.booking_url),
                    "address": request.address,
                    "city": request.city,
                    "raw_destination": request.raw_destination,
                    "canonical_destination": request.canonical_destination,
                    "country": request.country,
                    "property_type": request.property_type,
                    "latitude": request.latitude,
                    "longitude": request.longitude,
                },
            ).mappings().one()
        return dict(row)

    def replace_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> dict[str, Any] | None:
        """Point an existing owned property at a different Booking property.

        One transaction, because the intermediate states are all wrong: room
        types belonging to the previous hotel, or a baseline category that no
        longer exists.

        Discovered room types are always dropped -- they describe the OLD
        hotel and are meaningless for the new one. Tracked competitors are
        dropped only when ``canonical_destination`` changes: a different
        hotel in the same market still has a valid competitor set, so it
        survives the swap. ``market_changed`` in the return value reports
        which of the two happened.

        Both deletes are hard deletes, not the ``is_active = false``
        retirement ``TrackingRepository.replace_tracked_competitors`` uses
        for competitors. That matters for room types specifically:
        ``_refresh_owned_property_room_types`` re-inserts discovered rows
        with ``ON CONFLICT (account_id, owned_property_id, room_type,
        room_type_category) DO UPDATE SET ... is_active = true``. A
        soft-deleted old-hotel room type sharing that (room_type,
        room_type_category) label with the new hotel's would be silently
        resurrected by the conflict target instead of replaced.

        Returns ``{"id", "market_changed"}``, or None when the property does
        not exist or is inactive for this account (the caller 404s).

        The row id is preserved so scrape jobs, alert rules and audits that
        reference it stay valid.
        """
        with self._engine().begin() as connection:
            existing = connection.execute(
                text(
                    """
                    SELECT canonical_destination
                    FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                      AND is_active = true
                    FOR UPDATE
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().first()
            if existing is None:
                return None

            market_changed = existing["canonical_destination"] != request.canonical_destination

            # The discovered rooms describe the OLD hotel.
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_owned_property_room_types
                    WHERE account_id = :account_id
                      AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            )
            if market_changed:
                # Competitors were selected for a different destination.
                connection.execute(
                    text(
                        """
                        DELETE FROM roomrate_tracked_competitors
                        WHERE account_id = :account_id
                          AND owned_property_id = :owned_property_id
                        """
                    ),
                    {"account_id": account_id, "owned_property_id": owned_property_id},
                )

            row = connection.execute(
                text(
                    """
                    UPDATE roomrate_owned_properties
                    SET
                        display_name = :display_name,
                        booking_url = :booking_url,
                        address = :address,
                        city = :city,
                        raw_destination = :raw_destination,
                        canonical_destination = :canonical_destination,
                        country = :country,
                        property_type = :property_type,
                        latitude = :latitude,
                        longitude = :longitude,
                        location_source = 'booking_search',
                        location_confidence = 0.90,
                        selected_room_type_category = NULL,
                        updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                      AND is_active = true
                    RETURNING id
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "display_name": request.display_name,
                    "booking_url": str(request.booking_url),
                    "address": request.address,
                    "city": request.city,
                    "raw_destination": request.raw_destination,
                    "canonical_destination": request.canonical_destination,
                    "country": request.country,
                    "property_type": request.property_type,
                    "latitude": request.latitude,
                    "longitude": request.longitude,
                },
            ).mappings().one()
        return {"id": row["id"], "market_changed": market_changed}

    def delete_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
    ) -> bool:
        """Delete one owned property; returns whether a row was removed.

        The schema does the cleanup: room types, tracked competitors, alert
        rules and pricing audits are ON DELETE CASCADE, while scrape jobs are
        ON DELETE SET NULL so the scrape history survives. Scrape runs and rate
        observations reference the account, not the property -- that market
        price history is deliberately kept.

        Unlike every sibling here, there is no ``is_active`` fence, on
        purpose: a deactivated property can still be deleted. Only a row
        missing for this account returns False.
        """
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            )
        return bool(result.rowcount)

    def list_room_types(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[dict[str, Any]]:
        """Return active room types discovered for one owned property."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        owned_property_id,
                        room_type,
                        room_type_category,
                        sample_meals,
                        sample_free_cancellation,
                        sample_facilities,
                        sample_price_per_night_eur,
                        is_active,
                        created_at,
                        updated_at
                    FROM roomrate_owned_property_room_types
                    WHERE account_id = :account_id
                      AND owned_property_id = :owned_property_id
                      AND is_active = true
                    ORDER BY room_type_category ASC, sample_price_per_night_eur ASC NULLS LAST, room_type ASC
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_selected_room_type(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Return the discovered room row for the property's selected category.

        The user picks a CATEGORY in step 2, but a category can hold several
        discovered rows (e.g. two "double" variants). Cheapest-first, newest
        as tie-break, keeps the baseline on the same row the pricing screens
        quote instead of drifting between variants on every refresh.

        The row ``id`` and the sample columns ride along for the
        room-matching agent: match rows are keyed on ``rt.id`` and the
        samples feed the reference-room payload (spec 2026-09-29 Α.2).
        """
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        rt.id,
                        rt.owned_property_id,
                        rt.room_type,
                        rt.room_type_category,
                        rt.sample_price_per_night_eur,
                        rt.sample_meals,
                        rt.sample_free_cancellation,
                        rt.sample_facilities
                    FROM roomrate_owned_property_room_types rt
                    JOIN roomrate_owned_properties op
                      ON op.account_id = rt.account_id
                     AND op.id = rt.owned_property_id
                    WHERE rt.account_id = :account_id
                      AND rt.owned_property_id = :owned_property_id
                      AND rt.is_active = true
                      AND op.is_active = true
                      AND op.selected_room_type_category IS NOT NULL
                      AND rt.room_type_category = op.selected_room_type_category
                    ORDER BY rt.sample_price_per_night_eur ASC NULLS LAST, rt.updated_at DESC
                    LIMIT 1
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().first()
        return dict(row) if row else None

    def get_owned_room_type(
        self,
        account_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Return one active discovered room row by id, account-scoped.

        The manual room-matching endpoint takes ``owned_room_type_id``
        directly (spec 2026-09-29 Α.1: «Επανεκτίμηση ταιριάσματος» after the
        reference room changes), so the lookup is by primary key rather than
        via the property's selected category. Returns ``None`` for a
        missing, inactive or foreign-account row (the caller 404s).
        """
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        owned_property_id,
                        room_type,
                        room_type_category,
                        sample_price_per_night_eur,
                        sample_meals,
                        sample_free_cancellation,
                        sample_facilities
                    FROM roomrate_owned_property_room_types
                    WHERE account_id = :account_id
                      AND id = :owned_room_type_id
                      AND is_active = true
                    LIMIT 1
                    """
                ),
                {"account_id": account_id, "owned_room_type_id": owned_room_type_id},
            ).mappings().first()
        return dict(row) if row else None

    def get_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Return the owned property's market-key fields plus coordinates and Booking URL.

        Used by the price-recommendation endpoint to resolve the market key for
        the price-history lookup. Round 6: ``ScrapeJobService`` resolves the
        radius centre and ``GET /maps/own-property`` the «Εσείς» marker from
        this row. Returns ``None`` when the property does not exist or is
        inactive for this account.
        """
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        display_name,
                        canonical_destination,
                        selected_room_type_category,
                        booking_url,
                        latitude,
                        longitude
                    FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                      AND is_active = true
                    LIMIT 1
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().first()
        return dict(row) if row else None

    def set_selected_room_type(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        room_type_category: str,
    ) -> bool:
        """Persist the selected baseline room category if it was discovered for this property."""
        with self._engine().begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE roomrate_owned_properties op
                    SET
                        selected_room_type_category = :room_type_category,
                        updated_at = now()
                    WHERE op.account_id = :account_id
                      AND op.id = :owned_property_id
                      AND op.is_active = true
                      AND EXISTS (
                          SELECT 1
                          FROM roomrate_owned_property_room_types rt
                          WHERE rt.account_id = op.account_id
                            AND rt.owned_property_id = op.id
                            AND rt.room_type_category = :room_type_category
                            AND rt.is_active = true
                      )
                    RETURNING op.id
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "room_type_category": room_type_category,
                },
            ).mappings().first()
        return row is not None
