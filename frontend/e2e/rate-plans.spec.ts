/**
 * Rate plans (spec §5-§6): the same room's several prices on the map cards,
 * and the pricing page's like-for-like cancellation line.
 *
 * Hermetic like map-round6.spec.ts: every endpoint either page calls is
 * mocked with a `**` glob, the wildcard FIRST (Playwright matches LIFO, so
 * every specific route below it wins). Fixture values are deliberately
 * non-default (87.5 €, «-12%», Genius true, three plans of one room, a
 * matched cancellation class): a passing assertion proves the value came
 * from here, not from a component default.
 */
import { expect, Page, test } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  JOB_ID,
  mockMapbox,
  OWNED_PROPERTY_ID,
  ROOM_TYPE_ID,
  seedBrowserState,
} from "./helpers";

const PROP_OMEGA = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const PROP_PALIO = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
const PROP_SELINI = "ffffffff-ffff-ffff-ffff-ffffffffffff";
// The job a «Εύρεση ανταγωνιστών» click creates, as opposed to JOB_ID (restored on load).
const NEW_JOB_ID = "22222222-2222-2222-2222-2222222222dd";

// Ωμέγα carries the new rate-plan data, Σελήνη a SINGLE discounted plan;
// Παλαιό is an old run (rate_plan null).
const MAP_MARKERS = [
  { hotel_name: "Ξενοδοχείο Ωμέγα", property_id: PROP_OMEGA, room_package_id: null,
    room_type: "Δίκλινο με Θέα Θάλασσα", room_type_category: "double", property_type: "hotel",
    latitude: 36.34, longitude: 28.2, price_per_night_eur: 87.5, review_score: 8.7, review_count: 214,
    rooms_left: 4, distance_km: 0.9, category_match: "same", booking_url: "https://www.booking.com/hotel/gr/omega.html" },
  { hotel_name: "Ξενοδοχείο Παλαιό", property_id: PROP_PALIO, room_package_id: null,
    room_type: "Standard Δίκλινο", room_type_category: "double", property_type: "hotel",
    latitude: 36.36, longitude: 28.22, price_per_night_eur: 95, review_score: 7.9, review_count: 63,
    rooms_left: 3, distance_km: 2.3, category_match: "same", booking_url: null },
  { hotel_name: "Βίλα Σελήνη", property_id: PROP_SELINI, room_package_id: null,
    room_type: "Σουίτα με Τζακούζι", room_type_category: "double", property_type: "villa",
    latitude: 36.33, longitude: 28.19, price_per_night_eur: 126, review_score: 9.2, review_count: 41,
    rooms_left: 1, distance_km: 1.7, category_match: "same", booking_url: null },
];

/**
 * The `/api/v1/competitors/` rows (the match list). Ωμέγα's ONE room has
 * three plans: a Genius non-refundable one discounted to 87.5, a free
 * cancellation one without a discounted price (falls back to 110), and one
 * with an UNKNOWN cancellation type (no chip may be invented for it).
 */
