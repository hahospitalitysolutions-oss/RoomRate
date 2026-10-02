"""SQL-shape tests for AlertsRepository using a fake engine/connection.

These mirror the existing raw-SQL repository tests: they do not touch a real
database, they assert the SQL text and bound parameters the repository builds.
get_engine is monkeypatched to return a fake engine whose begin()/connect()
context managers yield a recording connection.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

import pytest

from api.repositories import alerts_repository as alerts_module
from api.repositories.alerts_repository import NOTIFICATION_COLUMNS, AlertsRepository
from api.services.price_alert_service import _serializable_notification


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
NOTIFICATION_ID = UUID("00000000-0000-0000-0000-0000000000c1")
RULE_ID = UUID("00000000-0000-0000-0000-0000000000ab")


class FakeResult:
    def __init__(self, rows=None, scalar=None, rowcount=0):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class FakeConnection:
    def __init__(self, result: FakeResult):
        self.result = result
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))
        return self.result


class FakeEngine:
    def __init__(self, result: FakeResult | None = None, connection=None):
        # Either wrap a scripted result or adopt a caller-provided connection
        # (used by the RETURNING-drift regression test below).
        self.connection = connection if connection is not None else FakeConnection(result)

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        yield self.connection


@pytest.fixture
def patch_engine(monkeypatch):
    def _install(result: FakeResult) -> FakeEngine:
        engine = FakeEngine(result)
        monkeypatch.setattr(alerts_module, "get_engine", lambda role="api": engine)
        return engine

    return _install


def test_list_active_rules_filters_by_account_and_property(patch_engine):
    rows = [{"id": UUID(int=1), "threshold_pct": 10, "direction": "any", "owned_property_id": None}]
    engine = patch_engine(FakeResult(rows=rows))

    result = AlertsRepository().list_active_rules(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert result == rows
    sql, params = engine.connection.executed[0]
    assert "roomrate_alert_rules" in sql
    assert "is_active = true" in sql
    # NULL (all-properties) rules OR rules matching the given property.
    assert "owned_property_id IS NULL" in sql
    assert params["account_id"] == ACCOUNT_ID
    assert params["owned_property_id"] == OWNED_PROPERTY_ID


def test_insert_notifications_empty_is_noop(patch_engine):
    engine = patch_engine(FakeResult(rows=[]))

    out = AlertsRepository().insert_notifications([])

    assert out == []
    # No SQL executed for an empty batch.
    assert engine.connection.executed == []


def test_insert_notifications_returns_rows_and_binds_payload_json(patch_engine):
    returned = [{"id": NOTIFICATION_ID, "created_at": "2026-06-14T00:00:00Z"}]
    engine = patch_engine(FakeResult(rows=returned))

    rows = [
        {
            "account_id": ACCOUNT_ID,
            "alert_rule_id": None,
            "notification_type": "price_change",
            "title": "Price drop: Hotel 10.0%",
            "message": "msg",
            "payload": {"change_pct": -10.0},
        }
    ]
    out = AlertsRepository().insert_notifications(rows)

    assert out == returned
    sql, params = engine.connection.executed[0]
    assert "INSERT INTO roomrate_notifications" in sql
    # RETURNING must carry the full feed column set (WS push serializes it).
    returning_clause = sql.split("RETURNING", 1)[1]
    for column in _notification_column_names():
        assert column in returning_clause
    # payload is bound as a JSON string for JSONB casting.
    assert isinstance(params["payloads"][0], str)
    assert "change_pct" in params["payloads"][0]
    assert params["account_ids"][0] == ACCOUNT_ID


def test_list_notifications_unread_only_and_pagination(patch_engine):
    engine = patch_engine(FakeResult(rows=[]))

    AlertsRepository().list_notifications(ACCOUNT_ID, unread_only=True, limit=10, offset=5)

    sql, params = engine.connection.executed[0]
    assert "is_read = false" in sql
    assert params["limit"] == 10
    assert params["offset"] == 5
    assert params["account_id"] == ACCOUNT_ID


def test_list_notifications_all_when_not_unread_only(patch_engine):
    engine = patch_engine(FakeResult(rows=[]))

    AlertsRepository().list_notifications(ACCOUNT_ID, unread_only=False)

    sql, _ = engine.connection.executed[0]
    assert "is_read = false" not in sql


def test_count_unread_returns_int(patch_engine):
    patch_engine(FakeResult(scalar=7))

    assert AlertsRepository().count_unread(ACCOUNT_ID) == 7


def test_count_unread_none_scalar_is_zero(patch_engine):
    patch_engine(FakeResult(scalar=None))

    assert AlertsRepository().count_unread(ACCOUNT_ID) == 0


def test_mark_read_is_account_fenced_and_returns_changed(patch_engine):
    engine = patch_engine(FakeResult(rowcount=1))

    changed = AlertsRepository().mark_read(ACCOUNT_ID, NOTIFICATION_ID)

    assert changed is True
    sql, params = engine.connection.executed[0]
    assert "UPDATE roomrate_notifications" in sql
    assert "account_id = :account_id" in sql
    assert "id = :notification_id" in sql
    assert params["account_id"] == ACCOUNT_ID
    assert params["notification_id"] == NOTIFICATION_ID


def test_mark_read_returns_false_when_nothing_changed(patch_engine):
    patch_engine(FakeResult(rowcount=0))

    assert AlertsRepository().mark_read(ACCOUNT_ID, NOTIFICATION_ID) is False


def test_mark_all_read_returns_updated_count(patch_engine):
    patch_engine(FakeResult(rowcount=4))

    assert AlertsRepository().mark_all_read(ACCOUNT_ID) == 4


# ---------------------------------------------------------------------------
# Regression: insert RETURNING vs. WebSocket-push serializer drift.
#
# The service builds the pg_notify payload from the rows insert_notifications
# RETURNs. When RETURNING listed only ``id, created_at`` the serializer
# KeyError'ed in production (swallowed by the never-raise publish contract),
# so the live WS push for price-change alerts never fired while the REST feed
# kept working. These tests drive the REAL SQL through a fake connection that
# echoes back ONLY the columns the RETURNING clause names, then assert the
# serializer reproduces every field from them — so they fail if the SQL and
# the serializer ever drift apart again.
# ---------------------------------------------------------------------------


def _notification_column_names() -> list[str]:
    """Column names in the repository's shared NOTIFICATION_COLUMNS list."""
    return [name.strip() for name in NOTIFICATION_COLUMNS.split(",") if name.strip()]


