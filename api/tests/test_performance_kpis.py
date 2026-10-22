"""«Απόδοση» KPIs: rooms open for sale, occupancy, ADR, RevPAR, net amounts, last year, channels, countries."""

import uuid
from datetime import date, timedelta

import pytest

from api.services.performance.kpis import Filters, KpiRejected, PerformanceKpiService
from api.services.performance.kpis.availability import availability
from api.services.performance.kpis.breakdowns import channel_totals, country_totals
from api.services.performance.kpis.figures import kpis
from api.services.performance.kpis.pace import on_the_books
from api.services.performance.kpis.patterns import LEAD_BUCKETS, LENGTH_BUCKETS, bucket, stay_patterns
from api.services.performance.kpis.periods import a_year_earlier, days, months

DOUBLE, SUITE, SPARE = uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)
ACCOUNT_ID = uuid.UUID(int=1)
PROPERTY = {"id": uuid.UUID(int=2), "canonical_destination": "rhodes", "selected_room_type_category": "double"}
JULY = days(date(2026, 7, 1), date(2026, 7, 31))


def inventory(kind, rooms, valid_from=None, valid_to=None):
    return {"room_type_id": kind, "rooms": rooms, "valid_from": valid_from, "valid_to": valid_to}


def closure(date_from, date_to, room_type_id=None, rooms=None):
    return {"date_from": date_from, "date_to": date_to, "room_type_id": room_type_id, "rooms": rooms}


def night(day, gross=100.0, commission=0.0, rooms=1, kind=DOUBLE, channel="Booking.com", country="DE", booking="b1"):
    return {"stay_date": day, "room_type_id": kind, "rooms": rooms, "gross_eur": gross, "commission_eur": commission,
            "channel": channel, "country_code": country, "booking_id": booking}


def booking(arrival, nights=3, status="confirmed", lead=30, kind=DOUBLE, adults=2, children=0):
    return {"status": status, "arrival": arrival, "departure": arrival + timedelta(days=nights),
            "booked_on": arrival - timedelta(days=lead) if lead is not None else None, "room_type_id": kind,
            "adults": adults, "children": children}


# ---------------------------------------------------------------------------
# Rooms open for sale
# ---------------------------------------------------------------------------


def test_rooms_per_day_from_the_counts_with_a_dated_count_winning():
    rooms = availability(
        JULY, [DOUBLE, SUITE],
        [inventory(DOUBLE, 5), inventory(SUITE, 1), inventory(DOUBLE, 3, date(2026, 7, 10), date(2026, 7, 19))],
        [],
    )

    assert rooms.rooms[date(2026, 7, 1)] == 6 and rooms.rooms[date(2026, 7, 10)] == 4
    assert rooms.total(JULY) == 31 * 6 - 10 * 2


def test_closures_whole_property_a_room_type_or_some_rooms():
    closures = [
        closure(date(2026, 7, 1), date(2026, 7, 3)),  # the property
        closure(date(2026, 7, 4), date(2026, 7, 4), SUITE),  # the suite
        closure(date(2026, 7, 5), date(2026, 7, 5), DOUBLE, rooms=2),  # two doubles
        closure(date(2026, 7, 6), date(2026, 7, 6), None, rooms=3),  # three rooms, anywhere
    ]
    whole = availability(JULY, [DOUBLE, SUITE], [inventory(DOUBLE, 5), inventory(SUITE, 1)], closures)
    suite = availability(JULY, [DOUBLE, SUITE], [inventory(DOUBLE, 5), inventory(SUITE, 1)], closures, room_type_id=SUITE)

    assert [whole.rooms[date(2026, 7, day)] for day in range(1, 8)] == [0, 0, 0, 5, 4, 3, 6]
    assert whole.closed == {date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)}
    assert [suite.rooms[date(2026, 7, day)] for day in range(1, 8)] == [0, 0, 0, 0, 1, 1, 1]
    assert date(2026, 7, 4) in suite.closed  # closed for the filtered room type