const COMPETITORS = [
  {
    hotel_name: "Ξενοδοχείο Ωμέγα", city: "Faliraki", address: "", property_type: "hotel",
    latitude: 36.34, longitude: 28.2, stars: 4, review_score: 8.7, review_count: 214,
    price_min_eur: 87.5, price_max_eur: 110, rooms_left: 4, distance_km: 0.9,
    category_match: "same", booking_url: "https://www.booking.com/hotel/gr/omega.html",
    best_match_score: 88,
    packages: [
      { room_type: "Δίκλινο με Θέα Θάλασσα", price_per_night_eur: 100, price_total_eur: 400,
        meals: "Πρωινό", free_cancellation: "No", rooms_left: 2, match_score: 88,
        room_type_category: "double", category_match: "same",
        rate_plan: { discounted_price_per_night_eur: 87.5, discount_pct: 12.5, discount_label: "-12%",
          has_genius_discount: true, cancellation_type: "non_refundable", payment_label: "Πληρωμή online" } },
      { room_type: "Δίκλινο με Θέα Θάλασσα", price_per_night_eur: 110, price_total_eur: 440,
        meals: "", free_cancellation: "Yes", rooms_left: 2, match_score: 82,
        room_type_category: "double", category_match: "same",
        rate_plan: { discounted_price_per_night_eur: null, discount_pct: null, discount_label: null,
          has_genius_discount: false, cancellation_type: "free_cancellation", payment_label: null } },
      { room_type: "Δίκλινο με Θέα Θάλασσα", price_per_night_eur: 104, price_total_eur: 416,
        meals: "Ημιδιατροφή", free_cancellation: "No", rooms_left: 1, match_score: 76,
        room_type_category: "double", category_match: "same",
        rate_plan: { discounted_price_per_night_eur: 104, discount_pct: null, discount_label: null,
          has_genius_discount: false, cancellation_type: "special_conditions", payment_label: null } },
    ],
  },
  {
    hotel_name: "Ξενοδοχείο Παλαιό", city: "Faliraki", address: "", property_type: "hotel",
    latitude: 36.36, longitude: 28.22, stars: 3, review_score: 7.9, review_count: 63,
    price_min_eur: 95, price_max_eur: 120, rooms_left: 3, distance_km: 2.3,
    category_match: "same", booking_url: null,
    best_match_score: 64,
    // TWO packages of ONE room, both without rate-plan data: the range and
    // the plans button must NOT appear — old rows render exactly as before.
    packages: [
      { room_type: "Standard Δίκλινο", price_per_night_eur: 95, price_total_eur: 380,
        meals: "", free_cancellation: "Yes", rooms_left: 3, match_score: 64,
        room_type_category: "double", category_match: "same", rate_plan: null },
      { room_type: "Standard Δίκλινο", price_per_night_eur: 120, price_total_eur: 480,
        meals: "", free_cancellation: "No", rooms_left: 1, match_score: 58,
        room_type_category: "double", category_match: "same", rate_plan: null },
    ],
  },
  {
    hotel_name: "Βίλα Σελήνη", city: "Faliraki", address: "", property_type: "villa",
    latitude: 36.33, longitude: 28.19, stars: 4, review_score: 9.2, review_count: 41,
    price_min_eur: 126, price_max_eur: 126, rooms_left: 1, distance_km: 1.7,
    category_match: "same", booking_url: null,
    best_match_score: 71,
    // ONE package whose plan carries a discount: the card must show the
    // guest-visible 126 (not the 140 main column) with the plan's chips,
    // and neither a range nor an expand button (spec §5, review decision).
    packages: [
      { room_type: "Σουίτα με Τζακούζι", price_per_night_eur: 140, price_total_eur: 560,
        meals: "Πρωινό", free_cancellation: "Yes", rooms_left: 1, match_score: 71,
        room_type_category: "double", category_match: "same",
        rate_plan: { discounted_price_per_night_eur: 126, discount_pct: 10, discount_label: "-10%",
          has_genius_discount: true, cancellation_type: "free_cancellation", payment_label: null } },
    ],
  },
];

const COMPLETED_JOB = {
  id: JOB_ID,
  account_id: CURRENT_USER.account_id,
  owned_property_id: OWNED_PROPERTY_ID,
  job_type: "competitor_search",
  room_type_category: "double",
  destination: "Faliraki",
  raw_destination: "Faliraki, Rhodes",
  canonical_destination: "faliraki",
  check_in: "2030-06-01",
  check_out: "2030-06-05",
  adults: 3,
  children: 1,
  rooms: 2,
  filters_payload: { limit: 12 },
  nearby_destinations: ["Ιξιά"],
  radius_km: 7,
  status: "completed",
  requested_at: "2030-05-01T10:00:00Z",
  started_at: "2030-05-01T10:00:05Z",
  finished_at: "2030-05-01T10:06:00Z",
  attempt_count: 1,
  max_attempts: 3,
  scrape_runs_count: 1,
  result_summary: { version: 1, rows_seen: 9, filter_counts: {}, rows_written: 5, warnings: [] as string[] },
};

