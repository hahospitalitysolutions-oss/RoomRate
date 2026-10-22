"""The period month by month (PerformanceMonthly): KPIs, unsold and closed nights, market, last year."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from api.services.performance.kpis.availability import Availability
from api.services.performance.kpis.figures import kpis, median
from api.services.performance.kpis.periods import a_year_earlier, by_month, days, month_of, months

_LAST_YEAR_KEYS = ("occupancy_pct", "adr_eur", "revpar_eur", "revenue_eur")


def month_rows(
    now: dict[str, Any],
    before: dict[str, Any] | None,
    market: list[dict[str, Any]],
    amounts: str,
    settings: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """MonthKpis for each month of ``now["filters"]``; ``now`` / ``before`` as KpiService loads them."""
    filters = now["filters"]
    nights, arrivals = by_month(now["nights"], "stay_date"), by_month(now["bookings"], "arrival")
    if before:
        nights_then, arrivals_then = by_month(before["nights"], "stay_date"), by_month(before["bookings"], "arrival")
    prices: dict[str, list[float]] = defaultdict(list)
    for row in market:
        prices[month_of(row["check_in"])].append(row["price_eur"])

    result = []
    for month, first, last in months(filters.date_from, filters.date_to):
        period = days(first, last)
        open_rooms = now["availability"].total(period)
        figures = kpis(nights.get(month, []), arrivals.get(month, []), open_rooms, amounts, settings)
        last_year = None
        if before:
            earlier = days(a_year_earlier(first), a_year_earlier(last))
            then = month_of(earlier[0])
            past = kpis(
                nights_then.get(then, []), arrivals_then.get(then, []), before["availability"].total(earlier),
                amounts, settings,
            )
            last_year = {key: past[key] for key in _LAST_YEAR_KEYS}
        result.append(_month(month, figures, period, now["availability"], prices.get(month, []), last_year))
    return result


def _month(month: str, figures: dict[str, Any], period, open_rooms: Availability, prices, last_year) -> dict[str, Any]:
    available = figures["room_nights_available"]
    return {
        "month": month,
        "occupancy_pct": figures["occupancy_pct"],
        "adr_eur": figures["adr_eur"],
        "revpar_eur": figures["revpar_eur"],
        "revenue_eur": figures["revenue_eur"],
        "bookings": figures["bookings"],
        "room_nights_sold": figures["room_nights_sold"],
        "room_nights_available": available,
        "room_nights_unsold": None if available is None else max(0, available - figures["room_nights_sold"]),
        "closed_days": sum(1 for day in period if day in open_rooms.closed),
        "market_median_eur": median(prices),
        "last_year": last_year,
    }
