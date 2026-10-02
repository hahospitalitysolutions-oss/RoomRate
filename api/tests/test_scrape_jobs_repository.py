from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from api.repositories.scrape_jobs_repository import (
    DailyQuotaExceededError,
    OwnedRoomUnionResult,
    ScrapeJobRepository,
    TooManyActiveJobsError,
    clean_owned_room_type_name,
)
from api.schemas.scrape_jobs import ScrapeJobCreate
from api.models.market import RoomRateScrapeJob
from api.tests._fakes import FakeConnection, FakeEngine, FakeResult, install_scripted_engine


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


def _patch_engine(monkeypatch, results: list[FakeResult] | None = None) -> FakeEngine:
    # results=None runs unscripted: every execute returns an empty result, for
    # tests that ignore result values and only assert on connection.calls.
    return install_scripted_engine(monkeypatch, "api.repositories.scrape_jobs_repository", results)


def _request() -> ScrapeJobCreate:
    return ScrapeJobCreate(
        destination="Rhodes",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
    )


def _inserted_job_row() -> dict:
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    return {
        "id": JOB_ID,
        "account_id": ACCOUNT_ID,
        "owned_property_id": None,
        "destination": "Rhodes",
        "raw_destination": "Rhodes",
        "canonical_destination": "rhodes",
        "check_in": date(2026, 7, 1),
        "check_out": date(2026, 7, 5),
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "job_type": "competitor_search",
        "room_type_category": None,
        "filters_payload": {},
        "status": "queued",
        "requested_at": now,
        "started_at": None,
        "finished_at": None,
        "error_message": None,
        "attempt_count": 0,
        "max_attempts": 3,
        "next_attempt_at": None,
    }


def test_create_job_rejects_when_too_many_active_jobs(monkeypatch):
    _patch_engine(
        monkeypatch,
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 2, "daily_jobs": 3}]),
        ],
    )
    repository = ScrapeJobRepository(max_concurrent_jobs_per_account=2, max_daily_jobs_per_account=20)

    with pytest.raises(TooManyActiveJobsError):
        repository.create_job(ACCOUNT_ID, _request())


def test_create_job_rejects_when_daily_quota_exceeded(monkeypatch):
    _patch_engine(
        monkeypatch,
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 0, "daily_jobs": 20}]),
        ],
    )
    repository = ScrapeJobRepository(max_concurrent_jobs_per_account=2, max_daily_jobs_per_account=20)

    with pytest.raises(DailyQuotaExceededError):
        repository.create_job(ACCOUNT_ID, _request())


def test_create_job_inserts_when_under_limits(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 1, "daily_jobs": 5}]),
            FakeResult(rows=[_inserted_job_row()]),
        ],
    )
    repository = ScrapeJobRepository(max_concurrent_jobs_per_account=2, max_daily_jobs_per_account=20)

    row = repository.create_job(ACCOUNT_ID, _request())

    assert row["id"] == JOB_ID
    assert row["scrape_runs_count"] == 0
    # Limit check and insert must run inside one transaction.
    assert engine.begin_calls == 1
    limits_sql = engine.connection.calls[1][0]
    assert "status IN ('queued', 'running')" in limits_sql
    assert "date_trunc('day', now())" in limits_sql


def test_create_job_takes_per_account_advisory_lock_before_counting(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 0, "daily_jobs": 0}]),
            FakeResult(rows=[_inserted_job_row()]),
        ],
    )
    repository = ScrapeJobRepository(max_concurrent_jobs_per_account=2, max_daily_jobs_per_account=20)

    repository.create_job(ACCOUNT_ID, _request())

    # The advisory lock must be the FIRST statement so concurrent create_job
    # calls for the same account serialize before counting active/daily jobs.
    lock_sql, lock_params = engine.connection.calls[0]
    assert "pg_advisory_xact_lock" in lock_sql
    assert "hashtext" in lock_sql
    assert lock_params["account_id"] == ACCOUNT_ID
    count_sql = engine.connection.calls[1][0]
    assert "count(*)" in count_sql
    assert "pg_advisory_xact_lock" not in count_sql


