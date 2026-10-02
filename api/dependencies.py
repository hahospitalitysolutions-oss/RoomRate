import secrets
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from api.config import settings
from api.db import db_connection
from api.repositories.accounts_repository import AccountsRepository
from api.repositories.alerts_repository import AlertsRepository
from api.repositories.price_history_repository import PriceHistoryRepository
from api.repositories.price_recommendation_audit_repository import (
    PriceRecommendationAuditRepository,
)
from api.repositories.room_match_repository import RoomMatchRepository
from api.repositories.room_rates_repository import RoomRatesRepository
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.schedule_repository import ScheduleRepository
from api.repositories.scrape_jobs_repository import ScrapeJobRepository
from api.repositories.tracking_repository import TrackingRepository
from api.services.market_service import MarketService
from api.services.price_recommendation_agent import PriceRecommendationAgent
from api.services.room_matching_agent import RoomMatchingAgentService
from api.services.auth_service import SupabaseAuthService
from api.services.notification_broadcaster import NotificationBroadcaster
from api.services.onboarding_service import OnboardingService
from api.services.price_alert_service import PriceAlertService
from api.services.schedule_service import ScheduleService
from api.services.scrape_job_service import BookingScrapeJobRunner, ScrapeJobService
from api.services.tracking_service import TrackingService
from api.services.ws_ticket_service import WsTicketService

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_auth = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AccountContext:
    """Authenticated tenant context for account-scoped RoomRate reads."""

    account_id: UUID
    auth_subject: str | None = None
    auth_provider: str | None = None
    email: str | None = None


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Validate internal API key for frontend/service calls."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    # Constant-time comparison: != short-circuits on the first differing byte,
    # leaking key-prefix timing on the sole internal-auth guard.
    if not secrets.compare_digest(api_key.encode(), settings.internal_api_key.encode()):
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key


def build_account_context(
    api_key: str,
    account_id_header: str | None,
) -> AccountContext:
    """Build the current account context from trusted request metadata.

    ``api_key`` must already be validated (``verify_api_key``); it is accepted
    only to make that trust requirement explicit at the call site.
    """
    _ = api_key
    account_id_raw = account_id_header or str(settings.roomrate_default_account_id)
    try:
        account_id = UUID(account_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="X-RoomRate-Account-ID must be a valid UUID") from exc
    return AccountContext(account_id=account_id)


def get_auth_service() -> SupabaseAuthService:
    """Build SupabaseAuthService for browser bearer token verification."""
    return SupabaseAuthService()


def get_accounts_repository() -> AccountsRepository:
    """Build AccountsRepository for auth/account overview routes."""
    return AccountsRepository()


async def resolve_token_account(
    token: str,
    auth_service: SupabaseAuthService,
    accounts_repository: AccountsRepository,
) -> AccountContext:
    """Resolve a tenant account from a verified Supabase access token.

    The single token→account path: verify the token, then map (or lazily
    create) the account for the Supabase identity. Shared by the HTTP
    dependency (via ``resolve_account_context``) and the WebSocket handshake
    so token verification lives in exactly one place.
    """
    user = await auth_service.verify_access_token(token)
    account_id = accounts_repository.get_or_create_account_for_identity(
        auth_provider="supabase",
        auth_subject=user.auth_subject,
        email=user.email,
        display_name=user.display_name,
    )
    return AccountContext(
        account_id=account_id,
        auth_subject=user.auth_subject,
        auth_provider="supabase",
        email=user.email,
    )


async def resolve_account_context(
    token: str | None,
    api_key: str | None,
    account_id_header: str | None,
    auth_service: SupabaseAuthService,
    accounts_repository: AccountsRepository,
) -> AccountContext:
    """Resolve a tenant account from a Supabase token or the internal API key.

    HTTP entry point (``get_account_context``). The token branch is delegated
    to ``resolve_token_account``, which the WebSocket handshake also calls —
    the WS path keeps its own, stricter api_key handling. Browser clients pass
    a Supabase access token; ``X-API-Key`` + account-id is the internal /
    local fallback only.
    """
    if token:
        return await resolve_token_account(token, auth_service, accounts_repository)

    validated_api_key = await verify_api_key(api_key)
    return build_account_context(
        api_key=validated_api_key,
        account_id_header=account_id_header,
    )


async def get_account_context(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_auth),
    api_key: str | None = Security(api_key_header),
    account_id_header: str | None = Header(default=None, alias="X-RoomRate-Account-ID"),
    auth_service: SupabaseAuthService = Depends(get_auth_service),
    accounts_repository: AccountsRepository = Depends(get_accounts_repository),
) -> AccountContext:
    """Resolve the tenant account for this request.

    Browser clients use Supabase `Authorization: Bearer` tokens. `X-API-Key`
    remains only as an internal/local fallback and must never be used in Angular.
    """
    return await resolve_account_context(
        token=credentials.credentials if credentials else None,
        api_key=api_key,
        account_id_header=account_id_header,
        auth_service=auth_service,
        accounts_repository=accounts_repository,
    )


def get_market_service(account: AccountContext = Depends(get_account_context)) -> MarketService:
    """Build MarketService with a PostgreSQL repository."""
    repository = RoomRatesRepository(
        db_connection,
        source=settings.roomrate_rate_source,
        account_id=account.account_id,
    )
    return MarketService(repository)


def get_price_history_repository() -> PriceHistoryRepository:
    """Build PriceHistoryRepository over the shared 'api' connection factory."""
    return PriceHistoryRepository(db_connection)


