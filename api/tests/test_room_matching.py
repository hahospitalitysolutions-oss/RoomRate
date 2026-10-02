import pytest

from api.services.room_matching import (
    RoomAttributes,
    attributes_from_dict,
    attributes_to_dict,
    compute_match_score,
    extract_room_attributes,
)


EMPTY_ATTRS = RoomAttributes(capacity=None, view=None, has_balcony=None, size_sqm=None, bed_hint=None)


# ---------------------------------------------------------------------------
# extract_room_attributes — table-driven EN + GR
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("room_type", "expected_capacity"),
    [
        ("Single Room", 1),
        ("Double Room with Balcony", 2),
        ("Triple Room", 3),
        ("Quadruple Room", 4),
        ("Quad Room", 4),
        ("Room for 4 people", 4),
        ("Room for 2 persons", 2),
        ("Apartment for 5 guests", 5),
        ("Special offer for 2 nights", None),  # number without person word
        ("Book for 3 days and save", None),
        ("Μονόκλινο Δωμάτιο", 1),
        ("Δίκλινο Δωμάτιο με Ντους", 2),
        ("Δικλινο Δωματιο", 2),  # unaccented Greek
        ("Τρίκλινο Δωμάτιο", 3),
        ("Τετράκλινο Δωμάτιο", 4),
        ("Τετρακλινο", 4),
        ("Δωμάτιο για 3 άτομα", 3),
        ("Δωμάτιο για 2 ενήλικες", 2),
        ("Δωμάτιο για 2 βράδια", None),  # number without person word
        ("Mystery Offer", None),
    ],
)
def test_extracts_capacity_from_room_name(room_type, expected_capacity):
    assert extract_room_attributes(room_type).capacity == expected_capacity


@pytest.mark.parametrize(
    ("room_type", "expected_view"),
    [
        ("Double Room with Sea View", "sea"),
        ("Standard Room with Garden View", "garden"),
        ("Room with Pool View", "pool"),
        ("Suite with Mountain View", "mountain"),
        ("Deluxe Room with City View", "city"),
        ("Δίκλινο με Θέα στη Θάλασσα", "sea"),
        ("Δίκλινο με θεα θαλασσα", "sea"),  # unaccented Greek
        ("Στούντιο με θέα στον Κήπο", "garden"),
        ("Δωμάτιο με θέα στην Πισίνα", "pool"),
        ("Δωμάτιο με θέα στο Βουνό", "mountain"),
        ("Δωμάτιο με θέα στην Πόλη", "city"),
        ("Plain Double Room", None),
    ],
)
def test_extracts_view_from_room_name(room_type, expected_view):
    assert extract_room_attributes(room_type).view == expected_view


@pytest.mark.parametrize(
    ("room_type", "expected_balcony"),
    [
        ("Double Room with Balcony", True),
        ("Δίκλινο Δωμάτιο με Μπαλκόνι", True),
        ("Δικλινο με μπαλκονι", True),
        ("Double Room", None),
    ],
)
def test_extracts_balcony_from_room_name(room_type, expected_balcony):
    assert extract_room_attributes(room_type).has_balcony is expected_balcony


@pytest.mark.parametrize(
    ("room_type", "expected_size"),
    [
        ("Studio 25 m²", 25.0),
        ("Apartment 40 sqm", 40.0),
        ("Suite 32.5 m2", 32.5),
        ("Δωμάτιο 28 τ.μ.", 28.0),
        ("Plain Room", None),
    ],
)
def test_extracts_size_from_room_name(room_type, expected_size):
    assert extract_room_attributes(room_type).size_sqm == expected_size


@pytest.mark.parametrize(
    ("room_type", "expected_bed"),
    [
        ("King Room", "king"),
        ("Queen Studio", "queen"),
        ("Twin Room", "twin"),
        ("Room with Sofa Bed", "sofa"),
        ("Double Room", "double"),
        ("Single Room", "single"),
        ("Mystery Offer", None),
        ("Mystery Booking Offer", None),  # "Booking" must not match "king"
    ],
)
def test_extracts_bed_hint_from_room_name(room_type, expected_bed):
    assert extract_room_attributes(room_type).bed_hint == expected_bed


def test_extracts_capacity_and_size_from_payload_keys():
    attrs = extract_room_attributes("Mystery Offer", {"maxGuests": 3, "roomSize": "27"})
    assert attrs.capacity == 3
    assert attrs.size_sqm == 27.0


@pytest.mark.parametrize("guest_key", ["max_persons", "maxGuests", "max_guests", "persons", "occupancy"])
def test_payload_guest_keys_are_all_recognized(guest_key):
    assert extract_room_attributes("Room", {guest_key: "2"}).capacity == 2


@pytest.mark.parametrize("size_key", ["roomSize", "room_size_sqm", "size"])
def test_payload_size_keys_are_all_recognized(size_key):
    assert extract_room_attributes("Room", {size_key: 33}).size_sqm == 33.0


def test_room_name_capacity_wins_over_payload_guests():
    # The room label is more specific than search-level guest counts.
    attrs = extract_room_attributes("Triple Room", {"maxGuests": 2})
    assert attrs.capacity == 3


