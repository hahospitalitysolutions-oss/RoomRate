export type CurrentUser = {
  account_id: string;
  auth_provider?: string | null;
  auth_subject?: string | null;
  email?: string | null;
  onboarding_complete: boolean;
  owned_property_id?: string | null;
  property_name?: string | null;
  destination?: string | null;
  raw_destination?: string | null;
  canonical_destination?: string | null;
  selected_room_type_category?: string | null;
};

/** Spec §3.6: "same" = the package is in the baseline room's comparable pool, "similar" = anything else (incl. unknown). */
export type CategoryMatch = "same" | "similar";
/** Agents (spec Α.4): where the match scores of a `match_room=true` read came from. */
export type MatchSource = "agent" | "statistical";
/** `/api/v1/competitors/` sort keys; "distance" is new in Round 6. */
export type CompetitorSort = "match" | "price" | "distance";

/** Written by the scraper's ProgressReporter into `result_summary.progress` while a job runs (spec §3.5). */
export type ScrapeJobProgress = {
  stage: "scout" | "deep_crawl" | "persist" | string;
  done?: number | null;
  total?: number | null;
  destinations_total?: number | null;
  hotels_found?: number | null;
  hotels_in_radius?: number | null;
  updated_at?: string | null;
};

export type NearbyDestinationsResponse = { destination: string; canonical: string; nearby: string[] };

/** `GET /api/v1/maps/own-property` (spec §3.6). Coordinates may be null: then no «Εσείς» marker is drawn. */
export type OwnPropertyMapInfo = {
  display_name: string;
  latitude: number | null;
  longitude: number | null;
  radius_km: number | null;
  price_per_night_eur: number | null;
  room_type: string | null;
  booking_url: string | null;
};

export type CompetitorMapMarker = {
  hotel_name: string;
  property_id?: string | null;
  room_package_id?: string | null;
  room_type: string;
  room_type_category: string;
  property_type: string;
  latitude: number;
  longitude: number;
  price_per_night_eur: number;
  review_score: number;
  review_count: number;
  rooms_left: number;
  // Round 6 (spec §3.6), optional so the page keeps working against an older API.
  distance_km?: number | null;
  category_match?: CategoryMatch | null;
  booking_url?: string | null;
  /** The matching agent's verdict for the selected room; only sent with `comparable_only=false`. */
  comparable?: boolean | null;
};

export type OwnedPropertyRoomType = {
  id: string;
  owned_property_id: string;
  room_type: string;
  room_type_category: string;
  sample_meals?: string | null;
  sample_free_cancellation?: string | null;
  sample_facilities?: string | null;
  sample_price_per_night_eur?: number | null;
  is_active: boolean;
};

export type SelectedRoomTypeResponse = {
  owned_property_id: string;
  selected_room_type_category: string;
  onboarding_complete: boolean;
};

export type PropertyCandidate = {
  candidate_key: string;
  display_name: string;
  booking_url: string;
  city: string;
  address: string;
  country?: string | null;
  property_type?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  stars?: number | null;
  review_score?: number | null;
  review_count?: number | null;
};

export type ScrapeJobResponse = {
  id: string;
  account_id: string;
  owned_property_id?: string | null;
  job_type: string;
  room_type_category?: string | null;
  destination: string;
  raw_destination?: string | null;
  canonical_destination?: string | null;
  check_in: string;
  check_out: string;
  adults: number;
  children: number;
  rooms: number;
  filters_payload: Record<string, unknown>;
  status: "queued" | "running" | "completed" | "failed" | string;
  requested_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at?: string | null;
  scrape_runs_count: number;
  result_summary?: ScrapeResultSummary | null;
  // Round 6 (spec §3.1): what the search sent, so a restore can show it again.
  nearby_destinations?: string[] | null;
  radius_km?: number | null;
};

export type ScrapeResultFilterCount = {
  before: number;
  after: number;
};

