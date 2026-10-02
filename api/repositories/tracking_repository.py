from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine
from api.schemas.tracking import TrackedCompetitorSetRequest
from api.services.room_rates_normalizer import comparable_room_type_categories


class TrackingRepository:
    """PostgreSQL repository for user-selected competitor tracking."""

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

    def add_tracked_competitors(
        self,
        account_id: uuid.UUID,
        request: TrackedCompetitorSetRequest,
    ) -> int:
        """Add or reactivate tracked competitors without removing existing rows."""
        with self._engine().begin() as connection:
            return self._upsert_tracked_competitors(connection, account_id, request)

    def replace_tracked_competitors(
        self,
        account_id: uuid.UUID,
        request: TrackedCompetitorSetRequest,
    ) -> int:
        """Replace active tracked competitors for one property/category scope."""
        with self._engine().begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE roomrate_tracked_competitors
                    SET is_active = false, updated_at = now()
                    WHERE account_id = :account_id
                      AND owned_property_id = :owned_property_id
                      AND room_type_category = :room_type_category
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": request.owned_property_id,
                    "room_type_category": request.room_type_category,
                },
            )

            return self._upsert_tracked_competitors(connection, account_id, request)

    def _upsert_tracked_competitors(self, connection, account_id: uuid.UUID, request: TrackedCompetitorSetRequest) -> int:
        """Upsert the whole competitor batch in ONE unnest-based round trip.

        Duplicated property_ids within one request are collapsed to the last
        occurrence (the same final row the old per-competitor loop produced —
        a multi-row INSERT may not touch the same conflict target twice).
        The returned count still reflects the request size, like before.
        """
        if not request.competitors:
            return 0

        last_by_property = {competitor.property_id: competitor for competitor in request.competitors}
        deduped = list(last_by_property.values())
        connection.execute(
            text(
                """
                INSERT INTO roomrate_tracked_competitors (
                    id,
                    account_id,
                    owned_property_id,
                    competitor_property_id,
                    competitor_room_package_id,
                    room_type_category,
                    is_active
                )
                SELECT
                    incoming.id,
                    :account_id,
                    :owned_property_id,
                    incoming.competitor_property_id,
                    incoming.competitor_room_package_id,
                    :room_type_category,
                    true
                FROM unnest(
                    CAST(:ids AS uuid[]),
                    CAST(:competitor_property_ids AS uuid[]),
                    CAST(:competitor_room_package_ids AS uuid[])
                ) AS incoming(id, competitor_property_id, competitor_room_package_id)
                ON CONFLICT (
                    account_id,
                    owned_property_id,
                    room_type_category,
                    competitor_property_id
                ) DO UPDATE SET
                    competitor_room_package_id = EXCLUDED.competitor_room_package_id,
                    is_active = true,
                    updated_at = now()
                """
            ),
            {
                "ids": [uuid.uuid4() for _ in deduped],
                "account_id": account_id,
                "owned_property_id": request.owned_property_id,
                "room_type_category": request.room_type_category,
                "competitor_property_ids": [competitor.property_id for competitor in deduped],
                "competitor_room_package_ids": [competitor.room_package_id for competitor in deduped],
            },
        )
        return len(request.competitors)

    def list_tracked_competitors(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        room_type_category: str,
    ) -> list[dict]:
        """Return active tracked competitors for one comparable room pool.

        The persisted key remains exact (``double`` stays ``double``), while
        preselection reads also find an equivalent ``twin`` key and vice versa.
        """
        category_params = {
            f"room_type_category_{index}": category
            for index, category in enumerate(
                comparable_room_type_categories(room_type_category)
            )
        }
        if not category_params:
            return []
        placeholders = ", ".join(f":{name}" for name in category_params)
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    WITH pooled_tracking AS (
                        SELECT
                            competitor_property_id AS property_id,
                            competitor_room_package_id AS room_package_id,
                            updated_at,
                            created_at,
                            row_number() OVER (
                                PARTITION BY competitor_property_id
                                ORDER BY updated_at DESC, created_at DESC, id DESC
                            ) AS rn
                        FROM roomrate_tracked_competitors
                        WHERE account_id = :account_id
                          AND owned_property_id = :owned_property_id
                          AND room_type_category IN ({placeholders})
                          AND is_active = true
                    )
                    SELECT property_id, room_package_id
                    FROM pooled_tracking
                    WHERE rn = 1
                    ORDER BY updated_at DESC, created_at DESC, property_id ASC
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    **category_params,
                },
            ).mappings().all()
        # Αμυντικό dedupe στο application boundary: το SQL ήδη επιλέγει τη
        # νεότερη pooled γραμμή, αλλά ένα παλιό DB view/fake δεν πρέπει να
        # φουσκώσει το checklist με το ίδιο property δύο φορές.
        unique_rows: list[dict] = []
        seen_property_ids: set[object] = set()
        for row in rows:
            item = dict(row)
            property_id = item.get("property_id")
            if property_id in seen_property_ids:
                continue
            seen_property_ids.add(property_id)
            unique_rows.append(item)
        return unique_rows
