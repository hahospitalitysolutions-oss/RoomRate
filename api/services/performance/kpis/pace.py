"""On the books (PerformancePace): what is booked for a season today, against last season on the same day."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from api.services.performance.kpis.availability import Availability
from api.services.performance.kpis.figures import rounded
from api.services.performance.kpis.periods import a_year_earlier, by_month, days, month_of, months


def on_the_books(night: dict[str, Any], as_of: date, *, booking_date_known: bool) -> bool:
    """Whether the night was held on ``as_of``: booked by then and not cancelled by then.

    A booking without a booking date counts as held today (``booking_date_known``
    False) but never on a past day: nobody knows since when it was.
    """
    booked_on = night.get("booked_on")
    if booked_on is None:
        return not booking_date_known and night["status"] != "cancelled"
    if booked_on > as_of:
        return False
    if night["status"] == "cancelled":
        cancelled_on = night.get("cancelled_on")
        return cancelled_on is not None and cancelled_on > as_of
    return True


def season_pace(
    season: int,
    today: date,
    held: list[dict[str, Any]],
    open_rooms: Callable[[list[date], set], Availability],
) -> dict[str, Any]:
    """PerformancePace from every night of the season and of the one before (``held``).

    ``open_rooms(days, booked_room_types)`` gives the availability of those
    days. The months shown are the ones booked now or then, or sold last
    season in the end.
    """
    first, last = date(season, 1, 1), date(season, 12, 31)
    now = [n for n in held if n["stay_date"] >= first and on_the_books(n, today, booking_date_known=False)]
    last_season = [n for n in held if n["stay_date"] < first]
    then = [n for n in last_season if on_the_books(n, a_year_earlier(today), booking_date_known=True)]
    sold_months = {f"{season:04d}-{n['stay_date'].month:02d}" for n in last_season if n["status"] == "confirmed"}
    booked_types = {n["room_type_id"] for n in now + then if n["room_type_id"]}
    open_now = open_rooms(days(first, last), booked_types)
    open_then = open_rooms(days(a_year_earlier(first), a_year_earlier(last)), booked_types)
    now_by_month, then_by_month = by_month(now, "stay_date"), by_month(then, "stay_date")

    result = []
    for month, month_first, month_last in months(first, last):
        earlier = days(a_year_earlier(month_first), a_year_earlier(month_last))
        held_now = sum(n["rooms"] for n in now_by_month.get(month, []))
        held_then = sum(n["rooms"] for n in then_by_month.get(month_of(earlier[0]), []))
        if not (held_now or held_then or month in sold_months):
            continue
        rooms_now, rooms_then = open_now.total(days(month_first, month_last)), open_then.total(earlier)
        result.append(
            {
                "month": month,
                "otb_occupancy_pct": rounded(100 * held_now / rooms_now, 1) if rooms_now else None,
                "otb_room_nights": held_now,
                "last_year_same_day_pct": rounded(100 * held_then / rooms_then, 1) if rooms_then else None,
            }
        )
    week_ago = today - timedelta(days=7)
    picked = [n for n in now if n.get("booked_on") and week_ago < n["booked_on"] <= today]
    return {
        "season": season,
        "as_of": today,
        "months": result,
        "pickup_7d": {
            "bookings": len({n["booking_id"] for n in picked}),
            "room_nights": sum(n["rooms"] for n in picked),
        },
    }
