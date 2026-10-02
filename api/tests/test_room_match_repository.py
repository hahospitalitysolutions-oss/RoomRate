"""SQL-shape tests for room-match rows, the run audit and the run quota."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from api.repositories import room_match_repository as match_module
from api.repositories.room_match_repository import RoomMatchRepository


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
ROOM_TYPE_ID = UUID("00000000-0000-0000-0000-000000000abc")
PROPERTY_ID = UUID("00000000-0000-0000-0000-0000000000f1")


class FakeResult:
    def __init__(self, *, scalar_value=None, rows=None):
        self.scalar_value = scalar_value
        self.rows = rows or []

    def scalar(self):
        return self.scalar_value

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        return self.results.pop(0)


class FakeEngine:
    def __init__(self, results):
        self.connection = FakeConnection(results)
        self.begin_calls = 0

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        self.begin_calls += 1
        yield self.connection


def _install(monkeypatch, results) -> FakeEngine:
    engine = FakeEngine(results)
    monkeypatch.setattr(match_module, "get_engine", lambda role="api": engine)
    return engine


def test_count_runs_today_is_account_scoped_to_matching_runs_in_utc(monkeypatch):
    engine = _install(monkeypatch, [FakeResult(scalar_value=4)])

    count = RoomMatchRepository().count_runs_today(ACCOUNT_ID)

    sql, params = engine.connection.executed[0]
    assert count == 4
    assert "roomrate_agent_runs" in sql
    assert "account_id = :account_id" in sql
    assert "kind = 'room_matching'" in sql
    # Skips make no LLM call, so they never burn the daily quota.
    assert "status <> 'skipped'" in sql
    assert "UTC" in sql
    assert params["account_id"] == ACCOUNT_ID


def test_has_matches_checks_the_job_and_owned_room_scope(monkeypatch):
    engine = _install(monkeypatch, [FakeResult(scalar_value=1)])

    assert RoomMatchRepository().has_matches(ACCOUNT_ID, JOB_ID, ROOM_TYPE_ID) is True
    sql, params = engine.connection.executed[0]
    assert "roomrate_room_matches" in sql
    assert params == {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "owned_room_type_id": ROOM_TYPE_ID,
    }


def test_has_matches_false_when_no_row_exists(monkeypatch):
    _install(monkeypatch, [FakeResult(scalar_value=None)])

    assert RoomMatchRepository().has_matches(ACCOUNT_ID, JOB_ID, ROOM_TYPE_ID) is False


def test_replace_matches_deletes_then_inserts_in_one_transaction(monkeypatch):
    engine = _install(monkeypatch, [FakeResult(), FakeResult()])

    written = RoomMatchRepository().replace_matches(
        account_id=ACCOUNT_ID,
        scrape_job_id=JOB_ID,
        owned_room_type_id=ROOM_TYPE_ID,
        model_version="claude-sonnet-5-5",
        matches=[
            {
                "property_id": PROPERTY_ID,
                "room_type": "Double Room",
                "score": 85,
                "category_match": "same",
                "reasoning": "Ίδια κατηγορία και χωρητικότητα.",
            }
        ],
    )

    assert written == 1
    # DELETE + INSERT run inside ONE begin() so a crash between the two can
    # never leave the (job, room) scope half-replaced.
    assert engine.begin_calls == 1
    delete_sql, delete_params = engine.connection.executed[0]
    assert "DELETE FROM roomrate_room_matches" in delete_sql
    assert delete_params == {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "owned_room_type_id": ROOM_TYPE_ID,
    }
    insert_sql, insert_rows = engine.connection.executed[1]
    assert "INSERT INTO roomrate_room_matches" in insert_sql
    assert isinstance(insert_rows, list) and len(insert_rows) == 1
    row = insert_rows[0]
    assert row["account_id"] == ACCOUNT_ID
    assert row["property_id"] == PROPERTY_ID
    assert row["room_type"] == "Double Room"
    assert row["score"] == 85
    assert row["category_match"] == "same"
    assert row["model_version"] == "claude-sonnet-5-5"
    assert row["id"]  # each row gets its own uuid


def test_replace_matches_with_no_rows_only_clears_the_scope(monkeypatch):
    engine = _install(monkeypatch, [FakeResult()])

    written = RoomMatchRepository().replace_matches(
        account_id=ACCOUNT_ID,
        scrape_job_id=JOB_ID,
        owned_room_type_id=ROOM_TYPE_ID,
        model_version="claude-sonnet-5-5",
        matches=[],
    )

    assert written == 0
    assert len(engine.connection.executed) == 1
    assert "DELETE FROM roomrate_room_matches" in engine.connection.executed[0][0]


def test_insert_run_records_status_model_and_error(monkeypatch):
    engine = _install(monkeypatch, [FakeResult()])

    RoomMatchRepository().insert_run(
        account_id=ACCOUNT_ID,
        scrape_job_id=JOB_ID,
        status="error",
        model="claude-sonnet-5-5",
        error_message="timeout",
    )

    sql, params = engine.connection.executed[0]
    assert "INSERT INTO roomrate_agent_runs" in sql
    assert "'room_matching'" in sql
    assert params["account_id"] == ACCOUNT_ID
    assert params["scrape_job_id"] == JOB_ID
    assert params["status"] == "error"
    assert params["model"] == "claude-sonnet-5-5"
    assert params["error_message"] == "timeout"


def test_fetch_matches_returns_scoped_rows(monkeypatch):
    stored = {
        "property_id": PROPERTY_ID,
        "room_type": "Double Room",
        "score": 85.0,
        "category_match": "same",
        "reasoning": "Ίδια κατηγορία.",
        "model_version": "claude-sonnet-5-5",
    }
    engine = _install(monkeypatch, [FakeResult(rows=[stored])])

    rows = RoomMatchRepository().fetch_matches(ACCOUNT_ID, JOB_ID, ROOM_TYPE_ID)

    assert rows == [stored]
    sql, params = engine.connection.executed[0]
    assert "FROM roomrate_room_matches" in sql
    assert params == {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "owned_room_type_id": ROOM_TYPE_ID,
    }


def test_replace_matches_persists_the_comparable_verdict_and_reads_it_back(monkeypatch):
    engine = _install(monkeypatch, [FakeResult(), FakeResult()])

    RoomMatchRepository().replace_matches(
        account_id=ACCOUNT_ID,
        scrape_job_id=JOB_ID,
        owned_room_type_id=ROOM_TYPE_ID,
        model_version="claude-sonnet-5-5",
        matches=[
            {
                "property_id": PROPERTY_ID,
                "room_type": "Junior Suite",
                "score": 85,
                "category_match": "similar",
                "reasoning": "Σουίτα, όχι υποκατάστατο.",
                "comparable": False,
            }
        ],
    )

    insert_sql, insert_rows = engine.connection.executed[1]
    assert ":comparable" in insert_sql
    assert insert_rows[0]["comparable"] is False

    read_engine = _install(monkeypatch, [FakeResult(rows=[])])
    RoomMatchRepository().fetch_matches(ACCOUNT_ID, JOB_ID, ROOM_TYPE_ID)
    select_sql, _ = read_engine.connection.executed[0]
    assert "comparable" in select_sql
