/**
 * Queue §4.3: recommendation-card polish.
 *
 * B1 — the "Suggested range" bounds must round OUTWARD (floor the low bound,
 * ceil the high bound) so the displayed range can only ever be WIDER than
 * the true price_range_low_eur/price_range_high_eur, never narrower. Nearest-
 * euro rounding on each bound independently (the old behavior) can narrow a
 * range: 305.5-306.5 rendered as "306 € – 307 €", silently dropping the
 * 305.5-305.99 slice a hotelier would still read as "not in the range".
 *
 * B2 — a market snapshot backed by only 1-2 comparable hotels must not be
 * presented with the same apparent confidence as a well-populated one (e.g.
 * "Own position 100.0 percentile" reads as authoritative even with 2
 * hotels). The card gets a Greek, zero-emoji small-sample notice and the
 * percentile line is annotated. The comparable-hotel count comes from
 * /api/v1/market/summary's total_hotels for the same filters just sent to
 * the recommendation endpoint — the recommendation payload's own
 * statistics.sample_runs counts scrape RUNS over time, not competitors
 * within a run, so it cannot stand in here (see pricing-page.component.ts
 * loadComparableCompetitors doc comment).
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, OWNED_PROPERTY_ID, ROOM_TYPE_ID, seedBrowserState } from "./helpers";

const ROOM_TYPES = [
  {
    id: ROOM_TYPE_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Double Room with Sea View",
    room_type_category: "double",
    is_active: true,
  },
];

const PRICE_HISTORY = {
  canonical_destination: "faliraki",
  check_in: "2026-09-01",
  check_out: "2026-09-05",
  points: [
    { run_index: 1, observed_at: "2026-08-20T08:00:00Z", hotel_name: "Hotel Alpha", min_price_eur: 95 },
    { run_index: 1, observed_at: "2026-08-20T08:00:00Z", hotel_name: "Hotel Beta", min_price_eur: 118 },
  ],
};

const MARKET_SUMMARY_BASE = {
  destination: "faliraki",
  check_in: "2026-09-01",
  check_out: "2026-09-05",
  total_records: 20,
  total_hotels: 12,
  price_min_eur: 80,
  price_max_eur: 160,
  price_avg_eur: 115,
  price_median_eur: 110,
  avg_review_score: 8.5,
  rooms_left_total: 30,
};

/** A complete, valid PriceStatistics — tests override only what they need. */
function statistics(overrides: Record<string, unknown> = {}): unknown {
  return {
    sample_runs: 4,
    own_reference_price_eur: 115,
    market_median_eur: 110,
    market_p25_eur: 95,
    market_p75_eur: 125,
    own_position_percentile: 60,
    trend_7d_pct: -3.2,
    trend_30d_pct: null,
    lead_time_days: 12,
    statistical_recommendation_eur: 108,
    notes: [],
    ...overrides,
  };
}

/** A complete, valid PriceRecommendation — tests override only what they need. */
function recommendation(overrides: Record<string, unknown> = {}): unknown {
  return {
    recommended_price_eur: 112,
    price_range_low_eur: 98,
    price_range_high_eur: 124,
    confidence: "medium",
    reasoning: "Positioned just above the market median for a strong review score.",
    key_factors: ["Market median EUR 110"],
    source: "agent",
    ...overrides,
  };
}

type MockOptions = {
  statistics?: unknown;
  recommendation?: unknown;
  totalHotels?: number;
  demandSignals?: unknown;
};

