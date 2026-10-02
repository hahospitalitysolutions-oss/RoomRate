"""Round 6 read routes (spec §3.6, §7): include_similar, sort=distance and
GET /api/v1/maps/own-property."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_scrape_job_service,
)
from api.main import app
from api.schemas.scrape_jobs import ScrapeJobResponse
from api.services.market_service import MarketService

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
REA_BOOKING_URL = "https://www.booking.com/hotel/gr/rea.html"


def _owned_property(**overrides) -> dict:
    row = {
        "id": OWNED_PROPERTY_ID,
        "display_name": "Rea Hotel",
        "canonical_destination": "faliraki",
        "selected_room_type_category": "double",
        "booking_url": REA_BOOKING_URL,
        # NUMERIC(9,6) columns arrive as Decimal.
        "latitude": Decimal("36.340000"),
        "longitude": Decimal("28.200000"),
    }
    row.update(overrides)
    return row


class FakeOnboardingRepository:
    def __init__(self, owned_property: dict | None = None):
        self.owned_property = owned_property
        self.owned_calls: list[tuple[UUID, UUID]] = []

    def get_owned_property(self, account_id, owned_property_id):
        self.owned_calls.append((account_id, owned_property_id))
        return self.owned_property

    def get_selected_room_type(self, account_id, owned_property_id):
        return {"room_type": "Δίκλινο Δωμάτιο", "room_type_category": "double"}


class RecordingMarketService:
    """Records what the routes pass; returns empty (valid) payloads."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def get_competitors(self, filters, **options):
        self.calls.append(("competitors", {"filters": filters, **options}))
        return []

    def get_competitor_map_markers(self, filters, **options):
        self.calls.append(("markers", {"filters": filters, **options}))
        return []

    def get_market_summary(self, filters, **options):
        self.calls.append(("summary", {"filters": filters, **options}))
        return {
            "total_records": 0,
            "total_hotels": 0,
            "price_min_eur": 0.0,
            "price_max_eur": 0.0,
            "price_avg_eur": 0.0,
            "price_median_eur": 0.0,
            "avg_review_score": 0.0,
            "rooms_left_total": 0,
        }


class FakeOwnRatesRepository:
    """RoomRatesRepository stand-in behind a REAL MarketService."""

    def __init__(self, own_rows: list[dict]):
        self.own_rows = own_rows
        self.own_calls: list[tuple] = []

    def fetch_room_rates(self, filters):
        return []

    def fetch_available_amenities(self, filters):
        return []

    def fetch_own_property_rates(self, account_id, scrape_job_id, display_name):
        self.own_calls.append((account_id, scrape_job_id, display_name))
        return self.own_rows


class FakeScrapeJobService:
    def __init__(self, job: ScrapeJobResponse | None = None):
        self.job = job
        self.get_calls: list[tuple[UUID, UUID]] = []

    def get_job(self, account_id, job_id):
        self.get_calls.append((account_id, job_id))
        return self.job


def _job(**overrides) -> ScrapeJobResponse:
    payload = {
        "id": JOB_ID,
        "account_id": ACCOUNT_ID,
        "owned_property_id": OWNED_PROPERTY_ID,
        "destination": "Φαληράκι",
        "check_in": date(2030, 7, 1),
        "check_out": date(2030, 7, 5),
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "room_type_category": "twin",
        "status": "completed",
        "requested_at": datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
        "nearby_destinations": ["Ιξιά"],
        "radius_km": Decimal("10.0"),
    }
    payload.update(overrides)
    return ScrapeJobResponse(**payload)


def _own_rows() -> list[dict]:
    return [
        {"room_type": "Σουίτα", "room_type_category": "suite", "price_per_night_eur": Decimal("70.00"), "booking_url": None},
        {
            "room_type": "Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια",
            "room_type_category": "twin",
            "price_per_night_eur": Decimal("92.00"),
            "booking_url": "https://www.booking.com/hotel/gr/rea-from-scrape.html",
        },
    ]


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _client(
    market_service=None,
    onboarding_repository: FakeOnboardingRepository | None = None,
    scrape_job_service: FakeScrapeJobService | None = None,
) -> TestClient:
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_market_service] = lambda: market_service or RecordingMarketService()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repository or FakeOnboardingRepository()
    app.dependency_overrides[get_scrape_job_service] = lambda: scrape_job_service or FakeScrapeJobService()
    return TestClient(app)


