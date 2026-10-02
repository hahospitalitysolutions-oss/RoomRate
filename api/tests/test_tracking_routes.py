from uuid import UUID

from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context, get_tracking_service
from api.main import app
from api.schemas.tracking import TrackedCompetitorListResponse, TrackedCompetitorSetResponse


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000789")


class FakeTrackingService:
    def __init__(self):
        self.calls = []

    def add_tracked_competitors(self, account_id, request):
        self.calls.append(("add", account_id, request))
        return TrackedCompetitorSetResponse(
            owned_property_id=request.owned_property_id,
            room_type_category=request.room_type_category,
            saved_count=len(request.competitors),
        )

    def replace_tracked_competitors(self, account_id, request):
        self.calls.append(("replace", account_id, request))
        return TrackedCompetitorSetResponse(
            owned_property_id=request.owned_property_id,
            room_type_category=request.room_type_category,
            saved_count=len(request.competitors),
        )

    def list_tracked_competitors(self, account_id, owned_property_id, room_type_category):
        self.calls.append((account_id, owned_property_id, room_type_category))
        return TrackedCompetitorListResponse(
            owned_property_id=owned_property_id,
            room_type_category=room_type_category,
            competitors=[{"property_id": PROPERTY_ID, "room_package_id": None}],
        )


def test_replace_tracked_competitors_endpoint_saves_checkbox_selection():
    service = FakeTrackingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_tracking_service] = lambda: service
    client = TestClient(app)

    response = client.put(
        "/api/v1/tracked/competitors",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "room_type_category": "Double",
            "competitors": [{"property_id": str(PROPERTY_ID)}],
        },
    )

    assert response.status_code == 200
    assert response.json()["saved_count"] == 1
    assert service.calls[0][0] == "replace"
    assert service.calls[0][1] == ACCOUNT_ID
    assert service.calls[0][2].room_type_category == "double"

    app.dependency_overrides.clear()


def test_add_tracked_competitors_endpoint_appends_checkbox_selection():
    service = FakeTrackingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_tracking_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/tracked/competitors",
        json={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "room_type_category": "Double",
            "competitors": [{"property_id": str(PROPERTY_ID)}],
        },
    )

    assert response.status_code == 200
    assert response.json()["saved_count"] == 1
    assert service.calls[0][0] == "add"
    assert service.calls[0][1] == ACCOUNT_ID
    assert service.calls[0][2].room_type_category == "double"

    app.dependency_overrides.clear()


def test_list_tracked_competitors_endpoint_returns_saved_checkbox_selection():
    service = FakeTrackingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_tracking_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/v1/tracked/competitors",
        params={
            "owned_property_id": str(OWNED_PROPERTY_ID),
            "room_type_category": "Double",
        },
    )

    assert response.status_code == 200
    assert response.json()["competitors"][0]["property_id"] == str(PROPERTY_ID)
    assert response.json()["room_type_category"] == "double"
    assert service.calls[0] == (ACCOUNT_ID, OWNED_PROPERTY_ID, "double")

    app.dependency_overrides.clear()
