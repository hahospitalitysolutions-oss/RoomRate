# «Απόδοση»: API contract

The page `/performance` (frontend) and `api/routers/performance.py` (backend)
meet here. Types: `frontend/src/app/types/performance.ts` (source of truth for
field names). One example answer per call: `frontend/public/mock/performance/`
— the page runs on them until the endpoint exists (see
`frontend/src/app/services/performance-api.service.ts`).

Change this contract only in a small PR labelled `contract` that edits the
types, this file and the mock files together, approved by both sides.

## Common rules

- Base path `/api/v1/performance`. Every call: `owned_property_id` (UUID) of
  one of the caller's own properties; any other → 404 «Owned property not
  found». No data yet is not an error: `/status` says `has_data: false` and
  the analysis calls answer with zeros / empty lists.
- **Filters** (`/summary`, `/monthly`, `/channels`, `/countries`,
  `/room-types`, `/stay-patterns`): `date_from`, `date_to` (stay dates,
  inclusive; a night belongs to the date it starts), `room_type_id`
  (optional; omitted = whole property; ignored by `/room-types`),
  `amounts=gross|net`, `compare=last_year|none`.
- Amounts in €, 2 decimals. Percentages 0–100, 1 decimal. Months `YYYY-MM`.
- **Gross** = what the guest paid (VAT and resilience fee included).
  **Net** = gross − VAT − resilience fee (when included in the price) −
  channel commission (`/revenue-settings`).
- Occupancy = room nights sold / room nights available; available = rooms ×
  days − closed (`/closures`). ADR = revenue / nights sold. RevPAR = revenue /
  nights available. With a room type's room count missing, `occupancy_pct`,
  `revpar_eur`, `room_nights_available` are `null` (`/status.inventory_complete`
  false): show «Ορίστε τα δωμάτια ανά τύπο», never 0%.
- Cancelled and no-show bookings never count as sold nights or revenue; they
  count in `cancellations_pct` (by booking, over bookings made for the period).
- Revenue of a booking is spread evenly over its nights; a booking across
  two months counts in both.
- Market median: the median competitor price RoomRate collected for the same
  stays (destination, room category); `null` where there is none.
- Errors: `{"detail": "<Greek message>"}` with 400 (bad file / filters), 404,
  409 (import already committed), 413 (file over 5 MB), 422 (validation).

## Calls

| Call | Answer (type) | Example |
|---|---|---|
| `GET /status` | `PerformanceStatus` | `status.json`, `status-empty.json` |
| `GET /summary` (filters) | `PerformanceSummary` | `summary.json` |
| `GET /monthly` (filters) | `PerformanceMonthly` | `monthly.json` |
| `GET /channels` (filters) | `PerformanceChannels` | `channels.json` |
| `GET /countries` (filters) | `PerformanceCountries` (top 10 + «Λοιπές») | `countries.json` |
| `GET /room-types` (filters) | `PerformanceRoomTypes` | `room-types.json` |
| `GET /stay-patterns` (filters) | `PerformanceStayPatterns` | `stay-patterns.json` |
| `GET /pace?season=2027` | `PerformancePace` (bookings on the books vs last year on the same day) | `pace.json` |
| `GET /forecast?target=revenue\|bookings\|room_nights&horizon=1..12` | `PerformanceForecast` | `forecast.json`, `forecast-insufficient.json` |
| `GET /template.xlsx` | the Excel template (4 sheets) | — |
| `POST /imports` (multipart: `file`, `format=template\|booking`) | `ImportPreview`; nothing stored yet | `import-preview.json` |
| `PUT /imports/{id}/mapping` (`ImportMappingUpdate`) | `ImportPreview` | `import-preview.json` |
| `POST /imports/{id}/commit` (`ImportCommitRequest`) | `ImportCommitResult` | `import-commit.json` |
| `GET /imports` | `ImportSummary[]` (committed, newest first) | `imports.json` |
| `DELETE /bookings` | 204; every booking of the property gone, settings kept | — |
| `GET` / `PUT /inventory` | `RoomInventory` | `inventory.json` |
| `GET` / `PUT /closures` | `PropertyClosures` | `closures.json` |
| `GET` / `PUT /revenue-settings` | `RevenueSettings` | `revenue-settings.json` |

## Imports in detail

1. `POST /imports` reads the file (`.xlsx` or `.csv`, ≤ 5 MB, ≤ 20,000 rows),
   checks every row and keeps the result for 24 hours. Guests' names, emails,
   phones and addresses are never read. `errors[]` lists rows that will be
   skipped, with the Excel row number. `mapping[]` lists each room label of
   the file; labels seen in an earlier import come mapped already.
2. `PUT /imports/{id}/mapping` maps (or ignores) labels; `ready` turns true
   when none is left unmapped.
3. `POST /imports/{id}/commit` stores the bookings: a `booking_ref` already
   stored for the property is replaced (re-uploading never duplicates).
   `skip_errors: false` with errors present → 400.

Booking.com export columns read: Book number, Booked on, Check-in, Check-out,
Unit type / Room type, Rooms, Adults, Children, Price, Commission amount,
Status, Cancellation date, Booker country. Channel = «Booking.com». To be
confirmed against a real export (task B3).

## Forecast

`status: "insufficient_history"` below 12 months of stays (`history_months`,
`history_months_needed`): the tab explains, no chart. Otherwise `points[]`:
past months with `actual`, future months with `forecast` and a `low`–`high`
band. The model is chosen by the backend (rolling backtest; best sMAPE per
target and horizon); `accuracy.model` is for a tooltip, never a choice.
