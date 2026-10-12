"""Flight demand: the plan, the unit budget, the collection and the pickup signal."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from api.config import Settings
from api.services.aerodatabox_client import (
    AeroDataBoxAuthError,
    AeroDataBoxError,
    Arrival,
    ArrivalsWindow,
    QuotaExhausted,
)
from api.services.flight_demand_service import (
    PARSER_VERSION,
    FlightDemandService,
    aggregate_arrivals,
    airport_for,
    build_flight_demand_signal,
    daily_allowance,
    days_left_in_month,
    flight_daily_levels,
    plan_calls,
    read_dates,
    travel_dates,
)
from api.services.flight_plan import FLIGHT_PLANS, parse_weekdays, parse_windows, resolve_plan

TODAY = date(2026, 10, 12)  # a Monday
FRIDAY = date(2026, 10, 16)
SATURDAY = date(2026, 10, 17)
FREE = FLIGHT_PLANS["free"]
PRO = FLIGHT_PLANS["pro"]


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_parsers():
    assert parse_weekdays("5, 4") == (4, 5)
    assert parse_windows("12-24,00-12") == ((0, 12), (12, 24))
    assert parse_windows("08-20") == ((8, 20),)


@pytest.mark.parametrize("value", ["", "7", "-1", "fri"])
def test_bad_weekdays(value):
    with pytest.raises(ValueError):
        parse_weekdays(value)


@pytest.mark.parametrize("value", ["", "8-21", "20-08", "0-25", "08:00-20:00", "00-12,06-18"])
def test_bad_windows(value):
    """At most 12 hours (AeroDataBox's limit), within the day, no overlap."""
    with pytest.raises(ValueError):
        parse_windows(value)


def test_resolve_plan_applies_only_the_set_overrides():
    assert resolve_plan("free") is FREE
    plan = resolve_plan("Pro", monthly_units=2000, weekdays="", windows="06-18")

    assert (plan.name, plan.monthly_units, plan.weekdays, plan.windows) == ("pro", 2000, PRO.weekdays, ((6, 18),))
    with pytest.raises(ValueError):
        resolve_plan("gold")


def test_settings_refuse_a_bad_plan_at_startup():
    with pytest.raises(ValidationError):
        Settings(FLIGHT_DEMAND_PLAN="gold")
    with pytest.raises(ValidationError):
        Settings(FLIGHT_DEMAND_WINDOWS="08-22")
    settings = Settings(FLIGHT_DEMAND_PLAN="pro", FLIGHT_DEMAND_MONTHLY_UNITS=4000, AERODATABOX_API_KEY="k")
    assert settings.flight_plan.monthly_units == 4000 and settings.flight_demand_active


def test_the_free_plan_fits_two_airports_in_400_units():
    """Weekly re-read of 8 weeks of Fridays/Saturdays, one window: ~70 calls a month per airport."""
    calls_per_week = len(travel_dates(TODAY, FREE, 56)) * len(FREE.windows)
    units_per_month = calls_per_week * 2 * 31 / 7

    assert calls_per_week == 16
    assert 2 * units_per_month <= FREE.monthly_units <= 400


# ---------------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------------


def test_airport_from_the_property_coordinates():
    assert airport_for(36.34, 28.20) == ("RHO", "Ρόδος")  # Faliraki
    assert airport_for(36.46, 25.38) == ("JTR", "Σαντορίνη")  # Oia
    assert airport_for(35.19, 25.72) == ("HER", "Λασίθι")  # Agios Nikolaos lands at Heraklion
    assert airport_for(36.61, 27.84) is None  # Symi: by sea only
    assert airport_for(37.97, 23.73) is None  # Athens
    assert airport_for(None, None) is None


# ---------------------------------------------------------------------------
# Budget and planning
# ---------------------------------------------------------------------------


def test_days_left_in_month():
    assert days_left_in_month(date(2026, 10, 12)) == 20
    assert days_left_in_month(date(2026, 10, 31)) == 1
    assert days_left_in_month(date(2026, 12, 31)) == 1
    assert days_left_in_month(date(2028, 2, 1)) == 29


def test_daily_allowance_spreads_the_month():
    # 190 calls over 20 days.
    assert daily_allowance(TODAY, FREE, used_calls=0, units_per_call=2) == 10
    assert daily_allowance(TODAY, FREE, used_calls=185, units_per_call=2) == 1
    assert daily_allowance(TODAY, FREE, used_calls=190, units_per_call=2) == 0
    assert daily_allowance(TODAY, FREE, used_calls=500, units_per_call=2) == 0
    # The last day spends what is left, within the per-run cap.
    assert daily_allowance(date(2026, 10, 31), FREE, used_calls=150, units_per_call=2) == 20
    assert daily_allowance(date(2026, 10, 31), FREE, used_calls=185, units_per_call=2) == 5


def test_travel_dates_are_the_plan_weekdays_from_tomorrow():
    dates = travel_dates(TODAY, FREE, 14)

    assert dates == [FRIDAY, SATURDAY, FRIDAY + timedelta(days=7), SATURDAY + timedelta(days=7)]
    assert travel_dates(FRIDAY, FREE, 2) == [SATURDAY]
    assert len(travel_dates(TODAY, PRO, 14)) == 14


def test_plan_calls_reads_what_is_missing_or_stale_nearest_first():
    newest = {
        ("RHO", FRIDAY, 8): TODAY - timedelta(days=2),  # fresh
        ("RHO", SATURDAY, 8): TODAY - timedelta(days=7),  # due again
    }

    due = plan_calls(TODAY, ["JTR", "RHO"], FREE, horizon_days=7, refresh_days=7, newest_looks=newest)

    assert [(call.airport_iata, call.travel_date, call.window) for call in due] == [
        ("JTR", FRIDAY, (8, 20)),
        ("JTR", SATURDAY, (8, 20)),
        ("RHO", SATURDAY, (8, 20)),
    ]


def test_aggregate_arrivals_counts_origins_and_airlines():
    arrivals = [
        Arrival("A3 1", "Aegean", "ATH", "Athens", None),
        Arrival("A3 2", "Aegean", "ATH", "Athens", None),
        Arrival("EZY 1", "easyJet", "LGW", "London Gatwick", None),
        Arrival("X 1", None, None, None, None),
    ]

    assert aggregate_arrivals(arrivals) == {
        "arrivals": 4,
        "airlines": 2,
        "origins": {
            "ATH": {"name": "Athens", "flights": 2},
            "LGW": {"name": "London Gatwick", "flights": 1},
            "?": {"name": "?", "flights": 1},
        },
    }


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


def row(travel_date, observed_on, arrivals, *, start=8, end=20, airlines=5, origins=None):
    return {
        "airport_iata": "RHO",
        "travel_date": travel_date,
        "window_start_hour": start,
        "window_end_hour": end,
        "observed_on": observed_on,
        "arrivals": arrivals,
        "airlines": airlines,
        "origins": origins if origins is not None else {"LGW": {"name": "London Gatwick", "flights": 4}},
    }


def signal(rows, check_in=SATURDAY, today=TODAY):
    return build_flight_demand_signal(rows, check_in, today, airport_iata="RHO", island="Ρόδος", max_age_days=10)


WEEK_AGO = TODAY - timedelta(days=7)


def test_no_rows_is_unknown():
    result = signal([])

    assert result.level == "unknown" and result.travel_dates == []


def test_a_single_look_is_unknown_until_read_again():
    result = signal([row(SATURDAY, TODAY, 40)])

    assert result.level == "unknown"
    assert result.arrivals == 40 and result.compared_with is None
    assert "σε περίπου μία εβδομάδα" in result.summary
    assert result.summary.startswith("Αφίξεις στο αεροδρόμιο RHO (Ρόδος) Σάβ 17/10 08:00–20:00: 40 πτήσεις")


def test_flights_added_since_the_earlier_look_is_high():
    result = signal([row(SATURDAY, WEEK_AGO, 40), row(SATURDAY, TODAY, 44)])

    assert result.level == "high"
    assert (result.arrivals_before, result.flights_added, result.arrivals_change_pct) == (40, 4, 10.0)
    assert result.compared_with == WEEK_AGO
    assert "Από τις 05/10: +4 πτήσεις (+10%)" in result.summary
    assert result.summary.endswith("Ζήτηση πτήσεων: Υψηλή.")
    assert result.top_origins == ["London Gatwick"]


def test_flights_dropped_is_low_and_small_moves_are_normal():
    assert signal([row(SATURDAY, WEEK_AGO, 40), row(SATURDAY, TODAY, 36)]).level == "low"
    assert signal([row(SATURDAY, WEEK_AGO, 40), row(SATURDAY, TODAY, 42)]).level == "normal"
    # +10% but one flight: a single extra charter on a quiet day.
    assert signal([row(SATURDAY, WEEK_AGO, 10), row(SATURDAY, TODAY, 11)]).level == "normal"


def test_the_oldest_look_in_the_pickup_range_is_the_baseline():
    rows = [
        row(SATURDAY, TODAY - timedelta(days=30), 20),  # too old
        row(SATURDAY, TODAY - timedelta(days=14), 40),
        row(SATURDAY, WEEK_AGO, 42),
        row(SATURDAY, TODAY - timedelta(days=1), 45),  # too close to compare
        row(SATURDAY, TODAY, 46),
    ]

    result = signal(rows)

    assert result.compared_with == TODAY - timedelta(days=14)
    assert result.flights_added == 6


def test_a_check_in_not_collected_reads_the_nearest_collected_days():
    rows = [row(FRIDAY, WEEK_AGO, 30), row(FRIDAY, TODAY, 34), row(SATURDAY, WEEK_AGO, 40), row(SATURDAY, TODAY, 44)]

    wednesday = signal(rows, check_in=FRIDAY - timedelta(days=2))
    sunday = signal(rows, check_in=SATURDAY + timedelta(days=1))

    assert wednesday.travel_dates == [FRIDAY] and wednesday.arrivals == 34
    assert "Η 14/10 δεν συλλέγεται" in wednesday.summary
    assert sunday.travel_dates == [SATURDAY]
    assert signal(rows, check_in=FRIDAY + timedelta(days=10)).level == "unknown"


def test_read_dates_ties_take_both():
    assert read_dates([FRIDAY, SATURDAY + timedelta(days=1)], SATURDAY) == [FRIDAY, SATURDAY + timedelta(days=1)]
    assert read_dates([FRIDAY], FRIDAY + timedelta(days=4)) == []


def test_windows_pair_with_themselves_and_whole_days_have_no_hours():
    rows = [
        row(SATURDAY, WEEK_AGO, 20, start=0, end=12),
        row(SATURDAY, WEEK_AGO, 20, start=12, end=24),
        row(SATURDAY, TODAY, 22, start=0, end=12),
        row(SATURDAY, TODAY, 23, start=12, end=24),
    ]

    result = signal(rows)

    assert (result.arrivals, result.arrivals_before, result.flights_added) == (45, 40, 5)
    assert "Σάβ 17/10: 45 πτήσεις" in result.summary
    # The window switched from 08-20 to the whole day: the old window's last
    # look overlaps the new one and is not counted twice; no earlier look of
    # the new window yet.
    switched = signal([row(SATURDAY, WEEK_AGO, 40), row(SATURDAY, TODAY, 22, start=0, end=12)])
    assert switched.level == "unknown" and switched.arrivals == 22


def test_stale_looks_are_not_read():
    assert signal([row(SATURDAY, TODAY - timedelta(days=25), 30), row(SATURDAY, TODAY - timedelta(days=11), 40)]).level == "unknown"


def test_no_flights_before_is_unknown_not_high():
    result = signal([row(SATURDAY, WEEK_AGO, 0), row(SATURDAY, TODAY, 6)])

    assert result.level == "unknown" and result.flights_added == 6
    assert "Δεν υπήρχαν πτήσεις στην προηγούμενη ματιά" in result.summary


def test_daily_levels_one_entry_per_collected_date():
    rows = [row(FRIDAY, TODAY, 30), row(SATURDAY, WEEK_AGO, 40), row(SATURDAY, TODAY, 44)]

    days = flight_daily_levels(rows, TODAY, airport_iata="RHO", island="Ρόδος", max_age_days=10)

    assert [(day["travel_date"], day["arrivals"], day["level"]) for day in days] == [
        (FRIDAY, 30, "unknown"),
        (SATURDAY, 44, "high"),
    ]


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

RHODES = {"latitude": 36.34, "longitude": 28.20}
SANTORINI = {"latitude": 36.46, "longitude": 25.38}
SYMI = {"latitude": 36.61, "longitude": 27.84}


class FakeRepository:
    def __init__(self, points=(RHODES,), used=0, newest=None):
        self.points = list(points)
        self.used = used
        self.newest = newest or {}
        self.saved = []
        self.calls_since_args = []

    def property_points(self):
        return self.points

    def calls_since(self, observed_since):
        self.calls_since_args.append(observed_since)
        return self.used

    def newest_looks(self, airports, date_from):
        return self.newest

    def upsert_observation(self, **kwargs):
        self.saved.append(kwargs)


class FakeClient:
    def __init__(self, answers=None, units=(300,)):
        self.answers = list(answers or [])
        self.units = list(units)
        self.calls = []
        self.closed = False

    def arrivals(self, iata, travel_date, start_hour, end_hour):
        self.calls.append((iata, travel_date, start_hour, end_hour))
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, Exception):
            raise answer
        units = self.units.pop(0) if len(self.units) > 1 else self.units[0]
        arrivals = answer if answer is not None else [Arrival("A3 1", "Aegean", "ATH", "Athens", None)]
        return ArrivalsWindow(arrivals, units, {"arrivals": []})

    def close(self):
        self.closed = True


