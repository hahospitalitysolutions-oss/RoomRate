"""Background scheduler: enqueue-only schedule ticks + claim-based execution.

All job callables here are plain SYNC functions. ``AsyncIOScheduler`` runs
them in its default ThreadPoolExecutor, so they never block the event loop.
Running multiple API processes is safe by construction:

* ``run_schedule_tick`` is serialized cluster-wide by a Postgres advisory
  lock, so due schedules are enqueued exactly once per cycle.
* ``run_queued_job_executor`` and the local BackgroundTasks kick both funnel
  through ``ScrapeJobRepository.claim_job`` (an atomic fenced UPDATE), so a
  job handed to two workers executes exactly once.
* ``run_stale_job_sweeper`` and ``run_scout_cache_cleanup`` are idempotent
  UPDATE/DELETE statements; concurrent runs just find nothing left to do.
* ``run_scrape_csv_cleanup`` sweeps the local scrape-output directory; a
  concurrent delete of the same file is logged and skipped, never raised.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from api.config import settings
from api.db import get_engine

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from api.services.schedule_service import ScheduleService
    from api.services.scrape_job_service import ScrapeJobRepositoryProtocol, ScrapeJobService

logger = logging.getLogger(__name__)

# Cluster-wide advisory lock key for the schedule tick. Arbitrary but stable:
# every API process must use the same constant so only one enqueues per cycle.
SCHEDULER_TICK_LOCK_KEY = 815_372_001

# scout_cache rows older than this are useless (stage-1 reuse window is 12h).
SCOUT_CACHE_RETENTION_DAYS = 7


@dataclass
class SchedulerStatus:
    """Mutable liveness holder; exposed on app.state for health checks."""

    last_tick_at: datetime | None = None


def run_schedule_tick(
    schedule_service: ScheduleService,
    status: SchedulerStatus | None = None,
    now: datetime | None = None,
    engine: Any = None,
) -> int:
    """Enqueue scheduled scrape jobs for all due configs; returns jobs created.

    Guarded by ``pg_try_advisory_lock`` on a dedicated connection so that in
    multi-process deployments only one process enqueues per cycle; the others
    skip immediately. The (session-level) lock is explicitly released and the
    connection closed even when enqueueing raises.
    """
    engine = engine if engine is not None else get_engine(role="api")
    tick_now = now if now is not None else datetime.now(timezone.utc)
    if status is not None:
        # Liveness is recorded even for lock-skipped ticks: the scheduler ran.
        status.last_tick_at = tick_now
    connection = engine.connect()
    try:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": SCHEDULER_TICK_LOCK_KEY},
        ).scalar()
        if not acquired:
            logger.info("Schedule tick skipped: another process holds the tick lock")
            return 0
        try:
            created = schedule_service.run_due_schedules(tick_now)
        finally:
            try:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": SCHEDULER_TICK_LOCK_KEY},
                )
            except Exception:
                # A failed unlock would return this connection to the pool
                # still holding the session-level advisory lock, blocking
                # every future tick cluster-wide. Invalidate discards the
                # raw DBAPI connection so Postgres releases the lock.
                connection.invalidate()
                raise
        if created:
            logger.info(
                "Schedule tick enqueued %s scrape job(s)",
                created,
                extra={"jobs_enqueued": created},
            )
        return created
    finally:
        connection.close()


def run_queued_job_executor(
    scrape_job_repository: ScrapeJobRepositoryProtocol,
    scrape_job_service: ScrapeJobService,
    schedule_service: ScheduleService,
    batch_size: int = 3,
) -> int:
    """Run the oldest queued jobs across all accounts; returns jobs executed.

    This is the execution path for scheduled jobs and the safety net for
    API-created jobs. In local ``all`` mode it is also the safety net for a
    lost BackgroundTasks kick. ``claim_job`` makes double execution
    impossible, so racing another executor is harmless.

    ``run_job`` returns True only when this call actually claimed and ran the
    job; lost claims and vanished jobs return False and are neither counted
    nor fed into the schedule breaker — the winning worker's executor owns
    that outcome, so feedback is applied exactly once cluster-wide.
    """
    executed = 0
    for job in scrape_job_repository.list_queued_jobs(limit=batch_size):
        account_id: uuid.UUID = job["account_id"]
        job_id: uuid.UUID = job["id"]
        try:
            ran = scrape_job_service.run_job(account_id, job_id)
        except Exception:
            # run_job records job-level failures itself; reaching here means
            # the orchestration crashed. Keep draining the batch.
            logger.exception(
                "Queued-job executor crashed: account_id=%s job_id=%s",
                account_id,
                job_id,
                extra={"account_id": str(account_id), "job_id": str(job_id)},
            )
            continue
        if not ran:
            continue
        executed += 1
        _apply_schedule_breaker_feedback(
            scrape_job_repository, schedule_service, account_id, job_id
        )
    return executed


def _apply_schedule_breaker_feedback(
    scrape_job_repository: ScrapeJobRepositoryProtocol,
    schedule_service: ScheduleService,
    account_id: uuid.UUID,
    job_id: uuid.UUID,
) -> None:
    """Feed a scheduled job's terminal outcome into the schedule breaker.

    Only jobs enqueued by the scheduler (``scheduled`` column) count: manual
    job failures must never disable an account's schedule. Non-terminal
    statuses (e.g. still 'running' because another worker won the claim) are
    ignored. Feedback failures are logged, never raised — breaker bookkeeping
    must not abort the executor batch.
    """
    try:
        row = scrape_job_repository.get_job(account_id, job_id)
        if not row:
            return
        if not row.get("scheduled"):
            return
        if row["status"] == "failed":
            schedule_service.record_scheduled_job_failure(account_id)
        elif row["status"] == "completed":
            schedule_service.record_scheduled_job_success(account_id)
    except Exception:
        logger.exception(
            "Schedule breaker feedback failed: account_id=%s job_id=%s",
            account_id,
            job_id,
            extra={"account_id": str(account_id), "job_id": str(job_id)},
        )


def run_stale_job_sweeper(
    scrape_job_repository: ScrapeJobRepositoryProtocol, stale_after_minutes: int = 5
) -> int:
    """Recover running jobs whose worker heartbeat went silent; returns count.

    Known gap (companion to the ``TODO Phase D: notify`` in
    ``ScheduleService.record_scheduled_job_failure``): SCHEDULED jobs failed
    here never feed the schedule circuit breaker. Breaker feedback only runs
    in the executor when its own ``run_job`` call executed the job, and the
    worker whose heartbeat died can never report back — so an account whose
    scheduled jobs always die silently is swept but never auto-disabled.
    Phase D's notification/feedback plumbing should close this.
    """
    swept = scrape_job_repository.fail_stale_jobs(stale_after_minutes=stale_after_minutes)
    if swept:
        logger.warning(
            "Stale-job sweeper recovered or failed %s job(s) with lost heartbeats",
            swept,
            extra={"swept_count": swept},
        )
    return swept


def run_scout_cache_cleanup(engine: Any = None) -> int:
    """Delete week-old scout_cache rows; returns the number deleted.

    The table is created by the scraper itself (not Alembic), so the delete is
    guarded by ``to_regclass`` for fresh databases where it does not exist.
    """
    engine = engine if engine is not None else get_engine(role="api")
    with engine.begin() as connection:
        if connection.execute(text("SELECT to_regclass('scout_cache')")).scalar() is None:
            return 0
        result = connection.execute(
            text(
                """
                DELETE FROM scout_cache
                WHERE cached_at < now() - make_interval(days => :days)
                """
            ),
            {"days": SCOUT_CACHE_RETENTION_DAYS},
        )
    deleted = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    if deleted:
        logger.info("Scout-cache cleanup deleted %s expired row(s)", deleted)
    return deleted


def run_price_recommendation_audit_cleanup(
    engine: Any = None, retention_days: int | None = None
) -> int:
    """Delete price-recommendation audit rows past retention; returns the count.

    The audit table stores the full request and response payload of every
    pricing decision — the same kind of history the notification feed already
    bounds via NOTIFICATION_RETENTION_DAYS. Without this it grows forever.
    ``retention_days <= 0`` disables the sweep.
    """
    retention = (
        retention_days
        if retention_days is not None
        else settings.price_recommendation_audit_retention_days
    )
    if retention <= 0:
        return 0
    engine = engine if engine is not None else get_engine(role="api")
    with engine.begin() as connection:
        result = connection.execute(
            text(
                """
                DELETE FROM roomrate_price_recommendation_audits
                WHERE created_at < now() - make_interval(days => :days)
                """
            ),
            {"days": retention},
        )
    deleted = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    if deleted:
        logger.info("Price-recommendation audit cleanup deleted %s row(s)", deleted)
    return deleted


def purge_old_scrape_csvs(directory: Path, older_than_days: int, now: datetime | None = None) -> int:
    """Delete ``*.csv`` files and leftover job checkpoints older than ``older_than_days``.

    Pure helper (filesystem only) so it unit-tests with ``tmp_path`` and an
    injected ``now``. Retention <= 0 disables deletion entirely; a missing
    directory is fine (no scrape has run yet). Per-file errors are logged and
    skipped so one locked file cannot abort the sweep. Returns files deleted.
    """
    if older_than_days <= 0 or not directory.is_dir():
        return 0
    reference = now if now is not None else datetime.now(timezone.utc)
    cutoff_ts = reference.timestamp() - older_than_days * 86_400
    deleted = 0
    for csv_path in directory.glob("*.csv"):
        try:
            if csv_path.stat().st_mtime < cutoff_ts:
                csv_path.unlink()
                deleted += 1
        except OSError:
            logger.warning("Could not delete old scrape CSV %s", csv_path, exc_info=True)
    # What a finally failed job left for a retry that never came
    # (scraper/checkpoint.py); a successful job removes its own.
    for checkpoint_dir in directory.glob("checkpoint_*"):
        try:
            if checkpoint_dir.is_dir() and checkpoint_dir.stat().st_mtime < cutoff_ts:
                shutil.rmtree(checkpoint_dir)
                deleted += 1
        except OSError:
            logger.warning("Could not delete old scrape checkpoint %s", checkpoint_dir, exc_info=True)
    return deleted


def run_scrape_csv_cleanup() -> int:
    """Scheduler job: purge scrape CSVs past retention. Never raises.

    The per-job CSVs are debugging artifacts only — structured results live
    in PostgreSQL — so deleting them is always safe.
    """
    # Lazy import (create_scheduler style): keeps this module importable
    # without pulling the scrape service's transitive dependencies.
    from api.services.scrape_job_service import SCRAPE_OUTPUT_DIR

    try:
        deleted = purge_old_scrape_csvs(SCRAPE_OUTPUT_DIR, settings.scrape_csv_retention_days)
    except Exception:
        logger.exception("Scrape-CSV cleanup crashed")
        return 0
    if deleted:
        logger.info(
            "Scrape-CSV cleanup deleted %s file(s) past retention",
            deleted,
            extra={"csv_files_deleted": deleted},
        )
    return deleted


def create_scheduler(
    schedule_service: ScheduleService | None = None,
    scrape_job_service: ScrapeJobService | None = None,
    scrape_job_repository: ScrapeJobRepositoryProtocol | None = None,
    status: SchedulerStatus | None = None,
) -> AsyncIOScheduler:
    """Build the AsyncIOScheduler with all RoomRate background jobs.

    Dependencies default to the same factories the API routes use (imported
    lazily so this module stays importable without apscheduler installed and
    free of import cycles). Every job runs with ``coalesce=True`` and
    ``max_instances=1`` so a slow cycle collapses missed runs instead of
    stacking them.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from api import dependencies

    if scrape_job_service is None:
        # get_scrape_job_service wires the price-alert evaluator, so the
        # executor path evaluates alerts on completion just like the API path.
        scrape_job_service = dependencies.get_scrape_job_service()
    if schedule_service is None:
        # Share the scrape-job service so quotas/claims behave identically, and
        # the same price-alert service as notifier so the breaker can tell an
        # account its schedule was disabled.
        from api.repositories.schedule_repository import ScheduleRepository
        from api.services.schedule_service import ScheduleService

        schedule_service = ScheduleService(
            repository=ScheduleRepository(),
            scrape_job_service=scrape_job_service,
            notifier=dependencies.get_price_alert_service(),
        )
    if scrape_job_repository is None:
        scrape_job_repository = scrape_job_service.repository

    scheduler = AsyncIOScheduler(
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    scheduler.add_job(
        partial(run_schedule_tick, schedule_service, status=status),
        trigger="interval",
        minutes=settings.scheduler_tick_minutes,
        id="schedule_tick",
        name="Enqueue due scheduled scrape jobs",
    )
    # NOTE: a batch of slow scrapes can outlast the 1-minute interval; with
    # max_instances=1 APScheduler then logs "maximum number of running
    # instances reached" warnings. That noise is expected and harmless: the
    # missed runs coalesce and the queue drains on the next free cycle.
    scheduler.add_job(
        partial(
            run_queued_job_executor,
            scrape_job_repository,
            scrape_job_service,
            schedule_service,
            batch_size=settings.executor_batch_size,
        ),
        trigger="interval",
        minutes=1,
        id="queued_job_executor",
        name="Run oldest queued scrape jobs (claim-based)",
    )
    scheduler.add_job(
        partial(run_stale_job_sweeper, scrape_job_repository, stale_after_minutes=settings.stale_job_minutes),
        trigger="interval",
        minutes=5,
        id="stale_job_sweeper",
        name="Fail running jobs with lost heartbeats",
    )
    scheduler.add_job(
        run_scout_cache_cleanup,
        trigger="cron",
        hour=4,
        minute=20,
        id="scout_cache_cleanup",
        name="Delete week-old scout_cache rows",
    )
    scheduler.add_job(
        run_scrape_csv_cleanup,
        trigger="cron",
        hour=4,
        minute=40,
        id="scrape_csv_cleanup",
        name="Delete scrape CSVs past retention",
    )
    scheduler.add_job(
        run_price_recommendation_audit_cleanup,
        trigger="cron",
        hour=4,
        minute=50,
        id="price_recommendation_audit_cleanup",
        name="Delete pricing audit rows past retention",
    )
    return scheduler
