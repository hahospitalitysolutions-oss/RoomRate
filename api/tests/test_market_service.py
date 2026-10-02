from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from api.schemas.market import OwnedRoomReference
from api.services.market_helpers import haversine_km
from api.services.market_service import (
    MarketService,
    RoomRateFilters,
    build_agent_match_lookup,
)
from api.services.room_rates_normalizer import normalize_room_rate_row


class FakeRoomRatesRepository:
    def __init__(self, rows):
        self.rows = rows
        self.last_filters = None

    def fetch_room_rates(self, filters: RoomRateFilters) -> list[dict]:
        self.last_filters = filters
        return self.rows


def _rows() -> list[dict]:
    return [
        {
            "record_id": "r1",
            "hotel_name": "Aegean View",
            "city": "Faliraki",
            "address": "Faliraki Center",
            "property_type": "Hotel",
            "latitude": 36.340001,
            "longitude": 28.200001,
            "stars": 3.0,
            "review_score": 8.8,
            "review_count": 120,
            "price_per_night_eur": 80.0,
            "price_total_eur": 400.0,
            "nights": 5,
            "guests": 2,
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "room_type": "Double Room",
            "meals": "Δεν περιλαμβάνεται",
            "free_cancellation": "Ναι",
            "facilities": "AC|WiFi|Balcony",
            "rooms_left": 2,
            "check_in": "2026-06-15",
            "check_out": "2026-06-20",
            "scraped_at": "2026-04-14T18:39:00Z",
        },
        {
            "record_id": "r2",
            "hotel_name": "Aegean View",
            "city": "Faliraki",
            "address": "Faliraki Center",
            "property_type": "Hotel",
            "latitude": 36.340001,
            "longitude": 28.200001,
            "stars": 3.0,
            "review_score": 8.8,
            "review_count": 120,
            "price_per_night_eur": 110.0,
            "price_total_eur": 550.0,
            "nights": 5,
            "guests": 2,
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "room_type": "Superior Room",
            "meals": "Πρωινό",
            "free_cancellation": "Όχι",
            "facilities": "AC|WiFi|Sea view",
            "rooms_left": 1,
            "check_in": "2026-06-15",
            "check_out": "2026-06-20",
            "scraped_at": "2026-04-14T18:39:00Z",
        },
        {
            "record_id": "r3",
            "hotel_name": "Budget Stay",
            "city": "Faliraki",
            "address": "Ermou 10",
            "property_type": "Apartment",
            "latitude": 36.341111,
            "longitude": 28.201111,
            "stars": 0.0,
            "review_score": 7.9,
            "review_count": 75,
            "price_per_night_eur": 60.0,
            "price_total_eur": 300.0,
            "nights": 5,
            "guests": 2,
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "room_type": "Studio",
            "meals": "Δεν περιλαμβάνεται",
            "free_cancellation": "Ναι",
            "facilities": "Kitchen|WiFi",
            "rooms_left": 5,
            "check_in": "2026-06-15",
            "check_out": "2026-06-20",
            "scraped_at": "2026-04-14T18:39:00Z",
        },
    ]


def _matching_row(**overrides) -> dict:
    row = {
        "record_id": "m0",
        "hotel_name": "Hotel",
        "city": "Faliraki",
        "address": "Faliraki Center",
        "property_type": "Hotel",
        "latitude": 36.34,
        "longitude": 28.2,
        "stars": 3.0,
        "review_score": 8.0,
        "review_count": 10,
        "price_per_night_eur": 80.0,
        "price_total_eur": 400.0,
        "nights": 5,
        "guests": 2,
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "room_type": "Double Room",
        "room_type_category": "double",
        "meals": "",
        "free_cancellation": "",
        "facilities": "",
        "rooms_left": 2,
        "check_in": "2026-06-15",
        "check_out": "2026-06-20",
        "scraped_at": "2026-04-14T18:39:00Z",
    }
    row.update(overrides)
    return row


def _matching_rows() -> list[dict]:
    return [
        _matching_row(
            record_id="m1",
            hotel_name="Hotel Exact",
            room_type="Double Room with Sea View",
            price_per_night_eur=90.0,
            price_total_eur=450.0,
            room_attributes={"capacity": 2, "view": "sea", "bed_hint": "double"},
        ),
        _matching_row(
            record_id="m2",
            hotel_name="Hotel Garden",
            room_type="Double Room with Garden View",
            price_per_night_eur=70.0,
            price_total_eur=350.0,
            room_attributes={"capacity": 2, "view": "garden", "bed_hint": "double"},
        ),
        # No room_attributes key at all: candidate attrs must come from
        # on-the-fly extraction of the room name (fallback path).
        _matching_row(
            record_id="m3",
            hotel_name="Hotel Quad",
            room_type="Quadruple Apartment",
            room_type_category="quadruple",
            price_per_night_eur=50.0,
            price_total_eur=250.0,
        ),
    ]


OWNED_ROOM = OwnedRoomReference(
    room_type="Double Room with Sea View",
    room_type_category="double",
    room_attributes={"capacity": 2, "view": "sea", "bed_hint": "double"},
)


def test_get_competitors_without_owned_room_keeps_scores_none():
    service = MarketService(FakeRoomRatesRepository(_matching_rows()))

    competitors = service.get_competitors(RoomRateFilters())

    assert all(competitor.best_match_score is None for competitor in competitors)
    assert all(
        package.match_score is None
        for competitor in competitors
        for package in competitor.packages
    )


