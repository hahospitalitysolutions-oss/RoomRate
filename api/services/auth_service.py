"""Local access-token verification: Neon Auth, or Supabase (Phase F).

Neon Auth (Better Auth managed by Neon) is used when ``NEON_AUTH_URL`` is set:
its tokens are verified against ``<NEON_AUTH_URL>/.well-known/jwks.json`` by
``NeonAuthService`` below. Without it, Supabase tokens are verified as
described here.

Supabase access tokens are JWTs. Previously this module verified them by calling
Supabase ``/auth/v1/user`` over HTTP on every request and caching the remote
result in-process for 60s — adding a network round-trip and a stale-result
window. This module now validates tokens **locally**: it checks the signature,
expiry (``exp``) and audience (``aud``) with PyJWT and never touches the network
on the hot path.

Signing scheme — auto-detected from the token header ``alg``:

* ``HS256`` (legacy Supabase projects with a shared JWT secret) → verified with
  ``settings.supabase_jwt_secret``.
* ``RS256`` / ``ES256`` (projects using asymmetric signing keys) → verified
  against the project's JWKS. The JWKS JSON is fetched once and the keys are
  cached in-process keyed by ``kid``; the cache is refreshed only when a token
  presents an unknown ``kid`` (and at most once every
  ``_JWKS_MIN_REFRESH_SECONDS``), never per request.

The public interface is unchanged: ``verify_access_token(token)`` returns a
``SupabaseAuthUser`` and raises ``HTTPException(401)`` on any invalid token, so
``resolve_account_context`` / ``get_account_context`` / the WebSocket handshake
and their test fakes keep working as-is.

When neither a JWT secret nor a resolvable JWKS URL is configured the service
cannot verify tokens; it raises the same 500 the old code raised for an
unconfigured Supabase, preserving the existing API-key-only dev/test behavior
(no token is supplied in those paths, so this branch is never hit there).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import jwt
from fastapi import HTTPException

from api.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupabaseAuthUser:
    """Verified identity from the configured auth provider."""

    auth_subject: str
    email: str | None = None
    display_name: str | None = None
    # roomrate_user_identities.auth_provider: one account per (provider, subject).
    auth_provider: str = "supabase"


class AccessTokenVerifier(Protocol):
    """What the account resolution needs from an auth provider."""

    async def verify_access_token(self, token: str) -> SupabaseAuthUser: ...


# Algorithms we accept. Supabase signs access tokens with one of these; the
# concrete algorithm is read from the token header and matched to a key source.
_HMAC_ALGORITHMS = frozenset({"HS256"})
_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})
_SUPPORTED_ALGORITHMS = _HMAC_ALGORITHMS | _ASYMMETRIC_ALGORITHMS

# A JWKS refresh is only triggered by an unknown ``kid`` and never more often
# than this, so a stream of bogus tokens cannot hammer the JWKS endpoint.
_JWKS_MIN_REFRESH_SECONDS = 60.0


# The one 500 for "this API cannot verify browser logins". It names the fix,
# because the usual cause is a developer's .env missing NEON_AUTH_URL (or an
# API started before it was added): the frontend shows it in Greek.
AUTH_NOT_CONFIGURED_DETAIL = (
    "Login is not configured on this API: set NEON_AUTH_URL in .env and restart the API."
)


def login_verification_configured() -> bool:
    """True when the API can verify browser tokens (Neon Auth or Supabase)."""
    return bool(
        settings.neon_auth_url
        or settings.neon_auth_jwks_url
        or settings.supabase_jwt_secret
        or settings.supabase_jwks_url
        or settings.supabase_url
    )


def _invalid_token(detail: str = "Invalid or expired access token") -> HTTPException:
    """The single 401 raised for every kind of token rejection."""
    return HTTPException(status_code=401, detail=detail)


def _resolved_jwks_url() -> str:
    """Configured JWKS URL, or the standard Supabase endpoint derived from the URL."""
    if settings.supabase_jwks_url:
        return settings.supabase_jwks_url
    if settings.supabase_url:
        return settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return ""


class _JWKSCache:
    """In-process JWKS signing-key cache, refreshed only on an unknown ``kid``.

    PyJWT's ``PyJWKClient`` already caches keys, but we wrap it so the refresh
    policy (refresh on cache-miss, rate-limited) and the network failure mode
    are explicit and testable. ``PyJWKClient.get_signing_key_from_jwt`` fetches
    and caches on first use and re-fetches on an unknown ``kid``; we add the
    rate limit so repeated bad ``kid`` values don't spam the endpoint.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._client_url: str | None = None
        # Sentinel ``-inf`` so the FIRST JWKS use always passes the window guard
        # (``now - (-inf) >= window``) while every later refresh is rate-limited.
        # This timestamp tracks JWKS *fetches* and must persist across a
        # null-and-rebuild of ``_client`` — it is never reset on rebuild.
        self._last_refresh_at: float = float("-inf")

    def _ensure_client(self, jwks_url: str) -> Any:
        # Build the client on first use, or rebuild if the configured URL
        # changed (e.g. tests patching settings). This only manages client
        # lifecycle; it must NOT touch ``_last_refresh_at`` (doing so would
        # reset the rate-limit clock and defeat the unknown-kid throttle).
        if self._client is None or self._client_url != jwks_url:
            self._client = jwt.PyJWKClient(jwks_url, cache_keys=True)
            self._client_url = jwks_url
        return self._client

    def get_signing_key(self, token: str, jwks_url: str) -> Any:
        """Return the PEM/public key for ``token``'s ``kid``; refresh on miss.

        On an unknown ``kid`` at most one JWKS re-fetch is performed per
        ``_JWKS_MIN_REFRESH_SECONDS`` window: a flood of forged-``kid`` tokens
        is rejected (401) without any further network call until the window
        elapses, while a genuinely-rotated ``kid`` is accepted within at most
        one window once the refresh fires.
        """
        client = self._ensure_client(jwks_url)
        try:
            return client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as exc:
            # Unknown kid (or empty cache). Only spend a JWKS fetch if the
            # rate-limit window has elapsed since the last one; otherwise reject
            # without touching the network so bad kids cannot hammer the JWKS
            # endpoint. ``_last_refresh_at`` persists across the rebuild below,
            # so a stream of distinct bad kids triggers at most one fetch/window.
            now = time.monotonic()
            if now - self._last_refresh_at < _JWKS_MIN_REFRESH_SECONDS:
                raise _invalid_token() from exc
            self._last_refresh_at = now
            # Force a fresh client so the JWKS JSON is re-fetched.
            self._client = None
            client = self._ensure_client(jwks_url)
            try:
                return client.get_signing_key_from_jwt(token).key
            except jwt.PyJWKClientError as retry_exc:
                raise _invalid_token() from retry_exc
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc


