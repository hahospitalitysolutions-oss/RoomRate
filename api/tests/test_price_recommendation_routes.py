from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_price_history_repository,
    get_price_recommendation_audit_repository,
    get_price_recommendation_agent,
)
from api.main import app
from api.schemas.agents import PriceRecommendation
from api.schemas.market import (
    AgentDataQuality,
    AgentMeta,
    SmartAdvisorContext,
    SmartAdvisorSignals,
)
from api.services.market_service import RoomRateFilters
from api.services.price_recommendation_agent import (
    PRICE_RECOMMENDATION_PROMPT_VERSION,
    PriceRecommendationAgent,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


@pytest.fixture(autouse=True)
def _rate_limit_disabled(monkeypatch):
    """Keep this file's POSTs out of the process-wide rate-limit bucket.

    Every request here hits a rate-limited path through the shared app, and the
    limiter keys all TestClient calls to one client. Two dozen requests would
    eat the per-minute budget of route tests that run later in the same
    session (they then fail with 429). A limit of 0 disables limiting; the
    limiter itself is covered by test_rate_limit.py.
    """
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)


class FakeOnboardingRepository:
    def __init__(self, owned_property=None, selected_room_type=None):
        self._owned_property = owned_property
        self._selected_room_type = selected_room_type

    def get_owned_property(self, account_id, owned_property_id):
        return self._owned_property

    def get_selected_room_type(self, account_id, owned_property_id):
        return self._selected_room_type


class FakePriceHistoryRepository:
    def __init__(self, rows, own_live_price=None, latest_run_id=None, agent_matches=None):
        self.rows = rows
        self.own_live_price = own_live_price
        self.latest_run_id = latest_run_id
        self.agent_matches = agent_matches or []
        self.last_kwargs = None
        self.own_price_kwargs = None
        self.run_id_calls = []
        self.agent_match_calls = []

    def fetch_price_series(self, **kwargs):
        self.last_kwargs = kwargs
        return self.rows

    def fetch_own_live_price(self, **kwargs):
        self.own_price_kwargs = kwargs
        return self.own_live_price

    def fetch_latest_completed_run_id(self, **kwargs):
        self.run_id_calls.append(kwargs)
        return self.latest_run_id

    def fetch_latest_run_agent_matches(self, **kwargs):
        self.agent_match_calls.append(kwargs)
        return self.agent_matches


class FakeMarketService:
    def __init__(self):
        self.last_filters = None
        self.last_origin = None
        self.last_agent_matches = None

    def get_smart_advisor_context(
        self, filters: RoomRateFilters, my_hotel_name=None, origin=None, agent_matches=None
    ):
        self.last_filters = filters
        self.last_origin = origin
        self.last_agent_matches = agent_matches
        return SmartAdvisorContext(
            meta=AgentMeta(destination="Faliraki", market_stats={"price_median_eur": 120.0}),
            data_quality=AgentDataQuality(
                total_rows=2,
                total_competitors=2,
                rows_without_facilities=0,
                rows_without_coordinates=0,
                rows_without_rooms_left=0,
                facility_coverage_pct=100.0,
            ),
            pricing_signals=SmartAdvisorSignals(
                cheapest_competitor="A",
                most_expensive_competitor="B",
                market_price_spread_eur=40.0,
                low_availability_competitors=[],
                free_cancellation_share_pct=0.0,
                breakfast_available_share_pct=0.0,
            ),
            time_context={},
            competitors=[],
        )


class FakeAgent:
    """Simulates the no-key path: always returns a statistical-source recommendation."""

    def __init__(self):
        self.recommend_calls = 0
        self.last_stats = None
        self.last_cancellation_class = None

    def recommend(self, stats, advisor_context, own_cancellation_type=None):
        self.recommend_calls += 1
        self.last_stats = stats
        self.last_cancellation_class = own_cancellation_type
        return PriceRecommendation(
            recommended_price_eur=120.0,
            price_range_low_eur=110.0,
            price_range_high_eur=130.0,
            confidence="low",
            reasoning="Statistical baseline.",
            key_factors=["Market median EUR 120.0"],
            source="statistical",
        )


class FakeAuditRepository:
    def __init__(self, cached=None, count=0, cached_prompt_version=None):
        self.cached = cached
        self.count = count
        # The prompt version the seeded entry was stored under; None means
        # "stored under whatever version is current" (most tests don't care).
        self.cached_prompt_version = cached_prompt_version
        self.insert_calls = []
        self.get_cached_prompt_versions = []

    def get_cached(self, account_id, request_hash, prompt_version):
        self.get_cached_prompt_versions.append(prompt_version)
        # Mirrors the SQL predicate: an entry stored under another prompt
        # version never serves.
        if self.cached_prompt_version not in (None, prompt_version):
            return None
        return self.cached

    def count_today(self, account_id):
        return self.count

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return {
            "id": UUID("00000000-0000-0000-0000-000000000999"),
            "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        }


def _history_rows():
    return [
        {
            "rn": 1,
            "observed_at": "2026-06-30T08:00:00+00:00",
            "property_id": UUID("00000000-0000-0000-0000-0000000000a1"),
            "hotel_name": "A",
            "min_price": 100.0,
        },
        {
            "rn": 1,
            "observed_at": "2026-06-30T08:00:00+00:00",
            "property_id": UUID("00000000-0000-0000-0000-0000000000a2"),
            "hotel_name": "B",
            "min_price": 140.0,
        },
    ]


def _override_account():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_price_recommendation_audit_repository] = (
        lambda: FakeAuditRepository()
    )


