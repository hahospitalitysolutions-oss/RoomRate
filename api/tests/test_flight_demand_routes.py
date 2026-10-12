"""The flight signal in the price advisor, and GET /api/v1/demand/flights."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_ferry_repository,
    get_flight_repository,
    get_market_service,
    get_onboarding_repository,
    get_price_history_repository,
    get_price_recommendation_agent,
    get_price_recommendation_audit_repository,
)
from api.main import app
from api.tests.test_ferry_demand_routes import (
    ACCOUNT_ID,
    CHECK_IN,
    OWNED_PROPERTY,
    OWNED_PROPERTY_ID,
    TODAY,
    CapturingAgent,
    FakeAudit,
    FakeFerryRepository,
    FakeHistory,
    FakeMarket,
    FakeOnboardingRepository,
)

# Faliraki, Rhodes.
ON_RHODES = {**OWNED_PROPERTY, "latitude": 36.34, "longitude": 28.20}
WEEK_AGO = TODAY - timedelta(days=7)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "ferry_demand_enabled", False)
    monkeypatch.setattr(settings, "flight_demand_enabled", True)
    monkeypatch.setattr(settings, "aerodatabox_api_key", "test-key")
    yield
    app.dependency_overrides.clear()


def flight_row(travel_date, observed_on, arrivals):
    return {
        "airport_iata": "RHO", "travel_date": travel_date, "window_start_hour": 8, "window_end_hour": 20,
        "observed_on": observed_on, "arrivals": arrivals, "airlines": 9,
        "origins": {"MAN": {"name": "Manchester", "flights": 5}},
    }


class FakeFlightRepository:
    def __init__(self, rows=(), fail=False):
        self.rows = list(rows)
        self.fail = fail
        self.calls = []

    def observation_history(self, airport_iata, *, date_from, date_to, observed_since):
        self.calls.append((airport_iata, date_from, date_to))
        if self.fail:
            raise RuntimeError("database down")
        return [row for row in self.rows if date_from <= row["travel_date"] <= date_to]


def busy_flights():
    return [flight_row(CHECK_IN, WEEK_AGO, 40), flight_row(CHECK_IN, TODAY, 46)]


def recommend(flight_repository, owned_property=ON_RHODES):
    agent = CapturingAgent()
    audit = FakeAudit()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit
    app.dependency_overrides[get_price_history_repository] = lambda: FakeHistory()
    app.dependency_overrides[get_onboarding_repository] = lambda: FakeOnboardingRepository(owned_property)
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent
    app.dependency_overrides[get_market_service] = lambda: FakeMarket()
    app.dependency_overrides[get_ferry_repository] = lambda: FakeFerryRepository()
    app.dependency_overrides[get_flight_repository] = lambda: flight_repository
    response = TestClient(app).post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": CHECK_IN.isoformat(),
            "check_out": (CHECK_IN + timedelta(days=2)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), agent, audit


def test_the_advisor_sees_the_flight_signal_of_the_island_airport():
    repository = FakeFlightRepository(busy_flights())

    body, agent, audit = recommend(repository)

    assert repository.calls[0][0] == "RHO"
    flights = agent.contexts[0].demand_signals["flights"]
    assert (flights["level"], flights["flights_added"], flights["airport_iata"]) == ("high", 6, "RHO")
    assert "ferries" not in agent.contexts[0].demand_signals
    assert body["demand_signals"]["flights"]["summary"].startswith("Αφίξεις στο αεροδρόμιο RHO (Ρόδος)")
    assert body["demand_signals"]["ferries"] is None
    assert audit.inserts[0]["request_payload"]["flight_signal"] == {"level": "high", "observed_on": str(TODAY)}


def test_unknown_flights_reach_the_owner_but_not_the_model():
    body, agent, _ = recommend(FakeFlightRepository([flight_row(CHECK_IN, TODAY, 40)]))

    assert agent.contexts[0].demand_signals is None
    assert body["demand_signals"]["flights"]["level"] == "unknown"


def test_no_flight_signal_off_an_island_airport_without_a_key_or_on_failure(monkeypatch):
    repository = FakeFlightRepository(busy_flights())
    on_symi = {**OWNED_PROPERTY, "latitude": 36.61, "longitude": 27.84}

    assert recommend(repository, on_symi)[0]["demand_signals"] is None
    assert recommend(repository, OWNED_PROPERTY)[0]["demand_signals"] is None  # no coordinates
    assert recommend(FakeFlightRepository(fail=True))[0]["recommendation"]["recommended_price_eur"] == 110.0
    assert repository.calls == []

    monkeypatch.setattr(settings, "aerodatabox_api_key", "")
    body, _, audit = recommend(repository)
    assert body["demand_signals"] is None and repository.calls == []
    assert audit.inserts[0]["request_payload"]["flight_signal"] is None


def get_flights(flight_repository, owned_property=ON_RHODES):
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_repository] = lambda: FakeOnboardingRepository(owned_property)
    app.dependency_overrides[get_flight_repository] = lambda: flight_repository
    return TestClient(app).get("/api/v1/demand/flights", params={"owned_property_id": str(OWNED_PROPERTY_ID)})


def test_flights_endpoint_lists_the_collected_days():
    response = get_flights(FakeFlightRepository(busy_flights()))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["airport_iata"], body["island"]) == ("flights", "RHO", "Ρόδος")
    assert body["days"] == [
        {
            "travel_date": CHECK_IN.isoformat(), "arrivals": 46, "airlines": 9, "arrivals_change_pct": 15.0,
            "flights_added": 6, "compared_with": WEEK_AGO.isoformat(), "top_origins": ["Manchester"],
            "level": "high",
        }
    ]
    assert body["observed_on"] == TODAY.isoformat()


def test_flights_endpoint_without_an_airport_or_a_key(monkeypatch):
    repository = FakeFlightRepository(busy_flights())

    symi = get_flights(repository, {**OWNED_PROPERTY, "latitude": 36.61, "longitude": 27.84}).json()
    assert (symi["status"], symi["airport_iata"], symi["days"]) == ("no_airport", None, [])

    monkeypatch.setattr(settings, "aerodatabox_api_key", "")
    off = get_flights(repository).json()
    assert (off["status"], off["airport_iata"], off["days"]) == ("off", "RHO", [])
    assert repository.calls == []


def test_flights_endpoint_404_for_a_property_not_of_this_account():
    response = get_flights(FakeFlightRepository(), owned_property=None)

    assert response.status_code == 404
