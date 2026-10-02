from uuid import UUID
import asyncio
import time

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api.dependencies import build_account_context, get_account_context
from api.config import settings
from api.services.auth_service import SupabaseAuthService, SupabaseAuthUser


def test_build_account_context_uses_header_account_id():
    context = build_account_context(
        api_key="test-key",
        account_id_header="00000000-0000-0000-0000-000000000123",
    )

    assert context.account_id == UUID("00000000-0000-0000-0000-000000000123")
    # The api-key path never carries a verified identity.
    assert context.auth_subject is None


def test_build_account_context_falls_back_to_default_account_id():
    context = build_account_context(api_key="test-key", account_id_header=None)

    assert context.account_id == UUID("00000000-0000-0000-0000-000000000001")


def test_build_account_context_rejects_invalid_account_id():
    with pytest.raises(HTTPException) as exc_info:
        build_account_context(api_key="test-key", account_id_header="not-a-uuid")

    assert exc_info.value.status_code == 422


class FakeAuthService:
    async def verify_access_token(self, token: str) -> SupabaseAuthUser:
        assert token == "valid-token"
        return SupabaseAuthUser(
            auth_subject="supabase-user-123",
            email="owner@example.com",
            display_name="Owner",
        )


class FakeAccountsRepository:
    def get_or_create_account_for_identity(self, **kwargs):
        assert kwargs == {
            "auth_provider": "supabase",
            "auth_subject": "supabase-user-123",
            "email": "owner@example.com",
            "display_name": "Owner",
        }
        return UUID("00000000-0000-0000-0000-000000000999")


def test_get_account_context_resolves_supabase_bearer_token():
    context = asyncio.run(
        get_account_context(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token"),
            api_key=None,
            account_id_header=None,
            auth_service=FakeAuthService(),
            accounts_repository=FakeAccountsRepository(),
        )
    )

    assert context.account_id == UUID("00000000-0000-0000-0000-000000000999")
    assert context.auth_provider == "supabase"
    assert context.auth_subject == "supabase-user-123"
    assert context.email == "owner@example.com"


def test_get_account_context_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            get_account_context(
                credentials=None,
                api_key=None,
                account_id_header=None,
                    auth_service=FakeAuthService(),
                accounts_repository=FakeAccountsRepository(),
            )
        )

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Local Supabase JWT verification (Phase F). Tokens are validated in-process —
# no network round-trip — so these mint self-signed tokens and assert verify +
# identity extraction, and that tampered/expired/bad-aud tokens raise 401.
# ---------------------------------------------------------------------------

# >= 32 bytes so PyJWT does not emit an InsecureKeyLengthWarning for HS256.
HS256_SECRET = "test-supabase-jwt-secret-0123456789abcdef"


