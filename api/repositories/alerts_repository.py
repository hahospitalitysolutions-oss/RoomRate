from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine

# Shared SELECT column list for notification feed reads so list/feed shapes match.
NOTIFICATION_COLUMNS = """
    id, account_id, alert_rule_id, notification_type, title, message,
    payload, is_read, created_at
"""

# Shared SELECT column list for alert rules.
RULE_COLUMNS = """
    id, account_id, owned_property_id, rule_type, threshold_pct, direction,
    is_active, created_at, updated_at
"""


class AlertsRepository:
    """PostgreSQL repository for alert rules and the notification feed."""

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

    def list_active_rules(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID | None = None
    ) -> list[dict]:
        """Active rules for an account that apply to the given property.

        A rule with ``owned_property_id IS NULL`` applies to every property; a
        scoped rule only applies to its property. When ``owned_property_id`` is
        None only the account-wide (NULL) rules match.
        """
        with self._engine().connect() as connection:
            rows = (
                connection.execute(
                    text(
                        f"""
                        SELECT {RULE_COLUMNS}
                        FROM roomrate_alert_rules
                        WHERE account_id = :account_id
                          AND is_active = true
                          AND (
                              owned_property_id IS NULL
                              OR owned_property_id = :owned_property_id
                          )
                        ORDER BY created_at ASC
                        """
                    ),
                    {"account_id": account_id, "owned_property_id": owned_property_id},
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def list_rules(self, account_id: uuid.UUID) -> list[dict]:
        """Return every alert rule for one account, newest first."""
        with self._engine().connect() as connection:
            rows = (
                connection.execute(
                    text(
                        f"""
                        SELECT {RULE_COLUMNS}
                        FROM roomrate_alert_rules
                        WHERE account_id = :account_id
                        ORDER BY created_at DESC, id DESC
                        """
                    ),
                    {"account_id": account_id},
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def create_rule(self, account_id: uuid.UUID, values: dict[str, Any]) -> dict | None:
        """Create a rule, rejecting a property owned by another account."""
        rule_id = uuid.uuid4()
        params = {
            "id": rule_id,
            "account_id": account_id,
            "owned_property_id": values.get("owned_property_id"),
            "rule_type": values.get("rule_type", "price_change"),
            "threshold_pct": values.get("threshold_pct", 10.0),
            "direction": values.get("direction", "any"),
            "is_active": values.get("is_active", True),
        }
        with self._engine().begin() as connection:
            row = (
                connection.execute(
                    text(
                        f"""
                        INSERT INTO roomrate_alert_rules (
                            id, account_id, owned_property_id, rule_type,
                            threshold_pct, direction, is_active
                        )
                        SELECT
                            :id, :account_id, :owned_property_id, :rule_type,
                            :threshold_pct, :direction, :is_active
                        WHERE :owned_property_id IS NULL
                           OR EXISTS (
                               SELECT 1
                               FROM roomrate_owned_properties
                               WHERE id = :owned_property_id
                                 AND account_id = :account_id
                           )
                        RETURNING {RULE_COLUMNS}
                        """
                    ),
                    params,
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def update_rule(
        self,
        account_id: uuid.UUID,
        rule_id: uuid.UUID,
        values: dict[str, Any],
    ) -> dict | None:
        """Update selected rule fields with account and property ownership fences."""
        allowed_fields = {"owned_property_id", "threshold_pct", "direction", "is_active"}
        changes = {key: value for key, value in values.items() if key in allowed_fields}
        if not changes:
            return self.get_rule(account_id, rule_id)
        assignments = ", ".join(f"{field} = :{field}" for field in changes)
        params = {"account_id": account_id, "rule_id": rule_id, **changes}
        property_guard = ""
        if "owned_property_id" in changes and changes["owned_property_id"] is not None:
            property_guard = """
                AND EXISTS (
                    SELECT 1
                    FROM roomrate_owned_properties
                    WHERE id = :owned_property_id
                      AND account_id = :account_id
                )
            """
        with self._engine().begin() as connection:
            row = (
                connection.execute(
                    text(
                        f"""
                        UPDATE roomrate_alert_rules
                        SET {assignments}, updated_at = now()
                        WHERE id = :rule_id
                          AND account_id = :account_id
                          {property_guard}
                        RETURNING {RULE_COLUMNS}
                        """
                    ),
                    params,
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def get_rule(self, account_id: uuid.UUID, rule_id: uuid.UUID) -> dict | None:
        """Fetch one rule inside its account boundary."""
        with self._engine().connect() as connection:
            row = (
                connection.execute(
                    text(
                        f"""
                        SELECT {RULE_COLUMNS}
                        FROM roomrate_alert_rules
                        WHERE account_id = :account_id
                          AND id = :rule_id
                        """
                    ),
                    {"account_id": account_id, "rule_id": rule_id},
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def delete_rule(self, account_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
        """Delete one owned rule; notification history remains via SET NULL."""
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_alert_rules
                    WHERE account_id = :account_id
                      AND id = :rule_id
                    """
                ),
                {"account_id": account_id, "rule_id": rule_id},
            )
        return bool(result.rowcount and result.rowcount > 0)

    def insert_notifications(self, rows: list[dict]) -> list[dict]:
        """Bulk-insert notifications; returns the full inserted rows.

        Uses ``unnest`` over parallel arrays so the whole batch is one round
        trip. Empty input is a no-op. ``payload`` is serialized to JSON text and
        cast to JSONB inside SQL (NULL stays NULL).

        Rows carrying an ``event_key`` are idempotent: the partial unique index
        on (account_id, event_key) plus ON CONFLICT DO NOTHING silently drops a
        duplicate of an already-raised alert, and the dropped row is NOT
        returned — so callers never re-publish it to WebSocket clients. Rows
        without an event_key always insert.

        RETURNING must yield the full ``NOTIFICATION_COLUMNS`` set: the service
        serializes these rows for the WebSocket push (pg_notify payload), and a
        narrower column list silently breaks the live push (the publish path
        never raises).
        """
        if not rows:
            return []

        ids = [uuid.uuid4() for _ in rows]
        params: dict[str, Any] = {
            "ids": ids,
            "account_ids": [row["account_id"] for row in rows],
            "alert_rule_ids": [row.get("alert_rule_id") for row in rows],
            "notification_types": [row["notification_type"] for row in rows],
            "titles": [row["title"] for row in rows],
            "messages": [row["message"] for row in rows],
            "payloads": [
                json.dumps(row["payload"]) if row.get("payload") is not None else None
                for row in rows
            ],
            "event_keys": [row.get("event_key") for row in rows],
        }
        with self._engine().begin() as connection:
            returned = (
                connection.execute(
                    text(
                        f"""
                        INSERT INTO roomrate_notifications (
                            id, account_id, alert_rule_id, notification_type,
                            title, message, payload, event_key
                        )
                        SELECT
                            t.id,
                            t.account_id,
                            t.alert_rule_id,
                            t.notification_type,
                            t.title,
                            t.message,
                            CAST(t.payload AS jsonb),
                            t.event_key
                        FROM unnest(
                            CAST(:ids AS uuid[]),
                            CAST(:account_ids AS uuid[]),
                            CAST(:alert_rule_ids AS uuid[]),
                            CAST(:notification_types AS text[]),
                            CAST(:titles AS text[]),
                            CAST(:messages AS text[]),
                            CAST(:payloads AS text[]),
                            CAST(:event_keys AS text[])
                        ) AS t(
                            id, account_id, alert_rule_id, notification_type,
                            title, message, payload, event_key
                        )
                        ON CONFLICT (account_id, event_key)
                            WHERE event_key IS NOT NULL
                            DO NOTHING
                        RETURNING {NOTIFICATION_COLUMNS}
                        """
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in returned]

    def list_notifications(
        self,
        account_id: uuid.UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Newest-first notification feed for one account."""
        unread_filter = "AND is_read = false" if unread_only else ""
        with self._engine().connect() as connection:
            rows = (
                connection.execute(
                    text(
                        f"""
                        SELECT {NOTIFICATION_COLUMNS}
                        FROM roomrate_notifications
                        WHERE account_id = :account_id
                        {unread_filter}
                        ORDER BY created_at DESC, id DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"account_id": account_id, "limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def delete_expired_notifications(self, account_id: uuid.UUID, retention_days: int) -> int:
        """Delete feed rows older than the account retention window."""
        if retention_days <= 0:
            return 0
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_notifications
                    WHERE account_id = :account_id
                      AND created_at < now() - make_interval(days => :retention_days)
                    """
                ),
                {"account_id": account_id, "retention_days": retention_days},
            )
        return result.rowcount if result.rowcount and result.rowcount > 0 else 0

    def count_unread(self, account_id: uuid.UUID) -> int:
        """Count unread notifications for one account."""
        with self._engine().connect() as connection:
            count = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM roomrate_notifications
                    WHERE account_id = :account_id
                      AND is_read = false
                    """
                ),
                {"account_id": account_id},
            ).scalar()
        return int(count) if count is not None else 0

    def mark_read(self, account_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
        """Mark one notification read; fenced on account_id.

        Returns whether a matching row exists for this account (the route 404s
        on False). The UPDATE is NOT additionally fenced on ``is_read = false``
        so re-marking an already-read owned notification is idempotently True
        rather than a spurious 404.
        """
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE roomrate_notifications
                    SET is_read = true
                    WHERE account_id = :account_id
                      AND id = :notification_id
                    """
                ),
                {"account_id": account_id, "notification_id": notification_id},
            )
        return bool(result.rowcount and result.rowcount > 0)

    def mark_all_read(self, account_id: uuid.UUID) -> int:
        """Mark every unread notification read for one account; returns count."""
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE roomrate_notifications
                    SET is_read = true
                    WHERE account_id = :account_id
                      AND is_read = false
                    """
                ),
                {"account_id": account_id},
            )
        return result.rowcount if result.rowcount and result.rowcount > 0 else 0
