"""The «Απόδοση» figure calls: read the rows each needs once, then build the answer.

Each call is a handful of queries: the property's rooms (which also give its
room types), closures, the period's nights and arrivals (with last year's
in the same query), the net-amount settings only for net, and the market.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from typing import Any

from api.repositories.performance import PerformanceRepository, room_types_from
from api.services.performance.kpis.availability import availability
from api.services.performance.kpis.breakdowns import channel_months, channel_totals, country_totals, room_type_rows
from api.services.performance.kpis.figures import delta, kpis, median
from api.services.performance.kpis.monthly import month_rows
from api.services.performance.kpis.pace import season_pace
from api.services.performance.kpis.patterns import stay_patterns
from api.services.performance.kpis.periods import Filters, KpiRejected, a_year_earlier, days, in_period
from api.services.room_rates_normalizer import comparable_room_type_categories


class PerformanceKpiService:
    def __init__(self, repository: PerformanceRepository):
        self.repository = repository

    # ------------------------------------------------------------------
    # The calls
    # ------------------------------------------------------------------

    def summary(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, filters)
        now = self._figures(data["now"], data)
        before = self._figures(data["before"], data) if data["before"] else None
        market = self._market(account_id, owned_property, filters, self._category(owned_property, filters, data))
        return {
            "period": {"date_from": filters.date_from, "date_to": filters.date_to},
            "amounts": filters.amounts,
            "kpis": now,
            "last_year": before,
            "delta": delta(now, before) if before else None,
            "market_median_eur": median(row["price_eur"] for row in market),
        }

    def monthly(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, filters)
        market = self._market(account_id, owned_property, filters, self._category(owned_property, filters, data))
        rows = month_rows(data["now"], data["before"], market, filters.amounts, data["settings"])
        return {"amounts": filters.amounts, "months": rows}

    def channels(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, replace(filters, compare="none"))
        nights, settings = data["now"]["nights"], data["settings"]
        totals = channel_totals(nights, filters.amounts, settings)
        names = [row["channel"] for row in totals]
        return {
            "amounts": filters.amounts,
            "totals": totals,
            "monthly": channel_months(nights, names, filters.date_from, filters.date_to, filters.amounts, settings),
        }

    def countries(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, replace(filters, compare="none"))
        return {
            "amounts": filters.amounts,
            "countries": country_totals(data["now"]["nights"], filters.amounts, data["settings"]),
        }

    def room_types(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        """Each room type over the period (the room type filter does not apply), highest revenue first."""
        filters = replace(filters, room_type_id=None, compare="none")
        data = self._load(account_id, owned_property, filters)
        period = days(filters.date_from, filters.date_to)
        ids = [room["id"] for room in data["room_types"]]
        markets: dict[tuple[str, ...], float | None] = {}

        def market_median(category: str | None) -> float | None:
            pool = comparable_room_type_categories(category)
            if pool not in markets:
                prices = self._market(account_id, owned_property, filters, category)
                markets[pool] = median(row["price_eur"] for row in prices)
            return markets[pool]

        rows = room_type_rows(
            data["room_types"],
            data["now"]["nights"],
            data["now"]["bookings"],
            lambda kind: availability(period, ids, data["inventory"], data["closures"], room_type_id=kind),
            market_median,
            (filters.date_from, filters.date_to),
            filters.amounts,
            data["settings"],
        )
        return {"amounts": filters.amounts, "room_types": rows}

    def stay_patterns(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        data = self._load(account_id, owned_property, replace(filters, compare="none"))
        return stay_patterns(data["now"]["bookings"], filters.date_from, filters.date_to)

    def pace(self, account_id: uuid.UUID, owned_property: dict[str, Any], season: int, today: date) -> dict[str, Any]:
        """PerformancePace: the season's nights on the books today, against last season on the same day."""
        if not 2000 <= season <= 2100:
            raise KpiRejected("Μη έγκυρη σεζόν.")
        owned_property_id = owned_property["id"]
        first, last = date(season, 1, 1), date(season, 12, 31)
        held = self.repository.held_nights(
            account_id, owned_property_id, [(first, last), (a_year_earlier(first), a_year_earlier(last))]
        )
        inventory = self.repository.inventory(account_id, owned_property_id)
        closures = self.repository.closures(account_id, owned_property_id)
        ids = [room["id"] for room in room_types_from(inventory)]
        return season_pace(
            season,
            today,
            held,
            lambda period, booked: availability(period, ids, inventory, closures, sold_room_types=booked),
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _load(self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters) -> dict[str, Any]:
        """The period's (and last year's) nights and arrivals, the rooms, closures and net settings."""
        filters.check()
        owned_property_id = owned_property["id"]
        inventory = self.repository.inventory(account_id, owned_property_id)
        room_types = room_types_from(inventory)
        if filters.room_type_id and filters.room_type_id not in {room["id"] for room in room_types}:
            raise KpiRejected("Ο τύπος δωματίου δεν ανήκει στο κατάλυμα.")
        last = filters.last_year() if filters.compare == "last_year" else None
        windows = [(filters.date_from, filters.date_to)] + ([(last.date_from, last.date_to)] if last else [])
        nights = self.repository.sold_nights(account_id, owned_property_id, windows, filters.room_type_id)
        arrivals = self.repository.bookings_arriving(account_id, owned_property_id, windows, filters.room_type_id)
        closures = self.repository.closures(account_id, owned_property_id)
        ids = [room["id"] for room in room_types]

        def window(f: Filters) -> dict[str, Any]:
            period_nights = in_period(nights, "stay_date", f.date_from, f.date_to)
            booked = {night["room_type_id"] for night in period_nights if night["room_type_id"]}
            return {
                "filters": f,
                "nights": period_nights,
                "bookings": in_period(arrivals, "arrival", f.date_from, f.date_to),
                "availability": availability(
                    days(f.date_from, f.date_to), ids, inventory, closures,
                    room_type_id=f.room_type_id, sold_room_types=booked,
                ),
            }

        before = window(last) if last else None
        if before and not before["nights"] and not before["bookings"]:
            before = None  # no data a year earlier
        return {
            "now": window(filters),
            "before": before,
            "settings": (
                self.repository.revenue_settings(account_id, owned_property_id) if filters.amounts == "net" else None
            ),
            "filters": filters,
            "room_types": room_types,
            "inventory": inventory,
            "closures": closures,
        }

    @staticmethod
    def _figures(part: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        f = part["filters"]
        available = part["availability"].total(days(f.date_from, f.date_to))
        return kpis(part["nights"], part["bookings"], available, f.amounts, data["settings"])

    @staticmethod
    def _category(owned_property: dict[str, Any], filters: Filters, data: dict[str, Any]) -> str | None:
        """The room category the market is compared on: the filtered room type's, else the property's."""
        category = owned_property.get("selected_room_type_category")
        if filters.room_type_id:
            room = next((room for room in data["room_types"] if room["id"] == filters.room_type_id), None)
            category = room.get("category") if room else category
        return category

    def _market(
        self, account_id: uuid.UUID, owned_property: dict[str, Any], filters: Filters, category: str | None
    ) -> list[dict[str, Any]]:
        """Competitors' prices for the period's check-ins, rooms comparable with ``category`` (None: every room)."""
        destination = owned_property.get("canonical_destination")
        if not destination:
            return []
        return self.repository.market_prices(
            account_id, destination, filters.date_from, filters.date_to, comparable_room_type_categories(category)
        )
