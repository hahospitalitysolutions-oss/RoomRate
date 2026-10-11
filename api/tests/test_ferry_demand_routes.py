"""The ferry signal in the price advisor, and GET /api/v1/demand/ferries."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_ferry_repository,
    get_market_service,
    get_onboarding_repository,
    get_price_history_repository,
    get_price_recommendation_agent,
    get_price_recommendation_audit_repository,
)
from api.main import app
from api.schemas.agents import PriceRecommendation
from api.schemas.market import AgentDataQuality, AgentMeta, SmartAdvisorContext, SmartAdvisorSignals

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
ROUTE_ID = UUID("00000000-0000-0000-0000-0000000000a1")
TODAY = datetime.now(timezone.utc).date()
CHECK_IN = TODAY + timedelta(days=10)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "ferry_demand_enabled", True)
    yield
    app.dependency_overrides.clear()


OWNED_PROPERTY = {
    "id": OWNED_PROPERTY_ID,
    "display_name": "My Hotel",
    "canonical_destination": "rodos",
    "selected_room_type_category": "double",
}


class FakeOnboardingRepository:
    def __init__(self, owned_property=OWNED_PROPERTY):
        self.owned_property = owned_property
        self.calls = []

    def get_owned_property(self, account_id, owned_property_id):
        self.calls.append((account_id, owned_property_id))
        return self.owned_property

    def get_selected_room_type(self, account_id, owned_property_id):
        return {"room_type": "Double", "room_type_category": "double", "sample_price_per_night_eur": 100.0}


class FakeHistory:
    def fetch_price_series(self, **kwargs):
        return [
            {"rn": 1, "observed_at": f"{TODAY}T08:00:00+00:00", "property_id": UUID(int=index + 1),
             "hotel_name": name, "min_price": price}
            for index, (name, price) in enumerate([("A", 90.0), ("B", 110.0), ("C", 130.0)])
        ]

    def fetch_own_live_price(self, **kwargs):
        return None

    def fetch_latest_completed_run_id(self, **kwargs):
        return None

    def fetch_latest_run_agent_matches(self, **kwargs):
        return []


class FakeMarket:
    def get_smart_advisor_context(self, filters, my_hotel_name=None, origin=None, agent_matches=None):
        return SmartAdvisorContext(
            meta=AgentMeta(destination="rodos", market_stats={}),
            data_quality=AgentDataQuality(
                total_rows=3, total_competitors=3, rows_without_facilities=0,
                rows_without_coordinates=0, rows_without_rooms_left=0, facility_coverage_pct=100.0,
            ),
            pricing_signals=SmartAdvisorSignals(
                market_price_spread_eur=40.0, low_availability_competitors=[],
                free_cancellation_share_pct=0.0, breakfast_available_share_pct=0.0,
            ),
            time_context={},
            competitors=[],
        )


class CapturingAgent:
    model = "fake-model"

    def __init__(self):
        self.contexts = []

    def recommend(self, stats, advisor_context, own_cancellation_type=None):
        self.contexts.append(advisor_context)
        return PriceRecommendation(
            recommended_price_eur=110.0, price_range_low_eur=100.0, price_range_high_eur=120.0,
            confidence="low", reasoning="Βάση.", key_factors=["Διάμεσος 110 €"], source="statistical",
        )


class FakeAudit:
    def __init__(self):
        self.inserts = []

    def get_cached(self, account_id, request_hash, prompt_version):
        return None

    def count_today(self, account_id):
        return 0

    def insert(self, **kwargs):
        self.inserts.append(kwargs)
        return {"id": UUID(int=999), "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc)}


def ferry_row(travel_date, *, known=0, sold_out=0, price=60.0):
    return {
        "route_id": ROUTE_ID, "origin_port": "Piraeus", "destination_port": "Rhodes",
        "travel_date": travel_date, "observed_on": TODAY, "sailings": 2,
        "availability_known": known, "sold_out": sold_out,
        "min_price_eur": price, "median_price_eur": price,
    }


class FakeFerryRepository:
    def __init__(self, rows=(), profile=None, fail=False):
        self.rows = list(rows)
        self.profile = profile
        self.fail = fail
        self.profile_calls = []

    def latest_observations(self, canonical_destination, *, date_from, date_to, observed_since):
        if self.fail:
            raise RuntimeError("database down")
        return [row for row in self.rows if date_from <= row["travel_date"] <= date_to]

    def get_profile(self, canonical_destination):
        self.profile_calls.append(canonical_destination)
        return self.profile


def busy_rows():
    usual = [ferry_row(TODAY + timedelta(days=offset)) for offset in range(1, 7)]
    return usual + [
        ferry_row(CHECK_IN - timedelta(days=1), known=2, sold_out=2),
        ferry_row(CHECK_IN, known=2, sold_out=1),
    ]


def recommend(ferry_repository, agent=None, audit=None):
    agent = agent or CapturingAgent()
    audit = audit or FakeAudit()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit
    app.dependency_overrides[get_price_history_repository] = lambda: FakeHistory()
    app.dependency_overrides[get_onboarding_repository] = lambda: FakeOnboardingRepository()
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent
    app.dependency_overrides[get_market_service] = lambda: FakeMarket()
    app.dependency_overrides[get_ferry_repository] = lambda: ferry_repository
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


def test_the_advisor_sees_the_ferry_signal_and_the_answer_carries_it():
    body, agent, audit = recommend(FakeFerryRepository(busy_rows()))

    ferries = agent.contexts[0].demand_signals["ferries"]
    assert ferries["level"] == "high"
    assert ferries["sold_out"] == 3 and ferries["availability_known"] == 4
    assert body["demand_signals"]["ferries"]["level"] == "high"
    assert body["demand_signals"]["ferries"]["summary"].startswith("Πλοία προς Rhodes")
    # A new day's collection is a new request identity.
    assert audit.inserts[0]["request_payload"]["ferry_signal"] == {"level": "high", "observed_on": str(TODAY)}


def test_no_ferry_data_means_no_signal_anywhere():
    body, agent, audit = recommend(FakeFerryRepository())

    assert agent.contexts[0].demand_signals is None
    assert body["demand_signals"] is None
    assert audit.inserts[0]["request_payload"]["ferry_signal"] is None


def test_unknown_level_reaches_the_owner_but_not_the_model():
    far = [ferry_row(TODAY + timedelta(days=offset)) for offset in range(1, 4)]

    body, agent, _ = recommend(FakeFerryRepository(far))

    assert agent.contexts[0].demand_signals is None
    assert body["demand_signals"]["ferries"]["level"] == "unknown"


def test_a_ferry_read_failure_never_fails_the_recommendation():
    body, agent, _ = recommend(FakeFerryRepository(fail=True))

    assert body["recommendation"]["recommended_price_eur"] == 110.0
    assert body["demand_signals"] is None
    assert agent.contexts[0].demand_signals is None


def test_switched_off_the_ferry_data_is_not_read(monkeypatch):
    monkeypatch.setattr(settings, "ferry_demand_enabled", False)

    body, _, _ = recommend(FakeFerryRepository(fail=True))

    assert body["demand_signals"] is None


def get_ferries(ferry_repository, onboarding=None):
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding or FakeOnboardingRepository()
    app.dependency_overrides[get_ferry_repository] = lambda: ferry_repository
    return TestClient(app).get("/api/v1/demand/ferries", params={"owned_property_id": str(OWNED_PROPERTY_ID)})


def test_ferries_endpoint_lists_routes_and_days_of_the_own_destination():
    profile = {
        "canonical_destination": "rodos", "status": "ferry", "island_port": "Rhodes",
        "port_distance_km": 2.1, "matched_by": "name", "resolved_at": datetime(2026, 10, 11, tzinfo=timezone.utc),
        "routes": [{"id": ROUTE_ID, "origin_port": "Piraeus", "destination_port": "Rhodes",
                    "status": "auto", "sample_sailings": 1}],
    }
    ferry_repository = FakeFerryRepository(busy_rows(), profile=profile)
    onboarding = FakeOnboardingRepository()

    response = get_ferries(ferry_repository, onboarding)

    assert response.status_code == 200, response.text
    body = response.json()
    assert onboarding.calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID)]
    assert ferry_repository.profile_calls == ["rodos"]
    assert (body["status"], body["island_port"]) == ("ferry", "Rhodes")
    assert body["routes"][0]["origin_port"] == "Piraeus"
    days = {day["travel_date"]: day for day in body["days"]}
    assert days[CHECK_IN.isoformat()]["level"] == "high"
    assert days[CHECK_IN.isoformat()]["sold_out"] == 1
    assert body["observed_on"] == TODAY.isoformat()


def test_ferries_endpoint_before_the_first_lookup():
    body = get_ferries(FakeFerryRepository()).json()

    assert body == {
        "canonical_destination": "rodos", "status": None, "island_port": None, "port_distance_km": None,
        "resolved_at": None, "routes": [], "days": [], "observed_on": None,
    }


def test_ferries_endpoint_404_for_a_property_not_of_this_account():
    response = get_ferries(FakeFerryRepository(), FakeOnboardingRepository(owned_property=None))

    assert response.status_code == 404
