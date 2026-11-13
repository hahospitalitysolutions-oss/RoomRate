"""The property's set-up: rooms per room type, closed periods, net-amount settings."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


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
