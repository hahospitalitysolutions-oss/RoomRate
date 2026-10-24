"""«Απόδοση» KPIs from the owner's nights: occupancy, ADR, RevPAR, revenue, channels and countries.

Definitions (docs/performance/API.md, «Common rules»):

- A night belongs to the date it starts; only confirmed bookings' nights are
  sold (cancellations and no-shows never are).
- Available room nights: the rooms of each room type that day (the dated
  inventory row covering it, else the undated one), minus closed periods.
  A room type with sold nights but no room count makes availability, and so
  occupancy and RevPAR, unknown (None): never a made-up 0%.
- Occupancy = sold / available; ADR = revenue / sold; RevPAR = revenue / available.
- Net = (gross − resilience fee when in the price) / (1 + VAT) − commission.
- Bookings, cancellations, length of stay and lead time count the bookings
  arriving in the period; revenue, nights, channels and countries the nights.
- Last year: the same dates a year earlier (29 February → 28 February).

Pure functions on rows read by PerformanceRepository; PerformanceKpiService
reads them and answers each call.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from api.repositories.performance_repository import PerformanceRepository
from api.services.performance_countries import OTHER_COUNTRIES, country_name
from api.services.room_rates_normalizer import comparable_room_type_categories

MAX_PERIOD_DAYS = 3 * 366
TOP_COUNTRIES = 10


class KpiRejected(ValueError):
    """Filters the call cannot answer; the message is Greek (400)."""


@dataclass(frozen=True)
class Filters:
    date_from: date
    date_to: date
    room_type_id: uuid.UUID | None = None
    amounts: str = "gross"
    compare: str = "last_year"

    def last_year(self) -> Filters:
        return replace(
            self, date_from=a_year_earlier(self.date_from), date_to=a_year_earlier(self.date_to), compare="none"
        )


def a_year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def days(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def months(date_from: date, date_to: date) -> list[tuple[str, date, date]]:
    """Each month touching the period with the period's first and last day in it."""
    result = []
    start = date_from
    while start <= date_to:
        next_month = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
        end = min(date_to, next_month - timedelta(days=1))
        result.append((month_of(start), start, end))
        start = next_month
    return result


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def night_amount(night: dict[str, Any], amounts: str, settings: dict[str, Any] | None) -> float:
    gross = float(night["gross_eur"])
    if amounts != "net":
        return gross
    fee = settings["resilience_fee_eur"] * night["rooms"] if settings["fee_included_in_price"] else 0.0
    return (gross - fee) / (1 + settings["vat_pct"] / 100) - float(night["commission_eur"])


# ---------------------------------------------------------------------------
# Rooms open for sale, day by day
# ---------------------------------------------------------------------------


@dataclass
class Availability:
    # Rooms open for sale per day; None when a needed room count is missing.
    rooms: dict[date, int | None]
    # Days closed altogether (the property, or the filtered room type).
    closed: set[date]

    def total(self, period: Iterable[date]) -> int | None:
        counts = [self.rooms[day] for day in period]
        return None if any(count is None for count in counts) else sum(counts)


