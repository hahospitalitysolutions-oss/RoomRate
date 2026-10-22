"""PostgreSQL access for «Απόδοση»: the owner's bookings, uploads, rooms, closures and settings.

Migrations 20261020_0035 and 20261021_0036. Every read and write is fenced
by the caller's account and owned property. One class for the callers,
written in parts:

- ``setup``: rooms per room type, closed periods, net-amount settings
- ``labels``: the files' room labels → owned room types
- ``bookings``: store, look up and delete bookings; the status counts
- ``imports``: uploads waiting for commit, the commit, the history
- ``analytics``: what the figures read
- ``writers``: the writes shared inside one transaction
"""

from api.repositories.performance.analytics import AnalyticsQueries
from api.repositories.performance.bookings import BookingQueries
from api.repositories.performance.imports import ImportQueries
from api.repositories.performance.labels import LabelQueries
from api.repositories.performance.setup import DEFAULT_REVENUE_SETTINGS, SetupQueries, room_types_from
from api.repositories.performance.writers import BOOKING_FIELDS


class PerformanceRepository(SetupQueries, LabelQueries, BookingQueries, ImportQueries, AnalyticsQueries):
    """Every «Απόδοση» query; see the modules of this package."""


__all__ = ["BOOKING_FIELDS", "DEFAULT_REVENUE_SETTINGS", "PerformanceRepository", "room_types_from"]