def test_get_competitors_attaches_match_scores_for_owned_room():
    service = MarketService(FakeRoomRatesRepository(_matching_rows()))

    competitors = service.get_competitors(RoomRateFilters(), owned_room=OWNED_ROOM)

    by_name = {competitor.hotel_name: competitor for competitor in competitors}
    assert by_name["Hotel Exact"].best_match_score >= 80.0
    # Known different views are a hard conflict capped at 40.
    assert by_name["Hotel Garden"].best_match_score <= 40.0
    # Capacity 2 vs 4 (extracted from the name, fallback path) caps at 40 too.
    assert by_name["Hotel Quad"].best_match_score <= 40.0
    assert all(
        package.match_score is not None
        for competitor in competitors
        for package in competitor.packages
    )


def test_get_competitors_uses_stored_attributes_over_name_extraction():
    rows = [
        _matching_row(
            record_id="m4",
            hotel_name="Hotel Hidden Garden",
            room_type="Double Room",  # name alone says nothing about the view
            room_attributes={"capacity": 2, "view": "garden"},
        )
    ]
    service = MarketService(FakeRoomRatesRepository(rows))

    competitors = service.get_competitors(RoomRateFilters(), owned_room=OWNED_ROOM)

    # Stored garden view conflicts with the owned sea view → capped at 40.
    assert competitors[0].best_match_score <= 40.0


def test_min_match_score_drops_packages_and_empty_competitors():
    service = MarketService(FakeRoomRatesRepository(_matching_rows()))

    competitors = service.get_competitors(
        RoomRateFilters(), owned_room=OWNED_ROOM, min_match_score=60.0
    )

    assert [competitor.hotel_name for competitor in competitors] == ["Hotel Exact"]
    assert all(
        package.match_score >= 60.0
        for competitor in competitors
        for package in competitor.packages
    )


def test_sort_by_match_orders_by_best_score_then_price():
    service = MarketService(FakeRoomRatesRepository(_matching_rows()))

    competitors = service.get_competitors(
        RoomRateFilters(), owned_room=OWNED_ROOM, sort_by_match=True
    )

    # Hotel Exact is the most expensive but matches best → first.
    assert competitors[0].hotel_name == "Hotel Exact"
    scores = [competitor.best_match_score for competitor in competitors]
    assert scores == sorted(scores, reverse=True)


def test_sort_by_match_breaks_score_ties_by_price_ascending():
    rows = [
        _matching_row(
            record_id="t1",
            hotel_name="Hotel Pricey",
            room_type="Double Room with Sea View",
            price_per_night_eur=95.0,
            room_attributes={"capacity": 2, "view": "sea", "bed_hint": "double"},
        ),
        _matching_row(
            record_id="t2",
            hotel_name="Hotel Cheap",
            room_type="Double Room with Sea View",
            price_per_night_eur=55.0,
            room_attributes={"capacity": 2, "view": "sea", "bed_hint": "double"},
        ),
    ]
    service = MarketService(FakeRoomRatesRepository(rows))

    competitors = service.get_competitors(
        RoomRateFilters(), owned_room=OWNED_ROOM, sort_by_match=True
    )

    assert competitors[0].best_match_score == competitors[1].best_match_score
    assert [competitor.hotel_name for competitor in competitors] == ["Hotel Cheap", "Hotel Pricey"]


GREEK_DOUBLE_ROOM = OwnedRoomReference(
    room_type="Deluxe Δίκλινο Δωμάτιο με θέα στη Θάλασσα",
    room_type_category="double",
    room_attributes={"capacity": 2, "view": "sea"},
)


def _greek_bed_variant_rows() -> list[dict]:
    """One `double`-bucketed and one `twin`-bucketed row for the same 2-bed room."""
    return [
        _matching_row(
            record_id="g1",
            hotel_name="Hotel Same Bucket",
            room_type="Δίκλινο Δωμάτιο με θέα στη Θάλασσα",
            room_type_category="double",
            price_per_night_eur=140.0,
        ),
        _matching_row(
            record_id="g2",
            hotel_name="Hotel Other Bucket",
            room_type="Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια και Μερική Θέα στη Θάλασσα",
            room_type_category="twin",
            price_per_night_eur=120.0,
        ),
    ]


def test_cross_bucket_pool_rows_are_scored_not_discarded():
    """Pooled reads hand twin rows to a double owner; they must stay comparable."""
    service = MarketService(FakeRoomRatesRepository(_greek_bed_variant_rows()))

    competitors = service.get_competitors(RoomRateFilters(), owned_room=GREEK_DOUBLE_ROOM)

    by_name = {competitor.hotel_name: competitor for competitor in competitors}
    assert set(by_name) == {"Hotel Same Bucket", "Hotel Other Bucket"}
    # Capacity 2 on both sides and a matching sea view keep the cross-bucket
    # room well clear of the hard-conflict cap.
    assert by_name["Hotel Other Bucket"].best_match_score > 40.0


def test_same_category_bonus_still_ranks_the_exact_bucket_first():
    """The pool decides who is IN; the +20 same-category bonus ranks within it."""
    service = MarketService(FakeRoomRatesRepository(_greek_bed_variant_rows()))

    competitors = service.get_competitors(
        RoomRateFilters(), owned_room=GREEK_DOUBLE_ROOM, sort_by_match=True
    )

    assert [competitor.hotel_name for competitor in competitors] == [
        "Hotel Same Bucket",
        "Hotel Other Bucket",
    ]
    # Cheaper AND second: the ordering comes from the score, not the price.
    assert competitors[1].price_min_eur < competitors[0].price_min_eur
    assert competitors[0].best_match_score - competitors[1].best_match_score >= 20.0


