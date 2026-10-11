"""Ferry demand for island destinations, from Ferryhopper's MCP server.

Three steps, all destination-level (shared by every account there):

1. Profile: when an owner's property appears, find the island port that
   serves its destination (nearest port by coordinates, preferring one whose
   name matches the place) and the direct routes INTO it, ranked by sailings
   on a sample date. A destination whose nearest port is a mainland gateway
   (Piraeus, Rafina, …) has no ferry demand to read.
2. Collection: once a day, for each route, the sailings of the next days
   (every day for FERRY_DEMAND_DAILY_DAYS, then Fridays and Saturdays up to
   FERRY_DEMAND_HORIZON_DAYS, plus the arrival of every enabled schedule),
   within a call budget.
3. Signal: around a check-in (the day before and the day itself), how many
   sailings, how many had no seats, and the fares against the same routes'
   other dates — high / normal / low.

Rules, not a trained model: there is no history to train on yet. The counts
are kept so a model can be trained once a season has been collected.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

from rapidfuzz import fuzz

from api.repositories.ferry_repository import PROFILE_REFRESH_DAYS
from api.schemas.demand import FerryDemandSignal
from api.services.ferryhopper_client import FerryPort, FerryTrip
from api.services.greek_islands import island_at
from api.services.market_helpers import haversine_km
from api.services.mcp_http_client import McpError

logger = logging.getLogger(__name__)

# Bump when parsing changes: observations keep the version they were read with.
PARSER_VERSION = "fh-2026-10-12"

MAX_PORT_DISTANCE_KM = 30.0
NAME_MATCH_SCORE = 85
# Without coordinates only a near-certain name match is trusted.
NAME_ONLY_MATCH_SCORE = 92
MAX_ROUTES_PER_DESTINATION = 4
# Ranking is once per destination a month; Rhodes alone has ~50 connections,
# and an alphabetical cut would skip Kos and Symi.
MAX_RANKING_CALLS = 60
# Island ports tried in turn when the first has no route with a sailing
# (e.g. an "ALL PORTS" group the trip search does not take).
MAX_PORT_CANDIDATES = 2
# The tool answer kept for re-parsing; larger answers are cut.
RAW_PAYLOAD_MAX_CHARS = 50_000
# Consecutive failed calls after which a collection run stops (server down,
# blocked or changed): the rest of the budget would fail the same way.
MAX_CONSECUTIVE_FAILURES = 5
# Observations older than this are not a current view of the sailings.
SIGNAL_MAX_AGE_DAYS = 3

# Pickup: the same sailing day of the same route, now against an earlier
# look at it. The live calendar (2026-10-11) showed that comparing one
# travel date with others reads the timetable, not demand: every weekday
# runs other ships, and November runs fewer than October.
PICKUP_MIN_DAYS = 3
PICKUP_MAX_DAYS = 21
# Ferryhopper flags no sold-out sailing; it stops offering a full cabin or
# fare tier. A quarter of the accommodations gone since the earlier look,
# or the cheapest fare up 15%, is a sailing filling up.
HIGH_OPTIONS_PICKUP_PCT = -25.0
HIGH_PRICE_PICKUP_PCT = 15.0
LOW_PRICE_PICKUP_PCT = -10.0
# Extra sailings: against the same route on the same weekday (other ships
# run on other weekdays). An added sailing on a twice-daily route is +50%.
HIGH_EXTRA_SAILINGS_PCT = 50.0
MIN_WEEKDAY_SAMPLES = 2
# For a source that flags sold-out sailings.
HIGH_SOLD_OUT_SHARE_PCT = 40.0

# Mainland ports ferries leave FROM. A hotel whose nearest port is one of
# these is on the mainland: ferries there are not demand for it.
MAINLAND_GATEWAYS = frozenset(
    {
        "piraeus", "rafina", "lavrio", "lavrion", "perama", "igoumenitsa", "patra", "patras",
        "kyllini", "killini", "volos", "agioskonstantinos", "thessaloniki", "kavala",
        "alexandroupoli", "alexandroupolis", "oropos", "gytheio", "gythio", "neapoli",
        "keramoti", "glyfa", "arkitsa", "rio", "antirrio", "astakos", "mytikas", "preveza",
        "sagiada", "aigio", "agiamarina", "kymi", "athens",
    }
)

# Greek transliteration ↔ English names. Ferryhopper lists ports in Greek
# transliteration (RODOS, PIRAEUS; probe 2026-10-11) while a Booking city can
# be either; the lookup goes both ways (_EXONYM_LOOKUP below).
EXONYMS: dict[str, tuple[str, ...]] = {
    "rodos": ("rhodes",),
    "kerkyra": ("corfu",),
    "irakleio": ("heraklion", "iraklion"),
    "aigina": ("aegina",),
    "ydra": ("hydra",),
    "alonnisos": ("alonissos",),
    "limnos": ("lemnos",),
    "lesvos": ("mytilene", "lesbos"),
    "mytilini": ("mytilene",),
    "ithaki": ("ithaca",),
    "thira": ("santorini",),
    "santorini": ("thira",),
    "zakynthos": ("zante",),
    "kefalonia": ("cephalonia", "kefallonia"),
    "kriti": ("crete",),
    "megisti": ("kastellorizo",),
    "kastellorizo": ("megisti",),
}

def _exonym_lookup() -> dict[str, tuple[str, ...]]:
    lookup: dict[str, dict[str, None]] = {}
    for name, others in EXONYMS.items():
        for other in others:
            lookup.setdefault(name, {})[other] = None
            lookup.setdefault(other, {})[name] = None
    return {name: tuple(others) for name, others in lookup.items()}


_EXONYM_LOOKUP = _exonym_lookup()

_GREEK_DIGRAPHS = (("ου", "ou"), ("αυ", "av"), ("ευ", "ev"), ("γγ", "ng"), ("μπ", "mp"))
_GREEK_LETTERS = str.maketrans(
    {
        "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
        "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
        "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
        "ω": "o",
    }
)


class FerryClient(Protocol):
    last_payload: Any

    def ports(self) -> list[FerryPort]: ...

    def direct_connections(self, port: str) -> list[FerryPort]: ...

    def trips(self, departure_port: str, arrival_port: str, travel_date: date) -> list[FerryTrip]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class PortMatch:
    port: FerryPort
    distance_km: float | None
    matched_by: str


@dataclass
class CollectionReport:
    routes: int = 0
    calls: int = 0
    saved: int = 0
    failed: int = 0
    aborted: bool = False
    destinations: dict[str, int] = field(default_factory=dict)


def latin(text: str) -> str:
    """Lower-case Latin form of a Greek or Latin place name, accents removed."""
    lowered = "".join(
        character
        for character in unicodedata.normalize("NFD", str(text or "").casefold())
        if unicodedata.category(character) != "Mn"
    )
    for greek, roman in _GREEK_DIGRAPHS:
        lowered = lowered.replace(greek, roman)
    lowered = lowered.translate(_GREEK_LETTERS)
    return re.sub(r"[^0-9a-z]+", " ", lowered).strip()


def place_keys(names: Iterable[str | None]) -> list[str]:
    """Latin forms of the place names plus their English exonyms."""
    keys: dict[str, None] = {}
    for name in names:
        key = latin(name or "")
        if not key:
            continue
        keys.setdefault(key, None)
        for exonym in _EXONYM_LOOKUP.get(key.replace(" ", ""), ()):
            keys.setdefault(exonym, None)
    return list(keys)


def is_mainland_gateway(port_name: str) -> bool:
    key = latin(port_name)
    tokens = set(key.split())
    return key.replace(" ", "") in MAINLAND_GATEWAYS or bool(tokens & MAINLAND_GATEWAYS)


def _name_score(port: FerryPort, keys: Sequence[str]) -> float:
    name = latin(port.name)
    return max((fuzz.token_set_ratio(name, key) for key in keys), default=0.0)


def is_port_group(port_name: str) -> bool:
    """Ferryhopper's "X (ALL PORTS)" groups several real ports."""
    return "all ports" in latin(port_name)


