"""ProgressReporter: best-effort result_summary.progress writer (spec §3.5)."""

import json
from uuid import UUID

import pytest

from scraper.progress import ProgressReporter

JOB_ID = UUID("00000000-0000-0000-0000-000000000777")


class _RecordingEngine:
    """engine.begin() context manager that records statements (or raises)."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.executed: list[tuple[str, dict]] = []

    def begin(self):
        return self

    def __enter__(self):
        if self.error:
            raise self.error
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), params or {}))
        return self


def _progress(engine: _RecordingEngine) -> dict:
    return json.loads(engine.executed[-1][1]["progress"])


def test_update_merges_progress_into_the_running_job_row():
    engine = _RecordingEngine()
    reporter = ProgressReporter(engine, JOB_ID)

    assert reporter.update("scout", done=0, total=5, destinations_total=5, hotels_found=0) is True

    sql, params = engine.executed[0]
    assert "COALESCE(result_summary, '{}'::jsonb) || jsonb_build_object('progress', CAST(:progress AS jsonb))" in sql
    assert "WHERE id = :job_id AND status = 'running'" in sql
    assert params["job_id"] == JOB_ID
    snapshot = _progress(engine)
    assert snapshot.pop("updated_at").endswith("Z")
    assert snapshot == {"stage": "scout", "done": 0, "total": 5, "destinations_total": 5, "hotels_found": 0}


def test_counts_accumulate_and_done_total_reset_when_the_stage_changes():
    engine = _RecordingEngine()
    reporter = ProgressReporter(engine, JOB_ID)
    reporter.update("scout", done=0, total=5, destinations_total=5, hotels_found=0)
    reporter.update("scout", done=5, total=5, hotels_found=61, hotels_in_radius=48)

    reporter.update("deep_crawl", total=48)

    snapshot = _progress(engine)
    assert snapshot["stage"] == "deep_crawl"
    assert "done" not in snapshot and snapshot["total"] == 48
    assert (snapshot["destinations_total"], snapshot["hotels_found"], snapshot["hotels_in_radius"]) == (5, 61, 48)

    reporter.update("deep_crawl", done=8, total=48)

    assert (_progress(engine)["done"], _progress(engine)["total"]) == (8, 48)


@pytest.mark.parametrize("engine, job_id", [(None, JOB_ID), (_RecordingEngine(), None)])
def test_update_is_a_noop_without_an_engine_or_a_job_id(engine, job_id):
    reporter = ProgressReporter(engine, job_id)

    assert reporter.enabled is False
    assert reporter.update("scout", done=0, total=1) is False
    if engine is not None:
        assert engine.executed == []


def test_update_swallows_and_logs_database_errors(caplog):
    reporter = ProgressReporter(_RecordingEngine(error=RuntimeError("connection refused")), JOB_ID)

    with caplog.at_level("WARNING", logger="roomrate.scraper"):
        assert reporter.update("persist") is False

    assert "connection refused" in caplog.text