def test_price_recommendation_returns_statistics_and_recommendation():
    history_repo = FakePriceHistoryRepository(_history_rows())
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )
    agent = FakeAgent()
    market_service = FakeMarketService()

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent
    app.dependency_overrides[get_market_service] = lambda: market_service

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
            "adults": 2,
            "children": 0,
            "rooms": 1,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "statistics" in body
    assert "recommendation" in body
    assert body["recommendation"]["source"] == "statistical"
    assert body["statistics"]["sample_runs"] == 1
    assert agent.recommend_calls == 1
    # The market key flowed through to the history lookup.
    assert history_repo.last_kwargs["canonical_destination"] == "faliraki"
    assert history_repo.last_kwargs["room_type_category"] == "double"
    # v4: the advisor context reads the comparison basis the statistics were
    # computed on, not the tracked-only subset; the owned property still
    # travels so the service can flag the tracked competitors. Two
    # same-category hotels and no similar-only one: scope "same", so the
    # comparable pool only (the history read still sees similar hotels and
    # lets the statistics decide).
    advisor_filters = market_service.last_filters
    assert body["statistics"]["stats_scope"]["used"] == "same"
    assert advisor_filters.selected_competitors_only is False
    assert advisor_filters.include_similar is False
    assert history_repo.last_kwargs["include_similar"] is True
    assert str(advisor_filters.owned_property_id) == str(OWNED_PROPERTY_ID)
    assert advisor_filters.destination == "faliraki"
    assert advisor_filters.room_type_category == "double"
    assert (advisor_filters.adults, advisor_filters.children, advisor_filters.rooms) == (2, 0, 1)

    app.dependency_overrides.clear()


def test_price_recommendation_preserves_genuine_zero_own_price():
    # F7: a real sample price of 0.0 must reach the statistics as 0.0, not None
    # (the old `as_float(...) or None` collapsed a genuine 0 to "no own price").
    history_repo = FakePriceHistoryRepository(_history_rows())
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 0.0,
        },
    )
    agent = FakeAgent()

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 200, response.text
    assert agent.last_stats.own_reference_price_eur == 0.0
    assert response.json()["statistics"]["own_reference_price_eur"] == 0.0

    app.dependency_overrides.clear()


def test_price_recommendation_404_when_no_selected_room_type():
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": None,
        },
        selected_room_type=None,
    )

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: FakePriceHistoryRepository([])
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_price_recommendation_404_when_owned_property_missing():
    onboarding_repo = FakeOnboardingRepository(owned_property=None, selected_room_type=None)

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: FakePriceHistoryRepository([])
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_price_recommendation_uses_body_room_type_category_override():
    history_repo = FakePriceHistoryRepository(_history_rows())
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": None,
        },
        # No selected room type, but the body provides the category explicitly.
        selected_room_type=None,
    )

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "room_type_category": "suite",
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 200, response.text
    assert history_repo.last_kwargs["room_type_category"] == "suite"
    app.dependency_overrides.clear()


def test_price_history_series_endpoint():
    history_repo = FakePriceHistoryRepository(_history_rows())

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo

    client = TestClient(app)
    response = client.get(
        "/api/v1/market/price-history",
        params={
            "destination": "faliraki",
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
            "adults": 2,
            "children": 0,
            "rooms": 1,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["canonical_destination"] == "faliraki"
    assert len(body["points"]) == 2
    assert body["points"][0]["hotel_name"] == "A"
    assert body["points"][0]["min_price_eur"] == 100.0
    assert history_repo.last_kwargs["canonical_destination"] == "faliraki"

    app.dependency_overrides.clear()


def test_price_history_normalizes_category_before_pooled_repository_read():
    history_repo = FakePriceHistoryRepository([])
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo

    response = TestClient(app).get(
        "/api/v1/market/price-history",
        params={
            "destination": "faliraki",
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
            "room_type_category": "  DOUBLE  ",
        },
    )

    assert response.status_code == 200, response.text
    assert history_repo.last_kwargs["room_type_category"] == "double"
    app.dependency_overrides.clear()


class NoDataAgent:
    """Simulates insufficient data: recommend() finds nothing to anchor on."""

    def recommend(self, stats, advisor_context, own_cancellation_type=None):
        return None


def test_price_recommendation_unavailable_on_insufficient_data():
    """No usable data â‡’ recommendation_available=false and NO â‚¬0 recommendation."""
    history_repo = FakePriceHistoryRepository([])  # empty market history
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": None,
        },
    )

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: NoDataAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendation_available"] is False
    assert body["recommendation"] is None
    # Statistics (with their explanatory notes) still ship so the UI can say why.
    assert body["statistics"]["sample_runs"] == 0
    assert body["statistics"]["notes"]

    app.dependency_overrides.clear()


