"""Static neighbouring-area defaults and their onboarding endpoint (spec §3.1)."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context
from api.main import app
from api.services.nearby_destinations import default_nearby_destinations

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
FALIRAKI_NEARBY = ["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"]


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("destination", ["Φαληράκι", "faliraki", " Faliraki "])
def test_faliraki_defaults_resolve_through_every_alias(destination):
    assert default_nearby_destinations(destination) == FALIRAKI_NEARBY


@pytest.mark.parametrize("destination", ["Rhodes", "Ρόδος", "Nowhere", "", None])
def test_other_destinations_have_no_defaults(destination):
    assert default_nearby_destinations(destination) == []


def test_defaults_are_a_fresh_list_each_call():
    """Callers may append chips; the static table must never change."""
    default_nearby_destinations("Φαληράκι").append("Λίνδος")

    assert default_nearby_destinations("Φαληράκι") == FALIRAKI_NEARBY


def test_nearby_destinations_endpoint_returns_the_contract_shape():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    client = TestClient(app)

    response = client.get("/api/v1/onboarding/nearby-destinations", params={"destination": "Φαληράκι"})

    assert response.status_code == 200
    assert response.json() == {"destination": "Φαληράκι", "canonical": "faliraki", "nearby": FALIRAKI_NEARBY}


def test_nearby_destinations_endpoint_rejects_a_blank_destination():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    client = TestClient(app)

    assert client.get("/api/v1/onboarding/nearby-destinations", params={"destination": "   "}).status_code == 422
    assert client.get("/api/v1/onboarding/nearby-destinations").status_code == 422
