import logging
from datetime import date, datetime, timezone
from uuid import UUID

from api.schemas.onboarding import (
    AutomaticSetupRequest,
    OwnedPropertyOnboardingCreate,
    PropertyCandidate,
)
from api.schemas.scrape_jobs import ScrapeJobResponse
from api.services.onboarding_service import BookingPropertyCandidateProvider, OnboardingService


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")


def test_booking_candidate_provider_searches_location_first(monkeypatch):
    calls = []

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, engine
        calls.append((config.destination, config.scout_max_items))
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    candidates = provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=24,
    )

    assert calls == [("Faliraki", 12)]
    assert len(candidates) == 1
    assert candidates[0].display_name == "Aegean View"


def test_booking_candidate_provider_falls_back_to_property_name_search(monkeypatch):
    calls = []

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, engine
        calls.append((config.destination, config.scout_max_items))
        if config.destination == "Faliraki":
            return []
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    candidates = provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=24,
    )

    assert calls == [("Faliraki", 12), ("Aegean View, Faliraki", 12)]
    assert len(candidates) == 1
    assert candidates[0].display_name == "Aegean View"


def test_booking_candidate_provider_searches_by_name_when_the_area_list_misses_the_property(monkeypatch):
    """«Αναζήτηση ξανά» used to return the same cached top-of-area list forever."""
    calls = []
    salamina_hotels = [
        {"name": "Salamis Bay Hotel", "url": "https://www.booking.com/hotel/gr/salamis-bay.html"},
        {"name": "Sabbal Apartments", "url": "https://www.booking.com/hotel/gr/sabbal.el.html?aid=1"},
    ]

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, engine
        calls.append(config.destination)
        if config.destination == "Σαλαμίνα":
            return [salamina_hotels[0], {"name": "Votsalo Studios", "url": "https://www.booking.com/hotel/gr/votsalo.html"}]
        return [salamina_hotels[1], salamina_hotels[0]]

    monkeypatch.setattr("scraper.build_client", lambda: object())
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    candidates = BookingPropertyCandidateProvider().search(
        property_name="Sabbal Apartments",
        location="Σαλαμίνα",
        check_in=date(2026, 11, 3),
        check_out=date(2026, 11, 7),
        adults=2,
        children=0,
        rooms=1,
        limit=8,
        cache_hours=24,
    )

    assert calls == ["Σαλαμίνα", "Sabbal Apartments, Σαλαμίνα"]
    # Name results first, then the rest of the area, each listing once.
    assert [c.display_name for c in candidates] == ["Sabbal Apartments", "Salamis Bay Hotel", "Votsalo Studios"]


def test_booking_candidate_provider_forwards_cache_hours_to_scout_config(monkeypatch):
    """Pins the pass-through at the literal call site the live bug traced to
    (onboarding_service.py's _search_config, formerly a hardcoded
    scout_cache_hours=0): the configured cache_hours must reach
    ScraperConfig.scout_cache_hours unchanged. The property-name-fallback
    search reuses the same _search_config helper (see
    test_booking_candidate_provider_falls_back_to_property_name_search), so
    one pinned call site covers both.
    """
    captured_cache_hours = []

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, engine
        captured_cache_hours.append(config.scout_cache_hours)
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=7,
    )

    assert captured_cache_hours == [7]


def test_booking_candidate_provider_keeps_the_address_from_a_cache_hit(monkeypatch):
    """Closes the loop on the scout cache's address column.

    Warm and cold searches must produce identical candidates. The scout cache
    returns hotel dicts shaped exactly like a live scout run, so a cache HIT
    must still fill PropertyCandidate.address -- this is what the pick and
    confirm cards render, and what auto-setup persists on the owned property.
    """
    cached_hotel = {
        "name": "Aegean View",
        "url": "https://www.booking.com/hotel/gr/aegean-view.html",
        "city": "Faliraki",
        "address": "Leoforos Kalithea 12, Faliraki",
        "property_type": "Hotel",
        "stars": 3.0,
        "review_score": 8.8,
        "review_count": 120,
    }

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, config, engine
        return [cached_hotel]

    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider(engine=object())
    candidates = provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=24,
    )

    assert candidates[0].address == "Leoforos Kalithea 12, Faliraki"


