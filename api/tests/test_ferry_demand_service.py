"""Ferry demand: port lookup, route ranking, collection budget and the signal rules."""

from datetime import date, timedelta
from uuid import UUID

from api.services.ferry_demand_service import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_ROUTES_PER_DESTINATION,
    PARSER_VERSION,
    RAW_PAYLOAD_MAX_CHARS,
    FerryDemandService,
    aggregate_trips,
    build_ferry_demand_signal,
    daily_levels,
    is_mainland_gateway,
    latin,
    load_ferry_signal,
    place_keys,
    plan_travel_dates,
    resolve_island_port,
    trimmed_payload,
)
from api.services.ferryhopper_client import FerryPort, FerryTrip
from api.services.mcp_http_client import McpError

TODAY = date(2026, 10, 11)  # a Sunday
ROUTE_A = UUID("00000000-0000-0000-0000-0000000000a1")
ROUTE_B = UUID("00000000-0000-0000-0000-0000000000b2")

PORTS = [
    FerryPort("Piraeus", "PIR", 37.942, 23.646),
    FerryPort("Perama", "PER", 37.965, 23.570),
    FerryPort("Salamina (Paloukia)", "SAL", 37.958, 23.508),
    FerryPort("Rhodes", "RHO", 36.449, 28.228),
    FerryPort("Kos", "KOS", 36.894, 27.289),
]


def trip(price=50.0, available=None, direct=None, operator="Blue Star"):
    return FerryTrip(departure="17:30", operator=operator, price_eur=price, available=available, direct=direct)


def test_greek_place_names_become_latin_keys_with_exonyms():
    assert latin("Σαλαμίνα") == "salamina"
    assert latin("Μύκονος") == "mykonos"
    assert latin("Ζάκυνθος") == "zakynthos"
    assert place_keys(["Ρόδος", None, "rodos"]) == ["rodos", "rhodes"]
    assert place_keys(["Κέρκυρα"]) == ["kerkyra", "corfu"]


def test_english_names_reach_the_transliterated_port_names_and_back():
    """Ferryhopper lists RODOS and PIRAEUS (probe 2026-10-11); a Booking city may say Rhodes."""
    assert "rodos" in place_keys(["Rhodes"])
    assert "corfu" in place_keys(["Kerkyra"]) and "kerkyra" in place_keys(["Corfu"])


LIVE_PORTS = [FerryPort("PIRAEUS", "PIR", country="Greece"), FerryPort("RODOS", "RHO", country="Greece"),
              FerryPort("AG.MARINA (LEROS)", "AGM", country="Greece")]


def test_without_port_coordinates_the_island_is_found_by_name():
    for city in ("Rhodes", "Ρόδος", "Rodos"):
        match, reason = resolve_island_port(LIVE_PORTS, [city], 36.44, 28.22)
        assert (match.port.name, match.matched_by, reason) == ("RODOS", "name", "")


def test_mainland_gateways_are_recognised_by_name_token():
    assert is_mainland_gateway("Piraeus (Athens)")
    assert is_mainland_gateway("Agios Konstantinos")
    assert not is_mainland_gateway("Salamina (Paloukia)")
    assert not is_mainland_gateway("Rhodes")


def test_the_port_named_after_the_place_wins_over_a_nearer_mainland_one():
    """Salamina sits a strait away from Perama: the name decides, not the metres."""
    match, reason = resolve_island_port(PORTS, ["Σαλαμίνα", "Σαλαμίνα"], 37.962, 23.540)

    assert reason == ""
    assert match.port.name == "Salamina (Paloukia)"
    assert match.matched_by == "name"
    assert 0 < match.distance_km < 5


def test_a_hotel_next_to_a_mainland_port_has_no_ferry_demand():
    match, reason = resolve_island_port(PORTS, ["Πειραιάς"], 37.945, 23.640)

    assert match is None
    assert "Piraeus" in reason and "στεριά" in reason


def test_nearest_port_is_used_when_no_name_matches():
    match, _ = resolve_island_port(PORTS, ["Ιξιά"], 36.42, 28.20)

    assert match.port.name == "Rhodes"
    assert match.matched_by == "distance"


def test_without_coordinates_only_a_near_certain_name_counts():
    match, _ = resolve_island_port(PORTS, ["Ρόδος"], None, None)
    assert match.port.name == "Rhodes" and match.distance_km is None

    match, reason = resolve_island_port(PORTS, ["Κάπου αλλού"], None, None)
    assert match is None and reason


def test_far_ports_are_not_matched_by_distance():
    match, _ = resolve_island_port(PORTS, ["Nowhere"], 40.0, 20.0)

    assert match is None


