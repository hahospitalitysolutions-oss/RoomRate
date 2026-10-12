"""Pure-ASGI middlewares for the RoomRate API.

Written against the raw ASGI protocol (not BaseHTTPMiddleware) so responses
stream through unbuffered and the per-request overhead stays at a couple of
header-dict operations.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders

from api.logging_utils import request_id_var

# Security headers applied to every HTTP response. No Content-Security-Policy
# is set on purpose: this API returns JSON only (no HTML documents are ever
# rendered from it), so a CSP would add response weight without protecting
# anything — the Angular frontend sets its own CSP where it is hosted.
BASE_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
)

# Two years with subdomains (the hstspreload.org recommendation). Only sent in
# production: HSTS from localhost would poison the browser's HSTS cache for
# the whole host and break every plain-HTTP dev server on this machine.
HSTS_HEADER: tuple[str, str] = (
    "Strict-Transport-Security",
    "max-age=63072000; includeSubDomains",
)


class SecurityHeadersMiddleware:
    """Attach standard security headers to every HTTP response.

    ``include_hsts`` is decided once at wiring time (production only).
    Existing headers are never clobbered — a route that sets its own value
    for one of these wins (``setdefault`` semantics).
    """

    def __init__(self, app: Any, include_hsts: bool = False):
        self.app = app
        self._headers = BASE_SECURITY_HEADERS + ((HSTS_HEADER,) if include_hsts else ())

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._headers:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


# Inbound X-Request-ID values are echoed back verbatim, so cap their length
# to keep a hostile client from inflating every log line and response.
MAX_REQUEST_ID_LENGTH = 128


class RequestIDMiddleware:
    """Per-request correlation id, honoring an inbound X-Request-ID.

    The id (client-provided or a fresh UUID) is stored in a contextvar for the
    request's lifetime — RequestIdFilter injects it into every log record,
    including from sync endpoints in the threadpool — and echoed back as the
    ``X-Request-ID`` response header so clients/proxies can correlate.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = Headers(scope=scope).get("x-request-id", "")
        request_id = inbound.strip()[:MAX_REQUEST_ID_LENGTH] or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).setdefault("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


class UnhandledErrorMiddleware:
    """Turn an unhandled exception into a JSON 500 that still gets CORS headers.

    Starlette's own 500 comes from ServerErrorMiddleware, outside
    CORSMiddleware, so it carries no Access-Control-Allow-Origin: the browser
    then reports a network failure and the app said "could not reach the
    RoomRate API" for what was really a server error. Installed inside CORS,
    this answers with a real 500 the frontend can read. The exception is still
    logged with its traceback, and reported to Sentry when it is configured.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def send_tracking(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception as exc:
            if response_started:
                raise
            logging.getLogger("api.errors").exception(
                "Unhandled error on %s %s", scope.get("method"), scope.get("path")
            )
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            body = b'{"detail":"Internal Server Error"}'
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            })
            await send({"type": "http.response.body", "body": body})
