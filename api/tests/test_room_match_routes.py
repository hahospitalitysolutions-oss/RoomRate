"""Route tests for POST /api/v1/agents/room-matches (spec 2026-09-29 Α.1).

The endpoint is synchronous and account-scoped: the job must belong to the
account and be completed, and ONE run covers every room type of the job's
property (owner decision 2026-11-13). The response is exactly ``{status,
matches_written, room_types_matched, skip_reason, source: "agent"}`` — the
map mirrors this contract. Quota exhaustion is a 429, like the pricing
endpoint.
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
    get_room_matching_service,
    get_scrape_job_service,
)
from api.main import app
from api.rate_limit import RATE_LIMITED_POST_PATHS
from api.schemas.scrape_jobs import ScrapeJobResponse
from api.services.room_matching_agent import RoomMatchRunResult


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


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
            "owned_property_id": OWNED_PROPERTY_ID,
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


class FakeRoomMatchingService:
    def __init__(self, result: RoomMatchRunResult | None):
        self.result = result
        self.calls = []

    def run_for_job(self, account_id, job, *, only_missing, wait_for_run_in_flight):
        self.calls.append(
            {
                "account_id": account_id,
                "job": job,
                "only_missing": only_missing,
                "wait_for_run_in_flight": wait_for_run_in_flight,
            }
        )
        return self.result


_COMPLETED = RoomMatchRunResult(status="completed", matches_written=12, room_types_matched=3)


def _client(*, job=None, result=_COMPLETED):
    jobs = FakeScrapeJobService(job=job)
    matcher = FakeRoomMatchingService(result=result)
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: jobs
    app.dependency_overrides[get_room_matching_service] = lambda: matcher
    return TestClient(app), jobs, matcher


def _post(client, **body):
    return client.post(
        "/api/v1/agents/room-matches",
        json={"scrape_job_id": str(JOB_ID), **body},
    )


def test_completed_run_returns_the_exact_contract_payload():
    client, jobs, matcher = _client(job=_job())

    response = _post(client)

    assert response.status_code == 200
    # The frontend lane mirrors this exact JSON.
    assert response.json() == {
        "status": "completed",
        "matches_written": 12,
        "room_types_matched": 3,
        "skip_reason": None,
        "source": "agent",
    }
    assert jobs.calls == [(ACCOUNT_ID, JOB_ID)]
    call = matcher.calls[0]
    assert call["account_id"] == ACCOUNT_ID
    assert call["job"].id == JOB_ID
    # «Νέο ταίριασμα» REPLACES every room type's rows, and the endpoint
    # waits for a run already in flight instead of paying for a second one.
    assert call["only_missing"] is False
    assert call["wait_for_run_in_flight"] is True


def test_the_call_after_a_search_scores_only_the_missing_room_types():
    client, _, matcher = _client(job=_job())

    response = _post(client, only_missing=True)

    assert response.status_code == 200
    assert matcher.calls[0]["only_missing"] is True
    assert matcher.calls[0]["wait_for_run_in_flight"] is True


def test_an_older_client_still_sending_one_room_type_is_accepted():
    client, _, matcher = _client(job=_job())

    response = _post(client, owned_room_type_id="00000000-0000-0000-0000-000000000abc")

    assert response.status_code == 200
    assert matcher.calls[0]["only_missing"] is False


def test_missing_job_is_404_and_the_agent_never_runs():
    client, _, matcher = _client(job=None)

    response = _post(client)

    assert response.status_code == 404
    assert "Scrape job" in response.json()["detail"]
    assert matcher.calls == []


def test_uncompleted_job_is_409():
    client, _, matcher = _client(job=_job(status="running"))

    response = _post(client)

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]
    assert matcher.calls == []


def test_a_property_without_room_types_is_409():
    client, _, _ = _client(job=_job(), result=None)

    response = _post(client)

    assert response.status_code == 409
    assert "room types" in response.json()["detail"].lower()


def test_quota_exhaustion_is_429_like_the_pricing_endpoint():
    client, _, _ = _client(
        job=_job(),
        result=RoomMatchRunResult(status="skipped", skip_reason="quota_exceeded"),
    )

    response = _post(client)

    assert response.status_code == 429
    assert "quota" in response.json()["detail"].lower()


def test_no_api_key_is_a_200_skip():
    client, _, _ = _client(
        job=_job(),
        result=RoomMatchRunResult(status="skipped", skip_reason="no_api_key"),
    )

    response = _post(client)

    assert response.status_code == 200
    assert response.json() == {
        "status": "skipped",
        "matches_written": 0,
        "room_types_matched": 0,
        "skip_reason": "no_api_key",
        "source": "agent",
    }


def test_agent_error_is_a_200_error_status_not_a_5xx():
    """Spec Α.5: this path never surfaces an error to the user as a failure."""
    client, _, _ = _client(
        job=_job(),
        result=RoomMatchRunResult(status="error", matches_written=0),
    )

    response = _post(client)

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["matches_written"] == 0


def test_a_run_still_in_progress_after_the_wait_is_a_200_skip_with_its_reason():
    """Another run holds the job past the wait: the page can say «still
    running» instead of «AI unavailable»."""
    client, _, _ = _client(
        job=_job(),
        result=RoomMatchRunResult(status="skipped", skip_reason="in_progress"),
    )

    response = _post(client, only_missing=True)

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert response.json()["skip_reason"] == "in_progress"


def test_room_matches_post_is_rate_limited_like_the_other_llm_paths():
    assert "/api/v1/agents/room-matches" in RATE_LIMITED_POST_PATHS