def _named_ports(ports: Sequence[FerryPort], keys: Sequence[str], threshold: float) -> list[FerryPort]:
    """Ports whose name matches a key, best first.

    A port named exactly after the place comes first, then an "ALL PORTS"
    group of it (several ports of one island), then ports merely mentioning
    it ("SAMI (KEFALONIA)").
    """
    compact_keys = {key.replace(" ", "") for key in keys}
    ranked = []
    for port in ports:
        score = _name_score(port, keys)
        if score < threshold:
            continue
        base = latin(re.sub(r"\(\s*all ports\s*\)", "", port.name, flags=re.IGNORECASE)).replace(" ", "")
        group = is_port_group(port.name)
        tier = 2 if base in compact_keys and not group else 1 if group and base in compact_keys else 0
        ranked.append((tier, score, -len(port.name), port))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [port for *_, port in ranked]


def port_candidates(
    ports: Sequence[FerryPort],
    place_names: Iterable[str | None],
    latitude: float | None,
    longitude: float | None,
) -> tuple[list[PortMatch], str]:
    """The island ports that may serve a place, best first; empty with the reason in Greek.

    Ports with coordinates: the nearest within MAX_PORT_DISTANCE_KM,
    preferring one named after the place. Ports without (Ferryhopper): the
    island the property's own coordinates fall on, then its ports by name. A
    property off every island is mainland. Without the property's
    coordinates only a near-certain name match counts.
    """
    keys = place_keys(place_names)
    has_coordinates = latitude is not None and longitude is not None
    located = [port for port in ports if port.latitude is not None and port.longitude is not None]
    if has_coordinates and located:
        near = sorted(
            ((haversine_km(latitude, longitude, port.latitude, port.longitude), port) for port in located),
            key=lambda pair: pair[0],
        )
        near = [(distance, port) for distance, port in near if distance <= MAX_PORT_DISTANCE_KM]
        named = [(distance, port) for distance, port in near if _name_score(port, keys) >= NAME_MATCH_SCORE]
        if named or near:
            distance, port = (named or near)[0]
            if is_mainland_gateway(port.name):
                return [], (
                    f"Το κοντινότερο λιμάνι ({port.name}) είναι στη στεριά: "
                    "τα πλοία από εκεί δεν δείχνουν ζήτηση για τον προορισμό."
                )
            return [PortMatch(port, round(distance, 2), "name" if named else "distance")], ""
    island = island_at(latitude, longitude) if has_coordinates else None
    if island is not None:
        found = _named_ports(ports, place_keys(island.names), NAME_MATCH_SCORE)
        if not found:
            return [], f"Δεν υπάρχει λιμάνι για {island.label} στα δρομολόγια της Ferryhopper."
        return [PortMatch(port, None, "island") for port in found[:MAX_PORT_CANDIDATES]], ""
    if has_coordinates:
        return [], "Το κατάλυμα δεν είναι σε νησί με ακτοπλοϊκή σύνδεση: τα πλοία δεν δείχνουν ζήτηση εκεί."
    found = [port for port in _named_ports(ports, keys, NAME_ONLY_MATCH_SCORE) if not is_mainland_gateway(port.name)]
    if not found:
        return [], "Δεν βρέθηκε λιμάνι για τον προορισμό."
    return [PortMatch(port, None, "name") for port in found[:MAX_PORT_CANDIDATES]], ""