def test_a_sold_room_type_without_a_count_makes_occupancy_unknown_never_zero():
    counts = [inventory(DOUBLE, 5)]

    assert availability(JULY, [DOUBLE, SUITE], counts, [], sold_room_types={SUITE}).total(JULY) is None
    # A room type nobody booked counts as no rooms.
    assert availability(JULY, [DOUBLE, SPARE], counts, [], sold_room_types={DOUBLE}).total(JULY) == 155
    assert availability(JULY, [DOUBLE, SUITE], counts, [], room_type_id=SUITE).total(JULY) is None
    assert availability(JULY, [DOUBLE], [], []).total(JULY) is None


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def test_occupancy_adr_revpar_and_booking_habits():
    nights = [night(date(2026, 7, 1), 120.0, 20.0), night(date(2026, 7, 2), 80.0, 0.0, rooms=2)]
    arrivals = [booking(date(2026, 7, 1), 2, lead=40), booking(date(2026, 7, 3), 4, lead=None),
                booking(date(2026, 7, 9), status="cancelled"), booking(date(2026, 7, 9), status="no_show")]
    figures = kpis(nights, arrivals, 10, "gross", None)

    assert figures == {
        "occupancy_pct": 30.0, "adr_eur": 66.67, "revpar_eur": 20.0, "revenue_eur": 200.0,
        "room_nights_sold": 3, "room_nights_available": 10, "bookings": 2, "cancellations_pct": 25.0,
        "avg_length_of_stay": 3.0, "avg_lead_time_days": 40.0, "commission_eur": 20.0,
    }


def test_net_takes_off_the_fee_in_the_price_vat_and_commission():
    settings = {"vat_pct": 13.0, "resilience_fee_eur": 1.5, "fee_included_in_price": True}
    figures = kpis([night(date(2026, 7, 1), 114.5, 17.0, rooms=1)], [], 1, "net", settings)

    assert figures["revenue_eur"] == round((114.5 - 1.5) / 1.13 - 17.0, 2) == 83.0
    assert figures["commission_eur"] == 17.0


def test_nothing_to_divide_by_gives_none():
    figures = kpis([], [], 0, "gross", None)

    assert (figures["occupancy_pct"], figures["adr_eur"], figures["revpar_eur"]) == (None, None, None)
    assert figures["cancellations_pct"] is None and figures["avg_length_of_stay"] is None


def test_dates_a_year_earlier_and_months_of_a_period():
    assert a_year_earlier(date(2028, 2, 29)) == date(2027, 2, 28)
    assert months(date(2026, 5, 15), date(2026, 7, 3)) == [
        ("2026-05", date(2026, 5, 15), date(2026, 5, 31)),
        ("2026-06", date(2026, 6, 1), date(2026, 6, 30)),
        ("2026-07", date(2026, 7, 1), date(2026, 7, 3)),
    ]


def test_channels_by_revenue_with_their_share_of_nights():
    nights = [night(date(2026, 7, 1), 200, 34, booking="a"), night(date(2026, 7, 2), 200, 34, booking="a"),
              night(date(2026, 7, 1), 150, 0, channel="Απευθείας", booking="b", rooms=2)]
    totals = channel_totals(nights, "gross", None)

    assert totals == [
        {"channel": "Booking.com", "bookings": 1, "room_nights": 2, "revenue_eur": 400.0, "commission_eur": 68.0,
         "share_pct": 50.0},
        {"channel": "Απευθείας", "bookings": 1, "room_nights": 2, "revenue_eur": 150.0, "commission_eur": 0.0,
         "share_pct": 50.0},
    ]


def test_ten_countries_then_the_rest_and_the_unknown():
    codes = ["GR"] * 3 + ["DE"] * 2 + ["GB", "IT", "FR", "US", "NL", "PL", "IL", "SE", "AT"] + [None]
    nights = [night(date(2026, 7, 1), 100, country=code, booking=f"b{index}") for index, code in enumerate(codes)]
    countries = country_totals(nights, "gross", None)

    assert [row["country"] for row in countries[:2]] == ["Ελλάδα", "Γερμανία"]
    assert len(countries) == 11 and countries[-1]["country"] == "Λοιπές"
    assert sum(row["bookings"] for row in countries) == len(codes)
    assert "Άγνωστη" in {row["country"] for row in countries}
    assert countries[0]["share_pct"] == round(100 * 3 / 15, 1)


