"""Unit tests for the PostgreSQL LISTEN/NOTIFY notification broadcaster.

No live database: publish() is exercised against a fake engine/connection that
records the pg_notify call, and the listener's notify-dispatch is exercised
against a fake raw connection exposing a .notifies list. The actual LISTEN loop
(select.select on a real socket) is integration-only and not tested here.
"""

from __future__ import annotations

import json
from uuid import UUID

from api.services import notification_broadcaster as broadcaster_module
from api.services.notification_broadcaster import (
    CHANNEL,
    NotificationBroadcaster,
    NotificationListener,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeConnection:
    def __init__(self):
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))

    def commit(self):
        # publish() always commits so the NOTIFY is delivered (DML autobegin).
        self.commits += 1


class FakeEngineCtx:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *exc):
        return False


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return FakeEngineCtx(self.connection)


def _broadcaster(monkeypatch, connection):
    engine = FakeEngine(connection)
    monkeypatch.setattr(broadcaster_module, "get_engine", lambda role="api": engine)
    return NotificationBroadcaster(), engine


def test_publish_builds_pg_notify_with_channel_and_json(monkeypatch):
    connection = FakeConnection()
    broadcaster, _ = _broadcaster(monkeypatch, connection)

    broadcaster.publish(ACCOUNT_ID, {"id": "abc", "title": "Price drop"})

    assert len(connection.executed) == 1
    sql, params = connection.executed[0]
    assert "pg_notify" in sql
    assert params["channel"] == CHANNEL
    decoded = json.loads(params["payload"])
    assert decoded["account_id"] == str(ACCOUNT_ID)
    assert decoded["notification"]["title"] == "Price drop"
    # The explicit commit is required for the NOTIFY to actually deliver.
    assert connection.commits == 1


def test_publish_oversize_payload_falls_back_to_compact_form(monkeypatch):
    connection = FakeConnection()
    broadcaster, _ = _broadcaster(monkeypatch, connection)

    big_message = "x" * 9000  # exceeds the ~7500-byte NOTIFY budget
    broadcaster.publish(
        ACCOUNT_ID, {"id": "note-123", "title": "T", "message": big_message}
    )

    sql, params = connection.executed[0]
    decoded = json.loads(params["payload"])
    # Compact fallback: only id, client refetches the rest via REST.
    assert decoded["account_id"] == str(ACCOUNT_ID)
    assert decoded["notification"] == {"id": "note-123"}
    assert "message" not in json.dumps(decoded["notification"])


def test_publish_swallows_engine_failure(monkeypatch):
    def boom(role="api"):
        raise RuntimeError("no database")

    monkeypatch.setattr(broadcaster_module, "get_engine", boom)
    broadcaster = NotificationBroadcaster()

    # Must not raise even when the engine is unavailable.
    broadcaster.publish(ACCOUNT_ID, {"id": "abc"})


def test_publish_many_uses_one_connection_and_commit_for_the_batch(monkeypatch):
    connection = FakeConnection()
    broadcaster, engine = _broadcaster(monkeypatch, connection)

    broadcaster.publish_many(
        ACCOUNT_ID,
        [{"id": "n1", "title": "First"}, {"id": "n2", "title": "Second"}],
    )

    # One checkout + one commit, but one pg_notify per payload.
    assert engine.connect_calls == 1
    assert connection.commits == 1
    assert len(connection.executed) == 2
    decoded = [json.loads(params["payload"]) for _, params in connection.executed]
    assert [d["notification"]["id"] for d in decoded] == ["n1", "n2"]
    assert all(d["account_id"] == str(ACCOUNT_ID) for d in decoded)
    assert all(params["channel"] == CHANNEL for _, params in connection.executed)


def test_publish_many_applies_oversize_fallback_per_payload(monkeypatch):
    connection = FakeConnection()
    broadcaster, _ = _broadcaster(monkeypatch, connection)

    broadcaster.publish_many(
        ACCOUNT_ID,
        [
            {"id": "small", "title": "Fits"},
            {"id": "big", "title": "T", "message": "x" * 9000},
        ],
    )

    decoded = [json.loads(params["payload"]) for _, params in connection.executed]
    # The small payload ships in full; only the oversize one collapses to {id}.
    assert decoded[0]["notification"]["title"] == "Fits"
    assert decoded[1]["notification"] == {"id": "big"}


