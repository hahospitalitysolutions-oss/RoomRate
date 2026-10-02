from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, computed_field, model_validator

from api.schemas.scrape_jobs import ScrapeJobResponse
from api.services.destination_aliases import canonical_destination as canonicalize_destination
from api.services.room_rates_normalizer import normalize_room_type_category_key


class PropertyCandidate(BaseModel):
    """Booking property candidate shown during onboarding."""

    candidate_key: str
    display_name: str
    booking_url: str
    city: str
    address: str = ""
    country: str | None = None
    property_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    stars: float | None = None
    review_score: float | None = None
    review_count: int | None = None


class OwnedPropertyOnboardingCreate(BaseModel):
    """Selected own-property candidate plus sample stay used for discovery."""

    display_name: str = Field(..., min_length=1, max_length=255)
    booking_url: HttpUrl
    city: str = Field(..., min_length=1, max_length=255)
    raw_destination: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = None
    country: str | None = None
    property_type: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_selection(self) -> OwnedPropertyOnboardingCreate:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        self.display_name = self.display_name.strip()
        self.city = self.city.strip()
        raw_destination = (self.raw_destination or self.city).strip()
        if not self.display_name:
            raise ValueError("display_name is required")
        if not self.city:
            raise ValueError("city is required")
        if not raw_destination:
            raise ValueError("raw_destination is required")
        self.raw_destination = raw_destination
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_destination(self) -> str | None:
        """Stable destination key, always derived from ``raw_destination``.

        Derived (not client-settable): clients used to be able to send it, but
        the validator always overwrote it, so accepting it only misled callers.
        """
        return canonicalize_destination(self.raw_destination) if self.raw_destination else None


class OwnedPropertyOnboardingResponse(BaseModel):
    """Response after creating an owned property and background discovery job."""

    owned_property_id: UUID
    discovery_job: ScrapeJobResponse


class AutomaticSetupRequest(BaseModel):
    """Signup-driven property setup request without a separate onboarding screen."""

    property_name: str = Field(..., min_length=1, max_length=255)
    location: str = Field(..., min_length=1, max_length=255)
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=1, ge=1)
    limit: int = Field(default=8, ge=1, le=25)

    @model_validator(mode="after")
    def validate_setup(self) -> AutomaticSetupRequest:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        self.property_name = self.property_name.strip()
        self.location = self.location.strip()
        if not self.property_name:
            raise ValueError("property_name is required")
        if not self.location:
            raise ValueError("location is required")
        return self


class AutomaticSetupResponse(BaseModel):
    """Created own property, auto-picked Booking candidate and discovery job."""

    owned_property_id: UUID
    selected_candidate: PropertyCandidate
    discovery_job: ScrapeJobResponse


class OwnedPropertyRoomType(BaseModel):
    """Room type discovered from the user's own property."""

    id: UUID
    owned_property_id: UUID
    room_type: str
    room_type_category: str
    sample_meals: str | None = None
    sample_free_cancellation: str | None = None
    sample_facilities: str | None = None
    sample_price_per_night_eur: float | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SelectedRoomTypeRequest(BaseModel):
    """User-selected baseline room category for competitor matching."""

    room_type_category: str = Field(..., min_length=1, max_length=50)

    @model_validator(mode="after")
    def normalize_category(self) -> SelectedRoomTypeRequest:
        normalized_category = normalize_room_type_category_key(self.room_type_category)
        if normalized_category is None:
            raise ValueError("room_type_category is required")
        self.room_type_category = normalized_category
        return self


class SelectedRoomTypeResponse(BaseModel):
    """Persisted baseline room category for one owned property."""

    owned_property_id: UUID
    selected_room_type_category: str
    onboarding_complete: bool


class NearbyDestinationsResponse(BaseModel):
    """Default neighbouring areas for a destination (Round 6 map form)."""

    destination: str
    canonical: str
    nearby: list[str] = Field(default_factory=list)