def test_payload_extraction_never_raises_on_weird_data():
    weird_payloads = [
        None,
        {},
        {"maxGuests": "not-a-number"},
        {"maxGuests": {"nested": True}},
        {"roomSize": [1, 2, 3]},
        {"persons": None},
        {"size": float("nan")},
        {"occupancy": -5},
    ]
    for payload in weird_payloads:
        attrs = extract_room_attributes("Double Room", payload)
        assert attrs.capacity == 2  # from the name; payload junk ignored


# ---------------------------------------------------------------------------
# attributes_to_dict / attributes_from_dict
# ---------------------------------------------------------------------------

def test_attributes_to_dict_omits_none_values():
    attrs = RoomAttributes(capacity=2, view="sea", has_balcony=None, size_sqm=None, bed_hint=None)
    assert attributes_to_dict(attrs) == {"capacity": 2, "view": "sea"}


def test_attributes_to_dict_of_empty_attrs_is_empty():
    assert attributes_to_dict(EMPTY_ATTRS) == {}


def test_attributes_from_dict_roundtrip():
    attrs = RoomAttributes(capacity=3, view="garden", has_balcony=True, size_sqm=30.0, bed_hint="twin")
    assert attributes_from_dict(attributes_to_dict(attrs)) == attrs


def test_attributes_from_dict_handles_none_and_junk():
    assert attributes_from_dict(None) == EMPTY_ATTRS
    assert attributes_from_dict({}) == EMPTY_ATTRS
    assert attributes_from_dict({"capacity": "junk", "view": 7, "size_sqm": "x"}) == EMPTY_ATTRS


# ---------------------------------------------------------------------------
# compute_match_score
# ---------------------------------------------------------------------------

def _score(owned_name, owned_attrs, candidate_name, candidate_attrs, same_category):
    return compute_match_score(owned_name, owned_attrs, candidate_name, candidate_attrs, same_category)


def test_identical_names_and_category_score_high():
    attrs = extract_room_attributes("Double Room with Sea View")
    score = _score("Double Room with Sea View", attrs, "Double Room with Sea View", attrs, True)
    assert score >= 80.0
    assert score <= 100.0


def test_same_category_beats_different_category_for_same_names():
    attrs = extract_room_attributes("Double Room")
    with_category = _score("Double Room", attrs, "Double Room", attrs, True)
    without_category = _score("Double Room", attrs, "Double Room", attrs, False)
    assert with_category == pytest.approx(without_category + 20.0)


def test_view_conflict_caps_score_at_40():
    owned = extract_room_attributes("Double Room with Sea View")
    candidate = extract_room_attributes("Double Room with Garden View")
    score = _score("Double Room with Sea View", owned, "Double Room with Garden View", candidate, True)
    assert score <= 40.0


def test_capacity_gap_above_one_caps_score_at_40():
    owned = extract_room_attributes("Single Room")
    candidate = extract_room_attributes("Quadruple Room")
    score = _score("Single Room", owned, "Quadruple Room", candidate, True)
    assert score <= 40.0


def test_capacity_gap_of_one_is_not_a_hard_conflict():
    owned = extract_room_attributes("Double Room")
    candidate = extract_room_attributes("Triple Room")
    score = _score("Double Room", owned, "Triple Room", candidate, True)
    assert score > 40.0


def test_matching_attributes_add_bonuses():
    plain = EMPTY_ATTRS
    rich_owned = RoomAttributes(capacity=2, view="sea", has_balcony=True, size_sqm=30.0, bed_hint="double")
    rich_candidate = RoomAttributes(capacity=2, view="sea", has_balcony=True, size_sqm=33.0, bed_hint="double")
    base = _score("Room A", plain, "Room A", plain, False)
    boosted = _score("Room A", rich_owned, "Room A", rich_candidate, False)
    # capacity +15, view +10, balcony +5, size within ±20% +10
    assert boosted == pytest.approx(min(base + 40.0, 100.0))


def test_size_outside_20_percent_gets_no_bonus():
    owned = RoomAttributes(capacity=None, view=None, has_balcony=None, size_sqm=20.0, bed_hint=None)
    candidate = RoomAttributes(capacity=None, view=None, has_balcony=None, size_sqm=30.0, bed_hint=None)
    with_size = _score("Room A", owned, "Room A", candidate, False)
    without = _score("Room A", EMPTY_ATTRS, "Room A", EMPTY_ATTRS, False)
    assert with_size == pytest.approx(without)


def test_unknown_attributes_give_no_bonus_and_no_penalty():
    owned = extract_room_attributes("Double Room with Sea View")
    candidate = EMPTY_ATTRS
    score = _score("Double Room with Sea View", owned, "Double Room", candidate, True)
    assert score > 40.0  # unknown view on one side is NOT a conflict


def test_score_is_clamped_between_0_and_100():
    rich = RoomAttributes(capacity=2, view="sea", has_balcony=True, size_sqm=30.0, bed_hint="double")
    high = _score("Deluxe Double Room Sea View", rich, "Deluxe Double Room Sea View", rich, True)
    assert high == 100.0
    low = _score("", EMPTY_ATTRS, "", EMPTY_ATTRS, False)
    assert low >= 0.0
