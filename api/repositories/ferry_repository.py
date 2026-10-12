"""PostgreSQL access for the ferry demand signal (migration 20261011_0031).

Destination-level data, shared by every account in a destination: nothing
here is account-scoped, and nothing here is returned without first resolving
the caller's own owned property (api/routers/demand.py, api/routers/agents.py).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine

# A failed lookup is retried after this long; a good one is refreshed monthly.
ERROR_RETRY_HOURS = 6
PROFILE_REFRESH_DAYS = 30

COLLECTED_ROUTE_STATUSES = ("auto", "confirmed")


class FerryRepository:
    def __init__(self, engine_factory: Callable[[str], Engine] | None = None):
        self._engine_factory = engine_factory

    def _engine(self, role: str = "api") -> Engine:
        return self._engine_factory(role) if self._engine_factory else get_engine(role=role)

    def destinations_to_profile(self, limit: int = 20) -> list[dict[str, Any]]:
        """Destinations of active owned properties (ferries on) without a current port lookup.

        Coordinates are the average over the destination's properties, which
        sit in one town; NULL when none has coordinates.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT op.canonical_destination,
                           min(op.city) AS city,
                           min(op.raw_destination) AS raw_destination,
                           avg(op.latitude)::float AS latitude,
                           avg(op.longitude)::float AS longitude
                    FROM roomrate_owned_properties op
                    LEFT JOIN roomrate_demand_settings ds ON ds.owned_property_id = op.id
                    LEFT JOIN roomrate_ferry_destination_profiles p
                      ON p.canonical_destination = op.canonical_destination
                    WHERE op.is_active
                      AND COALESCE(ds.ferries_enabled, true)
                      AND op.canonical_destination <> ''
                      AND (
                        p.canonical_destination IS NULL
                        OR (p.status = 'error'
                            AND p.resolved_at < now() - make_interval(hours => :error_retry_hours))
                        OR p.resolved_at < now() - make_interval(days => :refresh_days)
                      )
                    GROUP BY op.canonical_destination
                    ORDER BY op.canonical_destination
                    LIMIT :limit
                    """
                ),
                {"error_retry_hours": ERROR_RETRY_HOURS, "refresh_days": PROFILE_REFRESH_DAYS, "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def save_profile(
        self,
        canonical_destination: str,
        *,
        status: str,
        island_port: str | None = None,
        island_port_code: str | None = None,
        port_distance_km: float | None = None,
        matched_by: str | None = None,
        detail: str | None = None,
        routes: Sequence[tuple[str, str, int | None]] = (),
    ) -> None:
        """Store a lookup result; on "ferry"/"no_ferry" also replace the auto routes.

        ``routes`` are (origin_port, destination_port, sample_sailings). Routes
        the owner confirmed or disabled are kept as they are; an "error"
        result leaves every route (and its history) untouched.
        """
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_ferry_destination_profiles (
                        canonical_destination, status, island_port, island_port_code,
                        port_distance_km, matched_by, detail, resolved_at
                    )
                    VALUES (
                        :canonical_destination, :status, :island_port, :island_port_code,
                        :port_distance_km, :matched_by, :detail, now()
                    )
                    ON CONFLICT (canonical_destination) DO UPDATE SET
                        status = EXCLUDED.status,
                        -- A failed refresh keeps the port it found before.
                        island_port = CASE WHEN EXCLUDED.status = 'error'
                            THEN roomrate_ferry_destination_profiles.island_port
                            ELSE EXCLUDED.island_port END,
                        island_port_code = CASE WHEN EXCLUDED.status = 'error'
                            THEN roomrate_ferry_destination_profiles.island_port_code
                            ELSE EXCLUDED.island_port_code END,
                        port_distance_km = CASE WHEN EXCLUDED.status = 'error'
                            THEN roomrate_ferry_destination_profiles.port_distance_km
                            ELSE EXCLUDED.port_distance_km END,
                        matched_by = CASE WHEN EXCLUDED.status = 'error'
                            THEN roomrate_ferry_destination_profiles.matched_by
                            ELSE EXCLUDED.matched_by END,
                        detail = EXCLUDED.detail,
                        resolved_at = now(),
                        updated_at = now()
                    """
                ),
                {
                    "canonical_destination": canonical_destination,
                    "status": status,
                    "island_port": island_port,
                    "island_port_code": island_port_code,
                    "port_distance_km": port_distance_km,
                    "matched_by": matched_by,
                    "detail": detail,
                },
            )
            if status == "error":
                return
            keep = [(origin, destination) for origin, destination, _ in routes]
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_ferry_routes
                    WHERE canonical_destination = :canonical_destination
                      AND status = 'auto'
                      AND (origin_port, destination_port) NOT IN (
                          SELECT o, d FROM unnest(CAST(:origins AS text[]), CAST(:destinations AS text[])) AS k(o, d)
                      )
                    """
                ),
                {
                    "canonical_destination": canonical_destination,
                    "origins": [origin for origin, _ in keep],
                    "destinations": [destination for _, destination in keep],
                },
            )
            for origin, destination, sample_sailings in routes:
                connection.execute(
                    text(
                        """
                        INSERT INTO roomrate_ferry_routes (
                            id, canonical_destination, origin_port, destination_port, sample_sailings
                        )
                        VALUES (:id, :canonical_destination, :origin_port, :destination_port, :sample_sailings)
                        ON CONFLICT (canonical_destination, origin_port, destination_port) DO UPDATE SET
                            sample_sailings = EXCLUDED.sample_sailings,
                            updated_at = now()
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "canonical_destination": canonical_destination,
                        "origin_port": origin,
                        "destination_port": destination,
                        "sample_sailings": sample_sailings,
                    },
                )

    def routes_to_collect(self, destinations: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Collected routes of destinations with an active owned property that has ferries on.

        ``destinations`` narrows them (the worker's due destinations); None is all.

        A destination whose last refresh failed ("error") keeps collecting on
        the routes found before; a first lookup that failed has none.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT r.id, r.canonical_destination, r.origin_port, r.destination_port
                    FROM roomrate_ferry_routes r
                    JOIN roomrate_ferry_destination_profiles p
                      ON p.canonical_destination = r.canonical_destination
                    WHERE p.status IN ('ferry', 'error')
                      AND r.status IN ('auto', 'confirmed')
                      AND EXISTS (
                          SELECT 1 FROM roomrate_owned_properties op
                          LEFT JOIN roomrate_demand_settings ds ON ds.owned_property_id = op.id
                          WHERE op.is_active AND op.canonical_destination = r.canonical_destination
                            AND COALESCE(ds.ferries_enabled, true)
                      )
                      AND (CAST(:destinations AS text[]) IS NULL
                           OR r.canonical_destination = ANY(CAST(:destinations AS text[])))
                    ORDER BY r.canonical_destination, r.origin_port, r.destination_port
                    """
                ),
                {"destinations": list(destinations) if destinations is not None else None},
            ).mappings().all()
        return [dict(row) for row in rows]

    def schedule_lead_days_by_destination(self) -> dict[str, set[int]]:
        """lead_days of the enabled schedules, per destination of the account's properties."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT op.canonical_destination, sc.lead_days
                    FROM roomrate_schedule_configs sc
                    JOIN roomrate_owned_properties op
                      ON op.account_id = sc.account_id AND op.is_active
                    WHERE sc.enabled
                    """
                )
            ).mappings().all()
        lead_days: dict[str, set[int]] = {}
        for row in rows:
            lead_days.setdefault(row["canonical_destination"], set()).add(int(row["lead_days"]))
        return lead_days

    def upsert_observation(
        self,
        *,
        route_id: uuid.UUID,
        travel_date: date,
        observed_on: date,
        sailings: int,
        availability_known: int,
        sold_out: int,
        min_price_eur: float | None,
        median_price_eur: float | None,
        operators: int,
        raw_payload: Any,
        parser_version: str,
        accommodation_options: int | None = None,
    ) -> None:
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_ferry_observations (
                        route_id, travel_date, observed_on, sailings, availability_known,
                        sold_out, min_price_eur, median_price_eur, operators, raw_payload,
                        parser_version, accommodation_options
                    )
                    VALUES (
                        :route_id, :travel_date, :observed_on, :sailings, :availability_known,
                        :sold_out, :min_price_eur, :median_price_eur, :operators,
                        CAST(:raw_payload AS jsonb), :parser_version, :accommodation_options
                    )
                    ON CONFLICT (route_id, travel_date, observed_on) DO UPDATE SET
                        observed_at = now(),
                        sailings = EXCLUDED.sailings,
                        availability_known = EXCLUDED.availability_known,
                        sold_out = EXCLUDED.sold_out,
                        min_price_eur = EXCLUDED.min_price_eur,
                        median_price_eur = EXCLUDED.median_price_eur,
                        operators = EXCLUDED.operators,
                        raw_payload = EXCLUDED.raw_payload,
                        parser_version = EXCLUDED.parser_version,
                        accommodation_options = EXCLUDED.accommodation_options
                    """
                ),
                {
                    "route_id": route_id,
                    "travel_date": travel_date,
                    "observed_on": observed_on,
                    "sailings": sailings,
                    "availability_known": availability_known,
                    "sold_out": sold_out,
                    "min_price_eur": min_price_eur,
                    "median_price_eur": median_price_eur,
                    "operators": operators,
                    "raw_payload": None if raw_payload is None else json.dumps(raw_payload, default=str),
                    "parser_version": parser_version,
                    "accommodation_options": accommodation_options,
                },
            )

    def observation_history(
        self,
        canonical_destination: str,
        *,
        date_from: date,
        date_to: date,
        observed_since: date,
    ) -> list[dict[str, Any]]:
        """Every look at the destination's collected routes since ``observed_since``.

        The pickup signal compares each sailing day's newest look with an
        earlier one; a route-day is a row per collection day.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT o.route_id, r.origin_port, r.destination_port, o.travel_date,
                           o.observed_on, o.sailings, o.availability_known, o.sold_out,
                           o.min_price_eur::float AS min_price_eur,
                           o.median_price_eur::float AS median_price_eur,
                           o.accommodation_options
                    FROM roomrate_ferry_observations o
                    JOIN roomrate_ferry_routes r ON r.id = o.route_id
                    WHERE r.canonical_destination = :canonical_destination
                      AND r.status IN ('auto', 'confirmed')
                      AND o.travel_date BETWEEN :date_from AND :date_to
                      AND o.observed_on >= :observed_since
                    ORDER BY o.route_id, o.travel_date, o.observed_on
                    """
                ),
                {
                    "canonical_destination": canonical_destination,
                    "date_from": date_from,
                    "date_to": date_to,
                    "observed_since": observed_since,
                },
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_profile(self, canonical_destination: str) -> dict[str, Any] | None:
        """The destination's port lookup with its routes, or None before the first lookup."""
        with self._engine().connect() as connection:
            profile = connection.execute(
                text(
                    """
                    SELECT canonical_destination, status, island_port, port_distance_km::float AS port_distance_km,
                           matched_by, resolved_at
                    FROM roomrate_ferry_destination_profiles
                    WHERE canonical_destination = :canonical_destination
                    """
                ),
                {"canonical_destination": canonical_destination},
            ).mappings().first()
            if profile is None:
                return None
            routes = connection.execute(
                text(
                    """
                    SELECT id, origin_port, destination_port, status, sample_sailings
                    FROM roomrate_ferry_routes
                    WHERE canonical_destination = :canonical_destination
                    ORDER BY status = 'disabled', origin_port
                    """
                ),
                {"canonical_destination": canonical_destination},
            ).mappings().all()
        return {**dict(profile), "routes": [dict(route) for route in routes]}

    def prune(self, *, raw_payload_days: int, observation_days: int) -> tuple[int, int]:
        """Clear raw answers past their retention and drop observations of long-past dates."""
        cleared = deleted = 0
        with self._engine("writer").begin() as connection:
            if raw_payload_days > 0:
                result = connection.execute(
                    text(
                        """
                        UPDATE roomrate_ferry_observations SET raw_payload = NULL
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
                        DELETE FROM roomrate_ferry_observations
                        WHERE travel_date < current_date - :days
                        """
                    ),
                    {"days": observation_days},
                )
                deleted = max(result.rowcount or 0, 0)
        return cleared, deleted
