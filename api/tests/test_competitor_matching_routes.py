from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_room_match_repository,
)
from api.main import app
from api.schemas.market import OwnedRoomReference
from api.services.market_service import MarketService

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
SCRAPE_JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
ROOM_TYPE_ID = UUID("00000000-0000-0000-0000-000000000abc")
PROPERTY_AGENT = UUID("00000000-0000-0000-0000-0000000000e1")
PROPERTY_STAT = UUID("00000000-0000-0000-0000-0000000000e2")


class FakeMatchMarketService:
    def __init__(self):
        self.calls = []

    def get_competitors(
        self,
        filters,
        owned_room=None,
        min_match_score=None,
        sort_by_match=False,
        origin=None,
        sort_by_distance=False,
        agent_matches=None,
    ):
        self.calls.append(
            {
                "filters": filters,
                "owned_room": owned_room,
                "min_match_score": min_match_score,
                "sort_by_match": sort_by_match,
                "origin": origin,
                "sort_by_distance": sort_by_distance,
                "agent_matches": agent_matches,
            }
        )
        return []


class FakeOnboardingRepository:
    def __init__(self, selected_room_type=None, owned_property=None):
        self.selected_room_type = selected_room_type
        self.owned_property = owned_property
        self.calls = []

    def get_selected_room_type(self, account_id, owned_property_id):
        self.calls.append((account_id, owned_property_id))
        return self.selected_room_type

    def get_owned_property(self, account_id, owned_property_id):
        # Round 6: the distance origin lookup; None = no coordinates known.
        return self.owned_property


def _client(service, repository):
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_market_service] = lambda: service
    app.dependency_overrides[get_onboarding_repository] = lambda: repository
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_competitors_endpoint_without_matching_keeps_default_behavior():
    service = FakeMatchMarketService()
    client = _client(service, FakeOnboardingRepository())

    response = client.get("/api/v1/competitors/")

    assert response.status_code == 200
    assert service.calls[0]["owned_room"] is None
    assert service.calls[0]["min_match_score"] is None
    assert service.calls[0]["sort_by_match"] is False


def test_match_room_requires_owned_property_id():
    client = _client(FakeMatchMarketService(), FakeOnboardingRepository())

    response = client.get("/api/v1/competitors/?match_room=true")

    assert response.status_code == 400
    assert "owned_property_id" in response.json()["detail"]


def test_match_room_returns_404_when_property_has_no_selected_room_type():
    repository = FakeOnboardingRepository(selected_room_type=None)
    client = _client(FakeMatchMarketService(), repository)

    response = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
    )

    assert response.status_code == 404
    assert repository.calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID)]


def test_match_room_resolves_owned_room_and_passes_matching_options():
    service = FakeMatchMarketService()
    repository = FakeOnboardingRepository(
        selected_room_type={
            "room_type": "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
            "room_type_category": "double",
        }
    )
    client = _client(service, repository)

    response = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
        "&min_match_score=55&sort=match"
    )

    assert response.status_code == 200
    call = service.calls[0]
    owned_room = call["owned_room"]
    assert isinstance(owned_room, OwnedRoomReference)
    assert owned_room.room_type == "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα"
    assert owned_room.room_type_category == "double"
    # No pre-extracted attributes: MarketService derives them from room_type
    # itself (identical to the old router-side dict round-trip).
    assert owned_room.room_attributes is None
    assert call["min_match_score"] == 55.0
    assert call["sort_by_match"] is True


def test_min_match_score_outside_0_100_is_rejected():
    client = _client(FakeMatchMarketService(), FakeOnboardingRepository())

    too_high = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
        "&min_match_score=101"
    )
    negative = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
        "&min_match_score=-1"
    )

    assert too_high.status_code == 422
    assert negative.status_code == 422


def test_unknown_sort_value_is_rejected():
    client = _client(FakeMatchMarketService(), FakeOnboardingRepository())

    response = client.get("/api/v1/competitors/?sort=stars")

    assert response.status_code == 422


def test_match_options_without_match_room_are_rejected():
    client = _client(FakeMatchMarketService(), FakeOnboardingRepository())

    min_score_only = client.get("/api/v1/competitors/?min_match_score=50")
    sort_only = client.get("/api/v1/competitors/?sort=match")

    assert min_score_only.status_code == 400
    assert sort_only.status_code == 400


# ---------------------------------------------------------------------------
# Agent match rows on the read side (spec 2026-09-29 Α.4)
# ---------------------------------------------------------------------------


class FakeRoomMatchRepository:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    def fetch_matches(self, account_id, scrape_job_id, owned_room_type_id):
        self.calls.append((account_id, scrape_job_id, owned_room_type_id))
        if self.error is not None:
            raise self.error
        return self.rows


def _selected_room_with_id() -> dict:
    return {
        "id": ROOM_TYPE_ID,
        "owned_property_id": OWNED_PROPERTY_ID,
        "room_type": "Double Room with Sea View",
        "room_type_category": "double",
    }