def test_publish_many_with_empty_batch_never_touches_the_engine(monkeypatch):
    def boom(role="api"):
        raise AssertionError("empty batch must not open a connection")

    monkeypatch.setattr(broadcaster_module, "get_engine", boom)

    NotificationBroadcaster().publish_many(ACCOUNT_ID, [])


def test_publish_many_swallows_engine_failure(monkeypatch):
    def boom(role="api"):
        raise RuntimeError("no database")

    monkeypatch.setattr(broadcaster_module, "get_engine", boom)

    # Must not raise even when the engine is unavailable.
    NotificationBroadcaster().publish_many(ACCOUNT_ID, [{"id": "abc"}])


# ---------------------------------------------------------------------------
# Listener notify-dispatch (fake raw connection)
# ---------------------------------------------------------------------------


class FakeNotify:
    def __init__(self, payload: str):
        self.payload = payload


class FakeManager:
    def __init__(self):
        self.sent: list[tuple[UUID, str]] = []

    async def send_to_account(self, account_id, text):
        self.sent.append((account_id, text))


def test_dispatch_routes_notify_payload_to_account_manager():
    manager = FakeManager()
    listener = NotificationListener(manager)
    dispatched: list = []
    # Capture what would be scheduled onto the loop instead of running a loop.
    listener._run_on_loop = lambda coro: dispatched.append(coro)

    payload = json.dumps(
        {"account_id": str(ACCOUNT_ID), "notification": {"id": "n1", "title": "Hi"}}
    )
    listener._dispatch_notify(FakeNotify(payload))

    assert len(dispatched) == 1
    # The coroutine targets the right account with the original JSON text.
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(dispatched[0])
    finally:
        loop.close()
    assert manager.sent[0][0] == ACCOUNT_ID
    assert json.loads(manager.sent[0][1])["notification"]["title"] == "Hi"


def test_dispatch_ignores_malformed_payload():
    manager = FakeManager()
    listener = NotificationListener(manager)
    dispatched: list = []
    listener._run_on_loop = lambda coro: dispatched.append(coro)

    # Not JSON -> swallowed, nothing dispatched.
    listener._dispatch_notify(FakeNotify("not-json"))
    # Missing account_id -> swallowed too.
    listener._dispatch_notify(FakeNotify(json.dumps({"notification": {}})))

    assert dispatched == []


# ---------------------------------------------------------------------------
# Listener stop() close-race (fake thread/connection)
# ---------------------------------------------------------------------------


class FakeThread:
    def __init__(self, alive_after_join: bool):
        self._alive_after_join = alive_after_join
        self.join_calls: list[float | None] = []

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def is_alive(self):
        return self._alive_after_join


class FakeListenConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_stop_closes_connection_when_thread_exits():
    """Happy path: the loop exited, so the connection is closed normally."""
    listener = NotificationListener(FakeManager())
    thread = FakeThread(alive_after_join=False)
    connection = FakeListenConnection()
    listener._thread = thread
    listener._connection = connection

    listener.stop()

    assert listener._stop_event.is_set()
    assert thread.join_calls == [5.0]
    assert connection.closed is True
    assert listener._thread is None
    assert listener._connection is None


def test_stop_skips_close_and_warns_when_join_times_out(caplog):
    """Join timed out (thread still alive): SKIP close to avoid the libpq race."""
    listener = NotificationListener(FakeManager())
    thread = FakeThread(alive_after_join=True)
    connection = FakeListenConnection()
    listener._thread = thread
    listener._connection = connection

    with caplog.at_level("WARNING", logger="api.services.notification_broadcaster"):
        listener.stop()

    # The connection must NOT be closed from this thread while the listener
    # thread may still be reading it.
    assert connection.closed is False
    # A warning explains the leaked connection.
    assert any(
        "did not exit within the stop timeout" in record.getMessage()
        for record in caplog.records
    )
    # References are cleared even though we leaked the connection.
    assert listener._thread is None
    assert listener._connection is None
