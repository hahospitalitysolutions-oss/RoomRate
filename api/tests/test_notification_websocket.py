"""Behavioral tests for the /ws/alerts WebSocket endpoint and its auth.

These exercise the security-sensitive handshake with FastAPI dependency
overrides (no real DB, no real listener thread):

  - no credentials                       -> server closes 4401
  - valid one-time ticket                -> accepted, initial "connected" frame
  - invalid/expired/used ticket          -> closed 4401
  - legacy ?token= (JWT in the URL)      -> closed 4401 (path removed)
  - api_key + account_id                 -> accepted
  - api_key WITHOUT account_id           -> closed 4401 (WS isolation rule)
  - api_key + non-UUID account_id        -> closed 4401
  - cross-account delivery isolation     -> a B-scoped socket never sees A's push

Browser clients authenticate with a short-lived one-time ticket minted over
authenticated REST (POST /api/v1/notifications/ws-ticket) so the Supabase JWT
never appears in a WebSocket URL (URLs leak into server/proxy logs and browser
history; a one-time 60s ticket is worthless once redeemed).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.config import settings
from api.dependencies import get_ws_ticket_service
from api.main import app
from api.services.notification_broadcaster import manager


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000002")
API_KEY = "test-internal-key"


class FakeWsTicketService:
    """Redeems from a scripted {raw_ticket: account_id} map, single-use."""

    def __init__(self, tickets: dict[str, UUID] | None = None):
        self.tickets = dict(tickets or {})
        self.redeem_calls: list[str] = []

    def issue(self, account_id: UUID) -> str:
        raise AssertionError("issue() is not part of the WS handshake")

    def redeem(self, ticket: str) -> UUID | None:
        self.redeem_calls.append(ticket)
        return self.tickets.pop(ticket, None)


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test gets a fresh manager registry and clean dependency overrides."""
    manager.active_connections.clear()
    yield
    app.dependency_overrides.clear()
    manager.active_connections.clear()


@pytest.fixture(autouse=True)
def _internal_api_key(monkeypatch):
    """Pin a known internal api key so verify_api_key is deterministic."""
    monkeypatch.setattr(settings, "internal_api_key", API_KEY)


def _override_tickets(tickets: dict[str, UUID] | None = None) -> FakeWsTicketService:
    service = FakeWsTicketService(tickets)
    app.dependency_overrides[get_ws_ticket_service] = lambda: service
    return service


def test_no_credentials_closes_4401():
    _override_tickets()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/alerts"):
            pass  # pragma: no cover - never reached, handshake is rejected

    assert exc_info.value.code == 4401


def test_valid_ticket_is_accepted_and_receives_connected_frame():
    _override_tickets({"good-ticket": ACCOUNT_ID})
    client = TestClient(app)

    with client.websocket_connect("/ws/alerts?ticket=good-ticket") as ws:
        frame = ws.receive_json()

    assert frame["type"] == "connected"


def test_invalid_ticket_closes_4401():
    service = _override_tickets({})  # nothing redeemable
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/alerts?ticket=expired-or-unknown"):
            pass  # pragma: no cover

    assert exc_info.value.code == 4401
    assert service.redeem_calls == ["expired-or-unknown"]


def test_ticket_is_single_use():
    """The same ticket must not open a second socket (replay protection)."""
    _override_tickets({"one-shot": ACCOUNT_ID})
    client = TestClient(app)

    with client.websocket_connect("/ws/alerts?ticket=one-shot") as ws:
        assert ws.receive_json()["type"] == "connected"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/alerts?ticket=one-shot"):
            pass  # pragma: no cover

    assert exc_info.value.code == 4401


def test_legacy_token_query_param_closes_4401():
    """JWTs in WebSocket URLs are no longer an auth path — 4401 regardless."""
    _override_tickets({"good-ticket": ACCOUNT_ID})
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/alerts?token=a.supabase.jwt"):
            pass  # pragma: no cover

    assert exc_info.value.code == 4401


def test_api_key_with_account_id_is_accepted():
    _override_tickets()
    client = TestClient(app)

    url = f"/ws/alerts?api_key={API_KEY}&account_id={ACCOUNT_ID}"
    with client.websocket_connect(url) as ws:
        frame = ws.receive_json()

    assert frame["type"] == "connected"


def test_api_key_without_account_id_closes_4401():
    """WS isolation rule: internal key MUST carry an explicit account_id."""
    _override_tickets()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/alerts?api_key={API_KEY}"):
            pass  # pragma: no cover

    assert exc_info.value.code == 4401


def test_api_key_with_invalid_account_id_closes_4401():
    _override_tickets()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/alerts?api_key={API_KEY}&account_id=not-a-uuid"):
            pass  # pragma: no cover

    assert exc_info.value.code == 4401


def test_invalid_api_key_closes_4401():
    _override_tickets()
    client = TestClient(app)

    url = f"/ws/alerts?api_key=wrong-key&account_id={ACCOUNT_ID}"
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(url):
            pass  # pragma: no cover

    assert exc_info.value.code == 4401


def test_dispatch_to_account_a_is_not_received_by_account_b_socket():
    """End-to-end isolation: a B-scoped live socket never sees A's notification.

    Account A connects over the real WS endpoint; we then dispatch a notification
    for account B via the manager and assert A's socket received nothing extra
    beyond its initial "connected" frame.
    """
    _override_tickets({"ticket-a": ACCOUNT_ID})
    client = TestClient(app)

    with client.websocket_connect("/ws/alerts?ticket=ticket-a") as ws:
        connected = ws.receive_json()
        assert connected["type"] == "connected"

        # Dispatch a notification for the OTHER account; A must not receive it.
        asyncio.run(
            manager.send_to_account(OTHER_ACCOUNT_ID, '{"hello":"B"}')
        )

        # Sending to A's own account proves the socket is live and routing works.
        asyncio.run(
            manager.send_to_account(ACCOUNT_ID, '{"hello":"A"}')
        )
        received = ws.receive_text()

    # The first (and only queued) frame after "connected" is A's own message,
    # never B's — confirming account scoping at the manager level end-to-end.
    assert received == '{"hello":"A"}'