def get_price_recommendation_audit_repository() -> PriceRecommendationAuditRepository:
    """Build the durable audit/quota/cache repository for pricing decisions."""
    return PriceRecommendationAuditRepository()


def get_alerts_repository() -> AlertsRepository:
    """Build AlertsRepository for alert rules and the notification feed."""
    return AlertsRepository()


def get_notification_broadcaster() -> NotificationBroadcaster:
    """Return the cross-process notification publisher (pg_notify)."""
    return NotificationBroadcaster()


def get_price_alert_service() -> PriceAlertService:
    """Build PriceAlertService — single factory reused by API and scheduler paths.

    The notifier is the pg_notify broadcaster so a notification raised on any
    worker reaches WebSocket clients on every process.
    """
    return PriceAlertService(
        alerts_repository=get_alerts_repository(),
        price_history_repository=get_price_history_repository(),
        tracking_repository=TrackingRepository(),
        notifier=get_notification_broadcaster(),
        default_threshold_pct=settings.default_alert_threshold_pct,
        notification_retention_days=settings.notification_retention_days,
    )


def get_notification_service(
    account: AccountContext = Depends(get_account_context),
) -> PriceAlertService:
    """PriceAlertService for the notification REST read/mark routes."""
    _ = account
    return get_price_alert_service()


def get_ws_ticket_service() -> WsTicketService:
    """Build WsTicketService for minting/redeeming one-time WS auth tickets."""
    return WsTicketService()


def get_room_match_repository() -> RoomMatchRepository:
    """Build the repository for AI room matches and agent-run audits."""
    return RoomMatchRepository()


@lru_cache(maxsize=1)
def get_room_matching_service() -> RoomMatchingAgentService:
    """Build the room-matching agent service (spec 2026-09-29 Μέρος Α).

    Memoized like the pricing agent so the cached Anthropic client survives
    across requests AND scrape-job completions. The rates repository is
    account-scoped, so it is built per run through the factory instead of
    being captured here. With an empty ``anthropic_api_key`` every run skips
    before constructing a client, so this is safe without credentials.
    """
    return RoomMatchingAgentService(
        match_repository=RoomMatchRepository(),
        onboarding_repository=OnboardingRepository(),
        rates_repository_factory=lambda account_id: RoomRatesRepository(
            db_connection,
            source=settings.roomrate_rate_source,
            account_id=account_id,
        ),
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_matching_model,
        timeout_seconds=settings.anthropic_matching_timeout_seconds,
        max_daily_runs=settings.max_daily_room_match_runs_per_account,
    )


def get_scrape_job_service() -> ScrapeJobService:
    """Build ScrapeJobService for account-scoped scrape orchestration.

    The alert evaluator and the room matcher are wired here so the
    API/BackgroundTasks path evaluates price alerts and AI room matching on
    completion, exactly like the scheduler executor path.
    """
    return ScrapeJobService(
        repository=ScrapeJobRepository(
            max_concurrent_jobs_per_account=settings.max_concurrent_scrape_jobs_per_account,
            max_daily_jobs_per_account=settings.max_daily_scrape_jobs_per_account,
            max_attempts=settings.scrape_job_max_attempts,
            retry_base_seconds=settings.scrape_job_retry_base_seconds,
            retry_max_seconds=settings.scrape_job_retry_max_seconds,
        ),
        runner=BookingScrapeJobRunner(),
        alert_evaluator=get_price_alert_service(),
        retry_base_seconds=settings.scrape_job_retry_base_seconds,
        retry_max_seconds=settings.scrape_job_retry_max_seconds,
        # Round 6: radius centre lookup (owner's coordinates). This factory is
        # the one construction site for the API, BackgroundTasks and scheduler
        # paths, so every job resolves its radius the same way.
        owned_property_lookup=OnboardingRepository(),
        # Spec 2026-09-29 Α.1: best-effort AI room matching after every
        # completed competitor scrape (idempotent; silent without a key).
        room_matcher=get_room_matching_service(),
    )


def get_schedule_service() -> ScheduleService:
    """Build ScheduleService for schedule config and scheduled enqueueing.

    The notifier lets the circuit breaker tell an account its schedule was
    auto-disabled (Phase D).
    """
    return ScheduleService(
        repository=ScheduleRepository(),
        scrape_job_service=get_scrape_job_service(),
        notifier=get_price_alert_service(),
    )


def get_onboarding_repository() -> OnboardingRepository:
    """Build OnboardingRepository for owned-property lookups (e.g. room matching)."""
    return OnboardingRepository()


def get_onboarding_service() -> OnboardingService:
    """Build OnboardingService for property setup and automatic discovery."""
    return OnboardingService(
        repository=OnboardingRepository(),
        scrape_job_service=get_scrape_job_service(),
    )


def get_tracking_service() -> TrackingService:
    """Build TrackingService for selected competitor persistence."""
    return TrackingService(repository=TrackingRepository())


@lru_cache(maxsize=1)
def get_price_recommendation_agent() -> PriceRecommendationAgent:
    """Build the hybrid price-recommendation agent from settings.

    Memoized (settings are static for the process lifetime) so the agent's
    cached Anthropic client survives across requests instead of being rebuilt
    per call. With an empty ``anthropic_api_key`` the agent never constructs a
    client and returns the statistical baseline, so this is safe without
    credentials.
    """
    return PriceRecommendationAgent(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
    )
