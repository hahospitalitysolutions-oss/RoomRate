from api.services.room_rates_normalizer import (
    comparable_room_type_categories,
    normalize_room_type_category,
)


def test_normalizes_double_room_labels():
    assert normalize_room_type_category("Deluxe Double Room with Sea View") == "double"


def test_normalizes_greek_double_room_labels():
    assert normalize_room_type_category("Δίκλινο Δωμάτιο με Ντους και Μπαλκόνι") == "double"


def test_normalizes_greek_single_room_labels():
    assert normalize_room_type_category("Μονόκλινο Δωμάτιο με Ντους") == "single"


def test_normalizes_studio_before_apartment():
    assert normalize_room_type_category("Studio Apartment with Balcony") == "studio"


def test_normalizes_greek_suite_and_studio_labels():
    assert normalize_room_type_category("Junior Σουίτα με θέα στη Θάλασσα") == "suite"
    assert normalize_room_type_category("Στούντιο με θέα στον Κήπο") == "studio"


def test_normalizes_unknown_room_label_to_other():
    assert normalize_room_type_category("Mystery Booking Offer") == "other"


def test_double_or_twin_bed_labels_split_across_two_categories():
    """The live split: one physical room, two buckets.

    Booking's Greek listings sell the same room as "1 double OR 2 singles".
    The twin keywords are checked first, so those labels land in `twin` while
    a plain "Δίκλινο" room lands in `double` — which is why an equality
    filter on the category discarded every comparable competitor room
    (live 2026-08-11: "Room type filter 'double': 29 -> 4 records").
    """
    assert normalize_room_type_category(
        "Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια και Μερική Θέα στη Θάλασσα"
    ) == "twin"
    assert normalize_room_type_category("Deluxe Δίκλινο Δωμάτιο με θέα στη Θάλασσα") == "double"


def test_double_and_twin_share_one_comparable_pool():
    assert comparable_room_type_categories("double") == ("double", "twin")
    assert comparable_room_type_categories("twin") == ("double", "twin")


def test_pool_of_the_split_labels_covers_both_live_room_names():
    twin_label = normalize_room_type_category(
        "Superior Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια και θέα στην Πόλη"
    )
    double_label = normalize_room_type_category("Deluxe Δίκλινο Δωμάτιο με θέα στη Θάλασσα")

    assert twin_label in comparable_room_type_categories(double_label)
    assert double_label in comparable_room_type_categories(twin_label)


def test_categories_outside_a_pool_stay_alone():
    assert comparable_room_type_categories("suite") == ("suite",)
    assert comparable_room_type_categories("family") == ("family",)


def test_blank_category_has_no_comparable_pool():
    assert comparable_room_type_categories(None) == ()
    assert comparable_room_type_categories("   ") == ()