def test_aggregate_counts_direct_sailings_seats_and_open_fares():
    stats = aggregate_trips(
        [
            trip(60.0, available=True),
            trip(45.0, available=False, operator="Seajets"),
            trip(80.0, available=None),
            trip(30.0, direct=False),  # a connection via another island: not a sailing in
        ]
    )

    assert stats == {
        "sailings": 3,
        "availability_known": 2,
        "sold_out": 1,
        "min_price_eur": 60.0,  # the sold-out 45 € seat cannot be bought
        "median_price_eur": 70.0,
        "operators": 2,
    }


def test_aggregate_of_nothing_is_zero_with_unknown_fares():
    assert aggregate_trips([]) == {
        "sailings": 0,
        "availability_known": 0,
        "sold_out": 0,
        "min_price_eur": None,
        "median_price_eur": None,
        "operators": 0,
    }


def test_travel_dates_are_daily_then_fridays_and_saturdays():
    dates = plan_travel_dates(TODAY, daily_days=3, horizon_days=14)

    assert dates == [
        date(2026, 10, 12),
        date(2026, 10, 13),
        date(2026, 10, 14),
        date(2026, 10, 16),
        date(2026, 10, 17),
        date(2026, 10, 23),
        date(2026, 10, 24),
    ]


def test_schedule_arrivals_survive_a_short_budget():
    arrival = date(2026, 11, 13)

    dates = plan_travel_dates(
        TODAY, daily_days=14, horizon_days=60, priority_dates={arrival - timedelta(days=1), arrival}, max_dates=4
    )

    assert dates == [date(2026, 10, 12), date(2026, 10, 13), date(2026, 11, 12), arrival]


def test_large_answers_are_cut_for_storage():
    small = {"trips": [1, 2]}
    assert trimmed_payload(small) is small
    big = trimmed_payload({"text": "x" * (RAW_PAYLOAD_MAX_CHARS + 10)})
    assert big["truncated"] is True and len(big["head"]) == RAW_PAYLOAD_MAX_CHARS
    assert trimmed_payload(None) is None


def row(route_id, travel_date, sailings=4, known=0, sold_out=0, price=60.0, observed_on=TODAY, origin="Piraeus"):
    return {
        "route_id": route_id,
        "origin_port": origin,
        "destination_port": "Rhodes",
        "travel_date": travel_date,
        "observed_on": observed_on,
        "sailings": sailings,
        "availability_known": known,
        "sold_out": sold_out,
        "min_price_eur": price,
        "median_price_eur": price,
    }


CHECK_IN = date(2026, 11, 14)


def usual_dates():
    return [date(2026, 10, 20) + timedelta(days=offset) for offset in range(6)]


def test_sold_out_sailings_make_demand_high():
    rows = [row(ROUTE_A, day) for day in usual_dates()]
    rows += [row(ROUTE_A, CHECK_IN - timedelta(days=1), known=4, sold_out=2), row(ROUTE_A, CHECK_IN, known=4, sold_out=2)]

    signal = build_ferry_demand_signal(rows, CHECK_IN, TODAY)

    assert signal.level == "high"
    assert signal.window == [date(2026, 11, 13), CHECK_IN]
    assert (signal.sailings, signal.availability_known, signal.sold_out) == (8, 8, 4)
    assert signal.sold_out_share_pct == 50.0
    assert signal.routes == ["Piraeus → Rhodes"]
    assert "Πλοία προς Rhodes για 13/11–14/11: 8 δρομολόγια, 4 χωρίς θέσεις" in signal.summary


def test_a_few_sold_out_sailings_stay_normal():
    rows = [row(ROUTE_A, day) for day in usual_dates()]
    rows += [row(ROUTE_A, CHECK_IN - timedelta(days=1), known=4, sold_out=2), row(ROUTE_A, CHECK_IN, known=4, sold_out=1)]

    signal = build_ferry_demand_signal(rows, CHECK_IN, TODAY)

    assert signal.sold_out_share_pct == 37.5
    assert signal.level == "normal"


def test_dearer_fares_than_usual_make_demand_high():
    rows = [row(ROUTE_A, day, price=60.0) for day in usual_dates()]
    rows += [row(ROUTE_A, CHECK_IN - timedelta(days=1), price=72.0), row(ROUTE_A, CHECK_IN, price=70.0)]

    signal = build_ferry_demand_signal(rows, CHECK_IN, TODAY)

    assert signal.level == "high"
    assert signal.price_vs_usual_pct == 18.3
    assert "+18% από το συνηθισμένο" in signal.summary


