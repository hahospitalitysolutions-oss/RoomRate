from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime
from typing import Any, Protocol

from api.config import settings
from api.schemas.onboarding import (
    AutomaticSetupRequest,
    AutomaticSetupResponse,
    OwnedPropertyOnboardingCreate,
    OwnedPropertyOnboardingResponse,
    OwnedPropertyRoomType,
    PropertyCandidate,
    SelectedRoomTypeResponse,
)
from api.schemas.scrape_jobs import JOB_TYPE_ROOM_DISCOVERY, ScrapeJobCreate
from api.services.room_rates_normalizer import compact_matching_key, matching_tokens
from api.services.scrape_job_service import ScrapeJobService

logger = logging.getLogger(__name__)


def _candidate_match_score(property_name: str, candidate: PropertyCandidate) -> float:
    target = compact_matching_key(property_name)
    candidate_name = compact_matching_key(candidate.display_name)
    if not target or not candidate_name:
        return 0.0
    if candidate_name == target:
        return 100.0
    if target in candidate_name or candidate_name in target:
        return 85.0
    target_tokens = matching_tokens(property_name)
    candidate_tokens = matching_tokens(candidate.display_name)
    if not target_tokens or not candidate_tokens:
        return 0.0
    overlap = len(target_tokens & candidate_tokens) / len(target_tokens | candidate_tokens)
    review_bonus = min(float(candidate.review_score or 0), 10.0) / 10.0
    return overlap * 70.0 + review_bonus


# An exact name, or one name containing the other (_candidate_match_score).
STRONG_NAME_MATCH = 85.0


def _candidate_url_key(candidate: PropertyCandidate) -> str:
    """One Booking listing whatever its query string: the URL without ?…/#…."""
    return candidate.booking_url.split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()


# 422 detail when this API has no Apify token; the frontend shows it in Greek.
BOOKING_SEARCH_NOT_CONFIGURED = (
    "Booking search is not configured on this API: set APIFY_TOKEN in .env and restart the API."
)

