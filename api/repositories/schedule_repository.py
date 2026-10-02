from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine

# Shared SELECT/RETURNING column list so every method returns the same shape.
CONFIG_COLUMNS = """
    id, account_id, enabled, frequency_hours, hour_local, timezone, hour_utc,
    lead_days, nights, adults, children, rooms, consecutive_failures,
    last_run_at, created_at, updated_at
"""


class ScheduleRepository:
    """PostgreSQL repository for per-account scrape schedule configuration."""

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

    def get_config(self, account_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch the schedule config for one account, or None when unset."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT {CONFIG_COLUMNS}
                    FROM roomrate_schedule_configs
                    WHERE account_id = :account_id
                    """
                ),
                {"account_id": account_id},
            ).mappings().first()
        return dict(row) if row else None

    def upsert_config(self, account_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        """Insert or update the single schedule row for one account.

        ``payload`` must carry the full editable field set (the service merges
        partial updates over the current row / defaults first), so the SQL
        stays static. ``last_run_at`` is intentionally untouched on update.

        ``consecutive_failures`` is deliberately NOT in the DO UPDATE SET
        list: a PUT carrying a stale read must not clobber breaker updates
        recorded concurrently by the executor. The enable-transition reset is
        an explicit ``reset_failures`` call made by the service instead.
        """
        with self._engine().begin() as connection:
            row = connection.execute(
                text(
                    f"""
                    INSERT INTO roomrate_schedule_configs (
                        id, account_id, enabled, frequency_hours, hour_local, hour_utc,
                        lead_days, nights, adults, children, rooms, consecutive_failures
                    )
                    VALUES (
                        :id, :account_id, :enabled, :frequency_hours, :hour_local, :hour_utc,
                        :lead_days, :nights, :adults, :children, :rooms, :consecutive_failures
                    )
                    ON CONFLICT (account_id) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        frequency_hours = EXCLUDED.frequency_hours,
                        hour_local = EXCLUDED.hour_local,
                        hour_utc = EXCLUDED.hour_utc,
                        lead_days = EXCLUDED.lead_days,
                        nights = EXCLUDED.nights,
                        adults = EXCLUDED.adults,
                        children = EXCLUDED.children,
                        rooms = EXCLUDED.rooms,
                        updated_at = now()
                    RETURNING {CONFIG_COLUMNS}
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "account_id": account_id,
                    "enabled": payload["enabled"],
                    "frequency_hours": payload["frequency_hours"],
                    "hour_local": payload["hour_local"],
                    "hour_utc": payload["hour_utc"],
                    "lead_days": payload["lead_days"],
                    "nights": payload["nights"],
                    "adults": payload["adults"],
                    "children": payload["children"],
                    "rooms": payload["rooms"],
                    "consecutive_failures": payload["consecutive_failures"],
                },
            ).mappings().one()
        return dict(row)

    def list_enabled_configs(self) -> list[dict[str, Any]]:
        """Every enabled schedule, least recently run first.

        Whether one is due depends on its own time zone and local calendar,
        so ScheduleService decides (api/services/schedule_timing.py). The
        table holds one row per account, so reading all enabled rows is cheap.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT {CONFIG_COLUMNS}
                    FROM roomrate_schedule_configs
                    WHERE enabled = true
                    ORDER BY last_run_at ASC NULLS FIRST
                    """
                ),
            ).mappings().all()
        return [dict(row) for row in rows]

    def touch_last_run(self, config_id: uuid.UUID, now: datetime) -> None:
        """Stamp a due config as ticked so it cannot re-fire every cycle."""
        with self._engine().begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE roomrate_schedule_configs
                    SET last_run_at = :now, updated_at = now()
                    WHERE id = :config_id
                    """
                ),
                {"config_id": config_id, "now": now},
            )

    def record_failure(self, account_id: uuid.UUID) -> int:
        """Increment the circuit breaker; returns the new failure count."""
        with self._engine().begin() as connection:
            failures = connection.execute(
                text(
                    """
                    UPDATE roomrate_schedule_configs
                    SET
                        consecutive_failures = consecutive_failures + 1,
                        updated_at = now()
                    WHERE account_id = :account_id
                    RETURNING consecutive_failures
                    """
                ),
                {"account_id": account_id},
            ).scalar()
        # No config row (e.g. deleted between enqueue and feedback): nothing
        # to break, report zero so callers never disable a missing schedule.
        return int(failures) if failures is not None else 0

    def reset_failures(self, account_id: uuid.UUID) -> None:
        """Reset the circuit breaker after a scheduled job succeeds."""
        with self._engine().begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE roomrate_schedule_configs
                    SET consecutive_failures = 0, updated_at = now()
                    WHERE account_id = :account_id
                      AND consecutive_failures <> 0
                    """
                ),
                {"account_id": account_id},
            )

    def disable(self, account_id: uuid.UUID) -> None:
        """Turn one account's schedule off (circuit breaker tripped)."""
        with self._engine().begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE roomrate_schedule_configs
                    SET enabled = false, updated_at = now()
                    WHERE account_id = :account_id
                    """
                ),
                {"account_id": account_id},
            )

    def list_schedulable_properties(self, account_id: uuid.UUID) -> list[dict[str, Any]]:
        """Owned properties that are ready for scheduled competitor scrapes.

        Ready means: active, with a selected room-type category, and with at
        least one active tracked competitor in that category — otherwise a
        scheduled scrape would produce data nobody compares against.

        Ordering is least-recently-scheduled first (never-scheduled before
        all): with the per-account active-jobs cap, an account with more
        schedulable properties than the cap would otherwise enqueue the SAME
        first properties every cycle and starve the rest forever. Ordering by
        each property's most recent scheduled scrape-job time rotates the cap
        slots fairly across cycles; ``created_at`` breaks ties.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT
                        op.id,
                        op.raw_destination,
                        op.canonical_destination,
                        op.selected_room_type_category
                    FROM roomrate_owned_properties op
                    WHERE op.account_id = :account_id
                      AND op.is_active = true
                      AND op.selected_room_type_category IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM roomrate_tracked_competitors tc
                          WHERE tc.account_id = op.account_id
                            AND tc.owned_property_id = op.id
                            AND tc.room_type_category = op.selected_room_type_category
                            AND tc.is_active = true
                      )
                    ORDER BY
                        (
                            SELECT max(sj.requested_at)
                            FROM roomrate_scrape_jobs sj
                            WHERE sj.owned_property_id = op.id
                              AND sj.scheduled
                        ) ASC NULLS FIRST,
                        op.created_at
                    """
                ),
                {"account_id": account_id},
            ).mappings().all()
        return [dict(row) for row in rows]
