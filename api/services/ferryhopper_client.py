"""Ferryhopper's public MCP server as RoomRate's ferry data source.

The server (FERRYHOPPER_MCP_URL, https://mcp.ferryhopper.com/mcp) is built for
AI assistants: its tool names have changed over time (search_trips_v2 became
search_ferry_trips) and its answers are not a published contract. So:

* the tools are discovered at runtime, taking the first known name present;
* arguments are mapped by meaning (departure, arrival, date, port) onto the
  property names of the tool's own ``inputSchema``;
* answers are read tolerantly: ``structuredContent`` when present, else the
  text content as JSON; trips and ports are found by their fields.

A figure the answer does not carry stays None, never a guessed number.
``scripts/probe_ferryhopper.py`` dumps the live answers next to what this
parser reads from them.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from api.services.mcp_http_client import McpError, McpHttpClient, tool_result_payload

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://mcp.ferryhopper.com/mcp"

TRIP_TOOLS = ("search_trips", "search_ferry_trips", "search_trips_v2")
PORT_TOOLS = ("get_ports", "get_global_ports")
CONNECTION_TOOLS = ("get_direct_connections_for_ports", "get_direct_connections")

# Bounded walk over an answer: deep enough for {"data": {"trips": [...]}}.
_MAX_DEPTH = 6

_DEPARTURE_KEYS = (
    "departure",
    "departuretime",
    "departuredatetime",
    "departuredate",
    "departs",
    "departureat",
    "starttime",
)
# A list of days each holding "trips" is not a trip: only segment lists count.
_SEGMENT_KEYS = ("segments", "legs")
# Ferryhopper (probe 2026-10-11): segments carry ownerCompany / marketingCompany.
_OPERATOR_KEYS = (
    "ownercompany",
    "marketingcompany",
    "company",
    "operator",
    "carrier",
    "companyname",
    "operatorname",
    "vesselcompany",
)
_PRICE_KEYS = (
    "price",
    "expectedprice",
    "lowestprice",
    "minprice",
    "fromprice",
    "pricefrom",
    "startingprice",
    "indicativeprice",
    "cheapestprice",
    "fare",
    "totalprice",
)
_PRICE_CENTS_KEYS = ("totalpriceincents", "pricecents", "priceincents", "amountincents", "cents")
# Ferryhopper prices each segment per accommodation (deck, seat, cabin…).
_ACCOMMODATION_KEYS = ("accommodations", "accommodation", "fares", "classes")
# Ferryhopper answers {"foundDirectItinerariesForTrip": [...], ...}.
_DIRECT_LIST_HINTS = ("direct", "itinerar")
_AVAILABLE_KEYS = ("available", "isavailable", "hasavailability", "bookable", "isbookable")
_SOLD_OUT_KEYS = ("soldout", "issoldout")
_NAME_KEYS = ("name", "portname", "title", "label", "displayname", "locationname")
_CODE_KEYS = ("code", "portcode", "locationcode", "abbreviation", "slug", "id")
_LAT_KEYS = ("lat", "latitude")
_LNG_KEYS = ("lng", "lon", "long", "longitude")
_GEO_KEYS = ("location", "coordinates", "geo", "position", "coords")
_COUNTRY_KEYS = ("country", "countryname", "countrycode")

_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class FerryPort:
    name: str
    code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = None


@dataclass(frozen=True)
class FerryTrip:
    departure: str | None
    operator: str | None
    price_eur: float | None
    available: bool | None
    direct: bool | None
    # Priced accommodations still offered (deck tiers, seats, cabins); a full
    # cabin or fare tier drops out of the list. None when the answer lists none.
    accommodation_options: int | None = None


class FerryhopperClient:
    """Ferry ports, direct connections and trips through Ferryhopper's MCP tools."""

    def __init__(
        self,
        mcp: McpHttpClient | None = None,
        *,
        url: str = DEFAULT_MCP_URL,
        min_interval_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._mcp = mcp if mcp is not None else McpHttpClient(url, timeout_seconds=timeout_seconds)
        self._min_interval = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_call_at: float | None = None
        self._tools: dict[str, dict[str, Any]] | None = None
        self.last_payload: Any = None

    def close(self) -> None:
        self._mcp.close()

    def __enter__(self) -> FerryhopperClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def ports(self) -> list[FerryPort]:
        tool = self._tool(PORT_TOOLS)
        return parse_ports(self._invoke(tool, self._arguments(tool, {})))

    def direct_connections(self, port: str) -> list[FerryPort]:
        """Ports with a direct sailing from ``port``."""
        tool = self._tool(CONNECTION_TOOLS)
        payload = self._invoke(tool, self._arguments(tool, {"port": port}))
        return [found for found in parse_ports(payload) if _key(found.name) != _key(port)]

    def trips(self, departure_port: str, arrival_port: str, travel_date: date) -> list[FerryTrip]:
        tool = self._tool(TRIP_TOOLS)
        arguments = self._arguments(
            tool,
            {"departure": departure_port, "arrival": arrival_port, "date": travel_date.isoformat()},
        )
        return parse_trips(self._invoke(tool, arguments))

    def tool_names(self) -> list[str]:
        return sorted(self._discover())

    def _discover(self) -> dict[str, dict[str, Any]]:
        if self._tools is None:
            self._tools = {str(tool.get("name")): tool for tool in self._mcp.list_tools() if tool.get("name")}
        return self._tools

    def _tool(self, candidates: tuple[str, ...]) -> dict[str, Any]:
        tools = self._discover()
        for name in candidates:
            if name in tools:
                return tools[name]
        raise McpError(f"Ferryhopper exposes none of {', '.join(candidates)} (has: {', '.join(sorted(tools))})")

    def _invoke(self, tool: dict[str, Any], arguments: dict[str, Any]) -> Any:
        if self._last_call_at is not None:
            wait = self._min_interval - (self._clock() - self._last_call_at)
            if wait > 0:
                self._sleep(wait)
        try:
            result = self._mcp.call_tool(str(tool["name"]), arguments)
        finally:
            self._last_call_at = self._clock()
        self.last_payload = tool_result_payload(result)
        return self.last_payload

    @staticmethod
    def _arguments(tool: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
        return map_arguments(tool.get("inputSchema") or {}, values)


def map_arguments(schema: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """Put each value under the schema property that means it.

    ``values`` keys are roles: departure, arrival, date, port. Without a
    schema the documented names are used. A role whose property is an array
    gets a one-item list. Required properties with an obvious default
    (passengers, language, currency) are filled; others are left to the
    server's error.
    """
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [str(name) for name in schema.get("required") or [] if isinstance(name, str)]
    if not properties:
        fallback = {"departure": "departurePort", "arrival": "arrivalPort", "date": "date", "port": "portLocation"}
        return {fallback[role]: value for role, value in values.items() if role in fallback}

    ordered = sorted(properties, key=lambda name: (name not in required, list(properties).index(name)))
    taken: set[str] = set()
    arguments: dict[str, Any] = {}
    for role, value in values.items():
        name = next((candidate for candidate in ordered if candidate not in taken and _means(candidate, role)), None)
        if name is None and role == "port":
            name = next(
                (
                    candidate
                    for candidate in ordered
                    if candidate not in taken and _schema_type(properties[candidate]) in ("string", "array", None)
                ),
                None,
            )
        if name is None:
            continue
        taken.add(name)
        arguments[name] = [value] if _schema_type(properties[name]) == "array" else value

    for name in required:
        if name in arguments:
            continue
        default = _default_for(name, properties.get(name) or {})
        if default is not None:
            arguments[name] = default
    return arguments


def _means(property_name: str, role: str) -> bool:
    key = _key(property_name)
    if role == "departure":
        return "depart" in key or "origin" in key or key in ("from", "fromport", "source")
    if role == "arrival":
        return "arriv" in key or "destination" in key or key in ("to", "toport")
    if role == "date":
        return "date" in key and "return" not in key
    if role == "port":
        return "port" in key or "location" in key or "depart" in key or "origin" in key
    return False


def _schema_type(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    return kind if isinstance(kind, str) else None


def _default_for(name: str, schema: dict[str, Any]) -> Any:
    key = _key(name)
    if "passeng" in key or "adult" in key:
        return 1 if _schema_type(schema) in ("integer", "number", None) else "1"
    if key in ("language", "lang", "locale"):
        return "en"
    if key == "currency":
        return "EUR"
    if "default" in schema:
        return schema["default"]
    return None


def parse_ports(payload: Any) -> list[FerryPort]:
    """Ports in a tool answer: a list of names or of port objects."""
    for items in _lists(payload):
        if items and all(isinstance(item, str) for item in items):
            return [FerryPort(name=item.strip()) for item in items if item.strip()]
        ports = [port for port in (_port(item) for item in items if isinstance(item, dict)) if port]
        if ports:
            return ports
    return []


def _port(item: dict[str, Any]) -> FerryPort | None:
    fields = _lowered(item)
    name = _first_text(fields, _NAME_KEYS)
    if not name:
        return None
    latitude, longitude = _coordinates(fields)
    country = fields.get("country")
    if isinstance(country, dict):
        country = _first_text(_lowered(country), ("name", "code"))
    elif not isinstance(country, str) or not country.strip():
        country = _first_text(fields, _COUNTRY_KEYS[1:])
    return FerryPort(
        name=name,
        code=_first_text(fields, _CODE_KEYS),
        latitude=latitude,
        longitude=longitude,
        country=country.strip() if isinstance(country, str) and country.strip() else None,
    )


def _coordinates(fields: dict[str, Any]) -> tuple[float | None, float | None]:
    latitude = _first_number(fields, _LAT_KEYS)
    longitude = _first_number(fields, _LNG_KEYS)
    if latitude is not None and longitude is not None:
        return latitude, longitude
    for key in _GEO_KEYS:
        nested = fields.get(key)
        if isinstance(nested, dict):
            inner = _lowered(nested)
            latitude = _first_number(inner, _LAT_KEYS)
            longitude = _first_number(inner, _LNG_KEYS)
            if latitude is not None and longitude is not None:
                return latitude, longitude
    return None, None


def parse_trips(payload: Any) -> list[FerryTrip]:
    """Trips in a tool answer.

    A list named for direct itineraries (Ferryhopper's
    ``foundDirectItinerariesForTrip``) is read as is, even empty: an
    indirect list next to it is not a sailing into the port. Otherwise the
    shallowest list whose objects look like trips.
    """
    if isinstance(payload, dict):
        for name, value in payload.items():
            key = _key(str(name))
            if isinstance(value, list) and all(hint in key for hint in _DIRECT_LIST_HINTS) and "indirect" not in key:
                return [_trip(item) for item in value if isinstance(item, dict)]
    for items in _lists(payload):
        dicts = [item for item in items if isinstance(item, dict)]
        if dicts and sum(1 for item in dicts if _looks_like_trip(item)) * 2 >= len(dicts):
            return [_trip(item) for item in dicts if _looks_like_trip(item)]
    return []


def _looks_like_trip(item: dict[str, Any]) -> bool:
    fields = _lowered(item)
    if any(key in fields for key in _DEPARTURE_KEYS):
        return True
    return any(isinstance(fields.get(key), list) and fields.get(key) for key in _SEGMENT_KEYS)


def _trip(item: dict[str, Any]) -> FerryTrip:
    fields = _lowered(item)
    segments = next(
        (fields[key] for key in _SEGMENT_KEYS if isinstance(fields.get(key), list) and fields[key]), None
    )
    first_segment = _lowered(segments[0]) if segments and isinstance(segments[0], dict) else {}
    direct: bool | None = None
    if segments is not None:
        direct = len(segments) == 1
    else:
        for key in ("transfers", "stops", "changes", "numberoftransfers"):
            count = _number(fields.get(key))
            if count is not None:
                direct = count == 0
                break
        if direct is None and isinstance(fields.get("direct"), bool):
            direct = fields["direct"]

    price = _price(fields)
    if price is None and segments:
        segment_prices = [_segment_price(segment) for segment in segments if isinstance(segment, dict)]
        if segment_prices and all(value is not None for value in segment_prices):
            price = round(sum(segment_prices), 2)

    options = [_offered(segment) for segment in segments or [] if isinstance(segment, dict)]
    known_options = [count for count in options if count is not None]

    return FerryTrip(
        departure=_departure(fields) or _departure(first_segment),
        operator=_operator(fields) or _operator(first_segment),
        price_eur=price,
        available=_availability(fields),
        direct=direct,
        # A connection is as open as its fullest leg.
        accommodation_options=min(known_options) if known_options else None,
    )


def _offered(segment: dict[str, Any]) -> int | None:
    """How many priced accommodations a segment still offers; None without a list."""
    fields = _lowered(segment)
    for key in _ACCOMMODATION_KEYS:
        options = fields.get(key)
        if isinstance(options, list):
            return sum(1 for option in options if isinstance(option, dict) and _price(_lowered(option)) is not None)
    return None


def _segment_price(segment: dict[str, Any]) -> float | None:
    """A segment's own price, else its cheapest accommodation (the deck fare)."""
    fields = _lowered(segment)
    price = _price(fields)
    if price is not None:
        return price
    for key in _ACCOMMODATION_KEYS:
        options = fields.get(key)
        if isinstance(options, list):
            prices = [_price(_lowered(option)) for option in options if isinstance(option, dict)]
            prices = [value for value in prices if value is not None]
            if prices:
                return min(prices)
    return None


def _departure(fields: dict[str, Any]) -> str | None:
    for key in _DEPARTURE_KEYS:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = _lowered(value)
            found = _first_text(inner, ("datetime", "time", "date", "local", "utc", "value"))
            if found:
                return found
    return None


def _operator(fields: dict[str, Any]) -> str | None:
    for key in _OPERATOR_KEYS:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            found = _first_text(_lowered(value), ("name", "title", "code"))
            if found:
                return found
    return None


def _price(fields: dict[str, Any]) -> float | None:
    for key in _PRICE_CENTS_KEYS:
        cents = _number(fields.get(key))
        if cents is not None and cents >= 0:
            return round(cents / 100, 2)
    currency = fields.get("currency")
    for key in _PRICE_KEYS:
        value = fields.get(key)
        if isinstance(value, dict):
            inner = _lowered(value)
            inner_currency = inner.get("currency") or currency
            if not _is_euro(inner_currency):
                continue
            for cents_key in _PRICE_CENTS_KEYS:
                cents = _number(inner.get(cents_key))
                if cents is not None and cents >= 0:
                    return round(cents / 100, 2)
            amount = _first_number(inner, ("amount", "value", "total", "price", "min", "from"))
            if amount is not None and amount >= 0:
                return round(amount, 2)
            continue
        if not _is_euro(currency) and not (isinstance(value, str) and "€" in value):
            continue
        amount = _number(value)
        if amount is not None and amount >= 0:
            return round(amount, 2)
    return None


def _is_euro(currency: Any) -> bool:
    return currency is None or str(currency).strip().upper() in ("EUR", "€", "")


def _availability(fields: dict[str, Any]) -> bool | None:
    for key in _SOLD_OUT_KEYS:
        if isinstance(fields.get(key), bool):
            return not fields[key]
    for key in _AVAILABLE_KEYS:
        if isinstance(fields.get(key), bool):
            return fields[key]
    status = fields.get("availability") or fields.get("status")
    if isinstance(status, str):
        text = _key(status)
        if "soldout" in text or "unavailable" in text or text == "full":
            return False
        if "available" in text or "limited" in text:
            return True
    seats = _first_number(fields, ("availableseats", "seatsavailable", "seatsleft"))
    if seats is not None:
        return seats > 0
    return None


def _lists(payload: Any) -> list[list[Any]]:
    """Every list in ``payload``, shallowest first (breadth-first)."""
    found: list[list[Any]] = []
    queue: deque[tuple[Any, int]] = deque([(payload, 0)])
    while queue:
        node, depth = queue.popleft()
        if isinstance(node, list):
            found.append(node)
            children = node
        elif isinstance(node, dict):
            children = list(node.values())
        else:
            continue
        if depth < _MAX_DEPTH:
            queue.extend((child, depth + 1) for child in children if isinstance(child, (list, dict)))
    return found


def _lowered(item: dict[str, Any]) -> dict[str, Any]:
    return {_key(str(name)): value for name, value in item.items()}


def _key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.casefold())


def _first_text(fields: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = fields.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return None


def _first_number(fields: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _number(fields.get(key))
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER.search(value.replace(" ", ""))
        if match:
            return float(match.group(0).replace(",", "."))
    return None
