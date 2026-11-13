"""The figures: /summary, /monthly, /channels, /countries, /room-types, /stay-patterns (period filters) and /pace."""

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_performance_kpi_service
from api.routers.performance_routes.common import OwnedProperty, answer, owned_property
from api.schemas.performance import (
    AmountBasis,
    Compare,
    PerformanceChannelsOut,
    PerformanceCountriesOut,
    PerformanceMonthlyOut,
    PerformancePaceOut,
    PerformanceRoomTypesOut,
    PerformanceStayPatternsOut,
    PerformanceSummaryOut,
)
from api.services.demand_schedule import greek_now
from api.services.performance.kpis import Filters, PerformanceKpiService

router = APIRouter()


def period_filters(
    date_from: date = Query(..., description="First stay date (a night belongs to the date it starts)"),
    date_to: date = Query(..., description="Last stay date, inclusive"),
    room_type_id: UUID | None = Query(None, description="An owned room type; omitted = the whole property"),
    amounts: AmountBasis = Query("gross"),
    compare: Compare = Query("last_year"),
) -> Filters:
    return Filters(date_from, date_to, room_type_id, amounts, compare)


class Figures:
    """The dependencies every figure call takes: the period, the caller's property, the service."""

    def __init__(
        self,
        filters: Filters = Depends(period_filters),
        owned: OwnedProperty = Depends(owned_property),
        service: PerformanceKpiService = Depends(get_performance_kpi_service),
    ):
        self.filters, self.owned, self.service = filters, owned, service

    def __call__(self, method) -> dict[str, Any]:
        return answer(lambda: method(self.owned.account_id, self.owned.row, self.filters))


@router.get("/summary", response_model=PerformanceSummaryOut)
def performance_summary(figures: Figures = Depends()) -> dict[str, Any]:
    """Occupancy, ADR, RevPAR, revenue and booking habits of the period, against last year and the market."""
    return figures(figures.service.summary)


@router.get("/monthly", response_model=PerformanceMonthlyOut)
def performance_monthly(figures: Figures = Depends()) -> dict[str, Any]:
    """The same figures month by month, with the unsold and closed nights."""
    return figures(figures.service.monthly)


@router.get("/channels", response_model=PerformanceChannelsOut)
def performance_channels(figures: Figures = Depends()) -> dict[str, Any]:
    """Revenue, nights and commission per channel, and per channel per month."""
    return figures(figures.service.channels)


@router.get("/countries", response_model=PerformanceCountriesOut)
def performance_countries(figures: Figures = Depends()) -> dict[str, Any]:
    """The ten booker countries with most bookings, then «Λοιπές»."""
    return figures(figures.service.countries)


@router.get("/room-types", response_model=PerformanceRoomTypesOut)
def performance_room_types(figures: Figures = Depends()) -> dict[str, Any]:
    """Each room type over the period, against the market for its category; room_type_id does not apply."""
    return figures(figures.service.room_types)


@router.get("/stay-patterns", response_model=PerformanceStayPatternsOut)
def performance_stay_patterns(figures: Figures = Depends()) -> dict[str, Any]:
    """How long guests stay and how early they book, for the bookings arriving in the period."""
    return figures(figures.service.stay_patterns)


@router.get("/pace", response_model=PerformancePaceOut)
def performance_pace(
    season: int = Query(..., ge=2000, le=2100, description="The season (year) to look at"),
    owned: OwnedProperty = Depends(owned_property),
    service: PerformanceKpiService = Depends(get_performance_kpi_service),
) -> dict[str, Any]:
    """The season's occupancy on the books today (Greek date), against last season on the same day."""
    today = greek_now().date()
    return answer(lambda: service.pace(owned.account_id, owned.row, season, today))
