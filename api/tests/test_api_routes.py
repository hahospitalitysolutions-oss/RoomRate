from fastapi.testclient import TestClient
from uuid import UUID

from api.dependencies import AccountContext, get_account_context, get_market_service
from api.main import app
from api.services.market_service import RoomRateFilters


class FakeMarketService:
    def __init__(self):
        self.last_filters: RoomRateFilters | None = None

    def get_market_summary(self, filters: RoomRateFilters, agent_matches=None):
        self.last_filters = filters
        return {
            "destination": filters.destination,
            "check_in": filters.check_in,
            "check_out": filters.check_out,
            "total_records": 2,
            "total_hotels": 1,
            "price_min_eur": 70.0,
            "price_max_eur": 90.0,
            "price_avg_eur": 80.0,
            "price_median_eur": 80.0,
            "avg_review_score": 8.5,
            "rooms_left_total": 3,
        }

    def get_available_amenities(self, filters: RoomRateFilters):
        self.last_filters = filters
        return ["wifi", "pool", "breakfast"]

    def get_competitor_map_markers(self, filters: RoomRateFilters, origin=None, agent_matches=None):
        self.last_filters = filters
        return [
            {
                "hotel_name": "Aegean View",
                "property_type": "Hotel",
                "latitude": 36.34,
                "longitude": 28.2,
                "price_per_night_eur": 80.0,
                "review_score": 8.8,
                "review_count": 120,
                "rooms_left": 2,
            }
        ]


def test_market_summary_endpoint_returns_valid_payload():
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(
        account_id=UUID("00000000-0000-0000-0000-000000000001")
    )
    client = TestClient(app)

    response = client.get("/api/v1/market/summary?destination=Faliraki")

    assert response.status_code == 200
    assert response.json()["total_hotels"] == 1
    assert response.json()["destination"] == "Faliraki"

    app.dependency_overrides.clear()


def test_map_endpoint_returns_marker_payload_for_mapbox():
    service = FakeMarketService()
    app.dependency_overrides[get_market_service] = lambda: service
    app.dependency_overrides[get_account_context] = lambda: AccountContext(
        account_id=UUID("00000000-0000-0000-0000-000000000001")
    )
    client = TestClient(app)

    response = client.get("/api/v1/maps/competitors")

    assert response.status_code == 200
    assert response.json()[0]["hotel_name"] == "Aegean View"
    assert response.json()[0]["longitude"] == 28.2

    app.dependency_overrides.clear()


def test_map_endpoint_accepts_repeated_amenity_filters():
    service = FakeMarketService()
    app.dependency_overrides[get_market_service] = lambda: service
    app.dependency_overrides[get_account_context] = lambda: AccountContext(
        account_id=UUID("00000000-0000-0000-0000-000000000001")
    )
    client = TestClient(app)

    response = client.get("/api/v1/maps/competitors?amenities=WiFi&amenities=Pool")

    assert response.status_code == 200
    assert service.last_filters is not None
    assert service.last_filters.amenities == ("wifi", "pool")

    app.dependency_overrides.clear()


def test_market_amenities_endpoint_returns_dynamic_filter_options():
    service = FakeMarketService()
    app.dependency_overrides[get_market_service] = lambda: service
    app.dependency_overrides[get_account_context] = lambda: AccountContext(
        account_id=UUID("00000000-0000-0000-0000-000000000001")
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/market/amenities",
        params={
            "destination": "Faliraki",
            "room_type_category": "Double",
        },
    )

    assert response.status_code == 200
    assert response.json() == ["wifi", "pool", "breakfast"]
    assert service.last_filters is not None
    assert service.last_filters.room_type_category == "double"

    app.dependency_overrides.clear()