# ----------------------------------------------------------------------------
# include_similar on markers, competitors and the market summary
# ----------------------------------------------------------------------------

READ_ROUTES = [
    ("/api/v1/maps/competitors", "markers"),
    ("/api/v1/competitors/", "competitors"),
    ("/api/v1/market/summary", "summary"),
]


@pytest.mark.parametrize("path, call_name", READ_ROUTES)
def test_read_routes_hide_non_comparable_rooms_by_default(path, call_name):
    """Owner decision 2026-09-30: comparable_only defaults to true, which
    without agent rows is the pre-Round-6 same-category pool."""
    service = RecordingMarketService()
    client = _client(service)

    response = client.get(path, params={"room_type_category": "double"})

    assert response.status_code == 200
    name, call = service.calls[0]
    assert name == call_name
    assert call["filters"].include_similar is False
    assert call["filters"].comparable_only is True


@pytest.mark.parametrize("path, call_name", READ_ROUTES)
@pytest.mark.parametrize(
    "params, expected_include_similar",
    [
        ({"comparable_only": "false"}, True),
        ({"comparable_only": "true"}, False),
        # An older client that only knows include_similar keeps its meaning.
        ({"include_similar": "true"}, True),
        # comparable_only, when sent, wins over include_similar.
        ({"include_similar": "true", "comparable_only": "true"}, False),
        ({"include_similar": "false", "comparable_only": "false"}, True),
    ],
)
def test_comparable_only_and_the_legacy_include_similar(path, call_name, params, expected_include_similar):
    service = RecordingMarketService()
    client = _client(service)

    response = client.get(path, params={"room_type_category": "double", **params})

    assert response.status_code == 200
    filters = service.calls[0][1]["filters"]
    assert filters.include_similar is expected_include_similar
    assert filters.comparable_only is (not expected_include_similar)


@pytest.mark.parametrize("path, call_name", READ_ROUTES)
def test_read_routes_pass_include_similar_false_for_the_same_category_toggle(path, call_name):
    service = RecordingMarketService()
    client = _client(service)

    response = client.get(path, params={"room_type_category": "double", "include_similar": "false"})

    assert response.status_code == 200
    assert service.calls[0][1]["filters"].include_similar is False


# ----------------------------------------------------------------------------
# distance origin and sort=distance
# ----------------------------------------------------------------------------


def test_markers_get_the_owner_coordinates_as_distance_origin():
    service = RecordingMarketService()
    repository = FakeOnboardingRepository(_owned_property())
    client = _client(service, repository)

    response = client.get("/api/v1/maps/competitors", params={"owned_property_id": str(OWNED_PROPERTY_ID)})

    assert response.status_code == 200
    assert service.calls[0][1]["origin"] == (36.34, 28.2)
    assert repository.owned_calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID)]


@pytest.mark.parametrize(
    "owned_property",
    [None, _owned_property(latitude=None, longitude=None), _owned_property(latitude=Decimal("0"), longitude=Decimal("0"))],
)
def test_markers_have_no_origin_without_a_located_owned_property(owned_property):
    service = RecordingMarketService()
    client = _client(service, FakeOnboardingRepository(owned_property))

    response = client.get("/api/v1/maps/competitors", params={"owned_property_id": str(OWNED_PROPERTY_ID)})

    assert response.status_code == 200
    assert service.calls[0][1]["origin"] is None


def test_markers_without_an_owned_property_never_look_one_up():
    service = RecordingMarketService()
    repository = FakeOnboardingRepository(_owned_property())
    client = _client(service, repository)

    response = client.get("/api/v1/maps/competitors")

    assert response.status_code == 200
    assert service.calls[0][1]["origin"] is None
    assert repository.owned_calls == []


def test_sort_distance_requires_an_owned_property():
    service = RecordingMarketService()
    client = _client(service)

    response = client.get("/api/v1/competitors/", params={"sort": "distance"})

    assert response.status_code == 400
    assert "owned_property_id" in response.json()["detail"]
    assert service.calls == []