# ---------------------------------------------------------------------------
# The calls, on an in-memory repository
# ---------------------------------------------------------------------------


class Repository:
    def __init__(self, nights=(), bookings=(), market=(), held=()):
        self._nights, self._bookings, self._market = list(nights), list(bookings), list(market)
        self._held = list(held)
        self.market_args = None

    def room_types(self, account_id, owned_property_id):
        return [{"id": DOUBLE, "label": "Double", "rooms": 5, "category": "double"},
                {"id": SUITE, "label": "Suite", "rooms": 1, "category": "suite"}]

    def inventory(self, account_id, owned_property_id):
        # The service reads the room types off the rooms: label and category come with them.
        return [{**inventory(DOUBLE, 5), "label": "Double", "category": "double"},
                {**inventory(SUITE, 1), "label": "Suite", "category": "suite"}]

    def closures(self, account_id, owned_property_id):
        return []

    def revenue_settings(self, account_id, owned_property_id):
        return {"vat_pct": 13.0, "resilience_fee_eur": 0.0, "fee_included_in_price": False}

    def held_nights(self, account_id, owned_property_id, windows):
        return [n for n in self._held if any(a <= n["stay_date"] <= b for a, b in windows)]

    def sold_nights(self, account_id, owned_property_id, windows, room_type_id=None):
        return [n for n in self._nights if any(a <= n["stay_date"] <= b for a, b in windows)
                and (room_type_id is None or n["room_type_id"] == room_type_id)]

    def bookings_arriving(self, account_id, owned_property_id, windows, room_type_id=None):
        return [b for b in self._bookings if any(a <= b["arrival"] <= c for a, c in windows)
                and (room_type_id is None or b["room_type_id"] == room_type_id)]

    def market_prices(self, account_id, destination, date_from, date_to, categories):
        self.market_args = (destination, date_from, date_to, categories)
        return [row for row in self._market if date_from <= row["check_in"] <= date_to]


def test_summary_against_last_year_and_the_market():
    repo = Repository(
        nights=[night(date(2026, 7, 1), 150), night(date(2025, 7, 1), 100)],
        bookings=[booking(date(2026, 7, 1), 1), booking(date(2025, 7, 1), 1)],
        market=[{"check_in": date(2026, 7, 1), "price_eur": 140.0}, {"check_in": date(2026, 7, 2), "price_eur": 160.0}],
    )
    summary = PerformanceKpiService(repo).summary(ACCOUNT_ID, PROPERTY, Filters(date(2026, 7, 1), date(2026, 7, 31)))

    assert summary["kpis"]["revenue_eur"] == 150.0 and summary["last_year"]["revenue_eur"] == 100.0
    assert summary["delta"] == {"occupancy_pts": 0.0, "adr_pct": 50.0, "revpar_pct": 50.0, "revenue_pct": 50.0}
    assert summary["market_median_eur"] == 150.0
    assert repo.market_args[0] == "rhodes" and "double" in repo.market_args[3]


def test_no_data_a_year_earlier_is_no_comparison():
    repo = Repository(nights=[night(date(2026, 7, 1))], bookings=[booking(date(2026, 7, 1))])
    service = PerformanceKpiService(repo)

    summary = service.summary(ACCOUNT_ID, PROPERTY, Filters(date(2026, 7, 1), date(2026, 7, 31)))
    assert summary["last_year"] is None and summary["delta"] is None
    monthly = service.monthly(ACCOUNT_ID, PROPERTY, Filters(date(2026, 7, 1), date(2026, 8, 31), compare="none"))
    assert [month["month"] for month in monthly["months"]] == ["2026-07", "2026-08"]
    assert monthly["months"][1]["room_nights_unsold"] == 6 * 31 and monthly["months"][0]["last_year"] is None


