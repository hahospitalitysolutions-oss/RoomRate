"""The owner's demand collection settings: when the worker collects, and the API to set it."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import (
    AccountContext,
    get_account_context,
    get_demand_settings_repository,
    get_onboarding_repository,
)
from api.main import app
from api.scheduler import run_demand_tick
from api.services.demand_schedule import DEMAND_TIMEZONE, due_scopes, greek_now, next_collection_at
from api.services.ferry_demand_service import CollectionReport
from api.services.flight_demand_service import FlightCollectionReport
from api.tests.test_scheduler import FakeEngine, FakeLockConnection

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
OTHER_ID = UUID("00000000-0000-0000-0000-000000000789")
# 07:30 and 09:30 Greek time on a summer (UTC+3) day.
BEFORE_EIGHT = datetime(2026, 7, 10, 4, 30, tzinfo=timezone.utc)
AFTER_EIGHT = datetime(2026, 7, 10, 6, 30, tzinfo=timezone.utc)
DAY = date(2026, 7, 10)


def prop(destination="rodos", *, ferries=True, flights=True, hour=8, requested_at=None, lat=36.34, lon=28.20):
    return {
        "owned_property_id": PROPERTY_ID, "canonical_destination": destination, "latitude": lat, "longitude": lon,
        "ferries_enabled": ferries, "flights_enabled": flights, "hour_local": hour, "collect_requested_at": requested_at,
    }


def due(properties, now=AFTER_EIGHT, ran_today=(), last_started=None, **kwargs):
    return due_scopes(
        properties, ran_today=set(ran_today), last_started=last_started or {}, now_local=greek_now(now), **kwargs
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


def test_nothing_is_due_before_the_owner_hour():
    result = due([prop()], now=BEFORE_EIGHT)

    assert result.ferry_destinations == set() and not result.flights


def test_both_are_due_from_the_owner_hour_once_a_day():
    assert due([prop()]).ferry_destinations == {"rodos"}
    assert due([prop()]).flights
    ran = due([prop()], ran_today=[("ferries", "rodos"), ("flights", "all")])
    assert ran.ferry_destinations == set() and not ran.flights


def test_a_request_after_the_last_run_is_due_before_the_hour():
    asked = BEFORE_EIGHT - timedelta(minutes=2)

    result = due([prop(requested_at=asked)], now=BEFORE_EIGHT, last_started={("ferries", "rodos"): asked - timedelta(days=1)})
    stale = due(
        [prop(requested_at=asked)], now=BEFORE_EIGHT,
        last_started={("ferries", "rodos"): asked + timedelta(minutes=1), ("flights", "all"): asked + timedelta(minutes=1)},
    )

    assert result.ferry_destinations == {"rodos"} and result.flights
    assert stale.ferry_destinations == set() and not stale.flights


def test_switched_off_off_island_and_without_an_airport():
    assert due([prop(ferries=False, flights=False)]).ferry_destinations == set()
    # Symi: ferries yes, no airport.
    symi = due([prop("symi", lat=36.61, lon=27.84)])
    assert symi.ferry_destinations == {"symi"} and not symi.flights
    # Global switches (no AeroDataBox key).
    assert not due([prop()], flights_on=False).flights


def test_force_ignores_the_hour():
    assert due([prop(hour=23)], now=BEFORE_EIGHT, force=True).ferry_destinations == {"rodos"}


def test_next_collection():
    morning = greek_now(BEFORE_EIGHT)
    assert next_collection_at(8, False, morning) == morning.replace(hour=8, minute=0, second=0, microsecond=0)
    # Past the hour, not run yet: the next tick.
    late = greek_now(AFTER_EIGHT)
    assert next_collection_at(8, False, late) == late.replace(second=0, microsecond=0)
    assert next_collection_at(8, True, late) == datetime(2026, 7, 11, 8, tzinfo=DEMAND_TIMEZONE)


# ---------------------------------------------------------------------------
# The worker tick
# ---------------------------------------------------------------------------


class FakeSettingsRepository:
    def __init__(self, properties=None, runs=()):
        self.properties = [prop()] if properties is None else properties
        self.stored_runs = list(runs)
        self.claimed = []
        self.finished = []
        self.pruned = []

    def demand_properties(self):
        return self.properties

    def runs(self, kind=None, *, since):
        return [run for run in self.stored_runs if run["run_on"] >= since]

    def claim_run(self, kind, scope, run_on):
        if any((run["kind"], run["scope"], run["run_on"]) == (kind, scope, run_on) for run in self.stored_runs):
            return False
        self.claimed.append((kind, scope, run_on))
        self.stored_runs.append({"kind": kind, "scope": scope, "run_on": run_on, "started_at": AFTER_EIGHT})
        return True

    def finish_run(self, kind, scope, run_on, saved):
        self.finished.append((kind, scope, run_on, saved))

    def prune_runs(self, *, keep_days, today):
        self.pruned.append(today)


class FakeFerryRepo:
    def __init__(self, destinations=("rodos",)):
        self.destinations = destinations

    def routes_to_collect(self, destinations=None):
        return [{"canonical_destination": name} for name in self.destinations if destinations is None or name in destinations]

    def prune(self, **kwargs):
        pass


class FakeFerryService:
    def __init__(self, destinations=("rodos",), error=None):
        self.repository = FakeFerryRepo(destinations)
        self.calls = []
        self.error = error

    def resolve_profiles(self, today):
        self.calls.append(("resolve", today))
        return 1

    def collect(self, today, destinations=None):
        self.calls.append(("collect", today, tuple(destinations)))
        if self.error:
            raise self.error
        return CollectionReport(routes=1, calls=4, saved=4, destinations={name: 4 for name in destinations})


class FakeFlightService:
    def __init__(self):
        self.repository = FakeFerryRepo()
        self.calls = []

    def collect(self, today):
        self.calls.append(today)
        return FlightCollectionReport(airports=["RHO"], calls=3, saved=3)


@pytest.fixture
def demand_on(monkeypatch):
    monkeypatch.setattr(settings, "ferry_demand_enabled", True)
    monkeypatch.setattr(settings, "flight_demand_enabled", True)
    monkeypatch.setattr(settings, "aerodatabox_api_key", "test-key")


def tick(repository, ferry=None, flight=None, now=AFTER_EIGHT, **kwargs):
    return run_demand_tick(
        now, settings_repository=repository, ferry_service=ferry or FakeFerryService(),
        flight_service=flight or FakeFlightService(), engine=FakeEngine(FakeLockConnection()), **kwargs
    )


def test_tick_looks_up_then_collects_and_logs_the_runs(demand_on):
    repository = FakeSettingsRepository()
    ferry, flight = FakeFerryService(), FakeFlightService()

    result = tick(repository, ferry, flight)

    assert result == {"lookups": 1, "ferries": 4, "flights": 3}
    assert ferry.calls == [("resolve", DAY), ("collect", DAY, ("rodos",))]
    assert flight.calls == [DAY]
    assert repository.finished == [("ferries", "rodos", DAY, 4), ("flights", "all", DAY, 3)]
    # A second tick the same day collects nothing more.
    again_ferry, again_flight = FakeFerryService(), FakeFlightService()
    assert tick(repository, again_ferry, again_flight) == {"lookups": 1, "ferries": 0, "flights": 0}
    assert again_ferry.calls == [("resolve", DAY)] and again_flight.calls == []


def test_tick_before_the_hour_only_looks_up(demand_on):
    ferry, flight = FakeFerryService(), FakeFlightService()

    assert tick(FakeSettingsRepository(), ferry, flight, now=BEFORE_EIGHT) == {"lookups": 1, "ferries": 0, "flights": 0}
    assert ferry.calls == [("resolve", DAY)] and flight.calls == []


def test_a_destination_without_routes_yet_is_not_claimed(demand_on):
    repository = FakeSettingsRepository()

    tick(repository, FakeFerryService(destinations=()), FakeFlightService())

    assert ("ferries", "rodos", DAY) not in repository.claimed


def test_a_crashed_collection_still_closes_its_run(demand_on):
    repository = FakeSettingsRepository()

    result = tick(repository, FakeFerryService(error=RuntimeError("boom")), FakeFlightService())

    assert result["ferries"] == 0 and result["flights"] == 3
    assert ("ferries", "rodos", DAY, 0) in repository.finished


def test_tick_kinds_and_switches(demand_on, monkeypatch):
    ferry, flight = FakeFerryService(), FakeFlightService()
    assert tick(FakeSettingsRepository(), ferry, flight, kinds=("flights",)) == {"lookups": 0, "ferries": 0, "flights": 3}
    assert ferry.calls == []

    monkeypatch.setattr(settings, "aerodatabox_api_key", "")
    monkeypatch.setattr(settings, "ferry_demand_enabled", False)
    assert tick(FakeSettingsRepository()) == {}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

ON_RHODES = {"id": PROPERTY_ID, "canonical_destination": "rodos", "latitude": 36.34, "longitude": 28.20}


class FakeOnboarding:
    def __init__(self, owned_property=ON_RHODES):
        self.owned_property = owned_property

    def get_owned_property(self, account_id, owned_property_id):
        return self.owned_property if owned_property_id == PROPERTY_ID else None


class FakeStore:
    def __init__(self, runs=None):
        self.stored = {"ferries_enabled": True, "flights_enabled": True, "hour_local": 8, "collect_requested_at": None}
        self.runs = runs or {}
        self.released = []
        self.saves = []

    def get(self, account_id, owned_property_id):
        return dict(self.stored)

    def save(self, account_id, owned_property_id, *, ferries_enabled, flights_enabled, hour_local, request_collection=False):
        self.saves.append((account_id, owned_property_id, ferries_enabled, flights_enabled, hour_local, request_collection))
        self.stored.update(ferries_enabled=ferries_enabled, flights_enabled=flights_enabled, hour_local=hour_local)
        if request_collection:
            self.stored["collect_requested_at"] = datetime.now(timezone.utc)
        return dict(self.stored)

    def last_run(self, kind, scope):
        return self.runs.get(kind)

    def release_failed_run(self, kind, scope, run_on):
        self.released.append((kind, scope, run_on))
        return False


@pytest.fixture
def client(demand_on):
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_repository] = lambda: FakeOnboarding()
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(store):
    app.dependency_overrides[get_demand_settings_repository] = lambda: store
    return store


def test_settings_default_on_at_eight_for_an_island_with_an_airport(client):
    use(FakeStore())

    body = client.get("/api/v1/demand/settings", params={"owned_property_id": str(PROPERTY_ID)}).json()

    assert (body["ferries_enabled"], body["flights_enabled"], body["hour_local"]) == (True, True, 8)
    assert (body["island"], body["airport_iata"]) == ("Ρόδος", "RHO")
    assert body["ferries_available"] and body["flights_available"]
    assert body["next_collection_at"] is not None and body["collection_pending"] is False


def test_settings_of_a_mainland_property_apply_to_nothing(client):
    use(FakeStore())
    app.dependency_overrides[get_onboarding_repository] = lambda: FakeOnboarding(
        {**ON_RHODES, "canonical_destination": "athens", "latitude": 37.97, "longitude": 23.73}
    )

    body = client.get("/api/v1/demand/settings", params={"owned_property_id": str(PROPERTY_ID)}).json()

    assert (body["island"], body["ferries_available"], body["flights_available"]) == (None, False, False)
    assert body["next_collection_at"] is None


def test_save_settings_and_ask_for_a_collection_now(client):
    store = use(FakeStore())

    body = client.put(
        "/api/v1/demand/settings",
        params={"owned_property_id": str(PROPERTY_ID)},
        json={"ferries_enabled": True, "flights_enabled": False, "hour_local": 6, "collect_now": True},
    ).json()

    assert store.saves[0] == (ACCOUNT_ID, PROPERTY_ID, True, False, 6, False)
    assert store.saves[-1][-1] is True
    assert (body["flights_enabled"], body["hour_local"], body["collection_pending"]) == (False, 6, True)


@pytest.mark.parametrize("hour", [-1, 24])
def test_save_refuses_a_bad_hour(client, hour):
    use(FakeStore())

    response = client.put(
        "/api/v1/demand/settings", params={"owned_property_id": str(PROPERTY_ID)},
        json={"ferries_enabled": True, "flights_enabled": True, "hour_local": hour},
    )

    assert response.status_code == 422


def test_collect_now_once_a_day(client):
    today_run = {"run_on": greek_now().date(), "started_at": datetime.now(timezone.utc) - timedelta(hours=1),
                 "finished_at": datetime.now(timezone.utc), "saved": 12}
    use(FakeStore(runs={"ferries": today_run, "flights": today_run}))

    body = client.post("/api/v1/demand/collect", params={"owned_property_id": str(PROPERTY_ID)}).json()

    assert body["queued"] is False and body["message"].startswith("Η συλλογή έγινε σήμερα")


def test_collect_now_queues_and_retries_a_failed_run(client):
    yesterday = {"run_on": greek_now().date() - timedelta(days=1), "started_at": datetime.now(timezone.utc) - timedelta(days=1),
                 "finished_at": datetime.now(timezone.utc) - timedelta(days=1), "saved": 12}
    store = use(FakeStore(runs={"ferries": yesterday, "flights": yesterday}))

    body = client.post("/api/v1/demand/collect", params={"owned_property_id": str(PROPERTY_ID)}).json()

    assert body["queued"] is True and body["settings"]["collection_pending"] is True
    assert [kind for kind, _, _ in store.released] == ["ferries", "flights"]


def test_collect_now_with_everything_off(client):
    store = use(FakeStore())
    store.stored.update(ferries_enabled=False, flights_enabled=False)

    body = client.post("/api/v1/demand/collect", params={"owned_property_id": str(PROPERTY_ID)}).json()

    assert body["queued"] is False and store.saves == []


def test_settings_404_for_a_property_not_of_this_account(client):
    use(FakeStore())

    assert client.get("/api/v1/demand/settings", params={"owned_property_id": str(OTHER_ID)}).status_code == 404
    assert client.post("/api/v1/demand/collect", params={"owned_property_id": str(OTHER_ID)}).status_code == 404