def availability(
    period: Sequence[date],
    room_type_ids: Sequence[uuid.UUID],
    inventory: Sequence[dict[str, Any]],
    closures: Sequence[dict[str, Any]],
    *,
    room_type_id: uuid.UUID | None = None,
    sold_room_types: Iterable[uuid.UUID] = (),
) -> Availability:
    """Rooms per day for the whole property, or for ``room_type_id``."""
    rows: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        if row.get("rooms") is not None:
            rows[row["room_type_id"]].append(row)
    types = [room_type_id] if room_type_id else list(room_type_ids)
    sold = set(sold_room_types)
    unknown = (
        any(not rows.get(kind) for kind in types if room_type_id or kind in sold)
        or not any(rows.get(kind) for kind in types)
    )

    def capacity(kind: uuid.UUID, day: date) -> int:
        dated = [
            row for row in rows.get(kind, [])
            if (row.get("valid_from") or row.get("valid_to"))
            and (row.get("valid_from") or date.min) <= day <= (row.get("valid_to") or date.max)
        ]
        if dated:
            return dated[0]["rooms"]
        undated = [row for row in rows.get(kind, []) if not row.get("valid_from") and not row.get("valid_to")]
        return undated[0]["rooms"] if undated else 0

    result: dict[date, int | None] = {}
    closed: set[date] = set()
    for day in period:
        today = [closure for closure in closures if closure["date_from"] <= day <= closure["date_to"]]
        per_type = {kind: capacity(kind, day) for kind in types}
        before = sum(per_type.values())
        property_rooms_closed = 0
        for closure in today:
            if closure.get("room_type_id") is None:
                if closure.get("rooms") is None:
                    per_type = {kind: 0 for kind in per_type}
                elif room_type_id is None:
                    property_rooms_closed += closure["rooms"]
            elif closure["room_type_id"] in per_type:
                kind = closure["room_type_id"]
                per_type[kind] = 0 if closure.get("rooms") is None else max(0, per_type[kind] - closure["rooms"])
        open_rooms = max(0, sum(per_type.values()) - property_rooms_closed)
        if before > 0 and open_rooms == 0 and today:
            closed.add(day)
        result[day] = None if unknown else open_rooms
    return Availability(result, closed)


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def kpis(
    nights: Sequence[dict[str, Any]],
    bookings: Sequence[dict[str, Any]],
    available: int | None,
    amounts: str,
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """PerformanceKpis of the given nights (sold) and bookings (arriving)."""
    sold = sum(night["rooms"] for night in nights)
    revenue = sum(night_amount(night, amounts, settings) for night in nights)
    commission = sum(float(night["commission_eur"]) for night in nights)
    confirmed = [booking for booking in bookings if booking["status"] == "confirmed"]
    cancelled = sum(1 for booking in bookings if booking["status"] == "cancelled")
    stays = [(booking["departure"] - booking["arrival"]).days for booking in confirmed]
    leads = [(booking["arrival"] - booking["booked_on"]).days for booking in confirmed if booking.get("booked_on")]
    return {
        "occupancy_pct": _round(100 * sold / available, 1) if available else None,
        "adr_eur": _round(revenue / sold, 2) if sold else None,
        "revpar_eur": _round(revenue / available, 2) if available else None,
        "revenue_eur": round(revenue, 2),
        "room_nights_sold": sold,
        "room_nights_available": available,
        "bookings": len(confirmed),
        "cancellations_pct": _round(100 * cancelled / len(bookings), 1) if bookings else None,
        "avg_length_of_stay": _round(statistics.fmean(stays), 1) if stays else None,
        "avg_lead_time_days": _round(statistics.fmean(leads), 1) if leads else None,
        "commission_eur": round(commission, 2),
    }


def _change_pct(now: float | None, before: float | None) -> float | None:
    return _round(100 * (now / before - 1), 1) if now is not None and before else None


def delta(now: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    occupancy = (
        _round(now["occupancy_pct"] - before["occupancy_pct"], 1)
        if now["occupancy_pct"] is not None and before["occupancy_pct"] is not None
        else None
    )
    return {
        "occupancy_pts": occupancy,
        "adr_pct": _change_pct(now["adr_eur"], before["adr_eur"]),
        "revpar_pct": _change_pct(now["revpar_eur"], before["revpar_eur"]),
        "revenue_pct": _change_pct(now["revenue_eur"], before["revenue_eur"]),
    }


def median(prices: Iterable[float]) -> float | None:
    values = list(prices)
    return round(statistics.median(values), 2) if values else None


def in_period(rows: Iterable[dict[str, Any]], key: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
    return [row for row in rows if date_from <= row[key] <= date_to]


def channel_totals(nights: Sequence[dict[str, Any]], amounts: str, settings) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for night in nights:
        group = groups.setdefault(night["channel"], {"ids": set(), "room_nights": 0, "revenue": 0.0, "commission": 0.0})
        group["ids"].add(night["booking_id"])
        group["room_nights"] += night["rooms"]
        group["revenue"] += night_amount(night, amounts, settings)
        group["commission"] += float(night["commission_eur"])
    total = sum(group["room_nights"] for group in groups.values())
    rows = [
        {
            "channel": channel,
            "bookings": len(group["ids"]),
            "room_nights": group["room_nights"],
            "revenue_eur": round(group["revenue"], 2),
            "commission_eur": round(group["commission"], 2),
            "share_pct": round(100 * group["room_nights"] / total, 1) if total else 0.0,
        }
        for channel, group in groups.items()
    ]
    return sorted(rows, key=lambda row: (-row["revenue_eur"], row["channel"]))


def country_totals(nights: Sequence[dict[str, Any]], amounts: str, settings) -> list[dict[str, Any]]:
    """Top 10 booker countries by bookings, then «Λοιπές»; «Άγνωστη» where the file had none."""
    groups: dict[str, dict[str, Any]] = {}
    for night in nights:
        name = country_name(night.get("country_code"))
        group = groups.setdefault(name, {"ids": set(), "revenue": 0.0})
        group["ids"].add(night["booking_id"])
        group["revenue"] += night_amount(night, amounts, settings)
    ranked = sorted(groups.items(), key=lambda item: (-len(item[1]["ids"]), -item[1]["revenue"], item[0]))
    total = sum(len(group["ids"]) for group in groups.values())
    rows = [(name, len(group["ids"]), group["revenue"]) for name, group in ranked[:TOP_COUNTRIES]]
    rest = ranked[TOP_COUNTRIES:]
    if rest:
        rows.append((OTHER_COUNTRIES, sum(len(group["ids"]) for _, group in rest), sum(g["revenue"] for _, g in rest)))
    return [
        {
            "country": name,
            "bookings": count,
            "revenue_eur": round(revenue, 2),
            "share_pct": round(100 * count / total, 1) if total else 0.0,
        }
        for name, count, revenue in rows
    ]


# ---------------------------------------------------------------------------
# How long guests stay and how early they book
# ---------------------------------------------------------------------------

# (bucket, from, to): nights stayed; days between booking and arrival.
LENGTH_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1), ("2-3", 2, 3), ("4-5", 4, 5), ("6-9", 6, 9), ("10+", 10, None),
)
LEAD_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-7", 0, 7), ("8-30", 8, 30), ("31-60", 31, 60), ("61+", 61, None),
)


