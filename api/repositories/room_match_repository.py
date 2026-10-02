"""Persistence for room-matching agent results and the per-run audit/quota.

Spec 2026-09-29 Α.3. Match rows are only ever written as a full replacement
of one ``(scrape_job_id, owned_room_type_id)`` scope — DELETE + INSERT inside
one transaction — so readers never observe a half-replaced set. The agent-run
rows are the light audit trail («γιατί δεν βγήκε AI εκτίμηση») and the basis
of the daily per-account run quota.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from api.db import get_engine

ROOM_MATCHING_KIND = "room_matching"


class RoomMatchRepository:
    """Account-scoped repository for AI room matches and agent-run audits."""

    def count_runs_today(self, account_id: uuid.UUID) -> int:
        """Count matching-agent runs recorded for this account today (UTC).

        ``skipped`` audit rows are excluded: a skip makes no LLM call, and
        counting it would let quota/already-matched skips burn the quota.
        """
        with get_engine(role="api").connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM roomrate_agent_runs
                    WHERE account_id = :account_id
                      AND kind = 'room_matching'
                      AND status <> 'skipped'
                      AND created_at >= (
                          date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                      )
                    """
                ),
                {"account_id": account_id},
            ).scalar()
        return int(value or 0)

    def has_matches(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
    ) -> bool:
        """Whether agent rows already exist for this (job, owned room) scope.

        The automatic post-scrape trigger uses this for idempotency (spec
        Α.1): an already-scored job is never re-billed automatically.
        """
        with get_engine(role="api").connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT 1
                    FROM roomrate_room_matches
                    WHERE account_id = :account_id
                      AND scrape_job_id = :scrape_job_id
                      AND owned_room_type_id = :owned_room_type_id
                    LIMIT 1
                    """
                ),
                {
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "owned_room_type_id": owned_room_type_id,
                },
            ).scalar()
        return value is not None

    def replace_matches(
        self,
        *,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
        model_version: str,
        matches: list[dict[str, Any]],
    ) -> int:
        """Replace every match row of one (job, owned room) scope atomically.

        ``matches`` rows carry ``property_id``, ``room_type``, ``score``,
        ``category_match``, ``reasoning`` and ``comparable`` (the agent's
        substitute verdict; a row without one is stored NULL and readers
        fall back to ``score >= 50``). An empty list simply clears the
        scope (an honest zero-match rerun falls back to the statistical
        scores on the read side). Returns the number of rows written.
        """
        with get_engine(role="api").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_room_matches
                    WHERE account_id = :account_id
                      AND scrape_job_id = :scrape_job_id
                      AND owned_room_type_id = :owned_room_type_id
                    """
                ),
                {
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "owned_room_type_id": owned_room_type_id,
                },
            )
            if not matches:
                return 0
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_room_matches (
                        id, account_id, scrape_job_id, owned_room_type_id,
                        property_id, room_type, score, category_match,
                        reasoning, comparable, model_version
                    )
                    VALUES (
                        :id, :account_id, :scrape_job_id, :owned_room_type_id,
                        :property_id, :room_type, :score, :category_match,
                        :reasoning, :comparable, :model_version
                    )
                    """
                ),
                [
                    {
                        "id": uuid.uuid4(),
                        "account_id": account_id,
                        "scrape_job_id": scrape_job_id,
                        "owned_room_type_id": owned_room_type_id,
                        "property_id": match["property_id"],
                        "room_type": match["room_type"],
                        "score": match["score"],
                        "category_match": match["category_match"],
                        "reasoning": match.get("reasoning"),
                        "comparable": match.get("comparable"),
                        "model_version": model_version,
                    }
                    for match in matches
                ],
            )
        return len(matches)

    def insert_run(
        self,
        *,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID | None,
        status: str,
        model: str,
        error_message: str | None = None,
    ) -> None:
        """Record one agent execution (status ``ok``, ``error`` or ``skipped``)."""
        with get_engine(role="api").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_agent_runs (
                        id, account_id, scrape_job_id, kind, status, model,
                        error_message
                    )
                    VALUES (
                        :id, :account_id, :scrape_job_id, 'room_matching',
                        :status, :model, :error_message
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "status": status,
                    "model": model,
                    "error_message": error_message,
                },
            )

    def fetch_matches(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Return the agent match rows for one (job, owned room) scope."""
        with get_engine(role="api").connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT property_id, room_type, score, category_match,
                               reasoning, comparable, model_version
                        FROM roomrate_room_matches
                        WHERE account_id = :account_id
                          AND scrape_job_id = :scrape_job_id
                          AND owned_room_type_id = :owned_room_type_id
                        """
                    ),
                    {
                        "account_id": account_id,
                        "scrape_job_id": scrape_job_id,
                        "owned_room_type_id": owned_room_type_id,
                    },
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]
