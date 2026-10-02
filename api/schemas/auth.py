from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class CurrentUserResponse(BaseModel):
    """Authenticated user/account context used by browser clients."""

    account_id: UUID
    auth_provider: str | None = None
    auth_subject: str | None = None
    email: str | None = None
    onboarding_complete: bool
    owned_property_id: UUID | None = None
    property_name: str | None = None
    destination: str | None = None
    raw_destination: str | None = None
    canonical_destination: str | None = None
    selected_room_type_category: str | None = None
