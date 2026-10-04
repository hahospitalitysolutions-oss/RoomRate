"""Neon Auth (Better Auth) token verification and provider selection."""

import asyncio
import time
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

from api.config import settings
from api.dependencies import get_auth_service, resolve_token_account
from api.services import auth_service as auth_module
from api.services.auth_service import NeonAuthService, SupabaseAuthService

AUTH_URL = "https://ep-test.neonauth.c-6.eu-central-1.aws.neon.tech/neondb/auth"
KID = "neon-kid-1"
PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()


def _public_jwk() -> dict:
    jwk = jwt.algorithms.OKPAlgorithm.to_jwk(PRIVATE_KEY.public_key(), as_dict=True)
    return {**jwk, "kid": KID, "alg": "EdDSA", "use": "sig"}


def _token(private_key=PRIVATE_KEY, kid=KID, algorithm="EdDSA", **overrides) -> str:
    """A Better Auth JWT: the session user plus sub/iss/aud/iat/exp."""
    now = int(time.time())
    claims = {
        "id": "neon-user-1",
        "sub": "neon-user-1",
        "email": "owner@example.com",
        "emailVerified": True,
        "name": "Owner",
        "iss": AUTH_URL,
        "aud": AUTH_URL,
        "iat": now,
        "exp": now + 900,
        **overrides,
    }
    claims = {key: value for key, value in claims.items() if value is not None}
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": kid})


@pytest.fixture
def neon_settings(monkeypatch):
    """Neon Auth configured, the JWKS endpoint stubbed, a fresh key cache."""
    fetched_urls: list[str] = []

    class FakeJWKClient:
        def __init__(self, url, cache_keys=True):
            fetched_urls.append(url)

        def get_signing_key_from_jwt(self, token):
            if jwt.get_unverified_header(token).get("kid") != KID:
                raise jwt.PyJWKClientError("unknown kid")
            return jwt.PyJWK(_public_jwk())

    monkeypatch.setattr(settings, "neon_auth_url", AUTH_URL)
    monkeypatch.setattr(settings, "neon_auth_jwks_url", "")
    monkeypatch.setattr(settings, "neon_auth_jwt_issuer", "")
    monkeypatch.setattr(settings, "neon_auth_jwt_audience", "")
    monkeypatch.setattr(auth_module.jwt, "PyJWKClient", FakeJWKClient)
    monkeypatch.setattr(NeonAuthService, "_jwks_cache", auth_module._JWKSCache())
    return fetched_urls


def _verify(token: str):
    return asyncio.run(NeonAuthService().verify_access_token(token))


def test_eddsa_token_verifies_against_the_project_jwks(neon_settings):
    user = _verify(_token())

    assert user.auth_subject == "neon-user-1"
    assert user.email == "owner@example.com"
    assert user.display_name == "Owner"
    assert user.auth_provider == "neon_auth"
    assert neon_settings == [f"{AUTH_URL}/.well-known/jwks.json"]


def test_a_token_signed_by_another_key_is_rejected(neon_settings):
    forged = _token(private_key=ed25519.Ed25519PrivateKey.generate())

    with pytest.raises(HTTPException) as exc_info:
        _verify(forged)

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [{"exp": int(time.time()) - 3600}, {"sub": None}, {"exp": None}],
    ids=["expired", "no-sub", "no-exp"],
)
def test_expired_or_incomplete_tokens_are_rejected(neon_settings, overrides):
    with pytest.raises(HTTPException) as exc_info:
        _verify(_token(**overrides))

    assert exc_info.value.status_code == 401


def test_an_hmac_token_is_rejected_without_any_key_lookup(neon_settings):
    """No shared secret exists in Neon Auth: an HS256 token is a forgery shape."""
    forged = jwt.encode({"sub": "x", "exp": int(time.time()) + 60}, "guessed-shared-secret-of-thirty-two-bytes", algorithm="HS256")

    with pytest.raises(HTTPException) as exc_info:
        _verify(forged)

    assert exc_info.value.status_code == 401
    assert neon_settings == []


def test_issuer_and_audience_are_enforced_when_configured(neon_settings, monkeypatch):
    monkeypatch.setattr(settings, "neon_auth_jwt_issuer", AUTH_URL)
    monkeypatch.setattr(settings, "neon_auth_jwt_audience", AUTH_URL)

    assert _verify(_token()).auth_subject == "neon-user-1"
    for overrides in ({"iss": "https://elsewhere.example"}, {"aud": "https://elsewhere.example"}):
        with pytest.raises(HTTPException) as exc_info:
            _verify(_token(**overrides))
        assert exc_info.value.status_code == 401


def test_unconfigured_neon_auth_is_a_server_error(monkeypatch):
    monkeypatch.setattr(settings, "neon_auth_url", "")
    monkeypatch.setattr(settings, "neon_auth_jwks_url", "")

    with pytest.raises(HTTPException) as exc_info:
        _verify(_token())

    assert exc_info.value.status_code == 500


def test_the_verifier_follows_the_configuration(monkeypatch):
    monkeypatch.setattr(settings, "neon_auth_jwks_url", "")
    monkeypatch.setattr(settings, "neon_auth_url", "")
    assert isinstance(get_auth_service(), SupabaseAuthService)

    monkeypatch.setattr(settings, "neon_auth_url", AUTH_URL)
    assert isinstance(get_auth_service(), NeonAuthService)


def test_a_neon_identity_gets_its_own_provider_on_the_account(neon_settings):
    class RecordingAccounts:
        calls: list[dict] = []

        def get_or_create_account_for_identity(self, **kwargs):
            self.calls.append(kwargs)
            return UUID("00000000-0000-0000-0000-000000000777")

    accounts = RecordingAccounts()
    context = asyncio.run(resolve_token_account(_token(), NeonAuthService(), accounts))

    assert accounts.calls == [{
        "auth_provider": "neon_auth",
        "auth_subject": "neon-user-1",
        "email": "owner@example.com",
        "display_name": "Owner",
    }]
    assert context.auth_provider == "neon_auth"
    assert context.account_id == UUID("00000000-0000-0000-0000-000000000777")


def test_a_neon_token_at_a_supabase_only_api_names_the_missing_setting(monkeypatch):
    """Not a 401: that would sign the user out in a loop instead of naming the fix."""
    monkeypatch.setattr(settings, "neon_auth_url", "")
    monkeypatch.setattr(settings, "neon_auth_jwks_url", "")
    monkeypatch.setattr(settings, "supabase_url", "https://example.supabase.co")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_auth_service().verify_access_token(_token()))

    assert exc_info.value.status_code == 500
    assert "NEON_AUTH_URL" in exc_info.value.detail


def test_an_api_with_no_login_settings_names_neon_auth_url(monkeypatch):
    for name in ("neon_auth_url", "neon_auth_jwks_url", "supabase_url", "supabase_jwks_url", "supabase_jwt_secret"):
        monkeypatch.setattr(settings, name, "")

    assert not auth_module.login_verification_configured()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_auth_service().verify_access_token(_token()))

    assert exc_info.value.status_code == 500
    assert "NEON_AUTH_URL" in exc_info.value.detail
    assert "Supabase" not in exc_info.value.detail
