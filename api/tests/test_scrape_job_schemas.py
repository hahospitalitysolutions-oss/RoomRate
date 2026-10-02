"""ScrapeJobCreate / ScrapeJobResponse Round 6 fields (spec §3.1, §7)."""

from datetime import date

import pytest
from pydantic import ValidationError

from api.schemas.scrape_jobs import ScrapeJobCreate, ScrapeJobResponse


def _create(**overrides) -> ScrapeJobCreate:
    payload = {"destination": "Φαληράκι", "check_in": date(2030, 7, 1), "check_out": date(2030, 7, 5)}
    payload.update(overrides)
    return ScrapeJobCreate(**payload)


def _response(**overrides) -> ScrapeJobResponse:
    payload = {
        "id": "00000000-0000-0000-0000-000000000777",
        "account_id": "00000000-0000-0000-0000-000000000001",
        "destination": "Φαληράκι",
        "check_in": date(2030, 7, 1),
        "check_out": date(2030, 7, 5),
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "status": "queued",
        "requested_at": "2026-05-10T12:00:00Z",
    }
    payload.update(overrides)
    return ScrapeJobResponse(**payload)


def test_create_defaults_to_no_nearby_areas_and_no_radius():
    request = _create()

    assert request.nearby_destinations == []
    assert request.radius_km is None


def test_create_normalizes_nearby_destinations_leniently():
    """Trim, drop blanks, case-insensitive dedupe, drop the main destination —
    alias-aware, so «faliraki» IS the main destination «Φαληράκι»."""
    request = _create(nearby_destinations=[" Ιξιά ", "", "ιξιά", "Αφάντου", "faliraki", "Faliraki", "  "])

    assert request.nearby_destinations == ["Ιξιά", "Αφάντου"]


@pytest.mark.parametrize(
    "nearby_destinations",
    [
        [f"Περιοχή {index}" for index in range(9)],  # 9 distinct entries after normalization
        ["Κ" * 101],  # one entry over 100 characters
    ],
)
def test_create_rejects_too_many_or_too_long_nearby_destinations(nearby_destinations):
    with pytest.raises(ValidationError):
        _create(nearby_destinations=nearby_destinations)


@pytest.mark.parametrize("radius_km", [0.4, 50.1, -1])
def test_create_rejects_radius_outside_half_to_fifty_km(radius_km):
    with pytest.raises(ValidationError):
        _create(radius_km=radius_km)


def test_create_accepts_the_radius_bounds():
    assert _create(radius_km=0.5).radius_km == 0.5
    assert _create(radius_km=50).radius_km == 50.0


def test_response_reads_null_columns_of_pre_round6_jobs_as_defaults():
    response = _response(nearby_destinations=None, radius_km=None)

    assert response.nearby_destinations == []
    assert response.radius_km is None


def test_response_echoes_the_persisted_search_form():
    # NUMERIC(5,1) arrives as Decimal; JSONB as a list.
    response = _response(nearby_destinations=["Ιξιά", "Αφάντου"], radius_km="10.0")

    assert response.nearby_destinations == ["Ιξιά", "Αφάντου"]
    assert response.radius_km == 10.0
