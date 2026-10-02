from datetime import date, datetime, timezone
from uuid import UUID

from api.repositories.scrape_jobs_repository import (
    DailyQuotaExceededError,
    TooManyActiveJobsError,
)
from api.schemas.schedule import ScheduleConfigUpdate
from api.services.schedule_service import FAILURE_DISABLE_THRESHOLD, ScheduleService


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000002")
CONFIG_ID = UUID("00000000-0000-0000-0000-000000000999")
PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
# 09:00 in Athens (summer, UTC+3): a daily 08:00 schedule is due.
NOW = datetime(2026, 6, 13, 6, 0, tzinfo=timezone.utc)
# 08:00 in Athens in winter (UTC+2).
WINTER_NOW = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)


def _config_row(**overrides) -> dict:
    row = {
        "id": CONFIG_ID,
        "account_id": ACCOUNT_ID,
        "enabled": True,
        "frequency_hours": 24,
        "hour_local": 8,
        "timezone": "Europe/Athens",
        "hour_utc": 5,
        "lead_days": 30,
        "nights": 3,
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "consecutive_failures": 0,
        "last_run_at": None,
    }
    row.update(overrides)
    return row


def _property_row(**overrides) -> dict:
    row = {
        "id": PROPERTY_ID,
        "raw_destination": "Φαληράκι",
        "canonical_destination": "faliraki",
        "selected_room_type_category": "double",
    }
    row.update(overrides)
    return row


class FakeScheduleRepository:
    def __init__(self, config: dict | None = None):
        self.config = config
        self.enabled: list[dict] = []
        self.properties: dict[UUID, list[dict]] = {}
        self.upserts: list[tuple[UUID, dict]] = []
        self.touches: list[tuple[UUID, datetime]] = []
        self.failure_calls: list[UUID] = []
        self.reset_calls: list[UUID] = []
        self.disable_calls: list[UUID] = []
        self.consecutive_failures = 0

    def get_config(self, account_id):
        return self.config

    def upsert_config(self, account_id, payload):
        self.upserts.append((account_id, payload))
        return {**_config_row(account_id=account_id), **payload}

    def list_enabled_configs(self):
        return list(self.enabled)

    def touch_last_run(self, config_id, now):
        self.touches.append((config_id, now))

    def record_failure(self, account_id):
        self.failure_calls.append(account_id)
        self.consecutive_failures += 1
        return self.consecutive_failures

    def reset_failures(self, account_id):
        self.reset_calls.append(account_id)
        self.consecutive_failures = 0

    def disable(self, account_id):
        self.disable_calls.append(account_id)

    def list_schedulable_properties(self, account_id):
        return list(self.properties.get(account_id, []))


class FakeScrapeJobService:
    def __init__(self, errors: list[Exception | None] | None = None):
        # One scripted outcome per create_job call; None means success.
        self.errors = list(errors or [])
        self.created: list[tuple[UUID, object]] = []
        self.scheduled_flags: list[bool] = []

    def create_job(self, account_id, request, scheduled: bool = False):
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        # Mirror the real service: the scheduler passes scheduled=True, which is
        # the only way the flag lands in the persisted filters_payload.
        if scheduled:
            request.filters_payload["scheduled"] = True
        self.created.append((account_id, request))
        self.scheduled_flags.append(scheduled)
        return request


class FakeAlertNotifier:
    """Records notify_schedule_disabled calls; can be set to raise."""

    def __init__(self, raise_on_notify: bool = False):
        self.calls: list[tuple[UUID, int]] = []
        self.raise_on_notify = raise_on_notify

    def notify_schedule_disabled(self, account_id, consecutive_failures):
        self.calls.append((account_id, consecutive_failures))
        if self.raise_on_notify:
            raise RuntimeError("notifier down")