def test_price_recommendation_available_flag_true_with_data():
    history_repo = FakePriceHistoryRepository(_history_rows())
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )

    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendation_available"] is True
    assert body["recommendation"]["recommended_price_eur"] == 120.0

    app.dependency_overrides.clear()


def test_price_recommendation_reuses_account_scoped_cache():
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
        },
        selected_room_type={
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )
    history_repo = FakePriceHistoryRepository(_history_rows())
    agent = FakeAgent()
    cached_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    audit_repo = FakeAuditRepository(
        cached={
            "id": UUID("00000000-0000-0000-0000-000000000999"),
            "response_payload": {
                "statistics": {
                    "sample_runs": 1,
                    "lead_time_days": 30,
                    "notes": [],
                },
                "recommendation": None,
                "recommendation_available": False,
            },
            "model_version": "statistical-v1",
            "prompt_version": "v1",
            "created_at": cached_at,
        }
    )
    _override_account()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent
    app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit_repo

    response = TestClient(app).post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 200
    assert response.json()["cached"] is True
    assert history_repo.last_kwargs is None
    assert agent.recommend_calls == 0
    assert audit_repo.insert_calls == []

    app.dependency_overrides.clear()


def test_pre_upgrade_cache_entry_is_not_served_after_a_prompt_bump():
    """Review fix: the cache lookup keys on prompt_version, so an entry stored
    before a prompt/model upgrade can never answer for the new version."""
    audit_repo = FakeAuditRepository(
        cached=_cached_audit(), cached_prompt_version="2026-09-15.v2"
    )
    agent = FakeAgent()
    history_repo = FakePriceHistoryRepository(_history_rows())
    client = _round6_client(history_repo, agent=agent, audit_repo=audit_repo)

    response = _pricing_request(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cached"] is False
    # Computed fresh under the current prompt — never served stale.
    assert agent.recommend_calls == 1
    assert audit_repo.get_cached_prompt_versions == [PRICE_RECOMMENDATION_PROMPT_VERSION]
    app.dependency_overrides.clear()


def test_price_recommendation_daily_quota_returns_429_before_analysis():
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
        },
        selected_room_type={
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )
    history_repo = FakePriceHistoryRepository(_history_rows())
    agent = FakeAgent()
    audit_repo = FakeAuditRepository(
        count=settings.max_daily_price_recommendations_per_account
    )
    _override_account()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent
    app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit_repo

    response = TestClient(app).post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )

    assert response.status_code == 429
    assert history_repo.last_kwargs is None
    assert agent.recommend_calls == 0

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# The audit trail must never take the pricing endpoint down with it
# ---------------------------------------------------------------------------


class BrokenAuditRepository:
    """Every audit call fails, e.g. migration 0020 not applied yet."""

    def __init__(self, error: Exception | None = None):
        self.error = error or RuntimeError('relation "roomrate_price_recommendation_audits" does not exist')
        self.insert_calls: list[dict] = []

    def get_cached(self, account_id, request_hash, prompt_version):
        raise self.error

    def count_today(self, account_id):
        raise self.error

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        raise self.error


def _pricing_client(audit_repo, agent=None):
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )
    _override_account()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_history_repository] = lambda: FakePriceHistoryRepository(
        _history_rows()
    )
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent or FakeAgent()
    app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit_repo
    return TestClient(app)


def _pricing_request(client):
    return client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )


def test_unavailable_audit_table_still_returns_a_recommendation():
    """Shipping code before migration 0020 must not 500 every request.

    The cache lookup runs before any pricing work, so an unmigrated database
    took the whole endpoint down. The audit trail is valuable but it is not
    the product: degrade to an unaudited answer instead.
    """
    audit_repo = BrokenAuditRepository()
    client = _pricing_client(audit_repo)

    response = _pricing_request(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendation"]["recommended_price_eur"] == 120.0
    # No audit row exists, so no id/version may be claimed.
    assert body["audit_id"] is None
    assert body["cached"] is False

    app.dependency_overrides.clear()


def test_failed_audit_insert_does_not_discard_the_billed_recommendation():
    """A transient DB failure must not throw away a paid-for LLM answer."""
    audit_repo = BrokenAuditRepository()
    client = _pricing_client(audit_repo)

    response = _pricing_request(client)

    assert response.status_code == 200, response.text
    # The insert WAS attempted (so the failure is real, not skipped).
    assert audit_repo.insert_calls
    assert response.json()["recommendation"] is not None

    app.dependency_overrides.clear()


def test_quota_is_still_enforced_when_the_audit_table_is_healthy():
    """Degrading on failure must not weaken the quota on the happy path."""
    audit_repo = FakeAuditRepository(count=10_000)
    client = _pricing_client(audit_repo)

    response = _pricing_request(client)

    assert response.status_code == 429

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Round 6 — own reference price: live Booking row, then onboarding sample
# ---------------------------------------------------------------------------


def _round6_client(history_repo, sample_price=120.0, agent=None, audit_repo=None):
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": sample_price,
        },
    )
    _override_account()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent or FakeAgent()
    if audit_repo is not None:
        app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit_repo
    return TestClient(app)