const MARKET_SUMMARY = {
  destination: "faliraki", check_in: "2030-06-01", check_out: "2030-06-05",
  total_records: 6, total_hotels: 3, same_category_hotels: 3, similar_hotels: 0,
  price_min_eur: 87.5, price_max_eur: 126, price_avg_eur: 106, price_median_eur: 104,
  avg_review_score: 8.6, rooms_left_total: 8,
};

const OWN_PROPERTY = {
  display_name: "E2E Test Hotel", latitude: 36.345, longitude: 28.205, radius_km: 7,
  price_per_night_eur: 92, room_type: "Double Room with Sea View", booking_url: "https://www.booking.com/hotel/gr/e2e.html",
};

const PRICE_HISTORY = {
  canonical_destination: "faliraki",
  check_in: "2030-06-01",
  check_out: "2030-06-05",
  points: [
    { run_index: 1, observed_at: "2026-09-28T10:00:00Z", hotel_name: "Ξενοδοχείο Ωμέγα", min_price_eur: 87.5 },
  ],
};

/** A valid recommendation response whose statistics carry the given stats_scope. */
function pricingResponse(scope: Record<string, unknown> | null): Record<string, unknown> {
  return {
    statistics: {
      sample_runs: 4, sample_days: 3, own_reference_price_eur: 96,
      market_median_eur: 104, market_p25_eur: 90, market_p75_eur: 126,
      own_position_percentile: 58, position: { cheaper_than_you: 5, total: 9 },
      stats_scope: scope, trend_7d_pct: 1.5, trend_30d_pct: null, lead_time_days: 17,
      statistical_recommendation_eur: 108, notes: [],
    },
    recommendation: {
      recommended_price_eur: 108, price_range_low_eur: 98, price_range_high_eur: 118,
      confidence: "medium", reasoning: "Στατιστική σύσταση για το σενάριο των πλάνων τιμών.",
      key_factors: [], source: "statistical",
    },
    recommendation_available: true,
    audit_id: "66666666-6666-6666-6666-6666666666aa",
    generated_at: "2026-09-29T09:00:00Z",
    cached: false,
    model_version: "statistical-v1",
    prompt_version: "2026-09-15.v2",
    own_price_source: "booking_live",
  };
}

type RatePlansOptions = {
  recommendation?: Record<string, unknown>;
  /** Every `/competitors/` (match list) read, so a test can await a RE-load. */
  competitorReads?: string[];
};

/** Mock every endpoint the map and pricing pages call (the map-round6 list plus the pricing pair). */
async function mockRatePlansApi(page: Page, options: RatePlansOptions = {}): Promise<void> {
  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO: the wildcard FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [{ id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID,
      room_type: "Double Room with Sea View", room_type_category: "double", is_active: true }] }));
  await page.route(`${API}/api/v1/notifications/unread-count**`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) =>
    route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby: ["Ιξιά"] } }));
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => route.fulfill({ json: OWN_PROPERTY }));
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => route.fulfill({ json: MAP_MARKERS }));
  await page.route(`${API}/api/v1/competitors/**`, (route) => {
    options.competitorReads?.push(new URL(route.request().url()).pathname);
    return route.fulfill({ json: COMPETITORS });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: MARKET_SUMMARY }));
  // An empty facility list: the page falls back to its own twelve options.
  await page.route(`${API}/api/v1/market/amenities**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/market/price-history**`, (route) => route.fulfill({ json: PRICE_HISTORY }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    const request = route.request();
    // A «Εύρεση ανταγωνιστών» click: queued at once, completed on the first poll.
    if (request.method() === "POST") {
      return route.fulfill({ json: { ...COMPLETED_JOB, id: NEW_JOB_ID, status: "queued",
        started_at: null, finished_at: null, result_summary: null } });
    }
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: [COMPLETED_JOB] });
    }
    if (path.endsWith(NEW_JOB_ID)) {
      return route.fulfill({ json: { ...COMPLETED_JOB, id: NEW_JOB_ID } });
    }
    return route.fulfill({ json: COMPLETED_JOB });
  });
  await page.route(`${API}/api/v1/agents/price-recommendation**`, (route) =>
    route.fulfill({ json: options.recommendation ?? pricingResponse(null) }));
}

