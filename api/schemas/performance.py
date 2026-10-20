"""«Απόδοση» answers, field for field the types of frontend/src/app/types/performance.ts.

Change them only together with that file, docs/performance/API.md and the
samples in frontend/public/mock/performance (a «contract» PR).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ImportFormat = Literal["template", "booking"]
BookingStatus = Literal["confirmed", "cancelled", "no_show"]


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


class PerformanceRoomTypeOut(BaseModel):
    id: UUID
    label: str
    rooms: int | None


class ImportSummaryOut(BaseModel):
    id: UUID
    format: ImportFormat
    filename: str
    committed_at: datetime
    bookings_imported: int


class PerformanceStatusOut(BaseModel):
    has_data: bool
    bookings: int
    first_stay_date: date | None
    last_stay_date: date | None
    last_import: ImportSummaryOut | None
    inventory_complete: bool
    room_types: list[PerformanceRoomTypeOut]
    seasons: list[int]


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


class ImportedBookingRowOut(BaseModel):
    booking_ref: str
    booked_on: date | None
    arrival: date | None
    departure: date | None
    nights: int | None
    room_label: str
    room_type_id: UUID | None
    rooms: int
    gross_eur: float | None
    commission_eur: float | None
    net_eur: float | None
    channel: str
    status: BookingStatus
    error: str | None


class ImportErrorOut(BaseModel):
    row: int
    booking_ref: str | None
    message: str


class ImportMappingOut(BaseModel):
    label: str
    bookings: int
    room_type_id: UUID | None
    # The owner left this label's bookings out (room_type_id is then None).
    ignored: bool


class ImportPreviewOut(BaseModel):
    id: UUID
    format: ImportFormat
    filename: str
    rows_total: int
    new: int
    updated: int
    cancelled: int
    errors: list[ImportErrorOut]
    mapping: list[ImportMappingOut]
    sample: list[ImportedBookingRowOut]
    ready: bool


class ImportMappingItemIn(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    room_type_id: UUID | None = None
    ignore: bool = False


class ImportMappingUpdateIn(BaseModel):
    mapping: list[ImportMappingItemIn] = Field(max_length=500)


class ImportCommitIn(BaseModel):
    skip_errors: bool


class ImportCommitOut(BaseModel):
    imported: int
    updated: int
    skipped: int
    status: PerformanceStatusOut


# ---------------------------------------------------------------------------
# GET/PUT /inventory, /closures, /revenue-settings
# ---------------------------------------------------------------------------


class InventoryRoomTypeIn(BaseModel):
    room_type_id: UUID
    label: str = ""
    rooms: int | None = Field(default=None, ge=0, le=10_000)
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> "InventoryRoomTypeIn":
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("«Ισχύει έως» πριν από το «Ισχύει από».")
        return self


class InventoryRoomTypeOut(BaseModel):
    room_type_id: UUID
    label: str
    rooms: int | None
    valid_from: date | None
    valid_to: date | None


class RoomInventoryIn(BaseModel):
    room_types: list[InventoryRoomTypeIn] = Field(max_length=500)


class RoomInventoryOut(BaseModel):
    room_types: list[InventoryRoomTypeOut]


class ClosureIn(BaseModel):
    date_from: date
    date_to: date
    room_type_id: UUID | None = None
    rooms: int | None = Field(default=None, gt=0, le=10_000)
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _range(self) -> "ClosureIn":
        if self.date_to < self.date_from:
            raise ValueError("«Έως» πριν από το «Από».")
        return self


class ClosureOut(BaseModel):
    date_from: date
    date_to: date
    room_type_id: UUID | None
    rooms: int | None
    reason: str | None


class PropertyClosuresIn(BaseModel):
    closures: list[ClosureIn] = Field(max_length=500)


class PropertyClosuresOut(BaseModel):
    closures: list[ClosureOut]


class RevenueSettingsIn(BaseModel):
    vat_pct: float = Field(ge=0, le=100)
    resilience_fee_eur: float = Field(ge=0, le=1_000)
    fee_included_in_price: bool


class RevenueSettingsOut(BaseModel):
    vat_pct: float
    resilience_fee_eur: float
    fee_included_in_price: bool


# ---------------------------------------------------------------------------
# GET /summary, /monthly, /channels, /countries   (filters)
# ---------------------------------------------------------------------------

AmountBasis = Literal["gross", "net"]
Compare = Literal["last_year", "none"]


class PerformanceKpisOut(BaseModel):
    occupancy_pct: float | None
    adr_eur: float | None
    revpar_eur: float | None
    revenue_eur: float
    room_nights_sold: int
    room_nights_available: int | None
    bookings: int
    cancellations_pct: float | None
    avg_length_of_stay: float | None
    avg_lead_time_days: float | None
    commission_eur: float


class PeriodOut(BaseModel):
    date_from: date
    date_to: date


class KpiDeltaOut(BaseModel):
    occupancy_pts: float | None
    adr_pct: float | None
    revpar_pct: float | None
    revenue_pct: float | None


class PerformanceSummaryOut(BaseModel):
    period: PeriodOut
    amounts: AmountBasis
    kpis: PerformanceKpisOut
    last_year: PerformanceKpisOut | None
    delta: KpiDeltaOut | None
    market_median_eur: float | None


class MonthLastYearOut(BaseModel):
    occupancy_pct: float | None
    adr_eur: float | None
    revpar_eur: float | None
    revenue_eur: float


class MonthKpisOut(BaseModel):
    month: str
    occupancy_pct: float | None
    adr_eur: float | None
    revpar_eur: float | None
    revenue_eur: float
    bookings: int
    room_nights_sold: int
    room_nights_available: int | None
    room_nights_unsold: int | None
    closed_days: int
    market_median_eur: float | None
    last_year: MonthLastYearOut | None


class PerformanceMonthlyOut(BaseModel):
    amounts: AmountBasis
    months: list[MonthKpisOut]


class ChannelTotalOut(BaseModel):
    channel: str
    bookings: int
    room_nights: int
    revenue_eur: float
    commission_eur: float
    share_pct: float


class ChannelMonthOut(BaseModel):
    month: str
    revenue_by_channel: dict[str, float]


class PerformanceChannelsOut(BaseModel):
    amounts: AmountBasis
    totals: list[ChannelTotalOut]
    monthly: list[ChannelMonthOut]


class CountryTotalOut(BaseModel):
    country: str
    bookings: int
    revenue_eur: float
    share_pct: float


class PerformanceCountriesOut(BaseModel):
    amounts: AmountBasis
    countries: list[CountryTotalOut]