def test_own_reference_price_prefers_the_live_booking_row():
    history_repo = FakePriceHistoryRepository(
        _history_rows(), own_live_price={"price": 92.0, "cancellation_type": "non_refundable"}
    )
    client = _round6_client(history_repo, sample_price=120.0)

    body = _pricing_request(client).json()

    assert body["own_price_source"] == "booking_live"
    assert body["statistics"]["own_reference_price_eur"] == 92.0
    kwargs = history_repo.own_price_kwargs
    assert kwargs["display_name"] == "My Hotel"
    assert kwargs["room_type_category"] == "double"
    assert kwargs["canonical_destination"] == "faliraki"
    assert kwargs["check_in"].isoformat() == "2030-07-15"
    # The reference package's cancellation class scopes the history minimums.
    assert history_repo.last_kwargs["cancellation_type"] == "non_refundable"
    app.dependency_overrides.clear()


def test_own_reference_price_falls_back_to_the_onboarding_sample():
    history_repo = FakePriceHistoryRepository(_history_rows(), own_live_price=None)
    client = _round6_client(history_repo, sample_price=120.0)

    body = _pricing_request(client).json()

    assert body["own_price_source"] == "onboarding_sample"
    assert body["statistics"]["own_reference_price_eur"] == 120.0
    # A typed-in sample price has no cancellation class: no like-for-like scope.
    assert history_repo.last_kwargs["cancellation_type"] is None
    app.dependency_overrides.clear()


def test_own_reference_price_is_null_without_any_source():
    history_repo = FakePriceHistoryRepository(_history_rows(), own_live_price=None)
    client = _round6_client(history_repo, sample_price=None)

    body = _pricing_request(client).json()

    assert body["own_price_source"] is None
    assert body["statistics"]["own_reference_price_eur"] is None
    app.dependency_overrides.clear()


def test_advisor_context_gets_owner_origin_and_agent_gets_reference_class():
    """Spec 2026-09-29 Β.2: the router hands the market service the owner's
    coordinates (the distance_km origin) and the agent the reference
    package's cancellation class."""
    history_repo = FakePriceHistoryRepository(
        _history_rows(), own_live_price={"price": 92.0, "cancellation_type": "non_refundable"}
    )
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
            "latitude": 36.34,
            "longitude": 28.2,
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )
    agent = FakeAgent()
    market_service = FakeMarketService()
    _override_account()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_market_service] = lambda: market_service
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent

    response = _pricing_request(TestClient(app))

    assert response.status_code == 200, response.text
    assert market_service.last_origin == (36.34, 28.2)
    assert agent.last_cancellation_class == "non_refundable"
    app.dependency_overrides.clear()


def test_advisor_origin_is_none_without_owner_coordinates():
    """A property without stored coordinates yields no origin — distances stay
    null instead of being measured from (0, 0)."""
    history_repo = FakePriceHistoryRepository(_history_rows(), own_live_price=None)
    market_service = FakeMarketService()
    agent = FakeAgent()
    client = _round6_client(history_repo, agent=agent)
    app.dependency_overrides[get_market_service] = lambda: market_service

    response = _pricing_request(client)

    assert response.status_code == 200, response.text
    assert market_service.last_origin is None
    # A typed-in onboarding sample has no package, hence no cancellation class.
    assert agent.last_cancellation_class is None
    app.dependency_overrides.clear()


def test_price_recommendation_reads_similar_hotels_too():
    history_repo = FakePriceHistoryRepository(_history_rows())
    client = _round6_client(history_repo)

    assert _pricing_request(client).status_code == 200
    # Similar-only hotels ride along; the statistics decide whether to use them.
    assert history_repo.last_kwargs["include_similar"] is True
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Round 6 — a new scrape is a new cache key
# ---------------------------------------------------------------------------


