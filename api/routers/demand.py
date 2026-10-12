"""Outside demand for the owner's destination: ferries and flights by day.

Destination- and airport-level data, reached only through the caller's own
owned property: an account sees the ferries and flights of its own
destinations, nothing else.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_ferry_repository,
    get_flight_repository,
    get_onboarding_repository,
)
from api.repositories.ferry_repository import FerryRepository
from api.repositories.flight_repository import FlightRepository
from api.repositories.onboarding_repository import OnboardingRepository
from api.routers._filters import owned_property_origin
from api.schemas.demand import (
    FerryDayOut,
    FerryDemandResponse,
    FerryRouteOut,
    FlightDayOut,
    FlightDemandResponse,
)
from api.services.ferry_demand_service import daily_levels, history_window
from api.services.flight_demand_service import airport_for, flight_daily_levels, flight_history_window

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
    rows = ferry_repository.observation_history(
        destination, **history_window(today, None, settings.ferry_demand_horizon_days)
    )
    return FerryDemandResponse(
        canonical_destination=destination,
        status=profile["status"],
        island_port=profile.get("island_port"),
        port_distance_km=profile.get("port_distance_km"),
        resolved_at=profile.get("resolved_at"),
        routes=[FerryRouteOut(**route) for route in profile["routes"]],
        days=[FerryDayOut(**day) for day in daily_levels(rows, today) if day["travel_date"] >= today],
        observed_on=max((row["observed_on"] for row in rows), default=None),
    )


@router.get("/flights", response_model=FlightDemandResponse)
def flight_demand(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    flight_repository: FlightRepository = Depends(get_flight_repository),
) -> FlightDemandResponse:
    """The island airport of the property and its collected arrivals by travel date.

    ``status`` "off" without an AeroDataBox key; ``days`` is empty until the
    first daily collection, and each day's level is ``unknown`` until the
    same day has been read twice.
    """
    owned_property = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    destination = owned_property.get("canonical_destination") or ""
    origin = owned_property_origin(owned_property)
    airport = airport_for(*origin) if origin else None
    if airport is None:
        return FlightDemandResponse(canonical_destination=destination, status="no_airport")
    iata, island = airport
    if not settings.flight_demand_active:
        return FlightDemandResponse(canonical_destination=destination, status="off", airport_iata=iata, island=island)

    today = datetime.now(timezone.utc).date()
    max_age = settings.flight_signal_max_age_days
    rows = flight_repository.observation_history(
        iata, **flight_history_window(today, None, settings.flight_demand_horizon_days, max_age)
    )
    days = flight_daily_levels(rows, today, airport_iata=iata, island=island, max_age_days=max_age)
    return FlightDemandResponse(
        canonical_destination=destination,
        status="flights",
        airport_iata=iata,
        island=island,
        days=[FlightDayOut(**day) for day in days if day["travel_date"] >= today],
        observed_on=max((row["observed_on"] for row in rows), default=None),
    )