def test_market_summary_aggregates_unique_hotels_and_prices():
    service = MarketService(FakeRoomRatesRepository(_rows()))

    summary = service.get_market_summary(RoomRateFilters(destination="Faliraki"))

    assert summary.total_records == 3
    assert summary.total_hotels == 2
    # Round 6: one price per hotel (its cheapest package), not per row, so
    # Aegean View's 110 EUR superior room no longer moves the statistics.
    assert summary.price_min_eur == 60.0
    assert summary.price_max_eur == 80.0
    assert summary.price_avg_eur == pytest.approx(70.0)
    assert summary.price_median_eur == 70.0
    assert summary.rooms_left_total == 8
    assert (summary.same_category_hotels, summary.similar_hotels) == (2, 0)


def test_map_markers_use_cheapest_package_per_hotel():
    service = MarketService(FakeRoomRatesRepository(_rows()))

    markers = service.get_competitor_map_markers(RoomRateFilters())

    assert len(markers) == 2
    aegean = next(marker for marker in markers if marker.hotel_name == "Aegean View")
    assert aegean.price_per_night_eur == 80.0
    assert aegean.rooms_left == 2
    assert aegean.latitude == 36.340001


def test_smart_advisor_context_excludes_my_hotel_and_keeps_packages():
    service = MarketService(FakeRoomRatesRepository(_rows()))

    context = service.get_smart_advisor_context(RoomRateFilters(), my_hotel_name="Budget Stay")

    assert context.meta.total_competitors == 1
    assert context.meta.adults == 2
    assert context.meta.children == 0
    assert context.meta.rooms == 1
    assert context.competitors[0].hotel_name == "Aegean View"
    assert context.data_quality.facility_coverage_pct == 100.0
    assert context.pricing_signals.cheapest_competitor == "Aegean View"
    assert context.pricing_signals.free_cancellation_share_pct == 50.0
    assert [package.room_type for package in context.competitors[0].packages] == [
        "Double Room",
        "Superior Room",
    ]


def test_smart_advisor_context_carries_distance_and_honest_category_match():
    """Spec 2026-09-29 Β.2: advisor competitors carry distance_km measured from
    the owner's coordinates and a category_match judged against the filters'
    baseline category — not the unconditional "same" of a missing baseline."""
    rows = _rows()
    rows[0]["room_type_category"] = "double"   # Aegean View, 80 €
    rows[1]["room_type_category"] = "suite"    # Aegean View, 110 €
    rows[2]["room_type_category"] = "studio"   # Budget Stay, 60 €
    service = MarketService(FakeRoomRatesRepository(rows))

    context = service.get_smart_advisor_context(
        RoomRateFilters(room_type_category="double"),
        origin=(36.35, 28.21),
    )

    by_name = {competitor.hotel_name: competitor for competitor in context.competitors}
    aegean, budget = by_name["Aegean View"], by_name["Budget Stay"]
    assert aegean.distance_km == haversine_km(36.35, 28.21, 36.340001, 28.200001)
    assert budget.distance_km == haversine_km(36.35, 28.21, 36.341111, 28.201111)
    # Aegean sells a double (same pool) plus a suite; Budget only a studio.
    assert [package.category_match for package in aegean.packages] == ["same", "similar"]
    assert aegean.category_match == "same"
    assert budget.category_match == "similar"


def test_rejects_date_range_where_checkout_is_not_after_checkin():
    with pytest.raises(ValueError, match="check_out must be after check_in"):
        RoomRateFilters(
            check_in=date(2026, 6, 20),
            check_out=date(2026, 6, 20),
        )


def test_rejects_invalid_occupancy_filters():
    with pytest.raises(ValueError, match="adults must be at least 1"):
        RoomRateFilters(adults=0)
    with pytest.raises(ValueError, match="children cannot be negative"):
        RoomRateFilters(children=-1)
    with pytest.raises(ValueError, match="rooms must be at least 1"):
        RoomRateFilters(rooms=0)


def test_room_rate_filters_normalize_amenities():
    filters = RoomRateFilters(amenities=(" WiFi ", "", "Pool"))

    assert filters.amenities == ("wifi", "pool")


# ----------------------------------------------------------------------------
# Round 6 (§3.6): category_match, distance_km, booking_url, marker choice,
# one price per hotel in the summary, sort by distance, own-property rate
# ----------------------------------------------------------------------------

ORIGIN = (36.34, 28.2)


def _round6_rows() -> list[dict]:
    """Four hotels around Faliraki with same-category and similar packages."""
    return [
        # Aegean View: a cheaper suite (similar) and a double (same).
        _matching_row(
            record_id="a1", hotel_name="Aegean View", room_type="Suite", room_type_category="suite",
            price_per_night_eur=50.0, latitude=36.35, longitude=28.21, rooms_left=1,
            booking_url="https://www.booking.com/hotel/gr/aegean-view.html",
        ),
        _matching_row(
            record_id="a2", hotel_name="Aegean View", room_type="Double Room", room_type_category="double",
            price_per_night_eur=80.0, latitude=36.35, longitude=28.21, rooms_left=2,
            booking_url="https://www.booking.com/hotel/gr/aegean-view.html",
        ),
        # Ixia Bay: similar categories only; the cheapest one wins the marker.
        _matching_row(
            record_id="b1", hotel_name="Ixia Bay", room_type="Apartment", room_type_category="apartment",
            price_per_night_eur=60.0, latitude=36.41, longitude=28.19, rooms_left=3, review_score=9.0,
        ),
        _matching_row(
            record_id="b2", hotel_name="Ixia Bay", room_type="Studio", room_type_category="studio",
            price_per_night_eur=55.0, latitude=36.41, longitude=28.19, rooms_left=4, review_score=9.0,
        ),
        # Twin Palms: twin is in the double pool, so it is the same category.
        _matching_row(
            record_id="c1", hotel_name="Twin Palms", room_type="Twin Room", room_type_category="twin",
            price_per_night_eur=100.0, latitude=36.34, longitude=28.2, rooms_left=0, review_score=7.0,
        ),
        # No Coords Inn: an unknown category is similar and it has no location.
        _matching_row(
            record_id="d1", hotel_name="No Coords Inn", room_type="Mystery Offer", room_type_category=None,
            price_per_night_eur=90.0, latitude=0.0, longitude=0.0, rooms_left=5, review_score=0.0,
        ),
    ]


