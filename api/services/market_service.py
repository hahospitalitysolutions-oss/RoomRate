from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import lru_cache
from statistics import mean, median
from typing import Protocol
from uuid import UUID

from api.services.market_helpers import (
    as_coord,
    as_float,
    as_int,
    as_optional_float,
    category_match_for,
    distance_km_from,
    effective_price_per_night,
    exclude_hotel,
    group_by_hotel,
    hotel_group_key,
)
from api.services.room_matching import (
    RoomAttributes,
    attributes_from_dict,
    compute_match_score,
    extract_room_attributes,
)
from api.services.room_rates_normalizer import normalize_room_type_category
from api.schemas.market import (
    AdvisorCompetitor,
    AgentMeta,
    AgentDataQuality,
    Competitor,
    CompetitorMapMarker,
    CompetitorPackage,
    MarketSummary,
    OwnedRoomReference,
    RatePlan,
    SmartAdvisorSignals,
    SmartAdvisorContext,
)


# Cached label-only attribute extraction: identical room labels repeat across
# candidate rows, and RoomAttributes is a frozen dataclass so sharing one
# instance is safe. The payload-dict variant of extract_room_attributes stays
# uncached (dicts are unhashable and payloads rarely repeat).
@lru_cache(maxsize=2048)
def _extract_attributes_from_name(name: str) -> RoomAttributes:
    return extract_room_attributes(name)


# Rows written before migration 20260930_0027 carry no verdict (NULL): for
# them «comparable» means the agent scored the room at least this high.
LEGACY_COMPARABLE_MIN_SCORE = 50.0


@dataclass(frozen=True)
class AgentMatchOverride:
    """One agent-scored room: the values that replace the statistical score.

    ``comparable`` is the EFFECTIVE verdict: the stored ``comparable`` when
    not NULL, else ``score >= 50`` (older rows).
    """

    score: float
    reasoning: str | None
    comparable: bool = True


def build_agent_match_lookup(rows: list[dict]) -> dict[tuple[str, str], AgentMatchOverride]:
    """Index ``roomrate_room_matches`` rows for the package-scoring override.

    Keyed on ``(property_id, casefolded room name)`` — the same identity the
    matching agent validated against the job's packages — so the read side
    joins on property_id + room_type exactly as stored, tolerant only of
    case/whitespace drift. Junk rows (missing ids/names, unparseable scores)
    are skipped: a broken row must degrade to the statistical score, never
    break the competitors read.
    """
    lookup: dict[tuple[str, str], AgentMatchOverride] = {}
    for row in rows:
        property_id = row.get("property_id")
        room_type = str(row.get("room_type") or "").strip()
        score = as_optional_float(row.get("score"))
        if not property_id or not room_type or score is None:
            continue
        reasoning = str(row.get("reasoning")).strip() if row.get("reasoning") else None
        stored_verdict = row.get("comparable")
        comparable = (
            bool(stored_verdict)
            if stored_verdict is not None
            else score >= LEGACY_COMPARABLE_MIN_SCORE
        )
        lookup.setdefault(
            (str(property_id), room_type.casefold()),
            AgentMatchOverride(
                score=round(score, 1), reasoning=reasoning or None, comparable=comparable
            ),
        )
    return lookup