async function mockPricingPage(page: Page, options: MockOptions = {}): Promise<void> {
  await seedBrowserState(page);
  // The notification bell opens /ws/alerts on load; accept and ignore it.
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: ROOM_TYPES }),
  );
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) =>
    route.fulfill({ json: { count: 0 } }),
  );
  await page.route(`${API}/api/v1/notifications/ws-ticket`, (route) =>
    route.fulfill({ json: { ticket: "e2e-one-time-ticket", expires_in_seconds: 60 } }),
  );
  await page.route(`${API}/api/v1/market/price-history**`, (route) => route.fulfill({ json: PRICE_HISTORY }));
  await page.route(`${API}/api/v1/market/summary**`, (route) =>
    route.fulfill({
      json: { ...MARKET_SUMMARY_BASE, total_hotels: options.totalHotels ?? MARKET_SUMMARY_BASE.total_hotels },
    }),
  );
  await page.route(`${API}/api/v1/agents/price-recommendation`, (route) =>
    route.fulfill({
      json: {
        statistics: options.statistics ?? statistics(),
        recommendation: options.recommendation ?? recommendation(),
        recommendation_available: true,
        demand_signals: options.demandSignals ?? null,
      },
    }),
  );
}

test("B1: a 305.5-306.5 range renders as 305 € - 307 €, never the narrower 306 € - 307 €", async ({ page }) => {
  await mockPricingPage(page, {
    recommendation: recommendation({
      recommended_price_eur: 306,
      price_range_low_eur: 305.5,
      price_range_high_eur: 306.5,
    }),
  });

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  const range = page.locator(".recommendation-range");
  await expect(range).toBeVisible();
  await expect(range).toContainText("305 €");
  await expect(range).toContainText("307 €");
  // The narrowing bug rounded the low bound UP to 306, silently excluding
  // the true 305.5-305.99 slice of the range.
  // Round 5.1 re-derivation: this negative is purely NUMERIC, so translating
  // the line's prose to «Προτεινόμενο εύρος … ανά βράδυ» cannot make it pass
  // vacuously — the Greek carries no digits, and the positives above still
  // pin the two bounds that must be there.
  await expect(range).not.toContainText("306 €");
});

test("B2: a 2-comparable market snapshot gets a small-sample notice", async ({ page }) => {
  await mockPricingPage(page, {
    statistics: statistics({ own_position_percentile: 100.0, position: { cheaper_than_you: 2, total: 2 } }),
    totalHotels: 2,
  });

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  await expect(page.getByText("Προτεινόμενη τιμή ανά βράδυ")).toBeVisible();
  // The confidence chip maps the enum instead of interpolating it (Round 5.1
  // P-22): a regression that re-interpolated would render the raw API value
  // «medium» on the card the hotelier is asked to trust.
  await expect(page.getByText("Μέτρια βεβαιότητα")).toBeVisible();
  await expect(page.getByText(/Μικρό δείγμα/)).toBeVisible();
  await expect(page.getByText(/2 συγκρίσιμοι ανταγωνιστές/)).toBeVisible();
  // The position line itself is softened, not just a separate banner (Round 6
  // replaced the «P100» percentile with the «Φθηνότερα από εσάς» count).
  await expect(page.getByText(/Φθηνότερα από εσάς: 2 από 2 καταλύματα.*ενδεικτικό/)).toBeVisible();
});

test("B2: a 1-comparable market snapshot says «1 συγκρίσιμος ανταγωνιστής», singular", async ({ page }) => {
  // A single hotel is the smallest small sample there is, and the notice is
  // read by Greek-speaking hoteliers: «μόνο 1 συγκρίσιμοι ανταγωνιστές» is
  // simply ungrammatical, and broken copy on the one screen that sells the
  // product reads as a broken product.
  await mockPricingPage(page, {
    statistics: statistics({ own_position_percentile: 100.0 }),
    totalHotels: 1,
  });

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  await expect(page.getByText("Προτεινόμενη τιμή ανά βράδυ")).toBeVisible();
  await expect(page.getByText(/Μικρό δείγμα: μόνο 1 συγκρίσιμος ανταγωνιστής/)).toBeVisible();
  await expect(page.getByText(/συγκρίσιμοι/)).toHaveCount(0);
});