def _service(repository=None, scrape_job_service=None, notifier=None, now=NOW) -> ScheduleService:
    return ScheduleService(
        repository=repository or FakeScheduleRepository(),
        scrape_job_service=scrape_job_service or FakeScrapeJobService(),
        notifier=notifier,
        clock=lambda: now,
    )


# ----------------------------------------------------------------------------
# get_config / update_config
# ----------------------------------------------------------------------------


def test_get_config_returns_defaults_when_account_has_no_row():
    service = _service(FakeScheduleRepository(config=None))

    config = service.get_config(ACCOUNT_ID)

    assert config.account_id == ACCOUNT_ID
    assert config.enabled is False
    assert config.frequency_hours == 24
    assert config.hour_local == 8
    assert config.timezone == "Europe/Athens"
    assert config.hour_utc == 5  # 08:00 in a Greek summer
    assert config.lead_days == 30
    assert config.nights == 3
    assert config.adults == 2
    assert config.children == 0
    assert config.rooms == 1
    assert config.consecutive_failures == 0
    assert config.last_run_at is None


def test_get_config_returns_persisted_row():
    repository = FakeScheduleRepository(config=_config_row(hour_local=10, hour_utc=7, consecutive_failures=2))
    service = _service(repository)

    config = service.get_config(ACCOUNT_ID)

    assert config.enabled is True
    assert config.hour_local == 10
    assert config.consecutive_failures == 2


def test_get_config_derives_the_legacy_utc_hour_for_today_not_the_stored_one():
    """An old client converts hour_utc back with today's offset: it must see 10 all year."""
    # Saved in summer (10:00 Athens = 07 UTC); read in winter, where 10:00 is 08 UTC.
    repository = FakeScheduleRepository(config=_config_row(hour_local=10, hour_utc=7))

    summer = _service(repository, now=NOW).get_config(ACCOUNT_ID)
    winter = _service(repository, now=WINTER_NOW).get_config(ACCOUNT_ID)

    assert (summer.hour_local, summer.hour_utc) == (10, 7)
    assert (winter.hour_local, winter.hour_utc) == (10, 8)


def test_update_config_merges_partial_payload_over_defaults():
    repository = FakeScheduleRepository(config=None)
    service = _service(repository)

    config = service.update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_local=9, nights=5))

    account_id, payload = repository.upserts[0]
    assert account_id == ACCOUNT_ID
    assert payload["hour_local"] == 9
    assert payload["nights"] == 5
    # Untouched fields keep their defaults so the upsert SQL stays static.
    assert payload["frequency_hours"] == 24
    assert payload["enabled"] is False
    assert config.hour_local == 9


def test_update_config_stores_hour_utc_for_older_images_at_todays_offset():
    """A rollback image still reads hour_utc: it follows hour_local on every save."""
    summer_repository = FakeScheduleRepository(config=None)
    winter_repository = FakeScheduleRepository(config=None)

    _service(summer_repository, now=NOW).update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_local=8))
    _service(winter_repository, now=WINTER_NOW).update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_local=8))

    assert summer_repository.upserts[0][1]["hour_utc"] == 5
    assert winter_repository.upserts[0][1]["hour_utc"] == 6


def test_update_config_reads_a_legacy_hour_utc_with_todays_offset():
    """A client built before hour_local converted 08:00 Greek time with today's offset."""
    summer_repository = FakeScheduleRepository(config=None)
    winter_repository = FakeScheduleRepository(config=None)

    summer = _service(summer_repository, now=NOW).update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_utc=5))
    winter = _service(winter_repository, now=WINTER_NOW).update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_utc=6))

    assert summer_repository.upserts[0][1]["hour_local"] == 8
    assert winter_repository.upserts[0][1]["hour_local"] == 8
    # Wrap-around: 22:00 UTC is 01:00 the next day in a Greek summer.
    wrapped_repository = FakeScheduleRepository(config=None)
    _service(wrapped_repository, now=NOW).update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_utc=22))
    assert wrapped_repository.upserts[0][1]["hour_local"] == 1
    assert (summer.hour_local, winter.hour_local) == (8, 8)