def test_packages_and_competitors_are_labelled_against_the_filter_category():
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    competitors = service.get_competitors(RoomRateFilters(room_type_category="double"))

    by_name = {competitor.hotel_name: competitor for competitor in competitors}
    aegean_packages = {package.room_type_category: package.category_match for package in by_name["Aegean View"].packages}
    assert aegean_packages == {"suite": "similar", "double": "same"}
    assert by_name["Aegean View"].category_match == "same"  # any same package
    assert by_name["Ixia Bay"].category_match == "similar"
    assert by_name["Twin Palms"].category_match == "same"
    assert by_name["No Coords Inn"].category_match == "similar"
    assert by_name["No Coords Inn"].packages[0].room_type_category is None


def test_without_a_filter_category_every_package_is_the_same_category():
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    competitors = service.get_competitors(RoomRateFilters())

    assert {competitor.category_match for competitor in competitors} == {"same"}
    assert {package.category_match for competitor in competitors for package in competitor.packages} == {"same"}


def test_competitors_carry_distance_from_the_origin_and_the_booking_url():
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    with_origin = {c.hotel_name: c for c in service.get_competitors(RoomRateFilters(), origin=ORIGIN)}
    without_origin = service.get_competitors(RoomRateFilters())

    assert with_origin["Aegean View"].distance_km == haversine_km(36.34, 28.2, 36.35, 28.21)
    assert with_origin["Twin Palms"].distance_km == 0.0
    assert with_origin["No Coords Inn"].distance_km is None
    assert with_origin["Aegean View"].booking_url == "https://www.booking.com/hotel/gr/aegean-view.html"
    assert with_origin["Ixia Bay"].booking_url is None
    assert all(competitor.distance_km is None for competitor in without_origin)


def test_sort_by_distance_puts_the_nearest_first_and_unknown_distances_last():
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    nearest_first = service.get_competitors(RoomRateFilters(), origin=ORIGIN, sort_by_distance=True)
    no_origin = service.get_competitors(RoomRateFilters(), sort_by_distance=True)

    assert [c.hotel_name for c in nearest_first] == ["Twin Palms", "Aegean View", "Ixia Bay", "No Coords Inn"]
    # Without coordinates for the owner every distance is null: price order.
    assert [c.hotel_name for c in no_origin] == ["Aegean View", "Ixia Bay", "No Coords Inn", "Twin Palms"]


def test_map_marker_uses_the_cheapest_same_category_package_else_the_cheapest_similar():
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    markers = {m.hotel_name: m for m in service.get_competitor_map_markers(RoomRateFilters(room_type_category="double"), origin=ORIGIN)}

    # No Coords Inn cannot be plotted.
    assert set(markers) == {"Aegean View", "Ixia Bay", "Twin Palms"}
    aegean = markers["Aegean View"]
    assert (aegean.price_per_night_eur, aegean.room_type_category, aegean.category_match) == (80.0, "double", "same")
    assert aegean.rooms_left == 2
    assert aegean.booking_url == "https://www.booking.com/hotel/gr/aegean-view.html"
    assert aegean.distance_km == haversine_km(36.34, 28.2, 36.35, 28.21)
    ixia = markers["Ixia Bay"]
    assert (ixia.price_per_night_eur, ixia.room_type_category, ixia.category_match) == (55.0, "studio", "similar")
    assert markers["Twin Palms"].distance_km == 0.0


def test_map_marker_without_an_origin_has_no_distance():
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    markers = service.get_competitor_map_markers(RoomRateFilters())

    assert all(marker.distance_km is None for marker in markers)


def test_market_summary_uses_the_marker_price_per_hotel_and_counts_categories():
    """One summary box that agrees with the map: one price per hotel."""
    service = MarketService(FakeRoomRatesRepository(_round6_rows()))

    summary = service.get_market_summary(RoomRateFilters(room_type_category="double"))

    # Aegean 80 (same), Ixia 55 (similar), Twin Palms 100 (same), No Coords 90 (similar).
    assert summary.total_records == 6
    assert summary.total_hotels == 4
    assert (summary.same_category_hotels, summary.similar_hotels) == (2, 2)
    assert (summary.price_min_eur, summary.price_max_eur) == (55.0, 100.0)
    assert summary.price_avg_eur == pytest.approx(81.25)
    assert summary.price_median_eur == 85.0
    # rooms_left stays the row sum; the review average skips unrated hotels.
    assert summary.rooms_left_total == 15
    assert summary.avg_review_score == pytest.approx(8.0)


def test_empty_market_summary_reports_zero_category_counts():
    summary = MarketService(FakeRoomRatesRepository([])).get_market_summary(RoomRateFilters())

    assert (summary.total_hotels, summary.same_category_hotels, summary.similar_hotels) == (0, 0, 0)