@dataclass(frozen=True)
class RoomRateFilters:
    destination: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = None
    children: int | None = None
    rooms: int | None = None
    owned_property_id: UUID | str | None = None
    room_type_category: str | None = None
    scrape_job_id: UUID | str | None = None
    # The owned room the comparison set is judged against (agent rows are
    # keyed on it); None = the property's selected room, resolved by the
    # read routes. Storage reads ignore it.
    owned_room_type_id: UUID | str | None = None
    amenities: tuple[str, ...] = ()
    selected_competitors_only: bool = False
    limit: int = 500
    # Round 6 (§3.6): True reads every stored category except single rooms
    # (other categories are labelled «Παρόμοιο»); False keeps the comparable
    # pool of room_type_category, the pre-Round-6 read. False by default so
    # internal callers (the price advisor) stay on comparable rooms; the HTTP
    # routes default to true in api/routers/_filters.build_filters.
    include_similar: bool = False

    @property
    def comparable_only(self) -> bool:
        """Hide non-comparable packages (owner decision 2026-09-30).

        The inverse of ``include_similar`` — one source of truth, so the SQL
        fallback pool and the agent-verdict filter can never disagree.
        """
        return not self.include_similar

    def __post_init__(self) -> None:
        normalized_amenities = tuple(
            amenity.strip().lower()
            for amenity in self.amenities
            if amenity and amenity.strip()
        )
        object.__setattr__(self, "amenities", normalized_amenities)
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if self.adults is not None and self.adults < 1:
            raise ValueError("adults must be at least 1")
        if self.children is not None and self.children < 0:
            raise ValueError("children cannot be negative")
        if self.rooms is not None and self.rooms < 1:
            raise ValueError("rooms must be at least 1")
        if self.room_type_category is not None and not self.room_type_category.strip():
            raise ValueError("room_type_category cannot be blank")
        if self.selected_competitors_only and not self.owned_property_id:
            raise ValueError("owned_property_id is required when selected_competitors_only=true")
        if self.limit < 1 or self.limit > 5_000:
            raise ValueError("limit must be between 1 and 5000")


class RoomRatesRepositoryProtocol(Protocol):
    def fetch_room_rates(self, filters: RoomRateFilters) -> list[dict]:
        """Fetch room rate rows from storage."""

    def fetch_available_amenities(self, filters: RoomRateFilters) -> list[str]:
        """Fetch amenity filter options from storage."""

    def fetch_own_property_rates(
        self, account_id: UUID | str, scrape_job_id: UUID | str | None, display_name: str | None
    ) -> list[dict]:
        """Fetch the owner's own rows in one scrape job, cheapest first."""


def _row_price(row: dict) -> float:
    """The package's EFFECTIVE price (discounted when shown, else base).

    Every read-side figure goes through it — marker, competitor min/max,
    summary, advisor market stats — so the map, the list and the
    recommendation speak one price. Package payloads keep the base
    ``price_per_night_eur``; the discount rides in ``rate_plan``.
    """
    return as_float(effective_price_per_night(row))


def _agent_match_key(row: dict) -> tuple[str, str]:
    """A rate row's identity in the agent-match lookup (property + room name)."""
    return (
        str(row.get("property_id") or ""),
        str(row.get("room_type") or "").strip().casefold(),
    )


# The six rate_plan fields of spec 2026-09-29 §4 (rate_block_id stays a
# storage-only identity and is not exposed on the API).
_RATE_PLAN_FIELDS = (
    "discounted_price_per_night_eur",
    "discount_pct",
    "discount_label",
    "has_genius_discount",
    "cancellation_type",
    "payment_label",
)


def _text_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _build_rate_plan(row: dict) -> RatePlan | None:
    """The package's rate plan, or None when every plan column is NULL.

    Rows written before migration 20260929_0025 (and legacy-source rows) are
    NULL everywhere, so the API says «χωρίς στοιχεία πλάνου» instead of
    fabricating a plan with default values.
    """
    if all(row.get(field) is None for field in _RATE_PLAN_FIELDS):
        return None
    return RatePlan(
        discounted_price_per_night_eur=as_optional_float(row.get("discounted_price_per_night_eur")),
        discount_pct=as_optional_float(row.get("discount_pct")),
        discount_label=_text_or_none(row.get("discount_label")),
        has_genius_discount=bool(row.get("has_genius_discount")),
        cancellation_type=_text_or_none(row.get("cancellation_type")),
        payment_label=_text_or_none(row.get("payment_label")),
    )


