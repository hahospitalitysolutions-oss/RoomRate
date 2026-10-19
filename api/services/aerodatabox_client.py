"""AeroDataBox (RapidAPI): scheduled arrivals at an island airport.

One call = one airport and one local-time window of at most 12 hours. It
costs API units: 2 on this endpoint (probe 2026-10-11: 9 calls, 16 units),
against 400 a month on the free plan and 5,000 on Pro. FlightDemandService
keeps the collection within FLIGHT_DEMAND_MONTHLY_UNITS; the client reports
the units the plan has left after every call.

The answer's shape is read tolerantly (``movement`` or ``departure`` for
the flight's origin); ``scripts/run_flight_demand.py --day`` prints what was
read, to check it against the live answers.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
# Arrivals only, no cargo, private or codeshare duplicates: each row is one
# aircraft bringing passengers.
QUERY = {
    "withLeg": "false",
    "direction": "Arrival",
    "withCancelled": "false",
    "withCodeshared": "false",
    "withCargo": "false",
    "withPrivate": "false",
    "withLocation": "false",
}
_CANCELLED = {"canceled", "cancelled", "canceleduncertain"}


class AeroDataBoxError(RuntimeError):
    """The call failed; the collection stops after a few in a row."""


class QuotaExhausted(AeroDataBoxError):
    """The plan's units are spent (HTTP 429): nothing more this period."""


class AeroDataBoxAuthError(AeroDataBoxError):
    """The key is wrong or not subscribed (HTTP 401/403): every call would fail."""


@dataclass(frozen=True)
class Arrival:
    number: str | None
    airline: str | None
    origin_iata: str | None
    origin_name: str | None
    scheduled_local: str | None


@dataclass(frozen=True)
class ArrivalsWindow:
    arrivals: list[Arrival]
    # The plan's units left after this call (None when the header is absent).
    units_remaining: int | None
    payload: Any


class AeroDataBoxClient:
    def __init__(
        self,
        api_key: str,
        *,
        host: str = RAPIDAPI_HOST,
        timeout_seconds: float = 30.0,
        min_interval_seconds: float = 1.5,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._api_key = api_key
        self._host = host
        self._http = http if http is not None else httpx.Client(timeout=timeout_seconds)
        self._owns_http = http is None
        # The free plan allows about one request a second.
        self._min_interval = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_call_at: float | None = None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def arrivals(self, iata: str, travel_date: date, start_hour: int, end_hour: int) -> ArrivalsWindow:
        """Scheduled arrivals at ``iata`` between the local hours (end 24 = 23:59)."""
        start = f"{travel_date.isoformat()}T{start_hour:02d}:00"
        end = f"{travel_date.isoformat()}T{'23:59' if end_hour >= 24 else f'{end_hour:02d}:00'}"
        url = f"https://{self._host}/flights/airports/iata/{iata}/{start}/{end}"
        if self._last_call_at is not None:
            wait = self._min_interval - (self._clock() - self._last_call_at)
            if wait > 0:
                self._sleep(wait)
        self._last_call_at = self._clock()
        try:
            response = self._http.get(
                url,
                params=QUERY,
                headers={"x-rapidapi-key": self._api_key, "x-rapidapi-host": self._host},
            )
        except httpx.HTTPError as exc:
            raise AeroDataBoxError(f"{iata} {travel_date}: {type(exc).__name__}: {exc}") from exc
        remaining = _units_remaining(response.headers)
        if response.status_code == 204:
            return ArrivalsWindow([], remaining, None)
        if response.status_code == 429:
            raise QuotaExhausted(f"{iata} {travel_date}: the plan's units are spent (HTTP 429)")
        if response.status_code in (401, 403):
            raise AeroDataBoxAuthError(
                f"{iata} {travel_date}: HTTP {response.status_code}: the key is wrong or not subscribed to AeroDataBox"
            )
        if response.status_code >= 400:
            raise AeroDataBoxError(f"{iata} {travel_date}: HTTP {response.status_code}: {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AeroDataBoxError(f"{iata} {travel_date}: answer is not JSON") from exc
        return ArrivalsWindow(parse_arrivals(payload), remaining, payload)


def parse_arrivals(payload: Any) -> list[Arrival]:
    """The passenger arrivals in a FIDS answer; cancelled, cargo and codeshare rows left out."""
    items = payload.get("arrivals") if isinstance(payload, dict) else None
    arrivals = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("isCargo") is True:
            continue
        if str(item.get("codeshareStatus") or "").casefold() == "iscodeshared":
            continue
        if str(item.get("status") or "").replace(" ", "").casefold() in _CANCELLED:
            continue
        # withLeg=false: the other end of the flight is "movement"; with legs
        # it is "departure".
        origin = _dict(item.get("movement")) or _dict(item.get("departure"))
        airport = _dict(origin.get("airport"))
        scheduled = _dict(origin.get("scheduledTime")) or _dict(_dict(item.get("arrival")).get("scheduledTime"))
        arrivals.append(
            Arrival(
                number=_text(item.get("number")),
                airline=_text(_dict(item.get("airline")).get("name")),
                origin_iata=_text(airport.get("iata")),
                origin_name=_text(airport.get("name")) or _text(airport.get("shortName")),
                scheduled_local=_text(scheduled.get("local")) or _text(origin.get("scheduledTimeLocal")),
            )
        )
    return arrivals


def slim_payload(payload: Any) -> Any:
    """The answer with only the fields parse_arrivals reads, for re-parsing later.

    A summer 12-hour window at Rhodes is ~100 flights; the full answer would
    pass the stored-payload cap and be cut.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("arrivals"), list):
        return payload

    def end(value: Any) -> dict[str, Any]:
        movement = _dict(value)
        airport = _dict(movement.get("airport"))
        return {
            "airport": {key: airport[key] for key in ("iata", "name", "shortName") if key in airport},
            "scheduledTime": {"local": _dict(movement.get("scheduledTime")).get("local")},
        }

    flights = []
    for item in payload["arrivals"]:
        if not isinstance(item, dict):
            continue
        slim = {key: item[key] for key in ("number", "status", "codeshareStatus", "isCargo") if key in item}
        slim["airline"] = {"name": _dict(item.get("airline")).get("name")}
        for key in ("movement", "departure", "arrival"):
            if key in item:
                slim[key] = end(item[key])
        flights.append(slim)
    return {"arrivals": flights}


def _units_remaining(headers: httpx.Headers) -> int | None:
    value = headers.get("x-ratelimit-api-units-remaining")
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, (str, int)) and str(value).strip() else None