def test_booking_candidate_provider_threads_the_injected_engine_into_the_scout_call(monkeypatch):
    """The cache knob is inert without an engine: scraper/scout.py gates BOTH
    the cache read and the cache write on ``engine is not None``, independently
    of scout_cache_hours. So the engine reaching fetch_hotel_list is what
    actually activates PROPERTY_CANDIDATES_CACHE_HOURS -- assert the exact
    injected object, not merely "not None", so a stray second engine fails here.
    """
    injected_engine = object()
    captured_engines = []

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, config
        captured_engines.append(engine)
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider(engine=injected_engine)
    provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=24,
    )

    assert captured_engines == [injected_engine]


def test_booking_candidate_provider_threads_the_engine_into_the_name_retry_search(monkeypatch):
    """The property-name fallback is a SECOND paid scout run, so it needs the
    cache just as much as the first one. Pins that both call sites carry the
    same engine (the retry used to be an independent engine=None literal).
    """
    injected_engine = object()
    captured = []

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client
        captured.append((config.destination, engine))
        if config.destination == "Faliraki":
            return []
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider(engine=injected_engine)
    provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=24,
    )

    assert captured == [
        ("Faliraki", injected_engine),
        ("Aegean View, Faliraki", injected_engine),
    ]


def test_booking_candidate_provider_defaults_to_the_shared_api_engine(monkeypatch):
    """Production wiring: nothing injects an engine, so the provider must reuse
    the app's EXISTING cached engine via get_engine(role="api") -- not build a
    second pool. Recording the role keeps the "writer" engine (no statement
    timeout, meant for scraper bulk writes) from being borrowed by a request path.
    """
    shared_engine = object()
    requested_roles = []
    captured_engines = []

    def fake_get_engine(role="api"):
        requested_roles.append(role)
        return shared_engine

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, config
        captured_engines.append(engine)
        return []

    monkeypatch.setattr("api.db.get_engine", fake_get_engine)
    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    provider.search(
        property_name="Aegean View",
        location="Faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=3,
        cache_hours=24,
    )

    # Both scout calls (location + name retry) get the one shared engine...
    assert captured_engines == [shared_engine, shared_engine]
    # ...resolved exactly once per search, under the "api" role. Two lookups
    # would still work (get_engine caches), but one keeps the retry provably
    # on the same engine as the first search.
    assert requested_roles == ["api"]


def test_booking_candidate_provider_does_not_resolve_the_engine_until_it_searches(monkeypatch):
    """Resolution must be lazy, and laziness is a behavior, not a docstring.

    OnboardingService constructs this provider unconditionally, so resolving in
    __init__ would make the service require DATABASE_URL just to be imported or
    constructed. Laziness also means resolution is retried per search: a
    transiently missing DATABASE_URL heals on the next call (and re-warns),
    whereas an eager resolve would freeze that failure for the instance's life.
    """
    resolve_calls = []

    def fake_get_engine(role="api"):
        resolve_calls.append(role)
        return object()

    monkeypatch.setattr("api.db.get_engine", fake_get_engine)

    BookingPropertyCandidateProvider()

    # Construction alone must not touch the database configuration at all;
    # test_booking_candidate_provider_defaults_to_the_shared_api_engine covers
    # the other half (exactly one resolution once a search actually runs).
    assert resolve_calls == []


def test_booking_candidate_provider_still_searches_when_the_engine_is_unavailable(monkeypatch, caplog):
    """The cache is an optimization, not a dependency. get_engine() raises when
    DATABASE_URL is missing; that must degrade to today's uncached live scout
    (engine=None), never turn a working candidate search into a 500.

    The warning is part of the contract: a silently uncached search looks
    identical to a cache miss, so this line is the only signal an operator gets
    that every candidate search is paying full Apify cost.
    """
    captured_engines = []

    def fake_get_engine(role="api"):
        raise RuntimeError("DATABASE_URL is required for PostgreSQL access")

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, config
        captured_engines.append(engine)
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("api.db.get_engine", fake_get_engine)
    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    with caplog.at_level(logging.WARNING, logger="api.services.onboarding_service"):
        candidates = provider.search(
            property_name="Aegean View",
            location="Faliraki",
            check_in=date(2026, 7, 1),
            check_out=date(2026, 7, 5),
            adults=2,
            children=0,
            rooms=1,
            limit=3,
            cache_hours=24,
        )

    assert captured_engines == [None]
    assert len(candidates) == 1
    # Load-bearing words only, so rewording the sentence does not fail here.
    degraded_warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and "Scout cache disabled" in record.getMessage()
    ]
    assert len(degraded_warnings) == 1


