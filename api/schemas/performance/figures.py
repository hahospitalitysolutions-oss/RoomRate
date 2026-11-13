"""The figures: summary, monthly, channels, countries (KPIs) and room types, stay patterns, pace."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

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


LengthOfStayBucket = Literal["1", "2-3", "4-5", "6-9", "10+"]
LeadTimeBucket = Literal["0-7", "8-30", "31-60", "61+"]


class RoomTypePerformanceOut(BaseModel):
    id: UUID
    label: str
    rooms: int | None
    occupancy_pct: float | None
    adr_eur: float | None
    revpar_eur: float | None
    revenue_eur: float
    revenue_share_pct: float
    bookings: int
    room_nights_sold: int
    cancellations_pct: float | None
    market_median_eur: float | None


class PerformanceRoomTypesOut(BaseModel):
    amounts: AmountBasis
    room_types: list[RoomTypePerformanceOut]


class LengthShareOut(BaseModel):
    bucket: LengthOfStayBucket
    bookings: int
    share_pct: float


class LeadShareOut(BaseModel):
    bucket: LeadTimeBucket
    bookings: int
    share_pct: float


class StayMatrixCellOut(BaseModel):
    length_bucket: LengthOfStayBucket
    lead_bucket: LeadTimeBucket
    bookings: int


class MonthLeadTimeOut(BaseModel):
    month: str
    avg_lead_time_days: float | None


class PerformanceStayPatternsOut(BaseModel):
    length_of_stay: list[LengthShareOut]
    lead_time: list[LeadShareOut]
    matrix: list[StayMatrixCellOut]
    monthly_lead_time: list[MonthLeadTimeOut]
    avg_length_of_stay: float | None
    avg_lead_time_days: float | None
    avg_guests: float | None


class PaceMonthOut(BaseModel):
    month: str
    otb_occupancy_pct: float | None
    otb_room_nights: int
    last_year_same_day_pct: float | None


class PickupOut(BaseModel):
    bookings: int
    room_nights: int


class PerformancePaceOut(BaseModel):
    season: int
    as_of: date
    months: list[PaceMonthOut]
    pickup_7d: PickupOut