def resolve_island_port(
    ports: Sequence[FerryPort],
    place_names: Iterable[str | None],
    latitude: float | None,
    longitude: float | None,
) -> tuple[PortMatch | None, str]:
    """The best island port serving a place, or None with the reason in Greek."""
    candidates, reason = port_candidates(ports, place_names, latitude, longitude)
    return (candidates[0], "") if candidates else (None, reason)


def aggregate_trips(trips: Sequence[FerryTrip]) -> dict[str, Any]:
    """Counts and fares of one route-day. A trip not known to be indirect counts."""
    direct = [trip for trip in trips if trip.direct is not False]
    known = [trip for trip in direct if trip.available is not None]
    sold_out = sum(1 for trip in known if trip.available is False)
    open_prices = [trip.price_eur for trip in direct if trip.price_eur is not None and trip.available is not False]
    prices = open_prices or [trip.price_eur for trip in direct if trip.price_eur is not None]
    options = [trip.accommodation_options for trip in direct if trip.accommodation_options is not None]
    return {
        "sailings": len(direct),
        "availability_known": len(known),
        "sold_out": sold_out,
        "min_price_eur": round(min(prices), 2) if prices else None,
        "median_price_eur": round(statistics.median(prices), 2) if prices else None,
        "operators": len({trip.operator for trip in direct if trip.operator}),
        # Only when every sailing listed its accommodations: a partial sum
        # would read as a drop.
        "accommodation_options": sum(options) if direct and len(options) == len(direct) else None,
    }