def test_booking_candidate_provider_regains_the_cache_on_the_search_after_a_failure(monkeypatch, caplog):
    """A transient engine failure must heal on the NEXT search, not stick.

    This is the whole point of resolving per search rather than once: memoizing
    the outcome onto the instance (success AND failure) passes every other test
    in this file while permanently disabling the cache for a provider that
    merely started up before the database was reachable -- silently paying full
    Apify cost on every candidate search from then on. The warning count is
    half the assertion: healing must also stop the warning.
    """
    recovered_engine = object()
    resolution_outcomes = [
        RuntimeError("DATABASE_URL is required for PostgreSQL access"),
        recovered_engine,
    ]
    captured_engines = []

    def fake_get_engine(role="api"):
        outcome = resolution_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, config
        captured_engines.append(engine)
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "city": "Faliraki",
                "address": "Faliraki Center",
            }
        ]

    monkeypatch.setattr("api.db.get_engine", fake_get_engine)
    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    with caplog.at_level(logging.WARNING, logger="api.services.onboarding_service"):
        for _attempt in range(2):
            provider.search(
                property_name="Aegean View",
                location="Faliraki",
                check_in=date(2026, 7, 1),
                check_out=date(2026, 7, 5),
                adults=2,
                children=0,
                rooms=1,
                limit=3,
                cache_hours=24,
            )

    # Same provider instance: uncached first, cached once the engine came back.
    assert captured_engines == [None, recovered_engine]
    degraded_warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and "Scout cache disabled" in record.getMessage()
    ]
    assert len(degraded_warnings) == 1


def test_booking_candidate_provider_never_logs_a_malformed_database_url(monkeypatch, caplog):
    """The degradation warning must not become a credential leak.

    A non-empty but malformed DATABASE_URL makes SQLAlchemy raise
    ArgumentError whose message embeds the raw URL, password and all. Only
    api/db.py's own RuntimeError text is safe to echo; anything else is
    reported by exception type alone.
    """

    class ArgumentError(Exception):
        """Stands in for sqlalchemy.exc.ArgumentError (embeds the URL)."""

    def fake_get_engine(role="api"):
        raise ArgumentError(
            "Could not parse SQLAlchemy URL from string "
            "'postgresql+psycopg://roomrate:sup3rs3cr3t@db.example.com:5432/roomrate'"
        )

    def fake_build_client():
        return object()

    def fake_fetch_hotel_list(client, config, engine=None):
        _ = client, config, engine
        return []

    monkeypatch.setattr("api.db.get_engine", fake_get_engine)
    monkeypatch.setattr("scraper.build_client", fake_build_client)
    monkeypatch.setattr("scraper.fetch_hotel_list", fake_fetch_hotel_list)

    provider = BookingPropertyCandidateProvider()
    with caplog.at_level(logging.WARNING, logger="api.services.onboarding_service"):
        provider.search(
            property_name="Aegean View",
            location="Faliraki",
            check_in=date(2026, 7, 1),
            check_out=date(2026, 7, 5),
            adults=2,
            children=0,
            rooms=1,
            limit=3,
            cache_hours=24,
        )

    logged = " ".join(record.getMessage() for record in caplog.records)

    assert "sup3rs3cr3t" not in logged
    assert "db.example.com" not in logged
    # Still actionable: the operator learns which failure they are looking at.
    assert "Scout cache disabled" in logged
    assert "ArgumentError" in logged


class FakeOnboardingRepository:
    def __init__(self, replace_found: bool = True, delete_result: bool = True):
        self.replace_found = replace_found
        self.delete_result = delete_result
        self.created: tuple | None = None
        self.replaced: tuple | None = None
        self.deleted: tuple | None = None

    def create_owned_property(self, account_id, request):
        self.created = (account_id, request)
        return {"id": OWNED_PROPERTY_ID, "account_id": account_id, "display_name": request.display_name}

    def list_room_types(self, account_id, owned_property_id):
        return []

    def set_selected_room_type(self, account_id, owned_property_id, room_type_category):
        return True

    def replace_owned_property(self, account_id, owned_property_id, request):
        self.replaced = (account_id, owned_property_id, request)
        if not self.replace_found:
            return None
        return {"id": OWNED_PROPERTY_ID, "market_changed": False}

    def delete_owned_property(self, account_id, owned_property_id):
        self.deleted = (account_id, owned_property_id)
        return self.delete_result


