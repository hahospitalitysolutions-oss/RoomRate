import logging
from functools import lru_cache
from uuid import UUID
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)

# Dev fallback for INTERNAL_API_KEY. validate_production_settings refuses to
# boot a production process that still uses it — it is public in this repo.
DEV_INTERNAL_API_KEY = "dev-roomrate-key"

# APP_ENV spellings treated as "this is a production deployment".
PRODUCTION_ENV_NAMES = frozenset({"production", "prod"})


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # utf-8-sig: the owner's .env is written by a Windows editor with a UTF-8
    # BOM; plain utf-8 keeps the BOM glued to the first key so that variable
    # never loads. The scraper (scraper/clients.py) already tolerates it.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    # NOTE: MAPBOX_PUBLIC_TOKEN and SUPABASE_ANON_KEY are frontend-only env
    # vars (NG_APP_*); the API never reads them, so they have no fields here.
    app_env: str = Field(default="development", alias="APP_ENV")
    # ``all`` keeps the one-process local developer experience. Production
    # deployments must split the FastAPI web process (``api``) from the
    # Python APScheduler process (``worker``).
    process_role: Literal["api", "worker", "all"] = Field(
        default="all", alias="ROOMRATE_PROCESS_ROLE"
    )
    database_url: str = Field(default="", alias="DATABASE_URL")
    internal_api_key: str = Field(default=DEV_INTERNAL_API_KEY, alias="INTERNAL_API_KEY")
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    # "text" (human-readable, default) or "json" (one JSON object per log
    # line, incl. structured extras + X-Request-ID; for log aggregators).
    log_format: Literal["text", "json"] = Field(default="text", alias="LOG_FORMAT")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    # Local Supabase access-token verification (Phase F). Tokens are validated
    # in-process — no per-request network round-trip to /auth/v1/user.
    #   * HS256 projects (legacy shared secret): set SUPABASE_JWT_SECRET.
    #   * RS256/ES256 projects (asymmetric signing keys): set SUPABASE_JWKS_URL
    #     (defaults to <SUPABASE_URL>/auth/v1/.well-known/jwks.json when blank).
    # The signing scheme is auto-detected from the token header ``alg``. When
    # neither a secret nor a resolvable JWKS URL is configured, verification is
    # not possible and the service degrades exactly as before (the internal
    # API-key path keeps working for local dev/tests).
    supabase_jwt_secret: str = Field(default="", alias="SUPABASE_JWT_SECRET")
    supabase_jwks_url: str = Field(default="", alias="SUPABASE_JWKS_URL")
    supabase_jwt_audience: str = Field(default="authenticated", alias="SUPABASE_JWT_AUDIENCE")
    # Neon Auth (Better Auth managed by Neon). When NEON_AUTH_URL is set, browser
    # tokens are verified against <NEON_AUTH_URL>/.well-known/jwks.json instead
    # of Supabase. Issuer/audience are checked only when set (Better Auth uses
    # its base URL for both).
    neon_auth_url: str = Field(default="", alias="NEON_AUTH_URL")
    neon_auth_jwks_url: str = Field(default="", alias="NEON_AUTH_JWKS_URL")
    neon_auth_jwt_issuer: str = Field(default="", alias="NEON_AUTH_JWT_ISSUER")
    neon_auth_jwt_audience: str = Field(default="", alias="NEON_AUTH_JWT_AUDIENCE")
    roomrate_default_account_id: UUID = Field(
        default=UUID("00000000-0000-0000-0000-000000000001"),
        alias="ROOMRATE_DEFAULT_ACCOUNT_ID",
    )
    roomrate_rate_source: Literal["legacy", "normalized"] = Field(
        default="normalized",
        alias="ROOMRATE_RATE_SOURCE",
    )
    # Scrape-job execution guardrails: hard subprocess timeout plus per-account
    # concurrency/daily quotas enforced at job creation time.
    scrape_job_timeout_seconds: int = Field(default=1800, alias="SCRAPE_JOB_TIMEOUT_SECONDS")
    scrape_job_max_attempts: int = Field(default=3, alias="SCRAPE_JOB_MAX_ATTEMPTS")
    scrape_job_retry_base_seconds: int = Field(default=30, alias="SCRAPE_JOB_RETRY_BASE_SECONDS")
    scrape_job_retry_max_seconds: int = Field(default=900, alias="SCRAPE_JOB_RETRY_MAX_SECONDS")
    # Daily scheduler job deletes per-job scraper CSVs (output/scrapes/) older
    # than this many days; results live in PostgreSQL, the CSVs are debugging
    # artifacts. 0 disables the cleanup.
    scrape_csv_retention_days: int = Field(default=30, alias="SCRAPE_CSV_RETENTION_DAYS")
    max_concurrent_scrape_jobs_per_account: int = Field(default=2, alias="MAX_CONCURRENT_SCRAPE_JOBS_PER_ACCOUNT")
    max_daily_scrape_jobs_per_account: int = Field(default=20, alias="MAX_DAILY_SCRAPE_JOBS_PER_ACCOUNT")
    # GET /api/v1/onboarding/property-candidates (the setup wizard's «Δείξε
    # άλλα» search, and automatic_setup's candidate lookup) forwards this to
    # the scout pipeline's cache window instead of a hardcoded 0. 0 keeps the
    # old force-fresh-scrape-every-call behavior; default matches
    # ScraperConfig's own scout_cache_hours default.
    property_candidates_cache_hours: int = Field(
        default=24, ge=0, alias="PROPERTY_CANDIDATES_CACHE_HOURS"
    )
    # Background scheduler (APScheduler): master switch, schedule-tick cadence,
    # queued-job executor batch size, and the stale-running sweep threshold.
    scheduler_enabled: bool = Field(default=True, alias="ROOMRATE_SCHEDULER_ENABLED")
    scheduler_tick_minutes: int = Field(default=15, alias="ROOMRATE_SCHEDULER_TICK_MINUTES")
    executor_batch_size: int = Field(default=3, alias="ROOMRATE_EXECUTOR_BATCH_SIZE")
    stale_job_minutes: int = Field(default=5, alias="ROOMRATE_STALE_JOB_MINUTES")
    # Per-caller (account id, else client IP) cap for the expensive mutation
    # POSTs (scrape jobs, onboarding setup, price recommendation), per minute
    # and PER PROCESS. 0 disables rate limiting entirely.
    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    # Default price-change alert threshold (percent) used when an account has no
    # explicit alert rule: a competitor move >= this magnitude raises a
    # notification regardless of direction.
    default_alert_threshold_pct: float = Field(default=10.0, alias="ROOMRATE_DEFAULT_ALERT_THRESHOLD_PCT")
    # Account-scoped feed cleanup is piggybacked on feed reads. 0 keeps
    # notifications indefinitely; the default limits GDPR-relevant history.
    notification_retention_days: int = Field(default=180, alias="NOTIFICATION_RETENTION_DAYS")
    # Hybrid price-prediction agent (Phase E). When the API key is empty the
    # agent skips the LLM entirely and returns the statistical baseline, so the
    # endpoint stays usable without Anthropic credentials configured.
    # claude-opus-5-5 is the default (spec 2026-09-29 Β.1): thinking is always
    # on for this model, so the request never sends a `thinking` parameter and
    # depth is steered only via output_config.effort in the agent call.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-5-5", alias="ANTHROPIC_MODEL")
    anthropic_timeout_seconds: float = Field(default=30.0, alias="ANTHROPIC_TIMEOUT_SECONDS")
    # Room-matching agent (spec 2026-09-29 Μέρος Α). Shares the API key
    # above; the model is separate so matching can ride a different
    # (cheaper/newer) model than pricing without touching ANTHROPIC_MODEL.
    anthropic_matching_model: str = Field(
        default="claude-sonnet-5-5", alias="ANTHROPIC_MATCHING_MODEL"
    )
    # The matching call scores up to 150 rooms in one structured answer, so
    # it needs far more than the pricing timeout; the service also caps SDK
    # retries at 1 so a failing call cannot block the post-scrape hook for
    # several multiples of this value.
    anthropic_matching_timeout_seconds: float = Field(
        default=120.0, alias="ANTHROPIC_MATCHING_TIMEOUT_SECONDS"
    )
    # Daily cap on matching-agent RUNS (auto + manual) per account, enforced
    # against the roomrate_agent_runs audit rows.
    max_daily_room_match_runs_per_account: int = Field(
        default=30,
        ge=1,
        alias="MAX_DAILY_ROOM_MATCH_RUNS_PER_ACCOUNT",
    )
    max_daily_price_recommendations_per_account: int = Field(
        default=50,
        ge=1,
        alias="MAX_DAILY_PRICE_RECOMMENDATIONS_PER_ACCOUNT",
    )
    price_recommendation_cache_minutes: int = Field(
        default=15,
        ge=1,
        alias="PRICE_RECOMMENDATION_CACHE_MINUTES",
    )
    # Audit rows keep the full request/response payload of every pricing
    # decision; 0 disables the nightly cleanup and keeps them forever.
    price_recommendation_audit_retention_days: int = Field(
        default=180,
        ge=0,
        alias="PRICE_RECOMMENDATION_AUDIT_RETENTION_DAYS",
    )
    price_recommendation_max_change_pct: float = Field(
        default=20.0,
        ge=0,
        le=100,
        alias="PRICE_RECOMMENDATION_MAX_CHANGE_PCT",
    )
    # Optional error-monitoring integration (Sentry, see api.main.init_sentry).
    # A blank DSN keeps it off entirely and sentry_sdk is never imported.
    # Performance tracing defaults to 0 (errors only); raise deliberately
    # (0.05-0.2) when latency data is needed — it samples requests and
    # consumes Sentry quota.
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")
    sentry_traces_sample_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, alias="SENTRY_TRACES_SAMPLE_RATE"
    )

    @property
    def allowed_origins(self) -> list[str]:
        """Return configured CORS origins as a clean list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def is_production_env(app_env: str) -> bool:
    """Return True when APP_ENV designates a production deployment."""
    return app_env.strip().lower() in PRODUCTION_ENV_NAMES


def _is_localhost_origin(origin: str) -> bool:
    """True for CORS origins that only make sense on a developer machine."""
    host = origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def validate_production_settings(candidate: Settings) -> None:
    """Fail fast on unsafe production configuration; no-op outside production.

    Called once at lifespan startup. ALL violations are aggregated into one
    RuntimeError so a misconfigured deploy surfaces every problem in a single
    boot attempt instead of one per restart. A localhost-only CORS_ORIGINS is
    deliberately a warning, not fatal: an API-only rollout (curl, mobile,
    server-to-server) may legitimately have no browser origins yet.
    """
    if not is_production_env(candidate.app_env):
        return

    violations: list[str] = []
    if not candidate.database_url:
        violations.append("DATABASE_URL must be set (the API cannot serve without PostgreSQL)")
    if candidate.process_role == "all":
        violations.append(
            "ROOMRATE_PROCESS_ROLE must be 'api' or 'worker' in production; "
            "do not run scraping inside the FastAPI web process"
        )
    if candidate.process_role == "api":
        if candidate.internal_api_key == DEV_INTERNAL_API_KEY:
            violations.append(
                f"INTERNAL_API_KEY must not be the dev default ({DEV_INTERNAL_API_KEY!r}); "
                "generate a unique secret for production"
            )
        if len(candidate.internal_api_key) < 24:
            violations.append("INTERNAL_API_KEY must be at least 24 characters long")
        if not (
            candidate.neon_auth_url
            or candidate.neon_auth_jwks_url
            or candidate.supabase_jwt_secret
            or candidate.supabase_jwks_url
            or candidate.supabase_url
        ):
            violations.append(
                "NEON_AUTH_URL (or one of SUPABASE_JWT_SECRET / SUPABASE_JWKS_URL / SUPABASE_URL) "
                "must be set (browser access tokens cannot be verified otherwise)"
            )
    if candidate.process_role == "worker" and not candidate.scheduler_enabled:
        violations.append("ROOMRATE_SCHEDULER_ENABLED must be true for the worker process")
    if candidate.scrape_job_max_attempts < 1 or candidate.scrape_job_max_attempts > 10:
        violations.append("SCRAPE_JOB_MAX_ATTEMPTS must be between 1 and 10")
    if candidate.scrape_job_retry_base_seconds < 1:
        violations.append("SCRAPE_JOB_RETRY_BASE_SECONDS must be at least 1")
    if candidate.scrape_job_retry_max_seconds < candidate.scrape_job_retry_base_seconds:
        violations.append(
            "SCRAPE_JOB_RETRY_MAX_SECONDS must be greater than or equal to "
            "SCRAPE_JOB_RETRY_BASE_SECONDS"
        )

    origins = candidate.allowed_origins
    if origins and all(_is_localhost_origin(origin) for origin in origins):
        logger.warning(
            "CORS_ORIGINS contains only localhost origins in production; "
            "browsers on real domains will be blocked by CORS: %s",
            candidate.cors_origins,
        )

    if violations:
        details = "\n".join(f"  - {violation}" for violation in violations)
        raise RuntimeError(
            f"Refusing to start: invalid production configuration (APP_ENV={candidate.app_env}):\n{details}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


settings = get_settings()
