from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import AccountContext, get_account_context, get_onboarding_service
from api.main import app
from api.repositories.scrape_jobs_repository import DailyQuotaExceededError, TooManyActiveJobsError
from api.schemas.onboarding import (
    AutomaticSetupResponse,
    OwnedPropertyOnboardingCreate,
    OwnedPropertyOnboardingResponse,
    OwnedPropertyRoomType,
    PropertyCandidate,
    SelectedRoomTypeResponse,
)
from api.schemas.scrape_jobs import ScrapeJobResponse


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """`app` is a shared module singleton: a failed assertion would skip the
    manual clear() at a test's tail and leak fakes into later tests. The
    manual clears stay (harmless); this guarantees the teardown."""
    yield
    app.dependency_overrides.clear()


class FakeScrapeJobService:
    def __init__(self):
        self.run_calls = []

    def run_job(self, account_id, job_id):
        self.run_calls.append((account_id, job_id))


class FakeOnboardingService:
    def __init__(self, replace_found: bool = True, delete_result: bool = True):
        self.scrape_job_service = FakeScrapeJobService()
        self.created = None
        self.auto_setup_request = None
        self.replace_found = replace_found
        self.delete_result = delete_result
        self.replaced: tuple | None = None
        self.deleted: tuple | None = None

    def search_property_candidates(
        self,
        property_name,
        location,
        check_in,
        check_out,
        adults,
        children,
        rooms,
        limit,
    ):
        return [
            PropertyCandidate(
                candidate_key="candidate-1",
                display_name="Aegean View",
                booking_url="https://www.booking.com/hotel/gr/aegean-view.html",
                city=location,
                address="Faliraki",
                property_type="Hotel",
                latitude=36.34,
                longitude=28.2,
            )
        ]

    def create_owned_property_and_discovery_job(self, account_id, request: OwnedPropertyOnboardingCreate):
        self.created = (account_id, request)
        return OwnedPropertyOnboardingResponse(
            owned_property_id=OWNED_PROPERTY_ID,
            discovery_job=ScrapeJobResponse(
                id=JOB_ID,
                account_id=account_id,
                owned_property_id=OWNED_PROPERTY_ID,
                job_type="owned_property_room_discovery",
                room_type_category=None,
                destination=request.city,
                raw_destination=request.raw_destination,
                canonical_destination=request.canonical_destination,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                rooms=request.rooms,
                filters_payload={"target_urls": [str(request.booking_url)]},
                status="queued",
                requested_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
                scrape_runs_count=0,
            ),
        )

    def automatic_setup(self, account_id, request):
        self.auto_setup_request = (account_id, request)
        discovery = ScrapeJobResponse(
            id=JOB_ID,
            account_id=account_id,
            owned_property_id=OWNED_PROPERTY_ID,
            job_type="owned_property_room_discovery",
            room_type_category=None,
            destination=request.location,
            raw_destination=request.location,
            canonical_destination="faliraki",
            check_in=request.check_in,
            check_out=request.check_out,
            adults=request.adults,
            children=request.children,
            rooms=request.rooms,
            filters_payload={"target_urls": ["https://www.booking.com/hotel/gr/aegean-view.html"]},
            status="queued",
            requested_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
            scrape_runs_count=0,
        )
        return AutomaticSetupResponse(
            owned_property_id=OWNED_PROPERTY_ID,
            selected_candidate=PropertyCandidate(
                candidate_key="candidate-1",
                display_name="Aegean View",
                booking_url="https://www.booking.com/hotel/gr/aegean-view.html",
                city=request.location,
                address="Faliraki",
            ),
            discovery_job=discovery,
        )

    def list_room_types(self, account_id, owned_property_id):
        return [
            OwnedPropertyRoomType(
                id=UUID("00000000-0000-0000-0000-000000000999"),
                owned_property_id=owned_property_id,
                room_type="Deluxe Double Room",
                room_type_category="double",
                sample_meals="Breakfast included",
                sample_free_cancellation="Yes",
                sample_facilities="Balcony|WiFi",
                sample_price_per_night_eur=120.0,
                is_active=True,
                created_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
            )
        ]

    def select_owned_property_room_type(self, account_id, owned_property_id, room_type_category):
        return SelectedRoomTypeResponse(
            owned_property_id=owned_property_id,
            selected_room_type_category=room_type_category,
            onboarding_complete=True,
        )

    def replace_owned_property_and_discovery_job(self, account_id, owned_property_id, request):
        self.replaced = (account_id, owned_property_id, request)
        if not self.replace_found:
            return None
        return OwnedPropertyOnboardingResponse(
            owned_property_id=owned_property_id,
            discovery_job=ScrapeJobResponse(
                id=JOB_ID,
                account_id=account_id,
                owned_property_id=owned_property_id,
                job_type="owned_property_room_discovery",
                room_type_category=None,
                destination=request.city,
                raw_destination=request.raw_destination,
                canonical_destination=request.canonical_destination,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                rooms=request.rooms,
                filters_payload={"target_urls": [str(request.booking_url)]},
                status="queued",
                requested_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
                scrape_runs_count=0,
            ),
        )

    def delete_owned_property(self, account_id, owned_property_id):
        self.deleted = (account_id, owned_property_id)
        return self.delete_result


