import ast
import re
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.scrape_jobs_repository import ScrapeJobRepository
from api.schemas.scrape_jobs import ScrapeJobCreate
from api.services.scrape_job_service import (
    BookingScrapeJobRunner,
    ScrapeJobCommand,
    ScrapeJobService,
    ScrapeJobTimeoutError,
)
from api.tests._fakes import FakeResult, install_scripted_engine


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")


def _job(status: str = "queued") -> dict:
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    return {
        "id": JOB_ID,
        "account_id": ACCOUNT_ID,
        "owned_property_id": None,
        "destination": "Rhodes",
        "check_in": date(2026, 7, 1),
        "check_out": date(2026, 7, 5),
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
        "attempt_count": 0,
        "max_attempts": 3,
        "next_attempt_at": None,
        "scrape_runs_count": 0,
    }


def _command(**overrides) -> ScrapeJobCommand:
    defaults = dict(
        job_id=JOB_ID,
        account_id=ACCOUNT_ID,
        destination="Rhodes",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        job_type="competitor_search",
        room_type_category="double",
        filters_payload={},
    )
    defaults.update(overrides)
    return ScrapeJobCommand(**defaults)


class FakeScrapeJobRepository:
    def __init__(
        self,
        claim_result: bool = True,
        transition_applies: bool = True,
        union_error: Exception | None = None,
    ):
        self.created = None
        self.jobs = {JOB_ID: _job()}
        self.transitions = []
        self.claims = []
        self.heartbeats = []
        self.completions = []
        self.room_unions = []
        self.claim_result = claim_result
        # False simulates the sweeper having moved the job out of 'running'
        # before the worker's terminal UPDATE landed.
        self.transition_applies = transition_applies
        self.union_error = union_error

    def union_owned_room_types_from_job(self, account_id, job_id):
        # Records the job's status at call time: the union must only ever run
        # AFTER the completion transition committed.
        self.room_unions.append((account_id, job_id, self.jobs[job_id]["status"]))
        if self.union_error:
            raise self.union_error
        return 2

    def create_job(self, account_id, request, scheduled=False):
        self.created = (account_id, request, scheduled)
        return {**self.jobs[JOB_ID], "scheduled": scheduled}

    def get_job(self, account_id, job_id):
        if account_id != ACCOUNT_ID:
            return None
        return self.jobs.get(job_id)

    def list_jobs(self, account_id, limit=50):
        return list(self.jobs.values())

    def claim_job(self, account_id, job_id, claimed_by):
        self.claims.append((account_id, job_id, claimed_by))
        if not self.claim_result:
            return False
        self.transitions.append(("running", account_id, job_id))
        self.jobs[job_id] = {
            **self.jobs[job_id],
            "status": "running",
            "attempt_count": self.jobs[job_id]["attempt_count"] + 1,
        }
        return True

    def heartbeat(self, account_id, job_id):
        self.heartbeats.append((account_id, job_id))

    def complete_job(self, account_id, job_id, refresh_owned_property_id=None, result_summary=None):
        self.completions.append((account_id, job_id, refresh_owned_property_id, result_summary))
        if not self.transition_applies:
            return False, 0
        self.transitions.append(("completed", account_id, job_id))
        self.jobs[job_id] = {**self.jobs[job_id], "status": "completed"}
        return True, 2 if refresh_owned_property_id else 0

    def mark_failed(self, account_id, job_id, error_message):
        if not self.transition_applies:
            return False
        self.transitions.append(("failed", account_id, job_id, error_message))
        self.jobs[job_id] = {**self.jobs[job_id], "status": "failed", "error_message": error_message}
        return True

    def retry_or_fail(self, account_id, job_id, error_message, retry_delay_seconds):
        if not self.transition_applies:
            return None
        job = self.jobs[job_id]
        next_status = "queued" if job["attempt_count"] < job["max_attempts"] else "failed"
        self.transitions.append(
            (next_status, account_id, job_id, error_message, retry_delay_seconds)
        )
        self.jobs[job_id] = {
            **job,
            "status": next_status,
            "error_message": error_message,
        }
        return next_status


class FakeRunner:
    def __init__(
        self,
        error: Exception | None = None,
        heartbeat_calls: int = 0,
        result_summary: dict | None = None,
    ):
        self.error = error
        self.heartbeat_calls = heartbeat_calls
        self.result_summary = result_summary
        self.commands: list[ScrapeJobCommand] = []

    def run(self, command: ScrapeJobCommand, heartbeat=None) -> dict | None:
        self.commands.append(command)
        for _ in range(self.heartbeat_calls):
            if heartbeat is not None:
                heartbeat()
        if self.error:
            raise self.error
        return self.result_summary


class FakeAlertEvaluator:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[UUID, object]] = []
        self.error = error

    def evaluate_completed_job(self, account_id, job) -> int:
        self.calls.append((account_id, job))
        if self.error:
            raise self.error
        return 1


