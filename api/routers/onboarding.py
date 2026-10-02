from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from api.config import settings
from api.dependencies import AccountContext, get_account_context, get_onboarding_service
from api.schemas.onboarding import (
    AutomaticSetupRequest,
    AutomaticSetupResponse,
    NearbyDestinationsResponse,
    OwnedPropertyOnboardingCreate,
    OwnedPropertyOnboardingResponse,
    OwnedPropertyRoomType,
    PropertyCandidate,
    SelectedRoomTypeRequest,
    SelectedRoomTypeResponse,
)
from api.services.destination_aliases import canonical_destination as canonicalize_destination
from api.services.nearby_destinations import default_nearby_destinations
from api.services.onboarding_service import OnboardingService

router = APIRouter()


def _validate_active_stay(check_in: date, check_out: date) -> None:
    """Reject expired paid-provider searches at the HTTP boundary."""
    if check_out <= check_in:
        raise HTTPException(status_code=422, detail="check_out must be after check_in")
    # Η αναζήτηση Booking απαιτεί ενεργή ή μελλοντική ημερομηνία άφιξης.
    if check_in < date.today():
        raise HTTPException(status_code=422, detail="check_in cannot be in the past")


@router.get("/nearby-destinations", response_model=NearbyDestinationsResponse)
async def nearby_destinations(
    destination: str = Query(..., min_length=1, max_length=255),
    account: AccountContext = Depends(get_account_context),
) -> NearbyDestinationsResponse:
    """Return the default neighbouring areas the map form pre-fills for a destination.

    Static lookup (no provider call); the account dependency keeps the
    route behind the same authentication as the rest of onboarding.
    """
    _ = account
    stripped = destination.strip()
    if not stripped:
        raise HTTPException(status_code=422, detail="destination is required")
    return NearbyDestinationsResponse(
        destination=stripped,
        canonical=canonicalize_destination(stripped),
        nearby=default_nearby_destinations(stripped),
    )


@router.get("/property-candidates", response_model=list[PropertyCandidate])
def property_candidates(
    property_name: str = Query(..., min_length=1, max_length=255),
    location: str = Query(..., min_length=1, max_length=255),
    check_in: date = Query(...),
    check_out: date = Query(...),
    adults: int = Query(default=2, ge=1),
    children: int = Query(default=0, ge=0),
    rooms: int = Query(default=1, ge=1),
    limit: int = Query(default=8, ge=1, le=25),
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> list[PropertyCandidate]:
    """Search Booking candidates for the user's own property, best name match first.

    Results are ordered so ``candidates[0]`` is the best match; a picker UI can
    preselect it instead of re-implementing the match scoring. ``POST
    /auto-setup`` picks that same head of the list.

    Deliberately a plain ``def`` (like agents.price_recommendation): the
    Booking scout is a blocking remote call that can take minutes, so Starlette
    must run it in the threadpool instead of blocking the event loop.
    """
    _ = account
    _validate_active_stay(check_in, check_out)
    try:
        return service.search_property_candidates(
            property_name=property_name,
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/auto-setup",
    response_model=AutomaticSetupResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def automatic_setup(
    request: AutomaticSetupRequest,
    background_tasks: BackgroundTasks,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> AutomaticSetupResponse:
    """Create own property setup directly from signup property name/location.

    Quota refusals (QuotaExceededError) propagate to the app-level handler in
    ``api.main`` and become 429 there. Plain ``def`` on purpose: the candidate
    search inside runs the blocking Booking scout (threadpool, not event loop).
    """
    _validate_active_stay(request.check_in, request.check_out)
    try:
        response = service.automatic_setup(account.account_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if settings.process_role == "all":
        background_tasks.add_task(
            service.scrape_job_service.run_job,
            account.account_id,
            response.discovery_job.id,
        )
    return response


@router.post(
    "/owned-property",
    response_model=OwnedPropertyOnboardingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_owned_property(
    request: OwnedPropertyOnboardingCreate,
    background_tasks: BackgroundTasks,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> OwnedPropertyOnboardingResponse:
    """Save the selected own property and start automatic room discovery.

    Quota refusals (QuotaExceededError) propagate to the app-level handler in
    ``api.main`` and become 429 there.
    """
    _validate_active_stay(request.check_in, request.check_out)
    try:
        response = service.create_owned_property_and_discovery_job(account.account_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if settings.process_role == "all":
        background_tasks.add_task(
            service.scrape_job_service.run_job,
            account.account_id,
            response.discovery_job.id,
        )
    return response


@router.put(
    "/owned-property/{owned_property_id}",
    response_model=OwnedPropertyOnboardingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"description": "Owned property not found"}},
)
async def replace_owned_property(
    owned_property_id: UUID,
    request: OwnedPropertyOnboardingCreate,
    background_tasks: BackgroundTasks,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> OwnedPropertyOnboardingResponse:
    """Point the owned property at a different Booking property.

    Used when the user rejects the auto-matched candidate. Drops the previous
    hotel's room types and baseline, drops tracked competitors when the
    destination changes, and queues a fresh room discovery. The response does
    not report whether competitors were dropped, so re-read the tracked
    competitor list after a replace.

    Quota refusals (QuotaExceededError) propagate to the app-level handler in
    ``api.main`` and become 429 there.
    """
    _validate_active_stay(request.check_in, request.check_out)
    try:
        response = service.replace_owned_property_and_discovery_job(
            account.account_id, owned_property_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    if settings.process_role == "all":
        background_tasks.add_task(
            service.scrape_job_service.run_job,
            account.account_id,
            response.discovery_job.id,
        )
    return response


@router.delete(
    "/owned-property/{owned_property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Owned property not found"}},
)
async def delete_owned_property(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> Response:
    """Delete the owned property and everything scoped to it.

    Room types, tracked competitors, alert rules and pricing audits cascade
    away; scrape jobs survive with a NULL property. Market price history is
    account-scoped and is deliberately kept.
    """
    if not service.delete_owned_property(account.account_id, owned_property_id):
        raise HTTPException(status_code=404, detail="Owned property not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/owned-property/{owned_property_id}/room-types", response_model=list[OwnedPropertyRoomType])
async def owned_property_room_types(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> list[OwnedPropertyRoomType]:
    """Return room types discovered by the automatic onboarding scrape."""
    return service.list_room_types(account.account_id, owned_property_id)


@router.put(
    "/owned-property/{owned_property_id}/selected-room-type",
    response_model=SelectedRoomTypeResponse,
)
async def select_owned_property_room_type(
    owned_property_id: UUID,
    request: SelectedRoomTypeRequest,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> SelectedRoomTypeResponse:
    """Save the selected baseline room category for targeted competitor matching."""
    try:
        return service.select_owned_property_room_type(
            account.account_id,
            owned_property_id,
            request.room_type_category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
