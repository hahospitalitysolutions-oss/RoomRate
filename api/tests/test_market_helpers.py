"""market_helpers: the single haversine shared by the scraper and the read API."""

from decimal import Decimal
from uuid import UUID

import pytest

from api.services.market_helpers import category_match_for, distance_km_from, group_by_hotel, haversine_km

MARIA_FALIRAKI = UUID("00000000-0000-0000-0000-00000000f001")
MARIA_KOLYMBIA = UUID("00000000-0000-0000-0000-00000000f002")


def test_group_by_hotel_keeps_same_named_properties_apart():
    """Two «Studios Maria» in different villages are two competitors."""
    rows = [
        {"property_id": MARIA_FALIRAKI, "hotel_name": "Studios Maria", "latitude": 36.34, "room_type": "Studio"},
        {"property_id": MARIA_KOLYMBIA, "hotel_name": "Studios Maria", "latitude": 36.25, "room_type": "Studio"},
        {"property_id": MARIA_FALIRAKI, "hotel_name": "Studios Maria", "latitude": 36.34, "room_type": "Double"},
    ]

    groups = list(group_by_hotel(rows).values())

    assert [[row["room_type"] for row in group] for group in groups] == [["Studio", "Double"], ["Studio"]]
    assert [{row["property_id"] for row in group} for group in groups] == [{MARIA_FALIRAKI}, {MARIA_KOLYMBIA}]


def test_group_by_hotel_falls_back_to_the_name_without_a_property_id():
    """Legacy room_rates rows carry no property id: the name is all there is."""
    rows = [
        {"property_id": None, "hotel_name": " Aegean View ", "room_type": "Double"},
        {"hotel_name": "Aegean View", "room_type": "Suite"},
        {"property_id": None, "hotel_name": "", "room_type": "Nameless"},
        {"property_id": None, "hotel_name": "Blue Bay", "room_type": "Studio"},
    ]

    groups = list(group_by_hotel(rows).values())

    assert [[row["room_type"] for row in group] for group in groups] == [["Double", "Suite"], ["Studio"]]


def test_haversine_faliraki_to_rhodes_town_is_about_ten_and_a_half_km():
    assert haversine_km(36.34, 28.2, 36.4341, 28.2176) == pytest.approx(10.6, abs=0.1)


def test_haversine_is_zero_for_the_same_point_symmetric_and_one_decimal():
    assert haversine_km(36.34, 28.2, 36.34, 28.2) == 0.0
    assert haversine_km(36.34, 28.2, 36.41, 28.19) == haversine_km(36.41, 28.19, 36.34, 28.2)
    distance = haversine_km(36.34, 28.2, 36.41, 28.19)
    assert distance == round(distance, 1)


# ----------------------------------------------------------------------------
# Round 6 (§3.6): category_match_for and distance_km_from
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category, baseline, expected",
    [
        ("double", "double", "same"),
        # «1 Διπλό ή 2 Μονά»: twin and double are the same sellable room.
        ("twin", "double", "same"),
        ("double", "twin", "same"),
        (" twin ", "double", "same"),
        ("suite", "double", "similar"),
        ("apartment", "twin", "similar"),
        (None, "double", "similar"),
        ("", "double", "similar"),
        ("suite", "suite", "same"),
    ],
)
def test_category_match_compares_against_the_baseline_comparable_pool(category, baseline, expected):
    assert category_match_for(category, baseline) == expected


@pytest.mark.parametrize("baseline", [None, "", "   "])
@pytest.mark.parametrize("category", ["suite", "double", None])
def test_category_match_without_a_baseline_is_always_same(category, baseline):
    """Nothing to compare against: «Μόνο ίδια κατηγορία» must not hide rows."""
    assert category_match_for(category, baseline) == "same"


def test_distance_from_the_origin_uses_the_shared_haversine():
    origin = (36.34, 28.2)

    assert distance_km_from(origin, 36.4341, 28.2176) == haversine_km(36.34, 28.2, 36.4341, 28.2176)
    # NUMERIC columns arrive as Decimal.
    assert distance_km_from(origin, Decimal("36.410000"), Decimal("28.190000")) == haversine_km(36.34, 28.2, 36.41, 28.19)


@pytest.mark.parametrize(
    "origin, lat, lng",
    [
        (None, 36.41, 28.19),
        ((36.34, 28.2), None, 28.19),
        ((36.34, 28.2), 36.41, None),
        # 0.0 is how a missing coordinate is stored (as_coord coerces to it).
        ((36.34, 28.2), 0.0, 28.19),
        ((36.34, 28.2), 36.41, Decimal("0.000000")),
        ((36.34, 28.2), "not-a-number", 28.19),
        ((0.0, 0.0), 36.41, 28.19),
        ((None, 28.2), 36.41, 28.19),
    ],
)
def test_distance_is_none_when_the_origin_or_a_coordinate_is_missing(origin, lat, lng):
    assert distance_km_from(origin, lat, lng) is None