def test_run_job_invokes_alert_evaluator_for_completed_competitor_search():
    repository = FakeScrapeJobRepository()
    evaluator = FakeAlertEvaluator()
    service = ScrapeJobService(
        repository=repository, runner=FakeRunner(), alert_evaluator=evaluator
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert ran is True
    assert len(evaluator.calls) == 1
    account_id, job = evaluator.calls[0]
    assert account_id == ACCOUNT_ID
    # The evaluator receives the in-scope job object (job_type competitor_search).
    assert job.job_type == "competitor_search"
    assert job.id == JOB_ID


def test_run_job_skips_alert_evaluator_for_non_competitor_search():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {
        **_job(),
        "owned_property_id": UUID("00000000-0000-0000-0000-000000000456"),
        "job_type": "owned_property_room_discovery",
        "room_type_category": None,
    }
    evaluator = FakeAlertEvaluator()
    service = ScrapeJobService(
        repository=repository, runner=FakeRunner(), alert_evaluator=evaluator
    )

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert evaluator.calls == []


def test_run_job_skips_alert_evaluator_when_run_failed():
    repository = FakeScrapeJobRepository()
    evaluator = FakeAlertEvaluator()
    service = ScrapeJobService(
        repository=repository,
        runner=FakeRunner(RuntimeError("Apify unavailable")),
        alert_evaluator=evaluator,
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    # The job ran (True) but failed, so there is no completed scrape to evaluate.
    assert ran is True
    assert evaluator.calls == []


def test_run_job_alert_evaluator_exception_never_changes_result():
    repository = FakeScrapeJobRepository()
    evaluator = FakeAlertEvaluator(error=RuntimeError("evaluator blew up"))
    service = ScrapeJobService(
        repository=repository, runner=FakeRunner(), alert_evaluator=evaluator
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    # Alert failure must not affect job status or run_job's bool.
    assert ran is True
    assert repository.jobs[JOB_ID]["status"] == "completed"
    assert len(evaluator.calls) == 1


def test_run_job_without_evaluator_still_completes():
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(repository=repository, runner=FakeRunner())

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True


class FakeRoomMatcher:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[UUID, object]] = []
        self.error = error

    def run_for_completed_job(self, account_id, job):
        self.calls.append((account_id, job))
        if self.error:
            raise self.error
        return None


def test_run_job_triggers_room_matching_for_completed_competitor_search():
    repository = FakeScrapeJobRepository()
    matcher = FakeRoomMatcher()
    service = ScrapeJobService(
        repository=repository, runner=FakeRunner(), room_matcher=matcher
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert ran is True
    assert len(matcher.calls) == 1
    account_id, job = matcher.calls[0]
    assert account_id == ACCOUNT_ID
    assert job.id == JOB_ID
    assert job.job_type == "competitor_search"


def test_run_job_skips_room_matching_for_non_competitor_search():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {
        **_job(),
        "owned_property_id": UUID("00000000-0000-0000-0000-000000000456"),
        "job_type": "owned_property_room_discovery",
        "room_type_category": None,
    }
    matcher = FakeRoomMatcher()
    service = ScrapeJobService(
        repository=repository, runner=FakeRunner(), room_matcher=matcher
    )

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert matcher.calls == []


def test_run_job_skips_room_matching_when_run_failed():
    repository = FakeScrapeJobRepository()
    matcher = FakeRoomMatcher()
    service = ScrapeJobService(
        repository=repository,
        runner=FakeRunner(RuntimeError("Apify unavailable")),
        room_matcher=matcher,
    )

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True
    assert matcher.calls == []


def test_run_job_room_matcher_exception_never_changes_result():
    """Matching is best-effort exactly like the price alerts (spec A.1)."""
    repository = FakeScrapeJobRepository()
    evaluator = FakeAlertEvaluator()
    matcher = FakeRoomMatcher(error=RuntimeError("agent blew up"))
    service = ScrapeJobService(
        repository=repository,
        runner=FakeRunner(),
        alert_evaluator=evaluator,
        room_matcher=matcher,
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert ran is True
    assert repository.jobs[JOB_ID]["status"] == "completed"
    assert len(matcher.calls) == 1
    # The alert path already ran; a matcher failure must not undo that either.
    assert len(evaluator.calls) == 1


@pytest.mark.parametrize("job_type", ["competitor_search", "owned_property_room_discovery"])
def test_run_job_unions_owned_room_types_after_every_completed_job(job_type):
    """The owner's hotel shows up in competitor runs too, not only discovery."""
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {
        **_job(),
        "owned_property_id": UUID("00000000-0000-0000-0000-000000000456"),
        "job_type": job_type,
        "room_type_category": None,
    }
    service = ScrapeJobService(repository=repository, runner=FakeRunner())

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True

    # Exactly once, and only after the completion transition committed.
    assert repository.room_unions == [(ACCOUNT_ID, JOB_ID, "completed")]


def test_run_job_unions_owned_room_types_before_room_matching():
    """This job's matcher should already see the rooms the union just added."""
    order: list[str] = []
    repository = FakeScrapeJobRepository()
    original_union = repository.union_owned_room_types_from_job

    def _recording_union(account_id, job_id):
        order.append("union")
        return original_union(account_id, job_id)

    repository.union_owned_room_types_from_job = _recording_union

    class _OrderMatcher(FakeRoomMatcher):
        def run_for_completed_job(self, account_id, job):
            order.append("matching")
            return super().run_for_completed_job(account_id, job)

    service = ScrapeJobService(repository=repository, runner=FakeRunner(), room_matcher=_OrderMatcher())

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert order == ["union", "matching"]


def test_run_job_skips_owned_room_union_when_completion_did_not_apply():
    repository = FakeScrapeJobRepository(transition_applies=False)
    service = ScrapeJobService(repository=repository, runner=FakeRunner())

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.room_unions == []


def test_run_job_skips_owned_room_union_when_run_failed():
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(repository=repository, runner=FakeRunner(RuntimeError("Apify unavailable")))

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.room_unions == []


def test_run_job_owned_room_union_failure_is_swallowed():
    """Best-effort: a union failure never changes the job or the later hooks."""
    repository = FakeScrapeJobRepository(union_error=RuntimeError("union blew up"))
    evaluator = FakeAlertEvaluator()
    matcher = FakeRoomMatcher()
    service = ScrapeJobService(
        repository=repository,
        runner=FakeRunner(),
        alert_evaluator=evaluator,
        room_matcher=matcher,
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert ran is True
    assert repository.jobs[JOB_ID]["status"] == "completed"
    assert len(repository.room_unions) == 1
    assert len(evaluator.calls) == 1
    assert len(matcher.calls) == 1


def test_run_job_owned_room_union_failure_in_real_repository_is_swallowed(monkeypatch):
    """Service through real repository SQL: the union's own transaction fails
    AFTER the completion committed, and the job still reports completed."""
    engine = install_scripted_engine(
        monkeypatch,
        "api.repositories.scrape_jobs_repository",
        [
            FakeResult(rows=[_job()]),             # get_job
            FakeResult(rows=[{"id": JOB_ID}]),     # claim_job wins the claim
            FakeResult(rowcount=1),                # status update to completed
            # No scripted result for the union's SELECT: the fake raises
            # IndexError, standing in for a database error.
        ],
    )
    service = ScrapeJobService(repository=ScrapeJobRepository(), runner=FakeRunner())

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True

    union_sql = engine.connection.calls[-1][0]
    assert "lower(trim(op.display_name)) = lower(trim(p.display_name))" in union_sql
    # Completion was its own, earlier transaction; the union opened another.
    assert engine.begin_calls >= 3


def test_create_job_persists_account_scoped_request():
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(repository=repository, runner=FakeRunner())
    request = ScrapeJobCreate(
        destination="Rhodes",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
    )

    response = service.create_job(account_id=ACCOUNT_ID, request=request)

    assert response.id == JOB_ID
    assert response.status == "queued"
    assert repository.created == (ACCOUNT_ID, request, False)


def test_client_cannot_spoof_scheduled_flag_on_manual_create():
    # A manual create request that smuggles "scheduled": True must NOT result in
    # a scheduled job: the schema strips the legacy filters_payload key and the
    # repository only receives scheduled=True from the scheduler path.
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(repository=repository, runner=FakeRunner())
    request = ScrapeJobCreate(
        destination="Rhodes",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        filters_payload={"scheduled": True, "limit": 10},
    )

    # The schema already stripped the spoofed flag at validation time.
    assert "scheduled" not in request.filters_payload
    assert request.filters_payload == {"limit": 10}

    service.create_job(account_id=ACCOUNT_ID, request=request)

    assert repository.created == (ACCOUNT_ID, request, False)


def test_scheduler_path_sets_scheduled_flag_via_explicit_parameter():
    # The scheduler path (create_job(..., scheduled=True)) is the only way the
    # scheduled column is set; the runner and breaker key on it downstream.
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(repository=repository, runner=FakeRunner())
    request = ScrapeJobCreate(
        destination="Rhodes",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        filters_payload={"limit": 25},
    )

    response = service.create_job(account_id=ACCOUNT_ID, request=request, scheduled=True)

    assert repository.created == (ACCOUNT_ID, request, True)
    assert response.scheduled is True
    # The flag lives in its own column now, never inside filters_payload.
    assert "scheduled" not in request.filters_payload


def test_scrape_job_create_derives_raw_and_canonical_destination():
    request = ScrapeJobCreate(
        destination=" Φαληράκι ",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
    )

    assert request.destination == "Φαληράκι"
    assert request.raw_destination == "Φαληράκι"
    assert request.canonical_destination == "faliraki"


def test_run_job_claims_then_completes_and_invokes_runner_with_job_scope():
    repository = FakeScrapeJobRepository()
    runner = FakeRunner()
    service = ScrapeJobService(repository=repository, runner=runner)

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    # True: this call claimed and executed the job (executor counts it and
    # owns the breaker feedback for its outcome).
    assert ran is True
    assert repository.transitions == [
        ("running", ACCOUNT_ID, JOB_ID),
        ("completed", ACCOUNT_ID, JOB_ID),
    ]
    # The claim identifies the executing worker so stuck jobs can be traced.
    assert repository.claims[0][0] == ACCOUNT_ID
    assert repository.claims[0][1] == JOB_ID
    assert ":" in repository.claims[0][2]
    assert repository.completions == [(ACCOUNT_ID, JOB_ID, None, None)]
    command = runner.commands[0]
    assert command.job_id == JOB_ID
    assert command.account_id == ACCOUNT_ID
    assert command.destination == "Rhodes"
    assert command.check_in == date(2026, 7, 1)
    assert command.check_out == date(2026, 7, 5)
    assert command.adults == 2
    assert command.children == 0
    assert command.rooms == 1
    assert command.job_type == "competitor_search"
    assert command.room_type_category == "double"


def test_run_job_persists_the_successful_attempt_summary_in_completion():
    summary = {
        "version": 1,
        "rows_seen": 4,
        "filter_counts": {"room_type_category": {"before": 4, "after": 0}},
        "rows_written": 0,
    }
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(repository=repository, runner=FakeRunner(result_summary=summary))

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.completions == [(ACCOUNT_ID, JOB_ID, None, summary)]


def test_run_job_returns_false_and_skips_execution_when_claim_is_lost():
    repository = FakeScrapeJobRepository(claim_result=False)
    runner = FakeRunner()
    service = ScrapeJobService(repository=repository, runner=runner)

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    # False: another worker won the claim; this call must not report having
    # executed anything (the executor skips breaker feedback on False).
    assert ran is False
    assert runner.commands == []
    assert repository.transitions == []
    assert repository.completions == []


def test_run_job_returns_false_when_job_is_missing():
    repository = FakeScrapeJobRepository()
    runner = FakeRunner()
    service = ScrapeJobService(repository=repository, runner=runner)

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=UUID("00000000-0000-0000-0000-000000000999"))

    assert ran is False
    assert runner.commands == []
    assert repository.claims == []


def test_run_job_forwards_heartbeat_to_repository():
    repository = FakeScrapeJobRepository()
    runner = FakeRunner(heartbeat_calls=3)
    service = ScrapeJobService(repository=repository, runner=runner)

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.heartbeats == [(ACCOUNT_ID, JOB_ID)] * 3


def test_run_job_refreshes_owned_property_room_types_in_completion():
    owned_property_id = UUID("00000000-0000-0000-0000-000000000456")
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {
        **_job(),
        "owned_property_id": owned_property_id,
        "job_type": "owned_property_room_discovery",
        "room_type_category": None,
    }
    service = ScrapeJobService(repository=repository, runner=FakeRunner())

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.completions == [(ACCOUNT_ID, JOB_ID, owned_property_id, None)]


def test_run_job_discovery_completion_syncs_catalog_without_selecting_a_room(monkeypatch):
    """Whole discovery-completion path, service through real repository SQL.

    The fake repository above can only prove the service ASKS for the
    refresh. This wires the real ScrapeJobRepository onto a recording
    connection so the statements the completion actually issues are visible:
    the room catalog is synced, and the property's baseline room category is
    left alone for the user's explicit step-2 choice.
    """
    owned_property_id = UUID("00000000-0000-0000-0000-000000000456")
    job_row = {
        **_job(),
        "owned_property_id": owned_property_id,
        "job_type": "owned_property_room_discovery",
        "room_type_category": None,
    }
    engine = install_scripted_engine(
        monkeypatch,
        "api.repositories.scrape_jobs_repository",
        [
            FakeResult(rows=[job_row]),            # get_job
            FakeResult(rows=[{"id": JOB_ID}]),     # claim_job wins the claim
            FakeResult(rowcount=4),                # room-type catalog sync
            FakeResult(rowcount=1),                # status update to completed
            FakeResult(rows=[]),                   # owned-room union: nothing new
        ],
    )
    service = ScrapeJobService(repository=ScrapeJobRepository(), runner=FakeRunner())

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True

    executed_sql = " ".join(sql for sql, _ in engine.connection.calls)
    assert "INSERT INTO roomrate_owned_property_room_types" in executed_sql
    assert "status = 'completed'" in executed_sql
    assert "selected_room_type_category" not in executed_sql
    # The owned-room union READS the owned property (to find the owner's
    # hotel by name); nothing on this path may WRITE it.
    assert not re.search(r"(UPDATE|INSERT INTO|DELETE FROM)\s+roomrate_owned_properties\b", executed_sql)


def test_run_job_requeues_retryable_failure_when_attempts_remain():
    repository = FakeScrapeJobRepository()
    service = ScrapeJobService(
        repository=repository,
        runner=FakeRunner(RuntimeError("Apify unavailable")),
        retry_base_seconds=30,
        retry_max_seconds=30,
    )

    ran = service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    # This worker owned the attempt, but the durable state returns to queued.
    assert ran is True
    assert repository.transitions[0] == ("running", ACCOUNT_ID, JOB_ID)
    assert repository.transitions[1][0:4] == (
        "queued", ACCOUNT_ID, JOB_ID, "Apify unavailable"
    )
    assert repository.jobs[JOB_ID]["status"] == "queued"
    assert repository.completions == []


def test_run_job_marks_retryable_failure_terminal_when_attempts_exhausted():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {**repository.jobs[JOB_ID], "max_attempts": 1}
    service = ScrapeJobService(
        repository=repository,
        runner=FakeRunner(RuntimeError("Apify unavailable")),
        retry_base_seconds=30,
        retry_max_seconds=30,
    )

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.transitions[1][0] == "failed"
    assert repository.jobs[JOB_ID]["status"] == "failed"


def test_run_job_warns_when_completed_transition_did_not_apply(caplog):
    # Simulates the Phase C sweeper having already failed the job: the fenced
    # UPDATE matches no row and the worker must not pretend it completed.
    repository = FakeScrapeJobRepository(transition_applies=False)
    service = ScrapeJobService(repository=repository, runner=FakeRunner())

    with caplog.at_level("WARNING", logger="api.services.scrape_job_service"):
        service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.completions == [(ACCOUNT_ID, JOB_ID, None, None)]
    assert repository.jobs[JOB_ID]["status"] == "running"  # not resurrected
    assert any("completed-transition did not apply" in record.message for record in caplog.records)


def test_run_job_warns_when_failed_transition_did_not_apply(caplog):
    repository = FakeScrapeJobRepository(transition_applies=False)
    service = ScrapeJobService(
        repository=repository, runner=FakeRunner(RuntimeError("Apify unavailable"))
    )

    with caplog.at_level("WARNING", logger="api.services.scrape_job_service"):
        service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.completions == []
    assert any("failed-transition did not apply" in record.message for record in caplog.records)


def test_create_job_rejects_invalid_date_range():
    with pytest.raises(ValueError, match="check_out must be after check_in"):
        ScrapeJobCreate(
            destination="Rhodes",
            check_in=date(2026, 7, 5),
            check_out=date(2026, 7, 5),
        )


# ----------------------------------------------------------------------------
# BookingScrapeJobRunner._build_args — pure CLI-arg construction
# ----------------------------------------------------------------------------


def test_booking_runner_args_force_live_scout_and_pass_job_limits():
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    args = runner._build_args(
        _command(
            filters_payload={
                "limit": 25,
                "deep_crawl_max_items": 60,
                "amenities": ["wifi", "pool"],
                "target_urls": ["https://www.booking.com/hotel/gr/example.html"],
            },
        )
    )

    assert args[args.index("--scout-cache-hours") + 1] == "0"
    assert args[args.index("--scout-max-items") + 1] == "25"
    assert args[args.index("--deep-crawl-max-items") + 1] == "60"
    assert args[args.index("--required-amenity") + 1] == "wifi"
    assert args[args.index("--required-amenity", args.index("--required-amenity") + 1) + 1] == "pool"
    assert args[args.index("--target-url") + 1] == "https://www.booking.com/hotel/gr/example.html"


def test_booking_runner_args_reuse_scout_cache_for_scheduled_jobs():
    # Scheduled jobs may reuse a recent stage-1 scout (12h) to cut Booking
    # traffic; manual jobs (previous test) must keep forcing a live scout.
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    # run_job populates the typed command flag from the job's scheduled column.
    args = runner._build_args(_command(scheduled=True, filters_payload={"limit": 25}))

    assert args[args.index("--scout-cache-hours") + 1] == "12"


def test_booking_runner_args_pass_room_catalog_matching_filters():
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    args = runner._build_args(
        _command(
            destination="Faliraki",
            room_type_category="suite",
            filters_payload={
                "room_type": "Suite with Private Pool",
                "meals": "Very good breakfast included",
                "free_cancellation": "Yes",
            },
        )
    )

    assert args[args.index("--room-name-query") + 1] == "Suite with Private Pool"
    assert args[args.index("--required-meal") + 1] == "Very good breakfast included"
    assert args[args.index("--required-free-cancellation") + 1] == "Yes"


def test_booking_runner_args_bound_expensive_limits():
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    args = runner._build_args(_command(filters_payload={"limit": 5000, "deep_crawl_max_items": 9000}))

    assert args[args.index("--scout-max-items") + 1] == "80"
    assert args[args.index("--deep-crawl-max-items") + 1] == "320"


def test_booking_runner_run_delegates_built_args_to_execute(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "api.services.scrape_job_service.SCRAPE_OUTPUT_DIR", tmp_path / "output" / "scrapes"
    )
    runner = BookingScrapeJobRunner(timeout_seconds=10)
    captured = {}

    def fake_execute(args, heartbeat=None):
        captured["args"] = args
        captured["heartbeat"] = heartbeat

    monkeypatch.setattr(runner, "_execute", fake_execute)
    heartbeat = lambda: None  # noqa: E731

    runner.run(_command(), heartbeat=heartbeat)

    assert captured["args"][captured["args"].index("--destination") + 1] == "Rhodes"
    assert captured["heartbeat"] is heartbeat


def test_build_args_is_pure_and_does_not_create_output_dir(tmp_path, monkeypatch):
    output_dir = tmp_path / "output" / "scrapes"
    monkeypatch.setattr("api.services.scrape_job_service.SCRAPE_OUTPUT_DIR", output_dir)
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    args = runner._build_args(_command())

    # Arg construction must stay side-effect free; run() owns the mkdir.
    assert not output_dir.exists()
    assert args[args.index("--output-csv") + 1].startswith(str(output_dir))


def test_run_creates_output_dir_before_executing(tmp_path, monkeypatch):
    output_dir = tmp_path / "output" / "scrapes"
    monkeypatch.setattr("api.services.scrape_job_service.SCRAPE_OUTPUT_DIR", output_dir)
    runner = BookingScrapeJobRunner(timeout_seconds=10)
    monkeypatch.setattr(runner, "_execute", lambda args, heartbeat=None: None)

    runner.run(_command())

    assert output_dir.is_dir()


# ----------------------------------------------------------------------------
# BookingScrapeJobRunner._execute — hardened subprocess execution
# ----------------------------------------------------------------------------


def _write_scraper_module(tmp_path, monkeypatch, body: str) -> None:
    """Install a throwaway package that `python -m <name>` will execute.

    The runner launches the scraper as a MODULE, so these tests exercise that
    path — including the PYTHONPATH the runner injects — instead of a loose
    script file.
    """
    package = tmp_path / "fake_scraper_pkg"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(body, encoding="utf-8")
    monkeypatch.setattr("api.services.scrape_job_service.SCRAPER_MODULE", package.name)
    monkeypatch.setattr("api.services.scrape_job_service.PROJECT_ROOT", tmp_path)


def test_execute_runs_scraper_subprocess_to_completion(tmp_path, monkeypatch):
    _write_scraper_module(tmp_path, monkeypatch, "print('ok')\n")
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)

    runner._execute(["--ignored"])  # must not raise


def test_execute_parses_the_last_result_summary_sentinel(tmp_path, monkeypatch):
    _write_scraper_module(
        tmp_path,
        monkeypatch,
        "print('ROOMRATE_RESULT_SUMMARY {\"version\":1,\"rows_seen\":2,\"filter_counts\":{},\"rows_written\":2}')\n"
        "print('noise')\n"
        "print('ROOMRATE_RESULT_SUMMARY {\"version\":1,\"rows_seen\":4,\"filter_counts\":{},\"rows_written\":3}')\n",
    )
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)

    summary = runner._execute([])

    assert summary is not None
    assert summary["rows_seen"] == 4
    assert summary["rows_written"] == 3


def test_execute_decodes_utf8_greek_logs_before_result_summary(tmp_path, monkeypatch):
    _write_scraper_module(
        tmp_path,
        monkeypatch,
        "import sys\n"
        "sys.stdout.buffer.write("
        "'Κατάλυμα\\nROOMRATE_RESULT_SUMMARY {\"version\":1,\"rows_seen\":1,'"
        "'\"filter_counts\":{},\"rows_written\":1}\\n'.encode('utf-8'))\n",
    )
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)

    summary = runner._execute([])

    assert summary is not None
    assert summary["rows_written"] == 1


def test_execute_final_malformed_sentinel_invalidates_an_earlier_valid_one(tmp_path, monkeypatch, caplog):
    _write_scraper_module(
        tmp_path,
        monkeypatch,
        "print('ROOMRATE_RESULT_SUMMARY {\"version\":1,\"rows_seen\":2}')\n"
        "print('ROOMRATE_RESULT_SUMMARY not-json')\n",
    )
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)

    with caplog.at_level("WARNING", logger="api.services.scrape_job_service"):
        summary = runner._execute([])

    assert summary is None
    assert any("malformed scraper result summary" in record.message for record in caplog.records)


