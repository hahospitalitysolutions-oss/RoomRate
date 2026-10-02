from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Protocol

from api.repositories.scrape_jobs_repository import (
    DailyQuotaExceededError,
    TooManyActiveJobsError,
)
from api.schemas.schedule import ScheduleConfigResponse, ScheduleConfigUpdate
from api.schemas.scrape_jobs import JOB_TYPE_COMPETITOR_SEARCH, ScrapeJobCreate

logger = logging.getLogger(__name__)

# Circuit breaker: after this many consecutive scheduled-job failures the
# schedule is disabled until the operator explicitly re-enables it.
FAILURE_DISABLE_THRESHOLD = 3

# Scheduled scrapes use a fixed, conservative result limit. The "scheduled"
# flag (which switches the runner to the 12h scout cache and places the job at
# the back of the scheduler rotation) is NOT set here: ScrapeJobCreate strips
# any client-supplied "scheduled" key, so it is applied exclusively via
# ScrapeJobService.create_job(..., scheduled=True) on the scheduler path below.
SCHEDULED_FILTERS_PAYLOAD = {"limit": 25}


class ScheduleRepositoryProtocol(Protocol):
    def get_config(self, account_id: uuid.UUID) -> dict[str, Any] | None: ...

    def upsert_config(self, account_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]: ...

    def list_due_configs(self, now: datetime) -> list[dict[str, Any]]: ...

    def touch_last_run(self, config_id: uuid.UUID, now: datetime) -> None: ...

    def record_failure(self, account_id: uuid.UUID) -> int: ...

    def reset_failures(self, account_id: uuid.UUID) -> None: ...

    def disable(self, account_id: uuid.UUID) -> None: ...

    def list_schedulable_properties(self, account_id: uuid.UUID) -> list[dict[str, Any]]: ...


class ScrapeJobCreatorProtocol(Protocol):
    def create_job(
        self, account_id: uuid.UUID, request: ScrapeJobCreate, scheduled: bool = False
    ) -> Any: ...


class ScheduleDisabledNotifierProtocol(Protocol):
    def notify_schedule_disabled(self, account_id: uuid.UUID, consecutive_failures: int) -> None:
        """Notify the account that its schedule was auto-disabled."""