/** Restore the completed search and open the match list, where packages render. */
async function openMatchedCards(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.locator(".match-controls .checkbox-row", { hasText: "Ταίριασμα με το δικό μου δωμάτιο" })
    .locator("input[type=checkbox]").check();
  await expect(page.locator(".matched-card")).toHaveCount(3);
}

test("a room with several rate plans collapses to a range and opens into labelled rows", async ({ page }) => {
  await mockRatePlansApi(page);
  await openMatchedCards(page);

  const card = page.locator(".matched-card", { hasText: "Ξενοδοχείο Ωμέγα" });
  // The range spans the EFFECTIVE prices: 87.5 (discounted) to 110 (no
  // discounted price on the plan, so its main price counts). One format
  // everywhere on the card: fractional keeps exactly two decimals, whole
  // numbers keep none — the header min says 87,50 € too, never a rounded 88 €.
  await expect(card.getByTestId("room-price-range")).toHaveText("από 87,50 € έως 110 €");
  await expect(card).toContainText("87,50 €–110 €");
  await expect(card.getByText("88 €")).toHaveCount(0);
  // Collapsed: no plan rows, no chips yet.
  await expect(card.getByTestId("rate-plan-list")).toHaveCount(0);
  await expect(card.getByText("Genius")).toHaveCount(0);

  await card.getByRole("button", { name: "Πλάνα τιμών" }).click();
  const rows = card.getByTestId("rate-plan-row");
  await expect(rows).toHaveCount(3);
  // Genius plan: discounted price with its cents, every chip, the meals text.
  await expect(rows.nth(0)).toContainText("87,50 €");
  await expect(rows.nth(0).locator(".rate-plan-chip"))
    .toHaveText(["Genius", "-12%", "Μη επιστρέψιμη", "Πληρωμή online"]);
  await expect(rows.nth(0)).toContainText("Πρωινό");
  // Free-cancellation plan without a discounted price: the main 110 shows.
  await expect(rows.nth(1)).toContainText("110 €");
  await expect(rows.nth(1).locator(".rate-plan-chip")).toHaveText(["Δωρεάν ακύρωση"]);
  // Unknown cancellation type: no chip is invented; the meals text still shows.
  await expect(rows.nth(2)).toContainText("104 €");
  await expect(rows.nth(2).locator(".rate-plan-chip")).toHaveCount(0);
  await expect(rows.nth(2)).toContainText("Ημιδιατροφή");

  // The same button closes the list again.
  await card.getByRole("button", { name: "Πλάνα τιμών" }).click();
  await expect(card.getByTestId("rate-plan-list")).toHaveCount(0);
});