def test_execute_missing_sentinel_warns_and_returns_none(tmp_path, monkeypatch, caplog):
    _write_scraper_module(tmp_path, monkeypatch, "print('ordinary log line')\n")
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)

    with caplog.at_level("WARNING", logger="api.services.scrape_job_service"):
        summary = runner._execute([])

    assert summary is None
    assert any("completed without" in record.message for record in caplog.records)


def test_execute_raises_runtime_error_with_stderr_tail_on_nonzero_exit(tmp_path, monkeypatch):
    _write_scraper_module(tmp_path, monkeypatch, "import sys\nsys.stderr.write('boom-details')\nsys.exit(3)\n")
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)

    with pytest.raises(RuntimeError, match="boom-details"):
        runner._execute([])


def test_execute_kills_process_tree_and_raises_on_timeout(tmp_path, monkeypatch):
    _write_scraper_module(tmp_path, monkeypatch, "import time\ntime.sleep(60)\n")
    runner = BookingScrapeJobRunner(timeout_seconds=1, poll_interval_seconds=0.2)

    started = time.monotonic()
    with pytest.raises(ScrapeJobTimeoutError):
        runner._execute([])
    # The runner must give up shortly after the timeout, not wait for the child.
    assert time.monotonic() - started < 30


def test_execute_invokes_heartbeat_and_swallows_heartbeat_errors(tmp_path, monkeypatch):
    _write_scraper_module(tmp_path, monkeypatch, "import time\ntime.sleep(0.6)\n")
    # heartbeat_interval_seconds=0 disables the throttle: every poll beats.
    runner = BookingScrapeJobRunner(
        timeout_seconds=30, poll_interval_seconds=0.1, heartbeat_interval_seconds=0
    )
    beats = []

    def failing_heartbeat():
        beats.append(1)
        raise RuntimeError("db unavailable")

    runner._execute([], heartbeat=failing_heartbeat)  # must not raise

    assert len(beats) >= 1


