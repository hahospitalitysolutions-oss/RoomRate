from contextlib import asynccontextmanager
import logging

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.config import is_production_env, settings, validate_production_settings
from api.db import get_engine
from api.logging_utils import JsonLogFormatter, RequestIdFilter
from api.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from api.rate_limit import RateLimitMiddleware
from api.repositories.scrape_jobs_repository import QuotaExceededError
from api.routers import agents, competitors, maps, market, me, notifications, onboarding, schedule, scrape_jobs, tracking

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
# LOG_FORMAT=json swaps the root formatter for one-JSON-object-per-line
# output (stdlib only); the request-id filter enriches records in both modes.
_request_id_filter = RequestIdFilter()
for _handler in logging.getLogger().handlers:
    if settings.log_format == "json":
        _handler.setFormatter(JsonLogFormatter())
    _handler.addFilter(_request_id_filter)
logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """Initialize Sentry when a DSN is configured; report whether it ran.

    Importing ``sentry_sdk`` lazily keeps the dependency optional at runtime: a
    deployment (or test run) without ``SENTRY_DSN`` never imports it and never
    opens a transport. Called at import time, BEFORE the FastAPI app below is
    constructed, because the SDK's Starlette/FastAPI integration patches the
    middleware stack when it is set up — patching after ``app`` exists would
    leave request-scoped error context out of the reports.

    Returns:
        True when a client was initialized, False when no DSN is configured.
    """
    if not settings.sentry_dsn:
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Never ship request bodies, headers, cookies or user identifiers to a
        # third party: this service handles account ids and Supabase tokens.
        send_default_pii=False,
    )
    logger.info("Sentry error monitoring enabled (environment=%s)", settings.app_env)
    return True


init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast BEFORE opening any resources: a production process with dev
    # credentials or no database must refuse to boot, not come up half-working.
    validate_production_settings(settings)
    logger.info("RoomRate API starting up")
    # Health checks (Phase F) read liveness from app.state.scheduler_status:
    # the tick job stamps .last_tick_at on every cycle.
    from api.scheduler import SchedulerStatus

    app.state.scheduler = None
    app.state.scheduler_status = SchedulerStatus()

    # Cross-process notification listener: LISTENs on a dedicated connection and
    # fans pg_notify payloads to the shared (account-scoped) ConnectionManager.
    # An unavailable DB just logs and continues — REST stays source of truth.
    import asyncio

    from api.services.notification_broadcaster import NotificationListener
    from api.services.notification_broadcaster import manager as notification_manager

    app.state.notification_listener = NotificationListener(notification_manager)
    try:
        app.state.notification_listener.start(asyncio.get_running_loop())
    except Exception:
        logger.warning("Notification listener failed to start; continuing", exc_info=True)

    if settings.scheduler_enabled and settings.process_role == "all":
        # Lazy import: apscheduler is only required when the scheduler runs.
        from api.scheduler import create_scheduler

        app.state.scheduler = create_scheduler(status=app.state.scheduler_status)
        app.state.scheduler.start()
        logger.info("Background scheduler started")
    yield
    if app.state.scheduler is not None:
        app.state.scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
    listener = getattr(app.state, "notification_listener", None)
    if listener is not None:
        listener.stop()
        logger.info("Notification listener stopped")
    logger.info("RoomRate API shutting down")


app = FastAPI(
    title="RoomRate API",
    description="Market intelligence API for hotel owners and short-term rental operators",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    """Map scrape-quota refusals to 429 in ONE place.

    Quota refusals are a transient capacity condition, not bad input (422).
    Every route whose service path may create a scrape job (scrape-jobs,
    onboarding) relies on this handler instead of per-router catches.
    """
    _ = request
    return JSONResponse(status_code=429, content={"detail": str(exc)})


# Middleware ordering: each add_middleware() wraps the stack built so far, so
# the LAST one added runs OUTERMOST. CORS stays outermost so short-circuit
# responses from inner middlewares still carry CORS headers for browsers;
# security headers wrap the rate limiter so 429s also carry them.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware, include_hsts=is_production_env(settings.app_env))
# Request-id wraps everything below it so even rate-limit 429s are correlated.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-RoomRate-Account-ID"],
)

app.include_router(me.router, prefix="/api/v1", tags=["Current User"])
app.include_router(market.router, prefix="/api/v1/market", tags=["Market"])
app.include_router(competitors.router, prefix="/api/v1/competitors", tags=["Competitors"])
app.include_router(maps.router, prefix="/api/v1/maps", tags=["Maps"])
app.include_router(agents.router, prefix="/api/v1/agents", tags=["Agents"])
app.include_router(onboarding.router, prefix="/api/v1/onboarding", tags=["Onboarding"])
app.include_router(schedule.router, prefix="/api/v1/schedule", tags=["Schedule"])
app.include_router(scrape_jobs.router, prefix="/api/v1/scrape-jobs", tags=["Scrape Jobs"])
app.include_router(tracking.router, prefix="/api/v1/tracked", tags=["Tracked Competitors"])
# Two routers from the notifications module: the WS endpoint at /ws/alerts
# (no prefix) and the REST feed under /api/v1/notifications.
app.include_router(notifications.router, tags=["Notifications"])
app.include_router(notifications.rest_router, prefix="/api/v1/notifications", tags=["Notifications"])


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Unauthenticated, DB-free liveness probe with scheduler status.

    Reads scheduler liveness from ``app.state`` (populated by the lifespan):
    whether a scheduler object is present and running, and ``last_tick_at`` from
    the SchedulerStatus holder the tick stamps each cycle. Everything is guarded
    so the endpoint answers even when the scheduler is disabled/absent.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    status = getattr(request.app.state, "scheduler_status", None)
    last_tick_at = getattr(status, "last_tick_at", None) if status is not None else None
    return {
        "status": "ok",
        "service": "roomrate-api",
        "process_role": settings.process_role,
        "scheduler": {
            "enabled": settings.scheduler_enabled and settings.process_role == "all",
            "running": bool(scheduler is not None and getattr(scheduler, "running", False)),
            "last_tick_at": last_tick_at.isoformat() if last_tick_at is not None else None,
        },
    }


@app.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    """Return 200 only when this FastAPI process can safely receive traffic.

    The liveness endpoint remains DB-free so an orchestrator does not restart
    a healthy process during a database incident. Readiness checks PostgreSQL
    and, only in local ``all`` mode, the in-process scheduler.
    """
    database_ready = False
    database_error: str | None = None
    try:
        with get_engine(role="api").connect() as connection:
            database_ready = connection.execute(text("SELECT 1")).scalar() == 1
    except Exception as exc:
        database_error = type(exc).__name__
        logger.warning("Readiness database check failed", exc_info=True)

    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_expected = settings.scheduler_enabled and settings.process_role == "all"
    scheduler_ready = not scheduler_expected or bool(
        scheduler is not None and getattr(scheduler, "running", False)
    )
    ready = database_ready and scheduler_ready
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": "roomrate-api",
        "process_role": settings.process_role,
        "checks": {
            "database": {
                "ready": database_ready,
                "error": database_error,
            },
            "scheduler": {
                "expected_in_process": scheduler_expected,
                "ready": scheduler_ready,
            },
        },
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)
