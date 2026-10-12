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
from datetime import datetime, timedelta, timezone
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

# Ferry demand jobs: one process looks up ports / collects sailings at a time.
FERRY_PROFILE_LOCK_KEY = 815_372_002
FERRY_COLLECTION_LOCK_KEY = 815_372_003
# Flight demand collection: one process spends the AeroDataBox units at a time.
FLIGHT_COLLECTION_LOCK_KEY = 815_372_004
# The demand tick (lookups, then the due ferry and flight collections).
DEMAND_TICK_LOCK_KEY = 815_372_005
DEMAND_TICK_MINUTES = 5
# Run-log rows kept: a request is compared with its scope's last run.
DEMAND_RUN_RETENTION_DAYS = 60

# Ferry and flight observations of travel dates further back than this are dropped.
FERRY_OBSERVATION_RETENTION_DAYS = 400

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


def run_raw_ingestion_cleanup(engine: Any = None, retention_days: int | None = None) -> int:
    """Delete raw ingestion events past retention; returns the count. Never raises.

    Each scraped row is stored twice: as its package (package_payload, read by
    the app) and as a raw event (read by nothing). At ~1.7 KB a row the raw
    copies alone are ~3 MB per 2,000-row scrape, against Neon's 0.5 GB free
    storage. ``retention_days <= 0`` disables the sweep.
    """
    retention = retention_days if retention_days is not None else settings.raw_ingestion_retention_days
    if retention <= 0:
        return 0
    try:
        # Writer role: no API statement timeout for a large first sweep.
        engine = engine if engine is not None else get_engine(role="writer")
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_raw_ingestion_events
                    WHERE captured_at < now() - make_interval(days => :days)
                    """
                ),
                {"days": retention},
            )
    except Exception:
        logger.exception("Raw-ingestion cleanup crashed")
        return 0
    deleted = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    if deleted:
        logger.info("Raw-ingestion cleanup deleted %s row(s) past retention", deleted)
    return deleted


def _run_with_advisory_lock(key: int, job: Any, engine: Any, name: str) -> Any:
    """Run ``job()`` only if this process takes the session advisory lock ``key``.

    Returns None when another process holds it. Released (or the connection
    invalidated, which releases it) even when the job raises.
    """
    connection = engine.connect()
    try:
        if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar():
            logger.info("%s skipped: another process holds its lock", name)
            return None
        try:
            return job()
        finally:
            try:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception:
                connection.invalidate()
                raise
    finally:
        connection.close()


def run_ferry_profile_resolution(service: Any = None, engine: Any = None, today: Any = None) -> int:
    """Find the island port of destinations that have none yet. Never raises.

    Hourly and cheap when nothing is missing (one query): a newly claimed
    property gets its ferry routes within the hour.
    """
    if not settings.ferry_demand_enabled:
        return 0
    try:
        if service is None:
            from api.dependencies import get_ferry_demand_service

            service = get_ferry_demand_service()
        lookup_day = today if today is not None else datetime.now(timezone.utc).date()
        resolved = _run_with_advisory_lock(
            FERRY_PROFILE_LOCK_KEY,
            lambda: service.resolve_profiles(lookup_day),
            engine if engine is not None else get_engine(role="api"),
            "Ferry profile lookup",
        )
    except Exception:
        logger.exception("Ferry profile lookup crashed")
        return 0
    if resolved:
        logger.info("Ferry profile lookup resolved %s destination(s)", resolved)
    return resolved or 0


def run_ferry_demand_collection(service: Any = None, engine: Any = None, today: Any = None) -> int:
    """Read the next days' sailings of every ferry route; prune old answers. Never raises.

    Returns the observations saved.
    """
    if not settings.ferry_demand_enabled:
        return 0
    try:
        if service is None:
            from api.dependencies import get_ferry_demand_service

            service = get_ferry_demand_service()
        collection_day = today if today is not None else datetime.now(timezone.utc).date()
        report = _run_with_advisory_lock(
            FERRY_COLLECTION_LOCK_KEY,
            lambda: service.collect(collection_day),
            engine if engine is not None else get_engine(role="api"),
            "Ferry demand collection",
        )
    except Exception:
        logger.exception("Ferry demand collection crashed")
        return 0
    if report is None:
        return 0
    logger.info(
        "Ferry demand collection: %s route(s), %s call(s), %s saved, %s failed%s",
        report.routes,
        report.calls,
        report.saved,
        report.failed,
        " (stopped early)" if report.aborted else "",
    )
    try:
        service.repository.prune(
            raw_payload_days=settings.ferry_raw_payload_retention_days,
            observation_days=FERRY_OBSERVATION_RETENTION_DAYS,
        )
    except Exception:
        logger.exception("Ferry observation pruning crashed")
    return report.saved


def run_flight_demand_collection(
    service: Any = None, engine: Any = None, today: Any = None, max_calls: int | None = None
) -> int:
    """Read the due island-airport arrivals within today's share of the units. Never raises.

    Off without AERODATABOX_API_KEY. ``max_calls`` replaces the day's share
    (scripts/run_flight_demand.py --calls), never the month's budget.
    Returns the observations saved.
    """
    if not settings.flight_demand_active:
        return 0
    try:
        if service is None:
            from api.dependencies import get_flight_demand_service

            service = get_flight_demand_service()
        collection_day = today if today is not None else datetime.now(timezone.utc).date()
        report = _run_with_advisory_lock(
            FLIGHT_COLLECTION_LOCK_KEY,
            lambda: service.collect(collection_day, max_calls=max_calls),
            engine if engine is not None else get_engine(role="api"),
            "Flight demand collection",
        )
    except Exception:
        logger.exception("Flight demand collection crashed")
        return 0
    if report is None:
        return 0
    logger.info(
        "Flight demand collection: airports %s, %s due, %s call(s) of %s allowed, %s saved, %s failed, "
        "units left %s%s",
        ",".join(report.airports) or "none",
        report.due,
        report.calls,
        report.allowance,
        report.saved,
        report.failed,
        report.units_remaining if report.units_remaining is not None else "?",
        " (stopped early)" if report.aborted or report.out_of_units else "",
    )
    try:
        service.repository.prune(
            raw_payload_days=settings.flight_raw_payload_retention_days,
            observation_days=FERRY_OBSERVATION_RETENTION_DAYS,
        )
    except Exception:
        logger.exception("Flight observation pruning crashed")
    return report.saved


def run_demand_tick(
    now: datetime | None = None,
    *,
    settings_repository: Any = None,
    ferry_service: Any = None,
    flight_service: Any = None,
    engine: Any = None,
    force: bool = False,
    kinds: tuple[str, ...] = ("ferries", "flights"),
) -> dict[str, int]:
    """Port lookups, then the ferry and flight collections that are due. Never raises.

    Every DEMAND_TICK_MINUTES on the worker, so a property claimed or a
    «Συλλογή τώρα» pressed is served within minutes, and the owner's hour
    is kept while the worker is up. Each scope runs once a Greek day
    (roomrate_demand_runs). ``force`` treats every owner's hour as come
    (scripts/run_ferry_demand.py, run_flight_demand.py). Returns the
    lookups made and the observations saved per kind.
    """
    ferries_on = settings.ferry_demand_enabled and "ferries" in kinds
    flights_on = settings.flight_demand_active and "flights" in kinds
    if not (ferries_on or flights_on):
        return {}
    try:
        from api import dependencies

        if settings_repository is None:
            settings_repository = dependencies.get_demand_settings_repository()
        if ferries_on and ferry_service is None:
            ferry_service = dependencies.get_ferry_demand_service()
        if flights_on and flight_service is None:
            flight_service = dependencies.get_flight_demand_service()
        moment = now if now is not None else datetime.now(timezone.utc)
        result = _run_with_advisory_lock(
            DEMAND_TICK_LOCK_KEY,
            lambda: _demand_tick(
                moment,
                settings_repository,
                ferry_service if ferries_on else None,
                flight_service if flights_on else None,
                force,
            ),
            engine if engine is not None else get_engine(role="api"),
            "Demand tick",
        )
    except Exception:
        logger.exception("Demand tick crashed")
        return {}
    return result or {}


def _demand_tick(
    now: datetime, settings_repository: Any, ferry_service: Any, flight_service: Any, force: bool
) -> dict[str, int]:
    from api.services.demand_schedule import FLIGHTS_SCOPE, due_scopes, greek_now

    now_local = greek_now(now)
    # Observations and runs are dated by the Greek day, whatever the hour.
    day = now_local.date()
    result = {"lookups": 0, "ferries": 0, "flights": 0}
    if ferry_service is not None:
        result["lookups"] = ferry_service.resolve_profiles(day)

    runs = settings_repository.runs(since=day - timedelta(days=7))
    last_started: dict[tuple[str, str], datetime] = {}
    for run in runs:
        last_started.setdefault((run["kind"], run["scope"]), run["started_at"])
    due = due_scopes(
        settings_repository.demand_properties(),
        ran_today={(run["kind"], run["scope"]) for run in runs if run["run_on"] == day},
        last_started=last_started,
        now_local=now_local,
        ferries_on=ferry_service is not None,
        flights_on=flight_service is not None,
        force=force,
    )

    if ferry_service is not None and due.ferry_destinations:
        # A destination without routes yet (lookup pending or failed) is not
        # claimed: it is collected once its routes exist, the same day.
        with_routes = sorted(
            {route["canonical_destination"] for route in ferry_service.repository.routes_to_collect(sorted(due.ferry_destinations))}
        )
        claimed = [destination for destination in with_routes if settings_repository.claim_run("ferries", destination, day)]
        if claimed:
            saved_by_destination: dict[str, int] = {}
            try:
                report = ferry_service.collect(day, destinations=claimed)
                saved_by_destination = report.destinations
                result["ferries"] = report.saved
                logger.info(
                    "Ferry demand collection %s: %s call(s), %s saved, %s failed%s",
                    ",".join(claimed),
                    report.calls,
                    report.saved,
                    report.failed,
                    " (stopped early)" if report.aborted else "",
                )
                ferry_service.repository.prune(
                    raw_payload_days=settings.ferry_raw_payload_retention_days,
                    observation_days=FERRY_OBSERVATION_RETENTION_DAYS,
                )
            except Exception:
                logger.exception("Ferry demand collection crashed")
            finally:
                for destination in claimed:
                    settings_repository.finish_run("ferries", destination, day, saved_by_destination.get(destination, 0))

    if flight_service is not None and due.flights and settings_repository.claim_run("flights", FLIGHTS_SCOPE, day):
        saved = 0
        try:
            report = flight_service.collect(day)
            saved = report.saved
            result["flights"] = saved
            logger.info(
                "Flight demand collection: airports %s, %s due, %s call(s) of %s allowed, %s saved, units left %s%s",
                ",".join(report.airports) or "none",
                report.due,
                report.calls,
                report.allowance,
                report.saved,
                report.units_remaining if report.units_remaining is not None else "?",
                " (stopped early)" if report.aborted or report.out_of_units else "",
            )
            flight_service.repository.prune(
                raw_payload_days=settings.flight_raw_payload_retention_days,
                observation_days=FERRY_OBSERVATION_RETENTION_DAYS,
            )
        except Exception:
            logger.exception("Flight demand collection crashed")
        finally:
            settings_repository.finish_run("flights", FLIGHTS_SCOPE, day, saved)

    if result["ferries"] or result["flights"]:
        settings_repository.prune_runs(keep_days=DEMAND_RUN_RETENTION_DAYS, today=day)
    return result


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
        run_raw_ingestion_cleanup,
        trigger="cron",
        hour=4,
        minute=55,
        id="raw_ingestion_cleanup",
        name="Delete raw ingestion events past retention",
    )
    # Lookups within minutes of a claim, collections at each owner's hour or
    # on «Συλλογή τώρα»; the first tick shortly after the worker starts, so
    # a worker that is up only now and then still collects.
    scheduler.add_job(
        run_demand_tick,
        trigger="interval",
        minutes=DEMAND_TICK_MINUTES,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        id="demand_tick",
        name="Find island ports and collect the due ferry and flight demand",
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
