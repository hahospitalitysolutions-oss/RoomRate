"""fetch_hotel_lists: parallel scouts merged by URL, radius, sort, cap (spec §3.3)."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from scraper import ActorRunError, fetch_hotel_lists
from scraper.config import ScraperConfig

FALIRAKI = (36.34, 28.2)


def _item(name: str, slug: str, lat: float | None = 36.34, lng: float | None = 28.2) -> dict:
    location = {} if lat is None else {"lat": str(lat), "lng": str(lng)}
    return {"name": name, "url": f"https://www.booking.com/hotel/gr/{slug}.el.html?aid=1", "location": location, "type": "hotel"}


# Distances from FALIRAKI: Aegean View 0.0, Afandou Inn ~6.2, Ixia Bay ~7.8, Far Away ~40 km.
SCOUTS = {
    "Φαληράκι": [_item("Aegean View", "aegean-view"), _item("Far Away", "far-away", 36.7, 28.2), _item("No Coords", "no-coords", None, None)],
    "Ιξιά": [_item("Aegean View (dup)", "aegean-view", 36.35, 28.21), _item("Ixia Bay", "ixia-bay", 36.41, 28.19)],
    "Αφάντου": [_item("Afandou Inn", "afandou-inn", 36.29, 28.17)],
}


def _install_fake_actor(monkeypatch, failing: frozenset[str] = frozenset(), calls: list | None = None) -> None:
    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        destination = actor_input["search"]
        if calls is not None:
            calls.append((destination, actor_input["maxItems"]))
        if destination in failing:
            raise ActorRunError(f"actor down for {destination}")
        return SCOUTS.get(destination, [])

    monkeypatch.setattr("scraper.scout._run_actor", fake_run_actor)


def _config(**overrides) -> ScraperConfig:
    base = dict(
        destination="Φαληράκι",
        nearby_destinations=("Ιξιά", "Αφάντου"),
        scout_max_items=40,
        nearby_max_items=20,
        scout_cache_hours=0,
        deep_crawl_workers=3,
    )
    base.update(overrides)
    return ScraperConfig(**base)


class _FakeProgress:
    def __init__(self):
        self.updates: list[dict] = []

    def update(self, stage, done=None, total=None, **counts):
        self.updates.append({"stage": stage, "done": done, "total": total, **counts})


def _names(hotels: list[dict]) -> list[str]:
    return [hotel["name"] for hotel in hotels]


def test_merges_scouts_by_normalized_url_with_the_main_destination_first(monkeypatch):
    calls: list = []
    _install_fake_actor(monkeypatch, calls=calls)

    hotels = fetch_hotel_lists(client=object(), config=_config(), engine=None)

    assert _names(hotels) == ["Aegean View", "Far Away", "No Coords", "Ixia Bay", "Afandou Inn"]
    assert all("?" not in hotel["url"] for hotel in hotels)
    assert sorted(calls) == [("Αφάντου", 20), ("Ιξιά", 20), ("Φαληράκι", 40)]


def test_radius_keeps_hotels_within_reach_sorted_by_distance(monkeypatch):
    _install_fake_actor(monkeypatch)

    hotels = fetch_hotel_lists(client=object(), config=_config(origin_lat=36.34, origin_lng=28.2, radius_km=10), engine=None)

    # Far Away (~40 km) and No Coords are out; the rest are nearest-first.
    assert _names(hotels) == ["Aegean View", "Afandou Inn", "Ixia Bay"]


def test_an_origin_without_a_radius_only_sorts_with_unknown_distances_last(monkeypatch):
    _install_fake_actor(monkeypatch)

    hotels = fetch_hotel_lists(client=object(), config=_config(origin_lat=36.34, origin_lng=28.2), engine=None)

    assert _names(hotels) == ["Aegean View", "Afandou Inn", "Ixia Bay", "Far Away", "No Coords"]


def test_a_radius_that_keeps_no_hotel_warns_after_the_nearby_failures(monkeypatch):
    _install_fake_actor(monkeypatch, failing=frozenset({"Ιξιά"}))
    far_away = _config(origin_lat=40.64, origin_lng=22.94, radius_km=10)  # Θεσσαλονίκη
    warnings: list[str] = []

    hotels = fetch_hotel_lists(client=object(), config=far_away, engine=None, warnings=warnings)

    assert hotels == []
    assert warnings == ["nearby_scout_failed:Ιξιά", "radius_excluded_all"]


def test_a_radius_warns_when_every_hotel_lacks_coordinates(monkeypatch):
    monkeypatch.setitem(SCOUTS, "Φαληράκι", [_item("No Coords", "no-coords", None, None)])
    _install_fake_actor(monkeypatch)
    warnings: list[str] = []

    hotels = fetch_hotel_lists(
        client=object(),
        config=_config(nearby_destinations=(), origin_lat=36.34, origin_lng=28.2, radius_km=10),
        engine=None,
        warnings=warnings,
    )

    assert hotels == []
    assert warnings == ["radius_excluded_all"]


def test_no_radius_warning_when_the_scouts_found_nothing_to_exclude(monkeypatch):
    monkeypatch.setitem(SCOUTS, "Φαληράκι", [])
    _install_fake_actor(monkeypatch)
    warnings: list[str] = []

    hotels = fetch_hotel_lists(
        client=object(),
        config=_config(nearby_destinations=(), origin_lat=36.34, origin_lng=28.2, radius_km=10),
        engine=None,
        warnings=warnings,
    )

    assert hotels == []
    assert warnings == []


def test_cap_trims_the_sorted_list(monkeypatch):
    _install_fake_actor(monkeypatch)

    within = fetch_hotel_lists(
        client=object(),
        config=_config(origin_lat=36.34, origin_lng=28.2, radius_km=10, deep_crawl_max_hotels=2),
        engine=None,
    )
    unranked = fetch_hotel_lists(client=object(), config=_config(deep_crawl_max_hotels=2), engine=None)

    assert _names(within) == ["Aegean View", "Afandou Inn"]
    assert _names(unranked) == ["Aegean View", "Far Away"]


def test_nearby_scout_failure_is_a_warning_and_the_main_failure_an_error(monkeypatch):
    _install_fake_actor(monkeypatch, failing=frozenset({"Ιξιά"}))
    warnings: list[str] = []

    hotels = fetch_hotel_lists(client=object(), config=_config(), engine=None, warnings=warnings)

    assert warnings == ["nearby_scout_failed:Ιξιά"]
    assert "Ixia Bay" not in _names(hotels)

    _install_fake_actor(monkeypatch, failing=frozenset({"Φαληράκι"}))
    with pytest.raises(ActorRunError):
        fetch_hotel_lists(client=object(), config=_config(), engine=None)


def test_a_failed_main_scout_never_starts_the_scouts_still_queued(monkeypatch):
    calls: list[str] = []
    pool_shut_down = threading.Event()

    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        destination = actor_input["search"]
        calls.append(destination)
        if destination == "Φαληράκι":
            raise ActorRunError("actor down for the main destination")
        # A started nearby scout holds its worker until the pool shuts down, so
        # the only thing deciding whether the queue runs is the cancellation.
        pool_shut_down.wait(timeout=5)
        return []

    class ShutdownSignallingExecutor(ThreadPoolExecutor):
        def shutdown(self, wait=True, *, cancel_futures=False):
            super().shutdown(wait=False, cancel_futures=cancel_futures)
            pool_shut_down.set()
            if wait:
                super().shutdown(wait=True)

    monkeypatch.setattr("scraper.scout._run_actor", fake_run_actor)
    monkeypatch.setattr("scraper.scout.ThreadPoolExecutor", ShutdownSignallingExecutor)

    with pytest.raises(ActorRunError):
        fetch_hotel_lists(client=object(), config=_config(deep_crawl_workers=1), engine=None)

    # One worker: after the main failure it may already hold Ιξιά, but
    # Αφάντου is still queued and must never reach the actor.
    assert calls[0] == "Φαληράκι"
    assert "Αφάντου" not in calls


def test_progress_reports_each_scout_then_the_merge_and_radius(monkeypatch):
    _install_fake_actor(monkeypatch)
    progress = _FakeProgress()

    fetch_hotel_lists(
        client=object(),
        config=_config(origin_lat=36.34, origin_lng=28.2, radius_km=10),
        engine=None,
        progress=progress,
    )

    assert progress.updates[0] == {"stage": "scout", "done": 0, "total": 3, "destinations_total": 3, "hotels_found": 0}
    assert [update["done"] for update in progress.updates[1:4]] == [1, 2, 3]
    assert progress.updates[-1]["hotels_found"] == 5
    assert progress.updates[-1]["hotels_in_radius"] == 3


def test_hotels_found_counts_unique_hotels_and_never_goes_backwards(monkeypatch):
    _install_fake_actor(monkeypatch)
    progress = _FakeProgress()

    # One worker: the scouts finish in order Φαληράκι, Ιξιά, Αφάντου.
    fetch_hotel_lists(client=object(), config=_config(deep_crawl_workers=1), engine=None, progress=progress)

    found = [update["hotels_found"] for update in progress.updates]
    # Ιξιά lists Aegean View again: 6 listings but only 5 hotels, so the count
    # grows 3 -> 4 -> 5 and the post-merge snapshot repeats 5 instead of
    # dropping from a raw 6.
    assert found == [0, 3, 4, 5, 5]
    assert found == sorted(found)
