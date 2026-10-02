"""Pydantic schemas for the hybrid price-prediction agent (Phase E).

Two of these models are reused as Anthropic structured-output schemas
(``PriceRecommendation``), so they intentionally avoid numeric/string
constraints (``ge``/``le``/``min_length`` ...) — the SDK keeps the schema
simple with plain ``float``/``str``/``Literal`` fields. The request/response
envelope models below are normal FastAPI schemas and may use Field metadata.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Where the owner's reference price came from (Round 6 §5.2): the property's
# own live Booking row in the latest completed run, or the onboarding sample.
OwnPriceSource = Literal["booking_live", "onboarding_sample"]


class PricePosition(BaseModel):
    """How many hotels of the current market undercut the owner's reference price."""

    cheaper_than_you: int
    total: int


class PriceStatsScope(BaseModel):
    """Which hotels of the latest run back the statistics (Round 6 §5.2).

    ``same_category`` hotels offer the baseline category's comparable pool;
    ``similar`` hotels only offer other (non-single) categories. They are
    pulled in — one minimum per hotel — only when fewer than 5 same-category
    hotels exist, and ``used`` says whether that happened.

    ``cancellation_class`` (spec 2026-09-29 §4): "matched" when the per-hotel
    minimums were computed inside the own reference package's cancellation
    class and at least one competitor row of the latest run shares it — the
    page then renders «Σύγκριση σε τιμές ίδιας πολιτικής ακύρωσης». "all"
    when the own class is unknown or nothing matched (today's overall
    minimums).

    ``used == "agent"`` (owner decision 2026-09-30): the latest run's scrape
    job has room-matching agent rows for the owned room type, so the basis is
    exactly the agent-comparable rooms — ``comparable`` hotels, no widening.
    ``same_category`` then mirrors ``comparable`` so an older client still
    shows a truthful hotel count; ``comparable`` is 0 on the category path.
    """

    same_category: int
    similar: int
    used: Literal["same", "same_plus_similar", "agent"]
    cancellation_class: Literal["matched", "all"] = "all"
    comparable: int = 0


class PriceStatistics(BaseModel):
    """Deterministic statistical baseline computed from price history (no LLM).

    All monetary fields are EUR. Optional fields are ``None`` when the input
    history is too sparse to compute them; ``notes`` carries the data-quality
    caveats that explain any missing value.
    """

    sample_runs: int
    sample_days: int = 0
    own_reference_price_eur: float | None = None
    market_median_eur: float | None = None
    market_p25_eur: float | None = None
    market_p75_eur: float | None = None
    own_position_percentile: float | None = None
    position: PricePosition | None = None
    stats_scope: PriceStatsScope | None = None
    trend_7d_pct: float | None = None
    trend_30d_pct: float | None = None
    lead_time_days: int
    statistical_recommendation_eur: float | None = None
    notes: list[str] = Field(default_factory=list)


class PriceRecommendation(BaseModel):
    """A single nightly-price recommendation.

    Used both as the Anthropic structured-output schema (``output_format``) and
    as the value returned by the statistical fallback. Keep it free of numeric
    constraints so the SDK's structured-output validator accepts the schema.
    """

    recommended_price_eur: float
    price_range_low_eur: float
    price_range_high_eur: float
    confidence: Literal["low", "medium", "high"]
    reasoning: str
    key_factors: list[str]
    source: Literal["agent", "statistical"]


class PriceRecommendationRequest(BaseModel):
    """Request body for ``POST /api/v1/agents/price-recommendation``."""

    owned_property_id: UUID
    room_type_category: str | None = None
    # The owned room whose agent matches choose the comparison set; None =
    # the property's selected room type (today's reference room).
    owned_room_type_id: UUID | None = None
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_stay(self) -> "PriceRecommendationRequest":
        """Reject invalid or expired stay windows before querying history."""
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        # Η σύσταση τιμής αφορά ενεργή/μελλοντική εμπορική απόφαση.
        if self.check_in < date.today():
            raise ValueError("check_in cannot be in the past")
        return self


