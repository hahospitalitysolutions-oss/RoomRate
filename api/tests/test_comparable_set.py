"""The owner's comparison set (decision 2026-09-30).

The matching agent's ``comparable`` verdict chooses the packages of the
competitor list, the map markers and the market summary; the statistical
category pool is only the fallback when no agent rows exist. Non-comparable
rooms are hidden by default (``comparable_only=true``).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_market_service,
    get_onboarding_repository,
    get_room_match_repository,
)
from api.main import app
from api.schemas.market import OwnedRoomReference
from api.services.market_service import MarketService, RoomRateFilters, build_agent_match_lookup

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
SELECTED_ROOM_ID = UUID("00000000-0000-0000-0000-000000000abc")
OTHER_ROOM_ID = UUID("00000000-0000-0000-0000-000000000def")
PROP_A = UUID("00000000-0000-0000-0000-0000000000a1")
PROP_B = UUID("00000000-0000-0000-0000-0000000000b1")
PROP_C = UUID("00000000-0000-0000-0000-0000000000c1")

# The double pool (double + twin) the SQL fallback returns for include_similar=false.
DOUBLE_POOL = {"double", "twin"}


def _row(property_id, hotel_name, room_type, category, price, lat=36.34, lng=28.2) -> dict:
    return {
        "property_id": property_id,
        "hotel_name": hotel_name,
        "city": "Faliraki",
        "address": "",
        "property_type": "Hotel",
        "latitude": lat,
        "longitude": lng,
        "stars": 3.0,
        "review_score": 8.0,
        "review_count": 10,
        "price_per_night_eur": price,
        "price_total_eur": price * 4,
        "room_type": room_type,
        "room_type_category": category,
        "meals": "",
        "free_cancellation": "",
        "rooms_left": 3,
    }


ROWS = [
    # Hotel A: a comparable double and a CHEAPER suite the agent rejected.
    _row(PROP_A, "Alpha", "Double Room", "double", 100.0),
    _row(PROP_A, "Alpha", "Junior Suite", "suite", 80.0),
    # Hotel B: only a two-bedroom apartment (legacy row, low score -> not comparable).
    _row(PROP_B, "Beta", "Apartment 2 Bedrooms", "apartment", 70.0, lat=36.35, lng=28.21),
    # Hotel C: a twin the agent scored on an older row (NULL verdict, score >= 50).
    _row(PROP_C, "Gamma", "Twin Room", "twin", 90.0, lat=36.36, lng=28.22),
]


def _match(property_id, room_type, score, comparable) -> dict:
    return {
        "property_id": property_id,
        "room_type": room_type,
        "score": Decimal(str(score)),
        "category_match": "same",
        "reasoning": "Αιτιολόγηση.",
        "comparable": comparable,
        "model_version": "claude-sonnet-5-5",
    }


MATCH_ROWS = [
    _match(PROP_A, "Double Room", 92, True),
    # A high score but an explicit "not a substitute": the verdict wins.
    _match(PROP_A, "Junior Suite", 85, False),
    # NULL verdicts (rows written before 20260930_0027): score >= 50 decides.
    _match(PROP_B, "Apartment 2 Bedrooms", 40, None),
    _match(PROP_C, "Twin Room", 70, None),
]


class PoolRatesRepository:
    """Honours the SQL pool: include_similar=false returns the double pool only."""

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows if rows is not None else ROWS
        self.calls: list[RoomRateFilters] = []

    def fetch_room_rates(self, filters: RoomRateFilters) -> list[dict]:
        self.calls.append(filters)
        if filters.include_similar:
            return [dict(row) for row in self.rows]
        return [dict(row) for row in self.rows if row["room_type_category"] in DOUBLE_POOL]

    def fetch_available_amenities(self, filters: RoomRateFilters) -> list[str]:
        self.calls.append(filters)
        return ["pool"] if filters.include_similar else []


def _filters(comparable_only: bool = True) -> RoomRateFilters:
    return RoomRateFilters(
        scrape_job_id=JOB_ID,
        room_type_category="double",
        include_similar=not comparable_only,
    )


def _packages(competitors) -> dict[str, dict[str, bool]]:
    return {
        competitor.hotel_name: {package.room_type: package.comparable for package in competitor.packages}
        for competitor in competitors
    }


# ---------------------------------------------------------------------------
# The effective verdict
# ---------------------------------------------------------------------------


def test_effective_comparable_is_the_verdict_else_score_at_least_50():
    lookup = build_agent_match_lookup(
        MATCH_ROWS + [_match(PROP_B, "Studio", 50, None), _match(PROP_B, "Loft", 20, True)]
    )

    assert lookup[(str(PROP_A), "double room")].comparable is True
    assert lookup[(str(PROP_A), "junior suite")].comparable is False  # verdict beats score 85
    assert lookup[(str(PROP_B), "apartment 2 bedrooms")].comparable is False  # NULL, 40 < 50
    assert lookup[(str(PROP_C), "twin room")].comparable is True  # NULL, 70 >= 50
    assert lookup[(str(PROP_B), "studio")].comparable is True  # NULL, boundary 50
    assert lookup[(str(PROP_B), "loft")].comparable is True  # verdict beats score 20


# ---------------------------------------------------------------------------
# Service: agent path
# ---------------------------------------------------------------------------


def test_agent_non_comparable_rooms_are_hidden_by_default():
    repository = PoolRatesRepository()
    service = MarketService(repository)

    competitors = service.get_competitors(_filters(), agent_matches=build_agent_match_lookup(MATCH_ROWS))

    # The agent path reads the broad pool it scored, then narrows it.
    assert repository.calls[0].include_similar is True
    assert _packages(competitors) == {
        "Alpha": {"Double Room": True},
        "Gamma": {"Twin Room": True},
    }


def test_agent_non_comparable_rooms_are_flagged_with_comparable_only_false():
    service = MarketService(PoolRatesRepository())

    competitors = service.get_competitors(
        _filters(comparable_only=False), agent_matches=build_agent_match_lookup(MATCH_ROWS)
    )

    assert _packages(competitors) == {
        "Alpha": {"Junior Suite": False, "Double Room": True},
        "Beta": {"Apartment 2 Bedrooms": False},
        "Gamma": {"Twin Room": True},
    }


def test_agent_verdicts_apply_with_match_room_too_and_keep_the_match_source():
    service = MarketService(PoolRatesRepository())

    competitors = service.get_competitors(
        _filters(),
        owned_room=OwnedRoomReference(room_type="Double Room", room_type_category="double"),
        agent_matches=build_agent_match_lookup(MATCH_ROWS),
    )

    by_name = {competitor.hotel_name: competitor for competitor in competitors}
    assert set(by_name) == {"Alpha", "Gamma"}
    assert by_name["Alpha"].match_source == "agent"
    assert by_name["Alpha"].packages[0].match_score == 92.0


def test_a_room_the_agent_left_unscored_falls_back_to_the_category_pool():
    """A failed chunk keeps its rooms on the statistical logic (spec Α.5)."""
    rows = ROWS + [
        _row(PROP_C, "Gamma", "Double Deluxe", "double", 95.0),
        _row(PROP_C, "Gamma", "Family Suite", "suite", 60.0),
    ]
    service = MarketService(PoolRatesRepository(rows))

    competitors = service.get_competitors(
        _filters(comparable_only=False), agent_matches=build_agent_match_lookup(MATCH_ROWS)
    )

    gamma = _packages(competitors)["Gamma"]
    assert gamma["Double Deluxe"] is True
    assert gamma["Family Suite"] is False


@pytest.mark.parametrize("comparable_only", [True, False])
def test_markers_and_summary_agree_with_the_list(comparable_only):
    service = MarketService(PoolRatesRepository())
    lookup = build_agent_match_lookup(MATCH_ROWS)
    filters = _filters(comparable_only=comparable_only)

    competitors = service.get_competitors(filters, agent_matches=lookup)
    markers = service.get_competitor_map_markers(filters, agent_matches=lookup)
    summary = service.get_market_summary(filters, agent_matches=lookup)

    # One price per hotel = its cheapest comparable package (else its cheapest).
    expected = {}
    for competitor in competitors:
        comparable = [p.price_per_night_eur for p in competitor.packages if p.comparable]
        expected[competitor.hotel_name] = min(comparable or [p.price_per_night_eur for p in competitor.packages])
    assert {marker.hotel_name: marker.price_per_night_eur for marker in markers} == expected
    assert summary.total_hotels == len(expected)
    assert summary.price_min_eur == min(expected.values())
    assert summary.price_max_eur == max(expected.values())
    # Alpha's marker is its double (100), never the cheaper rejected suite (80).
    assert expected["Alpha"] == 100.0
    if comparable_only:
        assert set(expected) == {"Alpha", "Gamma"}
        assert all(marker.comparable for marker in markers)
    else:
        assert {marker.hotel_name: marker.comparable for marker in markers}["Beta"] is False


# ---------------------------------------------------------------------------
# Service: fallback path (no agent rows) == Round 6 include_similar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("comparable_only", [True, False])
def test_fallback_is_exactly_the_old_include_similar_read(comparable_only):
    repository = PoolRatesRepository()
    service = MarketService(repository)

    competitors = service.get_competitors(_filters(comparable_only=comparable_only))
    markers = service.get_competitor_map_markers(_filters(comparable_only=comparable_only))

    assert [call.include_similar for call in repository.calls] == [not comparable_only] * 2
    if comparable_only:
        assert _packages(competitors) == {
            "Alpha": {"Double Room": True},
            "Gamma": {"Twin Room": True},
        }
    else:
        # Every room, the non-same-category ones flagged (true == "same").
        assert _packages(competitors) == {
            "Alpha": {"Junior Suite": False, "Double Room": True},
            "Beta": {"Apartment 2 Bedrooms": False},
            "Gamma": {"Twin Room": True},
        }
        for competitor in competitors:
            for package in competitor.packages:
                assert package.comparable is (package.category_match == "same")
    # Markers: cheapest same-category, else cheapest similar (Round 6 rule).
    assert {marker.hotel_name: (marker.price_per_night_eur, marker.category_match) for marker in markers} == (
        {"Alpha": (100.0, "same"), "Gamma": (90.0, "same")}
        if comparable_only
        else {"Alpha": (100.0, "same"), "Beta": (70.0, "similar"), "Gamma": (90.0, "same")}
    )


# ---------------------------------------------------------------------------
# Routes: owned_room_type_id, the default selected room, over the wire
# ---------------------------------------------------------------------------


class FakeOnboardingRepository:
    def __init__(self):
        self.selected_calls: list[tuple] = []
        self.room_calls: list[tuple] = []

    def get_selected_room_type(self, account_id, owned_property_id):
        self.selected_calls.append((account_id, owned_property_id))
        return {"id": SELECTED_ROOM_ID, "room_type": "Double Room", "room_type_category": "double"}

    def get_owned_room_type(self, account_id, owned_room_type_id):
        self.room_calls.append((account_id, owned_room_type_id))
        if owned_room_type_id != OTHER_ROOM_ID:
            return None
        return {"id": OTHER_ROOM_ID, "room_type": "Twin Room", "room_type_category": "twin"}

    def get_owned_property(self, account_id, owned_property_id):
        return None


class FakeRoomMatchRepository:
    def __init__(self, rows_by_room: dict[UUID, list[dict]]):
        self.rows_by_room = rows_by_room
        self.calls: list[tuple] = []

    def fetch_matches(self, account_id, scrape_job_id, owned_room_type_id):
        self.calls.append((account_id, scrape_job_id, owned_room_type_id))
        return self.rows_by_room.get(owned_room_type_id, [])


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _client(match_repository, onboarding_repository=None, rates=None) -> TestClient:
    onboarding_repository = onboarding_repository or FakeOnboardingRepository()
    service = MarketService(rates or PoolRatesRepository())
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_market_service] = lambda: service
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repository
    app.dependency_overrides[get_room_match_repository] = lambda: match_repository
    return TestClient(app)


BASE_PARAMS = {
    "scrape_job_id": str(JOB_ID),
    "owned_property_id": str(OWNED_PROPERTY_ID),
    "room_type_category": "double",
}


def _read_all(client, **params) -> tuple[list[dict], list[dict], dict]:
    query = {**BASE_PARAMS, **params}
    competitors = client.get("/api/v1/competitors/", params=query)
    markers = client.get("/api/v1/maps/competitors", params=query)
    summary = client.get("/api/v1/market/summary", params=query)
    assert competitors.status_code == markers.status_code == summary.status_code == 200
    return competitors.json(), markers.json(), summary.json()


def test_routes_default_to_the_selected_rooms_agent_verdicts_and_agree():
    match_repository = FakeRoomMatchRepository({SELECTED_ROOM_ID: MATCH_ROWS})
    client = _client(match_repository)

    competitors, markers, summary = _read_all(client)

    assert {call[2] for call in match_repository.calls} == {SELECTED_ROOM_ID}
    assert {c["hotel_name"]: [p["room_type"] for p in c["packages"]] for c in competitors} == {
        "Alpha": ["Double Room"],
        "Gamma": ["Twin Room"],
    }
    assert all(p["comparable"] is True for c in competitors for p in c["packages"])
    assert {m["hotel_name"]: m["price_per_night_eur"] for m in markers} == {"Alpha": 100.0, "Gamma": 90.0}
    assert (summary["total_hotels"], summary["price_min_eur"], summary["price_max_eur"]) == (2, 90.0, 100.0)


def test_routes_show_flagged_rooms_with_comparable_only_false():
    client = _client(FakeRoomMatchRepository({SELECTED_ROOM_ID: MATCH_ROWS}))

    competitors, markers, summary = _read_all(client, comparable_only="false")

    flags = {(c["hotel_name"], p["room_type"]): p["comparable"] for c in competitors for p in c["packages"]}
    assert flags == {
        ("Alpha", "Junior Suite"): False,
        ("Alpha", "Double Room"): True,
        ("Beta", "Apartment 2 Bedrooms"): False,
        ("Gamma", "Twin Room"): True,
    }
    assert {m["hotel_name"]: (m["price_per_night_eur"], m["comparable"]) for m in markers} == {
        "Alpha": (100.0, True),
        "Beta": (70.0, False),
        "Gamma": (90.0, True),
    }
    assert summary["total_hotels"] == 3


def test_owned_room_type_id_selects_that_rooms_matches():
    # The selected room's verdicts keep Alpha; the other room's verdicts
    # reject everything but Gamma's twin.
    other_room_rows = [
        _match(PROP_A, "Double Room", 30, False),
        _match(PROP_A, "Junior Suite", 20, False),
        _match(PROP_B, "Apartment 2 Bedrooms", 10, False),
        _match(PROP_C, "Twin Room", 95, True),
    ]
    match_repository = FakeRoomMatchRepository(
        {SELECTED_ROOM_ID: MATCH_ROWS, OTHER_ROOM_ID: other_room_rows}
    )
    onboarding_repository = FakeOnboardingRepository()
    client = _client(match_repository, onboarding_repository)

    competitors, markers, summary = _read_all(client, owned_room_type_id=str(OTHER_ROOM_ID))

    assert {call[2] for call in match_repository.calls} == {OTHER_ROOM_ID}
    assert onboarding_repository.selected_calls == []
    assert [c["hotel_name"] for c in competitors] == ["Gamma"]
    assert [m["hotel_name"] for m in markers] == ["Gamma"]
    assert summary["total_hotels"] == 1


def test_match_room_with_owned_room_type_id_scores_against_that_room():
    match_repository = FakeRoomMatchRepository({OTHER_ROOM_ID: [_match(PROP_C, "Twin Room", 95, True)]})
    onboarding_repository = FakeOnboardingRepository()
    client = _client(match_repository, onboarding_repository)

    response = client.get(
        "/api/v1/competitors/",
        params={**BASE_PARAMS, "match_room": "true", "owned_room_type_id": str(OTHER_ROOM_ID)},
    )
    missing = client.get(
        "/api/v1/competitors/",
        params={**BASE_PARAMS, "match_room": "true", "owned_room_type_id": str(SELECTED_ROOM_ID)},
    )

    assert response.status_code == 200
    assert match_repository.calls == [(ACCOUNT_ID, JOB_ID, OTHER_ROOM_ID)]
    assert onboarding_repository.selected_calls == []
    gamma = {c["hotel_name"]: c for c in response.json()}["Gamma"]
    assert gamma["match_source"] == "agent"
    assert gamma["packages"][0]["match_score"] == 95.0
    assert missing.status_code == 404


def test_without_agent_rows_the_routes_serve_the_category_fallback():
    match_repository = FakeRoomMatchRepository({})
    client = _client(match_repository)

    default_read = _read_all(client)
    broad_read = _read_all(client, comparable_only="false")

    assert {c["hotel_name"] for c in default_read[0]} == {"Alpha", "Gamma"}
    assert {m["hotel_name"] for m in default_read[1]} == {"Alpha", "Gamma"}
    assert default_read[2]["total_hotels"] == 2
    assert {c["hotel_name"] for c in broad_read[0]} == {"Alpha", "Beta", "Gamma"}
    assert broad_read[2]["total_hotels"] == 3


def test_agent_lookup_failure_degrades_to_the_fallback_on_every_read():
    class BrokenMatchRepository:
        def fetch_matches(self, *args):
            raise RuntimeError("relation does not exist")

    client = _client(BrokenMatchRepository())

    competitors, markers, summary = _read_all(client)

    assert {c["hotel_name"] for c in competitors} == {"Alpha", "Gamma"}
    assert summary["total_hotels"] == 2


def test_amenity_options_use_the_broad_pool_when_agent_verdicts_exist():
    rates = PoolRatesRepository()
    client = _client(FakeRoomMatchRepository({SELECTED_ROOM_ID: MATCH_ROWS}), rates=rates)

    with_agent = client.get("/api/v1/market/amenities", params=BASE_PARAMS)
    without_job = client.get(
        "/api/v1/market/amenities", params={k: v for k, v in BASE_PARAMS.items() if k != "scrape_job_id"}
    )

    assert with_agent.json() == ["pool"]
    assert without_job.json() == []
    assert replace(rates.calls[-1], include_similar=False) == rates.calls[-1]
