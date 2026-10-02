from datetime import date
import warnings

from api.schemas.agents import PricePosition, PriceStatistics, PriceStatsScope
from api.services.price_statistics_service import compute_price_statistics


# Three runs for one market key. Newest run (rn=1) is the current market.
# Two properties per run so percentiles/median are non-degenerate.
# observed_at spans ~30 days so 7d/30d trend windows can be exercised.
def _history_rows() -> list[dict]:
    return [
        # Latest run (current market): 100 / 120 / 140 -> median 120, p25 110, p75 130
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 120.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p3", "hotel_name": "C", "min_price": 140.0},
        # ~7 days earlier: median 110
        {"rn": 2, "observed_at": "2026-06-23T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 90.0},
        {"rn": 2, "observed_at": "2026-06-23T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 110.0},
        {"rn": 2, "observed_at": "2026-06-23T08:00:00+00:00", "property_id": "p3", "hotel_name": "C", "min_price": 130.0},
        # ~30 days earlier: median 100
        {"rn": 3, "observed_at": "2026-05-31T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 80.0},
        {"rn": 3, "observed_at": "2026-05-31T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 100.0},
        {"rn": 3, "observed_at": "2026-05-31T08:00:00+00:00", "property_id": "p3", "hotel_name": "C", "min_price": 120.0},
    ]


def test_market_stats_from_latest_run():
    stats = compute_price_statistics(_history_rows(), own_price=125.0, check_in=date(2026, 7, 15))

    assert isinstance(stats, PriceStatistics)
    assert stats.sample_runs == 3
    assert stats.market_median_eur == 120.0
    assert stats.market_p25_eur == 110.0
    assert stats.market_p75_eur == 130.0


def test_own_position_percentile_within_current_market():
    # own price 125 sits between 100 and 140 -> roughly mid-high percentile.
    stats = compute_price_statistics(_history_rows(), own_price=125.0, check_in=date(2026, 7, 15))
    assert stats.own_reference_price_eur == 125.0
    assert stats.own_position_percentile is not None
    assert 0.0 <= stats.own_position_percentile <= 100.0
    assert stats.own_position_percentile > 50.0


def _five_hotel_history_rows() -> list[dict]:
    """The same five hotels in three runs: enough for a same-store trend."""
    current = {"p1": 100.0, "p2": 110.0, "p3": 120.0, "p4": 130.0, "p5": 140.0}
    rows = [
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": hotel, "min_price": price}
        for hotel, price in current.items()
    ]
    # ~7 days earlier every hotel was 10 EUR cheaper; ~30 days earlier 20 EUR.
    rows += [
        {"rn": 2, "observed_at": "2026-06-23T08:00:00+00:00", "property_id": hotel, "min_price": price - 10}
        for hotel, price in current.items()
    ]
    rows += [
        {"rn": 3, "observed_at": "2026-05-31T08:00:00+00:00", "property_id": hotel, "min_price": price - 20}
        for hotel, price in current.items()
    ]
    return rows


def test_trend_7d_positive_when_market_rose():
    # Per-hotel changes +11.11 / +10 / +9.09 / +8.33 / +7.69 -> median +9.09.
    stats = compute_price_statistics(_five_hotel_history_rows(), own_price=None, check_in=date(2026, 7, 15))
    assert stats.trend_7d_pct is not None
    assert stats.trend_7d_pct > 0
    # The middle hotel: (120 - 110) / 110 * 100 ~= 9.09
    assert round(stats.trend_7d_pct, 1) == 9.1
    # 30 days: +25 / +22.22 / +20 / +18.18 / +16.67 -> median +20.
    assert stats.trend_30d_pct == 20.0


def test_trend_calculation_emits_no_datetime_deprecation_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        stats = compute_price_statistics(
            _five_hotel_history_rows(), own_price=None, check_in=date(2026, 7, 15)
        )

    assert stats.trend_7d_pct == 9.09


def test_statistical_recommendation_clamped_to_p25_p75():
    stats = compute_price_statistics(_history_rows(), own_price=125.0, check_in=date(2026, 7, 15))
    assert stats.statistical_recommendation_eur is not None
    assert stats.market_p25_eur <= stats.statistical_recommendation_eur <= stats.market_p75_eur


def test_single_run_history_has_no_trend_and_a_caveat():
    rows = [
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 140.0},
    ]
    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.sample_runs == 1
    assert stats.trend_7d_pct is None
    assert stats.trend_30d_pct is None
    # still has a current market
    assert stats.market_median_eur == 120.0
    assert any("η τάση 7 ημερών δεν είναι διαθέσιμη" in note for note in stats.notes)


def test_empty_history_all_none_and_notes():
    stats = compute_price_statistics([], own_price=None, check_in=date(2026, 7, 15))

    assert stats.sample_runs == 0
    assert stats.market_median_eur is None
    assert stats.market_p25_eur is None
    assert stats.market_p75_eur is None
    assert stats.own_position_percentile is None
    assert stats.trend_7d_pct is None
    assert stats.trend_30d_pct is None
    assert stats.statistical_recommendation_eur is None
    assert stats.notes  # at least one caveat
    assert any(note.startswith("Δεν υπάρχει") for note in stats.notes)


def test_lead_time_days_computed_from_as_of():
    # Deterministic: pass as_of so the test doesn't depend on today's date.
    stats = compute_price_statistics(
        _history_rows(), own_price=125.0, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1)
    )
    assert stats.lead_time_days == 14


def test_no_own_price_yields_none_percentile_and_note():
    stats = compute_price_statistics(_history_rows(), own_price=None, check_in=date(2026, 7, 15))
    assert stats.own_reference_price_eur is None
    assert stats.own_position_percentile is None
    assert any("τιμή αναφοράς για το κατάλυμά σας" in note for note in stats.notes)


# ---------------------------------------------------------------------------
# Round 6 — scope (same vs same + similar), position, distinct days
# ---------------------------------------------------------------------------

LATEST = "2026-06-30T08:00:00+00:00"


def _row(rn, observed_at, property_id, same, similar=None):
    """A Round 6 repository row: two minimums per (run, hotel), legacy min_price = same."""
    return {
        "rn": rn,
        "observed_at": observed_at,
        "property_id": property_id,
        "hotel_name": property_id.upper(),
        "min_price": same,
        "min_price_same": same,
        "min_price_similar": similar,
    }


def test_five_same_category_hotels_keep_the_same_scope_and_ignore_similar_ones():
    rows = [_row(1, LATEST, f"p{index}", 100.0 + 10 * index) for index in range(5)]
    rows += [_row(1, LATEST, "s1", None, 40.0), _row(1, LATEST, "s2", None, 300.0)]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.stats_scope == PriceStatsScope(same_category=5, similar=2, used="same")
    assert stats.market_median_eur == 120.0  # 100..140; the similar 40 / 300 never counted
    assert not any("παρόμοια" in note for note in stats.notes)


def test_below_five_same_hotels_similar_ones_join_with_one_price_per_hotel():
    rows = [
        # Has both: counts once, at its same-category 100 — never its cheaper studio (80).
        _row(1, LATEST, "p1", 100.0, 80.0),
        _row(1, LATEST, "p2", 140.0),
        _row(1, LATEST, "s1", None, 90.0),
        _row(1, LATEST, "s2", None, 200.0),
    ]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.stats_scope == PriceStatsScope(same_category=2, similar=2, used="same_plus_similar")
    assert stats.market_median_eur == 120.0  # 90 / 100 / 140 / 200
    # No note repeats the basis: stats_scope carries it and the page renders it once.
    assert not any("παρόμοια" in note or "ίδιας κατηγορίας" in note for note in stats.notes)


def test_position_counts_hotels_strictly_cheaper_than_the_own_price():
    stats = compute_price_statistics(_history_rows(), own_price=120.0, check_in=date(2026, 7, 15))

    assert stats.position == PricePosition(cheaper_than_you=1, total=3)  # 100 < 120; 120 is not cheaper
    # Legacy rows (min_price only) are a same-category scope of every hotel.
    assert stats.stats_scope == PriceStatsScope(same_category=3, similar=0, used="same")
    assert compute_price_statistics(_history_rows(), own_price=None, check_in=date(2026, 7, 15)).position is None


def test_sample_days_counts_distinct_calendar_days_not_runs():
    rows = [
        _row(1, "2026-06-30T08:03:00+00:00", "p1", 100.0),
        _row(2, "2026-06-30T08:00:00+00:00", "p1", 100.0),  # rerun 3 minutes earlier, same day
        _row(3, "2026-06-23T08:00:00+00:00", "p1", 90.0),
    ]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.sample_runs == 3
    assert stats.sample_days == 2


def test_sample_days_are_calendar_days_in_athens():
    rows = [
        _row(1, "2026-06-30T08:00:00+00:00", "p1", 100.0),
        # 22:30 UTC on 29 June is 01:30 on 30 June in Athens (UTC+3): the same day.
        _row(2, "2026-06-29T22:30:00+00:00", "p1", 100.0),
    ]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.sample_runs == 2
    assert stats.sample_days == 1


def test_cancellation_class_is_matched_when_the_own_class_has_matches():
    """Spec 2026-09-29 §4: the pricing page's «ίδια πολιτική ακύρωσης» line."""
    rows = [
        dict(_row(1, LATEST, "p1", 100.0), cancellation_matched_same=True),
        dict(_row(1, LATEST, "p2", 120.0), cancellation_matched_same=False),
    ]

    stats = compute_price_statistics(
        rows, own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="non_refundable"
    )

    assert stats.stats_scope.cancellation_class == "matched"


def test_cancellation_class_is_all_without_an_own_class_even_with_matches():
    rows = [dict(_row(1, LATEST, "p1", 100.0), cancellation_matched_same=True)]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.stats_scope.cancellation_class == "all"


def test_cancellation_class_is_all_when_no_competitor_row_matches():
    rows = [
        dict(_row(1, LATEST, "p1", 100.0), cancellation_matched_same=False),
        dict(_row(1, LATEST, "p2", 120.0), cancellation_matched_same=0),  # SQLite spelling
    ]

    stats = compute_price_statistics(
        rows, own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="free_cancellation"
    )

    assert stats.stats_scope.cancellation_class == "all"


def test_cancellation_class_is_all_for_rows_that_predate_the_flag():
    stats = compute_price_statistics(
        _history_rows(), own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="non_refundable"
    )

    assert stats.stats_scope.cancellation_class == "all"


def test_cancellation_class_follows_the_latest_run_only():
    """An older run's match must not label the current comparison as matched."""
    rows = [
        dict(_row(1, LATEST, "p1", 100.0), cancellation_matched_same=False),
        dict(_row(2, "2026-06-23T08:00:00+00:00", "p1", 90.0), cancellation_matched_same=True),
    ]

    stats = compute_price_statistics(
        rows, own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="non_refundable"
    )

    assert stats.stats_scope.cancellation_class == "all"


def test_a_similar_only_match_outside_the_basis_never_flips_the_label():
    """Review fix: «matched» reflects ONLY the rows forming the statistical
    basis. With 5+ same-category hotels the basis is «same» — an equal-class
    row of a similar-only hotel that widening never used must not label the
    comparison as matched."""
    rows = [
        dict(_row(1, LATEST, f"p{index}", 100.0 + index), cancellation_matched_same=False)
        for index in range(6)
    ]
    rows.append(
        dict(
            _row(1, LATEST, "s1", None, 80.0),
            cancellation_matched_same=False,
            cancellation_matched_similar=True,
        )
    )

    stats = compute_price_statistics(
        rows, own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="non_refundable"
    )

    assert stats.stats_scope.used == "same"
    assert stats.stats_scope.cancellation_class == "all"


def test_a_similar_only_match_counts_once_widening_pulls_the_hotel_in():
    """Mirror of the case above: below 5 same hotels the basis widens, the
    similar-only hotel joins it, and its equal-class row labels «matched»."""
    rows = [
        dict(_row(1, LATEST, "p1", 100.0), cancellation_matched_same=False),
        dict(_row(1, LATEST, "p2", 120.0), cancellation_matched_same=False),
        dict(
            _row(1, LATEST, "s1", None, 80.0),
            cancellation_matched_same=False,
            cancellation_matched_similar=True,
        ),
    ]

    stats = compute_price_statistics(
        rows, own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="non_refundable"
    )

    assert stats.stats_scope.used == "same_plus_similar"
    assert stats.stats_scope.cancellation_class == "matched"


def test_a_same_hotel_counts_at_its_same_rows_even_in_the_widened_basis():
    """A hotel WITH a same-category price enters the widened basis at that
    price: an equal-class row among its SIMILAR packages stays outside the
    basis and must not label the comparison as matched."""
    rows = [
        dict(
            _row(1, LATEST, "p1", 100.0, 80.0),
            cancellation_matched_same=False,
            cancellation_matched_similar=True,
        ),
        dict(
            _row(1, LATEST, "s1", None, 90.0),
            cancellation_matched_same=False,
            cancellation_matched_similar=False,
        ),
    ]

    stats = compute_price_statistics(
        rows, own_price=None, check_in=date(2026, 7, 15), own_cancellation_type="non_refundable"
    )

    assert stats.stats_scope.used == "same_plus_similar"
    assert stats.stats_scope.cancellation_class == "all"


def test_trend_notes_are_greek_for_both_windows():
    rows = [_row(1, LATEST, "p1", 100.0), _row(1, LATEST, "p2", 140.0)]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert "Το ιστορικό δεν καλύπτει 7 ημέρες· η τάση 7 ημερών δεν είναι διαθέσιμη." in stats.notes
    assert "Το ιστορικό δεν καλύπτει 30 ημέρες· η τάση 30 ημερών δεν είναι διαθέσιμη." in stats.notes


# ---------------------------------------------------------------------------
# Same-store trends (live check 2026-09-30: a 10-hotel run against a 91-hotel
# run read as «+40%» with no hotel repricing)
# ---------------------------------------------------------------------------

WEEK_AGO = "2026-06-23T08:00:00+00:00"
MONTH_AGO = "2026-05-31T08:00:00+00:00"


def _run(rn, observed_at, prices: dict[str, float], **method_flags) -> list[dict]:
    """One run's rows; ``method_flags`` are the repository's per-run
    ``agent_basis`` / ``has_discount_data`` (absent = legacy rows, False)."""
    return [dict(_row(rn, observed_at, hotel, price), **method_flags) for hotel, price in prices.items()]


def test_a_composition_change_with_flat_prices_is_no_trend():
    shared = {"h1": 64.0, "h2": 72.0, "h3": 81.0, "h4": 95.0, "h5": 118.0}
    rows = _run(2, WEEK_AGO, {**shared, "gone1": 30.0, "gone2": 35.0})
    rows += _run(
        1,
        LATEST,
        {**shared, "new1": 150.0, "new2": 162.0, "new3": 175.0, "new4": 188.0, "new5": 199.0, "new6": 240.0},
    )

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    # Median vs median would read 72 -> 150 as +108.33%; no shared hotel repriced.
    assert stats.market_median_eur == 150.0
    assert stats.trend_7d_pct == 0.0
    # The anchor is the median itself, not a +108% nudge clamped to P75 (181.5).
    assert stats.statistical_recommendation_eur == 150.0
    assert not any("Λίγα κοινά καταλύματα" in note for note in stats.notes)


def test_matched_hotels_up_ten_percent_each_is_a_ten_percent_trend():
    baseline = {"h1": 80.0, "h2": 90.0, "h3": 150.0, "h4": 200.0, "h5": 260.0}
    rows = _run(2, WEEK_AGO, {**baseline, "gone": 20.0})
    rows += _run(1, LATEST, {**{hotel: price * 1.1 for hotel, price in baseline.items()}, "new": 500.0})

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.trend_7d_pct == 10.0


def test_four_matched_hotels_give_no_trend_and_a_note():
    rows = _run(2, WEEK_AGO, {"h1": 100.0, "h2": 120.0, "h3": 140.0, "h4": 160.0, "gone1": 90.0, "gone2": 95.0})
    rows += _run(1, LATEST, {"h1": 130.0, "h2": 156.0, "h3": 182.0, "h4": 208.0, "new1": 300.0})

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.trend_7d_pct is None
    assert (
        "Λίγα κοινά καταλύματα μεταξύ των αναζητήσεων (4) — η τάση 7 ημερών δεν υπολογίζεται."
        in stats.notes
    )
    # No trend, no nudge: the statistical anchor is the current median (130..300 -> 182).
    assert stats.statistical_recommendation_eur == stats.market_median_eur == 182.0


def test_thirty_day_trend_is_same_store_too():
    current = {"c1": 105.0, "c2": 126.0, "c3": 147.0, "c4": 168.0, "c5": 189.0}
    rows = _run(3, MONTH_AGO, {"c1": 120.0, "c2": 144.0, "c3": 168.0, "c4": 192.0, "c5": 216.0, "old1": 40.0, "old2": 45.0})
    rows += _run(2, WEEK_AGO, {"c1": 100.0, "c2": 120.0, "c3": 140.0, "c4": 160.0, "c5": 180.0})
    rows += _run(1, LATEST, {**current, "new1": 400.0})

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.trend_7d_pct == 5.0
    # Every hotel is 12.5% cheaper than a month ago; median vs median (144 ->
    # 157.5) would have read a +9.38% rise.
    assert stats.trend_30d_pct == -12.5


def test_thirty_day_trend_needs_five_matched_hotels_on_its_own():
    rows = _run(3, MONTH_AGO, {"c1": 120.0, "c2": 144.0, "c3": 168.0, "old1": 40.0, "old2": 45.0})
    rows += _run(2, WEEK_AGO, {"c1": 100.0, "c2": 120.0, "c3": 140.0, "c4": 160.0, "c5": 180.0})
    rows += _run(1, LATEST, {"c1": 105.0, "c2": 126.0, "c3": 147.0, "c4": 168.0, "c5": 189.0})

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.trend_7d_pct == 5.0
    assert stats.trend_30d_pct is None
    assert (
        "Λίγα κοινά καταλύματα μεταξύ των αναζητήσεων (3) — η τάση 30 ημερών δεν υπολογίζεται."
        in stats.notes
    )


DIFFERENT_METHOD_7D = "Οι προηγούμενες αναζητήσεις έγιναν με διαφορετική μέθοδο — η τάση 7 ημερών δεν υπολογίζεται."


def test_an_agent_basis_run_never_pairs_with_a_category_pool_run():
    week_ago = {"h1": 70.0, "h2": 82.0, "h3": 95.0, "h4": 101.0, "h5": 133.0}
    rows = _run(2, WEEK_AGO, week_ago, agent_basis=0, has_discount_data=1)
    # Same hotels, now priced over the agent's comparable rooms: a change of
    # basis, not a +25% market move.
    rows += _run(1, LATEST, {hotel: price * 1.25 for hotel, price in week_ago.items()}, agent_basis=1, has_discount_data=1)

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.stats_scope.used == "agent"
    assert stats.trend_7d_pct is None
    assert DIFFERENT_METHOD_7D in stats.notes


def test_base_only_runs_before_migration_0025_never_pair_with_discounted_ones():
    base = {"h1": 90.0, "h2": 104.0, "h3": 118.0, "h4": 126.0, "h5": 150.0}
    rows = _run(2, WEEK_AGO, base, has_discount_data=0)
    # The same base prices, now read effective (-8% online): no hotel repriced.
    rows += _run(1, LATEST, {hotel: price * 0.92 for hotel, price in base.items()}, has_discount_data=1)

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.trend_7d_pct is None  # not a false «-8%»
    assert DIFFERENT_METHOD_7D in stats.notes
    assert stats.statistical_recommendation_eur == stats.market_median_eur


def test_the_nearest_run_of_the_same_method_is_the_baseline():
    earlier = {"h1": 100.0, "h2": 110.0, "h3": 120.0, "h4": 130.0, "h5": 140.0}
    same_method = {"agent_basis": 1, "has_discount_data": 1}
    # Eight days back, priced the same way: the baseline.
    rows = _run(3, "2026-06-22T08:00:00+00:00", earlier, **same_method)
    # Exactly seven days back but over the category pool: skipped, although
    # nearer (its 50 EUR prices would read as +120%).
    rows += _run(2, WEEK_AGO, {hotel: 50.0 for hotel in earlier}, agent_basis=0, has_discount_data=1)
    rows += _run(1, LATEST, {hotel: price * 1.1 for hotel, price in earlier.items()}, **same_method)

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.trend_7d_pct == 10.0
    assert DIFFERENT_METHOD_7D not in stats.notes


def test_every_note_is_greek_on_every_path():
    empty = compute_price_statistics([], own_price=None, check_in=date(2026, 7, 15))
    unpriced = compute_price_statistics([_row(1, LATEST, "p1", None)], own_price=None, check_in=date(2026, 7, 15))
    single = compute_price_statistics([_row(1, LATEST, "p1", 100.0)], own_price=None, check_in=date(2026, 7, 15))

    for stats in (empty, unpriced, single):
        assert stats.notes
        # Every Greek sentence carries non-ASCII letters; an English leftover would not.
        assert not any(note.isascii() for note in stats.notes)