def bucket(value: int, buckets: Sequence[tuple[str, int, int | None]]) -> str:
    for name, low, high in buckets:
        if value >= low and (high is None or value <= high):
            return name
    return buckets[0][0]


def _shares(counts: dict[str, int], buckets: Sequence[tuple[str, int, int | None]]) -> list[dict[str, Any]]:
    total = sum(counts.values())
    return [
        {
            "bucket": name,
            "bookings": counts.get(name, 0),
            "share_pct": round(100 * counts.get(name, 0) / total, 1) if total else 0.0,
        }
        for name, _, _ in buckets
    ]


def stay_patterns(bookings: Sequence[dict[str, Any]], date_from: date, date_to: date) -> dict[str, Any]:
    """PerformanceStayPatterns of the confirmed bookings arriving in the period.

    Lead time and the length × lead matrix count only the bookings whose
    booking date is known.
    """
    confirmed = [booking for booking in bookings if booking["status"] == "confirmed"]
    nights = {id(booking): (booking["departure"] - booking["arrival"]).days for booking in confirmed}
    dated = [booking for booking in confirmed if booking.get("booked_on")]
    leads = {id(booking): (booking["arrival"] - booking["booked_on"]).days for booking in dated}

    lengths: dict[str, int] = defaultdict(int)
    for booking in confirmed:
        lengths[bucket(nights[id(booking)], LENGTH_BUCKETS)] += 1
    lead_counts: dict[str, int] = defaultdict(int)
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for booking in dated:
        lead_name = bucket(leads[id(booking)], LEAD_BUCKETS)
        lead_counts[lead_name] += 1
        pairs[(bucket(nights[id(booking)], LENGTH_BUCKETS), lead_name)] += 1

    monthly = []
    for month, first, last in months(date_from, date_to):
        values = [leads[id(booking)] for booking in dated if first <= booking["arrival"] <= last]
        monthly.append({"month": month, "avg_lead_time_days": _round(statistics.fmean(values), 1) if values else None})
    guests = [
        booking["adults"] + (booking.get("children") or 0) for booking in confirmed if booking.get("adults") is not None
    ]
    return {
        "length_of_stay": _shares(lengths, LENGTH_BUCKETS),
        "lead_time": _shares(lead_counts, LEAD_BUCKETS),
        "matrix": [
            {"length_bucket": length, "lead_bucket": lead, "bookings": pairs.get((length, lead), 0)}
            for length, _, _ in LENGTH_BUCKETS
            for lead, _, _ in LEAD_BUCKETS
        ],
        "monthly_lead_time": monthly,
        "avg_length_of_stay": _round(statistics.fmean(nights.values()), 1) if nights else None,
        "avg_lead_time_days": _round(statistics.fmean(leads.values()), 1) if leads else None,
        "avg_guests": _round(statistics.fmean(guests), 1) if guests else None,
    }


