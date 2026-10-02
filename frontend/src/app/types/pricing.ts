/**
 * Where the owner's reference price came from (Round 6 §5.2): the property's
 * own live Booking row in the latest completed run, or the onboarding sample.
 */
export type OwnPriceSource = "booking_live" | "onboarding_sample";

/** How many hotels of the current market undercut the owner's reference price. */
export type PricePosition = {
  cheaper_than_you: number;
  total: number;
};

/**
 * Which hotels of the latest run back the statistics (Round 6 §5.2).
 *
 * `same_category` hotels offer the baseline category's comparable pool;
 * `similar` hotels only offer other (non-single) categories. They are pulled
 * in only when fewer than 5 same-category hotels exist, and `used` says
 * whether that happened.
 */
export type PriceStatsScope = {
  same_category: number;
  similar: number;
  /** "agent": the matching agent's comparable set for the owner's room backs the statistics. */
  used: "same" | "same_plus_similar" | "agent";
  /** With `used === "agent"`: how many hotels the agent judged comparable. */
  comparable?: number | null;
  /**
   * Rate plans (spec §4): "matched" when the per-hotel floor was computed
   * within the owner's own cancellation class, "all" when no package shared
   * it and the floor fell back to every package. Absent on an older API.
   */
  cancellation_class?: "matched" | "all";
};

export type PriceStatistics = {
  sample_runs: number;
  // Distinct calendar days with a completed search (drives the confidence).
  sample_days: number;
  own_reference_price_eur?: number | null;
  market_median_eur?: number | null;
  market_p25_eur?: number | null;
  market_p75_eur?: number | null;
  own_position_percentile?: number | null;
  // Always sent; null without an own reference price or without a market.
  position: PricePosition | null;
  stats_scope: PriceStatsScope | null;
  trend_7d_pct?: number | null;
  trend_30d_pct?: number | null;
  lead_time_days: number;
  statistical_recommendation_eur?: number | null;
  notes: string[];
};

export type PriceRecommendation = {
  recommended_price_eur: number;
  price_range_low_eur: number;
  price_range_high_eur: number;
  confidence: "low" | "medium" | "high";
  reasoning: string;
  key_factors: string[];
  source: "agent" | "statistical";
};

export type PriceRecommendationResponse = {
  statistics: PriceStatistics;
  // null (with recommendation_available=false) when the market history and
  // own reference price cannot support any grounded number — the UI explains
  // via statistics.notes instead of showing a fabricated €0.
  recommendation: PriceRecommendation | null;
  recommendation_available: boolean;
  audit_id?: string | null;
  generated_at?: string | null;
  cached?: boolean;
  model_version?: string | null;
  prompt_version?: string | null;
  // Always sent; null when the property has neither a live Booking row nor a
  // sample price.
  own_price_source: OwnPriceSource | null;
};

export type PriceHistoryPoint = {
  run_index: number;
  observed_at?: string | null;
  property_id?: string | null;
  hotel_name: string;
  min_price_eur?: number | null;
};

export type PriceHistorySeries = {
  canonical_destination?: string | null;
  check_in: string;
  check_out: string;
  points: PriceHistoryPoint[];
};
