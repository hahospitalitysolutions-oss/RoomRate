from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context, get_schedule_service
from api.main import app
from api.schemas.schedule import ScheduleConfigResponse, ScheduleConfigUpdate


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeScheduleService:
    def __init__(self):
        self.get_calls: list[UUID] = []
        self.update_calls: list[tuple[UUID, ScheduleConfigUpdate]] = []

    def get_config(self, account_id: UUID) -> ScheduleConfigResponse:
        self.get_calls.append(account_id)
        return ScheduleConfigResponse(account_id=account_id)

    def update_config(self, account_id: UUID, payload: ScheduleConfigUpdate) -> ScheduleConfigResponse:
        self.update_calls.append((account_id, payload))
        return ScheduleConfigResponse(
            account_id=account_id,
            enabled=bool(payload.enabled),
            hour_utc=payload.hour_utc if payload.hour_utc is not None else 5,
            last_run_at=datetime(2026, 6, 12, 5, 0, tzinfo=timezone.utc),
        )


def _client(service: FakeScheduleService) -> TestClient:
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_schedule_service] = lambda: service
    return TestClient(app)


def test_get_schedule_returns_account_scoped_defaults():
    service = FakeScheduleService()
    client = _client(service)

    response = client.get("/api/v1/schedule")

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == str(ACCOUNT_ID)
    assert payload["enabled"] is False
    assert payload["frequency_hours"] == 24
    assert payload["hour_utc"] == 5
    assert payload["lead_days"] == 30
    assert payload["nights"] == 3
    assert payload["consecutive_failures"] == 0
    assert payload["last_run_at"] is None
    assert service.get_calls == [ACCOUNT_ID]

    app.dependency_overrides.clear()


def test_put_schedule_upserts_partial_payload_for_current_account():
    service = FakeScheduleService()
    client = _client(service)

    response = client.put("/api/v1/schedule", json={"enabled": True, "hour_utc": 6})

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["hour_utc"] == 6
    account_id, update = service.update_calls[0]
    assert account_id == ACCOUNT_ID
    assert update.enabled is True
    assert update.hour_utc == 6
    # Unsent fields must stay unset so the service merge keeps current values.
    assert "nights" not in update.model_dump(exclude_unset=True)

    app.dependency_overrides.clear()


def test_put_schedule_validates_ranges_like_db_checks():
    service = FakeScheduleService()
    client = _client(service)

    for invalid_payload in (
        {"hour_local": 24},
        {"hour_local": -1},
        {"hour_utc": 24},
        {"hour_utc": -1},
        {"frequency_hours": 0},
        {"lead_days": -1},
        {"nights": 0},
        {"adults": 0},
        {"children": -1},
        {"rooms": 0},
    ):
        response = client.put("/api/v1/schedule", json=invalid_payload)
        assert response.status_code == 422, invalid_payload

    assert service.update_calls == []

    app.dependency_overrides.clear()


def test_put_schedule_rejects_explicit_json_nulls():
    # All update fields are Optional only to express "not sent". An explicit
    # null survives exclude_unset and would hit the DB NOT NULL as a 500;
    # the schema must reject it up front as a 422.
    service = FakeScheduleService()
    client = _client(service)

    for null_payload in (
        {"frequency_hours": None},
        {"enabled": None},
        {"hour_utc": None, "nights": 4},
    ):
        response = client.put("/api/v1/schedule", json=null_payload)
        assert response.status_code == 422, null_payload
        assert "cannot be null" in response.text

    assert service.update_calls == []

    app.dependency_overrides.clear()