# One sentinel per notification column. Values are deliberately NOT the
# serializer's fallbacks (e.g. is_read=True, not the DB default false) so a
# column missing from RETURNING shows up as a wrong value, not a silent default.
RETURNED_COLUMN_SENTINELS = {
    "id": NOTIFICATION_ID,
    "account_id": ACCOUNT_ID,
    "alert_rule_id": RULE_ID,
    "notification_type": "price_change",
    "title": "Price drop: Hotel 10.0%",
    "message": "msg",
    "payload": {"change_pct": -10.0},
    "is_read": True,
    "created_at": datetime(2026, 6, 14, 12, 30, tzinfo=timezone.utc),
}


class ReturningColumnsConnection:
    """Fake connection whose result rows contain ONLY the RETURNING columns.

    Mimics the real driver: whatever the executed SQL's RETURNING clause
    names is exactly what each mapping row carries. Unknown columns raise
    KeyError so the sentinel table above must be kept in step with the SQL.
    """

    def __init__(self, column_values: dict):
        self.column_values = column_values
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        sql_text = str(sql)
        params = params or {}
        self.executed.append((sql_text, params))
        match = re.search(r"\bRETURNING\b(.+)\Z", sql_text, re.IGNORECASE | re.DOTALL)
        assert match is not None, "expected the INSERT to have a RETURNING clause"
        returned_columns = [c.strip() for c in match.group(1).split(",") if c.strip()]
        batch_size = len(params.get("ids", [None]))
        rows = [
            {column: self.column_values[column] for column in returned_columns}
            for _ in range(batch_size)
        ]
        return FakeResult(rows=rows)