def test_sort_distance_passes_the_origin_and_the_sort_flag():
    service = RecordingMarketService()
    client = _client(service, FakeOnboardingRepository(_owned_property()))

    response = client.get(
        "/api/v1/competitors/", params={"sort": "distance", "owned_property_id": str(OWNED_PROPERTY_ID)}
    )

    assert response.status_code == 200
    call = service.calls[0][1]
    assert call["origin"] == (36.34, 28.2)
    assert call["sort_by_distance"] is True
    assert call.get("owned_room") is None


def test_sort_distance_combines_with_room_matching():
    service = RecordingMarketService()
    client = _client(service, FakeOnboardingRepository(_owned_property()))

    response = client.get(
        "/api/v1/competitors/",
        params={"sort": "distance", "match_room": "true", "owned_property_id": str(OWNED_PROPERTY_ID)},
    )

    assert response.status_code == 200
    call = service.calls[0][1]
    assert call["sort_by_distance"] is True
    assert call["sort_by_match"] is False
    assert call["owned_room"].room_type_category == "double"


def test_price_sort_still_carries_distances_when_the_owner_is_known():
    service = RecordingMarketService()
    client = _client(service, FakeOnboardingRepository(_owned_property()))

    response = client.get("/api/v1/competitors/", params={"owned_property_id": str(OWNED_PROPERTY_ID)})

    assert response.status_code == 200
    call = service.calls[0][1]
    assert call["origin"] == (36.34, 28.2)
    assert call["sort_by_distance"] is False


# ----------------------------------------------------------------------------
# GET /api/v1/maps/own-property
# ----------------------------------------------------------------------------