test("packages without rate-plan data render as plain rows, without range or chips", async ({ page }) => {
  await mockRatePlansApi(page);
  await openMatchedCards(page);

  // Παλαιό has TWO packages of one room, but no rate-plan data (an old run):
  // exactly today's rendering — one plain row per package, nothing collapsed.
  const card = page.locator(".matched-card", { hasText: "Ξενοδοχείο Παλαιό" });
  const rows = card.locator(".matched-packages .card-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("Standard Δίκλινο");
  await expect(rows.nth(0)).toContainText("95 €");
  await expect(rows.nth(1)).toContainText("120 €");
  await expect(card.getByTestId("room-price-range")).toHaveCount(0);
  await expect(card.getByRole("button", { name: "Πλάνα τιμών" })).toHaveCount(0);
  await expect(card.locator(".rate-plan-chip")).toHaveCount(0);
});

test("the price-origin footnote appears exactly once under the results", async ({ page }) => {
  await mockRatePlansApi(page);
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

  await expect(page.getByText("Τιμές επίσημης ιστοσελίδας (desktop, χωρίς σύνδεση)")).toHaveCount(1);
});

test("a lone package with a rate plan shows its discounted price and chips, without an expander", async ({ page }) => {
  await mockRatePlansApi(page);
  await openMatchedCards(page);

  const card = page.locator(".matched-card", { hasText: "Βίλα Σελήνη" });
  const row = card.getByTestId("single-plan-row");
  // The guest-visible 126 renders — the pre-discount 140 main column never does.
  await expect(row).toContainText("126 €");
  await expect(card.getByText("140 €")).toHaveCount(0);
  await expect(row.locator(".rate-plan-chip")).toHaveText(["Genius", "-10%", "Δωρεάν ακύρωση"]);
  await expect(row).toContainText("Πρωινό");
  // Nothing to collapse behind one plan: no range, no button.
  await expect(card.getByTestId("room-price-range")).toHaveCount(0);
  await expect(card.getByRole("button", { name: "Πλάνα τιμών" })).toHaveCount(0);
});

test("a new search lands with every plans list collapsed again", async ({ page }) => {
  const competitorReads: string[] = [];
  await mockRatePlansApi(page, { competitorReads });
  await openMatchedCards(page);

  const card = page.locator(".matched-card", { hasText: "Ξενοδοχείο Ωμέγα" });
  await card.getByRole("button", { name: "Πλάνα τιμών" }).click();
  await expect(card.getByTestId("rate-plan-list")).toBeVisible();
  const readsBeforeSearch = competitorReads.length;

  // Re-run the search: the restored page keeps its filters behind «Φίλτρα».
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();

  // Wait for the NEW match load to land, then check the re-rendered card:
  // same hotel name, same room, but a fresh row set — it must start closed.
  await expect.poll(() => competitorReads.length).toBeGreaterThan(readsBeforeSearch);
  await expect(card.getByTestId("room-price-range")).toBeVisible();
  await expect(card.getByTestId("rate-plan-list")).toHaveCount(0);
});

test("a matched cancellation class explains the like-for-like comparison", async ({ page }) => {
  await mockRatePlansApi(page, {
    recommendation: pricingResponse({ same_category: 6, similar: 2, used: "same", cancellation_class: "matched" }),
  });
  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

  const panel = page.locator(".panel", { has: page.getByRole("heading", { name: "Στατιστικά αγοράς" }) });
  // The note sits next to the basis line it qualifies.
  await expect(panel.getByText("Βάση: 6 ίδιας κατηγορίας", { exact: true })).toBeVisible();
  await expect(panel.getByTestId("cancellation-class-note"))
    .toHaveText("Σύγκριση σε τιμές ίδιας πολιτικής ακύρωσης με το δωμάτιό σας.");
});

const silentScopes = [
  { name: "an \"all\" cancellation class", scope: { same_category: 6, similar: 2, used: "same", cancellation_class: "all" } },
  { name: "a scope without the field", scope: { same_category: 6, similar: 2, used: "same" } },
];

for (const { name, scope } of silentScopes) {
  test(`${name} says nothing about cancellation policy`, async ({ page }) => {
    await mockRatePlansApi(page, { recommendation: pricingResponse(scope) });
    await page.goto("/pricing");
    await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

    const panel = page.locator(".panel", { has: page.getByRole("heading", { name: "Στατιστικά αγοράς" }) });
    await expect(panel.getByText("Βάση: 6 ίδιας κατηγορίας", { exact: true })).toBeVisible();
    await expect(page.getByTestId("cancellation-class-note")).toHaveCount(0);
  });
}