class SupabaseAuthService:
    """Validate Supabase access tokens locally for browser requests."""

    # Shared across instances so the fetched JWKS survives per-request service
    # construction (``get_auth_service`` returns a fresh instance each call).
    _jwks_cache = _JWKSCache()

    async def verify_access_token(self, token: str) -> SupabaseAuthUser:
        """Return verified Supabase user info for a bearer token.

        Validates signature, ``exp`` and ``aud`` locally. Raises
        ``HTTPException(401)`` on any invalid/expired/wrong-audience token and
        ``HTTPException(500)`` only when verification is not configured at all.
        """
        algorithm = self._token_algorithm(token)
        key = self._signing_key(token, algorithm)

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=settings.supabase_jwt_audience or None,
                # Tolerate minor clock skew on exp/nbf/iat (Supabase always
                # issues iat, so we also require it).
                leeway=timedelta(seconds=60),
                options={
                    "require": ["exp", "iat"],
                    "verify_aud": bool(settings.supabase_jwt_audience),
                },
            )
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc

        return self._user_from_claims(claims)

    @staticmethod
    def _token_algorithm(token: str) -> str:
        """Read and validate the token header's ``alg`` before any verification."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc
        algorithm = header.get("alg")
        if algorithm == "EdDSA":
            # Supabase never signs with EdDSA; Neon Auth (Better Auth) does. A
            # Neon login reaching an API without NEON_AUTH_URL is a
            # configuration gap, not an expired session: say so instead of a
            # 401 that would sign the user out in a loop.
            raise HTTPException(status_code=500, detail=AUTH_NOT_CONFIGURED_DETAIL)
        if algorithm not in _SUPPORTED_ALGORITHMS:
            raise _invalid_token()
        return algorithm

    def _signing_key(self, token: str, algorithm: str) -> Any:
        """Resolve the verification key for the token's algorithm.

        If the token's algorithm CLASS has no configured key source but the
        OTHER class IS configured, the token is treated as invalid (401) rather
        than a server-config error: this is the algorithm-confusion forgery
        shape (e.g. an attacker-supplied HS256 token against a JWKS-only
        deployment) and a 401 is the correct, non-misleading response. A 500
        "not configured" is raised only when NEITHER scheme is available — a
        genuine deployment misconfiguration.
        """
        has_secret = bool(settings.supabase_jwt_secret)
        jwks_url = _resolved_jwks_url()
        has_jwks = bool(jwks_url)
        if not has_secret and not has_jwks:
            raise self._not_configured()

        if algorithm in _HMAC_ALGORITHMS:
            if not has_secret:
                # JWKS-only deployment, attacker-supplied HMAC token: 401.
                raise _invalid_token()
            return settings.supabase_jwt_secret
        # Asymmetric: fetch (and cache) the public key from the project JWKS.
        if not has_jwks:
            # HMAC-only deployment, attacker-supplied asymmetric token: 401.
            raise _invalid_token()
        return self._jwks_cache.get_signing_key(token, jwks_url)

    @staticmethod
    def _not_configured() -> HTTPException:
        """Raised when no secret/JWKS is available to verify the token."""
        return HTTPException(status_code=500, detail=AUTH_NOT_CONFIGURED_DETAIL)

    @staticmethod
    def _user_from_claims(claims: dict) -> SupabaseAuthUser:
        """Extract the same identity fields the network path used to return."""
        auth_subject = claims.get("sub")
        if not auth_subject:
            raise _invalid_token("Invalid Supabase user payload")
        metadata = claims.get("user_metadata") or {}
        email = claims.get("email")
        display_name = metadata.get("full_name") or metadata.get("name") or email
        return SupabaseAuthUser(
            auth_subject=str(auth_subject),
            email=email,
            display_name=display_name,
        )


# Neon Auth signs with the Better Auth JWT plugin: EdDSA (Ed25519) by default,
# or a configured asymmetric key pair. Never HMAC: there is no shared secret.
_NEON_ALGORITHMS = frozenset({"EdDSA", "ES256", "RS256", "PS256"})
NEON_AUTH_PROVIDER = "neon_auth"


def neon_auth_enabled() -> bool:
    """True when browser tokens come from Neon Auth instead of Supabase."""
    return bool(settings.neon_auth_url or settings.neon_auth_jwks_url)


def _neon_jwks_url() -> str:
    """Configured JWKS URL, or the Neon Auth endpoint derived from NEON_AUTH_URL."""
    if settings.neon_auth_jwks_url:
        return settings.neon_auth_jwks_url
    if settings.neon_auth_url:
        return settings.neon_auth_url.rstrip("/") + "/.well-known/jwks.json"
    return ""


class NeonAuthService:
    """Validate Neon Auth (Better Auth) JWTs locally against the project JWKS.

    The JWT is what the browser SDK returns as the session's access token; it
    lasts 15 minutes and the SDK renews it. Signature, ``exp`` and ``sub`` are
    always checked; ``iss`` and ``aud`` (Better Auth sets both to its base URL)
    only when NEON_AUTH_JWT_ISSUER / NEON_AUTH_JWT_AUDIENCE are set. The JWKS
    is per project, so a valid signature already means "issued by this
    project's Neon Auth".
    """

    _jwks_cache = _JWKSCache()

    async def verify_access_token(self, token: str) -> SupabaseAuthUser:
        jwks_url = _neon_jwks_url()
        if not jwks_url:
            raise HTTPException(status_code=500, detail=AUTH_NOT_CONFIGURED_DETAIL)
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc
        if algorithm not in _NEON_ALGORITHMS:
            raise _invalid_token()
        key = self._jwks_cache.get_signing_key(token, jwks_url)
        audience = settings.neon_auth_jwt_audience or None
        issuer = settings.neon_auth_jwt_issuer or None
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=audience,
                issuer=issuer,
                leeway=timedelta(seconds=60),
                options={
                    "require": ["exp", "sub"],
                    "verify_aud": audience is not None,
                    "verify_iss": issuer is not None,
                },
            )
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc
        auth_subject = claims.get("sub")
        if not auth_subject:
            raise _invalid_token("Invalid Neon Auth user payload")
        email = claims.get("email")
        return SupabaseAuthUser(
            auth_subject=str(auth_subject),
            email=email,
            display_name=claims.get("name") or email,
            auth_provider=NEON_AUTH_PROVIDER,
        )
