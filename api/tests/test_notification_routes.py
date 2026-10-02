"""Route + ConnectionManager tests for the notifications module.

REST routes use FastAPI dependency overrides for account context and the
notification service (a fake PriceAlertService-shaped object). The account-
scoped ConnectionManager is unit-tested directly with fake WebSockets.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context, get_notification_service
from api.main import app
from api.services.notification_broadcaster import ConnectionManager
from api.schemas.notifications import (
    AlertRuleResponse,
    MarkAllReadResponse,
    NotificationResponse,
    UnreadCountResponse,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000002")
NOTIFICATION_ID = UUID("00000000-0000-0000-0000-0000000000c1")
RULE_ID = UUID("00000000-0000-0000-0000-0000000000ab")


def _rule(**overrides) -> AlertRuleResponse:
    data = {
        "id": RULE_ID,
        "owned_property_id": None,
        "rule_type": "price_change",
        "threshold_pct": 10.0,
        "direction": "any",
        "is_active": True,
        "created_at": "2030-01-01T00:00:00Z",
        "updated_at": "2030-01-01T00:00:00Z",
    }
    data.update(overrides)
    return AlertRuleResponse(**data)


def _notification(**overrides) -> NotificationResponse:
    data = {
        "id": NOTIFICATION_ID,
        "notification_type": "price_change",
        "title": "Price drop: Hotel 12.0%",
        "message": "msg",
        "payload": {"change_pct": -12.0},
        "is_read": False,
        "created_at": "2026-06-14T00:00:00Z",
    }
    data.update(overrides)
    return NotificationResponse(**data)


class FakeNotificationService:
    def __init__(self, mark_read_result: bool = True):
        self.list_calls: list[tuple] = []
        self.unread_calls: list[UUID] = []
        self.mark_read_calls: list[tuple] = []
        self.mark_all_calls: list[UUID] = []
        self.mark_read_result = mark_read_result
        self.rule_calls: list[tuple] = []

    def list_notifications(self, account_id, unread_only=False, limit=50, offset=0):
        self.list_calls.append((account_id, unread_only, limit, offset))
        return [_notification()]

    def count_unread(self, account_id):
        self.unread_calls.append(account_id)
        return UnreadCountResponse(count=3)

    def mark_read(self, account_id, notification_id):
        self.mark_read_calls.append((account_id, notification_id))
        return self.mark_read_result

    def mark_all_read(self, account_id):
        self.mark_all_calls.append(account_id)
        return MarkAllReadResponse(updated=2)

    def list_rules(self, account_id):
        self.rule_calls.append(("list", account_id))
        return [_rule()]

    def create_rule(self, account_id, values):
        self.rule_calls.append(("create", account_id, values))
        return _rule(
            threshold_pct=values["threshold_pct"],
            direction=values["direction"],
        )

    def update_rule(self, account_id, rule_id, values):
        self.rule_calls.append(("update", account_id, rule_id, values))
        return _rule(is_active=values.get("is_active", True))

    def delete_rule(self, account_id, rule_id):
        self.rule_calls.append(("delete", account_id, rule_id))
        return True


def _client(service, account_id=ACCOUNT_ID) -> TestClient:
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=account_id)
    app.dependency_overrides[get_notification_service] = lambda: service
    return TestClient(app)


def test_list_notifications_passes_query_params():
    service = FakeNotificationService()
    client = _client(service)

    response = client.get(
        "/api/v1/notifications", params={"unread_only": "true", "limit": 10, "offset": 5}
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == str(NOTIFICATION_ID)
    assert service.list_calls == [(ACCOUNT_ID, True, 10, 5)]

    app.dependency_overrides.clear()


def test_list_notifications_defaults():
    service = FakeNotificationService()
    client = _client(service)

    response = client.get("/api/v1/notifications")

    assert response.status_code == 200
    assert service.list_calls == [(ACCOUNT_ID, False, 50, 0)]

    app.dependency_overrides.clear()


def test_list_notifications_rejects_limit_over_max():
    service = FakeNotificationService()
    client = _client(service)

    response = client.get("/api/v1/notifications", params={"limit": 500})

    assert response.status_code == 422
    assert service.list_calls == []

    app.dependency_overrides.clear()


def test_unread_count_endpoint():
    service = FakeNotificationService()
    client = _client(service)

    response = client.get("/api/v1/notifications/unread-count")

    assert response.status_code == 200
    assert response.json() == {"count": 3}
    assert service.unread_calls == [ACCOUNT_ID]

    app.dependency_overrides.clear()


def test_alert_rule_crud_endpoints_are_account_scoped():
    service = FakeNotificationService()
    client = _client(service)

    listed = client.get("/api/v1/notifications/rules")
    created = client.post(
        "/api/v1/notifications/rules",
        json={
            "rule_type": "price_change",
            "threshold_pct": 12.5,
            "direction": "drop",
            "is_active": True,
        },
    )
    updated = client.put(
        f"/api/v1/notifications/rules/{RULE_ID}",
        json={"is_active": False},
    )
    deleted = client.delete(f"/api/v1/notifications/rules/{RULE_ID}")

    assert listed.status_code == 200
    assert created.status_code == 201
    assert created.json()["threshold_pct"] == 12.5
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert deleted.status_code == 204
    assert all(call[1] == ACCOUNT_ID for call in service.rule_calls)

    app.dependency_overrides.clear()


def test_create_alert_rule_rejects_invalid_threshold():
    service = FakeNotificationService()
    client = _client(service)

    response = client.post(
        "/api/v1/notifications/rules",
        json={
            "rule_type": "price_change",
            "threshold_pct": 0,
            "direction": "any",
            "is_active": True,
        },
    )

    assert response.status_code == 422
    assert service.rule_calls == []

    app.dependency_overrides.clear()


def test_alert_rule_not_found_is_reported_without_cross_account_leak():
    service = FakeNotificationService()
    service.update_rule = lambda account_id, rule_id, values: None
    client = _client(service)

    response = client.put(
        f"/api/v1/notifications/rules/{RULE_ID}",
        json={"is_active": False},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Alert rule or owned property not found"

    app.dependency_overrides.clear()


def test_mark_read_returns_200_when_found():
    service = FakeNotificationService(mark_read_result=True)
    client = _client(service)

    response = client.post(f"/api/v1/notifications/{NOTIFICATION_ID}/read")

    assert response.status_code == 200
    assert service.mark_read_calls == [(ACCOUNT_ID, NOTIFICATION_ID)]

    app.dependency_overrides.clear()


def test_mark_read_returns_404_when_not_found_or_not_owned():
    service = FakeNotificationService(mark_read_result=False)
    client = _client(service)

    response = client.post(f"/api/v1/notifications/{NOTIFICATION_ID}/read")

    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_mark_all_read_returns_updated_count():
    service = FakeNotificationService()
    client = _client(service)

    response = client.post("/api/v1/notifications/read-all")

    assert response.status_code == 200
    assert response.json() == {"updated": 2}
    assert service.mark_all_calls == [ACCOUNT_ID]

    app.dependency_overrides.clear()


def test_account_isolation_uses_overridden_account():
    service = FakeNotificationService()
    client = _client(service, account_id=OTHER_ACCOUNT_ID)

    client.get("/api/v1/notifications")

    assert service.list_calls[0][0] == OTHER_ACCOUNT_ID

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# ConnectionManager (account-scoped)
# ---------------------------------------------------------------------------


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.accepted = False
        self.closed_code: int | None = None

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        self.closed_code = code


def test_connection_manager_send_to_account_only_reaches_that_account():
    manager = ConnectionManager()
    ws_a1 = FakeWebSocket()
    ws_a2 = FakeWebSocket()
    ws_b1 = FakeWebSocket()

    async def scenario():
        await manager.connect(ACCOUNT_ID, ws_a1)
        await manager.connect(ACCOUNT_ID, ws_a2)
        await manager.connect(OTHER_ACCOUNT_ID, ws_b1)
        await manager.send_to_account(ACCOUNT_ID, "hello-A")

    asyncio.run(scenario())

    assert ws_a1.sent == ["hello-A"]
    assert ws_a2.sent == ["hello-A"]
    assert ws_b1.sent == []  # other account never receives it


def test_connection_manager_disconnect_removes_socket_and_cleans_empty_account():
    manager = ConnectionManager()
    ws = FakeWebSocket()

    async def scenario():
        await manager.connect(ACCOUNT_ID, ws)
        await manager.disconnect(ACCOUNT_ID, ws)
        await manager.send_to_account(ACCOUNT_ID, "after-disconnect")

    asyncio.run(scenario())

    assert ws.sent == []
    # The account key is dropped once its last socket disconnects.
    assert ACCOUNT_ID not in manager.active_connections


def test_connection_manager_send_to_unknown_account_is_noop():
    manager = ConnectionManager()

    async def scenario():
        await manager.send_to_account(ACCOUNT_ID, "nobody-listening")

    asyncio.run(scenario())  # must not raise


# ---------------------------------------------------------------------------
# POST /ws-ticket (one-time WebSocket auth ticket)
# ---------------------------------------------------------------------------


class FakeWsTicketIssuer:
    def __init__(self):
        self.issue_calls: list[UUID] = []

    def issue(self, account_id):
        self.issue_calls.append(account_id)
        return "opaque-ws-ticket"

    def redeem(self, ticket):  # pragma: no cover - not used by the REST route
        raise AssertionError("redeem() is not part of the REST mint route")


def test_ws_ticket_endpoint_mints_ticket_for_current_account():
    from api.dependencies import get_ws_ticket_service
    from api.routers.notifications import WS_TICKET_TTL_SECONDS

    issuer = FakeWsTicketIssuer()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_ws_ticket_service] = lambda: issuer
    client = TestClient(app)

    response = client.post("/api/v1/notifications/ws-ticket")

    assert response.status_code == 200
    body = response.json()
    assert body["ticket"] == "opaque-ws-ticket"
    assert body["expires_in_seconds"] == WS_TICKET_TTL_SECONDS
    assert issuer.issue_calls == [ACCOUNT_ID]

    app.dependency_overrides.clear()


def test_ws_ticket_endpoint_requires_authentication():
    from api.dependencies import get_ws_ticket_service

    issuer = FakeWsTicketIssuer()
    app.dependency_overrides[get_ws_ticket_service] = lambda: issuer
    client = TestClient(app)

    # No bearer token and no internal api key: the shared account-context
    # dependency rejects the mint request before any ticket is issued.
    response = client.post("/api/v1/notifications/ws-ticket")

    assert response.status_code == 401
    assert issuer.issue_calls == []

    app.dependency_overrides.clear()
