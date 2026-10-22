"""«Απόδοση» routes: HTTP answers, ownership, and the contract against the page's sample answers."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_onboarding_repository,
    get_performance_import_service,
    get_performance_kpi_service,
    get_performance_repository,
)
from api.main import app
from api.routers import performance as performance_router
from api.schemas.performance import (
    ImportCommitOut,
    ImportPreviewOut,
    ImportSummaryOut,
    PerformanceChannelsOut,
    PerformanceCountriesOut,
    PerformanceMonthlyOut,
    PerformancePaceOut,
    PerformanceRoomTypesOut,
    PerformanceStatusOut,
    PerformanceStayPatternsOut,
    PerformanceSummaryOut,
    PropertyClosuresOut,
    RevenueSettingsOut,
    RoomInventoryOut,
)
from api.services.performance_import import ImportFileError
from api.services.performance_import_service import ImportAlreadyCommitted, ImportNotFound, ImportRejected
from api.services.performance_kpis import KpiRejected

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
DOUBLE = UUID("11111111-1111-4111-8111-000000000001")
MOCKS = Path(__file__).resolve().parents[2] / "frontend" / "public" / "mock" / "performance"
QUERY = {"owned_property_id": str(PROPERTY_ID)}


def mock(name):
    return json.loads((MOCKS / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The contract: the page's samples are exactly what these answers look like
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, schema",
    [
        ("status", PerformanceStatusOut),
        ("status-empty", PerformanceStatusOut),
        ("import-preview", ImportPreviewOut),
        ("import-commit", ImportCommitOut),
        ("imports", list[ImportSummaryOut]),
        ("inventory", RoomInventoryOut),
        ("closures", PropertyClosuresOut),
        ("revenue-settings", RevenueSettingsOut),
        ("summary", PerformanceSummaryOut),
        ("monthly", PerformanceMonthlyOut),
        ("channels", PerformanceChannelsOut),
        ("countries", PerformanceCountriesOut),
        ("room-types", PerformanceRoomTypesOut),
        ("stay-patterns", PerformanceStayPatternsOut),
        ("pace", PerformancePaceOut),
    ],
)
def test_the_page_samples_are_exactly_the_backend_answers(name, schema):
    sample = mock(name)
    adapter = TypeAdapter(schema)
    answer = adapter.dump_python(adapter.validate_python(sample), mode="json")

    def keys(value):
        if isinstance(value, dict):
            return {key: keys(item) for key, item in value.items()}
        if isinstance(value, list):
            return [keys(item) for item in value[:1]]
        return None

    assert keys(answer) == keys(sample)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class FakeOnboarding:
    def get_owned_property(self, account_id, owned_property_id):
        if (account_id, owned_property_id) == (ACCOUNT_ID, PROPERTY_ID):
            return {"id": PROPERTY_ID, "display_name": "Aigaio Rooms"}
        return None


class FakeService:
    def __init__(self):
        self.raise_on = {}
        self.calls = []

    def _maybe_raise(self, name):
        if name in self.raise_on:
            raise self.raise_on[name]

    def status(self, account_id, owned_property_id):
        return mock("status")

    def preview_upload(self, account_id, owned_property_id, content, filename, fmt):
        self.calls.append(("upload", filename, fmt, len(content)))
        self._maybe_raise("upload")
        return mock("import-preview")

    def update_mapping(self, account_id, owned_property_id, import_id, items):
        self.calls.append(("mapping", items))
        self._maybe_raise("mapping")
        return mock("import-preview")

    def commit(self, account_id, owned_property_id, import_id, *, skip_errors):
        self.calls.append(("commit", skip_errors))
        self._maybe_raise("commit")
        return mock("import-commit")


class FakeRepository:
    def __init__(self):
        self.saved = {}

    def room_types(self, account_id, owned_property_id):
        return [{"id": DOUBLE, "label": "Δίκλινο", "rooms": 5}]

    def inventory(self, account_id, owned_property_id):
        return mock("inventory")["room_types"]

    def save_inventory(self, account_id, owned_property_id, rows):
        self.saved["inventory"] = rows

    def closures(self, account_id, owned_property_id):
        return mock("closures")["closures"]

    def save_closures(self, account_id, owned_property_id, rows):
        self.saved["closures"] = rows

    def revenue_settings(self, account_id, owned_property_id):
        return mock("revenue-settings")

    def save_revenue_settings(self, account_id, owned_property_id, **settings):
        self.saved["settings"] = settings

    def imports(self, account_id, owned_property_id):
        return mock("imports")

    def delete_bookings(self, account_id, owned_property_id):
        self.saved["deleted"] = True
        return 3


@pytest.fixture
def client():
    service, repository = FakeService(), FakeRepository()
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_repository] = lambda: FakeOnboarding()
    app.dependency_overrides[get_performance_import_service] = lambda: service
    app.dependency_overrides[get_performance_repository] = lambda: repository
    yield TestClient(app), service, repository
    app.dependency_overrides.clear()


def test_upload_passes_the_file_and_its_format(client):
    http, service, _ = client
    response = http.post(
        "/api/v1/performance/imports", params=QUERY,
        files={"file": ("bookings.xlsx", b"PK\x03\x04data")}, data={"format": "booking"},
    )

    assert response.status_code == 200 and response.json()["filename"] == "bookings_export_2026.xlsx"
    assert service.calls == [("upload", "bookings.xlsx", "booking", 8)]


def test_upload_over_5_mb_is_refused_before_reading_it(client, monkeypatch):
    http, service, _ = client
    monkeypatch.setattr(performance_router, "MAX_UPLOAD_BYTES", 10)
    response = http.post(
        "/api/v1/performance/imports", params=QUERY, files={"file": ("big.csv", b"x" * 11)}, data={"format": "booking"},
    )

    assert response.status_code == 413 and response.json() == {"detail": "Το αρχείο είναι πάνω από 5 MB."}
    assert service.calls == []


def test_upload_needs_a_known_format(client):
    http, _, _ = client
    response = http.post("/api/v1/performance/imports", params=QUERY, files={"file": ("a.csv", b"x")}, data={"format": "pdf"})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "step, error, status, detail",
    [
        ("upload", ImportFileError("Το αρχείο είναι άδειο."), 400, "Το αρχείο είναι άδειο."),
        ("commit", ImportRejected("Αντιστοιχίστε πρώτα"), 400, "Αντιστοιχίστε πρώτα"),
        ("commit", ImportNotFound(), 404, performance_router.UPLOAD_GONE),
        ("commit", ImportAlreadyCommitted(), 409, performance_router.ALREADY_COMMITTED),
        ("mapping", ImportAlreadyCommitted(), 409, performance_router.ALREADY_COMMITTED),
    ],
)
def test_upload_refusals_are_greek_http_answers(client, step, error, status, detail):
    http, service, _ = client
    service.raise_on[step] = error
    import_id = "33333333-3333-4333-8333-000000000001"
    calls = {
        "upload": lambda: http.post("/api/v1/performance/imports", params=QUERY,
                                    files={"file": ("a.csv", b"x")}, data={"format": "booking"}),
        "commit": lambda: http.post(f"/api/v1/performance/imports/{import_id}/commit", params=QUERY,
                                    json={"skip_errors": True}),
        "mapping": lambda: http.put(f"/api/v1/performance/imports/{import_id}/mapping", params=QUERY,
                                    json={"mapping": [{"label": "Twin", "ignore": True}]}),
    }
    response = calls[step]()

    assert response.status_code == status and response.json() == {"detail": detail}


def test_inventory_refuses_another_property_room_type_and_a_backwards_range(client):
    http, _, repository = client
    foreign = http.put("/api/v1/performance/inventory", params=QUERY,
                       json={"room_types": [{"room_type_id": str(UUID(int=9)), "rooms": 3}]})
    backwards = http.put("/api/v1/performance/inventory", params=QUERY, json={"room_types": [
        {"room_type_id": str(DOUBLE), "rooms": 3, "valid_from": "2026-09-01", "valid_to": "2026-06-01"},
    ]})
    saved = http.put("/api/v1/performance/inventory", params=QUERY,
                     json={"room_types": [{"room_type_id": str(DOUBLE), "label": "Δίκλινο", "rooms": 5}]})

    assert foreign.status_code == 400 and foreign.json()["detail"] == performance_router.FOREIGN_ROOM_TYPE
    assert backwards.status_code == 422
    assert saved.status_code == 200 and repository.saved["inventory"][0]["rooms"] == 5


def test_closures_and_settings_round_trip(client):
    http, _, repository = client
    closures = http.put("/api/v1/performance/closures", params=QUERY, json={"closures": [
        {"date_from": "2026-11-01", "date_to": "2027-04-30", "reason": "Εκτός σεζόν"},
    ]})
    settings = http.put("/api/v1/performance/revenue-settings", params=QUERY,
                        json={"vat_pct": 13, "resilience_fee_eur": 1.5, "fee_included_in_price": True})
    bad_vat = http.put("/api/v1/performance/revenue-settings", params=QUERY,
                       json={"vat_pct": 130, "resilience_fee_eur": 0, "fee_included_in_price": False})

    assert closures.status_code == 200 and repository.saved["closures"][0]["room_type_id"] is None
    assert settings.status_code == 200 and repository.saved["settings"]["fee_included_in_price"] is True
    assert bad_vat.status_code == 422


def test_delete_bookings_answers_204(client):
    http, _, repository = client
    response = http.delete("/api/v1/performance/bookings", params=QUERY)

    assert response.status_code == 204 and response.content == b"" and repository.saved["deleted"]


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/v1/performance/status"),
        ("get", "/api/v1/performance/imports"),
        ("get", "/api/v1/performance/inventory"),
        ("get", "/api/v1/performance/closures"),
        ("get", "/api/v1/performance/revenue-settings"),
        ("delete", "/api/v1/performance/bookings"),
    ],
)
def test_another_account_property_is_not_found(client, method, path):
    http, _, repository = client
    response = getattr(http, method)(path, params={"owned_property_id": str(UUID(int=777))})

    assert response.status_code == 404 and response.json() == {"detail": "Owned property not found"}
    assert "deleted" not in repository.saved


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


class FakeKpis:
    def __init__(self):
        self.calls = []
        self.error = None

    def _answer(self, name, owned_property, filters):
        self.calls.append((name, owned_property["id"], filters))
        if self.error:
            raise self.error
        return mock(name)

    def summary(self, account_id, owned_property, filters):
        return self._answer("summary", owned_property, filters)

    def monthly(self, account_id, owned_property, filters):
        return self._answer("monthly", owned_property, filters)

    def channels(self, account_id, owned_property, filters):
        return self._answer("channels", owned_property, filters)

    def countries(self, account_id, owned_property, filters):
        return self._answer("countries", owned_property, filters)

    def room_types(self, account_id, owned_property, filters):
        return self._answer("room-types", owned_property, filters)

    def stay_patterns(self, account_id, owned_property, filters):
        return self._answer("stay-patterns", owned_property, filters)

    def pace(self, account_id, owned_property, season, today):
        return self._answer("pace", owned_property, (season, today))


@pytest.fixture
def kpi_client(client):
    http, _, _ = client
    kpis = FakeKpis()
    app.dependency_overrides[get_performance_kpi_service] = lambda: kpis
    return http, kpis


@pytest.mark.parametrize("name", ["summary", "monthly", "channels", "countries", "room-types", "stay-patterns"])
def test_kpi_calls_pass_the_filters(kpi_client, name):
    http, kpis = kpi_client
    response = http.get(f"/api/v1/performance/{name}", params={
        **QUERY, "date_from": "2026-05-01", "date_to": "2026-10-31", "room_type_id": str(DOUBLE), "amounts": "net",
    })

    assert response.status_code == 200 and response.json() == mock(name)
    (called, property_id, filters), = kpis.calls
    assert (called, property_id) == (name, PROPERTY_ID)
    assert (filters.date_from.isoformat(), filters.room_type_id, filters.amounts, filters.compare) == (
        "2026-05-01", DOUBLE, "net", "last_year"
    )


def test_kpi_calls_need_the_period_and_known_values(kpi_client):
    http, kpis = kpi_client

    assert http.get("/api/v1/performance/summary", params=QUERY).status_code == 422
    assert http.get("/api/v1/performance/summary", params={
        **QUERY, "date_from": "2026-05-01", "date_to": "2026-10-31", "amounts": "both",
    }).status_code == 422
    assert kpis.calls == []


def test_kpi_refusal_is_a_greek_400(kpi_client):
    http, kpis = kpi_client
    kpis.error = KpiRejected("Η περίοδος τελειώνει πριν αρχίσει.")
    response = http.get("/api/v1/performance/monthly", params={**QUERY, "date_from": "2026-10-31", "date_to": "2026-05-01"})

    assert response.status_code == 400 and response.json() == {"detail": "Η περίοδος τελειώνει πριν αρχίσει."}


def test_pace_takes_the_season_and_today_in_greece(kpi_client, monkeypatch):
    from datetime import datetime, timezone

    http, kpis = kpi_client
    # 23:30 UTC on 21 October is already 22 October in Greece.
    monkeypatch.setattr(performance_router, "greek_now", lambda: datetime(2026, 10, 22, 2, 30, tzinfo=timezone.utc))
    response = http.get("/api/v1/performance/pace", params={**QUERY, "season": 2027})

    assert response.status_code == 200 and response.json() == mock("pace")
    (_, _, (season, today)), = kpis.calls
    assert (season, today.isoformat()) == (2027, "2026-10-22")


@pytest.mark.parametrize("params", [{}, {"season": 1999}, {"season": "next"}])
def test_pace_needs_a_season(kpi_client, params):
    http, kpis = kpi_client

    assert http.get("/api/v1/performance/pace", params={**QUERY, **params}).status_code == 422
    assert kpis.calls == []