def service(repository, client, plan=FREE):
    return FlightDemandService(repository, lambda: client, plan, horizon_days=56, refresh_days=7, units_per_call=2)


def test_collect_reads_the_due_windows_within_the_day_share():
    repository = FakeRepository(points=[RHODES, SANTORINI, SYMI, {"latitude": 36.35, "longitude": 28.21}])
    client = FakeClient()

    report = service(repository, client).collect(TODAY)

    assert report.airports == ["JTR", "RHO"]
    assert repository.calls_since_args == [date(2026, 10, 1)]
    assert report.due == 32 and report.allowance == 10
    assert report.calls == report.saved == 10
    assert client.calls[:2] == [("JTR", FRIDAY, 8, 20), ("RHO", FRIDAY, 8, 20)]
    first = repository.saved[0]
    assert first["observed_on"] == TODAY and first["parser_version"] == PARSER_VERSION
    assert (first["arrivals"], first["airlines"], first["units_remaining"]) == (1, 1, 300)
    assert client.closed


def test_collect_without_island_airports_opens_no_client():
    def factory():
        raise AssertionError("no call expected")

    report = FlightDemandService(FakeRepository(points=[SYMI]), factory, FREE).collect(TODAY)

    assert report.airports == [] and report.calls == 0


def test_collect_with_the_month_spent_makes_no_call():
    client = FakeClient()

    report = service(FakeRepository(used=190), client).collect(TODAY)

    assert report.allowance == 0 and client.calls == []


