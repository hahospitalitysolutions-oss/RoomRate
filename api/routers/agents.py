import hashlib
import json
import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_price_history_repository,
    get_price_recommendation_audit_repository,
    get_price_recommendation_agent,
    get_room_matching_service,
    get_scrape_job_service,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.price_history_repository import PriceHistoryRepository
from api.repositories.price_recommendation_audit_repository import (
    PriceRecommendationAuditRepository,
)
from api.routers._filters import owned_property_origin
from api.schemas.agents import (
    OwnPriceSource,
    PriceRecommendationRequest,
    PriceRecommendationResponse,
    RoomMatchRunRequest,
    RoomMatchRunResponse,
)
from api.services.market_helpers import as_optional_float
from api.services.market_service import MarketService, RoomRateFilters
from api.services.price_recommendation_agent import (
    PRICE_RECOMMENDATION_PROMPT_VERSION,
    PriceRecommendationAgent,
    apply_business_guardrails,
)
from api.services.price_statistics_service import compute_price_statistics
from api.services.room_matching_agent import RoomMatchingAgentService
from api.services.scrape_job_service import ScrapeJobService

router = APIRouter()
logger = logging.getLogger(__name__)


def build_recommendation_request_hash(account_id: str, payload: dict) -> str:
    """Return a stable SHA-256 identity for one account-scoped pricing request."""
    serialized = json.dumps(
        {"account_id": account_id, **payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_own_reference_price(
    price_history_repository: PriceHistoryRepository,
    *,
    account_id,
    canonical_destination: str,
    request: PriceRecommendationRequest,
    room_type_category: str,
    display_name: str | None,
    sample_price: float | None,
) -> tuple[float | None, OwnPriceSource | None, str | None]:
    """Own reference price, in order: live Booking row of the latest run, onboarding sample, none.

    The live row is what the market actually sees for these dates; the
    onboarding sample is a typed-in number that goes stale. Neither is ever
    counted as a competitor (the repository CTE excludes the own hotel).

    The third element is the reference PACKAGE's cancellation class (spec
    2026-09-29 §4), which scopes the like-for-like history minimums. A
    typed-in sample has no package, so its class is None.
    """
    live_reference = price_history_repository.fetch_own_live_price(
        account_id=account_id,
        canonical_destination=canonical_destination,
        check_in=request.check_in,
        check_out=request.check_out,
        adults=request.adults,
        children=request.children,
        rooms=request.rooms,
        room_type_category=room_type_category,
        display_name=display_name,
    )
    if live_reference is not None:
        return (
            round(live_reference["price"], 2),
            "booking_live",
            live_reference.get("cancellation_type"),
        )
    if sample_price is not None:
        return round(sample_price, 2), "onboarding_sample", None
    return None, None, None


@router.post("/room-matches", response_model=RoomMatchRunResponse)
def run_room_matching(
    request: RoomMatchRunRequest,
    account: AccountContext = Depends(get_account_context),
    scrape_job_service: ScrapeJobService = Depends(get_scrape_job_service),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: RoomMatchingAgentService = Depends(get_room_matching_service),
) -> RoomMatchRunResponse:
    """Run the AI room-matching agent for one (scrape job, owned room) scope.

    Synchronous (spec 2026-09-29 Α.1): the UI's «Επανεκτίμηση ταιριάσματος»
    button waits for the outcome. The run REPLACES any existing agent rows
    for the scope — only the automatic post-scrape trigger is idempotent.
    Agent failures come back as ``status: "error"`` with zero rows (never a
    5xx, spec Α.5); a missing API key is ``status: "skipped"``; the daily
    per-account run quota is a 429 like the pricing endpoint.

    Deliberately a plain ``def``: the blocking ``service.run`` holds the
    worker thread up to the Anthropic timeout, so Starlette must run it in
    the threadpool instead of the event loop.
    """
    job = scrape_job_service.get_job(account.account_id, request.scrape_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Scrape job is not completed yet; run matching once it finishes",
        )
    owned_room = onboarding_repository.get_owned_room_type(
        account.account_id, request.owned_room_type_id
    )
    if owned_room is None:
        raise HTTPException(status_code=404, detail="Owned room type not found")

    result = service.run(
        account.account_id, job.id, owned_room, skip_if_existing=False
    )
    if result.skip_reason == "quota_exceeded":
        raise HTTPException(
            status_code=429,
            detail=(
                "Daily room-matching quota reached for this account. "
                "Try again tomorrow."
            ),
        )
    return RoomMatchRunResponse(
        status=result.status, matches_written=result.matches_written
    )


@router.post("/price-recommendation", response_model=PriceRecommendationResponse)
def price_recommendation(
    request: PriceRecommendationRequest,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    price_history_repository: PriceHistoryRepository = Depends(get_price_history_repository),
    market_service: MarketService = Depends(get_market_service),
    agent: PriceRecommendationAgent = Depends(get_price_recommendation_agent),
    audit_repository: PriceRecommendationAuditRepository = Depends(
        get_price_recommendation_audit_repository
    ),
) -> PriceRecommendationResponse:
    """Hybrid (statistical + LLM) nightly-price recommendation for an owned room.

    Resolves the owned property's market key, builds a statistical baseline from
    recent price history, gathers smart-advisor market context, and asks the
    price agent for a recommendation. The agent always returns one — falling back
    to the statistical baseline when no LLM is configured or the call fails.

    Deliberately a plain ``def``: every call in here is synchronous (repos plus
    the blocking ``agent.recommend``, up to the Anthropic timeout), so Starlette
    runs it in the threadpool instead of blocking the event loop.
    """
    owned_property = onboarding_repository.get_owned_property(
        account.account_id, request.owned_property_id
    )
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")

    canonical_destination = owned_property.get("canonical_destination")
    if not canonical_destination:
        raise HTTPException(status_code=404, detail="Owned property has no canonical destination")

    # Resolve the room category + onboarding sample price. A body-supplied
    # category wins; otherwise we use the account's selected baseline room type.
    # The owned room whose agent matches choose the comparison set: the
    # requested one (it must belong to this property), else the selected one.
    if request.owned_room_type_id is not None:
        selected_room = onboarding_repository.get_owned_room_type(
            account.account_id, request.owned_room_type_id
        )
        if selected_room is None or str(selected_room.get("owned_property_id")) != str(
            request.owned_property_id
        ):
            raise HTTPException(status_code=404, detail="Owned room type not found")
    else:
        selected_room = onboarding_repository.get_selected_room_type(
            account.account_id, request.owned_property_id
        )
    owned_room_type_id = selected_room.get("id") if selected_room is not None else None
    room_type_category = request.room_type_category
    sample_price: float | None = None
    if selected_room is not None:
        room_type_category = room_type_category or selected_room.get("room_type_category")
        # as_optional_float preserves a genuine 0.0 sample price and maps only a
        # missing/unparseable value to "no sample price".
        sample_price = as_optional_float(selected_room.get("sample_price_per_night_eur"))
    if room_type_category is None:
        raise HTTPException(
            status_code=404,
            detail="No selected room type for this property; provide room_type_category",
        )

    # A new scrape must yield a new recommendation inside the cache window, so
    # the latest completed run of this market key is part of the request identity
    # (None when the market has never been scraped — still a stable key).
    latest_run_id = price_history_repository.fetch_latest_completed_run_id(
        account_id=account.account_id,
        canonical_destination=canonical_destination,
        check_in=request.check_in,
        check_out=request.check_out,
        adults=request.adults,
        children=request.children,
        rooms=request.rooms,
    )
    audit_request_payload = {
        **request.model_dump(mode="json"),
        "room_type_category": room_type_category,
        "canonical_destination": canonical_destination,
        "latest_run_id": latest_run_id,
        "owned_room_type_id": str(owned_room_type_id) if owned_room_type_id else None,
    }
    request_hash = build_recommendation_request_hash(
        str(account.account_id), audit_request_payload
    )
    # The audit trail is valuable but it is NOT the product: every audit call
    # below degrades to a logged warning. Before this, an unmigrated database
    # (code shipped ahead of migration 0020) made the cache lookup 500 every
    # single request, and a transient failure discarded an already-billed LLM
    # answer.
    try:
        # Keyed on the current prompt version too: a pre-upgrade entry must
        # never serve after a prompt/model bump.
        cached = audit_repository.get_cached(
            account.account_id, request_hash, PRICE_RECOMMENDATION_PROMPT_VERSION
        )
    except Exception:
        logger.warning(
            "Price recommendation cache lookup failed; computing fresh: account_id=%s",
            account.account_id,
            exc_info=True,
        )
        cached = None
    if cached is not None:
        cached_payload = cached["response_payload"]
        if isinstance(cached_payload, str):
            cached_payload = json.loads(cached_payload)
        return PriceRecommendationResponse.model_validate(
            {
                **cached_payload,
                "audit_id": cached["id"],
                "generated_at": cached["created_at"],
                "cached": True,
                "model_version": cached["model_version"],
                "prompt_version": cached["prompt_version"],
            }
        )

    try:
        recommendations_today = audit_repository.count_today(account.account_id)
    except Exception:
        # Quota tracking is unavailable; serving the request beats refusing
        # every account. The failure is loud in the logs.
        logger.warning(
            "Price recommendation quota check failed; serving unmetered: account_id=%s",
            account.account_id,
            exc_info=True,
        )
        recommendations_today = None
    if (
        recommendations_today is not None
        and recommendations_today >= settings.max_daily_price_recommendations_per_account
    ):
        raise HTTPException(
            status_code=429,
            detail=(
                "Daily price-recommendation quota reached for this account. "
                "Try again tomorrow or reuse an identical cached request."
            ),
        )
    started_at = perf_counter()

    # The live lookup sits AFTER the cache/quota gates so a cache hit costs no query.
    own_price, own_price_source, own_cancellation_type = resolve_own_reference_price(
        price_history_repository,
        account_id=account.account_id,
        canonical_destination=canonical_destination,
        request=request,
        room_type_category=room_type_category,
        display_name=owned_property.get("display_name"),
        sample_price=sample_price,
    )

    history_rows = price_history_repository.fetch_price_series(
        account_id=account.account_id,
        canonical_destination=canonical_destination,
        check_in=request.check_in,
        check_out=request.check_out,
        adults=request.adults,
        children=request.children,
        rooms=request.rooms,
        room_type_category=room_type_category,
        # Similar-only hotels ride along; the statistics decide whether to use them.
        include_similar=True,
        # Like-for-like (spec 2026-09-29 §4): the own reference package's
        # cancellation class scopes the per-hotel minimums; None (unknown
        # class or sample price) keeps today's overall minimums.
        cancellation_type=own_cancellation_type,
        # Owner decision 2026-09-30: runs whose job has agent matches for
        # this owned room price every hotel over its agent-comparable rooms.
        owned_room_type_id=owned_room_type_id,
    )

    statistics = compute_price_statistics(
        history_rows, own_price, request.check_in, own_cancellation_type=own_cancellation_type
    )

    # Same market key AND the same comparison basis the statistics read: every
    # competitor, similar-category hotels included (as fetch_price_series
    # above). Scoping this to the tracked competitors made the model report
    # «only 2 competitors» over statistics computed on 10; the owner's watched
    # set is now a per-competitor ``tracked`` flag instead (owned_property_id
    # lets the service resolve it).
    advisor_filters = RoomRateFilters(
        destination=canonical_destination,
        check_in=request.check_in,
        check_out=request.check_out,
        adults=request.adults,
        children=request.children,
        rooms=request.rooms,
        owned_property_id=request.owned_property_id,
        room_type_category=room_type_category,
        include_similar=True,
    )
    # Β.2: the owner's coordinates give the advisor competitors a distance_km;
    # the reference package's cancellation class scopes the per-competitor
    # cheapest-in-same-class figure inside the agent payload.
    # Agent basis: the model reads exactly the agent-comparable rooms the
    # statistics were computed on.
    comparable_rooms = None
    if (
        owned_room_type_id
        and statistics.stats_scope is not None
        and statistics.stats_scope.used == "agent"
    ):
        comparable_rooms = price_history_repository.fetch_agent_comparable_rooms(
            account_id=account.account_id,
            canonical_destination=canonical_destination,
            check_in=request.check_in,
            check_out=request.check_out,
            adults=request.adults,
            children=request.children,
            rooms=request.rooms,
            owned_room_type_id=owned_room_type_id,
        )
    # Passed only in agent mode, so the category path calls it as before.
    agent_basis_kwargs = {"comparable_rooms": comparable_rooms} if comparable_rooms else {}
    advisor_context = market_service.get_smart_advisor_context(
        advisor_filters,
        my_hotel_name=owned_property.get("display_name"),
        origin=owned_property_origin(owned_property),
        **agent_basis_kwargs,
    )

    recommendation = apply_business_guardrails(
        agent.recommend(
            statistics, advisor_context, own_cancellation_type=own_cancellation_type
        ),
        statistics,
        settings.price_recommendation_max_change_pct,
    )
    # None = the data cannot anchor any number; the UI explains via the
    # statistics notes instead of showing a fabricated €0.
    response = PriceRecommendationResponse(
        statistics=statistics,
        recommendation=recommendation,
        recommendation_available=recommendation is not None,
        own_price_source=own_price_source,
    )
    source = recommendation.source if recommendation is not None else None
    model_version = (
        getattr(agent, "model", "agent-unknown")
        if source == "agent"
        else "statistical-v1"
    )
    response_payload = response.model_dump(mode="json")
    try:
        audit = audit_repository.insert(
            account_id=account.account_id,
            owned_property_id=request.owned_property_id,
            request_hash=request_hash,
            request_payload=audit_request_payload,
            response_payload=response_payload,
            source=source,
            model_version=model_version,
            prompt_version=PRICE_RECOMMENDATION_PROMPT_VERSION,
            latency_ms=round((perf_counter() - started_at) * 1000),
            cache_minutes=settings.price_recommendation_cache_minutes,
        )
    except Exception:
        # The decision was already computed (and, on the agent path, already
        # billed). Refusing to return it would waste the call AND lose the
        # record, so the audit falls back to the application log — which ships
        # to the same aggregator — and the caller still gets its answer.
        logger.error(
            "Price recommendation audit persistence failed; emitting audit to the log instead: "
            "account_id=%s property_id=%s",
            account.account_id,
            request.owned_property_id,
            exc_info=True,
            extra={
                "account_id": str(account.account_id),
                "owned_property_id": str(request.owned_property_id),
                "audit_request": audit_request_payload,
                "audit_response": response_payload,
                "source": source,
                "model_version": model_version,
                "prompt_version": PRICE_RECOMMENDATION_PROMPT_VERSION,
            },
        )
        return response.model_copy(
            update={
                "cached": False,
                "model_version": model_version,
                "prompt_version": PRICE_RECOMMENDATION_PROMPT_VERSION,
            }
        )
    return response.model_copy(
        update={
            "audit_id": audit["id"],
            "generated_at": audit["created_at"],
            "cached": False,
            "model_version": model_version,
            "prompt_version": PRICE_RECOMMENDATION_PROMPT_VERSION,
        }
    )
