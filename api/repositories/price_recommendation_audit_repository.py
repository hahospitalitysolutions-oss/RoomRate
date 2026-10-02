"""Persistence, quota and short-lived cache for pricing-agent decisions."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from api.db import get_engine


class PriceRecommendationAuditRepository:
    """Account-scoped repository for pricing-agent audit records."""

    def count_today(self, account_id: uuid.UUID) -> int:
        """Count recommendations generated for this account today (UTC)."""
        with get_engine(role="api").connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM roomrate_price_recommendation_audits
                    WHERE account_id = :account_id
                      AND created_at >= (
                          date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                      )
                    """
                ),
                {"account_id": account_id},
            ).scalar()
        return int(value or 0)

    def get_cached(
        self, account_id: uuid.UUID, request_hash: str, prompt_version: str
    ) -> dict[str, Any] | None:
        """Return the latest unexpired response for an identical request.

        Scoped to the CURRENT prompt version: an entry audited under a
        pre-upgrade prompt/model must never serve after the upgrade. The SQL
        equality filter covers every future bump automatically.
        """
        with get_engine(role="api").connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT id, response_payload, model_version, prompt_version, created_at
                        FROM roomrate_price_recommendation_audits
                        WHERE account_id = :account_id
                          AND request_hash = :request_hash
                          AND prompt_version = :prompt_version
                          AND cache_expires_at > now()
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "account_id": account_id,
                        "request_hash": request_hash,
                        "prompt_version": prompt_version,
                    },
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def insert(
        self,
        *,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request_hash: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        source: str | None,
        model_version: str,
        prompt_version: str,
        latency_ms: int,
        cache_minutes: int,
    ) -> dict[str, Any]:
        """Persist one complete decision and return its audit metadata."""
        audit_id = uuid.uuid4()
        with get_engine(role="api").begin() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        INSERT INTO roomrate_price_recommendation_audits (
                            id, account_id, owned_property_id, request_hash,
                            request_payload, response_payload, source,
                            model_version, prompt_version, latency_ms,
                            cache_expires_at
                        )
                        VALUES (
                            :id, :account_id, :owned_property_id, :request_hash,
                            CAST(:request_payload AS jsonb),
                            CAST(:response_payload AS jsonb),
                            :source, :model_version, :prompt_version, :latency_ms,
                            now() + make_interval(mins => :cache_minutes)
                        )
                        RETURNING id, created_at
                        """
                    ),
                    {
                        "id": audit_id,
                        "account_id": account_id,
                        "owned_property_id": owned_property_id,
                        "request_hash": request_hash,
                        "request_payload": json.dumps(request_payload, default=str),
                        "response_payload": json.dumps(response_payload, default=str),
                        "source": source,
                        "model_version": model_version,
                        "prompt_version": prompt_version,
                        "latency_ms": max(0, latency_ms),
                        "cache_minutes": max(1, cache_minutes),
                    },
                )
                .mappings()
                .one()
            )
        return dict(row)