test("B2: a 3-comparable market snapshot shows no small-sample notice", async ({ page }) => {
  await mockPricingPage(page, {
    statistics: statistics({ own_position_percentile: 66.7 }),
    totalHotels: 3,
  });

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  // Scoped to the card on purpose. The "no recommendation yet" empty state now
  // reads «...για να δείτε την προτεινόμενη τιμή ανά βράδυ...», which CONTAINS
  // this heading verbatim (getByText matches case-insensitive substrings), so
  // an unscoped lookup could go green against the empty state — and the
  // toHaveCount(0) below would then pass vacuously, because the notice it
  // denies only ever renders inside the card.
  await expect(page.locator(".recommendation-card").getByText("Προτεινόμενη τιμή ανά βράδυ")).toBeVisible();
  await expect(page.getByText(/Μικρό δείγμα/)).toHaveCount(0);
});

test("Ferries: the card shows the ferry demand around the check-in, its level on the border", async ({ page }) => {
  await mockPricingPage(page, {
    demandSignals: {
      ferries: {
        level: "high",
        check_in: "2026-09-01",
        window: ["2026-08-31", "2026-09-01"],
        sailings: 8,
        availability_known: 8,
        sold_out: 4,
        sold_out_share_pct: 50,
        min_price_eur: 62,
        options_change_pct: -32,
        price_change_pct: 18,
        compared_with: "2026-08-15",
        sailings_vs_usual_pct: null,
        routes: ["PIRAEUS → RODOS"],
        observed_on: "2026-08-20",
        summary:
          "Πλοία προς RODOS για 31/08–01/09: 8 δρομολόγια, 4 χωρίς θέσεις, από τις 15/08: θέσεις προς πώληση -32%, τιμές εισιτηρίων +18%. Ζήτηση πλοίων: Υψηλή.",
      },
    },
  });

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  const ferry = page.getByTestId("ferry-demand");
  await expect(ferry).toBeVisible();
  await expect(ferry).toContainText("Πλοία");
  await expect(ferry).toContainText("4 χωρίς θέσεις");
  await expect(ferry).toContainText("Ζήτηση πλοίων: Υψηλή");
  await expect(ferry).toHaveClass(/demand-high/);
});

test("Ferries: a mainland destination (no signal) shows no ferry line", async ({ page }) => {
  await mockPricingPage(page);

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  await expect(page.getByText("Προτεινόμενη τιμή ανά βράδυ")).toBeVisible();
  await expect(page.getByTestId("ferry-demand")).toHaveCount(0);
  await expect(page.getByTestId("flight-demand")).toHaveCount(0);
});

test("Flights: the card shows the island airport's arrivals next to the ferries", async ({ page }) => {
  await mockPricingPage(page, {
    demandSignals: {
      ferries: null,
      flights: {
        level: "low",
        check_in: "2026-09-01",
        airport_iata: "RHO",
        island: "Ρόδος",
        travel_dates: ["2026-08-29"],
        arrivals: 36,
        airlines: 11,
        arrivals_before: 40,
        flights_added: -4,
        arrivals_change_pct: -10,
        compared_with: "2026-08-15",
        top_origins: ["Manchester", "London Gatwick"],
        observed_on: "2026-08-22",
        summary:
          "Αφίξεις στο αεροδρόμιο RHO (Ρόδος) Σάβ 29/08 08:00–20:00: 36 πτήσεις από 11 αεροπορικές, κυρίως από Manchester, London Gatwick. Από τις 15/08: -4 πτήσεις (-10%). Ζήτηση πτήσεων: Χαμηλή.",
      },
    },
  });

  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  const flights = page.getByTestId("flight-demand");
  await expect(flights).toBeVisible();
  await expect(flights).toContainText("Πτήσεις");
  await expect(flights).toContainText("-4 πτήσεις (-10%)");
  await expect(flights).toHaveClass(/demand-low/);
  await expect(page.getByTestId("ferry-demand")).toHaveCount(0);
});