class FakeScrapeJobService:
    def __init__(self):
        self.created = None

    def create_job(self, account_id, request):
        self.created = (account_id, request)
        return ScrapeJobResponse(
            id=JOB_ID,
            account_id=account_id,
            owned_property_id=request.owned_property_id,
            job_type=request.job_type,
            room_type_category=request.room_type_category,
            destination=request.destination,
            raw_destination=request.raw_destination,
            canonical_destination=request.canonical_destination,
            check_in=request.check_in,
            check_out=request.check_out,
            adults=request.adults,
            children=request.children,
            rooms=request.rooms,
            filters_payload=request.filters_payload,
            status="queued",
            requested_at=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        )


def _candidate(display_name: str, review_score: float | None = 8.0) -> PropertyCandidate:
    return PropertyCandidate(
        candidate_key=display_name.lower().replace(" ", "-"),
        display_name=display_name,
        booking_url=f"https://www.booking.com/hotel/gr/{display_name.lower().replace(' ', '-')}.html",
        city="Faliraki",
        review_score=review_score,
    )


class FakeCandidateProvider:
    """Mirrors BookingPropertyCandidateProvider.search so a renamed kwarg fails here too."""

    def __init__(self, candidates: list[PropertyCandidate]):
        self.candidates = candidates
        self.calls: list[dict] = []

    def search(
        self,
        property_name: str,
        location: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        limit: int,
        cache_hours: int,
    ) -> list[PropertyCandidate]:
        self.calls.append(
            {
                "property_name": property_name,
                "location": location,
                "check_in": check_in,
                "check_out": check_out,
                "adults": adults,
                "children": children,
                "rooms": rooms,
                "limit": limit,
                "cache_hours": cache_hours,
            }
        )
        return list(self.candidates)


def test_owned_property_discovery_job_uses_bounded_direct_url_payload():
    scrape_jobs = FakeScrapeJobService()
    service = OnboardingService(repository=FakeOnboardingRepository(), scrape_job_service=scrape_jobs)

    service.create_owned_property_and_discovery_job(
        ACCOUNT_ID,
        OwnedPropertyOnboardingCreate(
            display_name="Rea Hotel",
            booking_url="https://www.booking.com/hotel/gr/rea.html",
            city="Faliraki",
            raw_destination="Faliraki",
            check_in=date(2026, 7, 1),
            check_out=date(2026, 7, 5),
            adults=2,
            children=0,
            rooms=1,
        ),
    )

    _, request = scrape_jobs.created
    assert request.job_type == "owned_property_room_discovery"
    assert request.filters_payload == {
        "target_urls": ["https://www.booking.com/hotel/gr/rea.html"],
        "limit": 1,
        "deep_crawl_max_items": 30,
    }


def test_search_property_candidates_returns_best_match_first():
    """The upcoming /setup wizard will preselect candidates[0], so ordering is a contract.

    The provider returns whatever Booking's search ranked; only the exact/
    substring name match is meaningful for "which hotel are you".
    """
    provider = FakeCandidateProvider(
        [
            _candidate("Faliraki Beach Resort", review_score=9.4),
            _candidate("Rea Hotel"),
            _candidate("Rea Hotel Annex"),
        ]
    )
    # cache_hours pinned explicitly so this assertion never depends on the
    # ambient PROPERTY_CANDIDATES_CACHE_HOURS setting (see the dedicated
    # cache_hours pass-through tests below for that behavior).
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
        property_candidates_cache_hours=24,
    )

    candidates = service.search_property_candidates(
        property_name="Rea Hotel",
        location="Faliraki",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=8,
    )

    assert [candidate.display_name for candidate in candidates] == [
        "Rea Hotel",
        "Rea Hotel Annex",
        "Faliraki Beach Resort",
    ]
    # The stay/occupancy arguments must reach the provider unchanged, under the
    # names the real provider declares.
    assert provider.calls == [
        {
            "property_name": "Rea Hotel",
            "location": "Faliraki",
            "check_in": date(2030, 7, 1),
            "check_out": date(2030, 7, 5),
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "limit": 8,
            "cache_hours": 24,
        }
    ]


def test_search_property_candidates_forwards_configured_cache_hours_to_the_provider():
    """Non-default value (7, not the 24 default) proves real pass-through of the
    configured PROPERTY_CANDIDATES_CACHE_HOURS setting, not a hardcoded number
    reappearing by coincidence.
    """
    provider = FakeCandidateProvider([_candidate("Rea Hotel")])
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
        property_candidates_cache_hours=7,
    )

    service.search_property_candidates(
        property_name="Rea Hotel",
        location="Faliraki",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=8,
    )

    assert provider.calls[0]["cache_hours"] == 7