def test_a_manual_fill_may_exceed_the_day_share_but_not_the_month():
    assert service(FakeRepository(used=0), FakeClient()).collect(TODAY, max_calls=16).calls == 16
    assert service(FakeRepository(used=185), FakeClient()).collect(TODAY, max_calls=16).calls == 5


def test_collect_stops_when_the_plan_runs_out_of_units():
    client = FakeClient(units=(5, 3, 1, 300))

    report = service(FakeRepository(), client).collect(TODAY)

    assert report.saved == 3 and report.out_of_units and report.units_remaining == 1


def test_collect_stops_on_429_and_on_a_bad_key():
    quota = service(FakeRepository(), FakeClient([None, QuotaExhausted("spent")])).collect(TODAY)
    auth = service(FakeRepository(), FakeClient([AeroDataBoxAuthError("401")])).collect(TODAY)

    assert (quota.saved, quota.calls, quota.out_of_units) == (1, 2, True)
    assert (auth.saved, auth.calls, auth.failed, auth.aborted) == (0, 1, 1, True)


def test_collect_skips_a_failed_call_and_stops_after_three_in_a_row():
    error = AeroDataBoxError("HTTP 500")
    skipped = service(FakeRepository(), FakeClient([error, None, error, error, None])).collect(TODAY)
    stopped = service(FakeRepository(), FakeClient([None, error, error, error, None])).collect(TODAY)

    assert (skipped.failed, skipped.aborted, skipped.saved) == (3, False, 7)
    assert (stopped.failed, stopped.aborted, stopped.saved, stopped.calls) == (3, True, 1, 4)


def test_pro_plan_reads_both_halves_of_every_day():
    client = FakeClient()

    report = service(FakeRepository(), client, plan=PRO).collect(TODAY)

    assert report.due == 112
    assert client.calls[:3] == [
        ("RHO", TODAY + timedelta(days=1), 0, 12),
        ("RHO", TODAY + timedelta(days=1), 12, 24),
        ("RHO", TODAY + timedelta(days=2), 0, 12),
    ]
