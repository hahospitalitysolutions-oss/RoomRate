"""«Απόδοση»: the owner's own bookings, from an Excel upload to the page's figures.

- ``amounts``: gross and net, the one place the net formula lives.
- ``countries``: booker countries, codes and Greek names.
- ``template``: the RoomRate bookings template (its columns are the importer's too).
- ``importing``: reading an uploaded file, the preview, the label mapping, the commit.
- ``kpis``: occupancy, ADR, RevPAR, revenue, channels, countries, room types,
  stay patterns and pace.
- ``demo``: synthetic bookings for building and showing the page.

Contract with the page: docs/performance/API.md.
"""
