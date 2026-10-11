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

from api.schemas.demand import FerryDemandSignal
from api.services.ferryhopper_client import FerryPort, FerryTrip
from api.services.market_helpers import haversine_km
from api.services.mcp_http_client import McpError

logger = logging.getLogger(__name__)

# Bump when parsing changes: observations keep the version they were read with.
PARSER_VERSION = "fh-2026-10-11"

MAX_PORT_DISTANCE_KM = 30.0
NAME_MATCH_SCORE = 85
# Without coordinates only a near-certain name match is trusted.
NAME_ONLY_MATCH_SCORE = 92
MAX_ROUTES_PER_DESTINATION = 4
MAX_RANKING_CALLS = 12
# The tool answer kept for re-parsing; larger answers are cut.
RAW_PAYLOAD_MAX_CHARS = 50_000
# Consecutive failed calls after which a collection run stops (server down,
# blocked or changed): the rest of the budget would fail the same way.
MAX_CONSECUTIVE_FAILURES = 5
# Observations older than this are not a current view of the sailings.
SIGNAL_MAX_AGE_DAYS = 3
# Other travel dates of a route needed before "usual" means anything.
MIN_BASELINE_DATES = 3

HIGH_SOLD_OUT_SHARE_PCT = 40.0
HIGH_PRICE_VS_USUAL_PCT = 15.0
HIGH_SAILINGS_VS_USUAL_PCT = 30.0
LOW_PRICE_VS_USUAL_PCT = -10.0

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

# Latin names in Greek transliteration → the English names port lists use.
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
        for exonym in EXONYMS.get(key.replace(" ", ""), ()):
            keys.setdefault(exonym, None)
    return list(keys)


def is_mainland_gateway(port_name: str) -> bool:
    key = latin(port_name)
    tokens = set(key.split())
    return key.replace(" ", "") in MAINLAND_GATEWAYS or bool(tokens & MAINLAND_GATEWAYS)


def _name_score(port: FerryPort, keys: Sequence[str]) -> float:
    name = latin(port.name)
    return max((fuzz.token_set_ratio(name, key) for key in keys), default=0.0)


def resolve_island_port(
    ports: Sequence[FerryPort],
    place_names: Iterable[str | None],
    latitude: float | None,
    longitude: float | None,
) -> tuple[PortMatch | None, str]:
    """The island port serving a place, or None with the reason in Greek."""
    keys = place_keys(place_names)
    match: PortMatch | None = None
    if latitude is not None and longitude is not None:
        near = sorted(
            (
                (haversine_km(latitude, longitude, port.latitude, port.longitude), port)
                for port in ports
                if port.latitude is not None and port.longitude is not None
            ),
            key=lambda pair: pair[0],
        )
        near = [(distance, port) for distance, port in near if distance <= MAX_PORT_DISTANCE_KM]
        named = [(distance, port) for distance, port in near if _name_score(port, keys) >= NAME_MATCH_SCORE]
        if named:
            distance, port = named[0]
            match = PortMatch(port, round(distance, 2), "name")
        elif near:
            distance, port = near[0]
            match = PortMatch(port, round(distance, 2), "distance")
    if match is None and keys:
        scored = sorted(((_name_score(port, keys), port) for port in ports), key=lambda pair: -pair[0])
        if scored and scored[0][0] >= NAME_ONLY_MATCH_SCORE:
            match = PortMatch(scored[0][1], None, "name")
    if match is None:
        return None, "Δεν βρέθηκε λιμάνι κοντά στον προορισμό."
    if is_mainland_gateway(match.port.name):
        return None, (
            f"Το κοντινότερο λιμάνι ({match.port.name}) είναι στη στεριά: "
            "τα πλοία από εκεί δεν δείχνουν ζήτηση για τον προορισμό."
        )
    return match, ""


