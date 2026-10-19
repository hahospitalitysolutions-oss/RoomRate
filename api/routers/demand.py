"""Outside demand for the owner's destination: ferries and flights by day.

Destination- and airport-level data, reached only through the caller's own
owned property: an account sees the ferries and flights of its own
destinations, nothing else.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_demand_settings_repository,
    get_ferry_repository,
    get_flight_repository,
    get_onboarding_repository,
)
from api.repositories.demand_settings_repository import DemandSettingsRepository
from api.repositories.ferry_repository import FerryRepository
from api.repositories.flight_repository import FlightRepository
from api.repositories.onboarding_repository import OnboardingRepository
from api.routers._filters import owned_property_origin
from api.schemas.demand import (
    DemandCollectOut,
    DemandRunOut,
    DemandSettingsIn,
    DemandSettingsOut,
    FerryDayOut,
    FerryDemandResponse,
    FerryRouteOut,
    FlightDayOut,
    FlightDemandResponse,
)
from api.services.demand_schedule import (
    FLIGHTS_SCOPE,
    demand_availability,
    greek_now,
    next_collection_at,
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
    settings_repository: DemandSettingsRepository = Depends(get_demand_settings_repository),
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

    if not settings_repository.get(account.account_id, owned_property_id)["ferries_enabled"]:
        return FerryDemandResponse(canonical_destination=destination, enabled=False)
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
    settings_repository: DemandSettingsRepository = Depends(get_demand_settings_repository),
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
    if not settings_repository.get(account.account_id, owned_property_id)["flights_enabled"]:
        return FlightDemandResponse(
            canonical_destination=destination, status="flights", enabled=False, airport_iata=iata, island=island
        )

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


def _own_property(onboarding_repository: OnboardingRepository, account: AccountContext, owned_property_id: UUID) -> dict:
    owned_property = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    return owned_property


def _run_out(run: dict[str, Any] | None) -> DemandRunOut | None:
    return DemandRunOut(**{key: run[key] for key in ("started_at", "finished_at", "saved")}) if run else None


def _settings_view(
    repository: DemandSettingsRepository, account: AccountContext, owned_property: dict, stored: dict | None = None
) -> DemandSettingsOut:
    """The stored settings with what they apply to, the last runs and the next one."""
    owned_property_id = owned_property["id"]
    stored = stored if stored is not None else repository.get(account.account_id, owned_property_id)
    origin = owned_property_origin(owned_property)
    island, airport = demand_availability(*origin) if origin else (None, None)
    destination = owned_property.get("canonical_destination") or ""
    ferries_available = settings.ferry_demand_enabled and island is not None and bool(destination)
    flights_available = settings.flight_demand_active and airport is not None
    ferries_last = repository.last_run("ferries", destination) if ferries_available else None
    flights_last = repository.last_run("flights", FLIGHTS_SCOPE) if flights_available else None
    # The runs this property's collection is made of: the kinds it has on.
    relevant = [
        run
        for run, on in (
            (ferries_last, stored["ferries_enabled"] and ferries_available),
            (flights_last, stored["flights_enabled"] and flights_available),
        )
        if on
    ]
    now_local = greek_now()
    requested_at = stored.get("collect_requested_at")
    pending = bool(relevant) and requested_at is not None and any(
        run is None or requested_at > run["started_at"] for run in relevant
    )
    ran_today = bool(relevant) and all(run is not None and run["run_on"] == now_local.date() for run in relevant)
    return DemandSettingsOut(
        owned_property_id=owned_property_id,
        ferries_enabled=stored["ferries_enabled"],
        flights_enabled=stored["flights_enabled"],
        hour_local=stored["hour_local"],
        island=island,
        airport_iata=airport,
        ferries_available=ferries_available,
        flights_available=flights_available,
        collect_requested_at=requested_at,
        collection_pending=pending,
        ferries_last_run=_run_out(ferries_last),
        flights_last_run=_run_out(flights_last),
        next_collection_at=next_collection_at(stored["hour_local"], ran_today, now_local) if relevant else None,
    )


@router.get("/settings", response_model=DemandSettingsOut)
def get_demand_settings(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: DemandSettingsRepository = Depends(get_demand_settings_repository),
) -> DemandSettingsOut:
    """Whether ferries and flights are read for the property's island, and at which Greek hour."""
    owned_property = _own_property(onboarding_repository, account, owned_property_id)
    return _settings_view(repository, account, owned_property)