def test_search_property_candidates_cache_hours_zero_disables_caching_by_config():
    """0 must still reach the provider as 0 -- the pre-fix force-fresh-scrape
    behavior stays reachable by explicit configuration, it is just no longer
    the only option.
    """
    provider = FakeCandidateProvider([_candidate("Rea Hotel")])
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
        property_candidates_cache_hours=0,
    )

    service.search_property_candidates(
        property_name="Rea Hotel",
        location="Faliraki",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=8,
    )

    assert provider.calls[0]["cache_hours"] == 0


def test_settings_expose_property_candidates_cache_hours_default():
    """Unset PROPERTY_CANDIDATES_CACHE_HOURS must default to 24 (matches
    ScraperConfig's own scout_cache_hours default in scraper/config.py).
    """
    from api.config import Settings

    fresh_settings = Settings(_env_file=None)

    assert fresh_settings.property_candidates_cache_hours == 24


def test_onboarding_service_falls_back_to_settings_when_cache_hours_unset():
    """No override passed -> OnboardingService reads PROPERTY_CANDIDATES_CACHE_HOURS
    from settings itself, exactly like BookingScrapeJobRunner falls back to
    settings.scrape_job_timeout_seconds.

    Expectation pinned via Settings(_env_file=None) -- house idiom, see
    test_config_validation.py:30 -- not the ambient settings singleton: a
    version comparing against the ambient singleton would silently lose its
    kill power the moment PROPERTY_CANDIDATES_CACHE_HOURS is ever set in the
    real .env (both sides would drift together). This stays pinned to the
    code default regardless of the environment.
    """
    from api.config import Settings

    provider = FakeCandidateProvider([_candidate("Rea Hotel")])
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
    )

    assert service.property_candidates_cache_hours == Settings(_env_file=None).property_candidates_cache_hours


def test_search_property_candidates_breaks_name_ties_by_review_score():
    """With no name overlap to separate them, the review bonus decides the order.

    This is the only tier where review_score changes anything: the exact (100.0)
    and substring (85.0) tiers ignore it. An unrated candidate scores 0.0 and so
    sinks below every rated one.
    """
    provider = FakeCandidateProvider(
        [
            _candidate("Kappa Villas", review_score=None),
            _candidate("Zeta Palace", review_score=6.0),
            _candidate("Omega Lodge", review_score=9.8),
        ]
    )
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
    )

    candidates = service.search_property_candidates(
        property_name="Rea Hotel",
        location="Faliraki",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=8,
    )

    assert [candidate.display_name for candidate in candidates] == [
        "Omega Lodge",
        "Zeta Palace",
        "Kappa Villas",
    ]


def test_automatic_setup_picks_the_best_match_not_the_provider_order():
    """automatic_setup must not diverge from what the upcoming /setup wizard will show first."""
    provider = FakeCandidateProvider(
        [
            _candidate("Faliraki Beach Resort", review_score=9.4),
            _candidate("Rea Hotel"),
        ]
    )
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
    )

    response = service.automatic_setup(
        ACCOUNT_ID,
        AutomaticSetupRequest(
            property_name="Rea Hotel",
            location="Faliraki",
            check_in=date(2030, 7, 1),
            check_out=date(2030, 7, 5),
        ),
    )

    assert response.selected_candidate.display_name == "Rea Hotel"


def test_automatic_setup_does_not_read_a_country_off_the_candidate():
    """The persisted country must come from a real extraction, not from a field nobody fills.

    BookingPropertyCandidateProvider._candidate_from_hotel never sets
    PropertyCandidate.country, so automatic_setup's old ``country=candidate.country``
    could only ever persist None. Anything that DID arrive there would be
    untrusted (no extraction defines it), which is why the read is gone rather
    than kept "just in case": a candidate carrying a country changes nothing.
    """
    candidate_with_country = _candidate("Rea Hotel").model_copy(update={"country": "GR"})
    repository = FakeOnboardingRepository()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=FakeCandidateProvider([candidate_with_country]),
    )

    service.automatic_setup(
        ACCOUNT_ID,
        AutomaticSetupRequest(
            property_name="Rea Hotel",
            location="Faliraki",
            check_in=date(2030, 7, 1),
            check_out=date(2030, 7, 5),
        ),
    )

    assert repository.created is not None
    _, created_request = repository.created
    assert created_request.country is None


