from datetime import datetime, timezone
from uuid import UUID

import pytest

from api.scheduler import (
    SCHEDULER_TICK_LOCK_KEY,
    SchedulerStatus,
    purge_old_scrape_csvs,
    run_queued_job_executor,
    run_schedule_tick,
    run_price_recommendation_audit_cleanup,
    run_scout_cache_cleanup,
    run_scrape_csv_cleanup,
    run_stale_job_sweeper,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
OTHER_JOB_ID = UUID("00000000-0000-0000-0000-000000000778")
NOW = datetime(2026, 6, 13, 6, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value, rowcount: int = 0):
        self.value = value
        self.rowcount = rowcount

    def scalar(self):
        return self.value


class FakeLockConnection:
    """Connection scripted for the advisory tick lock + cleanup statements."""

    def __init__(
        self,
        lock_acquired: bool = True,
        scalar_results: list | None = None,
        unlock_error: Exception | None = None,
    ):
        self.lock_acquired = lock_acquired
        self.scalar_results = list(scalar_results or [])
        self.unlock_error = unlock_error
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.invalidated = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "pg_try_advisory_lock" in sql:
            return _ScalarResult(self.lock_acquired)
        if "pg_advisory_unlock" in sql and self.unlock_error is not None:
            raise self.unlock_error
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return _ScalarResult(True)

    def invalidate(self):
        self.invalidated = True

    def close(self):
        self.closed = True

    # begin() support for run_scout_cache_cleanup
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeEngine:
    def __init__(self, connection: FakeLockConnection):
        self.connection = connection

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection


class FakeScheduleService:
    def __init__(self, due_result: int = 0, error: Exception | None = None):
        self.due_result = due_result
        self.error = error
        self.run_calls: list[datetime] = []
        self.failure_calls: list[UUID] = []
        self.success_calls: list[UUID] = []

    def run_due_schedules(self, now):
        self.run_calls.append(now)
        if self.error:
            raise self.error
        return self.due_result

    def record_scheduled_job_failure(self, account_id):
        self.failure_calls.append(account_id)
        return len(self.failure_calls)

    def record_scheduled_job_success(self, account_id):
        self.success_calls.append(account_id)


class FakeScrapeJobRepository:
    def __init__(self, queued: list[dict] | None = None, jobs: dict | None = None):
        self.queued = list(queued or [])
        self.jobs = dict(jobs or {})
        self.list_limits: list[int] = []
        self.sweep_calls: list[int] = []
        self.sweep_result = 0

    def list_queued_jobs(self, limit: int = 3):
        self.list_limits.append(limit)
        return self.queued[:limit]

    def get_job(self, account_id, job_id):
        return self.jobs.get(job_id)

    def fail_stale_jobs(self, stale_after_minutes: int = 5):
        self.sweep_calls.append(stale_after_minutes)
        return self.sweep_result


class FakeScrapeJobService:
    def __init__(self, errors: dict | None = None, claim_lost: set | None = None):
        self.errors = errors or {}
        # Jobs whose claim another worker won: run_job returns False for them.
        self.claim_lost = claim_lost or set()
        self.run_calls: list[tuple[UUID, UUID]] = []

    def run_job(self, account_id, job_id) -> bool:
        self.run_calls.append((account_id, job_id))
        if job_id in self.errors:
            raise self.errors[job_id]
        return job_id not in self.claim_lost


def _queued(job_id=JOB_ID):
    return {"account_id": ACCOUNT_ID, "id": job_id, "job_type": "competitor_search"}


def _job_row(status: str, scheduled: bool, job_id=JOB_ID):
    return {
        "id": job_id,
        "account_id": ACCOUNT_ID,
        "status": status,
        "scheduled": scheduled,
        "filters_payload": {"limit": 25} if scheduled else {},
    }


# ----------------------------------------------------------------------------
# run_schedule_tick — advisory-locked enqueue cycle
# ----------------------------------------------------------------------------


def test_schedule_tick_acquires_lock_runs_due_schedules_and_releases():
    connection = FakeLockConnection(lock_acquired=True)
    service = FakeScheduleService(due_result=2)
    status = SchedulerStatus()

    created = run_schedule_tick(service, status=status, now=NOW, engine=FakeEngine(connection))

    assert created == 2
    assert service.run_calls == [NOW]
    assert status.last_tick_at == NOW
    lock_sql, lock_params = connection.calls[0]
    assert "pg_try_advisory_lock" in lock_sql
    assert lock_params["key"] == SCHEDULER_TICK_LOCK_KEY
    unlock_sql, unlock_params = connection.calls[-1]
    assert "pg_advisory_unlock" in unlock_sql
    assert unlock_params["key"] == SCHEDULER_TICK_LOCK_KEY
    assert connection.closed is True


def test_schedule_tick_skips_when_another_process_holds_the_lock():
    connection = FakeLockConnection(lock_acquired=False)
    service = FakeScheduleService(due_result=2)
    status = SchedulerStatus()

    created = run_schedule_tick(service, status=status, now=NOW, engine=FakeEngine(connection))

    assert created == 0
    assert service.run_calls == []
    # We never held the lock, so we must not unlock it for the holder.
    assert not any("pg_advisory_unlock" in sql for sql, _ in connection.calls)
    assert connection.closed is True
    # Liveness still recorded: the tick ran, it just had nothing to do.
    assert status.last_tick_at == NOW


def test_schedule_tick_releases_lock_even_when_enqueue_raises():
    connection = FakeLockConnection(lock_acquired=True)
    service = FakeScheduleService(error=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        run_schedule_tick(service, now=NOW, engine=FakeEngine(connection))

    assert any("pg_advisory_unlock" in sql for sql, _ in connection.calls)
    assert connection.closed is True
    # The unlock succeeded, so the pooled connection stays valid.
    assert connection.invalidated is False


def test_schedule_tick_invalidates_connection_when_unlock_fails():
    # If the unlock statement fails, returning the connection to the pool
    # would leak the session-level advisory lock and block every future tick
    # cluster-wide. The connection must be invalidated so Postgres releases
    # the lock when the raw connection is discarded.
    connection = FakeLockConnection(lock_acquired=True, unlock_error=RuntimeError("connection reset"))
    service = FakeScheduleService(due_result=1)

    with pytest.raises(RuntimeError, match="connection reset"):
        run_schedule_tick(service, now=NOW, engine=FakeEngine(connection))

    assert connection.invalidated is True
    assert connection.closed is True


# ----------------------------------------------------------------------------
# run_queued_job_executor — claim-based safety net + breaker feedback
# ----------------------------------------------------------------------------


def test_executor_runs_queued_jobs_up_to_batch_size():
    repository = FakeScrapeJobRepository(
        queued=[_queued(JOB_ID), _queued(OTHER_JOB_ID)],
        jobs={
            JOB_ID: _job_row("completed", scheduled=False),
            OTHER_JOB_ID: _job_row("completed", scheduled=False, job_id=OTHER_JOB_ID),
        },
    )
    scrape_job_service = FakeScrapeJobService()
    schedule_service = FakeScheduleService()

    executed = run_queued_job_executor(repository, scrape_job_service, schedule_service, batch_size=1)

    assert repository.list_limits == [1]
    assert executed == 1
    assert scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID)]


