"""Unit tests for the pure helpers in scripts/staging_e2e.py.

Only pure logic is exercised — the staging run itself needs a live API,
worker, PostgreSQL and Apify credentials and is deliberately not run in CI.
Loading the module at import time also proves the script imports cleanly with
no live infrastructure configured.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "staging_e2e.py"

_spec = importlib.util.spec_from_file_location("staging_e2e", _SCRIPT_PATH)
staging = importlib.util.module_from_spec(_spec)
# Register before exec: dataclasses resolves cls.__module__ via sys.modules.
sys.modules[_spec.name] = staging
_spec.loader.exec_module(staging)  # must not require live infrastructure


# ----------------------------------------------------------------------------
# ws_alerts_url
# ----------------------------------------------------------------------------


def test_ws_alerts_url_derives_ws_scheme_and_encodes_ticket():
    url = staging.ws_alerts_url("http://127.0.0.1:8000", "abc+/=")
    assert url == "ws://127.0.0.1:8000/ws/alerts?ticket=abc%2B%2F%3D"


def test_ws_alerts_url_uses_wss_for_https_and_strips_trailing_slash():
    url = staging.ws_alerts_url("https://api.example.com/", "t1")
    assert url == "wss://api.example.com/ws/alerts?ticket=t1"


# ----------------------------------------------------------------------------
# select_tracked_competitors
# ----------------------------------------------------------------------------


def test_select_tracked_competitors_dedups_and_caps():
    markers = [
        {"property_id": "p1", "room_package_id": "rp1", "hotel_name": "A"},
        {"property_id": "p1", "room_package_id": "rp2", "hotel_name": "A"},
        {"property_id": None, "room_package_id": None, "hotel_name": "no-id"},
        {"property_id": "p2", "room_package_id": None, "hotel_name": "B"},
        {"property_id": "p3", "room_package_id": "rp3", "hotel_name": "C"},
    ]

    selected = staging.select_tracked_competitors(markers, limit=2)

    assert selected == [
        {"property_id": "p1", "room_package_id": "rp1"},
        {"property_id": "p2", "room_package_id": None},
    ]


def test_select_tracked_competitors_empty_input():
    assert staging.select_tracked_competitors([], limit=3) == []


# ----------------------------------------------------------------------------
# is_price_change_frame
# ----------------------------------------------------------------------------


def test_is_price_change_frame_matches_envelope():
    raw = '{"account_id": "a", "notification": {"notification_type": "price_change", "title": "t"}}'
    assert staging.is_price_change_frame(raw) is True


def test_is_price_change_frame_rejects_connected_and_other_types():
    assert staging.is_price_change_frame('{"type": "connected"}') is False
    assert staging.is_price_change_frame(
        '{"notification": {"notification_type": "schedule_disabled"}}'
    ) is False
    assert staging.is_price_change_frame("not json") is False


# ----------------------------------------------------------------------------
# recommendation_problems
# ----------------------------------------------------------------------------


def _valid_recommendation_body() -> dict:
    return {
        "statistics": {"sample_runs": 2, "notes": []},
        "recommendation_available": True,
        "recommendation": {
            "recommended_price_eur": 112.0,
            "price_range_low_eur": 98.0,
            "price_range_high_eur": 124.0,
            "confidence": "medium",
            "source": "statistical",
        },
    }


def test_recommendation_problems_empty_for_valid_body():
    assert staging.recommendation_problems(_valid_recommendation_body()) == []


def test_recommendation_problems_flags_unavailable_without_data_note():
    body = {
        "statistics": {"sample_runs": 0, "notes": ["No price history available."]},
        "recommendation_available": False,
        "recommendation": None,
    }
    problems = staging.recommendation_problems(body)
    assert any("recommendation_available" in problem for problem in problems)


def test_recommendation_problems_flags_non_positive_and_misordered_prices():
    body = _valid_recommendation_body()
    body["recommendation"]["recommended_price_eur"] = 0
    assert staging.recommendation_problems(body)

    body = _valid_recommendation_body()
    body["recommendation"]["price_range_low_eur"] = 200.0
    assert staging.recommendation_problems(body)


def test_recommendation_problems_flags_available_flag_mismatch():
    body = _valid_recommendation_body()
    body["recommendation"] = None
    problems = staging.recommendation_problems(body)
    assert any("null" in problem or "None" in problem for problem in problems)


def test_recommendation_problems_flags_invalid_confidence():
    body = _valid_recommendation_body()
    body["recommendation"]["confidence"] = "certain"
    assert staging.recommendation_problems(body)


# ----------------------------------------------------------------------------
# default_market_dates
# ----------------------------------------------------------------------------


def test_default_market_dates_are_ahead_and_ordered():
    check_in, check_out = staging.default_market_dates(date(2026, 7, 23))
    assert check_in == "2026-08-22"
    assert check_out == "2026-08-26"
