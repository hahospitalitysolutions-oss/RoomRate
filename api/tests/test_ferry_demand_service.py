"""Ferry demand: port lookup, route ranking, collection budget and the signal rules."""

import pytest
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
from api.services.ferry_demand_service import is_port_group, port_candidates
from api.services.ferryhopper_client import FerryPort, FerryTrip
from api.services.greek_islands import ISLANDS, island_at
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


def trip(price=50.0, available=None, direct=None, operator="Blue Star", options=None):
    return FerryTrip(
        departure="17:30", operator=operator, price_eur=price, available=available, direct=direct,
        accommodation_options=options,
    )


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
        assert (match.port.name, match.matched_by, reason) == ("RODOS", "island", "")


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
        "accommodation_options": None,
    }


def test_aggregate_of_nothing_is_zero_with_unknown_fares():
    assert aggregate_trips([]) == {
        "sailings": 0,
        "availability_known": 0,
        "sold_out": 0,
        "min_price_eur": None,
        "median_price_eur": None,
        "operators": 0,
        "accommodation_options": None,
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
    # The dearer of the two window days: 72 € against the usual 60 €.
    assert signal.price_vs_usual_pct == 20.0
    assert "+20% από το συνηθισμένο" in signal.summary


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


def test_no_sailing_into_the_port_means_no_ferry_until_the_monthly_refresh():
    """Salamina: Ferryhopper lists MEGARA, PERAMA → FANEROMENI, SALAMINA but sells no sailing."""
    repository = FakeRepository(destinations=[{"canonical_destination": "rodos", "city": "Ρόδος", "latitude": 36.44, "longitude": 28.22}])
    client = FakeClient(connections={"Rhodes": ["Kos", "Piraeus"]}, sailings={"Kos": 0, "Piraeus": 0})

    service(repository, client).resolve_profiles(TODAY)

    fields = repository.profiles[0][1]
    assert fields["status"] == "no_ferry"
    assert "routes" not in fields
    assert "δεν δείχνει δρομολόγια προς Rhodes" in fields["detail"] and "30 μέρες" in fields["detail"]


def test_failed_route_searches_are_an_error_retried_soon_not_a_month_of_no_ferry():
    repository = FakeRepository(destinations=[{"canonical_destination": "rodos", "city": "Ρόδος", "latitude": 36.44, "longitude": 28.22}])
    client = FakeClient(connections={"Rhodes": ["Kos", "Piraeus"]}, sailings={"Kos": 0}, fail={"Piraeus"})

    service(repository, client).resolve_profiles(TODAY)

    destination, fields = repository.profiles[0]
    assert fields["status"] == "error"
    assert "route searches into Rhodes failed" in fields["detail"]


def test_a_destination_where_nothing_sails_has_an_unknown_signal():
    rows = [row(ROUTE_A, day, sailings=0, price=None) for day in usual_dates()]
    rows += [row(ROUTE_A, CHECK_IN, sailings=0, price=None)]

    signal = build_ferry_demand_signal(rows, CHECK_IN, TODAY)

    assert signal.level == "unknown"
    assert "δεν δείχνει δρομολόγια" in signal.summary


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



# --- Islands from the property's coordinates (Ferryhopper ports have none) ---


@pytest.mark.parametrize(
    ("place", "latitude", "longitude", "island"),
    [
        ("Faliraki", 36.340, 28.202, "Ρόδος"),
        ("Lindos", 36.092, 28.087, "Ρόδος"),
        ("Oia", 36.462, 25.376, "Σαντορίνη"),
        ("Kardamena", 36.785, 27.140, "Κως"),
        ("Platis Gialos", 37.413, 25.347, "Μύκονος"),
        ("Paloukia", 37.958, 23.505, "Σαλαμίνα"),
        ("Vathy, Ithaca (inside Kefalonia's box too)", 38.365, 20.718, "Ιθάκη"),
        ("Argostoli", 38.176, 20.489, "Κεφαλονιά"),
        ("Chania", 35.514, 24.018, "Χανιά"),
        ("Hersonissos", 35.316, 25.390, "Ηράκλειο"),
        ("Kassiopi, Corfu", 39.788, 19.920, "Κέρκυρα"),
    ],
)
def test_island_from_coordinates(place, latitude, longitude, island):
    assert island_at(latitude, longitude).label == island, place


@pytest.mark.parametrize(
    ("place", "latitude", "longitude"),
    [
        ("Athens", 37.984, 23.728),
        ("Perama, across the strait from Salamina", 37.965, 23.575),
        ("Nafplio", 37.567, 22.806),
        ("Lefkada town (bridged)", 38.830, 20.705),
        ("Chalkida, Evia (bridged)", 38.463, 23.595),
    ],
)
def test_mainland_and_bridged_places_are_on_no_island(place, latitude, longitude):
    assert island_at(latitude, longitude) is None, place


def test_island_boxes_are_well_formed():
    for island in ISLANDS:
        assert island.lat_min < island.lat_max and island.lon_min < island.lon_max, island.label
        assert 34.5 < island.lat_min and island.lat_max < 41.0, island.label
        assert 19.0 < island.lon_min and island.lon_max < 30.0, island.label
        assert island.names and all(name == latin(name) for name in island.names), island.label


FERRYHOPPER_PORTS = [
    FerryPort("PIRAEUS", "PIR", country="Greece"),
    FerryPort("ATHENS (ALL PORTS)", "ATH", country="Greece"),
    FerryPort("RODOS", "RHO", country="Greece"),
    FerryPort("RODOS (ALL PORTS)", "RHO00", country="Greece"),
    FerryPort("KEFALONIA (ALL PORTS)", "KEF00", country="Greece"),
    FerryPort("SAMI (KEFALONIA)", "SAM", country="Greece"),
    FerryPort("NAFPLIO", "NAF", country="Greece"),
]


def test_a_village_hotel_finds_its_island_port():
    candidates, reason = port_candidates(FERRYHOPPER_PORTS, ["Φαληράκι", "Faliraki", "faliraki"], 36.340, 28.202)

    assert reason == ""
    # The port named exactly after the island before its "ALL PORTS" group.
    assert [match.port.name for match in candidates] == ["RODOS", "RODOS (ALL PORTS)"]
    assert candidates[0].matched_by == "island"


def test_a_multi_port_island_prefers_its_port_group():
    candidates, _ = port_candidates(FERRYHOPPER_PORTS, ["Αργοστόλι"], 38.176, 20.489)

    assert [match.port.name for match in candidates] == ["KEFALONIA (ALL PORTS)", "SAMI (KEFALONIA)"]


def test_a_mainland_port_town_is_not_an_island():
    candidates, reason = port_candidates(FERRYHOPPER_PORTS, ["Ναύπλιο", "Nafplio"], 37.567, 22.806)

    assert candidates == []
    assert "δεν είναι σε νησί" in reason


def test_an_island_missing_from_the_port_list_says_so():
    candidates, reason = port_candidates(FERRYHOPPER_PORTS, ["Σαλαμίνα"], 37.958, 23.505)

    assert candidates == []
    assert "Σαλαμίνα" in reason


def test_port_groups_are_recognised():
    assert is_port_group("ATHENS (ALL PORTS)")
    assert not is_port_group("PIRAEUS")


def test_profile_skips_port_groups_as_origins_and_ranks_past_the_alphabet():
    """Rhodes has ~50 connections: Kos and Symi must be ranked, ATHENS (ALL PORTS) never counted."""
    alphabet = [f"A{index:02d}" for index in range(30)]
    repository = FakeRepository(destinations=[{"canonical_destination": "faliraki", "city": "Φαληράκι",
                                               "latitude": 36.340, "longitude": 28.202}])
    client = FakeClient(
        ports=FERRYHOPPER_PORTS,
        connections={"RODOS": ["ATHENS (ALL PORTS)", "PIRAEUS", *alphabet, "KOS", "SYMI"]},
        sailings={"PIRAEUS": 2, "KOS": 5, "SYMI": 4, **{name: 0 for name in alphabet}},
    )

    service(repository, client).resolve_profiles(TODAY)

    fields = repository.profiles[0][1]
    assert (fields["status"], fields["island_port"], fields["matched_by"]) == ("ferry", "RODOS", "island")
    assert fields["routes"] == [("PIRAEUS", "RODOS", 2), ("KOS", "RODOS", 5), ("SYMI", "RODOS", 4)]
    assert "ATHENS (ALL PORTS)" not in {origin for origin, _, _ in client.trip_calls}


def test_a_port_group_the_search_does_not_take_falls_back_to_a_real_port():
    repository = FakeRepository(destinations=[{"canonical_destination": "argostoli", "city": "Αργοστόλι",
                                               "latitude": 38.176, "longitude": 20.489}])

    class GroupBlind(FakeClient):
        def trips(self, departure_port, arrival_port, travel_date):
            if "ALL PORTS" in arrival_port:
                return []
            self.trip_calls.append((departure_port, arrival_port, travel_date))
            return [trip(40.0)]

    client = GroupBlind(ports=FERRYHOPPER_PORTS,
                        connections={"KEFALONIA (ALL PORTS)": ["PATRA"], "SAMI (KEFALONIA)": ["PATRA"]})

    service(repository, client).resolve_profiles(TODAY)

    fields = repository.profiles[0][1]
    assert fields["island_port"] == "SAMI (KEFALONIA)"
    assert fields["routes"] == [("PATRA", "SAMI (KEFALONIA)", 1)]


# --- Offered accommodations: Ferryhopper's filling-up signal ---


def test_offered_accommodations_are_summed_only_when_every_sailing_lists_them():
    assert aggregate_trips([trip(options=20), trip(options=6)])["accommodation_options"] == 26
    assert aggregate_trips([trip(options=20), trip()])["accommodation_options"] is None


def test_fewer_offered_accommodations_than_usual_make_demand_high():
    usual = [dict(row(ROUTE_A, day, sailings=2), accommodation_options=44) for day in usual_dates()]
    filling = usual + [
        dict(row(ROUTE_A, CHECK_IN - timedelta(days=1), sailings=2), accommodation_options=20),
        dict(row(ROUTE_A, CHECK_IN, sailings=2), accommodation_options=24),
    ]

    signal = build_ferry_demand_signal(filling, CHECK_IN, TODAY)

    # The fuller of the two window days: 20 offered against the usual 44.
    assert signal.options_vs_usual_pct == -54.5
    assert signal.level == "high"
    assert "θέσεις/καμπίνες προς πώληση -54% από το συνηθισμένο" in signal.summary

    steady = usual + [
        dict(row(ROUTE_A, CHECK_IN, sailings=2), accommodation_options=40),
    ]
    assert build_ferry_demand_signal(steady, CHECK_IN, TODAY).level == "normal"


def test_a_busy_day_trip_route_does_not_drown_the_route_that_brings_stays():
    """Symi → Rhodes (6 a day, flat) next to Piraeus → Rhodes doubling its 2 sailings."""
    symi = [dict(row(ROUTE_B, day, sailings=6, origin="SYMI")) for day in usual_dates()]
    piraeus = [row(ROUTE_A, day, sailings=2, origin="PIRAEUS") for day in usual_dates()]
    window = [
        row(ROUTE_B, CHECK_IN - timedelta(days=1), sailings=6, origin="SYMI"),
        row(ROUTE_B, CHECK_IN, sailings=6, origin="SYMI"),
        row(ROUTE_A, CHECK_IN - timedelta(days=1), sailings=4, origin="PIRAEUS"),
        row(ROUTE_A, CHECK_IN, sailings=4, origin="PIRAEUS"),
    ]

    signal = build_ferry_demand_signal(symi + piraeus + window, CHECK_IN, TODAY)

    # Summed, 20 against 16 would read +25% (normal); per route it is 0% and +100%.
    assert signal.sailings_vs_usual_pct == 50.0
    assert signal.level == "high"
    assert signal.routes == ["PIRAEUS → Rhodes", "SYMI → Rhodes"]


def test_routes_weigh_by_what_they_usually_carry():
    """The live Rhodes case: Friday's Blue Star from Piraeus fills, Symi's day boats do not."""
    def piraeus(day, price=46.5, options=44):
        return dict(row(ROUTE_A, day, sailings=2, price=price, origin="PIRAEUS"), accommodation_options=options)

    def symi(day):
        return dict(row(ROUTE_B, day, sailings=6, price=20.0, origin="SYMI"), accommodation_options=12)

    usual = [piraeus(day) for day in usual_dates()] + [symi(day) for day in usual_dates()]
    friday = CHECK_IN - timedelta(days=1)
    window = [piraeus(friday, price=58.0, options=26), piraeus(CHECK_IN), symi(friday), symi(CHECK_IN)]

    signal = build_ferry_demand_signal(usual + window, CHECK_IN, TODAY)

    # Piraeus carries 44 a day against Symi's 12: (+24.7% × 44 + 0 × 12) / 56.
    assert signal.price_vs_usual_pct == 19.4
    assert signal.options_vs_usual_pct == -32.1
    assert signal.level == "high"