def _client(service: FakeOnboardingService) -> TestClient:
    """Wire the account context and onboarding service, matching the
    inline overrides the older tests in this file already use."""
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    return TestClient(app)


def test_property_candidates_endpoint_returns_booking_candidates():
    service = FakeOnboardingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/v1/onboarding/property-candidates",
        params={
            "property_name": "Aegean View",
            "location": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["display_name"] == "Aegean View"

    app.dependency_overrides.clear()


def test_create_owned_property_starts_discovery_job():
    service = FakeOnboardingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/owned-property",
        json={
            "display_name": "Aegean View",
            "booking_url": "https://www.booking.com/hotel/gr/aegean-view.html",
            "city": "Faliraki",
            "raw_destination": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
            "adults": 2,
            "children": 0,
            "rooms": 1,
        },
    )

    assert response.status_code == 202
    assert response.json()["owned_property_id"] == str(OWNED_PROPERTY_ID)
    assert response.json()["discovery_job"]["job_type"] == "owned_property_room_discovery"
    assert response.json()["discovery_job"]["raw_destination"] == "Faliraki"
    assert response.json()["discovery_job"]["canonical_destination"] == "faliraki"
    assert service.created[0] == ACCOUNT_ID
    assert service.created[1].raw_destination == "Faliraki"
    assert service.created[1].canonical_destination == "faliraki"
    assert service.scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID)]

    app.dependency_overrides.clear()


def test_auto_setup_endpoint_auto_selects_candidate_and_starts_room_discovery():
    service = FakeOnboardingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/auto-setup",
        json={
            "property_name": "Aegean View",
            "location": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
            "adults": 2,
            "children": 0,
            "rooms": 1,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["owned_property_id"] == str(OWNED_PROPERTY_ID)
    assert payload["selected_candidate"]["display_name"] == "Aegean View"
    assert payload["discovery_job"]["job_type"] == "owned_property_room_discovery"
    assert service.auto_setup_request[0] == ACCOUNT_ID
    assert service.auto_setup_request[1].property_name == "Aegean View"
    assert service.auto_setup_request[1].location == "Faliraki"
    assert service.scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID)]

    app.dependency_overrides.clear()