export type ScrapeResultSummary = {
  version: number;
  rows_seen: number;
  filter_counts: Record<string, ScrapeResultFilterCount>;
  rows_written: number;
  // While the job RUNS the summary is `{ progress }` alone (the four fields above
  // only arrive with completion); the poll loop reads nothing but `progress`.
  progress?: ScrapeJobProgress | null;
  warnings?: string[] | null;
};

export type AutomaticSetupResponse = {
  owned_property_id: string;
  selected_candidate: PropertyCandidate;
  discovery_job: ScrapeJobResponse;
};

export type OwnedPropertyOnboardingResponse = {
  owned_property_id: string;
  discovery_job: ScrapeJobResponse;
};

export type TrackedCompetitorListResponse = {
  owned_property_id: string;
  room_type_category: string;
  competitors: Array<{
    property_id: string;
    room_package_id?: string | null;
  }>;
};

export type MarketSummary = {
  destination?: string | null;
  check_in?: string | null;
  check_out?: string | null;
  total_records: number;
  total_hotels: number;
  price_min_eur: number;
  price_max_eur: number;
  price_avg_eur: number;
  price_median_eur: number;
  avg_review_score: number;
  rooms_left_total: number;
  // Round 6 (spec §3.6): hotels split by category match; absent on an older API.
  same_category_hotels?: number | null;
  similar_hotels?: number | null;
};

/**
 * Rate plans (spec §4): what distinguishes this package from the other
 * packages of the same room. Every field is null when the scrape did not
 * capture it; the whole object is null on rows written before the rate-plan
 * columns existed.
 */
export type CompetitorRatePlan = {
  /** What the guest actually pays per night; falls back to price_per_night_eur when null. */
  discounted_price_per_night_eur: number | null;
  discount_pct: number | null;
  /** Cleaned Booking text, e.g. «-8%». */
  discount_label: string | null;
  has_genius_discount: boolean;
  /** Raw Booking value: "free_cancellation" | "non_refundable" | other. */
  cancellation_type: string | null;
  /** Greek Booking label, e.g. «Πληρωμή online». */
  payment_label: string | null;
};

export type CompetitorPackage = {
  room_type: string;
  price_per_night_eur: number;
  price_total_eur: number;
  meals: string;
  free_cancellation: string;
  rooms_left: number;
  /** 0-100 similarity to the owned room; only set when match_room=true. */
  match_score?: number | null;
  room_type_category?: string | null;
  category_match?: CategoryMatch | null;
  /** Rate plans (spec §4), optional so the page keeps working against an older API. */
  rate_plan?: CompetitorRatePlan | null;
  /** Agents (spec Α.4): the agent's one-line Greek justification; null on statistical rows. */
  match_reasoning?: string | null;
  /** The matching agent's verdict for the selected room; only sent with `comparable_only=false`. */
  comparable?: boolean | null;
};

export type Competitor = {
  hotel_name: string;
  city: string;
  address: string;
  property_type: string;
  latitude: number;
  longitude: number;
  stars: number;
  review_score: number;
  review_count: number;
  price_min_eur: number;
  price_max_eur: number;
  rooms_left: number;
  /** Best package match_score for the hotel; only set when match_room=true. */
  best_match_score?: number | null;
  packages: CompetitorPackage[];
  // Round 6 (spec §3.6), optional so the page keeps working against an older API.
  distance_km?: number | null;
  category_match?: CategoryMatch | null;
  booking_url?: string | null;
  /** Agents (spec Α.4): where this row's match scores come from; absent on an older API. */
  match_source?: MatchSource | null;
};

/**
 * Agents (spec Α.1): manual re-run of the room-matching agent for one
 * (completed job, reference room) pair. 429 when the daily quota is spent.
 */
export type RoomMatchRunRequest = {
  scrape_job_id: string;
  owned_room_type_id: string;
};

export type RoomMatchRunResponse = {
  status: "completed" | "error" | "skipped";
  matches_written: number;
  source: "agent";
};
