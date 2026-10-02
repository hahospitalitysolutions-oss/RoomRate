"""Route tests for POST /api/v1/agents/room-matches (spec 2026-09-29 Α.1).

The endpoint is synchronous and account-scoped: the job must belong to the
account and be completed, the owned room row must exist, and the response is
exactly ``{status, matches_written, source: "agent"}`` — the frontend's
«Επανεκτίμηση ταιριάσματος» button mirrors this contract. Quota exhaustion is
a 429, like the pricing endpoint.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_onboarding_repository,
    get_room_matching_service,
    get_scrape_job_service,
)
from api.main import app
from api.rate_limit import RATE_LIMITED_POST_PATHS
from api.schemas.scrape_jobs import ScrapeJobResponse
from api.services.room_matching_agent import RoomMatchRunResult


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
ROOM_TYPE_ID = UUID("00000000-0000-0000-0000-000000000abc")


@pytest.fixture(autouse=True)
def _rate_limit_disabled(monkeypatch):
    """Keep this file's POSTs out of the process-wide rate-limit bucket."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)


def teardown_function():
    app.dependency_overrides.clear()


def _job(status: str = "completed") -> ScrapeJobResponse:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    return ScrapeJobResponse.model_validate(
        {
            "id": JOB_ID,
            "account_id": ACCOUNT_ID,
            "owned_property_id": None,
            "destination": "Rhodes",
            "check_in": date(2026, 10, 1),
            "check_out": date(2026, 10, 5),
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "job_type": "competitor_search",
            "room_type_category": "double",
            "filters_payload": {},
            "scheduled": False,
            "status": status,
            "requested_at": now,
            "started_at": None,
            "finished_at": None,
            "error_message": None,
            "attempt_count": 1,
            "max_attempts": 3,
            "next_attempt_at": None,
        }
    )


class FakeScrapeJobService:
    def __init__(self, job=None):
        self.job = job
        self.calls = []

    def get_job(self, account_id, job_id):
        self.calls.append((account_id, job_id))
        return self.job


class FakeOnboardingRepository:
    def __init__(self, owned_room_type=None):
        self.owned_room_type = owned_room_type
        self.calls = []

    def get_owned_room_type(self, account_id, owned_room_type_id):
        self.calls.append((account_id, owned_room_type_id))
        return self.owned_room_type


class FakeRoomMatchingService:
    def __init__(self, result: RoomMatchRunResult):
        self.result = result
        self.calls = []

    def run(self, account_id, scrape_job_id, owned_room, *, skip_if_existing=False):
        self.calls.append(
            {
                "account_id": account_id,
                "scrape_job_id": scrape_job_id,
                "owned_room": owned_room,
                "skip_if_existing": skip_if_existing,
            }
        )
        return self.result


def _owned_room() -> dict:
    return {
        "id": ROOM_TYPE_ID,
        "owned_property_id": UUID("00000000-0000-0000-0000-000000000456"),
        "room_type": "Double Room",
        "room_type_category": "double",
        "sample_price_per_night_eur": 95.0,
    }


def _client(
    *,
    job=None,
    owned_room_type=None,
    result=RoomMatchRunResult(status="completed", matches_written=3),
):
    jobs = FakeScrapeJobService(job=job)
    onboarding = FakeOnboardingRepository(owned_room_type=owned_room_type)
    matcher = FakeRoomMatchingService(result=result)
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: jobs
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding
    app.dependency_overrides[get_room_matching_service] = lambda: matcher
    return TestClient(app), jobs, onboarding, matcher


def _post(client):
    return client.post(
        "/api/v1/agents/room-matches",
        json={"scrape_job_id": str(JOB_ID), "owned_room_type_id": str(ROOM_TYPE_ID)},
    )


def test_completed_run_returns_the_exact_contract_payload():
    client, jobs, onboarding, matcher = _client(job=_job(), owned_room_type=_owned_room())

    response = _post(client)

    assert response.status_code == 200
    # The frontend lane mirrors this exact JSON.
    assert response.json() == {"status": "completed", "matches_written": 3, "source": "agent"}
    assert jobs.calls == [(ACCOUNT_ID, JOB_ID)]
    assert onboarding.calls == [(ACCOUNT_ID, ROOM_TYPE_ID)]
    call = matcher.calls[0]
    assert call["account_id"] == ACCOUNT_ID
    assert call["scrape_job_id"] == JOB_ID
    assert call["owned_room"]["id"] == ROOM_TYPE_ID
    # Manual runs REPLACE existing rows («Επανεκτίμηση»), never skip on them.
    assert call["skip_if_existing"] is False


def test_missing_job_is_404_and_the_agent_never_runs():
    client, _, _, matcher = _client(job=None, owned_room_type=_owned_room())

    response = _post(client)

    assert response.status_code == 404
    assert "Scrape job" in response.json()["detail"]
    assert matcher.calls == []


def test_uncompleted_job_is_409():
    client, _, _, matcher = _client(job=_job(status="running"), owned_room_type=_owned_room())

    response = _post(client)

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]
    assert matcher.calls == []


def test_missing_owned_room_type_is_404():
    client, _, _, matcher = _client(job=_job(), owned_room_type=None)

    response = _post(client)

    assert response.status_code == 404
    assert "room type" in response.json()["detail"].lower()
    assert matcher.calls == []


def test_quota_exhaustion_is_429_like_the_pricing_endpoint():
    client, _, _, _ = _client(
        job=_job(),
        owned_room_type=_owned_room(),
        result=RoomMatchRunResult(status="skipped", skip_reason="quota_exceeded"),
    )

    response = _post(client)

    assert response.status_code == 429
    assert "quota" in response.json()["detail"].lower()


def test_no_api_key_is_a_200_skip():
    client, _, _, _ = _client(
        job=_job(),
        owned_room_type=_owned_room(),
        result=RoomMatchRunResult(status="skipped", skip_reason="no_api_key"),
    )

    response = _post(client)

    assert response.status_code == 200
    assert response.json() == {"status": "skipped", "matches_written": 0, "source": "agent"}


def test_agent_error_is_a_200_error_status_not_a_5xx():
    """Spec Α.5: this path never surfaces an error to the user as a failure."""
    client, _, _, _ = _client(
        job=_job(),
        owned_room_type=_owned_room(),
        result=RoomMatchRunResult(status="error", matches_written=0),
    )

    response = _post(client)

    assert response.status_code == 200
    assert response.json() == {"status": "error", "matches_written": 0, "source": "agent"}


def test_room_matches_post_is_rate_limited_like_the_other_llm_paths():
    assert "/api/v1/agents/room-matches" in RATE_LIMITED_POST_PATHS
