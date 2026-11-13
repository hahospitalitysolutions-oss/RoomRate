"""The KPIs of a set of nights and bookings, the change since last year, and the market median."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from typing import Any

from api.services.performance.amounts import night_amount


def rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def mean(values: Sequence[float], digits: int = 1) -> float | None:
    return rounded(statistics.fmean(values), digits) if values else None


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
    confirmed = [booking for booking in bookings if booking["status"] == "confirmed"]
    cancelled = sum(1 for booking in bookings if booking["status"] == "cancelled")
    return {
        "occupancy_pct": rounded(100 * sold / available, 1) if available else None,
        "adr_eur": rounded(revenue / sold, 2) if sold else None,
        "revpar_eur": rounded(revenue / available, 2) if available else None,
        "revenue_eur": round(revenue, 2),
        "room_nights_sold": sold,
        "room_nights_available": available,
        "bookings": len(confirmed),
        "cancellations_pct": rounded(100 * cancelled / len(bookings), 1) if bookings else None,
        "avg_length_of_stay": mean([(b["departure"] - b["arrival"]).days for b in confirmed]),
        "avg_lead_time_days": mean([(b["arrival"] - b["booked_on"]).days for b in confirmed if b.get("booked_on")]),
        "commission_eur": round(sum(float(night["commission_eur"]) for night in nights), 2),
    }


def _change_pct(now: float | None, before: float | None) -> float | None:
    return rounded(100 * (now / before - 1), 1) if now is not None and before else None


def delta(now: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    both = now["occupancy_pct"] is not None and before["occupancy_pct"] is not None
    return {
        "occupancy_pts": rounded(now["occupancy_pct"] - before["occupancy_pct"], 1) if both else None,
        "adr_pct": _change_pct(now["adr_eur"], before["adr_eur"]),
        "revpar_pct": _change_pct(now["revpar_eur"], before["revpar_eur"]),
        "revenue_pct": _change_pct(now["revenue_eur"], before["revenue_eur"]),
    }


def median(prices: Iterable[float]) -> float | None:
    values = list(prices)
    return round(statistics.median(values), 2) if values else None