def test_extra_sailings_make_demand_high_and_cheaper_fares_low():
    usual = [row(ROUTE_A, day, sailings=2) for day in usual_dates()]
    extra = usual + [row(ROUTE_A, CHECK_IN - timedelta(days=1), sailings=3), row(ROUTE_A, CHECK_IN, sailings=3)]
    assert build_ferry_demand_signal(extra, CHECK_IN, TODAY).sailings_vs_usual_pct == 50.0
    assert build_ferry_demand_signal(extra, CHECK_IN, TODAY).level == "high"

    cheap = usual + [row(ROUTE_A, CHECK_IN - timedelta(days=1), sailings=2, price=50.0), row(ROUTE_A, CHECK_IN, sailings=2, price=50.0)]
    assert build_ferry_demand_signal(cheap, CHECK_IN, TODAY).level == "low"


def test_without_a_baseline_the_level_stays_normal():
    rows = [row(ROUTE_A, CHECK_IN, price=90.0), row(ROUTE_A, date(2026, 10, 20), price=60.0)]

    signal = build_ferry_demand_signal(rows, CHECK_IN, TODAY)

    assert signal.level == "normal"
    assert signal.price_vs_usual_pct is None and signal.sailings_vs_usual_pct is None


def test_stale_or_missing_observations_are_unknown():
    stale = [row(ROUTE_A, CHECK_IN, known=2, sold_out=2, observed_on=TODAY - timedelta(days=5))]

    assert build_ferry_demand_signal(stale, CHECK_IN, TODAY).level == "unknown"
    assert build_ferry_demand_signal([], CHECK_IN, TODAY).level == "unknown"


def test_daily_levels_sum_every_route_per_day():
    rows = [row(ROUTE_A, day) for day in usual_dates()] + [row(ROUTE_B, usual_dates()[0], sailings=1, price=40.0, origin="Kos")]

    days = daily_levels(rows, TODAY)

    assert days[0]["travel_date"] == date(2026, 10, 20)
    assert (days[0]["sailings"], days[0]["min_price_eur"]) == (5, 40.0)
    assert len(days) == 6


class FakeRepository:
    def __init__(self, destinations=(), routes=(), lead_days=None, rows=()):
        self.destinations = list(destinations)
        self.routes = list(routes)
        self.lead_days = lead_days or {}
        self.rows = list(rows)
        self.profiles = []
        self.observations = []
        self.latest_calls = []

    def destinations_to_profile(self, limit):
        return self.destinations[:limit]

    def save_profile(self, canonical_destination, **fields):
        self.profiles.append((canonical_destination, fields))

    def routes_to_collect(self):
        return self.routes

    def schedule_lead_days_by_destination(self):
        return self.lead_days

    def upsert_observation(self, **fields):
        self.observations.append(fields)

    def latest_observations(self, canonical_destination, **window):
        self.latest_calls.append((canonical_destination, window))
        return self.rows


class FakeClient:
    def __init__(self, ports=PORTS, connections=None, sailings=None, fail=()):
        self._ports = ports
        self._connections = connections or {}
        self._sailings = sailings or {}
        self._fail = set(fail)
        self.trip_calls = []
        self.closed = False
        self.last_payload = None

    def ports(self):
        return self._ports

    def direct_connections(self, port):
        return [FerryPort(name) for name in self._connections.get(port, [])]

    def trips(self, departure_port, arrival_port, travel_date):
        self.trip_calls.append((departure_port, arrival_port, travel_date))
        if departure_port in self._fail or "all" in self._fail:
            raise McpError("server said no")
        count = self._sailings.get(departure_port, 1)
        self.last_payload = {"trips": count}
        return [trip(55.0) for _ in range(count)]

    def close(self):
        self.closed = True


def service(repository, client, **kwargs):
    return FerryDemandService(repository, lambda: client, tracked_check_in=lambda day: day, **kwargs)


def test_profile_keeps_the_busiest_inbound_routes_gateways_first():
    repository = FakeRepository(destinations=[{"canonical_destination": "rodos", "city": "Ρόδος", "latitude": 36.44, "longitude": 28.22}])
    client = FakeClient(
        connections={"Rhodes": ["Kos", "Symi", "Piraeus", "Kalymnos", "Marmaris", "Halki"]},
        sailings={"Kos": 3, "Symi": 2, "Piraeus": 1, "Kalymnos": 1, "Marmaris": 2, "Halki": 0},
    )

    assert service(repository, client).resolve_profiles(TODAY) == 1

    destination, fields = repository.profiles[0]
    assert destination == "rodos"
    assert fields["status"] == "ferry" and fields["island_port"] == "Rhodes" and fields["matched_by"] == "name"
    assert fields["routes"] == [
        ("Piraeus", "Rhodes", 1),
        ("Kos", "Rhodes", 3),
        ("Marmaris", "Rhodes", 2),
        ("Symi", "Rhodes", 2),
    ]
    assert len(fields["routes"]) == MAX_ROUTES_PER_DESTINATION
    sample = client.trip_calls[0][2]
    assert sample.weekday() == 4 and sample >= TODAY + timedelta(days=7)
    assert client.closed


