from datetime import date
from decimal import Decimal

import pytest

from api.services.room_rates_normalizer import (
    RoomRateNormalizationError,
    canonicalize_name,
    normalize_room_rate_row,
    split_facilities,
)


def _legacy_row(**overrides):
    row = {
        "record_id": "rec-1",
        "scraped_at": "2026-04-14T18:39:00Z",
        "check_in": "2026-06-17",
        "check_out": "2026-06-23",
        "hotel_name": "  Vera   Studios! ",
        "city": "Faliraki",
        "address": "Faliraki Center",
        "property_type": "Aparthotel",
        "latitude": "36.339072",
        "longitude": "28.192404",
        "stars": "0",
        "review_score": "8.6",
        "review_count": "234",
        "price_per_night_eur": "60.004",
        "nights": "6",
        "guests": "2",
        "adults": "2",
        "children": "0",
        "rooms": "1",
        "room_type": "Studio",
        "meals": "Breakfast",
        "free_cancellation": "No",
        "price_total_eur": "360",
        "facilities": "WiFi|Pool|wifi| Balcony ",
        "rooms_left": "5",
    }
    row.update(overrides)
    return row


def test_normalizes_legacy_room_rate_row_into_stable_keys():
    normalized = normalize_room_rate_row(_legacy_row())

    assert normalized.source_record_id == "rec-1"
    assert normalized.canonical_name == "vera studios"
    assert normalized.display_name == "Vera Studios!"
    assert normalized.check_in == date(2026, 6, 17)
    assert normalized.check_out == date(2026, 6, 23)
    assert normalized.price_per_night_eur == Decimal("60.00")
    assert normalized.latitude == Decimal("36.339072")
    assert normalized.longitude == Decimal("28.192404")
    assert normalized.adults == 2
    assert normalized.children == 0
    assert normalized.rooms == 1
    assert normalized.source_property_key.startswith("booking_com|vera studios|faliraki|")
    assert normalized.source_run_key.startswith("booking_com|faliraki|2026-06-17|2026-06-23|2|0|1|")


def test_booking_url_comes_from_the_csv_hotel_url_without_touching_the_property_key():
    """Round 6: the map popup links «Άνοιγμα στο Booking» to this URL. The
    property key must stay byte-identical, or every existing property would
    get a new identity and lose its price history."""
    url = "https://www.booking.com/hotel/gr/vera-studios.html"

    with_url = normalize_room_rate_row(_legacy_row(hotel_url=f"  {url} "))
    without_url = normalize_room_rate_row(_legacy_row())

    assert with_url.booking_url == url
    assert without_url.booking_url is None
    assert with_url.source_property_key == without_url.source_property_key


@pytest.mark.parametrize("hotel_url", ["", "   ", float("nan"), "nan", "javascript:alert(1)", "www.booking.com/hotel"])
def test_booking_url_is_none_unless_the_csv_value_is_a_web_url(hotel_url):
    assert normalize_room_rate_row(_legacy_row(hotel_url=hotel_url)).booking_url is None


def test_splits_facilities_and_deduplicates_case_insensitively():
    assert split_facilities("WiFi|Pool| wifi |Balcony||") == ("Balcony", "Pool", "WiFi")


def test_canonicalize_name_is_case_and_spacing_stable():
    assert canonicalize_name("  Vera   Studios! ") == "vera studios"


def test_populates_room_attributes_from_room_type_and_payload():
    normalized = normalize_room_rate_row(
        _legacy_row(room_type="Δίκλινο Δωμάτιο με Μπαλκόνι και Θέα στη Θάλασσα")
    )

    assert normalized.room_attributes == {
        "capacity": 2,
        "view": "sea",
        "has_balcony": True,
        "bed_hint": "double",
    }


def test_room_attributes_are_none_when_nothing_is_extractable():
    normalized = normalize_room_rate_row(_legacy_row(room_type="Mystery Booking Offer"))

    assert normalized.room_attributes is None


def test_rejects_missing_required_hotel_name():
    with pytest.raises(RoomRateNormalizationError, match="hotel_name is required"):
        normalize_room_rate_row(_legacy_row(hotel_name=""))


def test_rejects_invalid_date_range():
    with pytest.raises(RoomRateNormalizationError, match="check_out must be after check_in"):
        normalize_room_rate_row(_legacy_row(check_out="2026-06-17"))