def test_executor_records_breaker_failure_for_failed_scheduled_job():
    repository = FakeScrapeJobRepository(
        queued=[_queued()],
        jobs={JOB_ID: _job_row("failed", scheduled=True)},
    )
    schedule_service = FakeScheduleService()

    run_queued_job_executor(repository, FakeScrapeJobService(), schedule_service, batch_size=3)

    assert schedule_service.failure_calls == [ACCOUNT_ID]
    assert schedule_service.success_calls == []


def test_executor_resets_breaker_for_completed_scheduled_job():
    repository = FakeScrapeJobRepository(
        queued=[_queued()],
        jobs={JOB_ID: _job_row("completed", scheduled=True)},
    )
    schedule_service = FakeScheduleService()

    run_queued_job_executor(repository, FakeScrapeJobService(), schedule_service, batch_size=3)

    assert schedule_service.success_calls == [ACCOUNT_ID]
    assert schedule_service.failure_calls == []


def test_executor_skips_breaker_feedback_for_manual_jobs():
    repository = FakeScrapeJobRepository(
        queued=[_queued()],
        jobs={JOB_ID: _job_row("failed", scheduled=False)},
    )
    schedule_service = FakeScheduleService()

    run_queued_job_executor(repository, FakeScrapeJobService(), schedule_service, batch_size=3)

    assert schedule_service.failure_calls == []
    assert schedule_service.success_calls == []


