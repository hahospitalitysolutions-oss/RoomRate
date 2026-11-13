"""The period broken down: by channel, by booker country, by room type."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from api.services.performance.amounts import night_amount
from api.services.performance.countries import OTHER_COUNTRIES, country_name
from api.services.performance.kpis.availability import Availability
from api.services.performance.kpis.figures import kpis
from api.services.performance.kpis.periods import by_month, days, months

TOP_COUNTRIES = 10


def _share(part: float, total: float) -> float:
    return round(100 * part / total, 1) if total else 0.0


def channel_totals(nights: Sequence[dict[str, Any]], amounts: str, settings) -> list[dict[str, Any]]:
    """Bookings, nights, revenue and commission per channel, highest revenue first; share of nights."""
    groups: dict[str, dict[str, Any]] = {}
    for night in nights:
        group = groups.setdefault(night["channel"], {"ids": set(), "nights": 0, "revenue": 0.0, "commission": 0.0})
        group["ids"].add(night["booking_id"])
        group["nights"] += night["rooms"]
        group["revenue"] += night_amount(night, amounts, settings)
        group["commission"] += float(night["commission_eur"])
    total = sum(group["nights"] for group in groups.values())
    rows = [
        {
            "channel": channel,
            "bookings": len(group["ids"]),
            "room_nights": group["nights"],
            "revenue_eur": round(group["revenue"], 2),
            "commission_eur": round(group["commission"], 2),
            "share_pct": _share(group["nights"], total),
        }
        for channel, group in groups.items()
    ]
    return sorted(rows, key=lambda row: (-row["revenue_eur"], row["channel"]))


def channel_months(nights, channels: list[str], date_from, date_to, amounts: str, settings) -> list[dict[str, Any]]:
    """Revenue per channel per month of the period, every channel in every month."""
    grouped = by_month(nights, "stay_date")
    result = []
    for month, _, _ in months(date_from, date_to):
        revenue = dict.fromkeys(channels, 0.0)
        for night in grouped.get(month, []):
            revenue[night["channel"]] += night_amount(night, amounts, settings)
        rounded = {name: round(value, 2) for name, value in revenue.items()}
        result.append({"month": month, "revenue_by_channel": rounded})
    return result


def country_totals(nights: Sequence[dict[str, Any]], amounts: str, settings) -> list[dict[str, Any]]:
    """Top 10 booker countries by bookings, then «Λοιπές»; «Άγνωστη» where the file had none."""
    groups: dict[str, dict[str, Any]] = {}
    for night in nights:
        group = groups.setdefault(country_name(night.get("country_code")), {"ids": set(), "revenue": 0.0})
        group["ids"].add(night["booking_id"])
        group["revenue"] += night_amount(night, amounts, settings)
    ranked = sorted(groups.items(), key=lambda item: (-len(item[1]["ids"]), -item[1]["revenue"], item[0]))
    rows = [(name, len(group["ids"]), group["revenue"]) for name, group in ranked[:TOP_COUNTRIES]]
    rest = ranked[TOP_COUNTRIES:]
    if rest:
        rows.append((OTHER_COUNTRIES, sum(len(g["ids"]) for _, g in rest), sum(g["revenue"] for _, g in rest)))
    total = sum(count for _, count, _ in rows)
    return [
        {"country": name, "bookings": count, "revenue_eur": round(revenue, 2), "share_pct": _share(count, total)}
        for name, count, revenue in rows
    ]


def room_type_rows(
    room_types: list[dict[str, Any]],
    nights: Sequence[dict[str, Any]],
    arrivals: Sequence[dict[str, Any]],
    open_rooms: Callable[[Any], Availability],
    market_median: Callable[[str | None], float | None],
    period: tuple,
    amounts: str,
    settings,
) -> list[dict[str, Any]]:
    """RoomTypePerformance per room type, highest revenue first.

    ``open_rooms(room_type_id)`` gives the type's availability over the
    period; ``market_median(category)`` the market for its room category.
    """
    period_days = days(*period)
    rows = []
    for room in room_types:
        figures = kpis(
            [night for night in nights if night["room_type_id"] == room["id"]],
            [booking for booking in arrivals if booking.get("room_type_id") == room["id"]],
            open_rooms(room["id"]).total(period_days),
            amounts,
            settings,
        )
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
                "market_median_eur": market_median(room.get("category")),
            }
        )
    total = sum(row["revenue_eur"] for row in rows)
    for row in rows:
        row["revenue_share_pct"] = _share(row["revenue_eur"], total)
    return sorted(rows, key=lambda row: (-row["revenue_eur"], row["label"]))