def _cached_audit():
    return {
        "id": UUID("00000000-0000-0000-0000-000000000999"),
        "response_payload": {
            "statistics": {"sample_runs": 1, "lead_time_days": 30, "notes": []},
            "recommendation": None,
            "recommendation_available": False,
        },
        "model_version": "statistical-v1",
        "prompt_version": "v1",
        "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


def test_cache_key_changes_with_the_latest_completed_run():
    audit_repo = FakeAuditRepository()
    hashes = []
    for run_id in ("run-1", "run-2"):
        history_repo = FakePriceHistoryRepository(_history_rows(), latest_run_id=run_id)
        client = _round6_client(history_repo, audit_repo=audit_repo)

        assert _pricing_request(client).status_code == 200

        hashes.append(audit_repo.insert_calls[-1]["request_hash"])
        assert audit_repo.insert_calls[-1]["request_payload"]["latest_run_id"] == run_id
        assert history_repo.run_id_calls[0]["canonical_destination"] == "faliraki"
        assert history_repo.run_id_calls[0]["check_in"].isoformat() == "2030-07-15"
    assert hashes[0] != hashes[1]
    app.dependency_overrides.clear()


def test_cache_key_is_stable_while_no_new_run_completes():
    audit_repo = FakeAuditRepository()
    for _ in range(2):
        history_repo = FakePriceHistoryRepository(_history_rows(), latest_run_id="run-1")
        _pricing_request(_round6_client(history_repo, audit_repo=audit_repo))

    first, second = (call["request_hash"] for call in audit_repo.insert_calls)
    assert first == second
    app.dependency_overrides.clear()


def test_latest_run_is_resolved_before_the_cache_lookup():
    history_repo = FakePriceHistoryRepository(_history_rows(), latest_run_id="run-1")
    client = _round6_client(history_repo, audit_repo=FakeAuditRepository(cached=_cached_audit()))

    assert _pricing_request(client).json()["cached"] is True
    # The run id is part of the key, so it is read on every request ...
    assert len(history_repo.run_id_calls) == 1
    # ... while a cache hit still reads no price series and no own live price.
    assert history_repo.last_kwargs is None
    assert history_repo.own_price_kwargs is None
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Round 6 — the response contract the pricing page mirrors
# ---------------------------------------------------------------------------

RESPONSE_KEYS = {
    "statistics",
    "recommendation",
    "recommendation_available",
    "audit_id",
    "generated_at",
    "cached",
    "model_version",
    "prompt_version",
    "own_price_source",
}
STATISTICS_KEYS = {
    "sample_runs",
    "sample_days",
    "own_reference_price_eur",
    "market_median_eur",
    "market_p25_eur",
    "market_p75_eur",
    "own_position_percentile",
    "position",
    "stats_scope",
    "trend_7d_pct",
    "trend_30d_pct",
    "lead_time_days",
    "statistical_recommendation_eur",
    "notes",
}
RECOMMENDATION_KEYS = {
    "recommended_price_eur",
    "price_range_low_eur",
    "price_range_high_eur",
    "confidence",
    "reasoning",
    "key_factors",
    "source",
}


def _round6_history_rows():
    """Latest run: 2 same-category hotels and 2 similar-only ones (scope widens)."""
    hotels = [
        ("00000000-0000-0000-0000-0000000000a1", "A", 100.0, 80.0),
        ("00000000-0000-0000-0000-0000000000a2", "B", 140.0, None),
        ("00000000-0000-0000-0000-0000000000b1", "C", None, 90.0),
        ("00000000-0000-0000-0000-0000000000b2", "D", None, 200.0),
    ]
    return [
        {
            "rn": 1,
            "observed_at": "2026-09-14T08:00:00+00:00",
            "property_id": UUID(property_id),
            "hotel_name": hotel_name,
            "min_price": same,
            "min_price_same": same,
            "min_price_similar": similar,
        }
        for property_id, hotel_name, same, similar in hotels
    ]


def test_price_recommendation_json_contract_carries_the_round6_fields():
    # Real agent without an API key: the statistical path end to end, no network.
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5")
    history_repo = FakePriceHistoryRepository(
        _round6_history_rows(),
        own_live_price={"price": 92.0, "cancellation_type": None},
        latest_run_id="run-1",
    )

    body = _pricing_request(_round6_client(history_repo, agent=agent)).json()

    assert set(body) == RESPONSE_KEYS
    assert set(body["statistics"]) == STATISTICS_KEYS
    assert set(body["recommendation"]) == RECOMMENDATION_KEYS
    assert body["own_price_source"] == "booking_live"
    assert body["statistics"]["own_reference_price_eur"] == 92.0
    # Only C (90) undercuts 92: A counts at its same-category 100, not its 80 studio.
    assert body["statistics"]["position"] == {"cheaper_than_you": 1, "total": 4}
    assert body["statistics"]["stats_scope"] == {
        "same_category": 2,
        "similar": 2,
        "used": "same_plus_similar",
        "cancellation_class": "all",
        # Category fallback: no agent basis, so no agent-comparable hotels.
        "comparable": 0,
    }
    assert body["statistics"]["sample_days"] == 1
    app.dependency_overrides.clear()


def test_price_recommendation_json_contract_nulls_without_data():
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5")
    history_repo = FakePriceHistoryRepository([], own_live_price=None)

    body = _pricing_request(_round6_client(history_repo, sample_price=None, agent=agent)).json()

    # The keys stay present so the page can branch on null instead of undefined.
    assert set(body) == RESPONSE_KEYS
    assert set(body["statistics"]) == STATISTICS_KEYS
    assert body["recommendation"] is None
    assert body["own_price_source"] is None
    assert body["statistics"]["position"] is None
    assert body["statistics"]["stats_scope"] is None
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Agent basis (owner decision 2026-09-30): both routes resolve the owned room
# type whose agent matches choose the comparison set.
# ---------------------------------------------------------------------------

SELECTED_ROOM_ID = UUID("00000000-0000-0000-0000-0000000000c1")
OTHER_ROOM_ID = UUID("00000000-0000-0000-0000-0000000000c2")


class RoomAwareOnboardingRepository(FakeOnboardingRepository):
    """Adds the owned-room lookup; rooms belong to their stated property."""

    def __init__(self, owned_property=None, selected_room_type=None, rooms=None):
        super().__init__(owned_property=owned_property, selected_room_type=selected_room_type)
        self.rooms = rooms or {}

    def get_owned_room_type(self, account_id, owned_room_type_id):
        return self.rooms.get(owned_room_type_id)


def _room_aware_onboarding():
    return RoomAwareOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "id": SELECTED_ROOM_ID,
            "owned_property_id": OWNED_PROPERTY_ID,
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
        rooms={
            OTHER_ROOM_ID: {
                "id": OTHER_ROOM_ID,
                "owned_property_id": UUID("00000000-0000-0000-0000-000000000999"),
                "room_type": "Suite",
                "room_type_category": "suite",
                "sample_price_per_night_eur": 200.0,
            }
        },
    )


