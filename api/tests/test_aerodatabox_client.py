"""AeroDataBox client: the request it sends, the answers it reads, the errors it raises."""

from datetime import date

import httpx
import pytest

from api.services.aerodatabox_client import (
    QUERY,
    AeroDataBoxAuthError,
    AeroDataBoxClient,
    AeroDataBoxError,
    QuotaExhausted,
    parse_arrivals,
    slim_payload,
)

DAY = date(2026, 10, 17)


def flight(number, airline, iata, name, *, status="Expected", codeshare="IsOperator", cargo=False, key="movement"):
    return {
        "number": number,
        "status": status,
        "codeshareStatus": codeshare,
        "isCargo": cargo,
        "airline": {"name": airline},
        key: {
            "airport": {"iata": iata, "name": name},
            "scheduledTime": {"local": f"{DAY}T10:15+03:00", "utc": f"{DAY}T07:15Z"},
        },
    }


PAYLOAD = {
    "arrivals": [
        flight("A3 200", "Aegean", "ATH", "Athens"),
        flight("EZY 8721", "easyJet", "LGW", "London Gatwick"),
        flight("OA 200", "Olympic", "ATH", "Athens", codeshare="IsCodeshared"),
        flight("X3 100", "TUIfly", "HAJ", "Hannover", status="Canceled"),
        flight("QY 1", "DHL", "LEJ", "Leipzig", cargo=True),
        flight("LS 1771", "Jet2", "MAN", "Manchester", key="departure"),
    ]
}


def test_parse_keeps_passenger_arrivals_only():
    arrivals = parse_arrivals(PAYLOAD)

    assert [arrival.number for arrival in arrivals] == ["A3 200", "EZY 8721", "LS 1771"]
    assert arrivals[1].origin_iata == "LGW" and arrivals[1].origin_name == "London Gatwick"
    assert arrivals[1].airline == "easyJet"
    assert arrivals[1].scheduled_local == f"{DAY}T10:15+03:00"
    # With legs the other end is "departure".
    assert arrivals[2].origin_iata == "MAN"


def test_the_stored_answer_keeps_what_the_parser_reads():
    noisy = {"arrivals": [{**item, "aircraft": {"model": "A320", "reg": "SX-DGA"}, "greatCircleDistance": {"km": 400}}
                          for item in PAYLOAD["arrivals"]]}

    slim = slim_payload(noisy)

    assert parse_arrivals(slim) == parse_arrivals(PAYLOAD)
    assert "aircraft" not in slim["arrivals"][0]
    assert slim_payload(None) is None


@pytest.mark.parametrize("payload", [None, [], {}, {"arrivals": None}, {"arrivals": ["x", 1]}])
def test_parse_tolerates_odd_answers(payload):
    assert parse_arrivals(payload) == []


def client_for(handler, **kwargs):
    return AeroDataBoxClient("secret-key", http=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_request_is_one_airport_one_window_with_the_rapidapi_headers():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=PAYLOAD, headers={"x-ratelimit-api-units-remaining": "382"})

    window = client_for(handler).arrivals("RHO", DAY, 8, 20)

    request = seen[0]
    assert request.url.path == "/flights/airports/iata/RHO/2026-10-17T08:00/2026-10-17T20:00"
    assert dict(request.url.params) == QUERY
    assert request.headers["x-rapidapi-key"] == "secret-key"
    assert request.headers["x-rapidapi-host"] == "aerodatabox.p.rapidapi.com"
    assert len(window.arrivals) == 3
    assert window.units_remaining == 382
    assert window.payload == PAYLOAD


def test_a_window_to_midnight_ends_at_23_59():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(204)

    window = client_for(handler).arrivals("JTR", DAY, 12, 24)

    assert seen == ["/flights/airports/iata/JTR/2026-10-17T12:00/2026-10-17T23:59"]
    # 204: no flights in the window.
    assert window.arrivals == [] and window.units_remaining is None


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, QuotaExhausted), (401, AeroDataBoxAuthError), (403, AeroDataBoxAuthError), (500, AeroDataBoxError)],
)
def test_http_errors(status, error):
    client = client_for(lambda request: httpx.Response(status, text="nope"))

    with pytest.raises(error):
        client.arrivals("RHO", DAY, 8, 20)


def test_the_key_never_appears_in_an_error():
    client = client_for(lambda request: httpx.Response(401, text="Invalid API key secret-key"))

    with pytest.raises(AeroDataBoxAuthError) as raised:
        client.arrivals("RHO", DAY, 8, 20)

    assert "secret-key" not in str(raised.value)


def test_network_errors_and_non_json_are_aerodatabox_errors():
    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(AeroDataBoxError):
        client_for(down).arrivals("RHO", DAY, 8, 20)
    with pytest.raises(AeroDataBoxError):
        client_for(lambda request: httpx.Response(200, text="<html>")).arrivals("RHO", DAY, 8, 20)


def test_calls_are_paced():
    sleeps = []
    now = [100.0]
    client = client_for(
        lambda request: httpx.Response(204),
        min_interval_seconds=1.5,
        sleep=lambda seconds: sleeps.append(seconds),
        clock=lambda: now[0],
    )

    client.arrivals("RHO", DAY, 8, 20)
    now[0] += 0.5
    client.arrivals("RHO", DAY, 8, 20)

    assert sleeps == [1.0]
