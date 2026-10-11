"""Outside demand signals for the price advisor (ferries first)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DemandLevel = Literal["high", "normal", "low", "unknown"]


class FerryDemandSignal(BaseModel):
    """Ferries INTO the destination around the arrival: the day before and the check-in day.

    Pickup (``*_change_pct``): the same sailing days against an earlier look
    at them (``compared_with``), routes weighted by what they carry.
    ``sailings_vs_usual_pct``: against the same routes on the same weekday.
    None until enough days are collected. ``sold_out`` counts only sailings
    whose answer said whether seats are left (Ferryhopper's do not).
    """

    level: DemandLevel
    check_in: date
    window: list[date]
    sailings: int = Field(0, ge=0)
    availability_known: int = Field(0, ge=0)
    sold_out: int = Field(0, ge=0)
    sold_out_share_pct: float | None = None
    min_price_eur: float | None = None
    # Accommodations (fare tiers, cabins) still offered: Ferryhopper drops
    # what is full, so a fall since the earlier look means filling up.
    options_change_pct: float | None = None
    price_change_pct: float | None = None
    compared_with: date | None = None
    sailings_vs_usual_pct: float | None = None
    routes: list[str] = Field(default_factory=list)
    observed_on: date | None = None
    summary: str


class DemandSignals(BaseModel):
    ferries: FerryDemandSignal | None = None


class FerryRouteOut(BaseModel):
    id: UUID
    origin_port: str
    destination_port: str
    status: str
    sample_sailings: int | None = None


class FerryDayOut(BaseModel):
    """All collected routes into the destination on one travel date."""

    travel_date: date
    sailings: int
    availability_known: int
    sold_out: int
    min_price_eur: float | None = None
    # Priced passenger accommodations the day's sailings still offer.
    accommodation_options: int | None = None
    # Pickup of a check-in that day (see FerryDemandSignal).
    options_change_pct: float | None = None
    price_change_pct: float | None = None
    level: DemandLevel


class FerryDemandResponse(BaseModel):
    canonical_destination: str
    # None until the first port lookup; "ferry" | "no_ferry" | "error" after.
    status: str | None = None
    island_port: str | None = None
    port_distance_km: float | None = None
    resolved_at: datetime | None = None
    routes: list[FerryRouteOut] = Field(default_factory=list)
    days: list[FerryDayOut] = Field(default_factory=list)
    observed_on: date | None = None
