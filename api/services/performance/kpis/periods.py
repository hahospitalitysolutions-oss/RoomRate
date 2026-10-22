"""The period a call asks about: filters, days, months, and the same dates a year earlier."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

MAX_PERIOD_DAYS = 3 * 366


class KpiRejected(ValueError):
    """Filters the call cannot answer; the message is Greek (400)."""


@dataclass(frozen=True)
class Filters:
    date_from: date
    date_to: date
    room_type_id: uuid.UUID | None = None
    amounts: str = "gross"
    compare: str = "last_year"

    def last_year(self) -> Filters:
        return replace(
            self, date_from=a_year_earlier(self.date_from), date_to=a_year_earlier(self.date_to), compare="none"
        )

    def check(self) -> None:
        if self.date_to < self.date_from:
            raise KpiRejected("Η περίοδος τελειώνει πριν αρχίσει.")
        if (self.date_to - self.date_from).days >= MAX_PERIOD_DAYS:
            raise KpiRejected("Διαλέξτε περίοδο έως 3 χρόνια.")


def a_year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def days(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def months(date_from: date, date_to: date) -> list[tuple[str, date, date]]:
    """Each month touching the period with the period's first and last day in it."""
    result = []
    start = date_from
    while start <= date_to:
        next_month = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
        result.append((month_of(start), start, min(date_to, next_month - timedelta(days=1))))
        start = next_month
    return result


def in_period(rows: Iterable[dict[str, Any]], key: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
    return [row for row in rows if date_from <= row[key] <= date_to]


def by_month(rows: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """The rows grouped by the month of their ``key`` date, read once instead of once per month."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[month_of(row[key])].append(row)
    return groups
