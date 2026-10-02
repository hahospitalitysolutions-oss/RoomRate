from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from api.services.room_rates_normalizer import normalize_room_type_category_key


class TrackedCompetitorInput(BaseModel):
    """One competitor selected from the map sidebar."""

    property_id: UUID
    room_package_id: UUID | None = None


class TrackedCompetitorSetRequest(BaseModel):
    """Replace the tracked competitor set for one property and room category."""

    owned_property_id: UUID
    room_type_category: str = Field(..., min_length=1, max_length=50)
    competitors: list[TrackedCompetitorInput]

    @model_validator(mode="after")
    def normalize_category(self) -> TrackedCompetitorSetRequest:
        normalized_category = normalize_room_type_category_key(self.room_type_category)
        if normalized_category is None:
            raise ValueError("room_type_category is required")
        self.room_type_category = normalized_category
        return self


class TrackedCompetitorSetResponse(BaseModel):
    """Saved tracked competitor set summary."""

    owned_property_id: UUID
    room_type_category: str
    saved_count: int = Field(..., ge=0)


class TrackedCompetitorListResponse(BaseModel):
    """Current tracked competitor set for one property and room category."""

    owned_property_id: UUID
    room_type_category: str
    competitors: list[TrackedCompetitorInput]
