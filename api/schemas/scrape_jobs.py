from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from api.services.destination_aliases import canonical_destination as canonicalize_destination
from api.services.room_rates_normalizer import normalize_room_type_category_key

ScrapeJobType = Literal["owned_property_room_discovery", "competitor_search"]

# Job-type constants for production code. They MUST match the ScrapeJobType
# Literal above (Literal requires inline string literals, so the values are
# necessarily duplicated here).
JOB_TYPE_COMPETITOR_SEARCH = "competitor_search"
JOB_TYPE_ROOM_DISCOVERY = "owned_property_room_discovery"

# Round 6 (§3.1) limits for the map form's neighbouring-area chips.
MAX_NEARBY_DESTINATIONS = 8
MAX_NEARBY_DESTINATION_LENGTH = 100


class ScrapeJobCreate(BaseModel):
    """Request body for an account-scoped market scrape job."""

    owned_property_id: UUID | None = None
    job_type: ScrapeJobType = "competitor_search"
    room_type_category: str | None = Field(default=None, min_length=1, max_length=50)
    destination: str = Field(..., min_length=1, max_length=255)
    raw_destination: str | None = Field(default=None, min_length=1, max_length=255)
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=1, ge=1)
    filters_payload: dict[str, Any] = Field(default_factory=dict)
    # Round 6 (§3.1): neighbouring Booking destinations scouted in parallel
    # and the radius (km) around the OWNER's property. Normalized in the
    # validator below; the radius centre is resolved server-side
    # (ScrapeJobService.run_job), never taken from the browser.
    nearby_destinations: list[str] = Field(default_factory=list)
    radius_km: float | None = Field(default=None, ge=0.5, le=50)

    @model_validator(mode="after")
    def validate_date_range(self) -> ScrapeJobCreate:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        # Security: ``filters_payload.scheduled`` controls scout-cache sharing
        # (12h) and scheduler-rotation placement and must never be client-set.
        # Strip it on every ScrapeJobCreate; only ScrapeJobService.create_job's
        # explicit ``scheduled=True`` (used solely by the scheduler) re-adds it.
        if isinstance(self.filters_payload, dict):
            self.filters_payload.pop("scheduled", None)
        self.destination = self.destination.strip()
        if not self.destination:
            raise ValueError("destination is required")
        raw_destination = (self.raw_destination or self.destination).strip()
        if not raw_destination:
            raise ValueError("raw_destination is required")
        self.raw_destination = raw_destination
        self.room_type_category = normalize_room_type_category_key(self.room_type_category)
        self.nearby_destinations = self._normalize_nearby_destinations(self.nearby_destinations)
        return self

    def _normalize_nearby_destinations(self, values: list[str]) -> list[str]:
        """Lenient where the UI chips repeat themselves, strict on the spec numbers.

        Trim, drop blanks, dedupe and drop the main destination by canonical key
        (case-, accent- and alias-insensitive: «faliraki» is «Φαληράκι»). An
        entry over 100 characters or more than 8 entries AFTER normalization is
        a validation error (422 at the boundary).
        """
        main_key = canonicalize_destination(self.destination)
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            entry = str(value or "").strip()
            if not entry:
                continue
            if len(entry) > MAX_NEARBY_DESTINATION_LENGTH:
                raise ValueError("nearby_destinations entries must be at most 100 characters")
            key = canonicalize_destination(entry)
            if key == main_key or key in seen:
                continue
            seen.add(key)
            normalized.append(entry)
        if len(normalized) > MAX_NEARBY_DESTINATIONS:
            raise ValueError("nearby_destinations allows at most 8 areas")
        return normalized

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_destination(self) -> str | None:
        """Stable destination key, always derived from ``raw_destination``.

        Derived (not client-settable): clients used to be able to send it, but
        the validator always overwrote it, so accepting it only misled callers.
        """
        return canonicalize_destination(self.raw_destination) if self.raw_destination else None


class ScrapeJobResponse(BaseModel):
    """API response for persisted scrape jobs."""

    id: UUID
    account_id: UUID
    owned_property_id: UUID | None = None
    job_type: str = "competitor_search"
    room_type_category: str | None = None
    destination: str
    raw_destination: str | None = None
    canonical_destination: str | None = None
    check_in: date
    check_out: date
    adults: int = Field(..., ge=1)
    children: int = Field(..., ge=0)
    rooms: int = Field(..., ge=1)
    filters_payload: dict[str, Any] = Field(default_factory=dict)
    # Scheduler-enqueued job (server-set; see ScrapeJobService.create_job).
    scheduled: bool = False
    status: str
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    next_attempt_at: datetime | None = None
    scrape_runs_count: int = Field(default=0, ge=0)
    result_summary: dict[str, Any] | None = None
    # Round 6: echoed back for «Επαναφορά τελευταίας αναζήτησης». NULL columns
    # (jobs older than migration 20260915_0024) read as the defaults.
    nearby_destinations: list[str] = Field(default_factory=list)
    radius_km: float | None = None

    @field_validator("nearby_destinations", mode="before")
    @classmethod
    def _null_nearby_is_empty(cls, value: object) -> object:
        return [] if value is None else value