def _two_studios_maria() -> list[dict]:
    """Same display name, different properties in different villages."""
    return [
        _matching_row(
            record_id="sm1", property_id="00000000-0000-0000-0000-00000000f001", hotel_name="Studios Maria",
            price_per_night_eur=55.0, latitude=36.34, longitude=28.2,
        ),
        _matching_row(
            record_id="sm2", property_id="00000000-0000-0000-0000-00000000f002", hotel_name="Studios Maria",
            price_per_night_eur=65.0, latitude=36.25, longitude=28.14,
        ),
    ]


def test_same_named_properties_stay_separate_competitors_markers_and_summary_hotels():
    service = MarketService(FakeRoomRatesRepository(_two_studios_maria()))

    competitors = service.get_competitors(RoomRateFilters(), origin=ORIGIN)
    markers = service.get_competitor_map_markers(RoomRateFilters())
    summary = service.get_market_summary(RoomRateFilters())

    assert [(c.hotel_name, c.price_min_eur, c.latitude) for c in competitors] == [
        ("Studios Maria", 55.0, 36.34),
        ("Studios Maria", 65.0, 36.25),
    ]
    assert [c.distance_km for c in competitors] == [0.0, haversine_km(36.34, 28.2, 36.25, 28.14)]
    assert sorted((str(m.property_id), m.latitude) for m in markers) == [
        ("00000000-0000-0000-0000-00000000f001", 36.34),
        ("00000000-0000-0000-0000-00000000f002", 36.25),
    ]
    assert (summary.total_hotels, summary.price_min_eur, summary.price_max_eur) == (2, 55.0, 65.0)


# ----------------------------------------------------------------------------
# Rate plans (spec 2026-09-29 §4): rate_plan per package; since the owner
# decision of 2026-09-30 markers and summaries read the EFFECTIVE price
# ----------------------------------------------------------------------------


def _rate_plan_row(**overrides) -> dict:
    row = _matching_row(
        record_id="plan1",
        hotel_name="Plan Hotel",
        discounted_price_per_night_eur=None,
        discount_pct=None,
        discount_label=None,
        has_genius_discount=None,
        cancellation_type=None,
        payment_label=None,
        rate_block_id=None,
    )
    row.update(overrides)
    return row


def test_packages_carry_the_rate_plan_when_any_plan_column_is_set():
    from decimal import Decimal

    rows = [
        _rate_plan_row(
            discounted_price_per_night_eur=Decimal("73.60"),  # as the DB returns it
            discount_pct=8.0,
            discount_label="-8%",
            has_genius_discount=True,
            cancellation_type="non_refundable",
            payment_label="Πληρωμή online",
            rate_block_id="123456789_0_2_0",
        )
    ]
    service = MarketService(FakeRoomRatesRepository(rows))

    package = service.get_competitors(RoomRateFilters())[0].packages[0]

    plan = package.rate_plan
    assert plan is not None
    assert plan.discounted_price_per_night_eur == 73.6
    assert plan.discount_pct == 8.0
    assert plan.discount_label == "-8%"
    assert plan.has_genius_discount is True
    assert plan.cancellation_type == "non_refundable"
    assert plan.payment_label == "Πληρωμή online"


def test_rate_plan_is_null_when_every_plan_column_is_null():
    """Pre-migration rows (all NULL) and legacy rows (no keys at all) both
    read as «χωρίς στοιχεία πλάνου»."""
    service_with_nulls = MarketService(FakeRoomRatesRepository([_rate_plan_row()]))
    service_without_keys = MarketService(FakeRoomRatesRepository(_matching_rows()))

    assert service_with_nulls.get_competitors(RoomRateFilters())[0].packages[0].rate_plan is None
    assert all(
        package.rate_plan is None
        for competitor in service_without_keys.get_competitors(RoomRateFilters())
        for package in competitor.packages
    )


def test_a_legacy_reingest_reads_back_with_a_null_rate_plan():
    """Review 2026-09-29 (spec §3): a legacy CSV without the plan columns
    normalizes with EVERY plan field None — has_genius_discount too, None
    instead of a materialized False — so the re-ingested package still reads
    back as rate_plan null («χωρίς στοιχεία πλάνου»)."""
    normalized = normalize_room_rate_row(_matching_row())
    reingested = _rate_plan_row(
        discounted_price_per_night_eur=normalized.discounted_price_per_night_eur,
        discount_pct=normalized.discount_pct,
        discount_label=normalized.discount_label,
        has_genius_discount=normalized.has_genius_discount,
        cancellation_type=normalized.cancellation_type,
        payment_label=normalized.payment_label,
        rate_block_id=normalized.rate_block_id,
    )
    service = MarketService(FakeRoomRatesRepository([reingested]))

    assert normalized.has_genius_discount is None
    assert service.get_competitors(RoomRateFilters())[0].packages[0].rate_plan is None


def test_rate_plan_survives_a_false_genius_flag_alone():
    """False is a stored value, not NULL: the plan must not collapse to null."""
    rows = [_rate_plan_row(has_genius_discount=False)]
    service = MarketService(FakeRoomRatesRepository(rows))

    plan = service.get_competitors(RoomRateFilters())[0].packages[0].rate_plan

    assert plan is not None
    assert plan.has_genius_discount is False
    assert plan.discounted_price_per_night_eur is None