def test_create_job_maps_duplicate_active_market_to_quota_error(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    class DuplicateRaisingConnection(FakeConnection):
        def execute(self, statement, params=None):
            result = super().execute(statement, params)
            if "INSERT INTO roomrate_scrape_jobs" in str(statement):
                raise IntegrityError(
                    "INSERT ...",
                    {},
                    Exception('duplicate key value violates unique constraint "uq_roomrate_scrape_jobs_active_market"'),
                )
            return result

    connection = DuplicateRaisingConnection(
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 0, "daily_jobs": 0}]),
            FakeResult(),
        ]
    )
    engine = FakeEngine(connection)
    monkeypatch.setattr("api.repositories.scrape_jobs_repository.get_engine", lambda: engine)
    repository = ScrapeJobRepository()

    with pytest.raises(TooManyActiveJobsError, match="already queued or running"):
        repository.create_job(ACCOUNT_ID, _request())


def test_claim_job_returns_true_when_queued_job_is_claimed(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[{"id": JOB_ID}])])
    repository = ScrapeJobRepository()

    claimed = repository.claim_job(ACCOUNT_ID, JOB_ID, claimed_by="host:1234")

    assert claimed is True
    sql, params = engine.connection.calls[0]
    assert "status = 'queued'" in sql
    assert "RETURNING id" in sql
    assert "heartbeat_at" in sql
    assert "attempt_count = attempt_count + 1" in sql
    assert params["claimed_by"] == "host:1234"


def test_claim_job_returns_false_when_already_claimed(monkeypatch):
    _patch_engine(monkeypatch, [FakeResult(rows=[])])
    repository = ScrapeJobRepository()

    assert repository.claim_job(ACCOUNT_ID, JOB_ID, claimed_by="host:1234") is False


def test_heartbeat_touches_running_job(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])
    repository = ScrapeJobRepository()

    repository.heartbeat(ACCOUNT_ID, JOB_ID)

    sql, params = engine.connection.calls[0]
    assert "heartbeat_at = now()" in sql
    assert "status = 'running'" in sql
    assert params["job_id"] == JOB_ID


def test_complete_job_refreshes_room_types_and_completes_in_one_transaction(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(rowcount=3),  # room-type catalog sync insert
            FakeResult(rowcount=1),  # status update to completed
        ],
    )
    repository = ScrapeJobRepository()

    completed, discovered = repository.complete_job(
        ACCOUNT_ID, JOB_ID, refresh_owned_property_id=OWNED_PROPERTY_ID
    )

    assert completed is True
    assert discovered == 3
    assert engine.begin_calls == 1
    executed_sql = " ".join(sql for sql, _ in engine.connection.calls)
    assert "roomrate_owned_property_room_types" in executed_sql
    assert "status = 'completed'" in executed_sql
    # Exactly two statements: the catalog sync and the status transition. A
    # third one creeping back in between them is how the baseline room used
    # to get auto-selected behind the user's back.
    assert len(engine.connection.calls) == 2


def test_complete_job_discovery_refresh_does_not_select_a_room(monkeypatch):
    """Discovery completion records what it found; it never picks the baseline.

    The baseline room is the user's explicit choice, made in step 2 of the
    setup wizard through PUT /owned-property/{id}/selected-room-type. When
    this path wrote it too, the account looked fully configured the moment
    discovery finished and the wizard's guard waved the user straight past
    steps 2-3.

    Unscripted engine (``results=None``): every execute returns an empty
    result, so the assertions read the recorded SQL instead of return values.
    """
    engine = _patch_engine(monkeypatch, None)
    repository = ScrapeJobRepository()

    repository.complete_job(ACCOUNT_ID, JOB_ID, refresh_owned_property_id=OWNED_PROPERTY_ID)

    executed_sql = " ".join(sql for sql, _ in engine.connection.calls)
    # The catalog sync stays: step 2 can only offer rooms discovery found.
    assert "INSERT INTO roomrate_owned_property_room_types" in executed_sql
    assert "ON CONFLICT" in executed_sql
    assert "is_active = true" in executed_sql
    # Nothing on this path may touch the owned property's selection.
    assert "selected_room_type_category" not in executed_sql
    assert "roomrate_owned_properties" not in executed_sql


