/**
 * «Απόδοση»: the contract between the performance API (api/routers/performance.py,
 * built by the backend team) and the performance page (pages/performance/,
 * built by the frontend team). docs/performance/API.md describes each call;
 * public/mock/performance/*.json holds one example answer per call, the data
 * the page runs on until the backend answers (services/performance-api.service.ts).
 *
 * Change this file only in a small «contract» PR that both sides approve,
 * together with API.md and the mock files.
 *
 * Every call takes `owned_property_id`. Amounts are euros, rounded to cents;
 * percentages are 0–100 with one decimal; months are "YYYY-MM"; dates are
 * "YYYY-MM-DD" stay dates (a night belongs to the date it starts).
 */

/** Gross: what the guest paid (VAT and resilience fee included). Net: minus VAT, fee and channel commission. */
export type AmountBasis = "gross" | "net";

/** Query of every analysis call except /status, /pace and /forecast. */
export type PerformanceFilters = {
  date_from: string;
  date_to: string;
  /** An owned room type id; omitted = the whole property. */
  room_type_id?: string | null;
  amounts: AmountBasis;
  /** "last_year" adds the same dates a year earlier to the answer. */
  compare: "last_year" | "none";
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/status
// ---------------------------------------------------------------------------

export type PerformanceRoomType = {
  id: string;
  label: string;
  /** null until the owner enters it (Booking.com export) or uploads the «Δωμάτια» sheet. */
  rooms: number | null;
};

export type ImportSummary = {
  id: string;
  format: ImportFormat;
  filename: string;
  committed_at: string;
  bookings_imported: number;
};

export type PerformanceStatus = {
  has_data: boolean;
  bookings: number;
  first_stay_date: string | null;
  last_stay_date: string | null;
  last_import: ImportSummary | null;
  /** false when a room type has no room count: occupancy and RevPAR cannot be computed. */
  inventory_complete: boolean;
  room_types: PerformanceRoomType[];
  /** Seasons (years) with stays, newest first: the period filter's presets. */
  seasons: number[];
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/summary   (filters)
// ---------------------------------------------------------------------------

export type PerformanceKpis = {
  /** null when inventory is incomplete. */
  occupancy_pct: number | null;
  adr_eur: number | null;
  revpar_eur: number | null;
  revenue_eur: number;
  room_nights_sold: number;
  room_nights_available: number | null;
  bookings: number;
  cancellations_pct: number | null;
  avg_length_of_stay: number | null;
  avg_lead_time_days: number | null;
  commission_eur: number;
};

export type PerformanceSummary = {
  period: { date_from: string; date_to: string };
  amounts: AmountBasis;
  kpis: PerformanceKpis;
  /** Same dates a year earlier; null when compare=none or no data then. */
  last_year: PerformanceKpis | null;
  delta: {
    occupancy_pts: number | null;
    adr_pct: number | null;
    revpar_pct: number | null;
    revenue_pct: number | null;
  } | null;
  /** Median competitor price for the same stays, from RoomRate's market data; null without it. */
  market_median_eur: number | null;
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/monthly   (filters)
// ---------------------------------------------------------------------------

export type MonthKpis = {
  month: string;
  occupancy_pct: number | null;
  adr_eur: number | null;
  revpar_eur: number | null;
  revenue_eur: number;
  bookings: number;
  room_nights_sold: number;
  room_nights_available: number | null;
  room_nights_unsold: number | null;
  /** Days the property (or the filtered room type) was closed: they are not «unsold». */
  closed_days: number;
  market_median_eur: number | null;
  last_year: {
    occupancy_pct: number | null;
    adr_eur: number | null;
    revpar_eur: number | null;
    revenue_eur: number;
  } | null;
};

export type PerformanceMonthly = {
  amounts: AmountBasis;
  months: MonthKpis[];
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/channels   (filters)
// ---------------------------------------------------------------------------

export type ChannelTotal = {
  channel: string;
  bookings: number;
  room_nights: number;
  revenue_eur: number;
  commission_eur: number;
  /** Share of room nights sold. */
  share_pct: number;
};

export type PerformanceChannels = {
  amounts: AmountBasis;
  totals: ChannelTotal[];
  /** Revenue per channel per month, for the stacked chart. */
  monthly: { month: string; revenue_by_channel: Record<string, number> }[];
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/countries   (filters)
// ---------------------------------------------------------------------------

export type CountryTotal = {
  /** Greek country name; "Άγνωστη" when the file had none, "Λοιπές" for the rest after the top 10. */
  country: string;
  bookings: number;
  revenue_eur: number;
  share_pct: number;
};

export type PerformanceCountries = {
  amounts: AmountBasis;
  countries: CountryTotal[];
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/room-types   (filters; room_type_id ignored)
// ---------------------------------------------------------------------------

export type RoomTypePerformance = {
  id: string;
  label: string;
  rooms: number | null;
  occupancy_pct: number | null;
  adr_eur: number | null;
  revpar_eur: number | null;
  revenue_eur: number;
  revenue_share_pct: number;
  bookings: number;
  room_nights_sold: number;
  cancellations_pct: number | null;
  market_median_eur: number | null;
};

export type PerformanceRoomTypes = {
  amounts: AmountBasis;
  room_types: RoomTypePerformance[];
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/stay-patterns   (filters)
// ---------------------------------------------------------------------------

export type LengthOfStayBucket = "1" | "2-3" | "4-5" | "6-9" | "10+";
export type LeadTimeBucket = "0-7" | "8-30" | "31-60" | "61+";

export type PerformanceStayPatterns = {
  length_of_stay: { bucket: LengthOfStayBucket; bookings: number; share_pct: number }[];
  lead_time: { bucket: LeadTimeBucket; bookings: number; share_pct: number }[];
  /** Bookings per (length, lead time): the heatmap. Every pair is present, zeros included. */
  matrix: { length_bucket: LengthOfStayBucket; lead_bucket: LeadTimeBucket; bookings: number }[];
  /** Average days between booking and arrival, by arrival month. */
  monthly_lead_time: { month: string; avg_lead_time_days: number | null }[];
  avg_length_of_stay: number | null;
  avg_lead_time_days: number | null;
  avg_guests: number | null;
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/pace?season=2027
// ---------------------------------------------------------------------------

export type PerformancePace = {
  season: number;
  /** Today: the bookings on the books when the call was made. */
  as_of: string;
  months: {
    month: string;
    otb_occupancy_pct: number | null;
    otb_room_nights: number;
    /** Occupancy on the books for the same month a year earlier, on the same day of the year. */
    last_year_same_day_pct: number | null;
  }[];
  pickup_7d: { bookings: number; room_nights: number };
};

// ---------------------------------------------------------------------------
// GET /api/v1/performance/forecast?target=revenue&horizon=12
// ---------------------------------------------------------------------------

export type ForecastTarget = "revenue" | "bookings" | "room_nights";

export type PerformanceForecast = {
  /** "insufficient_history": fewer than `history_months_needed` months of stays. */
  status: "ready" | "insufficient_history";
  history_months: number;
  history_months_needed: number;
  target: ForecastTarget;
  horizon_months: number;
  amounts: AmountBasis;
  /** Plain-language accuracy for the owner, e.g. «Τους τελευταίους 6 μήνες οι προβλέψεις απείχαν κατά μέσο όρο 8% από τα πραγματικά.» */
  accuracy_sentence: string | null;
  /** Internal, for an «advanced» tooltip only: never a choice for the owner. */
  accuracy: { smape_pct: number; mae: number; bias: number; model: string } | null;
  points: {
    month: string;
    actual: number | null;
    forecast: number | null;
    low: number | null;
    high: number | null;
  }[];
};

// ---------------------------------------------------------------------------
// Imports: GET /template.xlsx, POST /imports, PUT /imports/{id}/mapping,
// POST /imports/{id}/commit, GET /imports, DELETE /bookings
// ---------------------------------------------------------------------------

/** "template": RoomRate's Excel; "booking": the Booking.com extranet reservations export as is. */
export type ImportFormat = "template" | "booking";

export type BookingStatus = "confirmed" | "cancelled" | "no_show";

export type ImportedBookingRow = {
  booking_ref: string;
  booked_on: string | null;
  arrival: string | null;
  departure: string | null;
  nights: number | null;
  room_label: string;
  room_type_id: string | null;
  rooms: number;
  gross_eur: number | null;
  commission_eur: number | null;
  net_eur: number | null;
  channel: string;
  status: BookingStatus;
  /** Set on a row that will not be imported. */
  error: string | null;
};

export type ImportPreview = {
  id: string;
  format: ImportFormat;
  filename: string;
  rows_total: number;
  /** Bookings not seen before / with a booking_ref already stored (they are replaced). */
  new: number;
  updated: number;
  cancelled: number;
  errors: { row: number; booking_ref: string | null; message: string }[];
  /** Every room label of the file with RoomRate's room type it maps to (null = not mapped yet). */
  mapping: { label: string; bookings: number; room_type_id: string | null }[];
  /** First rows as read, guests' personal data never included. */
  sample: ImportedBookingRow[];
  /** true when every label is mapped (or ignored) and the file can be committed. */
  ready: boolean;
};

/** PUT /imports/{id}/mapping. room_type_id null with ignore=true leaves that label's bookings out. */
export type ImportMappingUpdate = {
  mapping: { label: string; room_type_id: string | null; ignore?: boolean }[];
};

/** POST /imports/{id}/commit body. */
export type ImportCommitRequest = { skip_errors: boolean };

export type ImportCommitResult = {
  imported: number;
  updated: number;
  skipped: number;
  status: PerformanceStatus;
};

// ---------------------------------------------------------------------------
// GET/PUT /inventory, /closures, /revenue-settings
// ---------------------------------------------------------------------------

export type RoomInventory = {
  room_types: {
    room_type_id: string;
    label: string;
    rooms: number | null;
    /** Only when the count changed within the year. */
    valid_from: string | null;
    valid_to: string | null;
  }[];
};

export type PropertyClosures = {
  closures: {
    date_from: string;
    date_to: string;
    /** null = the whole property. */
    room_type_id: string | null;
    /** null = every room of the type (or of the property). */
    rooms: number | null;
    reason: string | null;
  }[];
};

export type RevenueSettings = {
  /** VAT on accommodation, % (13 in Greece). */
  vat_pct: number;
  /** Climate resilience fee per room and night, €. */
  resilience_fee_eur: number;
  /** true when the guest's price includes the fee. */
  fee_included_in_price: boolean;
};