def test_execute_throttles_heartbeats_to_configured_interval(tmp_path, monkeypatch):
    _write_scraper_module(tmp_path, monkeypatch, "import time\ntime.sleep(0.6)\n")
    # Several 0.1s polls happen during the 0.6s run, but with a 30s heartbeat
    # interval (the default) none of them may touch the heartbeat callback.
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.1)
    beats = []

    runner._execute([], heartbeat=lambda: beats.append(1))

    assert runner.heartbeat_interval_seconds == 30.0
    assert beats == []


def test_settings_expose_scrape_job_limits_with_defaults():
    from api.config import Settings

    fresh_settings = Settings(_env_file=None)

    assert fresh_settings.scrape_job_timeout_seconds == 1800
    assert fresh_settings.max_concurrent_scrape_jobs_per_account == 2
    assert fresh_settings.max_daily_scrape_jobs_per_account == 20


def test_execute_launches_the_scraper_package_as_a_module(monkeypatch):
    """Production scrapes must run `python -m scraper`, not a loose script path.

    The docs have always presented the package as the entry point and
    booking_scraper_v3.py as a compatibility shim; the runner did the reverse.
    Launching the module keeps the two in step and removes the shim from the
    critical path of every scrape.
    """
    captured: dict = {}

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return ("", "")

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("api.services.scrape_job_service.subprocess.Popen", fake_popen)
    runner = BookingScrapeJobRunner(timeout_seconds=30, poll_interval_seconds=0.01)

    runner._execute(["--destination", "Faliraki"])

    command = captured["command"]
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "scraper"]
    assert command[3:] == ["--destination", "Faliraki"]
    # The old shim path must not appear anywhere in the command.
    assert not any("booking_scraper_v3" in str(part) for part in command)
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert captured["kwargs"]["env"]["PYTHONUTF8"] == "1"


