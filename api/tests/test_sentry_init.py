"""Pin the Sentry guard: monitoring stays off until SENTRY_DSN is configured.

The API must import, build its FastAPI app and run its whole test suite without
ever initializing an error-reporting client — a dev machine or CI run should
not ship exceptions to a third party. This pins both halves of that switch.
"""

import sentry_sdk

from api import main as api_main

# A syntactically valid DSN pointing at an unroutable host. sentry_sdk.init()
# performs no network I/O (the transport only sends when an event is captured,
# and this test captures none), so the host is never contacted.
DUMMY_DSN = "https://public@localhost.invalid/1"


def test_sentry_initializes_only_when_dsn_is_configured(monkeypatch):
    """No DSN -> no Sentry client; a DSN -> a configured, PII-free client."""
    original_client = sentry_sdk.get_client()
    try:
        if not api_main.settings.sentry_dsn:
            # The default developer/CI environment. api.main has already been
            # imported (and built `app`) by the time this module loads, so an
            # inactive client here proves import time did not initialize one.
            assert sentry_sdk.get_client().is_active() is False

        # Explicit blank DSN: the guard returns False and touches nothing.
        monkeypatch.setattr(api_main.settings, "sentry_dsn", "")
        assert api_main.init_sentry() is False
        assert sentry_sdk.get_client().is_active() is False

        # A configured DSN switches monitoring on, offline.
        monkeypatch.setattr(api_main.settings, "sentry_dsn", DUMMY_DSN)
        monkeypatch.setattr(api_main.settings, "sentry_traces_sample_rate", 0.25)
        monkeypatch.setattr(api_main.settings, "app_env", "production")
        assert api_main.init_sentry() is True

        client = sentry_sdk.get_client()
        assert client.is_active() is True
        assert client.dsn == DUMMY_DSN
        # environment follows APP_ENV so Sentry can separate prod from staging.
        assert client.options["environment"] == "production"
        assert client.options["traces_sample_rate"] == 0.25
        # Never ship request bodies, headers, cookies or user identifiers: this
        # service handles account ids and Supabase access tokens.
        assert client.options["send_default_pii"] is False
    finally:
        # Client.close() does NOT flip is_active() back in sentry-sdk 2.x, so
        # the global scope's client is restored explicitly; otherwise this test
        # would leave a live client behind for every test that follows.
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(original_client)