# ---------------------------------------------------------------------------
# On the books: what is booked for a season, now and on the same day last year
# ---------------------------------------------------------------------------


def on_the_books(night: dict[str, Any], as_of: date, *, booking_date_known: bool) -> bool:
    """Whether the night was held on ``as_of``: booked by then and not cancelled by then.

    A booking without a booking date counts as held today (``booking_date_known``
    False) but never on a past day: nobody knows since when it was.
    """
    booked_on = night.get("booked_on")
    if booked_on is None:
        if booking_date_known:
            return False
        return night["status"] != "cancelled"
    if booked_on > as_of:
        return False
    if night["status"] == "cancelled":
        cancelled_on = night.get("cancelled_on")
        return cancelled_on is not None and cancelled_on > as_of
    return True


# ---------------------------------------------------------------------------
# The calls
# ---------------------------------------------------------------------------


class PerformanceKpiService:
    def __init__(self, repository: PerformanceRepository):
        self.repository = repository

    def _load(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        if filters.date_to < filters.date_from:
            raise KpiRejected("Η περίοδος τελειώνει πριν αρχίσει.")
        if (filters.date_to - filters.date_from).days >= MAX_PERIOD_DAYS:
            raise KpiRejected("Διαλέξτε περίοδο έως 3 χρόνια.")
        owned_property_id = owned_property["id"]
        room_types = self.repository.room_types(account_id, owned_property_id)
        if filters.room_type_id and filters.room_type_id not in {room["id"] for room in room_types}:
            raise KpiRejected("Ο τύπος δωματίου δεν ανήκει στο κατάλυμα.")
        windows = [(filters.date_from, filters.date_to)]
        last = filters.last_year() if filters.compare == "last_year" else None
        if last:
            windows.append((last.date_from, last.date_to))
        nights = self.repository.sold_nights(account_id, owned_property_id, windows, filters.room_type_id)
        bookings = self.repository.bookings_arriving(account_id, owned_property_id, windows, filters.room_type_id)
        inventory = self.repository.inventory(account_id, owned_property_id)
        closures = self.repository.closures(account_id, owned_property_id)
        settings = (
            self.repository.revenue_settings(account_id, owned_property_id) if filters.amounts == "net" else None
        )

        def window(f: Filters) -> dict[str, Any]:
            period_nights = in_period(nights, "stay_date", f.date_from, f.date_to)
            return {
                "filters": f,
                "nights": period_nights,
                "bookings": in_period(bookings, "arrival", f.date_from, f.date_to),
                "availability": availability(
                    days(f.date_from, f.date_to),
                    [room["id"] for room in room_types],
                    inventory,
                    closures,
                    room_type_id=f.room_type_id,
                    sold_room_types={night["room_type_id"] for night in period_nights if night["room_type_id"]},
                ),
            }

        now = window(filters)
        before = window(last) if last else None
        if before and not before["nights"] and not before["bookings"]:
            before = None  # no data a year earlier
        return {
            "now": now, "before": before, "settings": settings, "room_types": room_types,
            "inventory": inventory, "closures": closures,
        }

    def _market(
        self,
        account_id: uuid.UUID,
        owned_property: dict[str, Any],
        filters: Filters,
        room_types: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        category = owned_property.get("selected_room_type_category")
        if filters.room_type_id:
            category = next(
                (room.get("category") for room in room_types if room["id"] == filters.room_type_id), category
            )
        return self._market_for(account_id, owned_property, filters, category)

    def _market_for(
        self,
        account_id: uuid.UUID,
        owned_property: dict[str, Any],
        filters: Filters,
        category: str | None,
    ) -> list[dict[str, Any]]:
        """Competitors' prices for the period's check-ins, rooms comparable with ``category`` (None: every room)."""
        destination = owned_property.get("canonical_destination")
        if not destination:
            return []
        return self.repository.market_prices(
            account_id,
            destination,
            filters.date_from,
            filters.date_to,
            comparable_room_type_categories(category),
        )

    def _kpis(self, part: dict[str, Any], filters: Filters, settings) -> dict[str, Any]:
        period = days(part["filters"].date_from, part["filters"].date_to)
        return kpis(part["nights"], part["bookings"], part["availability"].total(period), filters.amounts, settings)

    def summary(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, filters)
        now = self._kpis(data["now"], filters, data["settings"])
        before = self._kpis(data["before"], filters, data["settings"]) if data["before"] else None
        market = self._market(account_id, owned_property, filters, data["room_types"])
        return {
            "period": {"date_from": filters.date_from, "date_to": filters.date_to},
            "amounts": filters.amounts,
            "kpis": now,
            "last_year": before,
            "delta": delta(now, before) if before else None,
            "market_median_eur": median(row["price_eur"] for row in market),
        }

    def monthly(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, filters)
        now, before, settings = data["now"], data["before"], data["settings"]
        market = self._market(account_id, owned_property, filters, data["room_types"])
        prices: dict[str, list[float]] = defaultdict(list)
        for row in market:
            prices[month_of(row["check_in"])].append(row["price_eur"])
        result = []
        for month, first, last in months(filters.date_from, filters.date_to):
            period = days(first, last)
            figures = kpis(
                in_period(now["nights"], "stay_date", first, last),
                in_period(now["bookings"], "arrival", first, last),
                now["availability"].total(period),
                filters.amounts,
                settings,
            )
            last_year = None
            if before:
                ly_first, ly_last = a_year_earlier(first), a_year_earlier(last)
                earlier = kpis(
                    in_period(before["nights"], "stay_date", ly_first, ly_last),
                    in_period(before["bookings"], "arrival", ly_first, ly_last),
                    before["availability"].total(days(ly_first, ly_last)),
                    filters.amounts,
                    settings,
                )
                last_year = {key: earlier[key] for key in ("occupancy_pct", "adr_eur", "revpar_eur", "revenue_eur")}
            available = figures["room_nights_available"]
            result.append(
                {
                    "month": month,
                    "occupancy_pct": figures["occupancy_pct"],
                    "adr_eur": figures["adr_eur"],
                    "revpar_eur": figures["revpar_eur"],
                    "revenue_eur": figures["revenue_eur"],
                    "bookings": figures["bookings"],
                    "room_nights_sold": figures["room_nights_sold"],
                    "room_nights_available": available,
                    "room_nights_unsold": (
                        None if available is None else max(0, available - figures["room_nights_sold"])
                    ),
                    "closed_days": sum(1 for day in period if day in now["availability"].closed),
                    "market_median_eur": median(prices.get(month, [])),
                    "last_year": last_year,
                }
            )
        return {"amounts": filters.amounts, "months": result}

    def channels(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, replace(filters, compare="none"))
        nights, settings = data["now"]["nights"], data["settings"]
        totals = channel_totals(nights, filters.amounts, settings)
        names = [row["channel"] for row in totals]
        monthly = []
        for month, first, last in months(filters.date_from, filters.date_to):
            revenue = {name: 0.0 for name in names}
            for night in in_period(nights, "stay_date", first, last):
                revenue[night["channel"]] += night_amount(night, filters.amounts, settings)
            monthly.append(
                {"month": month, "revenue_by_channel": {name: round(value, 2) for name, value in revenue.items()}}
            )
        return {"amounts": filters.amounts, "totals": totals, "monthly": monthly}

    def countries(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, replace(filters, compare="none"))
        return {
            "amounts": filters.amounts,
            "countries": country_totals(data["now"]["nights"], filters.amounts, data["settings"]),
        }

    def room_types(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        """Each room type over the period (the room type filter does not apply), highest revenue first."""
        filters = replace(filters, room_type_id=None, compare="none")
        data = self._load(account_id, owned_property, filters)
        nights, arrivals, settings = data["now"]["nights"], data["now"]["bookings"], data["settings"]
        period = days(filters.date_from, filters.date_to)
        ids = [room["id"] for room in data["room_types"]]
        markets: dict[tuple[str, ...], float | None] = {}
        rows = []
        for room in data["room_types"]:
            own_nights = [night for night in nights if night["room_type_id"] == room["id"]]
            open_rooms = availability(
                period, ids, data["inventory"], data["closures"], room_type_id=room["id"]
            ).total(period)
            figures = kpis(
                own_nights,
                [booking for booking in arrivals if booking.get("room_type_id") == room["id"]],
                open_rooms,
                filters.amounts,
                settings,
            )
            pool = comparable_room_type_categories(room.get("category"))
            if pool not in markets:
                market = self._market_for(account_id, owned_property, filters, room.get("category"))
                markets[pool] = median(row["price_eur"] for row in market)
            rows.append(
                {
                    "id": room["id"],
                    "label": room["label"],
                    "rooms": room["rooms"],
                    "occupancy_pct": figures["occupancy_pct"],
                    "adr_eur": figures["adr_eur"],
                    "revpar_eur": figures["revpar_eur"],
                    "revenue_eur": figures["revenue_eur"],
                    "revenue_share_pct": 0.0,
                    "bookings": figures["bookings"],
                    "room_nights_sold": figures["room_nights_sold"],
                    "cancellations_pct": figures["cancellations_pct"],
                    "market_median_eur": markets[pool],
                }
            )
        total = sum(row["revenue_eur"] for row in rows)
        for row in rows:
            row["revenue_share_pct"] = round(100 * row["revenue_eur"] / total, 1) if total else 0.0
        rows.sort(key=lambda row: (-row["revenue_eur"], row["label"]))
        return {"amounts": filters.amounts, "room_types": rows}

    def stay_patterns(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, replace(filters, compare="none"))
        return stay_patterns(data["now"]["bookings"], filters.date_from, filters.date_to)

    def pace(self, account_id: uuid.UUID, owned_property: dict[str, Any], season: int, today: date) -> dict[str, Any]:
        """PerformancePace: the season's nights on the books today, against last season on the same day."""
        if not 2000 <= season <= 2100:
            raise KpiRejected("Μη έγκυρη σεζόν.")
        owned_property_id = owned_property["id"]
        first, last = date(season, 1, 1), date(season, 12, 31)
        last_year_today = a_year_earlier(today)
        held = self.repository.held_nights(
            account_id, owned_property_id, [(first, last), (a_year_earlier(first), a_year_earlier(last))]
        )
        room_types = self.repository.room_types(account_id, owned_property_id)
        inventory = self.repository.inventory(account_id, owned_property_id)
        closures = self.repository.closures(account_id, owned_property_id)
        ids = [room["id"] for room in room_types]

        now = [night for night in held if first <= night["stay_date"] <= last
               and on_the_books(night, today, booking_date_known=False)]
        last_season = [night for night in held if night["stay_date"] < first]
        before = [night for night in last_season if on_the_books(night, last_year_today, booking_date_known=True)]
        # The season's months: what is booked now, or what last season sold in the end.
        sold_months = {
            f"{season:04d}-{night['stay_date'].month:02d}" for night in last_season if night["status"] == "confirmed"
        }
        sold_types = {night["room_type_id"] for night in now + before if night["room_type_id"]}
        open_now = availability(days(first, last), ids, inventory, closures, sold_room_types=sold_types)
        open_before = availability(
            days(a_year_earlier(first), a_year_earlier(last)), ids, inventory, closures, sold_room_types=sold_types
        )

        result = []
        for month, month_first, month_last in months(first, last):
            period = days(month_first, month_last)
            earlier = days(a_year_earlier(month_first), a_year_earlier(month_last))
            held_now = sum(night["rooms"] for night in in_period(now, "stay_date", month_first, month_last))
            held_before = sum(night["rooms"] for night in in_period(before, "stay_date", earlier[0], earlier[-1]))
            rooms_now, rooms_before = open_now.total(period), open_before.total(earlier)
            if not (held_now or held_before or month in sold_months):
                continue  # nothing booked now or then, nothing sold last season: not part of the season
            result.append(
                {
                    "month": month,
                    "otb_occupancy_pct": _round(100 * held_now / rooms_now, 1) if rooms_now else None,
                    "otb_room_nights": held_now,
                    "last_year_same_day_pct": _round(100 * held_before / rooms_before, 1) if rooms_before else None,
                }
            )
        week_ago = today - timedelta(days=7)
        picked = [night for night in now if night.get("booked_on") and week_ago < night["booked_on"] <= today]
        return {
            "season": season,
            "as_of": today,
            "months": result,
            "pickup_7d": {
                "bookings": len({night["booking_id"] for night in picked}),
                "room_nights": sum(night["rooms"] for night in picked),
            },
        }