def test_complete_job_without_refresh_only_marks_completed(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])
    repository = ScrapeJobRepository()

    completed, discovered = repository.complete_job(ACCOUNT_ID, JOB_ID)

    assert completed is True
    assert discovered == 0
    assert engine.begin_calls == 1
    sql, _ = engine.connection.calls[0]
    assert "status = 'completed'" in sql
    # The transition is fenced so a swept (failed) job can't be resurrected.
    assert "AND status = 'running'" in sql
    assert "roomrate_owned_property_room_types" not in sql


def test_complete_job_persists_result_summary_in_the_fenced_transaction(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])
    repository = ScrapeJobRepository()
    summary = {
        "version": 1,
        "rows_seen": 4,
        "filter_counts": {"room_type_category": {"before": 4, "after": 0}},
        "rows_written": 0,
    }

    completed, _ = repository.complete_job(ACCOUNT_ID, JOB_ID, result_summary=summary)

    assert completed is True
    assert engine.begin_calls == 1
    sql, params = engine.connection.calls[0]
    assert "result_summary = :result_summary" in sql
    assert "AND status = 'running'" in sql
    assert params["result_summary"] == summary


def test_scrape_job_model_declares_nullable_jsonb_result_summary():
    column = RoomRateScrapeJob.__table__.c.result_summary

    assert column.nullable is True
    assert column.type.__class__.__name__ == "JSONB"


def test_complete_job_reports_when_job_was_not_running(monkeypatch):
    # rowcount 0: the sweeper already moved the job out of 'running'.
    _patch_engine(monkeypatch, [FakeResult(rowcount=0)])
    repository = ScrapeJobRepository()

    completed, discovered = repository.complete_job(ACCOUNT_ID, JOB_ID)

    assert completed is False
    assert discovered == 0


def test_mark_failed_is_fenced_on_running_status_and_truncates_error(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rowcount=1)])
    repository = ScrapeJobRepository()

    updated = repository.mark_failed(ACCOUNT_ID, JOB_ID, "boom " * 1000)

    assert updated is True
    sql, params = engine.connection.calls[0]
    assert "status = 'failed'" in sql
    assert "AND status = 'running'" in sql
    assert len(params["error_message"]) == 2_000


def test_mark_failed_reports_when_job_was_not_running(monkeypatch):
    _patch_engine(monkeypatch, [FakeResult(rowcount=0)])
    repository = ScrapeJobRepository()

    assert repository.mark_failed(ACCOUNT_ID, JOB_ID, "boom") is False


def test_retry_or_fail_requeues_running_job_with_delay(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[{"status": "queued"}])])
    repository = ScrapeJobRepository()

    status = repository.retry_or_fail(ACCOUNT_ID, JOB_ID, "provider unavailable", 45.5)

    assert status == "queued"
    sql, params = engine.connection.calls[0]
    assert "attempt_count < max_attempts" in sql
    assert "make_interval" in sql
    assert "AND status = 'running'" in sql
    assert params["retry_delay_seconds"] == 45.5


def test_list_queued_jobs_returns_oldest_first_across_accounts(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(
                rows=[
                    {"account_id": ACCOUNT_ID, "id": JOB_ID, "job_type": "competitor_search"},
                ]
            )
        ],
    )
    repository = ScrapeJobRepository()

    jobs = repository.list_queued_jobs(limit=3)

    assert jobs == [{"account_id": ACCOUNT_ID, "id": JOB_ID, "job_type": "competitor_search"}]
    sql, params = engine.connection.calls[0]
    # FIFO safety-net executor: oldest queued jobs first, NOT account scoped.
    assert "status = 'queued'" in sql
    assert "ORDER BY requested_at" in sql
    assert "account_id = :account_id" not in sql
    assert params["limit"] == 3


def test_fail_stale_jobs_sweeps_running_jobs_with_lost_heartbeat(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [FakeResult(rows=[{"id": JOB_ID}, {"id": OWNED_PROPERTY_ID}])],
    )
    repository = ScrapeJobRepository()

    swept = repository.fail_stale_jobs(stale_after_minutes=5)

    assert swept == 2
    sql, params = engine.connection.calls[0]
    assert "attempt_count < max_attempts" in sql
    assert "heartbeat lost (sweeper)" in sql
    assert "next_attempt_at" in sql
    assert "status = 'running'" in sql
    assert "heartbeat_at < now() - make_interval" in sql
    assert "RETURNING id" in sql
    assert params["stale_after_minutes"] == 5
    assert params["retry_base_seconds"] == 30
    assert params["retry_max_seconds"] == 900


