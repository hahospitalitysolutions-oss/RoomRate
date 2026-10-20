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
