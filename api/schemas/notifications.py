from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationResponse(BaseModel):
    """One in-app notification feed row."""

    # from_attributes lets the response validate either a dict (raw SQL row)
    # or an ORM-like object, matching the rest of the API's response models.
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    notification_type: str
    title: str
    message: str
    payload: dict[str, Any] | None = None
    is_read: bool
    created_at: datetime


class AlertRuleCreate(BaseModel):
    """Create one account-scoped competitor price-change rule."""

    owned_property_id: UUID | None = None
    rule_type: Literal["price_change"] = "price_change"
    threshold_pct: float = Field(default=10.0, gt=0, le=100)
    direction: Literal["any", "drop", "rise"] = "any"
    is_active: bool = True


class AlertRuleUpdate(BaseModel):
    """Editable alert-rule fields; omitted values remain unchanged."""

    owned_property_id: UUID | None = None
    threshold_pct: float | None = Field(default=None, gt=0, le=100)
    direction: Literal["any", "drop", "rise"] | None = None
    is_active: bool | None = None


class AlertRuleResponse(BaseModel):
    """Persisted account-scoped alert-rule row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owned_property_id: UUID | None
    rule_type: str
    threshold_pct: float
    direction: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UnreadCountResponse(BaseModel):
    """Number of unread notifications for the current account."""

    count: int = Field(..., ge=0)


class MarkAllReadResponse(BaseModel):
    """How many notifications a mark-all-read request flipped to read."""

    updated: int = Field(..., ge=0)


class WsTicketResponse(BaseModel):
    """A freshly minted one-time WebSocket auth ticket."""

    ticket: str
    expires_in_seconds: int = Field(..., gt=0)