# ----------------------------------------------------------------------------
# Round 6: nearby areas, radius origin, hotel cap (spec §3.1/§3.2)
# ----------------------------------------------------------------------------

OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


class FakeOwnedPropertyLookup:
    def __init__(self, row: dict | None = None, error: Exception | None = None):
        self.row = row
        self.error = error
        self.calls: list[tuple[UUID, UUID]] = []

    def get_owned_property(self, account_id, owned_property_id):
        self.calls.append((account_id, owned_property_id))
        if self.error:
            raise self.error
        return self.row


def _flag_values(args: list[str], flag: str) -> list[str]:
    return [args[index + 1] for index, value in enumerate(args) if value == flag]


def test_booking_runner_args_default_to_the_round6_limits():
    args = BookingScrapeJobRunner(timeout_seconds=10)._build_args(_command())

    assert args[args.index("--scout-max-items") + 1] == "40"
    assert args[args.index("--deep-crawl-max-hotels") + 1] == "40"
    assert args[args.index("--deep-crawl-max-items") + 1] == "80"  # min(320, max(2*40, 40))
    assert args[args.index("--deep-crawl-batch-size") + 1] == "8"
    assert "--nearby-destination" not in args
    assert "--radius-km" not in args


def test_booking_runner_args_pass_nearby_areas_and_the_hotel_cap():
    # The 8-area maximum: 40 + 20 * 8 = 200 hotels would be asked for, so the
    # 120 cap really bites (4 areas land exactly on 120 and prove nothing).
    nearby_areas = ("Ιξιά", "Αφάντου", "Καλλιθέα Ρόδου", "Κολύμπια", "Λίνδος", "Αρχάγγελος", "Πεύκοι", "Λαδικό")

    args = BookingScrapeJobRunner(timeout_seconds=10)._build_args(_command(nearby_destinations=nearby_areas))

    assert _flag_values(args, "--nearby-destination") == list(nearby_areas)
    assert args[args.index("--nearby-max-items") + 1] == "20"  # min(20, 40)
    # hotel_cap = min(120, 200) = 120; package budget = min(320, max(2 * 120, 40))
    assert args[args.index("--deep-crawl-max-hotels") + 1] == "120"
    assert args[args.index("--deep-crawl-max-items") + 1] == "240"


