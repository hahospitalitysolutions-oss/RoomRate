"""Uploads: the preview, the mapping, the commit (ImportPreview, ImportMappingUpdate, ImportCommit*)."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from api.schemas.performance.status import ImportFormat, PerformanceStatusOut

BookingStatus = Literal["confirmed", "cancelled", "no_show"]


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