def _request_collection(
    repository: DemandSettingsRepository, account: AccountContext, owned_property: dict, stored: dict
) -> DemandCollectOut:
    """Ask the worker for a collection now, unless today's already saved data."""
    view = _settings_view(repository, account, owned_property, stored)
    kinds = [
        (kind, run)
        for kind, run, on in (
            ("ferries", view.ferries_last_run, view.ferries_enabled and view.ferries_available),
            ("flights", view.flights_last_run, view.flights_enabled and view.flights_available),
        )
        if on
    ]
    if not kinds:
        return DemandCollectOut(
            queued=False,
            message="Δεν υπάρχει ενεργή συλλογή πλοίων ή πτήσεων για αυτό το κατάλυμα.",
            settings=view,
        )
    today = greek_now().date()
    done_today = [
        run for _, run in kinds if run is not None and run.started_at.astimezone(greek_now().tzinfo).date() == today
    ]
    if len(done_today) == len(kinds) and all(run.finished_at is None or (run.saved or 0) > 0 for run in done_today):
        if any(run.finished_at is None for run in done_today):
            return DemandCollectOut(queued=False, message="Η συλλογή τρέχει αυτή τη στιγμή.", settings=view)
        at = max(run.started_at for run in done_today).astimezone(greek_now().tzinfo)
        return DemandCollectOut(
            queued=False,
            message=f"Η συλλογή έγινε σήμερα στις {at:%H:%M}. Η επόμενη αύριο στις {view.hour_local:02d}:00.",
            settings=view,
        )
    # A run of today that saved nothing (server down) may be tried again.
    destination = owned_property.get("canonical_destination") or ""
    for kind, _ in kinds:
        repository.release_failed_run(kind, destination if kind == "ferries" else FLIGHTS_SCOPE, today)
    stored = repository.save(
        account.account_id,
        owned_property["id"],
        ferries_enabled=stored["ferries_enabled"],
        flights_enabled=stored["flights_enabled"],
        hour_local=stored["hour_local"],
        request_collection=True,
    )
    return DemandCollectOut(
        queued=True,
        message="Η συλλογή ξεκινά μέσα σε λίγα λεπτά.",
        settings=_settings_view(repository, account, owned_property, stored),
    )


@router.put("/settings", response_model=DemandSettingsOut)
def put_demand_settings(
    owned_property_id: UUID,
    body: DemandSettingsIn,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: DemandSettingsRepository = Depends(get_demand_settings_repository),
) -> DemandSettingsOut:
    """Save the owner's choice; ``collect_now`` also asks for a collection within minutes."""
    owned_property = _own_property(onboarding_repository, account, owned_property_id)
    stored = repository.save(
        account.account_id,
        owned_property_id,
        ferries_enabled=body.ferries_enabled,
        flights_enabled=body.flights_enabled,
        hour_local=body.hour_local,
    )
    if body.collect_now:
        return _request_collection(repository, account, owned_property, stored).settings
    return _settings_view(repository, account, owned_property, stored)


@router.post("/collect", response_model=DemandCollectOut)
def collect_demand_now(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: DemandSettingsRepository = Depends(get_demand_settings_repository),
) -> DemandCollectOut:
    """«Συλλογή τώρα»: the worker's next tick (within minutes) collects; once a day."""
    owned_property = _own_property(onboarding_repository, account, owned_property_id)
    return _request_collection(repository, account, owned_property, repository.get(account.account_id, owned_property_id))