class OnboardingRepositoryProtocol(Protocol):
    def create_owned_property(self, account_id: uuid.UUID, request: OwnedPropertyOnboardingCreate) -> dict:
        """Persist a selected owned property."""

    def list_room_types(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[dict]:
        """Return room types discovered for an owned property."""

    def set_selected_room_type(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        room_type_category: str,
    ) -> bool:
        """Persist the user's selected baseline room category."""

    def replace_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> dict | None:
        """Point an existing owned property at a different Booking property."""

    def delete_owned_property(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID
    ) -> bool:
        """Delete one owned property; False when it was not found."""


class PropertyCandidateProviderProtocol(Protocol):
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
        """Return Booking property candidates matching a name and location."""


class BookingPropertyCandidateProvider:
    """Uses the existing Booking scraper scout stage for onboarding candidates."""

    def __init__(self, engine: Any = None):
        """Build a provider bound to an optional scout-cache engine.

        Args:
            engine: SQLAlchemy engine backing the scout cache. Defaults to the
                app's shared ``get_engine(role="api")`` engine, resolved lazily
                at each search (so a transient failure heals on the next one)
                and never at import or construction, which would make this
                provider require DATABASE_URL just to exist.
        """
        self._engine = engine

    def _resolve_engine(self) -> Any:
        """Return the engine the scout cache should use, or None to run uncached.

        Resolution is deliberate about two things. It reuses the application's
        existing cached, role-based engine (``api``: short queries under a
        server-side statement timeout) instead of opening a second pool. And it
        never lets an unavailable database break candidate search: the scout
        cache is an optimization, so a missing DATABASE_URL degrades to the
        previous uncached live scout rather than raising a 500 at the wizard.
        """
        if self._engine is not None:
            return self._engine
        # Imported here (not at module import) so the service stays importable
        # without database configuration; get_engine caches per role itself.
        from api.db import get_engine

        try:
            return get_engine(role="api")
        except Exception as exc:
            # Never echo the exception text unless we wrote it: a malformed but
            # non-empty DATABASE_URL makes SQLAlchemy raise ArgumentError with
            # the raw URL -- password included -- inside the message. Only
            # api/db.py's RuntimeError carries our own safe wording.
            detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            logger.warning(
                "Scout cache disabled for candidate search (engine unavailable): %s", detail
            )
            return None

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
        """Search Booking properties through the scraper scout actor.

        ``ActorRunError`` (Apify failed on every retry — an outage, not an
        empty market) becomes a clear ValueError so the onboarding routes
        return 422 with an actionable message instead of a raw 500.
        """
        from scraper import ActorRunError, build_client, fetch_hotel_list

        try:
            client = build_client()
        except OSError as exc:
            # No APIFY_TOKEN in this API's .env: a setup gap, not a crash.
            logger.warning("Booking candidate search is not configured: %s", exc)
            raise ValueError(BOOKING_SEARCH_NOT_CONFIGURED) from exc
        scout_limit = min(max(limit, 12), 25)
        # Resolved once so the location search and its name search share one
        # engine (and therefore one cache read/write path).
        engine = self._resolve_engine()

        def scout(destination: str) -> list[dict]:
            return fetch_hotel_list(
                client,
                self._search_config(
                    destination=destination,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    children=children,
                    rooms=rooms,
                    limit=scout_limit,
                    cache_hours=cache_hours,
                ),
                engine=engine,
            )

        try:
            hotels = scout(location.strip())
            candidates = [self._candidate_from_hotel(hotel, fallback_city=location) for hotel in hotels]
            # The location scout returns Booking's top hotels of the area,
            # cached for a day. A property outside them never appeared, and
            # «Αναζήτηση ξανά» returned the same list however the name was
            # corrected. Without a clear name match, search by name as well.
            if not any(_candidate_match_score(property_name, c) >= STRONG_NAME_MATCH for c in candidates):
                logger.info(
                    "Booking location scout has no clear name match; searching by property name: "
                    "property=%s location=%s",
                    property_name,
                    location,
                )
                named = [
                    self._candidate_from_hotel(hotel, fallback_city=location)
                    for hotel in scout(f"{property_name.strip()}, {location.strip()}")
                ]
                seen_urls = {_candidate_url_key(c) for c in named}
                candidates = named + [c for c in candidates if _candidate_url_key(c) not in seen_urls]
        except ActorRunError as exc:
            logger.warning("Booking candidate search failed (actor error): %s", exc)
            raise ValueError(
                "Booking search is temporarily unavailable; please try again shortly"
            ) from exc
        return candidates

    @staticmethod
    def _search_config(
        destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        limit: int,
        cache_hours: int,
    ):
        from scraper import ScraperConfig

        return ScraperConfig(
            destination=destination,
            check_in=datetime.combine(check_in, datetime.min.time()),
            check_out=datetime.combine(check_out, datetime.min.time()),
            adults=adults,
            children=children,
            rooms=rooms,
            scout_max_items=limit,
            # dry_run only means "do not persist rates from this search"; the
            # scout cache is separate and now active (search() threads the app
            # engine into fetch_hotel_list), unlike scraper/cli.py where
            # dry_run also implies engine=None.
            dry_run=True,
            # Verified truth, not a hope (scraper/scout.py): the scout cache
            # is keyed on an EXACT match of (destination, check_in, check_out,
            # adults, children, rooms, currency, language) -- not on `limit`,
            # and migration 20260711_0015 added no account column, so the rows
            # are shared. automatic_setup() and the wizard's «Δείξε άλλα» both
            # reach this same _search_config with the account's same stay, so
            # the first search warms the second even though their limits
            # differ. Onboarding rows land in the same scout_cache table the
            # scrape-job worker uses, which is intended reuse: identical stay
            # => identical Booking result set. The consequence to know is that
            # whichever search runs first fixes how many hotels the other one
            # sees from cache, since `limit` is deliberately not in the key.
            scout_cache_hours=cache_hours,
        )

    @staticmethod
    def _candidate_from_hotel(hotel: dict, fallback_city: str) -> PropertyCandidate:
        material = f"{hotel.get('name', '')}|{hotel.get('url', '')}"
        return PropertyCandidate(
            candidate_key=hashlib.sha256(material.encode("utf-8")).hexdigest()[:16],
            display_name=str(hotel.get("name") or "Unknown property"),
            booking_url=str(hotel.get("url") or ""),
            city=str(hotel.get("city") or fallback_city),
            address=str(hotel.get("address") or ""),
            property_type=hotel.get("property_type"),
            latitude=hotel.get("latitude") or None,
            longitude=hotel.get("longitude") or None,
            stars=hotel.get("stars") or None,
            review_score=hotel.get("review_score") or None,
            review_count=hotel.get("review_count") or None,
        )


class OnboardingService:
    """Application service for property onboarding and room discovery."""

    def __init__(
        self,
        repository: OnboardingRepositoryProtocol,
        scrape_job_service: ScrapeJobService,
        candidate_provider: PropertyCandidateProviderProtocol | None = None,
        property_candidates_cache_hours: int | None = None,
    ):
        self.repository = repository
        self.scrape_job_service = scrape_job_service
        self.candidate_provider = candidate_provider or BookingPropertyCandidateProvider()
        # Matches BookingScrapeJobRunner's settings-fallback pattern: an
        # explicit override (tests seed a non-default value) wins, otherwise
        # read PROPERTY_CANDIDATES_CACHE_HOURS from settings.
        self.property_candidates_cache_hours = (
            property_candidates_cache_hours
            if property_candidates_cache_hours is not None
            else settings.property_candidates_cache_hours
        )

    def search_property_candidates(
        self,
        property_name: str,
        location: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        limit: int,
    ) -> list[PropertyCandidate]:
        """Return candidate Booking properties for the onboarding picker.

        Ordered best name match first; ``candidates[0]`` is the best match and
        ties keep the provider's own relevance order. ``automatic_setup`` relies
        on this, so callers do not reimplement the scoring.
        """
        if check_out <= check_in:
            raise ValueError("check_out must be after check_in")
        if not property_name.strip() or not location.strip():
            raise ValueError("property_name and location are required")
        candidates = self.candidate_provider.search(
            property_name=property_name,
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            limit=limit,
            cache_hours=self.property_candidates_cache_hours,
        )
        # Sorted here so there is one scoring implementation, not one per consumer.
        return sorted(
            candidates,
            key=lambda candidate: _candidate_match_score(property_name, candidate),
            reverse=True,
        )

    def automatic_setup(
        self,
        account_id: uuid.UUID,
        request: AutomaticSetupRequest,
    ) -> AutomaticSetupResponse:
        """Auto-pick the best Booking candidate and queue owned-property room discovery."""
        candidates = self.search_property_candidates(
            property_name=request.property_name,
            location=request.location,
            check_in=request.check_in,
            check_out=request.check_out,
            adults=request.adults,
            children=request.children,
            rooms=request.rooms,
            limit=request.limit,
        )
        if not candidates:
            raise ValueError("No Booking candidates found for this property and location")

        # search_property_candidates already ordered by match score.
        selected_candidate = candidates[0]
        response = self.create_owned_property_and_discovery_job(
            account_id,
            OwnedPropertyOnboardingCreate(
                display_name=selected_candidate.display_name,
                booking_url=selected_candidate.booking_url,
                city=selected_candidate.city or request.location,
                raw_destination=request.location,
                address=selected_candidate.address or None,
                # No country here on purpose: _candidate_from_hotel never
                # extracts one, so reading candidate.country only ever wrote
                # None while reading as if a real value could arrive. The
                # column stays unset until something actually extracts it.
                property_type=selected_candidate.property_type,
                latitude=selected_candidate.latitude,
                longitude=selected_candidate.longitude,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                rooms=request.rooms,
            ),
        )
        return AutomaticSetupResponse(
            owned_property_id=response.owned_property_id,
            selected_candidate=selected_candidate,
            discovery_job=response.discovery_job,
        )

    def create_owned_property_and_discovery_job(
        self,
        account_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> OwnedPropertyOnboardingResponse:
        """Create the owned property and queue its automatic room discovery scrape."""
        row = self.repository.create_owned_property(account_id, request)
        return self._queue_room_discovery(account_id, row["id"], request)

    def replace_owned_property_and_discovery_job(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> OwnedPropertyOnboardingResponse | None:
        """Swap the owned property and rediscover the new hotel's rooms.

        Returns None when the property does not exist or is inactive for this
        account, so the router can 404 without revealing whether it exists.
        """
        row = self.repository.replace_owned_property(account_id, owned_property_id, request)
        if row is None:
            return None
        return self._queue_room_discovery(account_id, owned_property_id, request)

    def _queue_room_discovery(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> OwnedPropertyOnboardingResponse:
        """Queue the room-discovery scrape for one owned property.

        Shared by create and replace so the discovery job cannot drift between
        the two paths: same Booking page target, same crawl bounds, same stay.
        """
        job = self.scrape_job_service.create_job(
            account_id,
            ScrapeJobCreate(
                owned_property_id=owned_property_id,
                job_type=JOB_TYPE_ROOM_DISCOVERY,
                # raw_destination is always backfilled by the schema validator;
                # `or city` is belt-and-braces and keeps the type plainly str.
                destination=request.raw_destination or request.city,
                raw_destination=request.raw_destination,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                rooms=request.rooms,
                filters_payload={
                    "target_urls": [str(request.booking_url)],
                    "limit": 1,
                    "deep_crawl_max_items": 30,
                },
            ),
        )
        return OwnedPropertyOnboardingResponse(owned_property_id=owned_property_id, discovery_job=job)

    def delete_owned_property(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> bool:
        """Delete one owned property; False when it was not found."""
        return self.repository.delete_owned_property(account_id, owned_property_id)

    def list_room_types(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> list[OwnedPropertyRoomType]:
        """Return discovered room types for the current account/property."""
        return [
            OwnedPropertyRoomType.model_validate(row)
            for row in self.repository.list_room_types(account_id, owned_property_id)
        ]

    def select_owned_property_room_type(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        room_type_category: str,
    ) -> SelectedRoomTypeResponse:
        """Persist the baseline room category used for targeted competitor matching.

        ``room_type_category`` must already be the normalized slug key
        (``SelectedRoomTypeRequest`` normalizes it); only emptiness is re-checked.
        """
        if not room_type_category:
            raise ValueError("room_type_category is required")
        saved = self.repository.set_selected_room_type(
            account_id=account_id,
            owned_property_id=owned_property_id,
            room_type_category=room_type_category,
        )
        if not saved:
            raise ValueError("room_type_category was not discovered for this owned property")
        return SelectedRoomTypeResponse(
            owned_property_id=owned_property_id,
            selected_room_type_category=room_type_category,
            onboarding_complete=True,
        )
