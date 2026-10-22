"""Reading an owner's bookings file (the RoomRate template or the Booking.com export) and storing it.

- ``values``: one cell → a date, an amount, a status, a number.
- ``columns``: which header names each field in each format.
- ``sheets``: the file → rows, and where the header row is.
- ``rows``: one row → bookings, or a Greek message for the owner.
- ``reader``: the whole file → ``ParsedFile``.
- ``labels``: which owned room type a file's room label means.
- ``staging``: bookings to and from the JSON the upload is kept in.
- ``service``: preview, mapping, commit and the page's status.

Contract: docs/performance/API.md («Imports in detail»).
"""

from api.services.performance.importing.parsed import ImportFileError, ParsedFile, ParsedRow
from api.services.performance.importing.reader import read_bookings_file

__all__ = ["ImportFileError", "ParsedFile", "ParsedRow", "read_bookings_file"]