def test_a_room_type_filter_compares_with_its_own_category():
    repo = Repository(nights=[night(date(2026, 7, 1), kind=SUITE)], bookings=[booking(date(2026, 7, 1), kind=SUITE)])
    summary = PerformanceKpiService(repo).summary(
        ACCOUNT_ID, PROPERTY, Filters(date(2026, 7, 1), date(2026, 7, 31), room_type_id=SUITE)
    )

    assert summary["kpis"]["room_nights_available"] == 31
    assert repo.market_args[3] and "suite" in repo.market_args[3] and "double" not in repo.market_args[3]


@pytest.mark.parametrize(
    "filters, message",
    [
        (Filters(date(2026, 7, 31), date(2026, 7, 1)), "τελειώνει πριν αρχίσει"),
        (Filters(date(2022, 1, 1), date(2026, 7, 1)), "έως 3 χρόνια"),
        (Filters(date(2026, 7, 1), date(2026, 7, 31), room_type_id=uuid.UUID(int=99)), "δεν ανήκει"),
    ],
)
def test_filters_the_call_cannot_answer(filters, message):
    with pytest.raises(KpiRejected, match=message):
        PerformanceKpiService(Repository()).summary(ACCOUNT_ID, PROPERTY, filters)


# ---------------------------------------------------------------------------
# Room types, stay patterns, pace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, buckets, expected",
    [(1, LENGTH_BUCKETS, "1"), (3, LENGTH_BUCKETS, "2-3"), (9, LENGTH_BUCKETS, "6-9"), (10, LENGTH_BUCKETS, "10+"),
     (0, LEAD_BUCKETS, "0-7"), (8, LEAD_BUCKETS, "8-30"), (60, LEAD_BUCKETS, "31-60"), (61, LEAD_BUCKETS, "61+")],
)
def test_buckets(value, buckets, expected):
    assert bucket(value, buckets) == expected


def test_stay_patterns_of_the_confirmed_arrivals():
    arrivals = [
        booking(date(2026, 7, 1), 1, lead=3, adults=2),
        booking(date(2026, 7, 5), 4, lead=45, adults=2, children=2),
        booking(date(2026, 8, 2), 4, lead=None),
        booking(date(2026, 8, 9), 12, lead=90),
        booking(date(2026, 8, 9), 5, status="cancelled"),
    ]
    patterns = stay_patterns(arrivals, date(2026, 7, 1), date(2026, 8, 31))

    assert [row["bookings"] for row in patterns["length_of_stay"]] == [1, 0, 2, 0, 1]
    assert patterns["length_of_stay"][2]["share_pct"] == 50.0
    # Lead time: only the bookings with a known booking date.
    assert [row["bookings"] for row in patterns["lead_time"]] == [1, 0, 1, 1]
    assert len(patterns["matrix"]) == 20 and sum(cell["bookings"] for cell in patterns["matrix"]) == 3
    assert {"length_bucket": "10+", "lead_bucket": "61+", "bookings": 1} in patterns["matrix"]
    assert patterns["monthly_lead_time"] == [
        {"month": "2026-07", "avg_lead_time_days": 24.0},
        {"month": "2026-08", "avg_lead_time_days": 90.0},
    ]
    assert (patterns["avg_length_of_stay"], patterns["avg_lead_time_days"], patterns["avg_guests"]) == (5.2, 46.0, 2.5)


def test_stay_patterns_of_nothing():
    patterns = stay_patterns([], date(2026, 7, 1), date(2026, 7, 31))

    assert all(row["share_pct"] == 0.0 for row in patterns["length_of_stay"] + patterns["lead_time"])
    assert patterns["avg_guests"] is None and patterns["monthly_lead_time"] == [
        {"month": "2026-07", "avg_lead_time_days": None}
    ]


def held(day, booked_on, status="confirmed", cancelled_on=None, rooms=1, booking_id="h1"):
    return {"stay_date": day, "rooms": rooms, "room_type_id": DOUBLE, "booking_id": booking_id, "status": status,
            "booked_on": booked_on, "cancelled_on": cancelled_on}