def test_production_api_only_queues_onboarding_discovery(monkeypatch):
    service = FakeOnboardingService()
    monkeypatch.setattr(settings, "process_role", "api")
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service

    response = TestClient(app).post(
        "/api/v1/onboarding/auto-setup",
        json={
            "property_name": "Aegean View",
            "location": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 202
    assert service.scrape_job_service.run_calls == []
    app.dependency_overrides.clear()


def test_owned_property_room_types_endpoint_returns_discovered_types():
    service = FakeOnboardingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.get(f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}/room-types")

    assert response.status_code == 200
    assert response.json()[0]["room_type_category"] == "double"
    assert response.json()[0]["sample_meals"] == "Breakfast included"
    assert response.json()[0]["sample_free_cancellation"] == "Yes"
    assert response.json()[0]["sample_facilities"] == "Balcony|WiFi"

    app.dependency_overrides.clear()


def test_select_owned_property_room_type_persists_baseline_choice():
    service = FakeOnboardingService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}/selected-room-type",
        json={"room_type_category": "Double"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owned_property_id"] == str(OWNED_PROPERTY_ID)
    assert payload["selected_room_type_category"] == "double"
    assert payload["onboarding_complete"] is True

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# F5: quota refusals from the underlying scrape-job creation surface as 429
# here (consistent with the scrape_jobs router), while other ValueErrors stay
# 422. The discovery-job create_job call is where the quota guard fires.
# ---------------------------------------------------------------------------


class _QuotaRaisingOnboardingService(FakeOnboardingService):
    def __init__(self, error):
        super().__init__()
        self._error = error

    def create_owned_property_and_discovery_job(self, account_id, request):
        raise self._error

    def automatic_setup(self, account_id, request):
        raise self._error


def _owned_property_payload():
    return {
        "display_name": "Aegean View",
        "booking_url": "https://www.booking.com/hotel/gr/aegean-view.html",
        "city": "Faliraki",
        "raw_destination": "Faliraki",
        "check_in": "2030-07-01",
        "check_out": "2030-07-05",
        "adults": 2,
        "children": 0,
        "rooms": 1,
    }


def test_create_owned_property_maps_too_many_active_jobs_to_429():
    service = _QuotaRaisingOnboardingService(TooManyActiveJobsError("too many active jobs"))
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.post("/api/v1/onboarding/owned-property", json=_owned_property_payload())

    assert response.status_code == 429
    assert response.json()["detail"] == "too many active jobs"

    app.dependency_overrides.clear()


def test_create_owned_property_maps_daily_quota_to_429():
    service = _QuotaRaisingOnboardingService(DailyQuotaExceededError("daily quota exhausted"))
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.post("/api/v1/onboarding/owned-property", json=_owned_property_payload())

    assert response.status_code == 429

    app.dependency_overrides.clear()


def test_create_owned_property_other_value_error_stays_422():
    service = _QuotaRaisingOnboardingService(ValueError("bad input"))
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.post("/api/v1/onboarding/owned-property", json=_owned_property_payload())

    assert response.status_code == 422

    app.dependency_overrides.clear()


def test_auto_setup_maps_quota_error_to_429():
    service = _QuotaRaisingOnboardingService(DailyQuotaExceededError("daily quota exhausted"))
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/auto-setup",
        json={
            "property_name": "Aegean View",
            "location": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
            "adults": 2,
            "children": 0,
            "rooms": 1,
        },
    )

    assert response.status_code == 429

    app.dependency_overrides.clear()


def test_replace_owned_property_returns_202_and_the_discovery_job():
    service = FakeOnboardingService()
    client = _client(service)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}",
        json={
            "display_name": "Bellezza",
            "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
            "city": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["owned_property_id"] == str(OWNED_PROPERTY_ID)
    assert body["discovery_job"]["job_type"] == "owned_property_room_discovery"
    # The route forwarded the caller's identity, the path id and the parsed
    # body -- not some other property's.
    account_id, owned_property_id, request = service.replaced
    assert (account_id, owned_property_id) == (ACCOUNT_ID, OWNED_PROPERTY_ID)
    assert request.display_name == "Bellezza"
    # The job was scheduled to actually run, not just returned in the body
    # (process_role is "all" under tests, same as the POST assertions above).
    assert service.scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID)]

    app.dependency_overrides.clear()


def test_replace_owned_property_404s_when_not_found():
    """The tenancy half of the story (foreign account -> None) is proven at
    the repository layer; here the fake only demonstrates None -> 404."""
    service = FakeOnboardingService(replace_found=False)
    client = _client(service)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}",
        json={
            "display_name": "Bellezza",
            "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
            "city": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_replace_owned_property_rejects_a_past_check_in():
    """Same guard as create: Booking cannot price a stay that already started."""
    service = FakeOnboardingService()
    client = _client(service)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}",
        json={
            "display_name": "Bellezza",
            "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
            "city": "Faliraki",
            "check_in": "2020-07-01",
            "check_out": "2020-07-05",
        },
    )

    assert response.status_code == 422

    app.dependency_overrides.clear()


def test_delete_owned_property_returns_204():
    service = FakeOnboardingService()
    client = _client(service)

    response = client.delete(f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}")

    assert response.status_code == 204
    assert response.content == b""
    assert service.deleted == (ACCOUNT_ID, OWNED_PROPERTY_ID)

    app.dependency_overrides.clear()


def test_delete_owned_property_404s_when_missing():
    service = FakeOnboardingService(delete_result=False)
    client = _client(service)

    response = client.delete(f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}")

    assert response.status_code == 404

    app.dependency_overrides.clear()
