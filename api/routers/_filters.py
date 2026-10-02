import logging
from datetime import date
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_onboarding_repository,
    get_room_match_repository,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.room_match_repository import RoomMatchRepository
from api.services.market_helpers import as_optional_float
from api.services.market_service import (
    AgentMatchOverride,
    RoomRateFilters,
    build_agent_match_lookup,
)
from api.services.room_rates_normalizer import normalize_room_type_category_key

logger = logging.getLogger(__name__)

AgentMatchLookup = dict[tuple[str, str], AgentMatchOverride]


def build_filters(
    request: Request,
    destination: str | None = Query(default=None, description="Destination/city, e.g. Faliraki"),
    check_in: date | None = Query(default=None),
    check_out: date | None = Query(default=None),
    adults: int | None = Query(default=None, ge=1),
    children: int | None = Query(default=None, ge=0),
    rooms: int | None = Query(default=None, ge=1),
    owned_property_id: UUID | None = Query(default=None),
    room_type_category: str | None = Query(default=None),
    scrape_job_id: UUID | None = Query(default=None),
    amenities: list[str] | None = Query(default=None),
    selected_competitors_only: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=5_000),
    include_similar: bool = Query(
        default=True,
        description=(
            "Deprecated (kept for older clients): the inverse of comparable_only, honoured "
            "only when comparable_only is not sent."
        ),
    ),
    comparable_only: bool = Query(
        default=True,
        description=(
            "Owner decision 2026-09-30: true returns only the comparison set — the matching "
            "agent's comparable rooms when agent rows exist for (scrape_job_id, owned room), "
            "else the same-category pool of room_type_category; false returns every room "
            "with non-comparable packages flagged comparable=false."
        ),
    ),
    owned_room_type_id: UUID | None = Query(
        default=None,
        description="The owned room the comparison is judged against; default the selected room.",
    ),
) -> RoomRateFilters:
    """Build validated filters from query parameters.

    ``comparable_only`` wins when sent; an older client that only sends
    ``include_similar`` keeps its meaning (``include_similar=true`` ==
    ``comparable_only=false``); with neither, non-comparable rooms are hidden.
    The result is stored as ``include_similar`` (its inverse), the one flag
    the SQL pool and ``RoomRateFilters.comparable_only`` both read.
    """
    sent = request.query_params
    if "comparable_only" not in sent and "include_similar" in sent:
        comparable_only = not include_similar
    try:
        return RoomRateFilters(
            destination=destination,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            owned_property_id=owned_property_id,
            # Whitespace-only input collapses to "" (not None) so RoomRateFilters
            # keeps rejecting it with 422 "room_type_category cannot be blank".
            room_type_category=(normalize_room_type_category_key(room_type_category) or "") if room_type_category else None,
            scrape_job_id=scrape_job_id,
            owned_room_type_id=owned_room_type_id,
            amenities=tuple(amenities or ()),
            selected_competitors_only=selected_competitors_only,
            limit=limit,
            include_similar=not comparable_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def owned_property_origin(owned_property: dict | None) -> tuple[float, float] | None:
    """(latitude, longitude) of an owned-property row, or None when unknown.

    A missing, unparseable or 0.0 coordinate (how a missing one ends up
    stored) means "no location": distances stay null and the map draws no
    «Εσείς» marker.
    """
    if not owned_property:
        return None
    latitude = as_optional_float(owned_property.get("latitude"))
    longitude = as_optional_float(owned_property.get("longitude"))
    if not latitude or not longitude:
        return None
    return latitude, longitude


def build_origin(
    filters: RoomRateFilters = Depends(build_filters),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
) -> tuple[float, float] | None:
    """Resolve the distance origin from the account's own property, server-side.

    Round 6 (§3.6): ``distance_km`` is measured from the owner's coordinates,
    never from anything the browser sends. Without ``owned_property_id`` no
    lookup happens; a foreign or inactive property reads as None (the lookup
    is account-scoped), exactly like one without coordinates.
    """
    if not filters.owned_property_id:
        return None
    return owned_property_origin(
        onboarding_repository.get_owned_property(account.account_id, filters.owned_property_id)
    )


def load_agent_matches(
    match_repository: RoomMatchRepository,
    account: AccountContext,
    filters: RoomRateFilters,
    owned_room_type_id: UUID | str | None,
) -> AgentMatchLookup | None:
    """AI match lookup for (job, owned room), or None for the statistical fallback.

    Agent rows are keyed to one scrape job, so without ``scrape_job_id`` (or
    without an owned room id) the read stays statistical. Any lookup failure
    degrades the same way — these reads must never 500 because of the agent
    tables (spec 2026-09-29 Α.5), e.g. on a not-yet-migrated database.
    """
    if not filters.scrape_job_id or not owned_room_type_id:
        return None
    try:
        rows = match_repository.fetch_matches(
            account.account_id, filters.scrape_job_id, owned_room_type_id
        )
    except Exception:
        logger.warning(
            "Agent room-match lookup failed; serving the statistical fallback: "
            "account_id=%s scrape_job_id=%s",
            account.account_id,
            filters.scrape_job_id,
            exc_info=True,
        )
        return None
    return build_agent_match_lookup(rows) or None


def build_agent_matches(
    filters: RoomRateFilters = Depends(build_filters),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    match_repository: RoomMatchRepository = Depends(get_room_match_repository),
) -> AgentMatchLookup | None:
    """The matching agent's verdicts for this read, shared by list, map and summary.

    The owned room is ``owned_room_type_id`` when sent, else the property's
    selected room (the pre-contract behaviour). Nothing is looked up without
    a ``scrape_job_id``; a failing selected-room lookup degrades to the
    statistical fallback like a failing match lookup.
    """
    if not filters.scrape_job_id:
        return None
    owned_room_type_id = filters.owned_room_type_id
    if owned_room_type_id is None and filters.owned_property_id:
        try:
            selected = onboarding_repository.get_selected_room_type(
                account.account_id, filters.owned_property_id
            )
        except Exception:
            logger.warning(
                "Selected room lookup failed; serving the statistical fallback: "
                "account_id=%s owned_property_id=%s",
                account.account_id,
                filters.owned_property_id,
                exc_info=True,
            )
            return None
        owned_room_type_id = (selected or {}).get("id")
    return load_agent_matches(match_repository, account, filters, owned_room_type_id)