def test_profile_of_a_mainland_hotel_is_no_ferry():
    repository = FakeRepository(destinations=[{"canonical_destination": "peiraias", "city": "Πειραιάς", "latitude": 37.945, "longitude": 23.640}])

    service(repository, FakeClient()).resolve_profiles(TODAY)

    assert repository.profiles[0][1]["status"] == "no_ferry"


def test_a_failed_lookup_is_stored_as_error_for_a_retry():
    class Down(FakeClient):
        def ports(self):
            raise McpError("HTTP 403")

    repository = FakeRepository(destinations=[{"canonical_destination": "rodos", "city": "Ρόδος"}])

    service(repository, Down()).resolve_profiles(TODAY)

    assert repository.profiles == [("rodos", {"status": "error", "detail": "HTTP 403"})]


def test_off_season_ranking_keeps_the_gateways_unranked():
    repository = FakeRepository(destinations=[{"canonical_destination": "rodos", "city": "Ρόδος", "latitude": 36.44, "longitude": 28.22}])
    client = FakeClient(connections={"Rhodes": ["Kos", "Piraeus"]}, sailings={"Kos": 0, "Piraeus": 0})

    service(repository, client).resolve_profiles(TODAY)

    assert repository.profiles[0][1]["routes"] == [("Piraeus", "Rhodes", None)]


def test_no_lookup_and_no_session_when_nothing_is_missing():
    opened = []
    FerryDemandService(FakeRepository(), lambda: opened.append(1)).resolve_profiles(TODAY)

    assert opened == []


ROUTES = [
    {"id": ROUTE_A, "canonical_destination": "rodos", "origin_port": "Piraeus", "destination_port": "Rhodes"},
    {"id": ROUTE_B, "canonical_destination": "rodos", "origin_port": "Kos", "destination_port": "Rhodes"},
]


def test_collection_saves_one_observation_per_route_and_date_within_budget():
    repository = FakeRepository(routes=ROUTES, lead_days={"rodos": {33}})
    client = FakeClient(sailings={"Piraeus": 1, "Kos": 3})

    report = service(repository, client, daily_days=2, horizon_days=7, max_calls_per_run=8).collect(TODAY)

    # 8 calls / 2 routes = 4 dates: the schedule's arrival (33 days out) and its eve first.
    arrival = TODAY + timedelta(days=33)
    dates = sorted({call[2] for call in client.trip_calls})
    assert dates == [date(2026, 10, 12), date(2026, 10, 13), arrival - timedelta(days=1), arrival]
    assert (report.calls, report.saved, report.failed, report.aborted) == (8, 8, 0, False)
    first = repository.observations[0]
    assert first["observed_on"] == TODAY and first["parser_version"] == PARSER_VERSION
    assert first["raw_payload"] == {"trips": 1}
    assert {obs["sailings"] for obs in repository.observations} == {1, 3}
    assert client.closed


def test_collection_stops_when_the_server_keeps_failing():
    repository = FakeRepository(routes=ROUTES)
    client = FakeClient(fail={"all"})

    report = service(repository, client, daily_days=14, horizon_days=14).collect(TODAY)

    assert report.aborted is True
    assert report.calls == MAX_CONSECUTIVE_FAILURES
    assert repository.observations == []
    assert client.closed


def test_one_failing_route_does_not_stop_the_others():
    repository = FakeRepository(routes=ROUTES)
    client = FakeClient(fail={"Kos"})

    report = service(repository, client, daily_days=3, horizon_days=3).collect(TODAY)

    assert (report.saved, report.failed, report.aborted) == (3, 3, False)


def test_load_signal_reads_the_horizon_and_the_check_in():
    repository = FakeRepository(rows=[row(ROUTE_A, CHECK_IN)])

    signal = load_ferry_signal(repository, "rodos", CHECK_IN, today=TODAY, horizon_days=60)

    assert signal.level == "normal"
    _, window = repository.latest_calls[0]
    assert window == {
        "date_from": TODAY,
        "date_to": TODAY + timedelta(days=61),
        "observed_since": TODAY - timedelta(days=3),
    }
    assert load_ferry_signal(FakeRepository(), "rodos", CHECK_IN, today=TODAY, horizon_days=60) is None