def test_executor_skips_count_and_feedback_when_claim_was_lost():
    # run_job returns False on a lost claim: the OTHER worker executed the
    # job, so this executor must neither count it as executed nor apply
    # breaker feedback (the winner's executor owns that outcome — applying
    # it here too would double-feed the breaker across processes).
    repository = FakeScrapeJobRepository(
        queued=[_queued()],
        jobs={JOB_ID: _job_row("failed", scheduled=True)},
    )
    scrape_job_service = FakeScrapeJobService(claim_lost={JOB_ID})
    schedule_service = FakeScheduleService()

    executed = run_queued_job_executor(repository, scrape_job_service, schedule_service, batch_size=3)

    assert scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID)]
    assert executed == 0
    assert schedule_service.failure_calls == []
    assert schedule_service.success_calls == []


def test_executor_continues_after_run_job_crash():
    repository = FakeScrapeJobRepository(
        queued=[_queued(JOB_ID), _queued(OTHER_JOB_ID)],
        jobs={OTHER_JOB_ID: _job_row("completed", scheduled=True, job_id=OTHER_JOB_ID)},
    )
    scrape_job_service = FakeScrapeJobService(errors={JOB_ID: RuntimeError("worker crashed")})
    schedule_service = FakeScheduleService()

    executed = run_queued_job_executor(repository, scrape_job_service, schedule_service, batch_size=3)

    assert executed == 1
    assert scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID), (ACCOUNT_ID, OTHER_JOB_ID)]
    assert schedule_service.success_calls == [ACCOUNT_ID]


# ----------------------------------------------------------------------------
# run_stale_job_sweeper / run_scout_cache_cleanup
# ----------------------------------------------------------------------------


def test_sweeper_delegates_to_repository_and_returns_count():
    repository = FakeScrapeJobRepository()
    repository.sweep_result = 2

    swept = run_stale_job_sweeper(repository, stale_after_minutes=7)

    assert swept == 2
    assert repository.sweep_calls == [7]


def test_scout_cache_cleanup_skips_when_table_does_not_exist():
    connection = FakeLockConnection(scalar_results=[_ScalarResult(None)])

    deleted = run_scout_cache_cleanup(engine=FakeEngine(connection))

    assert deleted == 0
    assert not any("DELETE FROM scout_cache" in sql for sql, _ in connection.calls)


def test_scout_cache_cleanup_deletes_entries_older_than_seven_days():
    connection = FakeLockConnection(
        scalar_results=[_ScalarResult("scout_cache"), _ScalarResult(None, rowcount=5)]
    )

    deleted = run_scout_cache_cleanup(engine=FakeEngine(connection))

    assert deleted == 5
    delete_sql, delete_params = next(
        (sql, params) for sql, params in connection.calls if "DELETE FROM scout_cache" in sql
    )
    # Retention is a bound parameter (make_interval), not interpolated SQL.
    assert "make_interval(days => :days)" in delete_sql
    assert delete_params == {"days": 7}
    assert any("to_regclass('scout_cache')" in sql for sql, _ in connection.calls)


# ----------------------------------------------------------------------------
# purge_old_scrape_csvs / run_scrape_csv_cleanup
# ----------------------------------------------------------------------------


def _write_csv(directory, name: str, age_days: float, now: datetime) -> None:
    import os

    path = directory / name
    path.write_text("hotel,price\n")
    stale_ts = now.timestamp() - age_days * 86_400
    os.utime(path, (stale_ts, stale_ts))


def test_purge_deletes_old_csvs_and_keeps_recent_ones(tmp_path):
    _write_csv(tmp_path, "old.csv", age_days=45, now=NOW)
    _write_csv(tmp_path, "recent.csv", age_days=5, now=NOW)
    # Non-CSV files are never touched, whatever their age.
    _write_csv(tmp_path, "old.log", age_days=45, now=NOW)

    deleted = purge_old_scrape_csvs(tmp_path, older_than_days=30, now=NOW)

    assert deleted == 1
    assert not (tmp_path / "old.csv").exists()
    assert (tmp_path / "recent.csv").exists()
    assert (tmp_path / "old.log").exists()