def test_update_config_prefers_hour_local_over_a_legacy_hour_utc():
    """A current client sends both (for an API that predates hour_local): hour_local wins."""
    repository = FakeScheduleRepository(config=None)

    _service(repository).update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_local=9, hour_utc=23))

    _, payload = repository.upserts[0]
    assert payload["hour_local"] == 9
    assert payload["hour_utc"] == 6


def test_update_config_resets_breaker_when_enabling():
    repository = FakeScheduleRepository(config=_config_row(enabled=False, consecutive_failures=3))
    service = _service(repository)

    config = service.update_config(ACCOUNT_ID, ScheduleConfigUpdate(enabled=True))

    _, payload = repository.upserts[0]
    assert payload["enabled"] is True
    # Re-enabling is an explicit operator retry: the tripped breaker resets via
    # an explicit reset_failures statement (the upsert's DO UPDATE deliberately
    # never touches the breaker column), and the response reflects the reset.
    assert repository.reset_calls == [ACCOUNT_ID]
    assert config.consecutive_failures == 0


def test_update_config_keeps_breaker_when_already_enabled():
    repository = FakeScheduleRepository(config=_config_row(enabled=True, consecutive_failures=2))
    service = _service(repository)

    service.update_config(ACCOUNT_ID, ScheduleConfigUpdate(hour_utc=9))

    _, payload = repository.upserts[0]
    assert payload["consecutive_failures"] == 2
    assert repository.reset_calls == []


# ----------------------------------------------------------------------------
# run_due_schedules
# ----------------------------------------------------------------------------


def test_run_due_schedules_creates_jobs_and_touches_last_run():
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row()]
    repository.properties[ACCOUNT_ID] = [_property_row()]
    scrape_job_service = FakeScrapeJobService()
    service = _service(repository, scrape_job_service)

    created = service.run_due_schedules(NOW)

    assert created == 1
    assert repository.touches == [(CONFIG_ID, NOW)]
    account_id, request = scrape_job_service.created[0]
    assert account_id == ACCOUNT_ID
    assert request.owned_property_id == PROPERTY_ID
    assert request.job_type == "competitor_search"
    assert request.destination == "Φαληράκι"
    assert request.room_type_category == "double"
    assert request.check_in == date(2026, 7, 13)  # now + 30 lead days
    assert request.check_out == date(2026, 7, 16)  # check_in + 3 nights
    assert request.adults == 2
    assert request.children == 0
    assert request.rooms == 1
    # The scheduler path passes scheduled=True explicitly; the fake mirrors the
    # real service by writing the flag into the persisted filters_payload.
    assert scrape_job_service.scheduled_flags == [True]
    assert request.filters_payload == {"scheduled": True, "limit": 25}


def test_run_due_schedules_runs_only_the_schedules_due_on_their_local_clock():
    """Already run today (local date), or before the local hour: skipped and untouched."""
    ran_today = _config_row(id=UUID(int=1), last_run_at=datetime(2026, 6, 13, 5, 5, tzinfo=timezone.utc))
    later_hour = _config_row(id=UUID(int=2), hour_local=10)
    due = _config_row(id=UUID(int=3), last_run_at=datetime(2026, 6, 12, 5, 5, tzinfo=timezone.utc))
    repository = FakeScheduleRepository()
    repository.enabled = [ran_today, later_hour, due]

    _service(repository).run_due_schedules(NOW)

    assert repository.touches == [(UUID(int=3), NOW)]


def test_run_due_schedules_counts_lead_days_from_the_local_date():
    """01:30 in Athens is still yesterday in UTC: the check-in follows the owner's date."""
    just_after_midnight = datetime(2026, 6, 12, 22, 30, tzinfo=timezone.utc)
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row(hour_local=1)]
    repository.properties[ACCOUNT_ID] = [_property_row()]
    scrape_job_service = FakeScrapeJobService()

    _service(repository, scrape_job_service).run_due_schedules(just_after_midnight)

    _, request = scrape_job_service.created[0]
    assert request.check_in == date(2026, 7, 13)  # 13 June (Athens) + 30 days