def test_markers_summary_and_competitor_range_use_the_effective_price():
    """Owner decision 2026-09-30 (supersedes spec 2026-09-29 §4's «markers
    unchanged»): what a guest actually pays — the discounted price when
    shown — drives the marker, the summary and the competitor range, while
    each package payload keeps its base price (the discount is in rate_plan)."""
    rows = [
        _rate_plan_row(
            record_id="plan-a",
            price_per_night_eur=100.0,
            discounted_price_per_night_eur=60.0,
            discount_pct=40.0,
        ),
        _rate_plan_row(
            record_id="plan-b",
            room_type="Double Economy",
            price_per_night_eur=80.0,
            discounted_price_per_night_eur=79.0,
        ),
    ]
    service = MarketService(FakeRoomRatesRepository(rows))

    [marker] = service.get_competitor_map_markers(RoomRateFilters(room_type_category="double"))
    summary = service.get_market_summary(RoomRateFilters(room_type_category="double"))
    [competitor] = service.get_competitors(RoomRateFilters(room_type_category="double"))

    assert marker.price_per_night_eur == 60.0  # effective, not the 80.0 base minimum
    assert (summary.price_min_eur, summary.price_max_eur) == (60.0, 60.0)
    assert (competitor.price_min_eur, competitor.price_max_eur) == (60.0, 79.0)
    assert [
        (package.price_per_night_eur, package.rate_plan.discounted_price_per_night_eur)
        for package in competitor.packages
    ] == [(100.0, 60.0), (80.0, 79.0)]


def _effective_market_rows() -> list[dict]:
    """Three hotels: base minimums 120/100/105, effective minimums 96/100/99."""
    return [
        _rate_plan_row(
            record_id="d1",
            hotel_name="Discounted",
            price_per_night_eur=120.0,
            discounted_price_per_night_eur=96.0,
        ),
        # NULL discount (older row / no discount): falls back to the base.
        _rate_plan_row(record_id="n1", hotel_name="NoDiscount", price_per_night_eur=100.0),
        _rate_plan_row(
            record_id="m1",
            hotel_name="Mixed",
            price_per_night_eur=110.0,
            discounted_price_per_night_eur=99.0,
        ),
        _rate_plan_row(record_id="m2", hotel_name="Mixed", price_per_night_eur=105.0),
    ]


def test_a_discounted_package_lowers_marker_summary_and_advisor_median():
    service = MarketService(FakeRoomRatesRepository(_effective_market_rows()))
    filters = RoomRateFilters(room_type_category="double")

    markers = {m.hotel_name: m.price_per_night_eur for m in service.get_competitor_map_markers(filters)}
    summary = service.get_market_summary(filters)
    competitors = {c.hotel_name: c for c in service.get_competitors(filters)}
    context = service.get_smart_advisor_context(filters)

    assert markers == {"Discounted": 96.0, "NoDiscount": 100.0, "Mixed": 99.0}
    # Base prices would give a 105.0 median; the effective one is 99.0.
    assert (summary.price_min_eur, summary.price_median_eur, summary.price_max_eur) == (96.0, 99.0, 100.0)
    assert (competitors["Mixed"].price_min_eur, competitors["Mixed"].price_max_eur) == (99.0, 105.0)
    assert (competitors["NoDiscount"].price_min_eur, competitors["NoDiscount"].price_max_eur) == (100.0, 100.0)
    # The advisor's per-row market stats read the same effective prices.
    assert context.meta.market_stats["price_min_eur"] == 96.0
    assert context.meta.market_stats["price_median_eur"] == 99.5  # 96, 99, 100, 105
    assert context.pricing_signals.cheapest_competitor == "Discounted"


def test_own_property_rate_carries_the_discount_for_the_effective_marker():
    """The «Εσείς» row is picked by effective price; the router prices it so."""
    from api.services.market_helpers import effective_price_per_night

    repository = FakeOwnRatesRepository(
        [
            {"room_type": "Δίκλινο", "room_type_category": "double", "price_per_night_eur": 95.0,
             "discounted_price_per_night_eur": 87.4},
            {"room_type": "Δίκλινο Economy", "room_type_category": "double", "price_per_night_eur": 90.0,
             "discounted_price_per_night_eur": None},
        ]
    )

    rate = MarketService(repository).get_own_property_cheapest_rate("account", "job", "Rea Hotel", "double")

    assert rate["room_type"] == "Δίκλινο"
    assert effective_price_per_night(rate) == 87.4
    assert effective_price_per_night({"price_per_night_eur": 90.0}) == 90.0
    assert effective_price_per_night({}) is None


class FakeOwnRatesRepository(FakeRoomRatesRepository):
    def __init__(self, own_rows):
        super().__init__([])
        self.own_rows = own_rows
        self.own_calls: list[tuple] = []

    def fetch_own_property_rates(self, account_id, scrape_job_id, display_name):
        self.own_calls.append((account_id, scrape_job_id, display_name))
        return self.own_rows


def _own_rows() -> list[dict]:
    return [
        {"room_type": "Σουίτα", "room_type_category": "suite", "price_per_night_eur": 70.0},
        {"room_type": "Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια", "room_type_category": "twin", "price_per_night_eur": 92.0},
        {"room_type": "Δίκλινο Δωμάτιο", "room_type_category": "double", "price_per_night_eur": 95.0},
    ]


@pytest.mark.parametrize(
    "baseline, expected_price",
    [
        ("double", 92.0),  # cheapest in the double/twin pool
        ("suite", 70.0),
        ("apartment", 70.0),  # nothing in the pool: cheapest of any category
        (None, 70.0),
    ],
)
def test_own_property_cheapest_rate_prefers_the_comparable_pool(baseline, expected_price):
    repository = FakeOwnRatesRepository(_own_rows())
    service = MarketService(repository)

    rate = service.get_own_property_cheapest_rate("account", "job", "Rea Hotel", baseline)

    assert rate["price_per_night_eur"] == expected_price
    assert repository.own_calls == [("account", "job", "Rea Hotel")]


