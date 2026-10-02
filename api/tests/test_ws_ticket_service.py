"""Unit tests for WsTicketService (short-lived one-time WebSocket tickets).

Same style as the raw-SQL repository tests: a fake engine records the SQL text
and bound parameters, no real database is touched. The tests pin the security
properties: the raw ticket never reaches the database (only its SHA-256 hex),
redemption is a single atomic UPDATE fenced on unused+unexpired, and issue()
opportunistically deletes long-expired rows.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from uuid import UUID

import pytest

from api.services import ws_ticket_service as ws_module
from api.services.ws_ticket_service import WS_TICKET_TTL_SECONDS, WsTicketService


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, result: FakeResult):
        self.result = result
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))
        return self.result


class FakeEngine:
    def __init__(self, result: FakeResult | None = None):
        self.connection = FakeConnection(result or FakeResult())

    @contextmanager
    def begin(self):
        yield self.connection


@pytest.fixture
def patch_engine(monkeypatch):
    def _install(result: FakeResult | None = None) -> FakeEngine:
        engine = FakeEngine(result)
        monkeypatch.setattr(ws_module, "get_engine", lambda role="api": engine)
        return engine

    return _install


def test_issue_returns_opaque_token_and_stores_only_its_hash(patch_engine):
    engine = patch_engine()

    ticket = WsTicketService().issue(ACCOUNT_ID)

    # Opaque, URL-safe, high-entropy token for the client.
    assert isinstance(ticket, str)
    assert len(ticket) >= 32
    insert_sql, insert_params = next(
        (sql, params)
        for sql, params in engine.connection.executed
        if "INSERT INTO roomrate_ws_tickets" in sql
    )
    # The raw ticket must never be persisted — only its SHA-256 hex digest.
    assert insert_params["ticket_hash"] == hashlib.sha256(ticket.encode()).hexdigest()
    assert ticket not in str(insert_params.values())
    assert insert_params["account_id"] == ACCOUNT_ID
    assert insert_params["ttl_seconds"] == WS_TICKET_TTL_SECONDS
    assert "now() + make_interval" in insert_sql


def test_issue_tokens_are_unique_per_call(patch_engine):
    patch_engine()
    service = WsTicketService()

    assert service.issue(ACCOUNT_ID) != service.issue(ACCOUNT_ID)


def test_issue_deletes_long_expired_rows_opportunistically(patch_engine):
    engine = patch_engine()

    WsTicketService().issue(ACCOUNT_ID)

    delete_sql = next(
        sql for sql, _ in engine.connection.executed if "DELETE FROM roomrate_ws_tickets" in sql
    )
    # Only long-expired rows go; freshly-expired ones keep their audit value.
    assert "expires_at < now() - make_interval" in delete_sql


def test_redeem_returns_account_id_for_valid_ticket(patch_engine):
    engine = patch_engine(FakeResult(rows=[{"account_id": ACCOUNT_ID}]))

    account_id = WsTicketService().redeem("raw-ticket-value")

    assert account_id == ACCOUNT_ID
    sql, params = engine.connection.executed[0]
    # Atomic one-time redemption: flip used_at only while unused + unexpired.
    assert "UPDATE roomrate_ws_tickets" in sql
    assert "SET used_at = now()" in sql
    assert "used_at IS NULL" in sql
    assert "expires_at > now()" in sql
    assert "RETURNING account_id" in sql
    # Redemption looks up the hash, never the raw ticket.
    assert params["ticket_hash"] == hashlib.sha256(b"raw-ticket-value").hexdigest()


def test_redeem_returns_none_when_no_row_matches(patch_engine):
    patch_engine(FakeResult(rows=[]))

    assert WsTicketService().redeem("unknown-or-expired-or-used") is None


def test_redeem_rejects_blank_ticket_without_touching_the_database(patch_engine):
    engine = patch_engine()

    assert WsTicketService().redeem("") is None
    assert WsTicketService().redeem("   ") is None
    assert engine.connection.executed == []