def test_own_property_returns_the_marker_payload_for_a_job():
    rates = FakeOwnRatesRepository(_own_rows())
    jobs = FakeScrapeJobService(_job())
    client = _client(MarketService(rates), FakeOnboardingRepository(_owned_property()), jobs)

    response = client.get(
        "/api/v1/maps/own-property",
        params={"owned_property_id": str(OWNED_PROPERTY_ID), "scrape_job_id": str(JOB_ID)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "display_name": "Rea Hotel",
        "latitude": 36.34,
        "longitude": 28.2,
        "radius_km": 10.0,
        # The job's twin category pools with double: the suite is cheaper but not comparable.
        "price_per_night_eur": 92.0,
        "room_type": "Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια",
        "booking_url": REA_BOOKING_URL,
    }
    assert jobs.get_calls == [(ACCOUNT_ID, JOB_ID)]
    assert rates.own_calls == [(ACCOUNT_ID, JOB_ID, "Rea Hotel")]


@pytest.mark.parametrize(
    "result_summary, expected_radius",
    [
        # The scraper ran without the radius: no circle may suggest a filter
        # that never happened.
        ({"version": 1, "rows_written": 12, "warnings": ["nearby_scout_failed:Ιξιά", "radius_skipped_no_coordinates"]}, None),
        ({"version": 1, "rows_written": 12, "warnings": ["nearby_scout_failed:Ιξιά"]}, 10.0),
        ({"version": 1, "rows_written": 12, "warnings": []}, 10.0),
        ({"progress": {"stage": "scout"}}, 10.0),  # still running: no warnings yet
        (None, 10.0),
    ],
)
def test_own_property_reports_the_radius_only_when_the_job_applied_it(result_summary, expected_radius):
    rates = FakeOwnRatesRepository(_own_rows())
    jobs = FakeScrapeJobService(_job(result_summary=result_summary))
    client = _client(MarketService(rates), FakeOnboardingRepository(_owned_property()), jobs)

    response = client.get(
        "/api/v1/maps/own-property",
        params={"owned_property_id": str(OWNED_PROPERTY_ID), "scrape_job_id": str(JOB_ID)},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["radius_km"] == expected_radius
    # The rest of the marker never depends on the radius.
    assert (payload["latitude"], payload["longitude"], payload["price_per_night_eur"]) == (36.34, 28.2, 92.0)


def test_own_property_without_a_job_has_a_marker_but_no_radius_or_price():
    rates = FakeOwnRatesRepository(_own_rows())
    jobs = FakeScrapeJobService(_job())
    client = _client(MarketService(rates), FakeOnboardingRepository(_owned_property()), jobs)

    response = client.get("/api/v1/maps/own-property", params={"owned_property_id": str(OWNED_PROPERTY_ID)})

    assert response.status_code == 200
    payload = response.json()
    assert (payload["latitude"], payload["longitude"]) == (36.34, 28.2)
    assert (payload["radius_km"], payload["price_per_night_eur"], payload["room_type"]) == (None, None, None)
    assert jobs.get_calls == []
    assert rates.own_calls == []


def test_own_property_with_an_unknown_job_reads_as_no_job():
    rates = FakeOwnRatesRepository(_own_rows())
    client = _client(MarketService(rates), FakeOnboardingRepository(_owned_property()), FakeScrapeJobService(None))

    response = client.get(
        "/api/v1/maps/own-property",
        params={"owned_property_id": str(OWNED_PROPERTY_ID), "scrape_job_id": str(JOB_ID)},
    )

    assert response.status_code == 200
    assert (response.json()["radius_km"], response.json()["price_per_night_eur"]) == (None, None)
    assert rates.own_calls == []


def test_own_property_falls_back_to_the_selected_category_and_the_scraped_booking_url():
    scraped_url = "https://www.booking.com/hotel/gr/rea-scraped-suite.html"
    own_rows = _own_rows()
    own_rows[0]["booking_url"] = scraped_url  # the suite row is the one chosen below
    rates = FakeOwnRatesRepository(own_rows)
    jobs = FakeScrapeJobService(_job(room_type_category=None, radius_km=None))
    owned = _owned_property(selected_room_type_category="suite", booking_url=None)
    client = _client(MarketService(rates), FakeOnboardingRepository(owned), jobs)

    response = client.get(
        "/api/v1/maps/own-property",
        params={"owned_property_id": str(OWNED_PROPERTY_ID), "scrape_job_id": str(JOB_ID)},
    )

    payload = response.json()
    assert (payload["price_per_night_eur"], payload["room_type"]) == (70.0, "Σουίτα")
    assert payload["radius_km"] is None
    # No URL on the owned property: the link comes from the chosen scraped row.
    assert payload["booking_url"] == scraped_url


def test_own_property_booking_url_stays_empty_when_neither_source_has_one():
    rates = FakeOwnRatesRepository([{**_own_rows()[0], "booking_url": None}])
    owned = _owned_property(booking_url=None)
    client = _client(MarketService(rates), FakeOnboardingRepository(owned), FakeScrapeJobService(_job()))

    response = client.get(
        "/api/v1/maps/own-property",
        params={"owned_property_id": str(OWNED_PROPERTY_ID), "scrape_job_id": str(JOB_ID)},
    )

    assert response.json()["booking_url"] is None


def test_own_property_not_returned_by_booking_has_no_price():
    client = _client(
        MarketService(FakeOwnRatesRepository([])),
        FakeOnboardingRepository(_owned_property(latitude=None, longitude=None)),
        FakeScrapeJobService(_job()),
    )

    response = client.get(
        "/api/v1/maps/own-property",
        params={"owned_property_id": str(OWNED_PROPERTY_ID), "scrape_job_id": str(JOB_ID)},
    )

    payload = response.json()
    assert response.status_code == 200
    assert (payload["latitude"], payload["longitude"]) == (None, None)
    assert payload["radius_km"] == 10.0
    assert (payload["price_per_night_eur"], payload["room_type"]) == (None, None)


def test_own_property_is_404_for_an_unknown_or_foreign_property():
    client = _client(MarketService(FakeOwnRatesRepository([])), FakeOnboardingRepository(None))

    response = client.get("/api/v1/maps/own-property", params={"owned_property_id": str(OWNED_PROPERTY_ID)})

    assert response.status_code == 404
    # The route's own 404, not the router's "Not Found" for a missing path.
    assert response.json()["detail"] == "Owned property not found"


def test_own_property_requires_an_owned_property_id():
    client = _client(MarketService(FakeOwnRatesRepository([])), FakeOnboardingRepository(_owned_property()))

    assert client.get("/api/v1/maps/own-property").status_code == 422
