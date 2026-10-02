"""Rate limiting for the expensive mutation endpoints, shared by every API process.

A sliding window of hits per caller key. With a database the window lives in
PostgreSQL (``PostgresSlidingWindowLimiter``, table from migration
20260930_0029), so RATE_LIMIT_PER_MINUTE holds per caller across every
uvicorn worker and API replica — before it was counted per process, i.e. up
to N x the limit behind N workers. PostgreSQL is the one store the processes
already share, so no Redis is added.

``SlidingWindowLimiter`` is the in-process window (a deque per key under a
``threading.Lock``): the limiter without a database (local dev, tests) and
the fallback whenever the database check fails — a limiter outage must never
fail a request, and per-process limiting is exactly the previous behaviour.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from math import ceil
from typing import Any

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from api.config import settings
from api.db import get_engine

logger = logging.getLogger(__name__)

# Only the expensive mutations are limited: each spawns a scraper subprocess
# or an LLM call. Paths are matched with the trailing slash stripped so
# /scrape-jobs and /scrape-jobs/ share one bucket.
RATE_LIMITED_POST_PATHS = frozenset(
    {
        "/api/v1/scrape-jobs",
        "/api/v1/onboarding/auto-setup",
        "/api/v1/onboarding/owned-property",
        "/api/v1/agents/price-recommendation",
        "/api/v1/agents/room-matches",
        "/api/v1/notifications/ws-ticket",
    }
)

WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    """Thread-safe sliding-window hit counter with stale-bucket eviction."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._window = window_seconds
        self._clock = clock  # injectable so tests can drive a fake clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock()

    def check(self, key: str, limit: int) -> float | None:
        """Record a hit for ``key``; return None if allowed, else seconds to wait."""
        if limit <= 0:  # 0 (or negative) disables limiting entirely
            return None
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            if now - self._last_sweep >= self._window:
                # Drop buckets whose newest hit already left the window so
                # one-off keys (e.g. rotating IPs) cannot grow the dict forever.
                self._buckets = {k: hits for k, hits in self._buckets.items() if hits and hits[-1] > cutoff}
                self._last_sweep = now
            hits = self._buckets.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return hits[0] + self._window - now
            hits.append(now)
            return None


# Advisory locks in the two-int4 key space, under this namespace: PostgreSQL
# keeps it apart from the single-bigint locks of the scrape-job quota and the
# account upsert, so a rate-limit check never waits on (or blocks) those.
RATE_LIMIT_LOCK_NAMESPACE = 0x524C  # "RL"


