from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.dependencies import AccountContext, get_account_context, get_tracking_service
from api.schemas.tracking import TrackedCompetitorListResponse, TrackedCompetitorSetRequest, TrackedCompetitorSetResponse
from api.services.room_rates_normalizer import normalize_room_type_category_key
from api.services.tracking_service import TrackingService

router = APIRouter()


@router.post("/competitors", response_model=TrackedCompetitorSetResponse)
async def add_tracked_competitors(
    request: TrackedCompetitorSetRequest,
    account: AccountContext = Depends(get_account_context),
    service: TrackingService = Depends(get_tracking_service),
) -> TrackedCompetitorSetResponse:
    """Add checkbox-selected competitors without removing existing tracked rows."""
    return service.add_tracked_competitors(account.account_id, request)


@router.put("/competitors", response_model=TrackedCompetitorSetResponse)
async def replace_tracked_competitors(
    request: TrackedCompetitorSetRequest,
    account: AccountContext = Depends(get_account_context),
    service: TrackingService = Depends(get_tracking_service),
) -> TrackedCompetitorSetResponse:
    """Save the checkbox-selected competitor set for the current account."""
    return service.replace_tracked_competitors(account.account_id, request)


@router.get("/competitors", response_model=TrackedCompetitorListResponse)
async def list_tracked_competitors(
    owned_property_id: UUID = Query(...),
    room_type_category: str = Query(..., min_length=1, max_length=50),
    account: AccountContext = Depends(get_account_context),
    service: TrackingService = Depends(get_tracking_service),
) -> TrackedCompetitorListResponse:
    """Return saved checkbox selections for the current property and room category."""
    # "or """: whitespace-only input keeps its historical empty-string form so
    # the response model (room_type_category: str) stays valid.
    normalized_room_type_category = normalize_room_type_category_key(room_type_category) or ""
    return service.list_tracked_competitors(
        account.account_id,
        owned_property_id,
        normalized_room_type_category,
    )