def test_replace_owned_property_queues_a_fresh_discovery_job():
    """The new hotel's rooms are unknown, so discovery must run again."""
    repository = FakeOnboardingRepository()
    scrape_jobs = FakeScrapeJobService()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=scrape_jobs,
        candidate_provider=FakeCandidateProvider([]),
    )

    # raw_destination differs from city on purpose: it pins which of the two
    # the job carries. Occupancy is non-default on purpose too: both schemas
    # default to (2, 0, 1), so dropped kwargs would be refilled identically
    # and pass unnoticed.
    request = OwnedPropertyOnboardingCreate(
        display_name="Bellezza",
        booking_url="https://www.booking.com/hotel/gr/bellezza.html",
        city="Faliraki",
        raw_destination="Faliraki, Rhodes",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=3,
        children=1,
        rooms=2,
    )
    response = service.replace_owned_property_and_discovery_job(
        ACCOUNT_ID, OWNED_PROPERTY_ID, request
    )

    assert response.owned_property_id == OWNED_PROPERTY_ID
    # The repository saw the same identity and payload the caller sent --
    # swapped UUIDs are still UUIDs, so only recording the call catches that.
    assert repository.replaced == (ACCOUNT_ID, OWNED_PROPERTY_ID, request)
    account_id, job_request = scrape_jobs.created
    assert account_id == ACCOUNT_ID
    assert job_request.owned_property_id == OWNED_PROPERTY_ID
    assert job_request.job_type == "owned_property_room_discovery"
    # The whole stay must reach the job: discovery scrapes real prices, and a
    # dropped or hardcoded field would silently price the wrong stay.
    assert job_request.destination == "Faliraki, Rhodes"
    assert job_request.raw_destination == "Faliraki, Rhodes"
    assert job_request.check_in == date(2030, 7, 1)
    assert job_request.check_out == date(2030, 7, 5)
    assert (job_request.adults, job_request.children, job_request.rooms) == (3, 1, 2)
    # Full payload equality, like the create-path test: target_urls alone
    # would let the crawl bounds drift from the create path unnoticed.
    assert job_request.filters_payload == {
        "target_urls": ["https://www.booking.com/hotel/gr/bellezza.html"],
        "limit": 1,
        "deep_crawl_max_items": 30,
    }


def test_replace_owned_property_returns_none_when_not_owned():
    repository = FakeOnboardingRepository(replace_found=False)
    scrape_jobs = FakeScrapeJobService()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=scrape_jobs,
        candidate_provider=FakeCandidateProvider([]),
    )

    response = service.replace_owned_property_and_discovery_job(
        ACCOUNT_ID,
        OWNED_PROPERTY_ID,
        OwnedPropertyOnboardingCreate(
            display_name="Bellezza",
            booking_url="https://www.booking.com/hotel/gr/bellezza.html",
            city="Faliraki",
            check_in=date(2030, 7, 1),
            check_out=date(2030, 7, 5),
        ),
    )

    assert response is None
    # No scrape may be queued for a property we do not own.
    assert scrape_jobs.created is None


def test_delete_owned_property_delegates_to_the_repository():
    """Forwards the identity pair and returns the repository's answer.

    The False branch matters as much as True: Task 5's route turns it into
    the 404, so a service hardcoding True would delete nothing and say 204.
    """
    repository = FakeOnboardingRepository()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=FakeCandidateProvider([]),
    )

    assert service.delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID) is True
    assert repository.deleted == (ACCOUNT_ID, OWNED_PROPERTY_ID)

    missing = FakeOnboardingRepository(delete_result=False)
    service = OnboardingService(
        repository=missing,
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=FakeCandidateProvider([]),
    )
    assert service.delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID) is False


def test_booking_search_without_an_apify_token_is_a_clear_setup_error(monkeypatch):
    """Not a raw OSError (a CORS-less 500): a 422 the onboarding routes pass on."""
    import pytest

    from api.services.onboarding_service import BOOKING_SEARCH_NOT_CONFIGURED

    def missing_token():
        raise OSError("Δεν βρέθηκε APIFY_TOKEN.")

    monkeypatch.setattr("scraper.build_client", missing_token)

    with pytest.raises(ValueError) as exc_info:
        BookingPropertyCandidateProvider().search(
            property_name="Aegean View",
            location="Faliraki",
            check_in=date(2026, 7, 1),
            check_out=date(2026, 7, 5),
            adults=2,
            children=0,
            rooms=1,
            limit=3,
            cache_hours=24,
        )

    assert str(exc_info.value) == BOOKING_SEARCH_NOT_CONFIGURED
    assert "APIFY_TOKEN" in BOOKING_SEARCH_NOT_CONFIGURED