def test_insert_returning_covers_ws_push_serializer(monkeypatch):
    """The rows the real SQL RETURNs must fully feed _serializable_notification."""
    connection = ReturningColumnsConnection(RETURNED_COLUMN_SENTINELS)
    monkeypatch.setattr(
        alerts_module, "get_engine", lambda role="api": FakeEngine(connection=connection)
    )

    inserted = AlertsRepository().insert_notifications(
        [
            {
                "account_id": ACCOUNT_ID,
                "alert_rule_id": RULE_ID,
                "notification_type": "price_change",
                "title": "Price drop: Hotel 10.0%",
                "message": "msg",
                "payload": {"change_pct": -10.0},
            }
        ]
    )

    assert len(inserted) == 1
    # Must not raise, and every serialized field must come from the RETURNING
    # row (sentinels differ from the serializer's fallbacks on purpose).
    serialized = _serializable_notification(inserted[0])
    assert serialized == {
        "id": str(NOTIFICATION_ID),
        "account_id": str(ACCOUNT_ID),
        "alert_rule_id": str(RULE_ID),
        "notification_type": "price_change",
        "title": "Price drop: Hotel 10.0%",
        "message": "msg",
        "payload": {"change_pct": -10.0},
        "is_read": True,
        "created_at": RETURNED_COLUMN_SENTINELS["created_at"].isoformat(),
    }


def test_insert_returning_matches_notification_feed_columns(monkeypatch):
    """RETURNING and the feed SELECT share NOTIFICATION_COLUMNS exactly."""
    connection = ReturningColumnsConnection(RETURNED_COLUMN_SENTINELS)
    monkeypatch.setattr(
        alerts_module, "get_engine", lambda role="api": FakeEngine(connection=connection)
    )

    AlertsRepository().insert_notifications(
        [
            {
                "account_id": ACCOUNT_ID,
                "alert_rule_id": None,
                "notification_type": "price_change",
                "title": "t",
                "message": "m",
                "payload": None,
            }
        ]
    )

    sql, _ = connection.executed[0]
    returning_clause = sql.split("RETURNING", 1)[1]
    returned_columns = [c.strip() for c in returning_clause.split(",") if c.strip()]
    assert returned_columns == _notification_column_names()


# ---------------------------------------------------------------------------
# event_key dedup (idempotent inserts)
# ---------------------------------------------------------------------------


def test_insert_notifications_binds_event_keys_and_skips_conflicts(patch_engine):
    engine = patch_engine(FakeResult(rows=[]))

    AlertsRepository().insert_notifications(
        [
            {
                "account_id": ACCOUNT_ID,
                "alert_rule_id": None,
                "notification_type": "price_change",
                "title": "t",
                "message": "m",
                "payload": {"change_pct": -10.0},
                "event_key": "price_change:default:p1:2026-06-14",
            },
            {
                "account_id": ACCOUNT_ID,
                "alert_rule_id": None,
                "notification_type": "schedule_disabled",
                "title": "t2",
                "message": "m2",
                "payload": None,
                # No event_key: schedule notifications are not deduplicated.
            },
        ]
    )

    sql, params = engine.connection.executed[0]
    assert "event_key" in sql
    # Conflict target names the partial unique index predicate so only rows
    # WITH an event_key are deduplicated; NULL keys always insert.
    normalized_sql = " ".join(sql.split())
    assert (
        "ON CONFLICT (account_id, event_key) WHERE event_key IS NOT NULL DO NOTHING"
        in normalized_sql
    )
    assert params["event_keys"] == ["price_change:default:p1:2026-06-14", None]


def test_insert_notifications_returns_only_actually_inserted_rows(patch_engine):
    """A conflict-skipped duplicate must not be echoed back (or re-published)."""
    engine = patch_engine(FakeResult(rows=[]))  # everything conflicted away

    out = AlertsRepository().insert_notifications(
        [
            {
                "account_id": ACCOUNT_ID,
                "alert_rule_id": None,
                "notification_type": "price_change",
                "title": "t",
                "message": "m",
                "payload": None,
                "event_key": "k",
            }
        ]
    )

    assert out == []
    assert len(engine.connection.executed) == 1
