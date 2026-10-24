"""PostgreSQL access for the flight demand signal (migration 20261011_0033).

Airport-level data, shared by every account on the island: nothing here is
account-scoped, and nothing here is returned without first resolving the
caller's own owned property (api/routers/demand.py, api/routers/agents.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine


class FlightRepository:
    def __init__(self, engine_factory: Callable[[str], Engine] | None = None):
        self._engine_factory = engine_factory

    def _engine(self, role: str = "api") -> Engine:
        return self._engine_factory(role) if self._engine_factory else get_engine(role=role)

    def property_points(self) -> list[dict[str, Any]]:
        """Coordinates of the active owned properties with flights on: their islands give the airports."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT op.latitude::float AS latitude, op.longitude::float AS longitude
                    FROM roomrate_owned_properties op
                    LEFT JOIN roomrate_demand_settings ds ON ds.owned_property_id = op.id
                    WHERE op.is_active
                      AND COALESCE(ds.flights_enabled, true)
                      AND op.latitude IS NOT NULL AND op.longitude IS NOT NULL
                      AND op.latitude <> 0 AND op.longitude <> 0
                    """
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    def newest_looks(self, airports: Sequence[str], date_from: date) -> dict[tuple[str, date, int], date]:
        """The newest observed_on of each (airport, travel date, window start) from ``date_from`` on."""
        if not airports:
            return {}
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT airport_iata, travel_date, window_start_hour, max(observed_on) AS observed_on
                    FROM roomrate_flight_observations
                    WHERE airport_iata = ANY(CAST(:airports AS text[]))
                      AND travel_date >= :date_from
                    GROUP BY airport_iata, travel_date, window_start_hour
                    """
                ),
                {"airports": list(airports), "date_from": date_from},
            ).mappings().all()
        return {
            (row["airport_iata"], row["travel_date"], int(row["window_start_hour"])): row["observed_on"]
            for row in rows
        }

    def calls_since(self, observed_since: date) -> int:
        """AeroDataBox calls made from ``observed_since`` on (the month's spent budget)."""
        with self._engine().connect() as connection:
            calls = connection.execute(
                text(
                    """
                    SELECT COALESCE(sum(calls), 0)
                    FROM roomrate_flight_observations
                    WHERE observed_on >= :observed_since
                    """
                ),
                {"observed_since": observed_since},
            ).scalar()
        return int(calls or 0)

    def upsert_observation(
        self,
        *,
        airport_iata: str,
        travel_date: date,
        window_start_hour: int,
        window_end_hour: int,
        observed_on: date,
        arrivals: int,
        airlines: int,
        origins: dict[str, Any] | None,
        units_remaining: int | None,
        raw_payload: Any,
        parser_version: str,
    ) -> None:
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_flight_observations (
                        airport_iata, travel_date, window_start_hour, window_end_hour, observed_on,
                        arrivals, airlines, origins, units_remaining, raw_payload, parser_version
                    )
                    VALUES (
                        :airport_iata, :travel_date, :window_start_hour, :window_end_hour, :observed_on,
                        :arrivals, :airlines, CAST(:origins AS jsonb), :units_remaining,
                        CAST(:raw_payload AS jsonb), :parser_version
                    )
                    ON CONFLICT (airport_iata, travel_date, window_start_hour, observed_on) DO UPDATE SET
                        observed_at = now(),
                        window_end_hour = EXCLUDED.window_end_hour,
                        arrivals = EXCLUDED.arrivals,
                        airlines = EXCLUDED.airlines,
                        origins = EXCLUDED.origins,
                        calls = roomrate_flight_observations.calls + 1,
                        units_remaining = EXCLUDED.units_remaining,
                        raw_payload = EXCLUDED.raw_payload,
                        parser_version = EXCLUDED.parser_version
                    """
                ),
                {
                    "airport_iata": airport_iata,
                    "travel_date": travel_date,
                    "window_start_hour": window_start_hour,
                    "window_end_hour": window_end_hour,
                    "observed_on": observed_on,
                    "arrivals": arrivals,
                    "airlines": airlines,
                    "origins": None if origins is None else json.dumps(origins, ensure_ascii=False),
                    "units_remaining": units_remaining,
                    "raw_payload": None if raw_payload is None else json.dumps(raw_payload, default=str),
                    "parser_version": parser_version,
                },
            )

    def observation_history(
        self,
        airport_iata: str,
        *,
        date_from: date,
        date_to: date,
        observed_since: date,
    ) -> list[dict[str, Any]]:
        """Every look at the airport's travel dates since ``observed_since``, a row per window and day."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT airport_iata, travel_date, window_start_hour, window_end_hour, observed_on,
                           arrivals, airlines, origins
                    FROM roomrate_flight_observations
                    WHERE airport_iata = :airport_iata
                      AND travel_date BETWEEN :date_from AND :date_to
                      AND observed_on >= :observed_since
                    ORDER BY travel_date, window_start_hour, observed_on
                    """
                ),
                {
                    "airport_iata": airport_iata,
                    "date_from": date_from,
                    "date_to": date_to,
                    "observed_since": observed_since,
                },
            ).mappings().all()
        return [dict(row) for row in rows]

    def latest_units_remaining(self) -> tuple[int | None, Any]:
        """The units AeroDataBox last reported left, and when (None, None before any call)."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT units_remaining, observed_at
                    FROM roomrate_flight_observations
                    WHERE units_remaining IS NOT NULL
                    ORDER BY observed_at DESC
                    LIMIT 1
                    """
                )
            ).first()
        return (int(row[0]), row[1]) if row else (None, None)

    def prune(self, *, raw_payload_days: int, observation_days: int) -> tuple[int, int]:
        """Clear raw answers past their retention and drop observations of long-past dates."""
        cleared = deleted = 0
        with self._engine("writer").begin() as connection:
            if raw_payload_days > 0:
                result = connection.execute(
                    text(
                        """
                        UPDATE roomrate_flight_observations SET raw_payload = NULL
                        WHERE raw_payload IS NOT NULL
                          AND observed_on < current_date - :days
                        """
                    ),
                    {"days": raw_payload_days},
                )
                cleared = max(result.rowcount or 0, 0)
            if observation_days > 0:
                result = connection.execute(
                    text(
                        """
                        DELETE FROM roomrate_flight_observations
                        WHERE travel_date < current_date - :days
                        """
                    ),
                    {"days": observation_days},
                )
                deleted = max(result.rowcount or 0, 0)
        return cleared, deleted