def plan_travel_dates(
    today: date,
    *,
    daily_days: int,
    horizon_days: int,
    priority_dates: Iterable[date] = (),
    max_dates: int | None = None,
) -> list[date]:
    """Travel dates to look up, ascending: schedule arrivals first when the budget is short."""
    regular = [today + timedelta(days=offset) for offset in range(1, max(daily_days, 0) + 1)]
    regular += [
        today + timedelta(days=offset)
        for offset in range(max(daily_days, 0) + 1, max(horizon_days, 0) + 1)
        if (today + timedelta(days=offset)).weekday() in (4, 5)  # Friday, Saturday
    ]
    priority = sorted({day for day in priority_dates if day > today})
    ordered = list(dict.fromkeys([*priority, *regular]))
    if max_dates is not None:
        ordered = ordered[: max(max_dates, 0)]
    return sorted(ordered)


def trimmed_payload(payload: Any) -> Any:
    """The tool answer as stored: cut to RAW_PAYLOAD_MAX_CHARS of JSON text."""
    if payload is None:
        return None
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= RAW_PAYLOAD_MAX_CHARS:
        return payload
    return {"truncated": True, "chars": len(text), "head": text[:RAW_PAYLOAD_MAX_CHARS]}


def _route_label(origin: str, destination: str) -> str:
    return f"{origin} → {destination}"


RouteDay = tuple[Any, date]


def _latest(rows: Iterable[dict[str, Any]]) -> dict[RouteDay, dict[str, Any]]:
    """The newest observation of each (route, travel date)."""
    latest: dict[RouteDay, dict[str, Any]] = {}
    for row in rows:
        key = (row["route_id"], row["travel_date"])
        if key not in latest or row["observed_on"] > latest[key]["observed_on"]:
            latest[key] = row
    return latest


def _earlier_looks(
    rows: Iterable[dict[str, Any]], latest: dict[RouteDay, dict[str, Any]]
) -> dict[RouteDay, dict[str, Any]]:
    """Per (route, travel date): the oldest look PICKUP_MIN_DAYS..PICKUP_MAX_DAYS before the newest."""
    earlier: dict[RouteDay, dict[str, Any]] = {}
    for row in rows:
        key = (row["route_id"], row["travel_date"])
        newest = latest.get(key)
        if newest is None:
            continue
        age = (newest["observed_on"] - row["observed_on"]).days
        if PICKUP_MIN_DAYS <= age <= PICKUP_MAX_DAYS and (
            key not in earlier or row["observed_on"] < earlier[key]["observed_on"]
        ):
            earlier[key] = row
    return earlier


