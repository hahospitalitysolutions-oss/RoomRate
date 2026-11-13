"""Persistence for room-matching agent results and the per-run audit/quota.

Spec 2026-09-29 Α.3. Match rows are keyed per ``(scrape_job_id,
owned_room_type_id)``. One matching run scores a job against every owned
room type at once, so its rows are written as a full replacement of each
scored room type's scope — DELETE + INSERT of all of them inside ONE
transaction — and readers never observe a half-replaced set. The agent-run
rows are the light audit trail («γιατί δεν βγήκε AI εκτίμηση») and the basis
of the daily per-account run quota. A lease row (migration 20260930_0028)
names the run scoring a job right now, so a second caller never pays for a
duplicate run; it is keyed on the job plus an anchor room type (the
service picks the same one for every caller of the job).
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

    def matched_room_type_ids(
        self, account_id: uuid.UUID, scrape_job_id: uuid.UUID
    ) -> set[uuid.UUID]:
        """The owned room types that already hold agent rows for this job.

        The automatic run after a search scores only the room types missing
        here (spec Α.1 idempotency): an already-scored room type is never
        re-billed automatically.
        """
        with get_engine(role="api").connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT owned_room_type_id
                    FROM roomrate_room_matches
                    WHERE account_id = :account_id
                      AND scrape_job_id = :scrape_job_id
                    """
                ),
                {"account_id": account_id, "scrape_job_id": scrape_job_id},
            ).mappings().all()
        return {_as_uuid(row["owned_room_type_id"]) for row in rows}

    def replace_job_matches(
        self,
        *,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        model_version: str,
        matches_by_room_type: dict[uuid.UUID, list[dict[str, Any]]],
    ) -> int:
        """Replace the rows of every given owned room type of one job atomically.

        Each key is an owned room type the run scored; its list (possibly
        empty: an honest zero-match result falls back to the statistical
        scores on the read side) replaces that room type's scope. Room
        types not in the mapping keep their rows. Rows carry
        ``property_id``, ``room_type``, ``score``, ``category_match``,
        ``reasoning`` and ``comparable`` (a row without a verdict is stored
        NULL and readers fall back to ``score >= 50``). One transaction for
        all room types. Returns the number of rows written.
        """
        if not matches_by_room_type:
            return 0
        with get_engine(role="api").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_room_matches
                    WHERE account_id = :account_id
                      AND scrape_job_id = :scrape_job_id
                      AND owned_room_type_id = ANY(CAST(:owned_room_type_ids AS uuid[]))
                    """
                ),
                {
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "owned_room_type_ids": [str(room_id) for room_id in matches_by_room_type],
                },
            )
            rows = [
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
                for owned_room_type_id, matches in matches_by_room_type.items()
                for match in matches
            ]
            if not rows:
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
                rows,
            )
        return len(rows)

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

    def acquire_lease(
        self,
        *,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
        stale_after_seconds: float,
    ) -> uuid.UUID | None:
        """Claim the in-flight lease of one job (keyed with its anchor room type).

        Returns the claiming run's token, or None while a live run holds the
        job. One statement: a new lease, or the takeover of a stale one
        (older than ``stale_after_seconds``: a run that died without
        releasing it). Two concurrent claims serialize on the primary key,
        so exactly one of them gets a token.
        """
        with get_engine(role="api").begin() as connection:
            holder = connection.execute(
                text(
                    """
                    INSERT INTO roomrate_agent_leases (
                        kind, scrape_job_id, owned_room_type_id, account_id,
                        holder, acquired_at
                    )
                    VALUES (
                        'room_matching', :scrape_job_id, :owned_room_type_id,
                        :account_id, :holder, now()
                    )
                    ON CONFLICT (kind, scrape_job_id, owned_room_type_id) DO UPDATE
                    SET account_id = EXCLUDED.account_id,
                        holder = EXCLUDED.holder,
                        acquired_at = EXCLUDED.acquired_at
                    WHERE roomrate_agent_leases.acquired_at
                          < now() - :stale_after_seconds * interval '1 second'
                    RETURNING holder
                    """
                ),
                {
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "owned_room_type_id": owned_room_type_id,
                    "holder": uuid.uuid4(),
                    "stale_after_seconds": float(stale_after_seconds),
                },
            ).scalar()
        return holder

    def release_lease(
        self,
        *,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
        holder: uuid.UUID,
    ) -> None:
        """Release a lease this run holds; a lease another run took over stays."""
        with get_engine(role="api").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_agent_leases
                    WHERE kind = 'room_matching'
                      AND account_id = :account_id
                      AND scrape_job_id = :scrape_job_id
                      AND owned_room_type_id = :owned_room_type_id
                      AND holder = :holder
                    """
                ),
                {
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "owned_room_type_id": owned_room_type_id,
                    "holder": holder,
                },
            )

    def lease_held(
        self,
        *,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room_type_id: uuid.UUID,
        stale_after_seconds: float,
    ) -> bool:
        """Whether a live (not stale) run is scoring the job (anchor room type key)."""
        with get_engine(role="api").connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT 1
                    FROM roomrate_agent_leases
                    WHERE kind = 'room_matching'
                      AND account_id = :account_id
                      AND scrape_job_id = :scrape_job_id
                      AND owned_room_type_id = :owned_room_type_id
                      AND acquired_at >= now() - :stale_after_seconds * interval '1 second'
                    LIMIT 1
                    """
                ),
                {
                    "account_id": account_id,
                    "scrape_job_id": scrape_job_id,
                    "owned_room_type_id": owned_room_type_id,
                    "stale_after_seconds": float(stale_after_seconds),
                },
            ).scalar()
        return value is not None

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


def _as_uuid(value: Any) -> uuid.UUID:
    """Driver values come back as UUID (psycopg) or str (other drivers/fakes)."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
