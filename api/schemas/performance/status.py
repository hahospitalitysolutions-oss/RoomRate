"""GET /status (PerformanceStatus), as types/performance.ts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ImportFormat = Literal["template", "booking"]


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