def test_booking_runner_args_nearby_limit_follows_a_small_result_limit():
    args = BookingScrapeJobRunner(timeout_seconds=10)._build_args(
        _command(nearby_destinations=("Ιξιά",), filters_payload={"limit": 8})
    )

    assert args[args.index("--nearby-max-items") + 1] == "8"
    assert args[args.index("--deep-crawl-max-hotels") + 1] == "16"
    assert args[args.index("--deep-crawl-max-items") + 1] == "40"  # max(2 * 16, 40)


def test_booking_runner_args_emit_the_radius_only_with_a_resolved_origin():
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    with_origin = runner._build_args(_command(radius_km=10.0, origin_lat=36.34, origin_lng=28.2))
    without_origin = runner._build_args(_command(radius_km=10.0))

    assert with_origin[with_origin.index("--origin-lat") + 1] == "36.34"
    assert with_origin[with_origin.index("--origin-lng") + 1] == "28.2"
    assert with_origin[with_origin.index("--radius-km") + 1] == "10.0"
    assert "--radius-km" not in without_origin
    assert "--origin-lat" not in without_origin


def test_run_job_resolves_the_radius_origin_from_the_owned_property():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {
        **_job(),
        "owned_property_id": OWNED_PROPERTY_ID,
        "radius_km": 10.0,
        "nearby_destinations": ["Ιξιά"],
    }
    runner = FakeRunner(result_summary={"version": 1, "rows_seen": 1, "filter_counts": {}, "rows_written": 1, "warnings": []})
    lookup = FakeOwnedPropertyLookup(row={"latitude": Decimal("36.340000"), "longitude": Decimal("28.200000")})
    service = ScrapeJobService(repository=repository, runner=runner, owned_property_lookup=lookup)

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    command = runner.commands[0]
    assert lookup.calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID)]
    assert (command.origin_lat, command.origin_lng, command.radius_km) == (36.34, 28.2, 10.0)
    assert command.nearby_destinations == ("Ιξιά",)
    # Nothing to warn about: the scraper's summary passes through untouched.
    assert repository.completions[0][3] == runner.result_summary