def _hs256_token(secret=HS256_SECRET, *, exp_offset=3600, aud="authenticated", **extra):
    """Mint an HS256 Supabase-shaped access token for the verifier tests."""
    now = int(time.time())
    claims = {
        "sub": "supabase-user-123",
        "email": "owner@example.com",
        "user_metadata": {"name": "Owner"},
        "aud": aud,
        "iat": now,
        "exp": now + exp_offset,
        **extra,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def test_verify_access_token_hs256_extracts_identity(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", HS256_SECRET)
    monkeypatch.setattr(settings, "supabase_jwt_audience", "authenticated")

    user = asyncio.run(SupabaseAuthService().verify_access_token(_hs256_token()))

    assert user.auth_subject == "supabase-user-123"
    assert user.email == "owner@example.com"
    assert user.display_name == "Owner"


def test_verify_access_token_hs256_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", HS256_SECRET)
    token = _hs256_token(secret="a-different-secret-0123456789abcdefghij")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(SupabaseAuthService().verify_access_token(token))

    assert exc_info.value.status_code == 401


def test_verify_access_token_hs256_rejects_expired(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", HS256_SECRET)
    # Expired well beyond the 60s clock-skew leeway so it still rejects.
    token = _hs256_token(exp_offset=-300)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(SupabaseAuthService().verify_access_token(token))

    assert exc_info.value.status_code == 401


def test_verify_access_token_hs256_rejects_bad_audience(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", HS256_SECRET)
    monkeypatch.setattr(settings, "supabase_jwt_audience", "authenticated")
    token = _hs256_token(aud="some-other-audience")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(SupabaseAuthService().verify_access_token(token))

    assert exc_info.value.status_code == 401


def test_verify_access_token_raises_500_when_unconfigured(monkeypatch):
    # No secret and no JWKS/Supabase URL: verification cannot be performed.
    monkeypatch.setattr(settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(settings, "supabase_jwks_url", "")
    monkeypatch.setattr(settings, "supabase_url", "")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(SupabaseAuthService().verify_access_token(_hs256_token()))

    assert exc_info.value.status_code == 500


def test_verify_access_token_jwks_only_rejects_hs256_token_with_401(monkeypatch):
    # Algorithm-confusion forgery: JWKS configured but no shared HMAC secret, and
    # the attacker supplies an HS256-signed token. The HMAC branch has no key
    # source, but the OTHER (asymmetric) scheme IS configured, so this is an
    # invalid TOKEN (401), not a server-config error (500).
    monkeypatch.setattr(settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(settings, "supabase_jwks_url", "https://example.supabase.co/jwks")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(SupabaseAuthService().verify_access_token(_hs256_token()))

    assert exc_info.value.status_code == 401


def test_verify_access_token_jwks_rs256_verifies_and_rotates_on_unknown_kid(monkeypatch):
    # Generate an RSA keypair, build a JWKS, and verify an RS256 token against
    # a stubbed JWKS fetch. A first token with an unknown kid forces exactly one
    # JWKS refresh (rotation), after which verification succeeds.
    from cryptography.hazmat.primitives.asymmetric import rsa

    from api.services import auth_service as auth_module

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "test-kid-1"

    def _rs256_token(**extra):
        now = int(time.time())
        claims = {
            "sub": "supabase-user-456",
            "email": "rs@example.com",
            "user_metadata": {"full_name": "RS Owner"},
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
            **extra,
        }
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})

    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk["kid"] = kid
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"

    fetch_calls = {"count": 0}

    class FakeJWKClient:
        """Stubbed PyJWKClient modeling the JWKS endpoint.

        The cache rebuilds the client to force a JWKS re-fetch on an unknown
        ``kid``; each new client counts one fetch. The first fetch predates the
        rotated key (kid absent -> error); the refreshed fetch carries it.
        """

        def __init__(self, url, cache_keys=True):
            self.url = url
            fetch_calls["count"] += 1
            self._has_key = fetch_calls["count"] >= 2

        def get_signing_key_from_jwt(self, token):
            if not self._has_key:
                raise jwt.PyJWKClientError("unknown kid")
            return jwt.PyJWK(public_jwk)

    monkeypatch.setattr(settings, "supabase_jwks_url", "https://example.supabase.co/jwks")
    monkeypatch.setattr(settings, "supabase_jwt_audience", "authenticated")
    monkeypatch.setattr(auth_module.jwt, "PyJWKClient", FakeJWKClient)
    # Fresh cache so the rate-limit clock starts at zero for this test.
    monkeypatch.setattr(SupabaseAuthService, "_jwks_cache", auth_module._JWKSCache())

    user = asyncio.run(SupabaseAuthService().verify_access_token(_rs256_token()))

    assert user.auth_subject == "supabase-user-456"
    assert user.display_name == "RS Owner"
    # Two JWKS fetches: the initial (kid absent) plus exactly one refresh.
    assert fetch_calls["count"] == 2


def test_verify_access_token_jwks_unknown_kid_flood_is_rate_limited(monkeypatch):
    # Dependency-DoS guard: a flood of forged-``kid`` tokens within one refresh
    # window must trigger a BOUNDED number of JWKS fetches (<= 2), not one per
    # token. Each token carries a distinct unknown kid; the stubbed JWKS never
    # matches any kid, so every lookup is a cache miss. The fix keeps
    # ``_last_refresh_at`` across the null-and-rebuild, so after the first
    # refresh the window guard rejects all later tokens with no further fetch.
    from cryptography.hazmat.primitives.asymmetric import rsa

    from api.services import auth_service as auth_module

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def _rs256_token_with_kid(kid):
        now = int(time.time())
        claims = {
            "sub": "supabase-user-789",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
        }
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})

    fetch_calls = {"count": 0}

    class AlwaysMissJWKClient:
        """Stub whose JWKS never contains the requested kid (always a miss)."""

        def __init__(self, url, cache_keys=True):
            self.url = url
            fetch_calls["count"] += 1

        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("unknown kid")

    monkeypatch.setattr(settings, "supabase_jwks_url", "https://example.supabase.co/jwks")
    monkeypatch.setattr(settings, "supabase_jwt_audience", "authenticated")
    monkeypatch.setattr(auth_module.jwt, "PyJWKClient", AlwaysMissJWKClient)
    # Fresh cache so the rate-limit clock starts at its sentinel for this test.
    monkeypatch.setattr(SupabaseAuthService, "_jwks_cache", auth_module._JWKSCache())

    service = SupabaseAuthService()
    for i in range(80):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(service.verify_access_token(_rs256_token_with_kid(f"forged-kid-{i}")))
        assert exc_info.value.status_code == 401

    # 80 forged-kid tokens within one window -> at most 2 JWKS fetches
    # (initial empty-cache build + one rate-limited refresh), NOT ~80+.
    assert fetch_calls["count"] <= 2