def test_rejects_negative_price():
    with pytest.raises(RoomRateNormalizationError, match="price fields cannot be negative"):
        normalize_room_rate_row(_legacy_row(price_per_night_eur="-1"))


def test_rejects_invalid_occupancy_values():
    with pytest.raises(RoomRateNormalizationError, match="adults must be at least 1"):
        normalize_room_rate_row(_legacy_row(adults="0"))

    with pytest.raises(RoomRateNormalizationError, match="rooms must be at least 1"):
        normalize_room_rate_row(_legacy_row(rooms="0"))


# ---------------------------------------------------------------------------
# Rate plans (spec 2026-09-29 §3) — new fields with old-CSV defaults
# ---------------------------------------------------------------------------


def test_normalizes_rate_plan_fields_from_the_new_csv_columns():
    normalized = normalize_room_rate_row(
        _legacy_row(
            discounted_price_per_night_eur="55.204",
            discount_pct="8.0",
            discount_label="-8%",
            has_genius_discount="True",  # CSV round-trip spelling of the bool
            cancellation_type="non_refundable",
            payment_label="Πληρωμή online",
            rate_block_id="123456789_0_2_0",
        )
    )

    assert normalized.discounted_price_per_night_eur == Decimal("55.20")
    assert normalized.discount_pct == Decimal("8.0")
    assert normalized.discount_label == "-8%"
    assert normalized.has_genius_discount is True
    assert normalized.cancellation_type == "non_refundable"
    assert normalized.payment_label == "Πληρωμή online"
    assert normalized.rate_block_id == "123456789_0_2_0"


def test_rate_plan_fields_default_to_null_for_old_csvs():
    """CSVs written before the columns existed must keep normalizing, with
    EVERY plan field NULL — has_genius_discount too (None, not False), so a
    legacy re-ingest keeps rate_plan null end-to-end (review 2026-09-29)."""
    normalized = normalize_room_rate_row(_legacy_row())

    assert normalized.discounted_price_per_night_eur is None
    assert normalized.discount_pct is None
    assert normalized.discount_label is None
    assert normalized.has_genius_discount is None
    assert normalized.cancellation_type is None
    assert normalized.payment_label is None
    assert normalized.rate_block_id is None


def test_rate_plan_fields_treat_pandas_nan_as_missing():
    """DataFrame rows carry NaN for the optional plan columns (discount_pct is
    None-valued in the transform); NaN must neither break Decimal parsing nor
    reach the JSONB payload, which PostgreSQL rejects."""
    normalized = normalize_room_rate_row(
        _legacy_row(
            discounted_price_per_night_eur=float("nan"),
            discount_pct=float("nan"),
            discount_label=float("nan"),
            has_genius_discount=float("nan"),
            cancellation_type=float("nan"),
            payment_label=float("nan"),
            rate_block_id=float("nan"),
        )
    )

    assert normalized.discounted_price_per_night_eur is None
    assert normalized.discount_pct is None
    assert normalized.discount_label is None
    assert normalized.has_genius_discount is None
    assert normalized.cancellation_type is None
    assert normalized.payment_label is None
    assert normalized.rate_block_id is None
    # The payload the writer stores as JSONB carries None, never NaN.
    assert normalized.raw_payload["discount_pct"] is None


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, True),
        ("true", True),
        ("1", True),
        # False survives only as an explicitly present value (review 2026-09-29):
        # a fresh row with hasGeniusDiscount false still stores False…
        (False, False),
        ("False", False),
        # …while a missing/blank cell stays None so legacy rows keep NULL.
        (None, None),
        ("", None),
    ],
)
def test_has_genius_discount_parses_writer_bools_and_csv_spellings(value, expected):
    assert normalize_room_rate_row(_legacy_row(has_genius_discount=value)).has_genius_discount is expected


def test_rejects_negative_discounted_price():
    with pytest.raises(RoomRateNormalizationError, match="cannot be negative"):
        normalize_room_rate_row(_legacy_row(discounted_price_per_night_eur="-1"))


def test_rate_plan_labels_are_clipped_to_their_column_lengths():
    """Defensive: a runaway actor string must not fail the whole batch INSERT."""
    normalized = normalize_room_rate_row(
        _legacy_row(cancellation_type="x" * 100, payment_label="y" * 100, rate_block_id="z" * 100)
    )

    assert normalized.cancellation_type == "x" * 40
    assert normalized.payment_label == "y" * 60
    assert normalized.rate_block_id == "z" * 80