def test_own_property_cheapest_rate_is_none_without_a_job_or_own_rows():
    repository = FakeOwnRatesRepository([])
    service = MarketService(repository)

    assert service.get_own_property_cheapest_rate("account", None, "Rea Hotel", "double") is None
    assert repository.own_calls == []
    assert service.get_own_property_cheapest_rate("account", "job", "Rea Hotel", "double") is None


# ---------------------------------------------------------------------------
# Agent match overrides (spec 2026-09-29 Α.4)
# ---------------------------------------------------------------------------

PROP_EXACT = UUID("00000000-0000-0000-0000-0000000000e1")
PROP_GARDEN = UUID("00000000-0000-0000-0000-0000000000e2")


def _agent_market_rows() -> list[dict]:
    """Two properties; Hotel Exact also sells a suite the agent did not score."""
    return [
        _matching_row(
            record_id="a1",
            hotel_name="Hotel Exact",
            property_id=PROP_EXACT,
            room_type="Double Room with Sea View",
            price_per_night_eur=90.0,
            price_total_eur=450.0,
            room_attributes={"capacity": 2, "view": "sea", "bed_hint": "double"},
        ),
        _matching_row(
            record_id="a2",
            hotel_name="Hotel Exact",
            property_id=PROP_EXACT,
            room_type="Family Suite",
            room_type_category="suite",
            price_per_night_eur=140.0,
            price_total_eur=700.0,
        ),
        _matching_row(
            record_id="a3",
            hotel_name="Hotel Garden",
            property_id=PROP_GARDEN,
            room_type="Double Room with Garden View",
            price_per_night_eur=70.0,
            price_total_eur=350.0,
            room_attributes={"capacity": 2, "view": "garden", "bed_hint": "double"},
        ),
    ]


def _agent_row(property_id, room_type, score, reasoning="Ίδια κατηγορία και θέα."):
    """A roomrate_room_matches row as the repository returns it (Decimal score)."""
    return {
        "property_id": property_id,
        "room_type": room_type,
        "score": Decimal(str(score)),
        "category_match": "same",
        "reasoning": reasoning,
        "model_version": "claude-sonnet-5-5",
    }


def test_agent_rows_override_scores_and_carry_reasoning_mixed_response():
    """Pin the MIXED response: one competitor agent-scored, one statistical."""
    service = MarketService(FakeRoomRatesRepository(_agent_market_rows()))
    lookup = build_agent_match_lookup(
        [_agent_row(PROP_EXACT, "Double Room with Sea View", 88)]
    )

    competitors = service.get_competitors(
        RoomRateFilters(), owned_room=OWNED_ROOM, agent_matches=lookup
    )

    by_name = {competitor.hotel_name: competitor for competitor in competitors}
    exact = by_name["Hotel Exact"]
    garden = by_name["Hotel Garden"]

    sea_view = next(p for p in exact.packages if p.room_type == "Double Room with Sea View")
    suite = next(p for p in exact.packages if p.room_type == "Family Suite")
    # The agent-scored package takes the agent's score and reasoning...
    assert sea_view.match_score == 88.0
    assert sea_view.match_reasoning == "Ίδια κατηγορία και θέα."
    # ...while an unmatched room of the SAME competitor keeps its statistical
    # score with a null reasoning.
    assert suite.match_score is not None
    assert suite.match_reasoning is None
    # Per-competitor source: agent when ANY of its packages came from agent
    # rows; a competitor the agent never matched stays honestly statistical.
    assert exact.match_source == "agent"
    assert garden.match_source == "statistical"
    assert all(p.match_reasoning is None for p in garden.packages)


def test_agent_lookup_matches_room_names_case_insensitively():
    service = MarketService(FakeRoomRatesRepository(_agent_market_rows()))
    lookup = build_agent_match_lookup(
        [_agent_row(str(PROP_EXACT), "  double room WITH sea view ", 77, reasoning=None)]
    )

    competitors = service.get_competitors(
        RoomRateFilters(), owned_room=OWNED_ROOM, agent_matches=lookup
    )

    exact = next(c for c in competitors if c.hotel_name == "Hotel Exact")
    sea_view = next(p for p in exact.packages if p.room_type == "Double Room with Sea View")
    assert sea_view.match_score == 77.0
    assert sea_view.match_reasoning is None
    # reasoning None on an agent row still marks the competitor as agent.
    assert exact.match_source == "agent"


def test_min_match_score_filters_over_agent_scores():
    """The slider keeps working unchanged over the 0-100 agent scale."""
    service = MarketService(FakeRoomRatesRepository(_agent_market_rows()))
    lookup = build_agent_match_lookup(
        [
            _agent_row(PROP_EXACT, "Double Room with Sea View", 30),
            _agent_row(PROP_GARDEN, "Double Room with Garden View", 95),
        ]
    )

    competitors = service.get_competitors(
        RoomRateFilters(),
        owned_room=OWNED_ROOM,
        min_match_score=60.0,
        agent_matches=lookup,
    )

    # Exact's sea view fell to 30 (agent) and its suite scores low
    # statistically, so the whole competitor drops; Garden survives at 95.
    names = [competitor.hotel_name for competitor in competitors]
    assert names == ["Hotel Garden"]
    assert competitors[0].best_match_score == 95.0
    assert competitors[0].match_source == "agent"


