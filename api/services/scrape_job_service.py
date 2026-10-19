from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from api.config import settings
from api.schemas.scrape_jobs import (
    JOB_TYPE_COMPETITOR_SEARCH,
    JOB_TYPE_ROOM_DISCOVERY,
    ScrapeJobCreate,
    ScrapeJobResponse,
)
from api.services.market_helpers import as_optional_float
from api.services.retry_policy import calculate_exponential_backoff_seconds

logger = logging.getLogger(__name__)

# The scraper runs as a module (`python -m scraper`) rather than a script
# path: the package is the entry point, and -m makes the interpreter resolve
# it via sys.path instead of depending on a file location. PROJECT_ROOT is
# still needed because the subprocess inherits the worker's cwd, which is not
# guaranteed to be the repo root.
SCRAPER_MODULE = "scraper"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Where per-job scraper CSV outputs land (relative to the worker's cwd).
SCRAPE_OUTPUT_DIR = Path("output/scrapes")
RESULT_SUMMARY_SENTINEL = "ROOMRATE_RESULT_SUMMARY"

# Round 6 sizing (§3.1): default result limit, per-nearby-area scout size,
# deep-crawl hotel cap, and the batch size the runner pins on every job.
DEFAULT_RESULT_LIMIT = 40
MAX_NEARBY_LIMIT = 20
MAX_DEEP_CRAWL_HOTELS = 120
MAX_DEEP_CRAWL_ITEMS = 320
DEEP_CRAWL_BATCH_SIZE = 8
MANUAL_SCOUT_CACHE_HOURS = 2
RADIUS_SKIPPED_WARNING = "radius_skipped_no_coordinates"


class ScrapeJobTimeoutError(TimeoutError):
    """Raised when the scraper subprocess exceeds the configured timeout."""


def _is_retryable_scrape_error(error: Exception) -> bool:
    """Return whether an operational scraper error merits another attempt."""
    return isinstance(error, (TimeoutError, OSError, RuntimeError))


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Return a bounded integer from untrusted job filter payload values."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _merge_warnings(result_summary: dict | None, warnings: list[str]) -> dict | None:
    """Append service-side warnings to the scraper's summary.

    Warnings flow one way: the scraper emits ``warnings: []`` in its sentinel
    summary (nearby scout failures land there) and the service appends its
    own. A run with no parsable summary still records the service warning as
    ``{"warnings": [...]}``; with nothing to add the summary passes through.
    """
    if not warnings:
        return result_summary
    existing = list((result_summary or {}).get("warnings") or [])
    return {**(result_summary or {}), "warnings": [*existing, *warnings]}


@dataclass(frozen=True)
class ScrapeJobCommand:
    """Runtime command passed to the scraper runner."""

    job_id: uuid.UUID
    account_id: uuid.UUID
    destination: str
    check_in: date
    check_out: date
    adults: int
    children: int
    rooms: int
    job_type: str
    room_type_category: str | None
    filters_payload: dict
    # True only for scheduler-created jobs (the jobs table's ``scheduled``
    # column); switches the runner to the shared scout cache.
    scheduled: bool = False
    # Round 6: neighbouring areas + radius; the origin is the OWNER's
    # coordinates resolved in ScrapeJobService.run_job, never sent by the
    # browser. radius_km without an origin means "no radius" (flags omitted).
    nearby_destinations: tuple[str, ...] = ()
    radius_km: float | None = None
    origin_lat: float | None = None
    origin_lng: float | None = None


class ScrapeJobRepositoryProtocol(Protocol):
    def create_job(
        self, account_id: uuid.UUID, request: ScrapeJobCreate, scheduled: bool = False
    ) -> dict:
        """Persist a queued scrape job."""

    def get_job(self, account_id: uuid.UUID, job_id: uuid.UUID) -> dict | None:
        """Fetch one account-scoped scrape job."""

    def list_jobs(self, account_id: uuid.UUID, limit: int = 50) -> list[dict]:
        """List recent account-scoped scrape jobs."""

    def claim_job(self, account_id: uuid.UUID, job_id: uuid.UUID, claimed_by: str) -> bool:
        """Atomically claim one queued job for execution; False if already claimed."""

    def heartbeat(self, account_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """Record liveness for a running job."""

    def complete_job(
        self,
        account_id: uuid.UUID,
        job_id: uuid.UUID,
        refresh_owned_property_id: uuid.UUID | None = None,
        result_summary: dict | None = None,
    ) -> tuple[bool, int]:
        """Atomically persist refresh work and mark the job completed.

        Returns (transition_applied, discovered_room_type_count).
        """

    def union_owned_room_types_from_job(self, account_id: uuid.UUID, job_id: uuid.UUID) -> int:
        """Add the owner's room types seen in a completed job (union only); returns rows added."""

    def mark_failed(self, account_id: uuid.UUID, job_id: uuid.UUID, error_message: str) -> bool:
        """Mark a running scrape job as failed; False if it was not running."""

    def retry_or_fail(
        self,
        account_id: uuid.UUID,
        job_id: uuid.UUID,
        error_message: str,
        retry_delay_seconds: float,
    ) -> str | None:
        """Requeue a failed attempt or fail it when the attempt budget is exhausted."""

    def list_queued_jobs(self, limit: int = 3) -> list[dict]:
        """List the oldest queued jobs across ALL accounts (executor safety net)."""

    def fail_stale_jobs(self, stale_after_minutes: int = 5) -> int:
        """Fail running jobs whose worker stopped heartbeating; returns count."""


class ScrapeJobRunnerProtocol(Protocol):
    def run(self, command: ScrapeJobCommand, heartbeat: Callable[[], None] | None = None) -> dict | None:
        """Run the external scrape for one job."""


class AlertEvaluatorProtocol(Protocol):
    def evaluate_completed_job(self, account_id: uuid.UUID, job: object) -> int:
        """Raise price-change notifications for a completed competitor scrape."""


class OwnedPropertyLookupProtocol(Protocol):
    def get_owned_property(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict | None:
        """Return the owned property row (with latitude/longitude), or None."""


class RoomMatcherProtocol(Protocol):
    def run_for_completed_job(self, account_id: uuid.UUID, job: object) -> object | None:
        """Run AI room matching for a completed competitor scrape (idempotent)."""


class BookingScrapeJobRunner:
    """Bridge from persisted RoomRate scrape jobs to the Booking scraper CLI.

    The scraper runs as a child process (not in-process) so a hung or runaway
    scrape can be killed after ``timeout_seconds`` without taking the API
    worker down with it.
    """

    def __init__(
        self,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 5.0,
        heartbeat_interval_seconds: float = 30.0,
    ):
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.scrape_job_timeout_seconds
        )
        # The poll interval stays short for timeout responsiveness; heartbeats
        # are throttled separately so the DB is not touched on every poll.
        self.poll_interval_seconds = poll_interval_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    def run(self, command: ScrapeJobCommand, heartbeat: Callable[[], None] | None = None) -> dict | None:
        """Execute the Booking scraper subprocess for one persisted job."""
        # Filesystem side effect lives here (not in _build_args) so arg
        # construction stays pure and unit-testable.
        SCRAPE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return self._execute(self._build_args(command), heartbeat=heartbeat)

    def _build_args(self, command: ScrapeJobCommand) -> list[str]:
        """Build the scraper CLI argument list from one job command (pure)."""
        output_dir = SCRAPE_OUTPUT_DIR
        filters_payload = command.filters_payload or {}
        # Round 6 sizing (§3.1): 40 results by default, up to 20 per nearby
        # area, at most 120 hotels deep-crawled; the package budget follows
        # the hotel cap unless the payload pins deep_crawl_max_items.
        result_limit = _bounded_int(
            filters_payload.get("limit"), default=DEFAULT_RESULT_LIMIT, minimum=1, maximum=80
        )
        nearby_destinations = [str(area).strip() for area in command.nearby_destinations if str(area).strip()]
        nearby_limit = min(MAX_NEARBY_LIMIT, result_limit)
        hotel_cap = min(MAX_DEEP_CRAWL_HOTELS, result_limit + nearby_limit * len(nearby_destinations))
        deep_crawl_default = min(MAX_DEEP_CRAWL_ITEMS, max(2 * hotel_cap, 40))
        deep_crawl_max_items = _bounded_int(
            filters_payload.get("deep_crawl_max_items"),
            default=deep_crawl_default,
            minimum=result_limit,
            maximum=MAX_DEEP_CRAWL_ITEMS,
        )
        # The scout cache holds only the area's hotel LIST (names, links,
        # coordinates), never prices: every job deep-crawls live prices. So a
        # manual search may reuse a list up to 2 hours old for the same stay
        # and party (a re-run after changing filters, a second account member)
        # and skip a paid scout run; scheduled runs reuse it for 12 hours.
        scout_cache_hours = "12" if command.scheduled else str(MANUAL_SCOUT_CACHE_HOURS)
        args = [
            "--account-id",
            str(command.account_id),
            "--scrape-job-id",
            str(command.job_id),
            "--destination",
            command.destination,
            "--check-in",
            command.check_in.isoformat(),
            "--check-out",
            command.check_out.isoformat(),
            "--adults",
            str(command.adults),
            "--children",
            str(command.children),
            "--rooms",
            str(command.rooms),
            "--job-type",
            command.job_type,
            "--scout-cache-hours",
            scout_cache_hours,
            "--scout-max-items",
            str(result_limit),
            "--deep-crawl-max-items",
            str(deep_crawl_max_items),
            "--deep-crawl-max-hotels",
            str(hotel_cap),
            "--deep-crawl-batch-size",
            str(DEEP_CRAWL_BATCH_SIZE),
            "--output-csv",
            str(output_dir / f"scrape_job_{command.job_id}.csv"),
        ]
        if command.room_type_category:
            args.extend(["--room-type-category", command.room_type_category])
        room_name_query = str(filters_payload.get("room_type") or "").strip()
        if room_name_query:
            args.extend(["--room-name-query", room_name_query])
        required_meal = str(filters_payload.get("meals") or "").strip()
        if required_meal:
            args.extend(["--required-meal", required_meal])
        required_free_cancellation = str(filters_payload.get("free_cancellation") or "").strip()
        if required_free_cancellation:
            args.extend(["--required-free-cancellation", required_free_cancellation])
        for amenity in filters_payload.get("amenities", []):
            if str(amenity).strip():
                args.extend(["--required-amenity", str(amenity)])
        for target_url in filters_payload.get("target_urls", []):
            args.extend(["--target-url", str(target_url)])
        for area in nearby_destinations:
            args.extend(["--nearby-destination", area])
        if nearby_destinations:
            args.extend(["--nearby-max-items", str(nearby_limit)])
        if command.radius_km is not None and command.origin_lat is not None and command.origin_lng is not None:
            args.extend(
                [
                    "--origin-lat",
                    str(float(command.origin_lat)),
                    "--origin-lng",
                    str(float(command.origin_lng)),
                    "--radius-km",
                    str(float(command.radius_km)),
                ]
            )
        return args

    def _execute(self, args: list[str], heartbeat: Callable[[], None] | None = None) -> dict | None:
        """Run the scraper subprocess with heartbeat + hard timeout enforcement."""
        is_windows = os.name == "nt"
        # PYTHONPATH carries the repo root so `-m scraper` resolves no matter
        # what cwd the worker was started from.
        environment = {**os.environ}
        existing_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            f"{PROJECT_ROOT}{os.pathsep}{existing_path}" if existing_path else str(PROJECT_ROOT)
        )
        # The scraper emits Greek logs plus a machine-readable JSON sentinel.
        # Windows otherwise decodes PIPE text with the active ANSI code page
        # (commonly cp1252), which can crash subprocess' reader thread before
        # the final result summary is returned. Make both ends explicitly UTF-8.
        environment["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            [sys.executable, "-m", SCRAPER_MODULE, *args],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            # New process group/session so we can kill the whole tree on timeout.
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if is_windows else 0,
            start_new_session=(not is_windows),
        )
        started_at = time.monotonic()
        # claim_job already stamped heartbeat_at, so the first throttled
        # heartbeat only needs to land within heartbeat_interval_seconds.
        last_heartbeat_at = started_at
        stderr_output = ""
        stdout_output = ""
        while True:
            try:
                # communicate() drains stdout/stderr, so a chatty scraper can
                # never deadlock on a full pipe buffer while we wait.
                stdout_output, stderr_output = process.communicate(timeout=self.poll_interval_seconds)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now - last_heartbeat_at >= self.heartbeat_interval_seconds:
                    self._emit_heartbeat(heartbeat)
                    last_heartbeat_at = now
                elapsed = time.monotonic() - started_at
                if elapsed > self.timeout_seconds:
                    self._kill_process_tree(process)
                    raise ScrapeJobTimeoutError(
                        f"Scraper subprocess (pid={process.pid}) exceeded the "
                        f"{self.timeout_seconds}s timeout and was killed"
                    ) from None
        if process.returncode != 0:
            stderr_tail = (stderr_output or "").strip()[-2000:]
            raise RuntimeError(
                f"Scraper subprocess failed with exit code {process.returncode}: {stderr_tail}"
            )
        return self._parse_result_summary(stdout_output)

    @staticmethod
    def _parse_result_summary(stdout_output: str | None) -> dict | None:
        """Parse the final sentinel line; missing/malformed payloads fail safe."""
        sentinel_lines = [
            line for line in (stdout_output or "").splitlines()
            if RESULT_SUMMARY_SENTINEL in line
        ]
        if not sentinel_lines:
            logger.warning("Scraper completed without a %s payload", RESULT_SUMMARY_SENTINEL)
            return None
        payload_text = sentinel_lines[-1].split(RESULT_SUMMARY_SENTINEL, 1)[1].strip()
        try:
            payload = json.loads(payload_text)
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("unsupported or missing result-summary version")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Ignoring malformed scraper result summary: %s", exc)
            return None
        return payload

    @staticmethod
    def _emit_heartbeat(heartbeat: Callable[[], None] | None) -> None:
        """Heartbeat failures (e.g. transient DB issues) must not kill the scrape."""
        if heartbeat is None:
            return
        try:
            heartbeat()
        except Exception:
            logger.warning("Scrape job heartbeat failed; continuing scrape", exc_info=True)

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen) -> None:
        """Best-effort kill of the scraper subprocess and all of its children."""
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            logger.warning("Process-tree kill failed; falling back to direct kill", exc_info=True)
        try:
            process.kill()
        except Exception:
            pass
        try:
            # Reap the child and close its pipes so we don't leak handles.
            process.communicate(timeout=10)
        except Exception:
            pass