class PostgresSlidingWindowLimiter:
    """The sliding window in PostgreSQL: one bucket per caller for the whole deployment.

    Each check is one short transaction on the ``api`` engine: a per-key
    advisory lock (so concurrent checks of one caller in different processes
    serialize and can never both take the last slot), prune the key's hits
    older than the window, count the rest, and record this hit only when it
    fits. The database clock times every hit, so processes never disagree on
    the window. Keys are stored as a SHA-256, so no raw IP, token or account
    id reaches the table.

    Any failure — no DATABASE_URL, the database down, a schema without
    migration 20260930_0029 — falls back to the in-process ``fallback``
    window: never a failed request because of the limiter.
    """

    def __init__(
        self,
        window_seconds: float = WINDOW_SECONDS,
        engine_getter: Callable[[], Any] | None = None,
        fallback: SlidingWindowLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._window = window_seconds
        # None = the app's ``api`` engine, used only when DATABASE_URL is set.
        self._engine_getter = engine_getter
        self.fallback = fallback or SlidingWindowLimiter(window_seconds=window_seconds, clock=clock)
        self._clock = clock
        self._lock = threading.Lock()
        # Once per window per process: the sweep of every expired row, and at
        # most one fallback warning (a database outage must not flood the log).
        self._last_sweep = clock()
        self._last_fallback_warning: float | None = None

    def check(self, key: str, limit: int) -> float | None:
        """Record a hit for ``key``; return None if allowed, else seconds to wait."""
        if limit <= 0:  # 0 (or negative) disables limiting entirely
            return None
        if self._engine_getter is None and not settings.database_url:
            # No shared store configured (local dev): the in-process window.
            return self.fallback.check(key, limit)
        try:
            return self._check_shared(key, limit)
        except Exception:
            self._warn_fallback()
            return self.fallback.check(key, limit)

    def _check_shared(self, key: str, limit: int) -> float | None:
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        params = {"key_hash": key_hash, "window_seconds": float(self._window)}
        engine = self._engine_getter() if self._engine_getter else get_engine(role="api")
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key_hash))"),
                {"namespace": RATE_LIMIT_LOCK_NAMESPACE, "key_hash": key_hash},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_rate_limit_hits
                    WHERE key_hash = :key_hash
                      AND hit_at <= now() - :window_seconds * interval '1 second'
                    """
                ),
                params,
            )
            window = connection.execute(
                text(
                    """
                    SELECT
                        count(*) AS hits,
                        EXTRACT(EPOCH FROM (
                            min(hit_at) + :window_seconds * interval '1 second' - now()
                        )) AS retry_after
                    FROM roomrate_rate_limit_hits
                    WHERE key_hash = :key_hash
                    """
                ),
                params,
            ).mappings().one()
            if int(window["hits"] or 0) >= limit:
                return max(float(window["retry_after"] or 0.0), 0.0)
            connection.execute(
                text("INSERT INTO roomrate_rate_limit_hits (key_hash, hit_at) VALUES (:key_hash, now())"),
                {"key_hash": key_hash},
            )
            if self._sweep_due():
                # Keys that never come back would otherwise leave their last
                # hits behind for good; the hit_at index keeps this cheap.
                connection.execute(
                    text(
                        """
                        DELETE FROM roomrate_rate_limit_hits
                        WHERE hit_at <= now() - :window_seconds * interval '1 second'
                        """
                    ),
                    params,
                )
        return None

    def _sweep_due(self) -> bool:
        now = self._clock()
        with self._lock:
            if now - self._last_sweep < self._window:
                return False
            self._last_sweep = now
            return True

    def _warn_fallback(self) -> None:
        now = self._clock()
        with self._lock:
            if self._last_fallback_warning is not None and now - self._last_fallback_warning < self._window:
                return
            self._last_fallback_warning = now
        logger.warning(
            "Shared rate limit unavailable; counting per process until the database answers",
            exc_info=True,
        )


def client_key(scope: dict) -> str:
    """Cheap per-caller bucket key derived only from credentials, never a hint.

    Order matters, and every branch must be something the caller cannot vary
    freely while keeping the same identity:

    * Bearer token -> a hash of the token. Stable per session and per user, so
      one browser cannot escape its bucket. Hashed (not stored raw) because
      bucket keys live in memory and reach logs.
    * ``X-API-Key`` + ``X-RoomRate-Account-ID`` -> the account id. Only trusted
      internal/server-side callers hold the key, and auth honours that header
      exactly on this path.
    * Otherwise the client IP. The FIRST ``X-Forwarded-For`` value is used on
      the assumption that exactly one trusted reverse proxy fronts the API and
      controls that header; without a proxy we fall back to the socket peer.

    ``X-RoomRate-Account-ID`` alone is NOT a key: the auth layer ignores it
    whenever a bearer token is present, so honouring it here let a client mint
    a fresh bucket per request by sending a random UUID and never hit a limit.
    """
    headers = Headers(scope=scope)
    authorization = (headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[len("bearer "):].strip()
        if token:
            return f"token:{hashlib.sha256(token.encode()).hexdigest()[:32]}"
    account_id = (headers.get("x-roomrate-account-id") or "").strip()
    if account_id and (headers.get("x-api-key") or "").strip():
        return f"account:{account_id}"
    forwarded_for = (headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return f"ip:{forwarded_for.split(',')[0].strip()}"
    client = scope.get("client")
    return f"ip:{client[0]}" if client else "ip:unknown"


class RateLimitMiddleware:
    """Pure-ASGI guard: 429 + Retry-After for over-limit expensive POSTs.

    The default limiter is the shared PostgreSQL window when a database is
    configured, else the in-process one. The check runs in the threadpool:
    the shared one is a database round trip that must not block the event
    loop.
    """

    def __init__(
        self,
        app: Any,
        limiter: SlidingWindowLimiter | PostgresSlidingWindowLimiter | None = None,
        limit_getter: Callable[[], int] | None = None,
    ):
        self.app = app
        if limiter is None:
            limiter = PostgresSlidingWindowLimiter() if settings.database_url else SlidingWindowLimiter()
        self.limiter = limiter
        # The limit is read per request (not captured at construction) so the
        # settings object stays the single source of truth and tests can
        # monkeypatch it.
        self._limit = limit_getter or (lambda: settings.rate_limit_per_minute)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope["path"].rstrip("/") not in RATE_LIMITED_POST_PATHS
        ):
            await self.app(scope, receive, send)
            return
        retry_after = await run_in_threadpool(self.limiter.check, client_key(scope), self._limit())
        if retry_after is not None:
            response = JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(max(1, ceil(retry_after)))},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