def test_sort_by_match_ranks_agent_and_statistical_scores_together():
    service = MarketService(FakeRoomRatesRepository(_agent_market_rows()))
    lookup = build_agent_match_lookup(
        [
            _agent_row(PROP_EXACT, "Double Room with Sea View", 50),
            _agent_row(PROP_EXACT, "Family Suite", 40),
        ]
    )

    competitors = service.get_competitors(
        RoomRateFilters(),
        owned_room=OWNED_ROOM,
        sort_by_match=True,
        agent_matches=lookup,
    )

    # Garden keeps its statistical best (≤40, hard view conflict) and Exact
    # is agent-capped at 50: the ordering interleaves both sources.
    assert [c.hotel_name for c in competitors] == ["Hotel Exact", "Hotel Garden"]
    assert competitors[0].best_match_score == 50.0
    assert competitors[0].match_source == "agent"
    assert competitors[1].match_source == "statistical"


def test_build_agent_match_lookup_is_empty_for_no_rows():
    assert build_agent_match_lookup([]) == {}


def test_match_source_defaults_to_statistical_without_matching():
    service = MarketService(FakeRoomRatesRepository(_agent_market_rows()))

    competitors = service.get_competitors(RoomRateFilters())

    assert all(competitor.match_source == "statistical" for competitor in competitors)
    assert all(
        package.match_reasoning is None
        for competitor in competitors
        for package in competitor.packages
    )


# ----------------------------------------------------------------------------
# v4 price advisor — full comparison basis with a per-competitor tracked flag
# ----------------------------------------------------------------------------


class ScopedRoomRatesRepository:
    """Returns the full basis, or only the tracked rows for a tracked-only read."""

    def __init__(self, rows, tracked_property_ids):
        self.rows = rows
        self.tracked_property_ids = set(tracked_property_ids)
        self.calls: list[RoomRateFilters] = []

    def fetch_room_rates(self, filters: RoomRateFilters) -> list[dict]:
        self.calls.append(filters)
        if filters.selected_competitors_only:
            return [row for row in self.rows if row["property_id"] in self.tracked_property_ids]
        return self.rows


def _basis_rows() -> list[dict]:
    """Four hotels (one is the owner's own) — two tracked, two not."""
    base = _rows()[0]

    def row(property_id, name, category, price):
        return {
            **base,
            "property_id": property_id,
            "hotel_name": name,
            "room_type_category": category,
            "price_per_night_eur": price,
        }

    return [
        row("p-tracked-1", "Tracked Double", "double", 95.0),
        row("p-plain-1", "Plain Double", "double", 70.0),
        row("p-plain-2", "Plain Suite", "suite", 160.0),
        row("p-tracked-2", "Tracked Suite", "suite", 210.0),
        row("p-own", "My Hotel", "double", 90.0),
    ]


def test_smart_advisor_context_is_the_full_basis_with_tracked_flags():
    repository = ScopedRoomRatesRepository(
        _basis_rows(), tracked_property_ids={"p-tracked-1", "p-tracked-2"}
    )
    service = MarketService(repository)
    filters = RoomRateFilters(
        destination="faliraki",
        adults=2,
        owned_property_id="00000000-0000-0000-0000-000000000456",
        room_type_category="double",
        include_similar=True,
    )

    context = service.get_smart_advisor_context(filters, my_hotel_name="My Hotel")

    tracked = {competitor.hotel_name: competitor.tracked for competitor in context.competitors}
    # Every competitor of the basis (the own hotel excluded), not just the two tracked.
    assert tracked == {
        "Plain Double": False,
        "Tracked Double": True,
        "Plain Suite": False,
        "Tracked Suite": True,
    }
    # Still price-ordered and still carrying the per-competitor facts.
    assert [c.hotel_name for c in context.competitors] == [
        "Plain Double",
        "Tracked Double",
        "Plain Suite",
        "Tracked Suite",
    ]
    assert context.meta.total_competitors == 4
    assert {c.hotel_name: c.category_match for c in context.competitors}["Plain Suite"] == "similar"
    # The tracked set came from the repository's own tracked-only read of the
    # SAME market key; the basis read itself stayed unscoped.
    basis_read, tracked_read = repository.calls
    assert basis_read.selected_competitors_only is False
    assert tracked_read.selected_competitors_only is True
    assert tracked_read.destination == "faliraki"
    assert tracked_read.room_type_category == "double"
    assert tracked_read.include_similar is True

    # End to end into the prompt: flags and counts survive the trim.
    from api.services.price_recommendation_agent import PriceRecommendationAgent

    market = PriceRecommendationAgent._trim_advisor_context(context)
    assert {c["hotel_name"]: c["tracked"] for c in market["competitors"]} == tracked
    assert market["competitors_in_basis"] == 4
    assert market["competitors_shown"] == 4
    assert market["competitors_tracked"] == 2


def test_smart_advisor_context_without_an_owned_property_tracks_nothing():
    repository = ScopedRoomRatesRepository(_basis_rows(), tracked_property_ids={"p-tracked-1"})
    service = MarketService(repository)

    context = service.get_smart_advisor_context(RoomRateFilters(), my_hotel_name="My Hotel")

    assert len(context.competitors) == 4
    assert all(competitor.tracked is False for competitor in context.competitors)
    # No owned property, no tracked-only read.
    assert len(repository.calls) == 1


def test_tracked_only_advisor_read_marks_every_competitor_tracked_in_one_query():
    repository = ScopedRoomRatesRepository(_basis_rows(), tracked_property_ids={"p-tracked-1"})
    service = MarketService(repository)

    context = service.get_smart_advisor_context(
        RoomRateFilters(
            owned_property_id="00000000-0000-0000-0000-000000000456",
            selected_competitors_only=True,
        )
    )

    assert [(c.hotel_name, c.tracked) for c in context.competitors] == [("Tracked Double", True)]
    assert len(repository.calls) == 1