class ScrapeJobService:
    """Application service for scrape-job lifecycle and worker execution."""

    def __init__(
        self,
        repository: ScrapeJobRepositoryProtocol,
        runner: ScrapeJobRunnerProtocol,
        alert_evaluator: AlertEvaluatorProtocol | None = None,
        retry_base_seconds: int | None = None,
        retry_max_seconds: int | None = None,
        owned_property_lookup: OwnedPropertyLookupProtocol | None = None,
        room_matcher: RoomMatcherProtocol | None = None,
    ):
        self.repository = repository
        self.runner = runner
        # Optional (Phase D): evaluates completed competitor scrapes for price
        # alerts. Wired on both the API and scheduler paths.
        self.alert_evaluator = alert_evaluator
        # Optional (spec 2026-09-29 Α.1): AI room matching after a completed
        # competitor scrape, best-effort exactly like the alerts above.
        self.room_matcher = room_matcher
        # Round 6: resolves the radius centre from the owner's property. None
        # (tests, minimal wiring) means every radius request degrades to
        # "no radius + warning".
        self.owned_property_lookup = owned_property_lookup
        self.retry_base_seconds = (
            retry_base_seconds
            if retry_base_seconds is not None
            else settings.scrape_job_retry_base_seconds
        )
        self.retry_max_seconds = (
            retry_max_seconds
            if retry_max_seconds is not None
            else settings.scrape_job_retry_max_seconds
        )

    def create_job(
        self,
        account_id: uuid.UUID,
        request: ScrapeJobCreate,
        scheduled: bool = False,
    ) -> ScrapeJobResponse:
        """Create a queued job without running it inline in the request.

        ``scheduled`` is an INTERNAL flag set only by the scheduler path
        (``ScheduleService``) and stored in the jobs table's ``scheduled``
        column. ``ScrapeJobCreate`` strips any client-supplied
        ``filters_payload.scheduled`` (legacy location), so the flag can only
        be set here — a manual API request can never mark its job as scheduled
        (which would grant 12h scout-cache sharing and back-of-rotation
        placement).
        """
        row = self.repository.create_job(account_id, request, scheduled=scheduled)
        return ScrapeJobResponse.model_validate(row)

    def get_job(self, account_id: uuid.UUID, job_id: uuid.UUID) -> ScrapeJobResponse | None:
        """Fetch one job by id for polling/status screens."""
        row = self.repository.get_job(account_id, job_id)
        return ScrapeJobResponse.model_validate(row) if row else None

    def list_jobs(self, account_id: uuid.UUID, limit: int = 50) -> list[ScrapeJobResponse]:
        """List recent jobs for the current account."""
        return [ScrapeJobResponse.model_validate(row) for row in self.repository.list_jobs(account_id, limit=limit)]

    def run_job(self, account_id: uuid.UUID, job_id: uuid.UUID) -> bool:
        """Claim and run a queued job, recording lifecycle status atomically.

        Returns True only when THIS call won the claim and executed the job —
        regardless of whether the run completed or failed. Returns False when
        the job was not found or another worker already claimed it. The
        queued-job executor gates its breaker feedback on this so two workers
        racing the same scheduled job can never double-feed the breaker;
        BackgroundTasks callers simply ignore the value.
        """
        job = self.get_job(account_id, job_id)
        if not job:
            logger.warning(
                "Scrape job not found or not account scoped: account_id=%s job_id=%s",
                account_id,
                job_id,
                extra={"account_id": str(account_id), "job_id": str(job_id)},
            )
            return False

        claimed_by = f"{socket.gethostname()}:{os.getpid()}"
        if not self.repository.claim_job(account_id, job_id, claimed_by):
            logger.info(
                "Scrape job already claimed by another worker: account_id=%s job_id=%s claimed_by=%s",
                account_id,
                job_id,
                claimed_by,
                extra={"account_id": str(account_id), "job_id": str(job_id), "claimed_by": claimed_by},
            )
            return False
        logger.info(
            "Scrape job claimed: account_id=%s job_id=%s",
            account_id,
            job_id,
            extra={"account_id": str(account_id), "job_id": str(job_id), "claimed_by": claimed_by},
        )

        def _heartbeat() -> None:
            self.repository.heartbeat(account_id, job_id)

        origin, service_warnings = self._resolve_radius_origin(account_id, job)
        try:
            result_summary = self.runner.run(
                ScrapeJobCommand(
                    job_id=job.id,
                    account_id=job.account_id,
                    destination=job.destination,
                    check_in=job.check_in,
                    check_out=job.check_out,
                    adults=job.adults,
                    children=job.children,
                    rooms=job.rooms,
                    job_type=job.job_type,
                    room_type_category=job.room_type_category,
                    filters_payload=job.filters_payload,
                    scheduled=job.scheduled,
                    nearby_destinations=tuple(job.nearby_destinations),
                    radius_km=job.radius_km,
                    origin_lat=origin[0] if origin else None,
                    origin_lng=origin[1] if origin else None,
                ),
                heartbeat=_heartbeat,
            )
        except Exception as exc:
            logger.exception(
                "Scrape job failed: account_id=%s job_id=%s",
                account_id,
                job_id,
                extra={"account_id": str(account_id), "job_id": str(job_id)},
            )
            transition: str | None
            if _is_retryable_scrape_error(exc):
                attempt_number = job.attempt_count + 1
                retry_delay = calculate_exponential_backoff_seconds(
                    base_seconds=self.retry_base_seconds,
                    attempt=attempt_number,
                    max_seconds=self.retry_max_seconds,
                )
                transition = self.repository.retry_or_fail(
                    account_id,
                    job_id,
                    str(exc),
                    retry_delay_seconds=retry_delay,
                )
                if transition == "queued":
                    logger.warning(
                        "Scrape job requeued after attempt %s/%s in %.1fs: account_id=%s job_id=%s",
                        attempt_number,
                        job.max_attempts,
                        retry_delay,
                        account_id,
                        job_id,
                        extra={
                            "account_id": str(account_id),
                            "job_id": str(job_id),
                            "attempt": attempt_number,
                            "max_attempts": job.max_attempts,
                            "retry_delay_seconds": retry_delay,
                        },
                    )
            else:
                transition = (
                    "failed"
                    if self.repository.mark_failed(account_id, job_id, str(exc))
                    else None
                )
            if transition is None:
                logger.warning(
                    "Scrape job failed-transition did not apply (job no longer running, "
                    "likely swept or changed concurrently): account_id=%s job_id=%s",
                    account_id,
                    job_id,
                    extra={"account_id": str(account_id), "job_id": str(job_id)},
                )
            # The job DID run under our claim. The executor reloads its status:
            # queued retries do not feed the schedule breaker; terminal
            # failures do.
            return True

        refresh_owned_property_id = (
            job.owned_property_id
            if job.job_type == JOB_TYPE_ROOM_DISCOVERY and job.owned_property_id
            else None
        )
        result_summary = _merge_warnings(result_summary, service_warnings)
        # Refresh + completion happen in ONE transaction so a crash between the
        # two can never leave the job stuck in 'running'.
        completed, discovered_count = self.repository.complete_job(
            account_id,
            job_id,
            refresh_owned_property_id=refresh_owned_property_id,
            result_summary=result_summary,
        )
        if not completed:
            logger.warning(
                "Scrape job completed-transition did not apply (job no longer running, "
                "likely swept or changed concurrently): account_id=%s job_id=%s",
                account_id,
                job_id,
                extra={"account_id": str(account_id), "job_id": str(job_id)},
            )
        else:
            logger.info(
                "Scrape job completed: account_id=%s job_id=%s",
                account_id,
                job_id,
                extra={"account_id": str(account_id), "job_id": str(job_id)},
            )
        if refresh_owned_property_id:
            logger.info(
                "Owned property room discovery refreshed %s room types: account_id=%s job_id=%s",
                discovered_count,
                account_id,
                job_id,
                extra={
                    "account_id": str(account_id),
                    "job_id": str(job_id),
                    "discovered_room_type_count": discovered_count,
                },
            )
        if completed:
            # Every job type: the owner's hotel shows up in competitor runs
            # too. Before room matching, so this job's matcher sees the rooms.
            self._union_owned_room_types(account_id, job_id)
        if completed and job.job_type == JOB_TYPE_COMPETITOR_SEARCH:
            self._evaluate_alerts(account_id, job)
            self._run_room_matching(account_id, job)
        return True

    def _resolve_radius_origin(
        self, account_id: uuid.UUID, job: ScrapeJobResponse
    ) -> tuple[tuple[float, float] | None, list[str]]:
        """Resolve the radius centre from the OWNER's property, never the browser.

        Every miss (no owned property on the job, no lookup wired, a lookup
        failure, missing or zero coordinates) degrades to "no radius" plus the
        ``radius_skipped_no_coordinates`` warning rather than failing the job.
        """
        if job.radius_km is None:
            return None, []
        owned: dict = {}
        if self.owned_property_lookup is not None and job.owned_property_id:
            try:
                owned = self.owned_property_lookup.get_owned_property(account_id, job.owned_property_id) or {}
            except Exception:
                logger.warning(
                    "Owned property lookup failed; running without radius: account_id=%s job_id=%s",
                    account_id,
                    job.id,
                    exc_info=True,
                )
        latitude = as_optional_float(owned.get("latitude"))
        longitude = as_optional_float(owned.get("longitude"))
        # 0.0 is how a missing coordinate ends up stored (as_coord coerces
        # unparseable values to 0.0), and the scraper treats it the same way.
        if not latitude or not longitude:
            logger.warning(
                "Radius %.1f km requested but the owned property has no coordinates; running without radius: "
                "account_id=%s job_id=%s",
                job.radius_km,
                account_id,
                job.id,
            )
            return None, [RADIUS_SKIPPED_WARNING]
        return (latitude, longitude), []

    def _union_owned_room_types(self, account_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """Add the owner's room types this completed job saw to the owned catalog.

        Booking lists only the rooms that fit the searched party and
        discovery runs once, so the catalog would otherwise miss rooms (e.g.
        the single). The repository matches the owner's hotel by name in the
        job's runs and only ever ADDS rows. Strictly best-effort: the job is
        already completed, so a failure is logged and swallowed and never
        changes the job's status or run_job's return value.
        """
        try:
            added = self.repository.union_owned_room_types_from_job(account_id, job_id)
        except Exception:
            logger.exception(
                "Owned room-type union failed (job already completed): account_id=%s job_id=%s",
                account_id,
                job_id,
            )
            return
        if added:
            logger.info(
                "Owned room catalog gained %s room types from a completed job: account_id=%s job_id=%s",
                added,
                account_id,
                job_id,
                extra={
                    "account_id": str(account_id),
                    "job_id": str(job_id),
                    "added_room_type_count": added,
                },
            )

    def _evaluate_alerts(self, account_id: uuid.UUID, job: ScrapeJobResponse) -> None:
        """Run price-alert evaluation for a completed competitor scrape.

        Alert evaluation is strictly best-effort: a failure here must never
        change the job's status or run_job's return value, so everything is
        wrapped and logged.
        """
        if self.alert_evaluator is None:
            return
        try:
            self.alert_evaluator.evaluate_completed_job(account_id, job)
        except Exception:
            logger.exception(
                "Price-alert evaluation failed (job already completed): account_id=%s job_id=%s",
                account_id,
                job.id,
            )

    def _run_room_matching(self, account_id: uuid.UUID, job: ScrapeJobResponse) -> None:
        """Trigger AI room matching for a completed competitor scrape.

        Strictly best-effort (spec 2026-09-29 Α.1/Α.5): a failure here must
        never change the job's status or run_job's return value. The matcher
        itself is idempotent per (job, selected room) and skips silently
        without an API key.
        """
        if self.room_matcher is None:
            return
        try:
            self.room_matcher.run_for_completed_job(account_id, job)
        except Exception:
            logger.exception(
                "Room matching failed (job already completed): account_id=%s job_id=%s",
                account_id,
                job.id,
            )
