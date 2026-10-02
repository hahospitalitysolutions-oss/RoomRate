from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.config import settings
from api.main import app
from api.scheduler import SchedulerStatus
from api.tests._fakes import FakeResult, install_scripted_engine


class FakeRunningScheduler:
    """Minimal stand-in for the APScheduler the lifespan stores on app.state."""

    def __init__(self, running: bool):
        self._running = running

    @property
    def running(self) -> bool:
        return self._running


def test_health_reports_scheduler_liveness(monkeypatch):
    # A live scheduler with a recorded last tick.
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    tick_at = datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc)
    status = SchedulerStatus(last_tick_at=tick_at)
    app.state.scheduler = FakeRunningScheduler(running=True)
    app.state.scheduler_status = status

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["scheduler"]["enabled"] is True
    assert payload["scheduler"]["running"] is True
    assert payload["scheduler"]["last_tick_at"] == tick_at.isoformat()


def test_health_handles_disabled_or_absent_scheduler(monkeypatch):
    # Scheduler disabled / never started: running=false, last_tick_at=null.
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    app.state.scheduler = None
    app.state.scheduler_status = SchedulerStatus()

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["scheduler"]["enabled"] is False
    assert payload["scheduler"]["running"] is False
    assert payload["scheduler"]["last_tick_at"] is None


def test_health_survives_missing_state_attributes():
    # Defensive: if app.state was never populated, /health must still answer.
    if hasattr(app.state, "scheduler"):
        delattr(app.state, "scheduler")
    if hasattr(app.state, "scheduler_status"):
        delattr(app.state, "scheduler_status")

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["scheduler"]["running"] is False
    assert payload["scheduler"]["last_tick_at"] is None


def test_ready_returns_200_when_database_is_reachable(monkeypatch):
    install_scripted_engine(monkeypatch, "api.main", [FakeResult(rows=[{"one": 1}])])
    monkeypatch.setattr(settings, "process_role", "api")

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"]["database"]["ready"] is True


def test_ready_returns_503_when_database_is_unavailable(monkeypatch):
    def unavailable_engine(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("api.main.get_engine", unavailable_engine)
    monkeypatch.setattr(settings, "process_role", "api")

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"]["error"] == "RuntimeError"


def test_ready_requires_scheduler_only_in_local_all_mode(monkeypatch):
    install_scripted_engine(monkeypatch, "api.main", [FakeResult(rows=[{"one": 1}])])
    monkeypatch.setattr(settings, "process_role", "all")
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    app.state.scheduler = FakeRunningScheduler(running=False)

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["scheduler"]["ready"] is False
