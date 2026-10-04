import logging

import pytest

from api.config import (
    DEV_INTERNAL_API_KEY,
    Settings,
    is_production_env,
    validate_production_settings,
)


def make_settings(**overrides) -> Settings:
    """Build Settings with production-safe defaults, overridable per test.

    Every validated field is passed explicitly (init kwargs beat env vars in
    pydantic-settings) so ambient environment variables cannot leak in.
    """
    values = {
        "APP_ENV": "production",
        "ROOMRATE_PROCESS_ROLE": "api",
        "DATABASE_URL": "postgresql+psycopg2://user:pass@db:5432/roomrate",
        "INTERNAL_API_KEY": "a-sufficiently-long-production-key",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_JWT_SECRET": "",
        "SUPABASE_JWKS_URL": "",
        "CORS_ORIGINS": "https://app.example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


# ----------------------------------------------------------------------------
# is_production_env
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["production", "prod", "PRODUCTION", " Prod "])
def test_is_production_env_accepts_prod_spellings(app_env):
    assert is_production_env(app_env) is True


@pytest.mark.parametrize("app_env", ["development", "dev", "test", "staging", ""])
def test_is_production_env_rejects_non_prod(app_env):
    assert is_production_env(app_env) is False


# ----------------------------------------------------------------------------
# validate_production_settings
# ----------------------------------------------------------------------------


def test_dev_env_is_never_validated():
    # Everything wrong — but APP_ENV is development, so boot must proceed.
    bad_but_dev = make_settings(
        APP_ENV="development",
        DATABASE_URL="",
        INTERNAL_API_KEY=DEV_INTERNAL_API_KEY,
        SUPABASE_URL="",
    )
    validate_production_settings(bad_but_dev)  # must not raise


def test_production_with_good_settings_passes():
    validate_production_settings(make_settings())  # must not raise


def test_production_rejects_the_unscoped_legacy_rate_source():
    """room_rates has no account column: every account would read every market."""
    with pytest.raises(RuntimeError, match="ROOMRATE_RATE_SOURCE"):
        validate_production_settings(make_settings(ROOMRATE_RATE_SOURCE="legacy"))


def test_production_rejects_combined_api_and_worker_role():
    with pytest.raises(RuntimeError, match="ROOMRATE_PROCESS_ROLE"):
        validate_production_settings(make_settings(ROOMRATE_PROCESS_ROLE="all"))


def test_worker_role_does_not_require_browser_auth_but_requires_scheduler():
    worker = make_settings(
        ROOMRATE_PROCESS_ROLE="worker",
        INTERNAL_API_KEY=DEV_INTERNAL_API_KEY,
        SUPABASE_URL="",
        SUPABASE_JWT_SECRET="",
        SUPABASE_JWKS_URL="",
    )
    validate_production_settings(worker)

    with pytest.raises(RuntimeError, match="ROOMRATE_SCHEDULER_ENABLED"):
        validate_production_settings(
            make_settings(ROOMRATE_PROCESS_ROLE="worker", ROOMRATE_SCHEDULER_ENABLED=False)
        )


@pytest.mark.parametrize("app_env", ["prod", "PRODUCTION"])
def test_prod_alias_spellings_also_validate(app_env):
    with pytest.raises(RuntimeError):
        validate_production_settings(make_settings(APP_ENV=app_env, DATABASE_URL=""))


def test_all_violations_are_aggregated_in_one_error():
    bad = make_settings(
        DATABASE_URL="",
        INTERNAL_API_KEY=DEV_INTERNAL_API_KEY,  # dev default AND < 24 chars
        SUPABASE_URL="",
        SUPABASE_JWT_SECRET="",
        SUPABASE_JWKS_URL="",
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_production_settings(bad)

    message = str(exc_info.value)
    assert "DATABASE_URL" in message
    assert "dev default" in message
    assert "at least 24 characters" in message
    assert "SUPABASE_JWT_SECRET / SUPABASE_JWKS_URL / SUPABASE_URL" in message


def test_short_api_key_is_rejected_even_when_not_dev_default():
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_settings(make_settings(INTERNAL_API_KEY="short-but-unique"))
    assert "at least 24 characters" in str(exc_info.value)


def test_any_supabase_auth_source_satisfies_the_auth_check():
    for overrides in (
        {"SUPABASE_URL": "https://project.supabase.co"},
        {"SUPABASE_URL": "", "SUPABASE_JWT_SECRET": "shared-secret"},
        {"SUPABASE_URL": "", "SUPABASE_JWKS_URL": "https://x/jwks.json"},
    ):
        validate_production_settings(make_settings(**overrides))  # must not raise


def test_localhost_only_cors_warns_but_does_not_block_boot(caplog):
    localhost_only = make_settings(CORS_ORIGINS="http://localhost:4200,http://127.0.0.1:4200")

    with caplog.at_level(logging.WARNING, logger="api.config"):
        validate_production_settings(localhost_only)  # must not raise

    assert any("localhost" in record.message for record in caplog.records)


def test_mixed_cors_with_real_origin_does_not_warn(caplog):
    mixed = make_settings(CORS_ORIGINS="http://localhost:4200,https://app.example.com")

    with caplog.at_level(logging.WARNING, logger="api.config"):
        validate_production_settings(mixed)

    assert not [r for r in caplog.records if "CORS_ORIGINS" in r.message]


# ----------------------------------------------------------------------------
# .env encoding (Round 6 §6): the owner's .env is saved with a UTF-8 BOM
# ----------------------------------------------------------------------------


def test_settings_read_a_dotenv_saved_with_a_utf8_bom(tmp_path, monkeypatch):
    """With plain utf-8 the first key reads as '\ufeffROOMRATE_STALE_JOB_MINUTES'
    and silently never applies; the scraper already loads .env with utf-8-sig.
    """
    monkeypatch.delenv("ROOMRATE_STALE_JOB_MINUTES", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"\xef\xbb\xbfROOMRATE_STALE_JOB_MINUTES=42\n")

    loaded = Settings(_env_file=str(env_file))

    assert loaded.stale_job_minutes == 42


def test_settings_declare_the_bom_tolerant_env_file_encoding():
    assert Settings.model_config["env_file_encoding"] == "utf-8-sig"