def test_run_due_schedules_uses_canonical_destination_when_raw_is_missing():
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row()]
    repository.properties[ACCOUNT_ID] = [_property_row(raw_destination=None)]
    scrape_job_service = FakeScrapeJobService()
    service = _service(repository, scrape_job_service)

    service.run_due_schedules(NOW)

    _, request = scrape_job_service.created[0]
    assert request.destination == "faliraki"


def test_run_due_schedules_touches_last_run_even_without_schedulable_properties():
    # A broken/empty account must not re-tick on every scheduler cycle.
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row()]
    service = _service(repository)

    created = service.run_due_schedules(NOW)

    assert created == 0
    assert repository.touches == [(CONFIG_ID, NOW)]
    assert repository.failure_calls == []


def test_run_due_schedules_skips_quota_errors_and_continues(caplog):
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row()]
    repository.properties[ACCOUNT_ID] = [
        _property_row(),
        _property_row(id=UUID("00000000-0000-0000-0000-000000000457")),
        _property_row(id=UUID("00000000-0000-0000-0000-000000000458")),
    ]
    scrape_job_service = FakeScrapeJobService(
        errors=[TooManyActiveJobsError("active cap"), DailyQuotaExceededError("daily cap"), None]
    )
    service = _service(repository, scrape_job_service)

    with caplog.at_level("WARNING", logger="api.services.schedule_service"):
        created = service.run_due_schedules(NOW)

    assert created == 1
    assert len(scrape_job_service.created) == 1
    # Quota refusals are expected operating conditions, not breaker failures.
    assert repository.failure_calls == []
    assert repository.touches == [(CONFIG_ID, NOW)]
    # Cap pressure must be visible to operators: per-skip warnings plus an
    # account summary with enqueued/skipped property counts.
    skip_warnings = [r for r in caplog.records if "skipped (quota)" in r.getMessage()]
    assert len(skip_warnings) == 2
    assert all(r.levelname == "WARNING" for r in skip_warnings)
    summary = next(r for r in caplog.records if "Schedule quota pressure" in r.getMessage())
    assert "properties_enqueued=1" in summary.getMessage()
    assert "properties_skipped=2" in summary.getMessage()


def test_run_due_schedules_does_not_warn_about_quota_pressure_without_skips(caplog):
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row()]
    repository.properties[ACCOUNT_ID] = [_property_row()]
    service = _service(repository, FakeScrapeJobService())

    with caplog.at_level("WARNING", logger="api.services.schedule_service"):
        service.run_due_schedules(NOW)

    assert not any("Schedule quota pressure" in r.getMessage() for r in caplog.records)


def test_run_due_schedules_enqueues_in_repository_rotation_order():
    # Fairness under the active-jobs cap lives in the repository: it returns
    # least-recently-scheduled properties first, and the service must enqueue
    # in EXACTLY that order so cap-truncated accounts rotate across cycles.
    rotated_ids = [
        UUID("00000000-0000-0000-0000-000000000458"),
        UUID("00000000-0000-0000-0000-000000000456"),
        UUID("00000000-0000-0000-0000-000000000457"),
    ]
    repository = FakeScheduleRepository()
    repository.enabled = [_config_row()]
    repository.properties[ACCOUNT_ID] = [_property_row(id=property_id) for property_id in rotated_ids]
    scrape_job_service = FakeScrapeJobService()
    service = _service(repository, scrape_job_service)

    service.run_due_schedules(NOW)

    created_ids = [request.owned_property_id for _, request in scrape_job_service.created]
    assert created_ids == rotated_ids


