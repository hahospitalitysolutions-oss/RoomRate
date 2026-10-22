"""«Απόδοση» figures from the owner's nights and bookings.

Definitions (docs/performance/API.md, «Common rules»):

- A night belongs to the date it starts; only confirmed bookings' nights are
  sold (cancellations and no-shows never are).
- Available room nights: the rooms of each room type that day minus closed
  periods; unknown (None) when a booked room type has no room count, so
  occupancy and RevPAR are None too, never a made-up 0%.
- Occupancy = sold / available; ADR = revenue / sold; RevPAR = revenue / available.
- Bookings, cancellations, length of stay and lead time count the bookings
  arriving in the period; revenue, nights, channels and countries the nights.
- Last year: the same dates a year earlier (29 February → 28 February).

Modules: ``periods`` (filters, dates), ``availability`` (rooms open per day),
``figures`` (the KPIs of some nights), ``monthly``, ``breakdowns`` (channels,
countries, room types), ``patterns`` (stay length and lead time), ``pace``
(on the books), and ``service`` (reads the rows and answers each call).
"""

from api.services.performance.kpis.periods import Filters, KpiRejected
from api.services.performance.kpis.service import PerformanceKpiService

__all__ = ["Filters", "KpiRejected", "PerformanceKpiService"]
