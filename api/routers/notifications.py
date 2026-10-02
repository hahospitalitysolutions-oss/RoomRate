from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_notification_service,
    get_ws_ticket_service,
    verify_api_key,
)
from api.schemas.notifications import (
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    MarkAllReadResponse,
    NotificationResponse,
    UnreadCountResponse,
    WsTicketResponse,
)
from api.services.notification_broadcaster import manager
from api.services.price_alert_service import PriceAlertService
from api.services.ws_ticket_service import WS_TICKET_TTL_SECONDS, WsTicketService

logger = logging.getLogger(__name__)


# WebSocket router (no prefix) — keeps the existing /ws/alerts path.
router = APIRouter()

# REST router (mounted under /api/v1/notifications in main.py).
rest_router = APIRouter()


async def _resolve_ws_account(
    websocket: WebSocket,
    ticket_service: WsTicketService,
) -> AccountContext | None:
    """Authenticate a WS handshake from query params. Returns None on failure.

    Accepts ``?ticket=<one-time ticket>`` (browser; minted via the
    authenticated ``POST /api/v1/notifications/ws-ticket`` REST route) or the
    internal ``?api_key=<internal>`` + ``?account_id=<uuid>`` fallback.

    Browsers deliberately do NOT pass their Supabase JWT here: WebSocket URLs
    end up in server/proxy access logs and browser history, and a leaked
    access token stays valid until expiry. A redeemed 60-second one-time
    ticket is worthless the moment this handshake completes.

    WS api_key rule (intentionally STRICTER than HTTP): the internal-key branch
    REQUIRES an explicit, valid ``account_id`` query param. The HTTP path falls
    back to ``settings.roomrate_default_account_id`` when account-id is absent,
    but for a *live notification stream* that silent fallback would subscribe an
    internal caller to the DEFAULT tenant's feed — a tenant-isolation footgun.
    So here, api_key without account_id is rejected (closed 4401). The ticket
    path is unaffected: the ticket itself carries tenant identity.
    """
    ticket = websocket.query_params.get("ticket")
    api_key = websocket.query_params.get("api_key")
    account_id_param = websocket.query_params.get("account_id")
    try:
        if ticket:
            account_id = ticket_service.redeem(ticket)
            if account_id is None:
                logger.info("WS ticket handshake rejected: invalid, expired or reused ticket")
                return None
            return AccountContext(account_id=account_id)

        # Internal api_key branch — reuse the same verifier as HTTP.
        await verify_api_key(api_key)
        # ... but REQUIRE account_id explicitly (no default-tenant fallback).
        if not account_id_param:
            logger.info("WS api_key handshake rejected: missing account_id")
            return None
        try:
            account_id = UUID(account_id_param)
        except ValueError:
            logger.info("WS api_key handshake rejected: account_id is not a UUID")
            return None
        return AccountContext(account_id=account_id)
    except HTTPException:
        return None
    except Exception:
        logger.warning("WebSocket auth raised unexpectedly", exc_info=True)
        return None


@router.websocket("/ws/alerts")
async def alerts_socket(
    websocket: WebSocket,
    ticket_service: WsTicketService = Depends(get_ws_ticket_service),
) -> None:
    """Stream account-scoped notifications to a connected dashboard.

    The socket is authenticated BEFORE accept: an unauthenticated handshake is
    closed with code 4401 and never enters the receive loop. The ticket
    service is injected via ``Depends`` (same DI graph as the REST routes) so
    tests can override it and no per-handshake factory construction happens
    here.
    """
    account = await _resolve_ws_account(websocket, ticket_service)
    if account is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    await manager.connect(account.account_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "message": "RoomRate alerts connected"})
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        logger.info("RoomRate alert WebSocket disconnected")
    finally:
        # Every exit path (client drop during the greeting send, protocol
        # errors, shutdown cancellation) must deregister the socket or the
        # ConnectionManager registry grows for the process lifetime.
        await manager.disconnect(account.account_id, websocket)


@rest_router.post("/ws-ticket", response_model=WsTicketResponse)
async def mint_ws_ticket(
    account: AccountContext = Depends(get_account_context),
    service: WsTicketService = Depends(get_ws_ticket_service),
) -> WsTicketResponse:
    """Mint a one-time short-lived ticket for the /ws/alerts handshake.

    Authenticated exactly like every other REST route (Supabase bearer token
    or internal api key), so the WebSocket URL only ever carries a ticket that
    dies on first use.
    """
    return WsTicketResponse(
        ticket=service.issue(account.account_id),
        expires_in_seconds=WS_TICKET_TTL_SECONDS,
    )


@rest_router.get("/rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> list[AlertRuleResponse]:
    """Return all price-change alert rules owned by the current account."""
    return service.list_rules(account.account_id)


@rest_router.post("/rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    payload: AlertRuleCreate,
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> AlertRuleResponse:
    """Create an account-wide or property-scoped price-change alert rule."""
    rule = service.create_rule(account.account_id, payload.model_dump())
    if rule is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    return rule


@rest_router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: UUID,
    payload: AlertRuleUpdate,
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> AlertRuleResponse:
    """Update an owned alert rule with property ownership validation."""
    rule = service.update_rule(
        account.account_id,
        rule_id,
        payload.model_dump(exclude_unset=True),
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule or owned property not found")
    return rule


@rest_router.delete("/rules/{rule_id}", status_code=204, response_class=Response)
async def delete_alert_rule(
    rule_id: UUID,
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> Response:
    """Delete an owned alert rule while retaining historical notifications."""
    if not service.delete_rule(account.account_id, rule_id):
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return Response(status_code=204)


@rest_router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> list[NotificationResponse]:
    """Return the current account's notification feed, newest first."""
    return service.list_notifications(
        account.account_id, unread_only=unread_only, limit=limit, offset=offset
    )


@rest_router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> UnreadCountResponse:
    """Return the number of unread notifications for the current account."""
    return service.count_unread(account.account_id)


@rest_router.post("/{notification_id}/read")
async def mark_read(
    notification_id: UUID,
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> dict[str, bool]:
    """Mark one notification read; 404 when it is not found or not owned."""
    if not service.mark_read(account.account_id, notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"updated": True}


@rest_router.post("/read-all", response_model=MarkAllReadResponse)
async def mark_all_read(
    account: AccountContext = Depends(get_account_context),
    service: PriceAlertService = Depends(get_notification_service),
) -> MarkAllReadResponse:
    """Mark every unread notification read for the current account."""
    return service.mark_all_read(account.account_id)
