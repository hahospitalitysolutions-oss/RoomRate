"""How long guests stay and how early they book (PerformanceStayPatterns), from the confirmed arrivals."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Any

from api.services.performance.kpis.figures import mean
from api.services.performance.kpis.periods import by_month, months

Buckets = tuple[tuple[str, int, int | None], ...]

# (bucket, from, to): nights stayed; days between booking and arrival.
LENGTH_BUCKETS: Buckets = (("1", 1, 1), ("2-3", 2, 3), ("4-5", 4, 5), ("6-9", 6, 9), ("10+", 10, None))
LEAD_BUCKETS: Buckets = (("0-7", 0, 7), ("8-30", 8, 30), ("31-60", 31, 60), ("61+", 61, None))


def bucket(value: int, buckets: Buckets) -> str:
    for name, low, high in buckets:
        if value >= low and (high is None or value <= high):
            return name
    return buckets[0][0]


def _shares(counts: Counter, buckets: Buckets) -> list[dict[str, Any]]:
    total = sum(counts.values())
    return [
        {"bucket": name, "bookings": counts[name], "share_pct": round(100 * counts[name] / total, 1) if total else 0.0}
        for name, _, _ in buckets
    ]


def stay_patterns(bookings: Sequence[dict[str, Any]], date_from: date, date_to: date) -> dict[str, Any]:
    """PerformanceStayPatterns of the confirmed bookings arriving in the period.

    Lead time and the length × lead matrix count only the bookings whose
    booking date is known.
    """
    stays = [
        {
            "arrival": booking["arrival"],
            "nights": (booking["departure"] - booking["arrival"]).days,
            "lead": (booking["arrival"] - booking["booked_on"]).days if booking.get("booked_on") else None,
            "guests": booking["adults"] + (booking.get("children") or 0) if booking.get("adults") is not None else None,
        }
        for booking in bookings
        if booking["status"] == "confirmed"
    ]
    dated = [stay for stay in stays if stay["lead"] is not None]
    lengths = Counter(bucket(stay["nights"], LENGTH_BUCKETS) for stay in stays)
    leads = Counter(bucket(stay["lead"], LEAD_BUCKETS) for stay in dated)
    pairs = Counter((bucket(stay["nights"], LENGTH_BUCKETS), bucket(stay["lead"], LEAD_BUCKETS)) for stay in dated)
    dated_by_month = by_month(dated, "arrival")
    return {
        "length_of_stay": _shares(lengths, LENGTH_BUCKETS),
        "lead_time": _shares(leads, LEAD_BUCKETS),
        "matrix": [
            {"length_bucket": length, "lead_bucket": lead, "bookings": pairs[(length, lead)]}
            for length, _, _ in LENGTH_BUCKETS
            for lead, _, _ in LEAD_BUCKETS
        ],
        "monthly_lead_time": [
            {"month": month, "avg_lead_time_days": mean([stay["lead"] for stay in dated_by_month.get(month, [])])}
            for month, _, _ in months(date_from, date_to)
        ],
        "avg_length_of_stay": mean([stay["nights"] for stay in stays]),
        "avg_lead_time_days": mean([stay["lead"] for stay in dated]),
        "avg_guests": mean([stay["guests"] for stay in stays if stay["guests"] is not None]),
    }