@pytest.mark.parametrize(
    "lookup",
    [
        FakeOwnedPropertyLookup(row={"latitude": None, "longitude": None}),
        FakeOwnedPropertyLookup(row={"latitude": Decimal("0.000000"), "longitude": Decimal("0.000000")}),
        FakeOwnedPropertyLookup(row=None),
        FakeOwnedPropertyLookup(error=RuntimeError("db down")),
        None,
    ],
)
def test_run_job_runs_without_radius_and_warns_when_the_origin_is_unknown(lookup):
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {**_job(), "owned_property_id": OWNED_PROPERTY_ID, "radius_km": 10.0}
    scraper_summary = {
        "version": 1,
        "rows_seen": 7,
        "filter_counts": {"single_rooms": {"before": 7, "after": 5}, "capacity": {"before": 5, "after": 5}},
        "rows_written": 5,
        "warnings": ["nearby_scout_failed:Ιξιά"],
    }
    runner = FakeRunner(result_summary=scraper_summary)
    service = ScrapeJobService(repository=repository, runner=runner, owned_property_lookup=lookup)

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True

    command = runner.commands[0]
    assert (command.origin_lat, command.origin_lng) == (None, None)
    assert command.radius_km == 10.0
    assert repository.transitions[-1][0] == "completed"
    # The service warning is appended; every other scraper key survives as is.
    assert repository.completions[0][3] == {
        "version": 1,
        "rows_seen": 7,
        "filter_counts": {"single_rooms": {"before": 7, "after": 5}, "capacity": {"before": 5, "after": 5}},
        "rows_written": 5,
        "warnings": ["nearby_scout_failed:Ιξιά", "radius_skipped_no_coordinates"],
    }