class ScheduleService:
    """Application service for schedule configuration and scheduled enqueueing."""

    def __init__(
        self,
        repository: ScheduleRepositoryProtocol,
        scrape_job_service: ScrapeJobCreatorProtocol,
        notifier: ScheduleDisabledNotifierProtocol | None = None,
    ):
        self.repository = repository
        self.scrape_job_service = scrape_job_service
        # Optional: the schedule-disabled notifier (Phase D). When unset the
        # breaker still disables schedules silently.
        self.notifier = notifier

    def get_config(self, account_id: uuid.UUID) -> ScheduleConfigResponse:
        """Return the account's schedule config, or defaults when unset."""
        row = self.repository.get_config(account_id)
        if row is None:
            return ScheduleConfigResponse(account_id=account_id)
        return ScheduleConfigResponse.model_validate(row)

    def update_config(self, account_id: uuid.UUID, payload: ScheduleConfigUpdate) -> ScheduleConfigResponse:
        """Merge a partial update over the current config and upsert it.

        Enabling a previously disabled schedule resets ``consecutive_failures``:
        re-enabling is an explicit operator "try again", so a breaker tripped
        by past failures must not immediately re-disable the schedule.
        """
        current = self.get_config(account_id)
        merged = current.model_dump()
        was_enabled = merged["enabled"]
        merged.update(payload.model_dump(exclude_unset=True))
        enabling = merged["enabled"] and not was_enabled
        if enabling:
            # Covers the insert path; existing rows are reset explicitly below
            # because the upsert's DO UPDATE never touches the breaker column
            # (a stale PUT must not clobber concurrent breaker updates).
            merged["consecutive_failures"] = 0
        row = self.repository.upsert_config(account_id, merged)
        if enabling:
            self.repository.reset_failures(account_id)
            row = {**row, "consecutive_failures": 0}
        return ScheduleConfigResponse.model_validate(row)

    def run_due_schedules(self, now: datetime) -> int:
        """Enqueue scheduled scrape jobs for every due config; returns count.

        For each due config the ``last_run_at`` stamp is written FIRST and
        unconditionally — even when the account yields zero jobs or fails —
        so a broken account cannot re-tick on every scheduler cycle.

        Per-job quota refusals (concurrency cap, daily cap, duplicate active
        market) are expected operating conditions: log and skip the job.
        Unexpected per-account errors feed the circuit breaker and processing
        continues with the next account.

        Fairness under the active-jobs cap comes entirely from the repository
        ordering: ``list_schedulable_properties`` returns least-recently-
        scheduled properties first, and jobs are enqueued in that exact order,
        so cap-skipped properties rotate to the front on later cycles. This
        method must never reorder the returned properties.
        """
        created = 0
        for config in self.repository.list_due_configs(now):
            account_id = config["account_id"]
            try:
                self.repository.touch_last_run(config["id"], now)
                properties = self.repository.list_schedulable_properties(account_id)
                enqueued = 0
                skipped = 0
                for request in self._build_job_payloads(config, properties, now):
                    try:
                        # scheduled=True is the ONLY way the "scheduled" flag is
                        # set; client requests can never spoof it (see schema).
                        self.scrape_job_service.create_job(account_id, request, scheduled=True)
                        enqueued += 1
                        created += 1
                    except (TooManyActiveJobsError, DailyQuotaExceededError) as exc:
                        skipped += 1
                        logger.warning(
                            "Scheduled scrape job skipped (quota): account_id=%s destination=%s error=%s",
                            account_id,
                            request.destination,
                            exc,
                            extra={"account_id": str(account_id), "config_id": str(config["id"])},
                        )
                if enqueued:
                    logger.info(
                        "Schedule tick enqueued jobs: account_id=%s jobs_enqueued=%s",
                        account_id,
                        enqueued,
                        extra={
                            "account_id": str(account_id),
                            "config_id": str(config["id"]),
                            "jobs_enqueued": enqueued,
                        },
                    )
                if skipped:
                    # Operators need to see sustained cap pressure: skipped
                    # properties only get a slot via least-recently-scheduled
                    # rotation, so chronic skips mean the cap is too low for
                    # this account's property count.
                    logger.warning(
                        "Schedule quota pressure: account_id=%s properties_enqueued=%s properties_skipped=%s",
                        account_id,
                        enqueued,
                        skipped,
                        extra={
                            "account_id": str(account_id),
                            "config_id": str(config["id"]),
                            "properties_skipped": skipped,
                        },
                    )
            except Exception:
                logger.exception(
                    "Scheduled enqueue failed: account_id=%s",
                    account_id,
                    extra={"account_id": str(account_id)},
                )
                self.record_scheduled_job_failure(account_id)
        return created

    def record_scheduled_job_failure(self, account_id: uuid.UUID) -> int:
        """Count one scheduled failure; trip the breaker at the threshold."""
        failures = self.repository.record_failure(account_id)
        if failures >= FAILURE_DISABLE_THRESHOLD:
            self.repository.disable(account_id)
            logger.warning(
                "Schedule disabled after %s consecutive scheduled-job failures: account_id=%s",
                failures,
                account_id,
                extra={"account_id": str(account_id), "consecutive_failures": failures},
            )
            self._notify_schedule_disabled(account_id, failures)
        return failures

    def _notify_schedule_disabled(self, account_id: uuid.UUID, consecutive_failures: int) -> None:
        """Best-effort schedule-disabled notification; never aborts the breaker."""
        if self.notifier is None:
            return
        try:
            self.notifier.notify_schedule_disabled(account_id, consecutive_failures)
        except Exception:
            logger.exception("Schedule-disabled notification failed: account_id=%s", account_id)

    def record_scheduled_job_success(self, account_id: uuid.UUID) -> None:
        """Any scheduled success proves the pipeline works: reset the breaker."""
        self.repository.reset_failures(account_id)

    def _build_job_payloads(
        self,
        config: dict[str, Any],
        properties: list[dict[str, Any]],
        now: datetime,
    ) -> list[ScrapeJobCreate]:
        """Build one competitor_search job per schedulable owned property."""
        check_in = now.date() + timedelta(days=config["lead_days"])
        check_out = check_in + timedelta(days=config["nights"])
        return [
            ScrapeJobCreate(
                owned_property_id=schedulable_property["id"],
                job_type=JOB_TYPE_COMPETITOR_SEARCH,
                room_type_category=schedulable_property["selected_room_type_category"],
                # The user-facing destination string scrapes best; fall back to
                # the canonical form for legacy rows without one.
                destination=(
                    schedulable_property["raw_destination"]
                    or schedulable_property["canonical_destination"]
                ),
                check_in=check_in,
                check_out=check_out,
                adults=config["adults"],
                children=config["children"],
                rooms=config["rooms"],
                filters_payload=dict(SCHEDULED_FILTERS_PAYLOAD),
            )
            for schedulable_property in properties
        ]
