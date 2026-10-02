from uuid import UUID

from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context, get_accounts_repository
from api.main import app
from api.tests._fakes import FakeAccountsRepository

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000123")

# Keyed on the account id: the shared fake raises KeyError for any other
# account, preserving the old in-fake assertion that the route passes the
# authenticated account's id through.
#
# This dict only mirrors the shape; what get_account_overview actually
# reports (and when onboarding_complete is true) is pinned by the overview
# tests in test_accounts_repository.py.
ACCOUNT_OVERVIEW = {
    "owned_property_id": UUID("00000000-0000-0000-0000-000000000456"),
    "property_name": "Rea Hotel",
    "destination": "Φαληράκι",
    "raw_destination": "Faliraki",
    "canonical_destination": "faliraki",
    "selected_room_type_category": "double",
    "onboarding_complete": True,
}


def test_me_endpoint_returns_account_and_onboarding_status():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(
        account_id=ACCOUNT_ID,
        auth_provider="supabase",
        auth_subject="supabase-user",
        email="owner@example.com",
    )
    app.dependency_overrides[get_accounts_repository] = lambda: FakeAccountsRepository(
        overviews={ACCOUNT_ID: ACCOUNT_OVERVIEW}
    )
    client = TestClient(app)

    response = client.get("/api/v1/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == "00000000-0000-0000-0000-000000000123"
    assert payload["auth_provider"] == "supabase"
    assert payload["onboarding_complete"] is True
    assert payload["destination"] == "Φαληράκι"
    assert payload["selected_room_type_category"] == "double"

    app.dependency_overrides.clear()