def _recommendation_body(**extra):
    return {
        "owned_property_id": str(OWNED_PROPERTY_ID),
        "check_in": "2030-07-15",
        "check_out": "2030-07-18",
        **extra,
    }


def test_price_recommendation_defaults_to_the_selected_room_type():
    history_repo = FakePriceHistoryRepository(_history_rows())
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = _room_aware_onboarding
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    response = TestClient(app).post(
        "/api/v1/agents/price-recommendation", json=_recommendation_body()
    )

    assert response.status_code == 200, response.text
    assert history_repo.last_kwargs["owned_room_type_id"] == SELECTED_ROOM_ID
    app.dependency_overrides.clear()


def test_price_recommendation_rejects_a_room_type_of_another_property():
    history_repo = FakePriceHistoryRepository(_history_rows())
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = _room_aware_onboarding
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

    response = TestClient(app).post(
        "/api/v1/agents/price-recommendation",
        json=_recommendation_body(owned_room_type_id=str(OTHER_ROOM_ID)),
    )

    assert response.status_code == 404
    assert history_repo.last_kwargs is None
    app.dependency_overrides.clear()


HOTEL_A_ID = UUID("00000000-0000-0000-0000-0000000000a1")
HOTEL_B_ID = UUID("00000000-0000-0000-0000-0000000000a2")


def _agent_match_rows(written_at="2026-09-30T10:00:00+00:00"):
    """The latest run's verdicts for the selected room, as the repository reads them."""
    return [
        {"property_id": HOTEL_A_ID, "room_type": "Double Room", "score": 90, "comparable": True,
         "reasoning": "Ίδιο δίκλινο.", "created_at": written_at},
        {"property_id": HOTEL_B_ID, "room_type": "Suite", "score": 20, "comparable": False,
         "reasoning": "Σουίτα.", "created_at": written_at},
    ]


def _agent_basis_history_rows():
    """The latest run priced on the agent basis (fetch_price_series agent_basis = 1)."""
    return [
        {**row, "min_price_same": row["min_price"], "min_price_similar": None, "agent_basis": 1}
        for row in _history_rows()
    ]


def _agent_client(history_repo, market_service, audit_repo=None):
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = _room_aware_onboarding
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: market_service
    if audit_repo is not None:
        app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit_repo
    return TestClient(app)


def test_cache_key_changes_when_the_agent_verdicts_change_without_a_new_run():
    """Post-scrape matching finishes after the job completed, and
    «Επανεκτίμηση» rewrites the verdicts: neither adds a run, yet both change
    the basis, so neither may be answered from a recommendation cached on
    the category pool or on older verdicts."""
    audit_repo = FakeAuditRepository()
    hashes = []
    for agent_matches in (
        [],
        _agent_match_rows("2026-09-30T10:00:00+00:00"),
        _agent_match_rows("2026-09-30T10:05:00+00:00"),
    ):
        history_repo = FakePriceHistoryRepository(
            _history_rows(), latest_run_id="run-1", agent_matches=agent_matches
        )
        client = _agent_client(history_repo, FakeMarketService(), audit_repo)

        assert client.post(
            "/api/v1/agents/price-recommendation", json=_recommendation_body()
        ).status_code == 200

        hashes.append(audit_repo.insert_calls[-1]["request_hash"])
        assert history_repo.agent_match_calls[0]["owned_room_type_id"] == SELECTED_ROOM_ID
    payload = audit_repo.insert_calls[-1]["request_payload"]
    assert payload["agent_matches"] == 2
    assert payload["agent_matches_written_at"] == "2026-09-30T10:05:00+00:00"
    assert len(set(hashes)) == 3
    app.dependency_overrides.clear()


