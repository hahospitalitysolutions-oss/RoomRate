from datetime import datetime, timezone
from uuid import UUID

from api.repositories.schedule_repository import ScheduleRepository
from api.tests._fakes import FakeEngine, FakeResult, install_scripted_engine


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
CONFIG_ID = UUID("00000000-0000-0000-0000-000000000999")
NOW = datetime(2026, 6, 13, 6, 0, tzinfo=timezone.utc)


def _patch_engine(monkeypatch, results: list[FakeResult]) -> FakeEngine:
    return install_scripted_engine(monkeypatch, "api.repositories.schedule_repository", results)


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
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def test_get_config_returns_account_scoped_row(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[_config_row()])])
    repository = ScheduleRepository()

    row = repository.get_config(ACCOUNT_ID)

    assert row["account_id"] == ACCOUNT_ID
    sql, params = engine.connection.calls[0]
    assert "FROM roomrate_schedule_configs" in sql
    assert params["account_id"] == ACCOUNT_ID


def test_get_config_returns_none_when_missing(monkeypatch):
    _patch_engine(monkeypatch, [FakeResult(rows=[])])

    assert ScheduleRepository().get_config(ACCOUNT_ID) is None


def test_upsert_config_inserts_with_conflict_update_on_account(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[_config_row(hour_local=10, hour_utc=7)])])
    repository = ScheduleRepository()

    row = repository.upsert_config(
        ACCOUNT_ID,
        {
            "enabled": True,
            "frequency_hours": 24,
            "hour_local": 10,
            "hour_utc": 7,
            "lead_days": 30,
            "nights": 3,
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "consecutive_failures": 0,
        },
    )

    assert row["hour_local"] == 10
    sql, params = engine.connection.calls[0]
    assert "INSERT INTO roomrate_schedule_configs" in sql
    assert "ON CONFLICT (account_id) DO UPDATE" in sql
    assert "RETURNING" in sql
    assert params["account_id"] == ACCOUNT_ID
    assert params["hour_local"] == 10
    assert "hour_local = EXCLUDED.hour_local" in sql
    # Kept current for an older API image (migration 20260930_0030).
    assert params["hour_utc"] == 7
    assert "hour_utc = EXCLUDED.hour_utc" in sql
    # The zone is not editable yet: new rows take the column default.
    assert "timezone" not in params
    # New rows need a client-generated primary key.
    assert params["id"] is not None
    # A PUT carrying a stale read must not clobber breaker updates recorded
    # concurrently by the executor: the conflict UPDATE never touches the
    # breaker column (the enable-transition reset is a separate statement).
    assert "consecutive_failures = EXCLUDED.consecutive_failures" not in sql


def test_list_enabled_configs_reads_every_enabled_schedule_least_recent_first(monkeypatch):
    """Due-ness depends on each schedule's zone and calendar: the service decides."""
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[_config_row()])])
    repository = ScheduleRepository()

    enabled = repository.list_enabled_configs()

    assert enabled == [_config_row()]
    sql, _params = engine.connection.calls[0]
    assert "WHERE enabled = true" in sql
    assert "ORDER BY last_run_at ASC NULLS FIRST" in sql
    assert "hour_local, timezone" in sql
    # No due rule in SQL any more: no UTC hour, no frequency arithmetic.
    assert "extract(hour" not in sql
    assert "frequency_hours * interval" not in sql


def test_touch_last_run_updates_one_config(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])
    repository = ScheduleRepository()

    repository.touch_last_run(CONFIG_ID, NOW)

    sql, params = engine.connection.calls[0]
    assert "SET last_run_at = :now" in sql
    assert params["config_id"] == CONFIG_ID
    assert params["now"] == NOW


def test_record_failure_increments_and_returns_new_count(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[{"consecutive_failures": 3}])])
    repository = ScheduleRepository()

    failures = repository.record_failure(ACCOUNT_ID)

    assert failures == 3
    sql, _ = engine.connection.calls[0]
    assert "consecutive_failures = consecutive_failures + 1" in sql
    assert "RETURNING consecutive_failures" in sql


def test_record_failure_returns_zero_when_no_config_exists(monkeypatch):
    _patch_engine(monkeypatch, [FakeResult(rows=[])])

    assert ScheduleRepository().record_failure(ACCOUNT_ID) == 0


def test_reset_failures_zeroes_the_breaker(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])

    ScheduleRepository().reset_failures(ACCOUNT_ID)

    sql, params = engine.connection.calls[0]
    assert "consecutive_failures = 0" in sql
    assert params["account_id"] == ACCOUNT_ID


def test_disable_turns_schedule_off(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])

    ScheduleRepository().disable(ACCOUNT_ID)

    sql, params = engine.connection.calls[0]
    assert "enabled = false" in sql
    assert params["account_id"] == ACCOUNT_ID


def test_list_schedulable_properties_requires_category_and_active_tracked_competitors(monkeypatch):
    property_id = UUID("00000000-0000-0000-0000-000000000456")
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(
                rows=[
                    {
                        "id": property_id,
                        "raw_destination": "Φαληράκι",
                        "canonical_destination": "faliraki",
                        "selected_room_type_category": "double",
                    }
                ]
            )
        ],
    )
    repository = ScheduleRepository()

    properties = repository.list_schedulable_properties(ACCOUNT_ID)

    assert properties[0]["id"] == property_id
    sql, params = engine.connection.calls[0]
    assert "selected_room_type_category IS NOT NULL" in sql
    assert "is_active = true" in sql
    assert "roomrate_tracked_competitors" in sql
    assert "tc.room_type_category = op.selected_room_type_category" in sql
    assert params["account_id"] == ACCOUNT_ID


def test_list_schedulable_properties_orders_by_least_recently_scheduled(monkeypatch):
    # With the per-account active-jobs cap, ordering by created_at would
    # enqueue the SAME first properties every cycle and starve the rest.
    # Rotation: never-scheduled first, then oldest scheduled scrape, with
    # created_at only as the tiebreak.
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[])])

    ScheduleRepository().list_schedulable_properties(ACCOUNT_ID)

    sql, _ = engine.connection.calls[0]
    order_by = sql[sql.index("ORDER BY"):]
    assert "max(sj.requested_at)" in order_by
    assert "FROM roomrate_scrape_jobs sj" in order_by
    assert "sj.owned_property_id = op.id" in order_by
    # Only scheduler-enqueued jobs count toward rotation; manual jobs must not
    # push a property to the back of the line.
    assert "sj.scheduled" in order_by
    assert "ASC NULLS FIRST" in order_by
    # created_at is the tiebreak, AFTER the rotation key.
    assert order_by.index("max(sj.requested_at)") < order_by.index("op.created_at")