def test_run_due_schedules_records_failure_and_continues_on_unexpected_error():
    repository = FakeScheduleRepository()
    broken = _config_row()
    healthy = _config_row(
        id=UUID("00000000-0000-0000-0000-000000000998"),
        account_id=OTHER_ACCOUNT_ID,
    )
    repository.enabled = [broken, healthy]
    repository.properties[ACCOUNT_ID] = [_property_row()]
    repository.properties[OTHER_ACCOUNT_ID] = [_property_row()]
    scrape_job_service = FakeScrapeJobService(errors=[RuntimeError("db down"), None])
    service = _service(repository, scrape_job_service)

    created = service.run_due_schedules(NOW)

    # The broken account records a breaker failure; the healthy one still runs.
    assert repository.failure_calls == [ACCOUNT_ID]
    assert created == 1
    assert scrape_job_service.created[0][0] == OTHER_ACCOUNT_ID
    # Both due configs were ticked regardless of outcome.
    assert [config_id for config_id, _ in repository.touches] == [broken["id"], healthy["id"]]


# ----------------------------------------------------------------------------
# Circuit breaker
# ----------------------------------------------------------------------------


def test_record_scheduled_job_failure_increments_without_disabling_below_threshold():
    repository = FakeScheduleRepository()
    service = _service(repository)

    failures = service.record_scheduled_job_failure(ACCOUNT_ID)

    assert failures == 1
    assert repository.disable_calls == []


def test_record_scheduled_job_failure_disables_at_threshold():
    repository = FakeScheduleRepository()
    repository.consecutive_failures = FAILURE_DISABLE_THRESHOLD - 1
    service = _service(repository)

    failures = service.record_scheduled_job_failure(ACCOUNT_ID)

    assert failures == FAILURE_DISABLE_THRESHOLD
    assert repository.disable_calls == [ACCOUNT_ID]


def test_record_scheduled_job_success_resets_breaker():
    repository = FakeScheduleRepository()
    repository.consecutive_failures = 2
    service = _service(repository)

    service.record_scheduled_job_success(ACCOUNT_ID)

    assert repository.reset_calls == [ACCOUNT_ID]
    assert repository.consecutive_failures == 0


def test_breaker_disable_notifies_account_at_threshold():
    repository = FakeScheduleRepository()
    repository.consecutive_failures = FAILURE_DISABLE_THRESHOLD - 1
    notifier = FakeAlertNotifier()
    service = _service(repository, notifier=notifier)

    service.record_scheduled_job_failure(ACCOUNT_ID)

    assert repository.disable_calls == [ACCOUNT_ID]
    assert notifier.calls == [(ACCOUNT_ID, FAILURE_DISABLE_THRESHOLD)]


def test_breaker_does_not_notify_below_threshold():
    repository = FakeScheduleRepository()
    notifier = FakeAlertNotifier()
    service = _service(repository, notifier=notifier)

    service.record_scheduled_job_failure(ACCOUNT_ID)

    assert notifier.calls == []


def test_breaker_notify_failure_is_swallowed():
    repository = FakeScheduleRepository()
    repository.consecutive_failures = FAILURE_DISABLE_THRESHOLD - 1
    notifier = FakeAlertNotifier(raise_on_notify=True)
    service = _service(repository, notifier=notifier)

    # A failing notifier must not abort the breaker bookkeeping.
    failures = service.record_scheduled_job_failure(ACCOUNT_ID)

    assert failures == FAILURE_DISABLE_THRESHOLD
    assert repository.disable_calls == [ACCOUNT_ID]


def test_breaker_disable_without_notifier_still_works():
    repository = FakeScheduleRepository()
    repository.consecutive_failures = FAILURE_DISABLE_THRESHOLD - 1
    service = _service(repository, notifier=None)

    failures = service.record_scheduled_job_failure(ACCOUNT_ID)

    assert failures == FAILURE_DISABLE_THRESHOLD
    assert repository.disable_calls == [ACCOUNT_ID]