def test_on_the_books_on_a_given_day():
    day = date(2025, 10, 20)

    assert on_the_books(held(date(2026, 7, 1), date(2025, 9, 1)), day, booking_date_known=True)
    assert not on_the_books(held(date(2026, 7, 1), date(2025, 11, 1)), day, booking_date_known=True)
    # Cancelled later: it was still held that day.
    assert on_the_books(held(date(2026, 7, 1), date(2025, 9, 1), "cancelled", date(2026, 1, 5)), day,
                        booking_date_known=True)
    assert not on_the_books(held(date(2026, 7, 1), date(2025, 9, 1), "cancelled", date(2025, 10, 1)), day,
                            booking_date_known=True)
    # No booking date: held today, unknown on a past day.
    assert on_the_books(held(date(2026, 7, 1), None), day, booking_date_known=False)
    assert not on_the_books(held(date(2026, 7, 1), None), day, booking_date_known=True)


def test_pace_against_last_season_on_the_same_day():
    today = date(2026, 10, 20)
    nights = [
        held(date(2027, 7, 1), date(2026, 9, 1), rooms=2, booking_id="a"),
        held(date(2027, 7, 2), date(2026, 10, 15), booking_id="b"),  # booked this week
        held(date(2027, 7, 3), date(2026, 9, 1), "cancelled", date(2026, 10, 1), booking_id="c"),
        held(date(2026, 7, 1), date(2025, 9, 1), booking_id="d"),  # on the books a year ago
        held(date(2026, 7, 2), date(2026, 3, 1), booking_id="e"),  # booked later
        held(date(2026, 9, 10), date(2026, 8, 1), booking_id="f"),  # September sold, later
    ]
    pace = PerformanceKpiService(Repository(held=nights)).pace(ACCOUNT_ID, PROPERTY, 2027, today)

    assert pace["as_of"] == today and pace["season"] == 2027
    july, september = pace["months"]
    assert july == {"month": "2027-07", "otb_occupancy_pct": round(100 * 3 / (6 * 31), 1), "otb_room_nights": 3,
                    "last_year_same_day_pct": round(100 * 1 / (6 * 31), 1)}
    assert september == {"month": "2027-09", "otb_occupancy_pct": 0.0, "otb_room_nights": 0,
                         "last_year_same_day_pct": 0.0}
    assert pace["pickup_7d"] == {"bookings": 1, "room_nights": 1}


def test_pace_refuses_a_season_out_of_range():
    with pytest.raises(KpiRejected):
        PerformanceKpiService(Repository()).pace(ACCOUNT_ID, PROPERTY, 1999, date(2026, 10, 20))


def test_room_types_with_their_share_and_their_category_market():
    repo = Repository(
        nights=[night(date(2026, 7, 1), 300, kind=DOUBLE), night(date(2026, 7, 1), 100, kind=SUITE, booking="s")],
        bookings=[booking(date(2026, 7, 1), 1, kind=DOUBLE), booking(date(2026, 7, 1), 1, kind=SUITE),
                  booking(date(2026, 7, 3), 1, kind=SUITE, status="cancelled")],
        market=[{"check_in": date(2026, 7, 1), "price_eur": 120.0}],
    )
    answer = PerformanceKpiService(repo).room_types(
        ACCOUNT_ID, PROPERTY, Filters(date(2026, 7, 1), date(2026, 7, 31), room_type_id=SUITE)
    )

    double, suite = answer["room_types"]
    assert (double["id"], double["revenue_share_pct"], double["rooms"]) == (DOUBLE, 75.0, 5)
    assert (suite["id"], suite["revenue_share_pct"], suite["cancellations_pct"]) == (SUITE, 25.0, 50.0)
    assert double["occupancy_pct"] == round(100 / (5 * 31), 1) and suite["occupancy_pct"] == round(100 / 31, 1)
    assert double["market_median_eur"] == 120.0