def test_run_job_records_the_radius_warning_even_without_a_scraper_summary():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {**_job(), "radius_km": 5.0}  # no owned property at all
    service = ScrapeJobService(repository=repository, runner=FakeRunner(result_summary=None))

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.completions[0][3] == {"warnings": ["radius_skipped_no_coordinates"]}


def test_run_job_without_a_radius_never_touches_the_summary_or_the_lookup():
    repository = FakeScrapeJobRepository()
    lookup = FakeOwnedPropertyLookup(row={"latitude": 36.34, "longitude": 28.2})
    service = ScrapeJobService(repository=repository, runner=FakeRunner(result_summary=None), owned_property_lookup=lookup)

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert lookup.calls == []
    assert repository.completions[0][3] is None


# ----------------------------------------------------------------------------
# Round 6: every production ScrapeJobService carries the owned-property lookup
# ----------------------------------------------------------------------------

API_ROOT = Path(__file__).resolve().parents[1]


def _scrape_job_service_construction_sites() -> list[tuple[str, int, bool]]:
    """(file, line, passes owned_property_lookup) for every ScrapeJobService(...) call."""
    sites: list[tuple[str, int, bool]] = []
    for path in sorted(API_ROOT.rglob("*.py")):
        relative = path.relative_to(API_ROOT)
        if relative.parts[0] == "tests":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "ScrapeJobService":
                keywords = {keyword.arg for keyword in node.keywords}
                sites.append((relative.as_posix(), node.lineno, "owned_property_lookup" in keywords))
    return sites


def test_every_production_scrape_job_service_receives_the_owned_property_lookup():
    """Without the lookup every radius request silently runs without a radius.

    Scans the API sources (tests excluded) so a new construction site, for a
    worker or a script, cannot forget it.
    """
    sites = _scrape_job_service_construction_sites()

    assert sites, "expected at least one production ScrapeJobService construction"
    assert [site for site in sites if not site[2]] == []


def test_api_background_onboarding_and_schedule_paths_share_the_wired_service():
    from api import dependencies

    assert isinstance(dependencies.get_scrape_job_service().owned_property_lookup, OnboardingRepository)
    # BackgroundTasks from onboarding run jobs through this nested service.
    assert isinstance(
        dependencies.get_onboarding_service().scrape_job_service.owned_property_lookup, OnboardingRepository
    )
    assert isinstance(
        dependencies.get_schedule_service().scrape_job_service.owned_property_lookup, OnboardingRepository
    )


def test_scheduler_executor_runs_queued_jobs_through_the_wired_service():
    """The worker process (api/worker.py) and the all-in-one API both build the
    scheduler with defaults; the queued-job executor must get the lookup too."""
    from api.scheduler import create_scheduler

    scheduler = create_scheduler()

    executor = scheduler.get_job("queued_job_executor")
    scrape_job_service = executor.func.args[1]
    assert isinstance(scrape_job_service, ScrapeJobService)
    assert isinstance(scrape_job_service.owned_property_lookup, OnboardingRepository)
