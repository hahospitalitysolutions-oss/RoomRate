from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_scrape_job_service,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.routers._filters import AgentMatchLookup, build_agent_matches, build_filters, build_origin, owned_property_origin
from api.schemas.market import CompetitorMapMarker, OwnPropertyMapInfo
from api.services.market_helpers import effective_price_per_night
from api.schemas.scrape_jobs import ScrapeJobResponse
from api.services.market_service import MarketService, RoomRateFilters
from api.services.scrape_job_service import RADIUS_SKIPPED_WARNING, ScrapeJobService

router = APIRouter()


def _applied_radius_km(job: ScrapeJobResponse | None) -> float | None:
    """The job's radius, or None when there is no job or the radius was skipped.

    A job that ran without coordinates for the owner records
    ``radius_skipped_no_coordinates`` in ``result_summary.warnings``; drawing
    its circle would suggest a distance filter that never happened.
    """
    if job is None:
        return None
    warnings = (job.result_summary or {}).get("warnings")
    if isinstance(warnings, list) and RADIUS_SKIPPED_WARNING in warnings:
        return None
    return job.radius_km


@router.get("/competitors", response_model=list[CompetitorMapMarker])
async def competitor_map_markers(
    filters: RoomRateFilters = Depends(build_filters),
    origin: tuple[float, float] | None = Depends(build_origin),
    agent_matches: AgentMatchLookup | None = Depends(build_agent_matches),
    service: MarketService = Depends(get_market_service),
) -> list[CompetitorMapMarker]:
    """Return Mapbox-ready competitor markers.

    With ``owned_property_id`` every marker carries ``distance_km`` from the
    owner's coordinates. The comparison set is the competitor list's
    (``comparable_only``, agent verdicts else the same-category pool), and
    each marker shows its hotel's cheapest comparable package.
    """
    return service.get_competitor_map_markers(filters, origin=origin, agent_matches=agent_matches)


@router.get("/own-property", response_model=OwnPropertyMapInfo)
def own_property_map_info(
    owned_property_id: UUID = Query(...),
    scrape_job_id: UUID | None = Query(default=None),
    account: AccountContext = Depends(get_account_context),
    service: MarketService = Depends(get_market_service),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    scrape_job_service: ScrapeJobService = Depends(get_scrape_job_service),
) -> OwnPropertyMapInfo:
    """Return the owner's own property for the map's «Εσείς» marker (Round 6 §3.6).

    ``scrape_job_id`` is optional: the marker exists before the first search.
    With a job, ``radius_km`` comes from it (null when the job reported the
    radius as skipped) and the price/room from the owner's cheapest package
    in the comparable pool of the job's category (else the property's
    selected category), falling back to any category. An unknown job reads
    as no job, so a stale «last search» id never hides the marker. Plain
    ``def``: the reads are blocking database calls.
    """
    owned_property = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")

    job = scrape_job_service.get_job(account.account_id, scrape_job_id) if scrape_job_id else None
    baseline_category = (job.room_type_category if job else None) or owned_property.get(
        "selected_room_type_category"
    )
    own_rate = (
        service.get_own_property_cheapest_rate(
            account.account_id,
            job.id,
            owned_property.get("display_name"),
            baseline_category,
        )
        if job
        else None
    ) or {}
    origin = owned_property_origin(owned_property)
    # The EFFECTIVE price, like-for-like with the competitor markers.
    price = effective_price_per_night(own_rate)
    return OwnPropertyMapInfo(
        display_name=str(owned_property.get("display_name") or ""),
        latitude=origin[0] if origin else None,
        longitude=origin[1] if origin else None,
        radius_km=_applied_radius_km(job),
        price_per_night_eur=round(price, 2) if price is not None else None,
        room_type=own_rate.get("room_type") or None,
        booking_url=owned_property.get("booking_url") or own_rate.get("booking_url") or None,
    )
