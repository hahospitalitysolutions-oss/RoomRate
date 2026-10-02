from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context, get_scrape_job_service
from api.main import app
from api.config import settings
from api.schemas.scrape_jobs import ScrapeJobCreate, ScrapeJobResponse


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000888")


class FakeScrapeJobService:
    def __init__(self):
        self.created = None
        self.run_calls = []

    def create_job(self, account_id: UUID, request: ScrapeJobCreate) -> ScrapeJobResponse:
        self.created = (account_id, request)
        return ScrapeJobResponse(
            id=JOB_ID,
            account_id=account_id,
            owned_property_id=request.owned_property_id,
            destination=request.destination,
            check_in=request.check_in,
            check_out=request.check_out,
            adults=request.adults,
            children=request.children,
            rooms=request.rooms,
            job_type=request.job_type,
            room_type_category=request.room_type_category,
            filters_payload=request.filters_payload,
            status="queued",
            requested_at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
            started_at=None,
            finished_at=None,
            error_message=None,
            scrape_runs_count=0,
        )

    def run_job(self, account_id: UUID, job_id: UUID) -> None:
        self.run_calls.append((account_id, job_id))


def test_create_scrape_job_endpoint_returns_queued_job_and_schedules_runner():
    service = FakeScrapeJobService()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/scrape-jobs/",
        json={
            "destination": "Rhodes",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "job_type": "competitor_search",
            "room_type_category": "double",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["id"] == str(JOB_ID)
    assert payload["status"] == "queued"
    assert service.created[0] == ACCOUNT_ID
    assert service.created[1].destination == "Rhodes"
    assert service.created[1].job_type == "competitor_search"
    assert service.created[1].room_type_category == "double"
    assert service.run_calls == [(ACCOUNT_ID, JOB_ID)]

    app.dependency_overrides.clear()


def test_api_process_only_persists_job_for_python_worker(monkeypatch):
    service = FakeScrapeJobService()
    monkeypatch.setattr(settings, "process_role", "api")
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: service

    response = TestClient(app).post(
        "/api/v1/scrape-jobs/",
        json={
            "destination": "Rhodes",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 202
    assert service.run_calls == []
    app.dependency_overrides.clear()


def test_create_scrape_job_endpoint_maps_quota_errors_to_429():
    from api.repositories.scrape_jobs_repository import TooManyActiveJobsError

    class QuotaLimitedService(FakeScrapeJobService):
        def create_job(self, account_id, request):
            raise TooManyActiveJobsError("Too many active scrape jobs for this account")

    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: QuotaLimitedService()
    client = TestClient(app)

    response = client.post(
        "/api/v1/scrape-jobs/",
        json={
            "destination": "Rhodes",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 429
    assert "Too many active scrape jobs" in response.json()["detail"]

    app.dependency_overrides.clear()


def test_create_scrape_job_endpoint_rejects_invalid_dates():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: FakeScrapeJobService()
    client = TestClient(app)

    response = client.post(
        "/api/v1/scrape-jobs/",
        json={
            "destination": "Rhodes",
            "check_in": "2030-07-05",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 422

    app.dependency_overrides.clear()
