"""PostgreSQL access for the owner's demand collection settings and the daily run log.

Migration 20261012_0034. Settings are per owned property and always read or
written through the caller's own account; the run log is worker state.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine

# A missing settings row is these.
DEFAULT_SETTINGS = {"ferries_enabled": True, "flights_enabled": True, "hour_local": 8, "collect_requested_at": None}


class DemandSettingsRepository:
    def __init__(self, engine_factory: Callable[[str], Engine] | None = None):
        self._engine_factory = engine_factory

    def _engine(self, role: str = "api") -> Engine:
        return self._engine_factory(role) if self._engine_factory else get_engine(role=role)

    def get(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict[str, Any]:
        """The property's settings, the defaults when the owner never saved any."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT ferries_enabled, flights_enabled, hour_local, collect_requested_at
                    FROM roomrate_demand_settings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().first()
        return dict(row) if row else dict(DEFAULT_SETTINGS)

    def save(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        *,
        ferries_enabled: bool,
        flights_enabled: bool,
        hour_local: int,
        request_collection: bool = False,
    ) -> dict[str, Any]:
        """Store the owner's choice; ``request_collection`` also asks for a run now."""
        with self._engine("writer").begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO roomrate_demand_settings (
                        owned_property_id, account_id, ferries_enabled, flights_enabled, hour_local,
                        collect_requested_at
                    )
                    VALUES (
                        :owned_property_id, :account_id, :ferries_enabled, :flights_enabled, :hour_local,
                        CASE WHEN :request_collection THEN now() END
                    )
                    ON CONFLICT (owned_property_id) DO UPDATE SET
                        ferries_enabled = EXCLUDED.ferries_enabled,
                        flights_enabled = EXCLUDED.flights_enabled,
                        hour_local = EXCLUDED.hour_local,
                        collect_requested_at = CASE WHEN :request_collection THEN now()
                            ELSE roomrate_demand_settings.collect_requested_at END,
                        updated_at = now()
                    WHERE roomrate_demand_settings.account_id = :account_id
                    RETURNING ferries_enabled, flights_enabled, hour_local, collect_requested_at
                    """
                ),
                {
                    "owned_property_id": owned_property_id,
                    "account_id": account_id,
                    "ferries_enabled": ferries_enabled,
                    "flights_enabled": flights_enabled,
                    "hour_local": hour_local,
                    "request_collection": request_collection,
                },
            ).mappings().first()
        return dict(row) if row else dict(DEFAULT_SETTINGS)

    def demand_properties(self) -> list[dict[str, Any]]:
        """Active owned properties with their settings (defaults filled in) and coordinates."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT op.id AS owned_property_id, op.canonical_destination,
                           op.latitude::float AS latitude, op.longitude::float AS longitude,
                           COALESCE(ds.ferries_enabled, true) AS ferries_enabled,
                           COALESCE(ds.flights_enabled, true) AS flights_enabled,
                           COALESCE(ds.hour_local, 8) AS hour_local,
                           ds.collect_requested_at
                    FROM roomrate_owned_properties op
                    LEFT JOIN roomrate_demand_settings ds ON ds.owned_property_id = op.id
                    WHERE op.is_active
                    ORDER BY op.canonical_destination, op.id
                    """
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    def runs(self, kind: str | None = None, *, since: date) -> list[dict[str, Any]]:
        """Runs from ``since`` on, newest first."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT kind, scope, run_on, started_at, finished_at, saved
                    FROM roomrate_demand_runs
                    WHERE run_on >= :since AND (CAST(:kind AS text) IS NULL OR kind = :kind)
                    ORDER BY started_at DESC
                    """
                ),
                {"since": since, "kind": kind},
            ).mappings().all()
        return [dict(row) for row in rows]

    def last_run(self, kind: str, scope: str) -> dict[str, Any] | None:
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT kind, scope, run_on, started_at, finished_at, saved
                    FROM roomrate_demand_runs
                    WHERE kind = :kind AND scope = :scope
                    ORDER BY run_on DESC
                    LIMIT 1
                    """
                ),
                {"kind": kind, "scope": scope},
            ).mappings().first()
        return dict(row) if row else None

    def claim_run(self, kind: str, scope: str, run_on: date) -> bool:
        """Start the day's run of a scope; False when it already ran (or runs) that day."""
        with self._engine("writer").begin() as connection:
            claimed = connection.execute(
                text(
                    """
                    INSERT INTO roomrate_demand_runs (kind, scope, run_on)
                    VALUES (:kind, :scope, :run_on)
                    ON CONFLICT DO NOTHING
                    RETURNING 1
                    """
                ),
                {"kind": kind, "scope": scope, "run_on": run_on},
            ).first()
        return claimed is not None

    def finish_run(self, kind: str, scope: str, run_on: date, saved: int) -> None:
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE roomrate_demand_runs SET finished_at = now(), saved = :saved
                    WHERE kind = :kind AND scope = :scope AND run_on = :run_on
                    """
                ),
                {"kind": kind, "scope": scope, "run_on": run_on, "saved": saved},
            )

    def release_failed_run(self, kind: str, scope: str, run_on: date) -> bool:
        """Forget the day's run when it saved nothing, so «Συλλογή τώρα» can try again.

        A run still in progress (not finished) or one that saved data stays.
        """
        with self._engine("writer").begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_demand_runs
                    WHERE kind = :kind AND scope = :scope AND run_on = :run_on
                      AND finished_at IS NOT NULL AND COALESCE(saved, 0) = 0
                    """
                ),
                {"kind": kind, "scope": scope, "run_on": run_on},
            )
        return bool(result.rowcount)

    def prune_runs(self, *, keep_days: int, today: date) -> int:
        with self._engine("writer").begin() as connection:
            result = connection.execute(
                text("DELETE FROM roomrate_demand_runs WHERE run_on < CAST(:today AS date) - :days"),
                {"today": today, "days": keep_days},
            )
        return max(result.rowcount or 0, 0)
