"""Booker countries: Greek, English, codes and the spellings owners use."""

import pytest

from api.services.performance.countries import UNKNOWN_COUNTRY, country_code, country_name


@pytest.mark.parametrize(
    "value, code",
    [
        ("Γερμανία", "DE"),
        ("ΓΕΡΜΑΝΙΑ", "DE"),
        ("γερμανια", "DE"),
        ("Germany", "DE"),
        ("de", "DE"),
        (" GR ", "GR"),
        ("Ελλάδα", "GR"),
        ("Η.Π.Α.", "US"),
        ("USA", "US"),
        ("UK", "GB"),
        ("Αγγλία", "GB"),
        ("Ηνωμένο Βασίλειο", "GB"),
        ("is", "IS"),
        ("zz", "ZZ"),  # a code outside the list is kept
    ],
)
def test_country_code(value, code):
    assert country_code(value) == code


@pytest.mark.parametrize("value", [None, "", "   ", "Ατλαντίδα", "12"])
def test_country_code_of_nothing_or_an_unknown_name_is_none(value):
    assert country_code(value) is None


def test_country_name_is_greek_with_a_fallback():
    assert country_name("DE") == "Γερμανία"
    assert country_name("us") == "Η.Π.Α."
    assert country_name("ZZ") == "ZZ"
    assert country_name(None) == UNKNOWN_COUNTRY