class PriceRecommendationResponse(BaseModel):
    """Response envelope: the statistical baseline plus the recommendation.

    ``recommendation`` is None — with ``recommendation_available`` False —
    when the market history and own reference price cannot support ANY
    grounded number. The statistics (and their ``notes``) still ship so the
    UI can explain what is missing instead of showing a fabricated €0.
    """

    model_config = ConfigDict(protected_namespaces=())

    statistics: PriceStatistics
    recommendation: PriceRecommendation | None = None
    recommendation_available: bool = True
    audit_id: UUID | None = None
    generated_at: datetime | None = None
    cached: bool = False
    model_version: str | None = None
    prompt_version: str | None = None
    own_price_source: OwnPriceSource | None = None


# ---------------------------------------------------------------------------
# Room-matching agent (spec 2026-09-29 Μέρος Α)
# ---------------------------------------------------------------------------


class RoomMatchCandidate(BaseModel):
    """One competitor room scored by the matching agent.

    Used as part of the Anthropic structured-output schema, so it stays free
    of numeric/string constraints (module docstring). ``c`` is the
    candidate's index in its chunk's input list — a few output tokens
    instead of echoing a UUID plus the room name. The service maps it back
    to the stored identity and drops unknown or repeated indices.
    """

    c: int
    score: int
    category_match: Literal["same", "similar"]
    reasoning: str
    # The owner's comparison set (decision 2026-09-30): the model's yes/no
    # verdict whether a guest looking for the reference room would genuinely
    # consider this room a substitute. Required, so every scored row carries
    # a verdict; the read side hides non-comparable rooms by default.
    comparable: bool

    @field_validator("score", mode="before")
    @classmethod
    def _round_fractional_score_half_up(cls, value: Any) -> Any:
        """Round a fractional score (87.5 -> 88) instead of failing the answer.

        One bad row must not sink a whole run: strict ``int`` validation
        would reject 87.5 and drop every score of the job. Rounding is
        half-up (Python's ``round`` is banker's: 88.5 -> 88); the 0-100
        clamp stays in the service. Validators do not alter the JSON schema,
        so the structured-output contract is still a plain integer.
        """
        if isinstance(value, bool) or not isinstance(value, (float, str)):
            return value
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            return value  # not a number at all: let int validation reject it
        if not number.is_finite():
            return value
        return int(number.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class RoomMatchProposal(BaseModel):
    """The matching agent's whole answer: one candidate per competitor room."""

    matches: list[RoomMatchCandidate]


class RoomMatchRunRequest(BaseModel):
    """Request body for ``POST /api/v1/agents/room-matches``."""

    scrape_job_id: UUID
    owned_room_type_id: UUID


class RoomMatchRunResponse(BaseModel):
    """Synchronous outcome of one manual matching run.

    ``skipped`` means the agent did not run: ``skip_reason`` "no_api_key"
    (no key configured) or "in_progress" (another run is still scoring this
    scope — its rows appear on a later read; a run that finishes within the
    wait comes back as ``completed`` with ITS rows). Quota exhaustion is a
    429, mirroring the pricing endpoint. ``source`` is the constant
    ``"agent"`` so the UI can label the refresh button's result without
    inspecting anything else.
    """

    status: Literal["completed", "error", "skipped"]
    matches_written: int = Field(default=0, ge=0)
    skip_reason: str | None = None
    source: Literal["agent"] = "agent"


class PriceHistoryPoint(BaseModel):
    """One competitor's cheapest price within a single scrape run."""

    run_index: int
    observed_at: str | None = None
    property_id: UUID | None = None
    hotel_name: str
    min_price_eur: float | None = None


class PriceHistorySeries(BaseModel):
    """Thin per-market price-history series for a frontend sparkline."""

    canonical_destination: str | None = None
    check_in: date
    check_out: date
    points: list[PriceHistoryPoint]