def aggregate_trips(trips: Sequence[FerryTrip]) -> dict[str, Any]:
    """Counts and fares of one route-day. A trip not known to be indirect counts."""
    direct = [trip for trip in trips if trip.direct is not False]
    known = [trip for trip in direct if trip.available is not None]
    sold_out = sum(1 for trip in known if trip.available is False)
    open_prices = [trip.price_eur for trip in direct if trip.price_eur is not None and trip.available is not False]
    prices = open_prices or [trip.price_eur for trip in direct if trip.price_eur is not None]
    return {
        "sailings": len(direct),
        "availability_known": len(known),
        "sold_out": sold_out,
        "min_price_eur": round(min(prices), 2) if prices else None,
        "median_price_eur": round(statistics.median(prices), 2) if prices else None,
        "operators": len({trip.operator for trip in direct if trip.operator}),
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


def build_ferry_demand_signal(
    rows: Sequence[dict[str, Any]],
    check_in: date,
    today: date,
) -> FerryDemandSignal:
    """Demand level from the ferries into the destination around ``check_in``.

    ``rows`` are the newest observation per (route, travel date) over the
    collection horizon (FerryRepository.latest_observations).
    """
    window = [check_in - timedelta(days=1), check_in]
    fresh = [row for row in rows if (today - row["observed_on"]).days <= SIGNAL_MAX_AGE_DAYS]
    in_window = [row for row in fresh if row["travel_date"] in window]
    if not in_window:
        return FerryDemandSignal(
            level="unknown",
            check_in=check_in,
            window=window,
            summary="Δεν υπάρχουν ακόμη πρόσφατα δεδομένα πλοίων για αυτές τις ημερομηνίες.",
        )

    baselines: dict[Any, dict[str, float | None]] = {}
    for route_id in {row["route_id"] for row in in_window}:
        others = [row for row in fresh if row["route_id"] == route_id and row["travel_date"] not in window]
        sailings = [row["sailings"] for row in others]
        prices = [row["min_price_eur"] for row in others if row["min_price_eur"] is not None]
        baselines[route_id] = {
            "sailings": statistics.median(sailings) if len(sailings) >= MIN_BASELINE_DATES else None,
            "price": statistics.median(prices) if len(prices) >= MIN_BASELINE_DATES else None,
        }

    sailings = sum(row["sailings"] for row in in_window)
    availability_known = sum(row["availability_known"] for row in in_window)
    sold_out = sum(row["sold_out"] for row in in_window)
    sold_out_share = round(100 * sold_out / availability_known, 1) if availability_known else None
    prices = [row["min_price_eur"] for row in in_window if row["min_price_eur"] is not None]

    usual_sailings = [baselines[row["route_id"]]["sailings"] for row in in_window]
    sailings_vs_usual = None
    if usual_sailings and all(value is not None for value in usual_sailings) and sum(usual_sailings) > 0:
        sailings_vs_usual = round(100 * (sailings / sum(usual_sailings) - 1), 1)
    price_changes = [
        row["min_price_eur"] / baselines[row["route_id"]]["price"] - 1
        for row in in_window
        if row["min_price_eur"] is not None and baselines[row["route_id"]]["price"]
    ]
    price_vs_usual = round(100 * statistics.median(price_changes), 1) if price_changes else None

    if (
        (sold_out_share is not None and sold_out_share >= HIGH_SOLD_OUT_SHARE_PCT)
        or (price_vs_usual is not None and price_vs_usual >= HIGH_PRICE_VS_USUAL_PCT)
        or (sailings_vs_usual is not None and sailings_vs_usual >= HIGH_SAILINGS_VS_USUAL_PCT)
    ):
        level = "high"
    elif not sold_out and price_vs_usual is not None and price_vs_usual <= LOW_PRICE_VS_USUAL_PCT:
        level = "low"
    else:
        level = "normal"

    routes = sorted({_route_label(row["origin_port"], row["destination_port"]) for row in in_window})
    destination = in_window[0]["destination_port"]
    parts = [f"{sailings} δρομολόγια"]
    if availability_known:
        parts.append(f"{sold_out} χωρίς θέσεις")
    if prices:
        price_text = f"ελάχιστο εισιτήριο {min(prices):.0f} €"
        if price_vs_usual is not None:
            price_text += f" ({price_vs_usual:+.0f}% από το συνηθισμένο)"
        parts.append(price_text)
    if sailings_vs_usual is not None and abs(sailings_vs_usual) >= 10:
        parts.append(f"δρομολόγια {sailings_vs_usual:+.0f}% από το συνηθισμένο")
    level_text = {"high": "Υψηλή", "normal": "Κανονική", "low": "Χαμηλή"}[level]
    summary = (
        f"Πλοία προς {destination} για {window[0]:%d/%m}–{window[1]:%d/%m}: "
        f"{', '.join(parts)}. Ζήτηση πλοίων: {level_text}."
    )
    return FerryDemandSignal(
        level=level,
        check_in=check_in,
        window=window,
        sailings=sailings,
        availability_known=availability_known,
        sold_out=sold_out,
        sold_out_share_pct=sold_out_share,
        min_price_eur=round(min(prices), 2) if prices else None,
        price_vs_usual_pct=price_vs_usual,
        sailings_vs_usual_pct=sailings_vs_usual,
        routes=routes,
        observed_on=max(row["observed_on"] for row in in_window),
        summary=summary,
    )


def daily_levels(rows: Sequence[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Per travel date, every collected route summed, with the level of a check-in that day."""
    by_day: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(row["travel_date"], []).append(row)
    days = []
    for travel_date in sorted(by_day):
        day_rows = by_day[travel_date]
        prices = [row["min_price_eur"] for row in day_rows if row["min_price_eur"] is not None]
        signal = build_ferry_demand_signal(rows, travel_date, today)
        days.append(
            {
                "travel_date": travel_date,
                "sailings": sum(row["sailings"] for row in day_rows),
                "availability_known": sum(row["availability_known"] for row in day_rows),
                "sold_out": sum(row["sold_out"] for row in day_rows),
                "min_price_eur": round(min(prices), 2) if prices else None,
                "level": signal.level,
            }
        )
    return days


def load_ferry_signal(
    repository: Any,
    canonical_destination: str,
    check_in: date,
    *,
    today: date,
    horizon_days: int,
) -> FerryDemandSignal | None:
    """The ferry signal for a check-in; None when the destination has no fresh ferry data."""
    rows = repository.latest_observations(
        canonical_destination,
        date_from=today,
        date_to=today + timedelta(days=max(horizon_days, (check_in - today).days) + 1),
        observed_since=today - timedelta(days=SIGNAL_MAX_AGE_DAYS),
    )
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
        match, reason = resolve_island_port(
            ports,
            (destination.get("city"), destination.get("raw_destination"), key),
            destination.get("latitude"),
            destination.get("longitude"),
        )
        if match is None:
            self.repository.save_profile(key, status="no_ferry", detail=reason)
            logger.info("Ferry profile %s: no island port (%s)", key, reason)
            return
        island = match.port.name
        connections = [port.name for port in client.direct_connections(island)]
        if not connections:
            self.repository.save_profile(
                key,
                status="no_ferry",
                island_port=island,
                island_port_code=match.port.code,
                port_distance_km=match.distance_km,
                matched_by=match.matched_by,
                detail="Το λιμάνι δεν έχει απευθείας γραμμές.",
            )
            return
        routes = self._rank_routes(client, island, connections, today)
        self.repository.save_profile(
            key,
            status="ferry",
            island_port=island,
            island_port_code=match.port.code,
            port_distance_km=match.distance_km,
            matched_by=match.matched_by,
            routes=routes,
        )
        logger.info("Ferry profile %s: port %s, %s route(s)", key, island, len(routes))

    @staticmethod
    def _rank_routes(
        client: FerryClient, island: str, connections: list[str], today: date
    ) -> list[tuple[str, str, int | None]]:
        """Keep the inbound routes with the most sailings on a sample Friday; gateways first."""
        sample = today + timedelta(days=7)
        sample += timedelta(days=(4 - sample.weekday()) % 7)
        candidates = sorted(dict.fromkeys(connections), key=lambda name: (not is_mainland_gateway(name), name))
        ranked: list[tuple[bool, int, str]] = []
        unranked: list[str] = []
        for origin in candidates[:MAX_RANKING_CALLS]:
            try:
                sailings = aggregate_trips(client.trips(origin, island, sample))["sailings"]
            except McpError as exc:
                logger.info("Ferry route ranking %s → %s failed: %s", origin, island, exc)
                unranked.append(origin)
                continue
            if sailings > 0:
                ranked.append((is_mainland_gateway(origin), sailings, origin))
        ranked.sort(key=lambda item: (not item[0], -item[1], item[2]))
        routes: list[tuple[str, str, int | None]] = [
            (origin, island, sailings) for _, sailings, origin in ranked[:MAX_ROUTES_PER_DESTINATION]
        ]
        if not routes:
            # Nothing sails on the sample date (off season) or ranking failed:
            # keep the gateways unranked rather than no route at all.
            fallback = [name for name in candidates if is_mainland_gateway(name)] or unranked or candidates
            routes = [(origin, island, None) for origin in fallback[:MAX_ROUTES_PER_DESTINATION]]
        return routes

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
