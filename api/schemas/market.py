from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Round 6 (§3.6): "same" when a package is in the comparable pool of the
# requested category (or no category was requested), else "similar".
CategoryMatch = Literal["same", "similar"]


class MarketSummary(BaseModel):
    destination: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    total_records: int = Field(..., ge=0)
    total_hotels: int = Field(..., ge=0)
    price_min_eur: float = Field(..., ge=0)
    price_max_eur: float = Field(..., ge=0)
    price_avg_eur: float = Field(..., ge=0)
    price_median_eur: float = Field(..., ge=0)
    avg_review_score: float = Field(..., ge=0)
    rooms_left_total: int = Field(..., ge=0)
    # Round 6: prices above use ONE price per hotel (the marker's package);
    # total_hotels = same_category_hotels + similar_hotels.
    same_category_hotels: int = Field(default=0, ge=0)
    similar_hotels: int = Field(default=0, ge=0)


class OwnedRoomReference(BaseModel):
    """The user's own room used as the baseline for competitor match scoring."""

    room_type: str
    room_type_category: str | None = None
    room_attributes: dict | None = None


class RatePlan(BaseModel):
    """One package's rate-plan facts (spec 2026-09-29 §4).

    Serialized as ``CompetitorPackage.rate_plan`` and null when every stored
    plan column is NULL (rows written before migration 20260929_0025) — the
    UI then shows the package as «χωρίς στοιχεία πλάνου».
    """

    discounted_price_per_night_eur: float | None = Field(default=None, ge=0)
    discount_pct: float | None = Field(default=None, ge=0)
    discount_label: str | None = None
    has_genius_discount: bool = False
    cancellation_type: str | None = None
    payment_label: str | None = None


class CompetitorPackage(BaseModel):
    room_type: str
    price_per_night_eur: float = Field(..., ge=0)
    price_total_eur: float = Field(..., ge=0)
    meals: str
    free_cancellation: str
    rooms_left: int = Field(..., ge=0)
    match_score: float | None = Field(default=None, ge=0, le=100)
    # Agents (spec 2026-09-29 Α.4): the AI's one-line Greek justification of
    # the score. Null whenever the score is statistical — the UI renders the
    # line only when it exists, so a null can never fake an AI estimate.
    match_reasoning: str | None = None
    # Round 6: stored category (None when the room name was not recognised).
    room_type_category: str | None = None
    category_match: CategoryMatch = "same"
    # Owner decision 2026-09-30: whether this package is in the owner's
    # comparison set — the matching agent's verdict when it judged the room,
    # else the statistical fallback (true exactly for same-category rows).
    # Only ever false in a comparable_only=false read.
    comparable: bool = True
    # Rate plans: the package's plan facts, null for pre-migration rows.
    rate_plan: RatePlan | None = None


class Competitor(BaseModel):
    hotel_name: str
    city: str
    address: str
    property_type: str
    latitude: float
    longitude: float
    stars: float = Field(..., ge=0)
    review_score: float = Field(..., ge=0)
    review_count: int = Field(..., ge=0)
    price_min_eur: float = Field(..., ge=0)
    price_max_eur: float = Field(..., ge=0)
    rooms_left: int = Field(..., ge=0)
    best_match_score: float | None = Field(default=None, ge=0, le=100)
    packages: list[CompetitorPackage]
    # Round 6: km from the owner's property (None without coordinates),
    # "same" when any package is, and the Booking listing link.
    distance_km: float | None = Field(default=None, ge=0)
    category_match: CategoryMatch = "same"
    # Agents (spec 2026-09-29 Α.4): where THIS competitor's match scores came
    # from — "agent" when at least one of its packages carries an AI score,
    # otherwise "statistical". Mixed responses are legitimate when the agent
    # matched only some properties; the badge must never claim a false source.
    match_source: Literal["agent", "statistical"] = "statistical"
    booking_url: str | None = None


class CompetitorMapMarker(BaseModel):
    hotel_name: str
    property_id: UUID | None = None
    room_package_id: UUID | None = None
    room_type: str = ""
    room_type_category: str = ""
    property_type: str
    latitude: float
    longitude: float
    price_per_night_eur: float = Field(..., ge=0)
    review_score: float = Field(..., ge=0)
    review_count: int = Field(..., ge=0)
    rooms_left: int = Field(..., ge=0)
    # Round 6: the marker shows the cheapest same-category package, else the
    # cheapest similar one; category_match says which.
    distance_km: float | None = Field(default=None, ge=0)
    category_match: CategoryMatch = "same"
    # Whether the marker's package is comparable (see CompetitorPackage);
    # false only when the hotel has no comparable package at all, which a
    # comparable_only=true read never returns.
    comparable: bool = True
    booking_url: str | None = None


class OwnPropertyMapInfo(BaseModel):
    """The owner's own property for the map's «Εσείς» marker (Round 6 §3.6).

    Coordinates are null when the property has none (the map then draws no
    marker and says so); radius_km comes from the scrape job; the price and
    room are the owner's cheapest comparable package in that job, null when
    Booking did not return the property.
    """

    display_name: str
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    price_per_night_eur: float | None = Field(default=None, ge=0)
    room_type: str | None = None
    booking_url: str | None = None


class AgentMeta(BaseModel):
    destination: str | None = None
    check_in: str | None = None
    check_out: str | None = None
    nights: int = Field(default=0, ge=0)
    guests: int = Field(default=0, ge=0)
    adults: int = Field(default=0, ge=0)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=0, ge=0)
    total_competitors: int = Field(default=0, ge=0)
    market_stats: dict[str, float]


class AgentDataQuality(BaseModel):
    total_rows: int = Field(..., ge=0)
    total_competitors: int = Field(..., ge=0)
    rows_without_facilities: int = Field(..., ge=0)
    rows_without_coordinates: int = Field(..., ge=0)
    rows_without_rooms_left: int = Field(..., ge=0)
    facility_coverage_pct: float = Field(..., ge=0, le=100)


class SmartAdvisorSignals(BaseModel):
    cheapest_competitor: str | None = None
    most_expensive_competitor: str | None = None
    market_price_spread_eur: float = Field(..., ge=0)
    low_availability_competitors: list[str]
    free_cancellation_share_pct: float = Field(..., ge=0, le=100)
    breakfast_available_share_pct: float = Field(..., ge=0, le=100)


class AdvisorCompetitor(Competitor):
    """A competitor of the price advisor's comparison basis.

    ``tracked`` marks the owner's watched competitors inside the FULL basis
    the statistics are computed over, so the model can single them out
    without mistaking the watched subset for the whole market. Advisor-only:
    the public competitor endpoints keep the plain ``Competitor`` shape.
    """

    tracked: bool = False


class SmartAdvisorContext(BaseModel):
    meta: AgentMeta
    data_quality: AgentDataQuality
    pricing_signals: SmartAdvisorSignals
    time_context: dict[str, Any]
    competitors: list[AdvisorCompetitor]