def test_cache_key_is_stable_while_the_agent_verdicts_stay():
    audit_repo = FakeAuditRepository()
    for _ in range(2):
        history_repo = FakePriceHistoryRepository(
            _history_rows(), latest_run_id="run-1", agent_matches=_agent_match_rows()
        )
        _agent_client(history_repo, FakeMarketService(), audit_repo).post(
            "/api/v1/agents/price-recommendation", json=_recommendation_body()
        )

    first, second = (call["request_hash"] for call in audit_repo.insert_calls)
    assert first == second
    app.dependency_overrides.clear()


def test_the_advisor_reads_the_agent_verdicts_the_statistics_were_computed_on():
    history_repo = FakePriceHistoryRepository(
        _agent_basis_history_rows(), agent_matches=_agent_match_rows()
    )
    market_service = FakeMarketService()

    response = _agent_client(history_repo, market_service).post(
        "/api/v1/agents/price-recommendation", json=_recommendation_body()
    )

    assert response.status_code == 200, response.text
    assert response.json()["statistics"]["stats_scope"]["used"] == "agent"
    # The whole lookup travels (a rejected room vs one the agent never
    # scored), and the filters ask for the comparison set: the service reads
    # the broad pool and narrows it with these verdicts, as the map does.
    lookup = market_service.last_agent_matches
    assert lookup[(str(HOTEL_A_ID), "double room")].comparable is True
    assert lookup[(str(HOTEL_B_ID), "suite")].comparable is False
    assert market_service.last_filters.include_similar is False
    app.dependency_overrides.clear()


def test_the_advisor_reads_similar_hotels_only_when_the_statistics_widened():
    rows = [
        # 1 same-category hotel + 1 similar-only one: the statistics widen.
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": HOTEL_A_ID,
         "hotel_name": "A", "min_price": 100.0, "min_price_same": 100.0, "min_price_similar": None},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": HOTEL_B_ID,
         "hotel_name": "B", "min_price": None, "min_price_same": None, "min_price_similar": 60.0},
    ]
    history_repo = FakePriceHistoryRepository(rows)
    market_service = FakeMarketService()

    response = _agent_client(history_repo, market_service).post(
        "/api/v1/agents/price-recommendation", json=_recommendation_body()
    )

    assert response.json()["statistics"]["stats_scope"]["used"] == "same_plus_similar"
    assert market_service.last_filters.include_similar is True
    assert market_service.last_agent_matches is None
    app.dependency_overrides.clear()


def test_price_history_reads_the_selected_room_basis_with_the_widening_rule():
    rows = [
        # Latest run: 1 same-category hotel + 1 similar-only one -> widened,
        # exactly as the statistics decide it.
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "00000000-0000-0000-0000-0000000000a1",
         "hotel_name": "A", "min_price": 100.0, "min_price_same": 100.0, "min_price_similar": 80.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "00000000-0000-0000-0000-0000000000a2",
         "hotel_name": "B", "min_price": None, "min_price_same": None, "min_price_similar": 60.0},
    ]
    history_repo = FakePriceHistoryRepository(rows)
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_onboarding_repository] = _room_aware_onboarding

    response = TestClient(app).get(
        "/api/v1/market/price-history",
        params={
            "destination": "faliraki",
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
            "owned_property_id": str(OWNED_PROPERTY_ID),
        },
    )

    assert response.status_code == 200, response.text
    assert history_repo.last_kwargs["owned_room_type_id"] == SELECTED_ROOM_ID
    assert history_repo.last_kwargs["include_similar"] is True
    prices = {point["hotel_name"]: point["min_price_eur"] for point in response.json()["points"]}
    # A keeps its same-category 100 (not its 80 studio); B joins at 60.
    assert prices == {"A": 100.0, "B": 60.0}
    app.dependency_overrides.clear()


def test_price_history_404s_an_unknown_owned_room_type():
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: FakePriceHistoryRepository([])
    app.dependency_overrides[get_onboarding_repository] = _room_aware_onboarding

    response = TestClient(app).get(
        "/api/v1/market/price-history",
        params={
            "destination": "faliraki",
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
            "owned_room_type_id": "00000000-0000-0000-0000-0000000000ff",
        },
    )

    assert response.status_code == 404
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# One basis, end to end over real SQL (owner report 2026-09-30: chart 59 EUR
# vs recommendation 82 EUR): the chart and the recommendation resolve the same
# owned room, the same reference cancellation class and the same basis, so
# the chart's latest median IS the statistics' market median.
# ---------------------------------------------------------------------------

