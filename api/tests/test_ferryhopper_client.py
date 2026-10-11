"""FerryhopperClient: tool discovery, argument mapping and tolerant answer parsing.

The answer shapes here are plausible forms, not recorded Ferryhopper answers:
scripts/probe_ferryhopper.py records the live ones.
"""

from datetime import date

import pytest

from api.services.ferryhopper_client import (
    FerryhopperClient,
    FerryPort,
    map_arguments,
    parse_ports,
    parse_trips,
)
from api.services.mcp_http_client import McpError


class FakeMcp:
    def __init__(self, tools, answers):
        self.tools = tools
        self.answers = answers
        self.calls = []
        self.closed = False

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.answers[name]

    def close(self):
        self.closed = True


TRIP_SCHEMA = {
    "type": "object",
    "properties": {
        "departureLocation": {"type": "string"},
        "arrivalLocation": {"type": "string"},
        "date": {"type": "string"},
        "passengers": {"type": "integer"},
    },
    "required": ["departureLocation", "arrivalLocation", "date", "passengers"],
}


def test_arguments_follow_the_tool_schema_names():
    arguments = map_arguments(TRIP_SCHEMA, {"departure": "Piraeus", "arrival": "Rhodes", "date": "2026-11-13"})

    assert arguments == {
        "departureLocation": "Piraeus",
        "arrivalLocation": "Rhodes",
        "date": "2026-11-13",
        "passengers": 1,
    }


def test_a_port_list_argument_gets_a_one_item_list():
    schema = {"properties": {"ports": {"type": "array", "items": {"type": "string"}}}, "required": ["ports"]}

    assert map_arguments(schema, {"port": "Rhodes"}) == {"ports": ["Rhodes"]}


def test_without_a_schema_the_documented_names_are_used():
    assert map_arguments({}, {"departure": "A", "arrival": "B", "date": "2026-11-13"}) == {
        "departurePort": "A",
        "arrivalPort": "B",
        "date": "2026-11-13",
    }


def test_ports_parse_from_objects_with_nested_coordinates_or_from_names():
    payload = {
        "ports": [
            {"name": "Rhodes", "code": "RHO", "location": {"lat": 36.45, "lng": 28.23}, "country": {"name": "Greece"}},
            {"portName": "Piraeus", "latitude": "37.94", "longitude": "23.64"},
            {"no_name": True},
        ]
    }

    assert parse_ports(payload) == [
        FerryPort(name="Rhodes", code="RHO", latitude=36.45, longitude=28.23, country="Greece"),
        FerryPort(name="Piraeus", code=None, latitude=37.94, longitude=23.64, country=None),
    ]
    assert parse_ports({"destinations": ["Kos", "Symi"]}) == [FerryPort("Kos"), FerryPort("Symi")]


def test_trips_parse_price_availability_operator_and_directness():
    payload = {
        "data": {
            "trips": [
                {
                    "departureTime": "2026-11-13T17:30:00",
                    "company": {"name": "Blue Star Ferries"},
                    "price": {"amount": 62.5, "currency": "EUR"},
                    "available": True,
                },
                {
                    "departure": {"dateTime": "2026-11-13T21:00:00"},
                    "operator": "Seajets",
                    "lowestPrice": "€ 89,00",
                    "soldOut": True,
                },
                {
                    "segments": [{"departureTime": "08:00", "company": "A"}, {"departureTime": "12:00"}],
                    "priceCents": 7400,
                },
                {"departureTime": "2026-11-13T23:00", "price": 40, "currency": "TRY"},
            ]
        }
    }

    trips = parse_trips(payload)

    assert [(trip.operator, trip.price_eur, trip.available, trip.direct) for trip in trips] == [
        ("Blue Star Ferries", 62.5, True, None),
        ("Seajets", 89.0, False, None),
        ("A", 74.0, None, False),
        (None, None, None, None),  # a fare in lira is not read as euros
    ]
    assert trips[1].departure == "2026-11-13T21:00:00"


def test_a_list_of_days_holding_trips_is_not_read_as_trips():
    payload = {"days": [{"date": "2026-11-13", "trips": [{"departureTime": "17:30", "price": 50}]}]}

    trips = parse_trips(payload)

    assert len(trips) == 1 and trips[0].price_eur == 50.0


def test_unreadable_answers_give_nothing_not_guesses():
    assert parse_trips("Sorry, no trips found.") == []
    assert parse_trips({"message": "none"}) == []
    assert parse_ports(None) == []


def test_client_discovers_the_renamed_tool_and_paces_calls():
    mcp = FakeMcp(
        tools=[{"name": "search_ferry_trips", "inputSchema": TRIP_SCHEMA}, {"name": "get_ports"}],
        answers={"search_ferry_trips": {"structuredContent": {"trips": [{"departureTime": "17:30", "price": 50}]}}},
    )
    sleeps = []
    now = iter([0.0, 0.2, 0.3, 1.5])
    client = FerryhopperClient(mcp, min_interval_seconds=1.0, sleep=sleeps.append, clock=lambda: next(now))

    first = client.trips("Piraeus", "Rhodes", date(2026, 11, 13))
    client.trips("Piraeus", "Rhodes", date(2026, 11, 14))

    assert first[0].price_eur == 50.0
    assert [name for name, _ in mcp.calls] == ["search_ferry_trips", "search_ferry_trips"]
    assert mcp.calls[1][1]["date"] == "2026-11-14"
    assert sleeps == [pytest.approx(0.8)]
    assert client.last_payload == {"trips": [{"departureTime": "17:30", "price": 50}]}


def test_direct_connections_drop_the_port_itself():
    mcp = FakeMcp(
        tools=[{"name": "get_direct_connections_for_ports", "inputSchema": {"properties": {"departurePort": {"type": "string"}}}}],
        answers={"get_direct_connections_for_ports": {"content": [{"type": "text", "text": '["Rhodes", "Piraeus", "Kos"]'}]}},
    )

    ports = FerryhopperClient(mcp, min_interval_seconds=0).direct_connections("Rhodes")

    assert [port.name for port in ports] == ["Piraeus", "Kos"]
    assert mcp.calls == [("get_direct_connections_for_ports", {"departurePort": "Rhodes"})]


def test_a_missing_tool_names_what_the_server_has():
    client = FerryhopperClient(FakeMcp(tools=[{"name": "trip_details"}], answers={}), min_interval_seconds=0)

    with pytest.raises(McpError, match="trip_details"):
        client.ports()