def test_create_job_persists_nearby_destinations_and_radius(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 0, "daily_jobs": 0}]),
            FakeResult(rows=[{**_inserted_job_row(), "nearby_destinations": ["Ιξιά"], "radius_km": 10.0}]),
        ],
    )
    request = ScrapeJobCreate(
        destination="Φαληράκι",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        nearby_destinations=["Ιξιά"],
        radius_km=10,
    )

    row = ScrapeJobRepository().create_job(ACCOUNT_ID, request)

    insert_sql, params = engine.connection.calls[2]
    assert "nearby_destinations" in insert_sql and "radius_km" in insert_sql
    assert params["nearby_destinations"] == ["Ιξιά"]
    assert params["radius_km"] == 10.0
    assert row["nearby_destinations"] == ["Ιξιά"]


def test_job_reads_select_the_round6_columns(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[])])

    assert ScrapeJobRepository().get_job(ACCOUNT_ID, JOB_ID) is None

    sql, _ = engine.connection.calls[0]
    assert "j.nearby_destinations" in sql and "j.radius_km" in sql


# ----------------------------------------------------------------------------
# Round 6: a new, failed or requeued attempt never shows the previous
# attempt's result_summary.progress (e.g. «deep_crawl 96/99»)
# ----------------------------------------------------------------------------

DROP_PROGRESS_SQL = (
    "result_summary = CASE WHEN result_summary IS NULL THEN NULL ELSE result_summary - 'progress' END"
)


@pytest.mark.parametrize(
    "result, transition",
    [
        (FakeResult(rows=[{"id": JOB_ID}]), lambda repository: repository.claim_job(ACCOUNT_ID, JOB_ID, "host:1")),
        (
            FakeResult(rows=[{"status": "queued"}]),
            lambda repository: repository.retry_or_fail(ACCOUNT_ID, JOB_ID, "provider unavailable", 30.0),
        ),
        (FakeResult(rowcount=1), lambda repository: repository.mark_failed(ACCOUNT_ID, JOB_ID, "boom")),
        # The sweeper is the fourth way a running job ends up queued or failed.
        (FakeResult(rows=[{"id": JOB_ID}]), lambda repository: repository.fail_stale_jobs(stale_after_minutes=5)),
    ],
    ids=["claim_job", "retry_or_fail", "mark_failed", "fail_stale_jobs"],
)
def test_attempt_transitions_drop_the_stale_progress_and_keep_other_summary_keys(monkeypatch, result, transition):
    engine = _patch_engine(monkeypatch, [result])

    transition(ScrapeJobRepository())

    sql = " ".join(engine.connection.calls[0][0].split())
    assert DROP_PROGRESS_SQL in sql
    # Only the progress key goes; the summary itself is never nulled or replaced.
    assert "result_summary = NULL" not in sql
    assert "result_summary = :result_summary" not in sql


# ---------------------------------------------------------------------------
# Owned-room union: the owner's room types from ANY completed run
# ---------------------------------------------------------------------------

REA_TWIN = "Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια, Ντους και Μπαλκόνι"
REA_DOUBLE = "Δίκλινο Δωμάτιο με Ντους και Μπαλκόνι"
REA_SINGLE = "Μονόκλινο Δωμάτιο με Ντους και Μπαλκόνι"
EARLIER_JOB_ID = UUID("00000000-0000-0000-0000-000000000701")


def _package(room_type: str, price: str, job_id: UUID = JOB_ID, meals: str | None = "Breakfast") -> dict:
    """One owner-hotel package row as the candidates SELECT returns it."""
    return {
        "owned_property_id": OWNED_PROPERTY_ID,
        "run_job_id": job_id,
        "room_type": room_type,
        "meals": meals,
        "free_cancellation": "Free cancellation",
        "facilities": "Balcony|Shower",
        "price_per_night_eur": Decimal(price),
    }


def _existing(room_type: str) -> dict:
    return {"owned_property_id": OWNED_PROPERTY_ID, "room_type": room_type}