def _client_with_matches(service, repository, match_repository):
    client = _client(service, repository)
    app.dependency_overrides[get_room_match_repository] = lambda: match_repository
    return client


def _agent_match_row(property_id, room_type, score, reasoning):
    return {
        "property_id": property_id,
        "room_type": room_type,
        "score": Decimal(str(score)),
        "category_match": "same",
        "reasoning": reasoning,
        "model_version": "claude-sonnet-5-5",
    }


def test_agent_rows_reach_the_service_as_an_override_lookup():
    service = FakeMatchMarketService()
    repository = FakeOnboardingRepository(selected_room_type=_selected_room_with_id())
    match_repository = FakeRoomMatchRepository(
        rows=[_agent_match_row(PROPERTY_AGENT, "Double Room", 88, "Ίδια κατηγορία.")]
    )
    client = _client_with_matches(service, repository, match_repository)

    response = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
        f"&scrape_job_id={SCRAPE_JOB_ID}"
    )

    assert response.status_code == 200
    assert match_repository.calls == [(ACCOUNT_ID, SCRAPE_JOB_ID, ROOM_TYPE_ID)]
    lookup = service.calls[0]["agent_matches"]
    assert lookup is not None
    override = lookup[(str(PROPERTY_AGENT), "double room")]
    assert override.score == 88.0
    assert override.reasoning == "Ίδια κατηγορία."


def test_without_a_scrape_job_the_agent_rows_are_never_read():
    service = FakeMatchMarketService()
    repository = FakeOnboardingRepository(selected_room_type=_selected_room_with_id())
    match_repository = FakeRoomMatchRepository(rows=[])
    client = _client_with_matches(service, repository, match_repository)

    response = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
    )

    assert response.status_code == 200
    assert match_repository.calls == []
    assert service.calls[0]["agent_matches"] is None


def test_agent_lookup_failure_degrades_to_statistical_scores():
    """A broken/unmigrated agent table must never 500 the competitors read."""
    service = FakeMatchMarketService()
    repository = FakeOnboardingRepository(selected_room_type=_selected_room_with_id())
    match_repository = FakeRoomMatchRepository(error=RuntimeError("relation does not exist"))
    client = _client_with_matches(service, repository, match_repository)

    response = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
        f"&scrape_job_id={SCRAPE_JOB_ID}"
    )

    assert response.status_code == 200
    assert service.calls[0]["agent_matches"] is None


def test_mixed_response_shape_over_the_wire():
    """End-to-end pin of the row shape the frontend mirrors: one competitor
    agent-scored (match_source=agent, package match_reasoning set), one purely
    statistical (match_source=statistical, null reasonings)."""

    def _row(property_id, hotel_name, room_type, price, attributes):
        return {
            "record_id": f"{hotel_name}-{room_type}",
            "property_id": property_id,
            "hotel_name": hotel_name,
            "city": "Faliraki",
            "address": "",
            "property_type": "Hotel",
            "latitude": 36.34,
            "longitude": 28.2,
            "stars": 3.0,
            "review_score": 8.5,
            "review_count": 100,
            "price_per_night_eur": price,
            "price_total_eur": price * 5,
            "nights": 5,
            "guests": 2,
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "room_type": room_type,
            "room_type_category": "double",
            "room_attributes": attributes,
            "meals": "",
            "free_cancellation": "",
            "facilities": "",
            "rooms_left": 2,
            "check_in": "2026-10-01",
            "check_out": "2026-10-05",
            "scraped_at": "2026-09-30T08:00:00Z",
        }

    class FakeRates:
        def fetch_room_rates(self, filters):
            return [
                _row(PROPERTY_AGENT, "Agent Hotel", "Double Room", 90.0, {"capacity": 2}),
                _row(PROPERTY_STAT, "Stat Hotel", "Double Room", 80.0, {"capacity": 2}),
            ]

    repository = FakeOnboardingRepository(selected_room_type=_selected_room_with_id())
    match_repository = FakeRoomMatchRepository(
        rows=[_agent_match_row(PROPERTY_AGENT, "Double Room", 91, "Ίδιο δωμάτιο, ίδια θέα.")]
    )
    client = _client_with_matches(MarketService(FakeRates()), repository, match_repository)

    response = client.get(
        f"/api/v1/competitors/?match_room=true&owned_property_id={OWNED_PROPERTY_ID}"
        f"&scrape_job_id={SCRAPE_JOB_ID}"
    )

    assert response.status_code == 200
    payload = {competitor["hotel_name"]: competitor for competitor in response.json()}
    agent_hotel = payload["Agent Hotel"]
    stat_hotel = payload["Stat Hotel"]

    assert agent_hotel["match_source"] == "agent"
    assert agent_hotel["packages"][0]["match_score"] == 91.0
    assert agent_hotel["packages"][0]["match_reasoning"] == "Ίδιο δωμάτιο, ίδια θέα."

    assert stat_hotel["match_source"] == "statistical"
    assert stat_hotel["packages"][0]["match_score"] is not None
    assert stat_hotel["packages"][0]["match_reasoning"] is None
