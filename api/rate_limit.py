"""In-process rate limiting for the expensive mutation endpoints.

A sliding-window hit counter per caller key, guarded by a plain
``threading.Lock`` (the critical section is a deque prune/append — cheap).

NOTE: state is PER PROCESS. Behind N gunicorn workers the effective
cluster-wide limit is up to N x RATE_LIMIT_PER_MINUTE. That is deliberate:
this guard blunts accidental floods and client retry loops without adding
Redis; exact global limiting belongs at the edge (reverse proxy/API gateway).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from collections.abc import Callable
from math import ceil
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from api.config import settings

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
    """Pure-ASGI guard: 429 + Retry-After for over-limit expensive POSTs."""

    def __init__(
        self,
        app: Any,
        limiter: SlidingWindowLimiter | None = None,
        limit_getter: Callable[[], int] | None = None,
    ):
        self.app = app
        self.limiter = limiter or SlidingWindowLimiter()
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
        retry_after = self.limiter.check(client_key(scope), self._limit())
        if retry_after is not None:
            response = JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(max(1, ceil(retry_after)))},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