def _change(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return now / before - 1


def _route_weights(route_ids: Iterable[Any], latest: dict[RouteDay, dict[str, Any]]) -> dict[Any, float]:
    """What each route usually offers in a day (median accommodations); equal when not all known.

    A Blue Star offering 22 accommodations a sailing outweighs a Symi day
    boat offering 2, so six day-trip sailings cannot drown the ship that
    brings the stays.
    """
    route_ids = list(dict.fromkeys(route_ids))
    weights: dict[Any, float] = {}
    for route_id in route_ids:
        offered = [
            row["accommodation_options"]
            for (route, _), row in latest.items()
            if route == route_id and row.get("accommodation_options")
        ]
        if not offered:
            return dict.fromkeys(route_ids, 1.0)
        weights[route_id] = float(statistics.median(offered))
    return weights


def _combine(per_route: dict[Any, list[float]], weights: dict[Any, float], pick: Callable[[list[float]], float]) -> float | None:
    """Percent: per route ``pick`` over its window days, then the routes weighted."""
    if not per_route:
        return None
    total = sum(weights.get(route_id, 1.0) for route_id in per_route)
    combined = sum(pick(values) * weights.get(route_id, 1.0) for route_id, values in per_route.items()) / total
    return round(100 * combined, 1)


def build_ferry_demand_signal(
    rows: Sequence[dict[str, Any]],
    check_in: date,
    today: date,
) -> FerryDemandSignal:
    """Demand level from the ferries into the destination around ``check_in``.

    ``rows`` are every observation of the destination's routes over the
    pickup look-back (FerryRepository.observation_history). Two readings:

    * pickup: each sailing day against an earlier look at the same one
      (accommodations gone, cheapest fare up), the route's most telling
      window day, routes weighted by what they carry;
    * extra sailings: each window day against the same route on the same
      weekday, the larger of the two days.

    ``unknown`` until either can be read (the first days of collection).
    """
    window = [check_in - timedelta(days=1), check_in]
    latest = {
        key: row
        for key, row in _latest(rows).items()
        if (today - row["observed_on"]).days <= SIGNAL_MAX_AGE_DAYS
    }
    in_window = [row for (_, travel_date), row in latest.items() if travel_date in window]
    if not in_window:
        return FerryDemandSignal(
            level="unknown",
            check_in=check_in,
            window=window,
            summary="Δεν υπάρχουν ακόμη πρόσφατα δεδομένα πλοίων για αυτές τις ημερομηνίες.",
        )
    if not any(row["sailings"] for row in latest.values()):
        return FerryDemandSignal(
            level="unknown",
            check_in=check_in,
            window=window,
            summary="Η Ferryhopper δεν δείχνει δρομολόγια προς τον προορισμό.",
        )

    weights = _route_weights((row["route_id"] for row in in_window), latest)

    earlier = _earlier_looks(rows, latest)
    option_pickup: dict[Any, list[float]] = {}
    price_pickup: dict[Any, list[float]] = {}
    compared_with: list[date] = []
    for row in in_window:
        before = earlier.get((row["route_id"], row["travel_date"]))
        if before is None:
            continue
        compared_with.append(before["observed_on"])
        options = _change(row.get("accommodation_options"), before.get("accommodation_options"))
        if options is not None:
            option_pickup.setdefault(row["route_id"], []).append(options)
        price = _change(row["min_price_eur"], before["min_price_eur"])
        if price is not None:
            price_pickup.setdefault(row["route_id"], []).append(price)
    # The same ship against itself barely moves without demand, so the
    # route's most telling window day counts: Friday's overnight ship
    # filling is Saturday's arrivals, whatever Saturday's ship does.
    options_change = _combine(option_pickup, weights, min)
    price_change = _combine(price_pickup, weights, max)

    extra: dict[Any, list[float]] = {}
    for row in in_window:
        same_weekday = [
            other["sailings"]
            for (route_id, travel_date), other in latest.items()
            if route_id == row["route_id"]
            and travel_date.weekday() == row["travel_date"].weekday()
            and travel_date not in window
        ]
        if len(same_weekday) >= MIN_WEEKDAY_SAMPLES:
            change = _change(row["sailings"], statistics.median(same_weekday))
            if change is not None:
                extra.setdefault(row["route_id"], []).append(change)
    sailings_change = _combine(extra, weights, max)

    sailings = sum(row["sailings"] for row in in_window)
    availability_known = sum(row["availability_known"] for row in in_window)
    sold_out = sum(row["sold_out"] for row in in_window)
    sold_out_share = round(100 * sold_out / availability_known, 1) if availability_known else None
    prices = [row["min_price_eur"] for row in in_window if row["min_price_eur"] is not None]

    readings = (options_change, price_change, sailings_change, sold_out_share)
    if all(reading is None for reading in readings):
        level = "unknown"
    elif (
        (options_change is not None and options_change <= HIGH_OPTIONS_PICKUP_PCT)
        or (price_change is not None and price_change >= HIGH_PRICE_PICKUP_PCT)
        or (sailings_change is not None and sailings_change >= HIGH_EXTRA_SAILINGS_PCT)
        or (sold_out_share is not None and sold_out_share >= HIGH_SOLD_OUT_SHARE_PCT)
    ):
        level = "high"
    elif price_change is not None and price_change <= LOW_PRICE_PICKUP_PCT and (options_change or 0) >= 0:
        level = "low"
    else:
        level = "normal"

    routes = sorted({_route_label(row["origin_port"], row["destination_port"]) for row in in_window})
    destination = in_window[0]["destination_port"]
    parts = [f"{sailings} δρομολόγια"]
    if availability_known:
        parts.append(f"{sold_out} χωρίς θέσεις")
    if prices:
        parts.append(f"φθηνότερο εισιτήριο {min(prices):.0f} €")
    if sailings_change is not None and sailings_change >= 10:
        parts.append(f"δρομολόγια {sailings_change:+.0f}% από τη συνηθισμένη ίδια μέρα")
    since = min(compared_with) if compared_with else None
    pickup = []
    if options_change is not None:
        pickup.append(f"θέσεις προς πώληση {options_change:+.0f}%")
    if price_change is not None:
        pickup.append(f"φθηνότερο εισιτήριο {price_change:+.0f}%")
    if pickup and since is not None:
        parts.append(f"από τις {since:%d/%m}: {', '.join(pickup)}")
    summary = f"Πλοία προς {destination} για {window[0]:%d/%m}–{window[1]:%d/%m}: {', '.join(parts)}."
    if level == "unknown":
        summary += (
            f" Η εξέλιξη των κρατήσεων φαίνεται μετά από {PICKUP_MIN_DAYS} μέρες συλλογής."
        )
    else:
        summary += f" Ζήτηση πλοίων: {dict(high='Υψηλή', normal='Κανονική', low='Χαμηλή')[level]}."
    return FerryDemandSignal(
        level=level,
        check_in=check_in,
        window=window,
        sailings=sailings,
        availability_known=availability_known,
        sold_out=sold_out,
        sold_out_share_pct=sold_out_share,
        min_price_eur=round(min(prices), 2) if prices else None,
        price_change_pct=price_change,
        options_change_pct=options_change,
        compared_with=since,
        sailings_vs_usual_pct=sailings_change,
        routes=routes,
        observed_on=max(row["observed_on"] for row in in_window),
        summary=summary,
    )


def daily_levels(rows: Sequence[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Per travel date, the newest look at every collected route, with the level of a check-in that day."""
    by_day: dict[date, list[dict[str, Any]]] = {}
    for (_, travel_date), row in _latest(rows).items():
        if (today - row["observed_on"]).days <= SIGNAL_MAX_AGE_DAYS:
            by_day.setdefault(travel_date, []).append(row)
    days = []
    for travel_date in sorted(by_day):
        day_rows = by_day[travel_date]
        prices = [row["min_price_eur"] for row in day_rows if row["min_price_eur"] is not None]
        signal = build_ferry_demand_signal(rows, travel_date, today)
        known_options = [row["accommodation_options"] for row in day_rows if row.get("accommodation_options") is not None]
        days.append(
            {
                "travel_date": travel_date,
                "sailings": sum(row["sailings"] for row in day_rows),
                "availability_known": sum(row["availability_known"] for row in day_rows),
                "sold_out": sum(row["sold_out"] for row in day_rows),
                "min_price_eur": round(min(prices), 2) if prices else None,
                "accommodation_options": sum(known_options) if known_options else None,
                "options_change_pct": signal.options_change_pct,
                "price_change_pct": signal.price_change_pct,
                "level": signal.level,
            }
        )
    return days


def history_window(today: date, check_in: date | None, horizon_days: int) -> dict[str, date]:
    """The observation_history arguments a signal over the horizon needs."""
    reach = max(horizon_days, (check_in - today).days if check_in else 0) + 1
    return {
        "date_from": today - timedelta(days=1),
        "date_to": today + timedelta(days=reach),
        "observed_since": today - timedelta(days=PICKUP_MAX_DAYS + SIGNAL_MAX_AGE_DAYS),
    }


def load_ferry_signal(
    repository: Any,
    canonical_destination: str,
    check_in: date,
    *,
    today: date,
    horizon_days: int,
) -> FerryDemandSignal | None:
    """The ferry signal for a check-in; None when the destination has no ferry data."""
    rows = repository.observation_history(canonical_destination, **history_window(today, check_in, horizon_days))
    if not rows:
        return None
    return build_ferry_demand_signal(rows, check_in, today)


class FerryDemandService:
    def __init__(
        self,
        repository: Any,
        client_factory: Callable[[], FerryClient],
        *,
        daily_days: int = 14,
        horizon_days: int = 60,
        max_calls_per_run: int = 200,
        tracked_check_in: Callable[[date], date] | None = None,
    ):
        self.repository = repository
        self.client_factory = client_factory
        self.daily_days = daily_days
        self.horizon_days = horizon_days
        self.max_calls_per_run = max_calls_per_run
        self._tracked_check_in = tracked_check_in

    def resolve_profiles(self, today: date, limit: int = 5) -> int:
        """Look up the port of destinations that have none (or a stale one). Returns lookups made."""
        destinations = self.repository.destinations_to_profile(limit=limit)
        if not destinations:
            return 0
        client = self.client_factory()
        try:
            ports: list[FerryPort] | None = None
            for destination in destinations:
                key = destination["canonical_destination"]
                try:
                    if ports is None:
                        ports = client.ports()
                    self._resolve_one(client, ports, destination, today)
                except McpError as exc:
                    logger.warning("Ferry profile lookup failed for %s: %s", key, exc)
                    self.repository.save_profile(key, status="error", detail=str(exc)[:500])
        finally:
            client.close()
        return len(destinations)

    def _resolve_one(self, client: FerryClient, ports: list[FerryPort], destination: dict[str, Any], today: date) -> None:
        key = destination["canonical_destination"]
        candidates, reason = port_candidates(
            ports,
            (destination.get("city"), destination.get("raw_destination"), key),
            destination.get("latitude"),
            destination.get("longitude"),
        )
        if not candidates:
            self.repository.save_profile(key, status="no_ferry", detail=reason)
            logger.info("Ferry profile %s: no island port (%s)", key, reason)
            return
        checked: list[str] = []
        for match in candidates:
            island = match.port.name
            # A group of ports would count the same sailings twice next to
            # its members (ATHENS (ALL PORTS) and PIRAEUS).
            connections = [
                port.name for port in client.direct_connections(island) if not is_port_group(port.name)
            ]
            if not connections:
                continue
            routes = self._rank_routes(client, island, connections, today)
            if routes:
                self._save_ferry_profile(key, match, routes)
                return
            checked.append(island)
        # No route with a sailing on the sample date: Ferryhopper does not sell
        # this crossing (Salamina's Perama–Paloukia shuttle), or it is out of
        # season. Nothing to read now; the monthly refresh looks again.
        first = candidates[0]
        detail = (
            f"Η Ferryhopper δεν δείχνει δρομολόγια προς {', '.join(checked)}· νέος έλεγχος σε {PROFILE_REFRESH_DAYS} μέρες."
            if checked
            else "Το λιμάνι δεν έχει απευθείας γραμμές."
        )
        self.repository.save_profile(
            key,
            status="no_ferry",
            island_port=first.port.name,
            island_port_code=first.port.code,
            port_distance_km=first.distance_km,
            matched_by=first.matched_by,
            detail=detail,
        )

    def _save_ferry_profile(
        self, key: str, match: PortMatch, routes: list[tuple[str, str, int | None]]
    ) -> None:
        self.repository.save_profile(
            key,
            status="ferry",
            island_port=match.port.name,
            island_port_code=match.port.code,
            port_distance_km=match.distance_km,
            matched_by=match.matched_by,
            routes=routes,
        )
        logger.info("Ferry profile %s: port %s, %s route(s)", key, match.port.name, len(routes))

    @staticmethod
    def _rank_routes(
        client: FerryClient, island: str, connections: list[str], today: date
    ) -> list[tuple[str, str, int | None]]:
        """The inbound routes with the most sailings on a sample Friday, gateways first.

        Empty when nothing sails into the port that day. Raises McpError when
        no route has a sailing and some searches failed: the answer is not
        known, so the lookup is retried soon rather than closed for a month.
        """
        sample = today + timedelta(days=7)
        sample += timedelta(days=(4 - sample.weekday()) % 7)
        candidates = sorted(dict.fromkeys(connections), key=lambda name: (not is_mainland_gateway(name), name))
        ranked: list[tuple[bool, int, str]] = []
        failures = 0
        last_error: McpError | None = None
        for origin in candidates[:MAX_RANKING_CALLS]:
            try:
                sailings = aggregate_trips(client.trips(origin, island, sample))["sailings"]
            except McpError as exc:
                logger.info("Ferry route ranking %s → %s failed: %s", origin, island, exc)
                failures += 1
                last_error = exc
                continue
            if sailings > 0:
                ranked.append((is_mainland_gateway(origin), sailings, origin))
        if not ranked and failures:
            raise McpError(f"{failures} of the route searches into {island} failed: {last_error}")
        ranked.sort(key=lambda item: (not item[0], -item[1], item[2]))
        return [(origin, island, sailings) for _, sailings, origin in ranked[:MAX_ROUTES_PER_DESTINATION]]

    def collect(self, today: date) -> CollectionReport:
        """Read the next days' sailings of every collected route, within the call budget."""
        report = CollectionReport()
        routes = self.repository.routes_to_collect()
        if not routes:
            return report
        report.routes = len(routes)
        by_destination: dict[str, list[dict[str, Any]]] = {}
        for route in routes:
            by_destination.setdefault(route["canonical_destination"], []).append(route)
        lead_days = self.repository.schedule_lead_days_by_destination() if self._tracked_check_in else {}
        budget_per_destination = max(self.max_calls_per_run // len(by_destination), 1)

        client = self.client_factory()
        consecutive_failures = 0
        try:
            for destination, destination_routes in by_destination.items():
                priority: set[date] = set()
                for lead in lead_days.get(destination, ()):
                    arrival = self._tracked_check_in(today + timedelta(days=lead))
                    priority.update({arrival - timedelta(days=1), arrival})
                dates = plan_travel_dates(
                    today,
                    daily_days=self.daily_days,
                    horizon_days=self.horizon_days,
                    priority_dates=priority,
                    max_dates=max(budget_per_destination // len(destination_routes), 1),
                )
                saved_before = report.saved
                for travel_date in dates:
                    for route in destination_routes:
                        report.calls += 1
                        try:
                            trips = client.trips(route["origin_port"], route["destination_port"], travel_date)
                        except McpError as exc:
                            report.failed += 1
                            consecutive_failures += 1
                            logger.warning(
                                "Ferry trips %s → %s on %s failed: %s",
                                route["origin_port"],
                                route["destination_port"],
                                travel_date,
                                exc,
                            )
                            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                                report.aborted = True
                                logger.error("Ferry collection stopped after %s failures in a row", consecutive_failures)
                                return report
                            continue
                        consecutive_failures = 0
                        self.repository.upsert_observation(
                            route_id=route["id"],
                            travel_date=travel_date,
                            observed_on=today,
                            raw_payload=trimmed_payload(client.last_payload),
                            parser_version=PARSER_VERSION,
                            **aggregate_trips(trips),
                        )
                        report.saved += 1
                report.destinations[destination] = report.saved - saved_before
        finally:
            client.close()
        return report
