"""Outside demand signals for the price advisor: ferries and flights into the destination."""

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


class FlightDemandSignal(BaseModel):
    """Scheduled arrivals at the island's airport around the check-in.

    ``travel_dates``: the collected days read, the check-in itself when it is
    collected, else the nearest within a few days (the free plan collects
    Fridays and Saturdays). Pickup: the same days' arrivals now against an
    earlier look (``compared_with``): airlines add flights where seats sell.
    """

    level: DemandLevel
    check_in: date
    airport_iata: str
    island: str
    travel_dates: list[date] = Field(default_factory=list)
    arrivals: int = Field(0, ge=0)
    airlines: int = Field(0, ge=0)
    arrivals_before: int | None = None
    flights_added: int | None = None
    arrivals_change_pct: float | None = None
    compared_with: date | None = None
    top_origins: list[str] = Field(default_factory=list)
    observed_on: date | None = None
    summary: str


class DemandSignals(BaseModel):
    ferries: FerryDemandSignal | None = None
    flights: FlightDemandSignal | None = None


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
    # False when the owner switched ferries off (Ρυθμίσεις): nothing else is sent.
    enabled: bool = True
    # None until the first port lookup; "ferry" | "no_ferry" | "error" after.
    status: str | None = None
    island_port: str | None = None
    port_distance_km: float | None = None
    resolved_at: datetime | None = None
    routes: list[FerryRouteOut] = Field(default_factory=list)
    days: list[FerryDayOut] = Field(default_factory=list)
    observed_on: date | None = None


class FlightDayOut(BaseModel):
    """The airport's collected arrivals on one travel date (every window of the day)."""

    travel_date: date
    arrivals: int
    airlines: int
    arrivals_change_pct: float | None = None
    flights_added: int | None = None
    compared_with: date | None = None
    top_origins: list[str] = Field(default_factory=list)
    level: DemandLevel


class FlightDemandResponse(BaseModel):
    canonical_destination: str
    # "flights": an island airport is collected; "no_airport": the property
    # is not on an island with one; "off": no AeroDataBox key or disabled.
    status: Literal["flights", "no_airport", "off"]
    # False when the owner switched flights off (Ρυθμίσεις): no days are sent.
    enabled: bool = True
    airport_iata: str | None = None
    island: str | None = None
    days: list[FlightDayOut] = Field(default_factory=list)
    observed_on: date | None = None


class DemandRunOut(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    saved: int | None = None


class DemandSettingsIn(BaseModel):
    ferries_enabled: bool
    flights_enabled: bool
    # Greek hour (Europe/Athens) of the daily collection.
    hour_local: int = Field(ge=0, le=23)
    # Also ask for a collection now (onboarding's «Ενεργοποίηση»).
    collect_now: bool = False


class DemandSettingsOut(BaseModel):
    """The owner's demand collection settings and what they apply to.

    ``island`` / ``airport_iata`` are None when the property is not on an
    island / one with an airport: ferries / flights do not apply there.
    ``flights_available`` is False without an AeroDataBox key.
    """

    owned_property_id: UUID
    ferries_enabled: bool
    flights_enabled: bool
    hour_local: int
    timezone: str = "Europe/Athens"
    island: str | None = None
    airport_iata: str | None = None
    ferries_available: bool
    flights_available: bool
    collect_requested_at: datetime | None = None
    # A requested collection the worker has not run yet.
    collection_pending: bool = False
    ferries_last_run: DemandRunOut | None = None
    flights_last_run: DemandRunOut | None = None
    # Greek time of the next daily collection; None when nothing is on.
    next_collection_at: datetime | None = None


class DemandCollectOut(BaseModel):
    """POST /demand/collect: queued, or why not (already collected today)."""

    queued: bool
    message: str
    settings: DemandSettingsOut
