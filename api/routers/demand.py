"""Outside demand for the owner's destination: ferry routes and sailings by day.

Destination-level data, reached only through the caller's own owned
property: an account sees the ferries of its own destinations, nothing else.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_ferry_repository,
    get_onboarding_repository,
)
from api.repositories.ferry_repository import FerryRepository
from api.repositories.onboarding_repository import OnboardingRepository
from api.schemas.demand import FerryDayOut, FerryDemandResponse, FerryRouteOut
from api.services.ferry_demand_service import SIGNAL_MAX_AGE_DAYS, daily_levels

router = APIRouter()


@router.get("/ferries", response_model=FerryDemandResponse)
def ferry_demand(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    ferry_repository: FerryRepository = Depends(get_ferry_repository),
) -> FerryDemandResponse:
    """The island port, the inbound routes and the next days' sailings of the property's destination.

    ``status`` is None until the worker's first port lookup (within the hour
    after a property is claimed); ``days`` is empty until the first daily
    collection.
    """
    owned_property = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    destination = owned_property.get("canonical_destination")
    if not destination:
        raise HTTPException(status_code=404, detail="Owned property has no canonical destination")

    profile = ferry_repository.get_profile(destination)
    if profile is None:
        return FerryDemandResponse(canonical_destination=destination)

    today = datetime.now(timezone.utc).date()
    rows = ferry_repository.latest_observations(
        destination,
        date_from=today,
        date_to=today + timedelta(days=settings.ferry_demand_horizon_days),
        observed_since=today - timedelta(days=SIGNAL_MAX_AGE_DAYS),
    )
    return FerryDemandResponse(
        canonical_destination=destination,
        status=profile["status"],
        island_port=profile.get("island_port"),
        port_distance_km=profile.get("port_distance_km"),
        resolved_at=profile.get("resolved_at"),
        routes=[FerryRouteOut(**route) for route in profile["routes"]],
        days=[FerryDayOut(**day) for day in daily_levels(rows, today)],
        observed_on=max((row["observed_on"] for row in rows), default=None),
    )