_PARITY_SCHEMA = (
    "CREATE TABLE roomrate_scrape_runs (id TEXT PRIMARY KEY, account_id TEXT,"
    " canonical_destination TEXT, check_in TEXT, check_out TEXT, adults INTEGER,"
    " children INTEGER, rooms INTEGER, status TEXT, finished_at TEXT, scrape_job_id TEXT)",
    "CREATE TABLE roomrate_properties (id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT)",
    "CREATE TABLE roomrate_rate_observations (id TEXT PRIMARY KEY, scrape_run_id TEXT, property_id TEXT)",
    "CREATE TABLE roomrate_room_packages (id TEXT PRIMARY KEY, rate_observation_id TEXT, room_type TEXT,"
    " room_type_category TEXT, cancellation_type TEXT, price_per_night_eur NUMERIC,"
    " discounted_price_per_night_eur NUMERIC)",
    "CREATE TABLE roomrate_owned_properties (id TEXT PRIMARY KEY, account_id TEXT,"
    " display_name TEXT, is_active BOOLEAN)",
    "CREATE TABLE roomrate_room_matches (account_id TEXT, scrape_job_id TEXT, owned_room_type_id TEXT,"
    " property_id TEXT, room_type TEXT, score NUMERIC, comparable BOOLEAN, reasoning TEXT,"
    " created_at TEXT)",
)

# The own package is non_refundable, so like-for-like moves every mixed-class
# hotel: medians 75 (like-for-like) vs 57.5 (overall minimums).
_PARITY_HOTELS = {
    "My Hotel": [("double", "non_refundable", 92)],
    "Hotel Match": [("double", "free_cancellation", 70), ("double", "non_refundable", 100)],
    "Hotel Two": [("double", "free_cancellation", 55), ("double", "non_refundable", 90)],
    "Hotel NullOnly": [("double", None, 60)],
    "Hotel OtherClass": [("double", "free_cancellation", 50)],
}


@pytest.fixture()
def parity_history_repository():
    import sqlite3

    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    from api.repositories.price_history_repository import PriceHistoryRepository

    if sqlite3.sqlite_version_info < (3, 39, 0):
        pytest.skip("IS DISTINCT FROM needs SQLite 3.39+")
    # The routes run in Starlette's threadpool: one shared in-memory database
    # must be usable from those worker threads.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with engine.begin() as connection:
        for statement in _PARITY_SCHEMA:
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO roomrate_owned_properties VALUES ('own-1', :account_id, 'My Hotel', 1)"),
            {"account_id": str(ACCOUNT_ID)},
        )
        connection.execute(
            text(
                "INSERT INTO roomrate_scrape_runs VALUES ('run-1', :account_id, 'lindos',"
                " '2030-07-15', '2030-07-18', 2, 0, 1, 'completed', '2026-09-14T08:00:00+00:00', 'job-1')"
            ),
            {"account_id": str(ACCOUNT_ID)},
        )
        for hotel_index, (hotel_name, packages) in enumerate(_PARITY_HOTELS.items(), start=1):
            property_id = f"00000000-0000-0000-0000-{hotel_index:012d}"
            observation_id = f"obs-{hotel_index}"
            connection.execute(
                text("INSERT INTO roomrate_properties VALUES (:id, :canonical_name, :display_name)"),
                {"id": property_id, "canonical_name": hotel_name.lower(), "display_name": hotel_name},
            )
            connection.execute(
                text("INSERT INTO roomrate_rate_observations VALUES (:id, 'run-1', :property_id)"),
                {"id": observation_id, "property_id": property_id},
            )
            for package_index, (category, cancellation, price) in enumerate(packages):
                connection.execute(
                    text(
                        "INSERT INTO roomrate_room_packages VALUES"
                        " (:id, :observation_id, 'Double Room', :category, :cancellation, :price, NULL)"
                    ),
                    {
                        "id": f"pkg-{hotel_index}-{package_index}",
                        "observation_id": observation_id,
                        "category": category,
                        "cancellation": cancellation,
                        "price": price,
                    },
                )
    yield PriceHistoryRepository(engine.connect)
    engine.dispose()


def test_chart_latest_median_equals_the_recommendation_market_median(parity_history_repository):
    from statistics import median

    onboarding = RoomAwareOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "lindos",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "id": SELECTED_ROOM_ID,
            "owned_property_id": OWNED_PROPERTY_ID,
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": 120.0,
        },
    )
    _override_account()
    app.dependency_overrides[get_price_history_repository] = lambda: parity_history_repository
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding
    app.dependency_overrides[get_price_recommendation_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    client = TestClient(app)

    chart = client.get(
        "/api/v1/market/price-history",
        params={
            "destination": "lindos",
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
            "owned_property_id": str(OWNED_PROPERTY_ID),
        },
    )
    recommendation = client.post(
        "/api/v1/agents/price-recommendation",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "check_in": "2030-07-15",
            "check_out": "2030-07-18",
        },
    )
    app.dependency_overrides.clear()

    assert chart.status_code == 200, chart.text
    assert recommendation.status_code == 200, recommendation.text
    latest_points = [point for point in chart.json()["points"] if point["run_index"] == 1]
    statistics = recommendation.json()["statistics"]
    assert "My Hotel" not in {point["hotel_name"] for point in latest_points}
    assert statistics["stats_scope"]["cancellation_class"] == "matched"
    assert statistics["market_median_eur"] == 75.0  # like-for-like, not the 57.5 overall
    assert median(point["min_price_eur"] for point in latest_points) == statistics["market_median_eur"]