def test_purge_deletes_checkpoints_a_failed_job_left_behind(tmp_path):
    import os

    old_checkpoint = tmp_path / "checkpoint_old-job"
    old_checkpoint.mkdir()
    (old_checkpoint / "scout.json").write_text("{}")
    stale_ts = NOW.timestamp() - 45 * 86_400
    os.utime(old_checkpoint, (stale_ts, stale_ts))
    (tmp_path / "checkpoint_running-job").mkdir()

    deleted = purge_old_scrape_csvs(tmp_path, older_than_days=30, now=NOW)

    assert deleted == 1
    assert not old_checkpoint.exists()
    assert (tmp_path / "checkpoint_running-job").exists()  # a job may still retry


def test_purge_disabled_with_zero_retention(tmp_path):
    _write_csv(tmp_path, "ancient.csv", age_days=400, now=NOW)

    deleted = purge_old_scrape_csvs(tmp_path, older_than_days=0, now=NOW)

    assert deleted == 0
    assert (tmp_path / "ancient.csv").exists()


def test_purge_handles_missing_directory(tmp_path):
    assert purge_old_scrape_csvs(tmp_path / "never-created", older_than_days=30, now=NOW) == 0


def test_run_scrape_csv_cleanup_uses_settings_and_output_dir(tmp_path, monkeypatch):
    from api.config import settings

    monkeypatch.setattr("api.services.scrape_job_service.SCRAPE_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(settings, "scrape_csv_retention_days", 30)
    _write_csv(tmp_path, "stale.csv", age_days=90, now=datetime.now(timezone.utc))
    _write_csv(tmp_path, "fresh.csv", age_days=1, now=datetime.now(timezone.utc))

    deleted = run_scrape_csv_cleanup()

    assert deleted == 1
    assert not (tmp_path / "stale.csv").exists()
    assert (tmp_path / "fresh.csv").exists()


def test_run_scrape_csv_cleanup_never_raises(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("api.scheduler.purge_old_scrape_csvs", explode)

    assert run_scrape_csv_cleanup() == 0  # crash is logged, not raised


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------


def test_settings_expose_scheduler_defaults():
    from api.config import Settings

    fresh_settings = Settings(_env_file=None)

    assert fresh_settings.scheduler_enabled is True
    assert fresh_settings.scheduler_tick_minutes == 15
    assert fresh_settings.executor_batch_size == 3
    assert fresh_settings.stale_job_minutes == 5
    assert fresh_settings.scrape_csv_retention_days == 30


# ----------------------------------------------------------------------------
# run_price_recommendation_audit_cleanup
# ----------------------------------------------------------------------------


def test_audit_cleanup_deletes_rows_past_retention():
    """Audit rows hold full request/response payloads and must not grow forever.

    Notifications already have NOTIFICATION_RETENTION_DAYS; the pricing audit
    table stores the same GDPR-relevant history (every request payload plus
    the recommendation returned) with no bound at all.
    """
    connection = FakeLockConnection(scalar_results=[_ScalarResult(None, rowcount=12)])

    deleted = run_price_recommendation_audit_cleanup(
        engine=FakeEngine(connection), retention_days=180
    )

    assert deleted == 12
    delete_sql, delete_params = next(
        (sql, params)
        for sql, params in connection.calls
        if "DELETE FROM roomrate_price_recommendation_audits" in sql
    )
    # Retention is a bound parameter (make_interval), never interpolated SQL.
    assert "make_interval(days => :days)" in delete_sql
    assert delete_params == {"days": 180}


def test_audit_cleanup_is_disabled_by_zero_retention():
    connection = FakeLockConnection(scalar_results=[_ScalarResult(None, rowcount=0)])

    deleted = run_price_recommendation_audit_cleanup(
        engine=FakeEngine(connection), retention_days=0
    )

    assert deleted == 0
    assert connection.calls == []
