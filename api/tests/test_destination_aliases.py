from api.services.destination_aliases import (
    canonical_destination,
    destination_variants,
    normalize_destination_key,
)


def test_destination_variants_matches_faliraki_across_english_and_greek():
    variants = destination_variants("Faliraki")

    assert "Faliraki" in variants
    assert "Φαληράκι" in variants
    assert "φαληράκι" in variants
    assert "Φαληρακι" in variants


def test_destination_variants_is_accent_insensitive_for_greek_input():
    variants = destination_variants("Φαληρακι")

    assert "Faliraki" in variants
    assert "Φαληράκι" in variants


def test_destination_variants_keeps_unknown_destination_unchanged():
    assert destination_variants("Unknown Beach") == ("Unknown Beach",)


def test_normalize_destination_key_removes_accents_case_and_spacing():
    assert normalize_destination_key(" ΦΑΛΗΡΆΚΙ ") == normalize_destination_key("φαληρακι")


def test_canonical_destination_uses_stable_alias_key():
    assert canonical_destination("Φαληράκι") == "faliraki"
    assert canonical_destination("Rodos") == "rhodes"
    assert canonical_destination("Unknown Beach") == "unknownbeach"