def _inserted_rooms(connection: FakeConnection) -> list[dict]:
    return [params for sql, params in connection.calls if "INSERT INTO roomrate_owned_property_room_types" in sql]


def test_union_from_competitor_run_adds_double_and_single_without_touching_twin_or_selection():
    # Live shape: discovery stored only the twin; a competitor run's packages
    # for the owner's own hotel show all three rooms.
    connection = FakeConnection(
        [
            FakeResult(rows=[
                _package(REA_TWIN, "60.00"),
                _package(REA_DOUBLE, "55.00"),
                _package(REA_SINGLE, "40.00"),
                _package(REA_TWIN, "72.00"),
            ]),
            FakeResult(rows=[_existing(REA_TWIN)]),  # the catalog as discovery left it
            FakeResult(rows=[{"id": UUID(int=1)}]),  # insert double
            FakeResult(rows=[{"id": UUID(int=2)}]),  # insert single
        ]
    )

    result = ScrapeJobRepository.union_owned_room_types(connection, account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert result == OwnedRoomUnionResult(candidates=3, inserted=2)
    inserted = _inserted_rooms(connection)
    assert [(room["room_type"], room["room_type_category"]) for room in inserted] == [
        (REA_DOUBLE, "double"),
        (REA_SINGLE, "single"),
    ]
    assert all(room["account_id"] == ACCOUNT_ID for room in inserted)
    assert all(room["owned_property_id"] == OWNED_PROPERTY_ID for room in inserted)
    assert all(room["first_seen_job_id"] == JOB_ID for room in inserted)
    assert inserted[1]["sample_price_per_night_eur"] == Decimal("40.00")
    assert inserted[1]["sample_meals"] == "Breakfast"
    assert inserted[1]["sample_facilities"] == "Balcony|Shower"

    candidates_sql, candidates_params = connection.calls[0]
    # Same name equality the competitor view uses to EXCLUDE the owner's hotel, account-scoped.
    assert "lower(trim(op.display_name)) = lower(trim(p.display_name))" in candidates_sql
    assert "op.account_id = sr.account_id" in candidates_sql
    assert "op.is_active = true" in candidates_sql
    assert "sr.account_id = :account_id" in candidates_sql
    assert "sr.scrape_job_id = :job_id" in candidates_sql
    assert candidates_params == {"account_id": ACCOUNT_ID, "job_id": JOB_ID}

    all_sql = " ".join(sql for sql, _ in connection.calls)
    # Union only: the twin row and the owner's selection are never written.
    assert "selected_room_type_category" not in all_sql
    assert "UPDATE" not in all_sql
    assert "is_active = false" not in all_sql
    assert "DELETE" not in all_sql
    insert_sql = " ".join(connection.calls[2][0].split())
    assert "ON CONFLICT ( account_id, owned_property_id, room_type, room_type_category ) DO NOTHING" in insert_sql


@pytest.mark.parametrize(
    "raw, cleaned",
    [
        (f"{REA_DOUBLE} 1/11", REA_DOUBLE),
        (f"  {REA_DOUBLE}   2/11  ", REA_DOUBLE),
        (REA_TWIN, REA_TWIN),  # «2 Μονά» keeps its digit
        ("Suite 1/2 Bath View", "Suite 1/2 Bath View"),  # not trailing
        ("Room1/11", "Room1/11"),  # no separating space
        (None, ""),
    ],
)
def test_clean_owned_room_type_name_strips_only_a_trailing_counter(raw, cleaned):
    assert clean_owned_room_type_name(raw) == cleaned


def test_union_strips_the_counter_artifact_and_deduplicates():
    connection = FakeConnection(
        [
            FakeResult(rows=[
                _package(f"{REA_DOUBLE} 1/11", "50.00"),
                _package(f"{REA_DOUBLE} 2/11", "45.00", meals="Room only"),
                _package(REA_DOUBLE, "48.00"),
                # Already in the catalog under its artifact name: no clean duplicate.
                _package(REA_TWIN, "60.00"),
            ]),
            FakeResult(rows=[_existing(f"{REA_TWIN} 1/11")]),
            FakeResult(rows=[{"id": UUID(int=1)}]),
        ]
    )

    result = ScrapeJobRepository.union_owned_room_types(connection, account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert result == OwnedRoomUnionResult(candidates=2, inserted=1)
    (double,) = _inserted_rooms(connection)
    assert double["room_type"] == REA_DOUBLE
    assert double["room_type_category"] == "double"
    # Samples come from the cheapest package, like the discovery upsert.
    assert double["sample_price_per_night_eur"] == Decimal("45.00")
    assert double["sample_meals"] == "Room only"


def test_union_rerun_is_idempotent():
    # Second pass: the catalog already holds all three (one under an artifact name).
    connection = FakeConnection(
        [
            FakeResult(rows=[
                _package(REA_TWIN, "60.00"),
                _package(REA_DOUBLE, "55.00"),
                _package(f"{REA_SINGLE} 3/11", "40.00"),
            ]),
            FakeResult(rows=[_existing(REA_TWIN), _existing(REA_DOUBLE), _existing(f"{REA_SINGLE} 1/11")]),
        ]
    )

    result = ScrapeJobRepository.union_owned_room_types(connection, account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert result == OwnedRoomUnionResult(candidates=3, inserted=0)
    assert _inserted_rooms(connection) == []
    assert len(connection.calls) == 2


def test_union_counts_only_rows_actually_inserted():
    # A concurrent writer got there first: DO NOTHING returns no id.
    connection = FakeConnection(
        [
            FakeResult(rows=[_package(REA_SINGLE, "40.00")]),
            FakeResult(rows=[]),
            FakeResult(rows=[]),
        ]
    )

    result = ScrapeJobRepository.union_owned_room_types(connection, account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert result == OwnedRoomUnionResult(candidates=1, inserted=0)


def test_union_job_without_the_owners_hotel_adds_nothing():
    connection = FakeConnection([FakeResult(rows=[])])

    result = ScrapeJobRepository.union_owned_room_types(connection, account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert result == OwnedRoomUnionResult(candidates=0, inserted=0)
    # No catalog read, no insert: only the candidates SELECT ran.
    assert len(connection.calls) == 1


def test_union_dry_run_counts_what_would_be_added_without_writing():
    connection = FakeConnection(
        [
            FakeResult(rows=[_package(REA_TWIN, "60.00"), _package(REA_DOUBLE, "55.00"), _package(REA_SINGLE, "40.00")]),
            FakeResult(rows=[_existing(REA_TWIN)]),
        ]
    )

    result = ScrapeJobRepository.union_owned_room_types(
        connection, account_id=ACCOUNT_ID, job_id=None, dry_run=True
    )

    assert result == OwnedRoomUnionResult(candidates=3, inserted=2)
    assert _inserted_rooms(connection) == []


def test_union_history_scope_scans_completed_runs_and_keeps_the_first_seen_job():
    connection = FakeConnection(
        [
            FakeResult(rows=[
                _package(REA_SINGLE, "44.00", job_id=EARLIER_JOB_ID),  # oldest run first
                _package(REA_SINGLE, "39.00", job_id=JOB_ID),
            ]),
            FakeResult(rows=[]),
            FakeResult(rows=[{"id": UUID(int=1)}]),
        ]
    )

    result = ScrapeJobRepository.union_owned_room_types(connection, account_id=ACCOUNT_ID)

    assert result == OwnedRoomUnionResult(candidates=1, inserted=1)
    candidates_sql, candidates_params = connection.calls[0]
    assert "sr.status = 'completed'" in candidates_sql
    assert "sr.scrape_job_id = :job_id" not in candidates_sql
    assert "ORDER BY sr.created_at ASC" in candidates_sql
    assert candidates_params == {"account_id": ACCOUNT_ID}
    (single,) = _inserted_rooms(connection)
    assert single["first_seen_job_id"] == EARLIER_JOB_ID
    assert single["sample_price_per_night_eur"] == Decimal("39.00")


def test_union_owned_room_types_from_job_runs_in_its_own_transaction(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[])])

    added = ScrapeJobRepository().union_owned_room_types_from_job(ACCOUNT_ID, JOB_ID)

    assert added == 0
    assert engine.begin_calls == 1
    assert engine.connection.calls[0][1] == {"account_id": ACCOUNT_ID, "job_id": JOB_ID}
