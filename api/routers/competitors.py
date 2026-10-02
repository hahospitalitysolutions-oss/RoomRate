import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_room_match_repository,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.room_match_repository import RoomMatchRepository
from api.routers._filters import build_agent_matches, build_filters, build_origin, load_agent_matches
from api.schemas.market import Competitor, OwnedRoomReference
from api.services.market_service import MarketService, RoomRateFilters

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=list[Competitor])
async def list_competitors(
    filters: RoomRateFilters = Depends(build_filters),
    match_room: bool = Query(default=False, description="Score packages against the owned room."),
    min_match_score: float | None = Query(default=None, ge=0, le=100),
    sort: Literal["price", "match", "distance"] = Query(default="price"),
    origin: tuple[float, float] | None = Depends(build_origin),
    account: AccountContext = Depends(get_account_context),
    service: MarketService = Depends(get_market_service),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    match_repository: RoomMatchRepository = Depends(get_room_match_repository),
) -> list[Competitor]:
    """Return competitors grouped by hotel with room package details.

    With ``match_room=true`` every package gets a 0-100 ``match_score`` against
    the owned property's selected room type; ``min_match_score`` filters and
    ``sort=match`` ranks by best match instead of price. With
    ``owned_property_id`` every competitor carries ``distance_km`` and
    ``sort=distance`` (which requires it) ranks nearest first.

    Agents (spec 2026-09-29 Α.4): when AI match rows exist for
    (``scrape_job_id``, selected room), those packages take the agent's score
    and Greek ``match_reasoning``, the rest keep the statistical score with a
    null reasoning, and each competitor reports ``match_source``
    "agent"|"statistical". The 0-100 scale, ``min_match_score`` and
    ``sort=match`` behave identically over both sources.

    Comparison set (owner decision 2026-09-30): with agent rows for
    (``scrape_job_id``, ``owned_room_type_id`` or the selected room) the
    agent's ``comparable`` verdicts choose the packages — hidden by default,
    flagged with ``comparable_only=false`` — with or without ``match_room``;
    without them the same-category pool is the fallback.
    """
    if sort == "distance" and not filters.owned_property_id:
        raise HTTPException(status_code=400, detail="sort=distance requires owned_property_id")
    sort_by_distance = sort == "distance"
    if not match_room:
        if min_match_score is not None:
            raise HTTPException(status_code=400, detail="min_match_score requires match_room=true")
        if sort == "match":
            raise HTTPException(status_code=400, detail="sort=match requires match_room=true")
        return service.get_competitors(
            filters,
            origin=origin,
            sort_by_distance=sort_by_distance,
            agent_matches=build_agent_matches(
                filters, account, onboarding_repository, match_repository
            ),
        )

    if not filters.owned_property_id:
        raise HTTPException(status_code=400, detail="owned_property_id is required when match_room=true")
    if filters.owned_room_type_id is not None:
        # The reference room the caller named (e.g. after «Επανεκτίμηση» for
        # a non-selected room); account-scoped, so a foreign id is a 404.
        selected = onboarding_repository.get_owned_room_type(
            account.account_id, filters.owned_room_type_id
        )
        if selected is None:
            raise HTTPException(status_code=404, detail="Owned room type not found")
    else:
        selected = onboarding_repository.get_selected_room_type(
            account.account_id, filters.owned_property_id
        )
        if selected is None:
            raise HTTPException(status_code=404, detail="No selected room type found for this property")

    owned_room = OwnedRoomReference(
        room_type=str(selected["room_type"]),
        room_type_category=selected.get("room_type_category"),
        # room_attributes stays None: MarketService extracts attributes from
        # room_type itself, identically to the dict round-trip this replaced.
    )
    return service.get_competitors(
        filters,
        owned_room=owned_room,
        min_match_score=min_match_score,
        sort_by_match=sort == "match",
        origin=origin,
        sort_by_distance=sort_by_distance,
        agent_matches=load_agent_matches(match_repository, account, filters, selected.get("id")),
    )
