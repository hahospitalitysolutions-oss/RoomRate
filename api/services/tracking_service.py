from __future__ import annotations

import uuid
from typing import Protocol

from api.schemas.tracking import (
    TrackedCompetitorInput,
    TrackedCompetitorListResponse,
    TrackedCompetitorSetRequest,
    TrackedCompetitorSetResponse,
)


class TrackingRepositoryProtocol(Protocol):
    def add_tracked_competitors(
        self,
        account_id: uuid.UUID,
        request: TrackedCompetitorSetRequest,
    ) -> int:
        """Add tracked competitors without removing existing selections."""

    def replace_tracked_competitors(
        self,
        account_id: uuid.UUID,
        request: TrackedCompetitorSetRequest,
    ) -> int:
        """Replace tracked competitors for a scope."""

    def list_tracked_competitors(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        room_type_category: str,
    ) -> list[dict]:
        """Return active tracked competitors for a scope."""


class TrackingService:
    """Application service for competitor checkbox selections."""

    def __init__(self, repository: TrackingRepositoryProtocol):
        self.repository = repository

    def add_tracked_competitors(
        self,
        account_id: uuid.UUID,
        request: TrackedCompetitorSetRequest,
    ) -> TrackedCompetitorSetResponse:
        """Add selected competitors to the existing tracked set."""
        saved_count = self.repository.add_tracked_competitors(account_id, request)
        return TrackedCompetitorSetResponse(
            owned_property_id=request.owned_property_id,
            room_type_category=request.room_type_category,
            saved_count=saved_count,
        )

    def replace_tracked_competitors(
        self,
        account_id: uuid.UUID,
        request: TrackedCompetitorSetRequest,
    ) -> TrackedCompetitorSetResponse:
        """Persist the selected competitor set for one room category."""
        saved_count = self.repository.replace_tracked_competitors(account_id, request)
        return TrackedCompetitorSetResponse(
            owned_property_id=request.owned_property_id,
            room_type_category=request.room_type_category,
            saved_count=saved_count,
        )

    def list_tracked_competitors(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        room_type_category: str,
    ) -> TrackedCompetitorListResponse:
        """Return selected competitor checkboxes for one room category.

        ``room_type_category`` must already be the normalized slug key; the
        router normalizes it via ``normalize_room_type_category_key``.
        """
        competitors = [
            TrackedCompetitorInput.model_validate(row)
            for row in self.repository.list_tracked_competitors(
                account_id=account_id,
                owned_property_id=owned_property_id,
                room_type_category=room_type_category,
            )
        ]
        return TrackedCompetitorListResponse(
            owned_property_id=owned_property_id,
            room_type_category=room_type_category,
            competitors=competitors,
        )
