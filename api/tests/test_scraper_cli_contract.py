"""The argv the API builds must parse in the scraper CLI.

The two sides were built on separate branches in Round 6; a flag one side
sends and the other does not know makes every scrape exit with code 2.
"""

import uuid
from datetime import date

from api.services.scrape_job_service import BookingScrapeJobRunner, ScrapeJobCommand
from scraper.cli import build_config_from_args


def _command(**overrides) -> ScrapeJobCommand:
    values = dict(
        job_id=uuid.UUID("5b0c6c1e-7d4e-4a53-9a0e-2f5f0c9d1a11"),
        account_id=uuid.UUID("0f3e7a9d-2b64-4c1f-8e5a-6d7c8b9a0e22"),
        destination="Φαληράκι",
        check_in=date(2026, 10, 5),
        check_out=date(2026, 10, 9),
        adults=3,
        children=1,
        rooms=2,
        job_type="competitor_search",
        room_type_category="twin",
        filters_payload={"limit": 30, "room_type": "Δίκλινο Δωμάτιο με Μπαλκόνι"},
    )
    values.update(overrides)
    return ScrapeJobCommand(**values)


def test_competitor_job_with_nearby_areas_and_radius_parses_into_the_scraper_config():
    command = _command(
        nearby_destinations=("Καλλιθέα Ρόδου", "Ιξιά", "Κολύμπια"),
        radius_km=7.5,
        origin_lat=36.339597,
        origin_lng=28.202631,
    )

    config = build_config_from_args(BookingScrapeJobRunner()._build_args(command))

    assert config.scrape_job_id == command.job_id
    assert config.destination == "Φαληράκι"
    assert config.nearby_destinations == ("Καλλιθέα Ρόδου", "Ιξιά", "Κολύμπια")
    assert config.scout_max_items == 30
    assert config.nearby_max_items == 20
    assert config.deep_crawl_max_hotels == 90
    assert config.radius_km == 7.5
    assert (config.origin_lat, config.origin_lng) == (36.339597, 28.202631)
    assert (config.adults, config.children, config.rooms) == (3, 1, 2)
    assert config.room_type_category == "twin"
    assert config.room_name_query == "Δίκλινο Δωμάτιο με Μπαλκόνι"
    assert config.scout_cache_hours == 2  # manual: a hotel list up to 2 h old, prices always live


def test_radius_without_an_origin_sends_no_radius_flags_and_still_parses():
    command = _command(nearby_destinations=("Ιξιά",), radius_km=12.0)

    config = build_config_from_args(BookingScrapeJobRunner()._build_args(command))

    assert config.radius_km is None
    assert config.origin_lat is None and config.origin_lng is None
    assert config.nearby_destinations == ("Ιξιά",)


def test_scheduled_room_discovery_job_parses_with_target_url_and_cache_window():
    command = _command(
        job_type="owned_property_room_discovery",
        room_type_category=None,
        scheduled=True,
        filters_payload={
            "limit": 1,
            "deep_crawl_max_items": 30,
            "target_urls": ["https://www.booking.com/hotel/gr/rea-hotel.el.html"],
        },
    )

    config = build_config_from_args(BookingScrapeJobRunner()._build_args(command))

    assert config.job_type == "owned_property_room_discovery"
    assert config.target_urls == ("https://www.booking.com/hotel/gr/rea-hotel.el.html",)
    assert config.scout_cache_hours == 12
    assert config.nearby_destinations == ()