def is_comparable(
    row: dict,
    baseline_category: str | None,
    agent_matches: dict[tuple[str, str], AgentMatchOverride] | None = None,
) -> bool:
    """Whether one package belongs to the owner's comparison set.

    Owner decision 2026-09-30: the matching agent's verdict decides whenever
    it judged this room; the statistical category pool is only the fallback —
    for a read without agent rows, or for a room the agent left unscored (a
    failed chunk keeps its rooms on the statistical logic, spec Α.5).
    """
    override = agent_matches.get(_agent_match_key(row)) if agent_matches else None
    if override is not None:
        return override.comparable
    return category_match_for(row.get("room_type_category"), baseline_category) == "same"


def _pick_marker_row(hotel_rows: list[dict], comparable: Callable[[dict], bool]) -> dict:
    """The package that represents a hotel on the map and in the summary.

    The cheapest COMPARABLE package when the hotel has one, otherwise its
    cheapest package, so a cheap suite never stands in for a hotel that also
    sells a substitute for the owner's room. With the category fallback this
    is exactly Round 6's «cheapest same-category, else cheapest similar».
    """
    comparable_rows = [row for row in hotel_rows if comparable(row)]
    return min(comparable_rows or hotel_rows, key=_row_price)


class MarketService:
    """Read-only market intelligence service for RoomRate dashboards and agents."""

    def __init__(self, repository: RoomRatesRepositoryProtocol):
        self.repository = repository

    def get_competitors(
        self,
        filters: RoomRateFilters,
        owned_room: OwnedRoomReference | None = None,
        min_match_score: float | None = None,
        sort_by_match: bool = False,
        origin: tuple[float, float] | None = None,
        sort_by_distance: bool = False,
        agent_matches: dict[tuple[str, str], AgentMatchOverride] | None = None,
    ) -> list[Competitor]:
        """Return competitors grouped by hotel, optionally scored against one owned room.

        Args:
            filters: Query filters for destination, dates and result limit.
            owned_room: The user's own room; when given, every package gets a
                0-100 match_score and every competitor a best_match_score.
            min_match_score: Drop packages scoring below this threshold (and
                competitors left with no packages). Only applies with owned_room.
            sort_by_match: Sort competitors by best_match_score descending,
                ties by price ascending, instead of price only.
            origin: The owner's (latitude, longitude); fills distance_km.
            sort_by_distance: Nearest first, unknown distances last, ties by
                price. Without an origin this is plain price order.
            agent_matches: AI room-match overrides (spec 2026-09-29 Α.4),
                keyed by ``build_agent_match_lookup``. A package found in the
                lookup takes the agent's score and reasoning; every other
                package keeps its statistical score with a null reasoning.
                Each competitor reports ``match_source="agent"`` when at
                least one of its packages was agent-scored — never a false
                source for a competitor the agent did not match.

                The same lookup carries the agent's ``comparable`` verdicts,
                which choose the comparison set (``_comparison_rows``) and
                label every package, with or without ``owned_room``.

        Returns:
            Competitor records grouped by hotel with room packages, each
            labelled same/similar against ``filters.room_type_category`` and
            comparable or not.

        Raises:
            ValueError: If filters are invalid.
        """
        rows = self._comparison_rows(filters, agent_matches)
        return self._build_competitors(
            rows,
            owned_room=owned_room,
            min_match_score=min_match_score,
            sort_by_match=sort_by_match,
            baseline_category=filters.room_type_category,
            origin=origin,
            sort_by_distance=sort_by_distance,
            agent_matches=agent_matches,
        )

    def get_market_summary(
        self,
        filters: RoomRateFilters,
        agent_matches: dict[tuple[str, str], AgentMatchOverride] | None = None,
    ) -> MarketSummary:
        """Calculate market summary metrics for the selected competitor set.

        Args:
            filters: Query filters for destination, dates and result limit.
            agent_matches: The matching agent's lookup for the read's (job,
                owned room); it chooses the comparison set exactly as for
                the competitor list and the map.

        Returns:
            Aggregated market metrics for dashboard KPI cards.

        Raises:
            ValueError: If filters are invalid.
        """
        rows = self._comparison_rows(filters, agent_matches)
        comparable = self._comparable_predicate(filters, agent_matches)
        # Round 6: ONE price per hotel, the package its map marker shows
        # (cheapest comparable, else cheapest other), so the single summary
        # box agrees with the map and the list, and a hotel selling ten room
        # types does not weigh ten times. total_records and rooms_left_total
        # stay per row.
        prices: list[float] = []
        review_scores: list[float] = []
        same_category_hotels = 0
        for hotel_rows in group_by_hotel(rows).values():
            chosen = _pick_marker_row(hotel_rows, comparable)
            prices.append(_row_price(chosen))
            review_score = as_float(chosen.get("review_score"))
            if review_score > 0:
                review_scores.append(review_score)
            if category_match_for(chosen.get("room_type_category"), filters.room_type_category) == "same":
                same_category_hotels += 1

        if not prices:
            return MarketSummary(
                destination=filters.destination,
                check_in=filters.check_in,
                check_out=filters.check_out,
                total_records=0,
                total_hotels=0,
                price_min_eur=0.0,
                price_max_eur=0.0,
                price_avg_eur=0.0,
                price_median_eur=0.0,
                avg_review_score=0.0,
                rooms_left_total=0,
            )

        return MarketSummary(
            destination=filters.destination,
            check_in=filters.check_in,
            check_out=filters.check_out,
            total_records=len(rows),
            total_hotels=len(prices),
            price_min_eur=round(min(prices), 2),
            price_max_eur=round(max(prices), 2),
            price_avg_eur=round(mean(prices), 2),
            price_median_eur=round(median(prices), 2),
            avg_review_score=round(mean(review_scores), 2) if review_scores else 0.0,
            rooms_left_total=sum(as_int(row.get("rooms_left")) for row in rows),
            same_category_hotels=same_category_hotels,
            similar_hotels=len(prices) - same_category_hotels,
        )

    def get_available_amenities(self, filters: RoomRateFilters) -> list[str]:
        """Return dynamic amenity filter options for the current market scope."""
        return self.repository.fetch_available_amenities(filters)

    def get_competitor_map_markers(
        self,
        filters: RoomRateFilters,
        origin: tuple[float, float] | None = None,
        agent_matches: dict[tuple[str, str], AgentMatchOverride] | None = None,
    ) -> list[CompetitorMapMarker]:
        """Return one Mapbox-ready marker per competitor hotel.

        Args:
            filters: Query filters for destination, dates and result limit.
            origin: The owner's (latitude, longitude); fills distance_km.
            agent_matches: The matching agent's lookup; it chooses the
                comparison set exactly as for the list and the summary.

        Returns:
            Markers using the cheapest comparable package per hotel, else its
            cheapest other package (only with ``comparable_only=false``);
            hotels without coordinates are skipped (they stay in the list).

        Raises:
            ValueError: If filters are invalid.
        """
        rows = self._comparison_rows(filters, agent_matches)
        comparable = self._comparable_predicate(filters, agent_matches)
        markers: list[CompetitorMapMarker] = []
        for hotel_rows in group_by_hotel(rows).values():
            chosen = _pick_marker_row(hotel_rows, comparable)
            lat = as_coord(chosen.get("latitude"))
            lng = as_coord(chosen.get("longitude"))
            if lat == 0.0 or lng == 0.0:
                continue
            markers.append(
                CompetitorMapMarker(
                    hotel_name=str(chosen.get("hotel_name") or ""),
                    property_id=chosen.get("property_id"),
                    room_package_id=chosen.get("room_package_id"),
                    room_type=str(chosen.get("room_type") or ""),
                    room_type_category=str(chosen.get("room_type_category") or ""),
                    property_type=str(chosen.get("property_type") or ""),
                    latitude=lat,
                    longitude=lng,
                    price_per_night_eur=_row_price(chosen),
                    review_score=as_float(chosen.get("review_score")),
                    review_count=as_int(chosen.get("review_count")),
                    rooms_left=as_int(chosen.get("rooms_left")),
                    distance_km=distance_km_from(origin, lat, lng),
                    category_match=category_match_for(
                        chosen.get("room_type_category"), filters.room_type_category
                    ),
                    comparable=comparable(chosen),
                    booking_url=chosen.get("booking_url") or None,
                )
            )
        return sorted(markers, key=lambda marker: marker.price_per_night_eur)

    def get_own_property_cheapest_rate(
        self,
        account_id: UUID | str,
        scrape_job_id: UUID | str | None,
        display_name: str | None,
        room_type_category: str | None,
    ) -> dict | None:
        """The owner's own package for the «Εσείς» marker (Round 6 §3.6).

        Cheapest own package in the comparable pool of ``room_type_category``
        (the job's category, falling back to the property's selected one),
        else the cheapest own package of any category; None without a job
        or when Booking did not return the property in it.
        """
        if not scrape_job_id:
            return None
        rows = self.repository.fetch_own_property_rates(account_id, scrape_job_id, display_name)
        if not rows:
            return None
        return _pick_marker_row(rows, lambda row: is_comparable(row, room_type_category))

    def _comparison_rows(
        self,
        filters: RoomRateFilters,
        agent_matches: dict[tuple[str, str], AgentMatchOverride] | None,
    ) -> list[dict]:
        """The rate rows of the comparison set the list, map and summary share.

        With agent rows the pool is the broad one the agent scored (every
        category but singles, plus the owner's pool) and, under
        ``comparable_only``, the agent's verdicts narrow it — a hotel left
        with no comparable package disappears everywhere. Without agent rows
        the SQL pool IS the fallback set, exactly Round 6's include_similar.
        Amenity filters stay hard SQL filters on top in both paths.
        """
        if not agent_matches:
            return self.repository.fetch_room_rates(filters)
        rows = self.repository.fetch_room_rates(replace(filters, include_similar=True))
        if not filters.comparable_only:
            return rows
        return [row for row in rows if is_comparable(row, filters.room_type_category, agent_matches)]

    @staticmethod
    def _comparable_predicate(
        filters: RoomRateFilters,
        agent_matches: dict[tuple[str, str], AgentMatchOverride] | None,
    ) -> Callable[[dict], bool]:
        """The per-package comparable verdict for one read (see ``is_comparable``)."""
        return lambda row: is_comparable(row, filters.room_type_category, agent_matches)

    def get_smart_advisor_context(
        self,
        filters: RoomRateFilters,
        my_hotel_name: str | None = None,
        origin: tuple[float, float] | None = None,
        comparable_rooms: set[tuple[str, str]] | None = None,
    ) -> SmartAdvisorContext:
        """Build pricing-agent context from current room rates.

        Args:
            filters: Query filters for destination, dates and result limit.
            my_hotel_name: Optional hotel name to exclude from competitors.
            origin: The owner's (latitude, longitude); fills each competitor's
                ``distance_km`` for the agent payload (spec 2026-09-29 Β.2).
            comparable_rooms: The agent-comparable (property_id, casefolded
                room_type) pairs when the room-matching agent chose the basis
                (owner decision 2026-09-30); only those packages reach the
                model, so it reads the statistics' own comparison set.

        Returns:
            Structured JSON payload for the price-recommendation agent. Each
            competitor carries ``tracked``: whether it is one of the owner's
            watched competitors for ``filters.owned_property_id`` (always
            False without an owned property).

        Raises:
            ValueError: If filters are invalid.
        """
        rows = exclude_hotel(self.repository.fetch_room_rates(filters), my_hotel_name)
        if comparable_rooms:
            rows = [row for row in rows if _agent_match_key(row) in comparable_rooms]
        tracked_keys = self._tracked_hotel_keys(filters, rows)
        # Β.2: the agent reads honest labels — distance measured from the
        # owner and category_match judged against the requested baseline
        # category (without the baseline everything reads "same"). Tracked
        # and untracked hotels are built apart (whole hotel groups, so no
        # competitor is split) because Competitor carries no property id.
        competitors: list[AdvisorCompetitor] = []
        for tracked in (True, False):
            partition = [row for row in rows if (hotel_group_key(row) in tracked_keys) == tracked]
            competitors.extend(
                AdvisorCompetitor(**competitor.model_dump(), tracked=tracked)
                for competitor in self._build_competitors(
                    partition, baseline_category=filters.room_type_category, origin=origin
                )
            )
        # Same price order the single build produced; the agent re-orders.
        competitors.sort(key=lambda competitor: competitor.price_min_eur)
        return SmartAdvisorContext(
            meta=self._build_agent_meta(filters, rows, total_competitors=len(competitors)),
            data_quality=self._build_data_quality(rows, total_competitors=len(competitors)),
            pricing_signals=self._build_pricing_signals(competitors),
            time_context=self._build_time_context(rows),
            competitors=competitors,
        )

    def _tracked_hotel_keys(self, filters: RoomRateFilters, rows: list[dict]) -> set[str]:
        """Hotel identities of the owner's tracked competitors in this scope.

        Reuses the repository's own tracked-competitor predicate (same market
        key, pool and occupancy) rather than re-deriving it, so «tracked» here
        means exactly the set the tracked-only read returns. A tracked-only
        read is tracked through and through; no owned property, no tracking.
        """
        if not filters.owned_property_id:
            return set()
        tracked_rows = (
            rows
            if filters.selected_competitors_only
            else self.repository.fetch_room_rates(replace(filters, selected_competitors_only=True))
        )
        return {key for key in map(hotel_group_key, tracked_rows) if key is not None}

    def _build_competitors(
        self,
        rows: list[dict],
        owned_room: OwnedRoomReference | None = None,
        min_match_score: float | None = None,
        sort_by_match: bool = False,
        baseline_category: str | None = None,
        origin: tuple[float, float] | None = None,
        sort_by_distance: bool = False,
        agent_matches: dict[tuple[str, str], AgentMatchOverride] | None = None,
    ) -> list[Competitor]:
        owned_attrs: RoomAttributes | None = None
        owned_category: str | None = None
        if owned_room is not None:
            # Prefer pre-computed attributes; fall back to label extraction.
            owned_attrs = (
                attributes_from_dict(owned_room.room_attributes)
                if owned_room.room_attributes
                else _extract_attributes_from_name(owned_room.room_type)
            )
            owned_category = normalize_room_type_category(
                owned_room.room_type_category or owned_room.room_type
            )

        competitors: list[Competitor] = []
        for hotel_rows in group_by_hotel(rows).values():
            sorted_rows = sorted(hotel_rows, key=_row_price)
            first = sorted_rows[0]
            packages: list[CompetitorPackage] = []
            # Effective prices of the surviving packages: price_min/max_eur
            # match the marker, while each payload keeps the base price.
            effective_prices: list[float] = []
            agent_scored_packages = 0
            for row in sorted_rows:
                match_score: float | None = None
                match_reasoning: str | None = None
                row_from_agent = False
                if owned_room is not None and owned_attrs is not None:
                    override = (
                        agent_matches.get(_agent_match_key(row)) if agent_matches else None
                    )
                    if override is not None:
                        # Agent rows win (spec Α.4); packages of a room the
                        # agent did not match keep the statistical score.
                        match_score = override.score
                        match_reasoning = override.reasoning
                        row_from_agent = True
                    else:
                        match_score = self._score_row(row, owned_room, owned_attrs, owned_category)
                    if min_match_score is not None and match_score < min_match_score:
                        continue
                if row_from_agent:
                    agent_scored_packages += 1
                row_category = str(row.get("room_type_category") or "").strip() or None
                row_comparable = is_comparable(row, baseline_category, agent_matches)
                packages.append(
                    CompetitorPackage(
                        room_type=str(row.get("room_type") or ""),
                        price_per_night_eur=as_float(row.get("price_per_night_eur")),
                        price_total_eur=as_float(row.get("price_total_eur")),
                        meals=str(row.get("meals") or ""),
                        free_cancellation=str(row.get("free_cancellation") or ""),
                        rooms_left=as_int(row.get("rooms_left")),
                        match_score=match_score,
                        match_reasoning=match_reasoning,
                        room_type_category=row_category,
                        category_match=category_match_for(row_category, baseline_category),
                        comparable=row_comparable,
                        rate_plan=_build_rate_plan(row),
                    )
                )
                effective_prices.append(_row_price(row))
            if not packages:
                # Every package fell below min_match_score: drop the competitor.
                continue
            scores = [package.match_score for package in packages if package.match_score is not None]
            # Κρατάμε το χαμηλότερο rooms_left ως απλό demand signal για τον ανταγωνιστή.
            rooms_left_values = [package.rooms_left for package in packages]
            competitors.append(
                Competitor(
                    hotel_name=str(first.get("hotel_name") or ""),
                    city=str(first.get("city") or ""),
                    address=str(first.get("address") or ""),
                    property_type=str(first.get("property_type") or ""),
                    latitude=as_coord(first.get("latitude")),
                    longitude=as_coord(first.get("longitude")),
                    stars=as_float(first.get("stars")),
                    review_score=as_float(first.get("review_score")),
                    review_count=as_int(first.get("review_count")),
                    price_min_eur=round(min(effective_prices), 2),
                    price_max_eur=round(max(effective_prices), 2),
                    rooms_left=min(rooms_left_values) if rooms_left_values else 0,
                    best_match_score=max(scores) if scores else None,
                    packages=packages,
                    distance_km=distance_km_from(origin, first.get("latitude"), first.get("longitude")),
                    category_match=(
                        "same" if any(package.category_match == "same" for package in packages) else "similar"
                    ),
                    # Honest per-competitor source (spec Α.4, «ποτέ ψευδής
                    # πηγή»): agent only when at least one surviving package
                    # of THIS competitor carries an agent score.
                    match_source="agent" if agent_scored_packages else "statistical",
                    booking_url=first.get("booking_url") or None,
                )
            )
        if sort_by_distance:
            return sorted(
                competitors,
                key=lambda competitor: (
                    competitor.distance_km is None,
                    competitor.distance_km or 0.0,
                    competitor.price_min_eur,
                ),
            )
        if sort_by_match:
            return sorted(
                competitors,
                key=lambda competitor: (-(competitor.best_match_score or 0.0), competitor.price_min_eur),
            )
        return sorted(competitors, key=lambda competitor: competitor.price_min_eur)

    @staticmethod
    def _score_row(
        row: dict,
        owned_room: OwnedRoomReference,
        owned_attrs: RoomAttributes,
        owned_category: str | None,
    ) -> float:
        """Score one candidate package row against the owned room (0-100)."""
        candidate_name = str(row.get("room_type") or "")
        stored_attributes = row.get("room_attributes")
        # Stored write-time attributes win; legacy/sparse rows fall back to
        # extracting from the room label on the fly.
        candidate_attrs = (
            attributes_from_dict(stored_attributes)
            if isinstance(stored_attributes, dict) and stored_attributes
            else _extract_attributes_from_name(candidate_name)
        )
        candidate_category = normalize_room_type_category(
            str(row.get("room_type_category") or "") or candidate_name
        )
        same_category = owned_category is not None and candidate_category == owned_category
        return round(
            compute_match_score(
                owned_room.room_type,
                owned_attrs,
                candidate_name,
                candidate_attrs,
                same_category,
            ),
            1,
        )

    def _build_agent_meta(self, filters: RoomRateFilters, rows: list[dict], total_competitors: int) -> AgentMeta:
        prices = [_row_price(row) for row in rows]
        first = rows[0] if rows else {}
        return AgentMeta(
            destination=filters.destination or str(first.get("city") or "") or None,
            check_in=str(filters.check_in or first.get("check_in") or "") or None,
            check_out=str(filters.check_out or first.get("check_out") or "") or None,
            nights=as_int(first.get("nights")),
            guests=as_int(first.get("guests")),
            adults=filters.adults or as_int(first.get("adults")),
            children=filters.children if filters.children is not None else as_int(first.get("children")),
            rooms=filters.rooms or as_int(first.get("rooms")),
            total_competitors=total_competitors,
            market_stats={
                "price_min_eur": round(min(prices), 2) if prices else 0.0,
                "price_max_eur": round(max(prices), 2) if prices else 0.0,
                "price_avg_eur": round(mean(prices), 2) if prices else 0.0,
                "price_median_eur": round(median(prices), 2) if prices else 0.0,
            },
        )

    def _build_data_quality(self, rows: list[dict], total_competitors: int) -> AgentDataQuality:
        total_rows = len(rows)
        rows_without_facilities = sum(1 for row in rows if not str(row.get("facilities") or "").strip())
        rows_without_coordinates = sum(
            1
            for row in rows
            if as_coord(row.get("latitude")) == 0.0 or as_coord(row.get("longitude")) == 0.0
        )
        rows_without_rooms_left = sum(1 for row in rows if row.get("rooms_left") in (None, ""))
        covered = total_rows - rows_without_facilities
        return AgentDataQuality(
            total_rows=total_rows,
            total_competitors=total_competitors,
            rows_without_facilities=rows_without_facilities,
            rows_without_coordinates=rows_without_coordinates,
            rows_without_rooms_left=rows_without_rooms_left,
            facility_coverage_pct=round((covered / total_rows) * 100, 2) if total_rows else 0.0,
        )

    def _build_pricing_signals(self, competitors: list[Competitor]) -> SmartAdvisorSignals:
        if not competitors:
            return SmartAdvisorSignals(
                cheapest_competitor=None,
                most_expensive_competitor=None,
                market_price_spread_eur=0.0,
                low_availability_competitors=[],
                free_cancellation_share_pct=0.0,
                breakfast_available_share_pct=0.0,
            )
        cheapest = min(competitors, key=lambda competitor: competitor.price_min_eur)
        most_expensive = max(competitors, key=lambda competitor: competitor.price_min_eur)
        packages = [package for competitor in competitors for package in competitor.packages]
        free_cancel = sum(1 for package in packages if self._is_yes(package.free_cancellation))
        breakfast = sum(1 for package in packages if self._mentions_breakfast(package.meals))
        return SmartAdvisorSignals(
            cheapest_competitor=cheapest.hotel_name,
            most_expensive_competitor=most_expensive.hotel_name,
            market_price_spread_eur=round(most_expensive.price_min_eur - cheapest.price_min_eur, 2),
            low_availability_competitors=[
                competitor.hotel_name for competitor in competitors if 0 < competitor.rooms_left <= 2
            ],
            free_cancellation_share_pct=round((free_cancel / len(packages)) * 100, 2) if packages else 0.0,
            breakfast_available_share_pct=round((breakfast / len(packages)) * 100, 2) if packages else 0.0,
        )

    @staticmethod
    def _is_yes(value: str) -> bool:
        normalized = value.strip().casefold()
        return normalized in {"yes", "true", "1", "ναι", "nai"}

    @staticmethod
    def _mentions_breakfast(value: str) -> bool:
        normalized = value.strip().casefold()
        return "breakfast" in normalized or "πρωιν" in normalized

    def _build_time_context(self, rows: list[dict]) -> dict:
        first = rows[0] if rows else {}
        check_in_raw = first.get("check_in")
        scraped_raw = str(first.get("scraped_at") or "")[:10]
        days_until_checkin = 0
        month = 0
        day_of_week = ""
        try:
            check_in = datetime.strptime(str(check_in_raw), "%Y-%m-%d").date()
            scraped = datetime.strptime(scraped_raw, "%Y-%m-%d").date()
            days_until_checkin = (check_in - scraped).days
            month = check_in.month
            day_of_week = check_in.strftime("%A")
        except (TypeError, ValueError):
            pass
        return {
            "days_until_checkin": days_until_checkin,
            "month": month,
            "day_of_week": day_of_week,
            "events": [],
        }
