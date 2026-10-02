from dataclasses import replace
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_price_history_repository,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.price_history_repository import PriceHistoryRepository
from api.routers._filters import AgentMatchLookup, build_agent_matches, build_filters
from api.schemas.agents import PriceHistoryPoint, PriceHistorySeries
from api.schemas.market import MarketSummary
from api.services.destination_aliases import canonical_destination as canonicalize_destination
from api.services.market_helpers import as_optional_float
from api.services.market_service import MarketService, RoomRateFilters
from api.services.own_reference_price import resolve_own_reference_price
from api.services.price_statistics_service import basis_price_rows
from api.services.room_rates_normalizer import normalize_room_type_category_key

router = APIRouter()


@router.get("/summary", response_model=MarketSummary)
async def market_summary(
    filters: RoomRateFilters = Depends(build_filters),
    agent_matches: AgentMatchLookup | None = Depends(build_agent_matches),
    service: MarketService = Depends(get_market_service),
) -> MarketSummary:
    """Return aggregate pricing and demand metrics for the selected market.

    One price per hotel — the map marker's package, the cheapest comparable
    one — over the competitor list's comparison set (``comparable_only``:
    the agent's verdicts, else the same-category pool), so the list, the map
    and the summary never disagree. ``same_category_hotels``/``similar_hotels``
    count the category split of those packages.
    """
    return service.get_market_summary(filters, agent_matches=agent_matches)


@router.get("/amenities", response_model=list[str])
async def available_amenities(
    filters: RoomRateFilters = Depends(build_filters),
    agent_matches: AgentMatchLookup | None = Depends(build_agent_matches),
    service: MarketService = Depends(get_market_service),
) -> list[str]:
    """Return dynamic amenity filter options for the selected market.

    With agent verdicts the comparison set may span every category, so the
    options come from the broad pool the agent judged (a superset — never a
    missing option); without them, from the comparison pool as before.
    """
    if agent_matches:
        filters = replace(filters, include_similar=True)
    return service.get_available_amenities(filters)


def _reference_cancellation_class(
    repository: PriceHistoryRepository,
    onboarding_repository: OnboardingRepository,
    *,
    account_id,
    owned_property_id,
    canonical_destination: str,
    check_in: date,
    check_out: date,
    adults: int,
    children: int,
    rooms: int,
    room_type_category: str | None,
) -> str | None:
    """The own reference package's cancellation class, as the recommendation reads it.

    The recommendation's own ``resolve_own_reference_price`` with the same
    market key, category and display name: the owner's live Booking package
    of the latest run. A typed-in sample price has no package, so no live
    row, no owned property or no category all mean None — today's overall
    minimums, exactly like the statistics.
    """
    if owned_property_id is None or room_type_category is None:
        return None
    owned_property = onboarding_repository.get_owned_property(account_id, owned_property_id)
    if owned_property is None:
        return None
    _, _, cancellation_type = resolve_own_reference_price(
        repository,
        account_id=account_id,
        canonical_destination=canonical_destination,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        rooms=rooms,
        room_type_category=room_type_category,
        display_name=owned_property.get("display_name"),
    )
    return cancellation_type


@router.get("/price-history", response_model=PriceHistorySeries)
async def price_history(
    destination: str = Query(..., description="Canonical destination, e.g. faliraki"),
    check_in: date = Query(...),
    check_out: date = Query(...),
    adults: int = Query(default=2, ge=1),
    children: int = Query(default=0, ge=0),
    rooms: int = Query(default=1, ge=1),
    room_type_category: str | None = Query(default=None),
    owned_property_id: UUID | None = Query(default=None),
    owned_room_type_id: UUID | None = Query(default=None),
    account: AccountContext = Depends(get_account_context),
    repository: PriceHistoryRepository = Depends(get_price_history_repository),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
) -> PriceHistorySeries:
    """Return a thin per-market price-history series for a frontend sparkline.

    One point per (run, competitor) with the competitor's cheapest nightly price
    in that run, newest run first. Account-scoped; ``destination`` is
    canonicalized server-side (like every other boundary) so 'Faliraki' or
    'Φαληράκι' matches the stored canonical market key instead of silently
    returning an empty series.

    One basis with the recommendation (owner decision 2026-09-30): the
    points are exactly the statistics' basis rows — the room-matching
    agent's comparable rooms when the latest run has agent matches for
    ``owned_room_type_id`` (default: the owned property's selected room
    type), else the category pool with the same <5 widening rule. The
    per-hotel minimums are like-for-like in the SAME reference cancellation
    class the recommendation resolves (the owner's live Booking package in
    the latest run), so the latest point's median is the recommendation's
    market median for the same inputs.
    """
    canonical = canonicalize_destination(destination) or destination
    normalized_room_category = normalize_room_type_category_key(room_type_category)
    # The owned room, resolved exactly as the recommendation does: the
    # requested one (it must belong to owned_property_id when both are
    # given), else the property's selected room type.
    owned_room: dict | None = None
    if owned_room_type_id is not None:
        owned_room = onboarding_repository.get_owned_room_type(
            account.account_id, owned_room_type_id
        )
        if owned_room is None or (
            owned_property_id is not None
            and str(owned_room.get("owned_property_id")) != str(owned_property_id)
        ):
            raise HTTPException(status_code=404, detail="Owned room type not found")
    elif owned_property_id is not None:
        owned_room = onboarding_repository.get_selected_room_type(
            account.account_id, owned_property_id
        )
    resolved_room_type_id = (
        (owned_room.get("id") or owned_room_type_id) if owned_room is not None else None
    )
    # Like the recommendation: an explicit category wins, else the owned room's.
    if normalized_room_category is None and owned_room is not None:
        normalized_room_category = normalize_room_type_category_key(
            owned_room.get("room_type_category")
        )
    own_cancellation_type = _reference_cancellation_class(
        repository,
        onboarding_repository,
        account_id=account.account_id,
        owned_property_id=owned_property_id
        or (owned_room.get("owned_property_id") if owned_room is not None else None),
        canonical_destination=canonical,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        rooms=rooms,
        room_type_category=normalized_room_category,
    )
    rows = repository.fetch_price_series(
        account_id=account.account_id,
        canonical_destination=canonical,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        rooms=rooms,
        room_type_category=normalized_room_category,
        # The statistics read similar-only hotels too and decide the scope;
        # basis_price_rows applies that very decision here.
        include_similar=True,
        owned_room_type_id=resolved_room_type_id,
        cancellation_type=own_cancellation_type,
    )
    points: list[PriceHistoryPoint] = []
    for row in basis_price_rows(rows):
        min_price = as_optional_float(row.get("min_price"))
        points.append(
            PriceHistoryPoint(
                run_index=int(row.get("rn") or 0),
                observed_at=str(row["observed_at"]) if row.get("observed_at") is not None else None,
                property_id=row.get("property_id"),
                hotel_name=str(row.get("hotel_name") or ""),
                min_price_eur=round(min_price, 2) if min_price is not None else None,
            )
        )
    return PriceHistorySeries(
        canonical_destination=canonical,
        check_in=check_in,
        check_out=check_out,
        points=points,
    )
