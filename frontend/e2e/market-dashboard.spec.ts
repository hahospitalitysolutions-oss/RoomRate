/**
 * «Αγορά vs εμείς» (owner request 2026-11-13): the dashboard that replaced the
 * map as the market page's centre column, its competitor selection, and the
 * per-competitor drill-down route. Hermetic: every endpoint is mocked, the
 * wildcard first (Playwright matches LIFO, so the specific routes win).
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, JOB_ID, mockMapbox, OWNED_PROPERTY_ID, ROOM_TYPE_ID, seedBrowserState } from "./helpers";

const PROP_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROP_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const PROP_GAMMA = "cccccccc-cccc-cccc-cccc-cccccccccccc";

const MARKER_BASE = {
  room_type: "Double Room",
  room_type_category: "double",
  property_type: "hotel",
  review_count: 120,
  rooms_left: 3,
  category_match: "same",
};
// Alpha has two packages (60 € and 70 €): the dashboard keeps one price per hotel, the cheapest.
const MARKERS = [
  { ...MARKER_BASE, hotel_name: "Hotel Alpha", property_id: PROP_ALPHA, room_package_id: "pkg-a1",
    latitude: 36.34, longitude: 28.2, price_per_night_eur: 60, review_score: 8.0, distance_km: 1.2 },
  { ...MARKER_BASE, hotel_name: "Hotel Alpha", property_id: PROP_ALPHA, room_package_id: "pkg-a2",
    latitude: 36.34, longitude: 28.2, price_per_night_eur: 70, review_score: 8.0, distance_km: 1.2 },
  { ...MARKER_BASE, hotel_name: "Hotel Beta", property_id: PROP_BETA, room_package_id: "pkg-b1",
    latitude: 36.35, longitude: 28.21, price_per_night_eur: 100, review_score: 9.0, distance_km: 0.4 },
  { ...MARKER_BASE, hotel_name: "Hotel Gamma", property_id: PROP_GAMMA, room_package_id: "pkg-g1",
    latitude: 36.36, longitude: 28.22, price_per_night_eur: 150, review_score: 7.0, distance_km: 2.0 },
];

const JOB = {
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
  adults: 2,
  children: 0,
  rooms: 1,
  filters_payload: {},
  status: "completed",
  requested_at: "2030-05-01T10:00:00Z",
  attempt_count: 1,
  max_attempts: 3,
  scrape_runs_count: 1,
  result_summary: { version: 1, rows_seen: 10, filter_counts: {}, rows_written: 4, warnings: [] as string[] },
};

// Deliberately not what the rows give (median 100): «Όλη η αγορά» must read the summary.
const SUMMARY = {
  total_records: 30, total_hotels: 12, price_min_eur: 55, price_max_eur: 210,
  price_avg_eur: 118, price_median_eur: 110, avg_review_score: 8.6, rooms_left_total: 40,
};

const OWN = {
  display_name: "E2E Test Hotel", latitude: 36.345, longitude: 28.205, radius_km: 10,
  price_per_night_eur: 90, room_type: "Double Room with Sea View", booking_url: null,
};

const COMPETITORS = [
  {
    hotel_name: "Hotel Beta", city: "Faliraki", address: "", property_type: "hotel",
    latitude: 36.35, longitude: 28.21, stars: 4, review_score: 9.0, review_count: 120,
    price_min_eur: 100, price_max_eur: 130, rooms_left: 3, distance_km: 0.4, best_match_score: 82,
    booking_url: "https://www.booking.com/hotel/gr/beta.html",
    packages: [
      { room_type: "Deluxe Double", price_per_night_eur: 100, price_total_eur: 400, meals: "Πρωινό",
        free_cancellation: "", rooms_left: 3, match_score: 82, room_type_category: "double" },
    ],
  },
];

// Three runs, Beta in every one: the drill-down draws Beta's own line over the median.
const HISTORY = {
  check_in: "2030-06-01",
  check_out: "2030-06-05",
  points: [1, 2, 3].flatMap((run) => [
    { run_index: run, observed_at: `2030-05-0${4 - run}T10:00:00Z`, property_id: PROP_ALPHA, hotel_name: "Hotel Alpha", min_price_eur: 60 + run },
    { run_index: run, observed_at: `2030-05-0${4 - run}T10:00:00Z`, property_id: PROP_BETA, hotel_name: "Hotel Beta", min_price_eur: 100 + run * 5 },
    { run_index: run, observed_at: `2030-05-0${4 - run}T10:00:00Z`, property_id: PROP_GAMMA, hotel_name: "Hotel Gamma", min_price_eur: 150 },
  ]),
};

type Options = { markers?: unknown[]; restorable?: boolean; markersStatus?: number };

async function mockMarket(page: Page, options: Options = {}): Promise<void> {
  const { markers = MARKERS, restorable = true, markersStatus = 200 } = options;
  await seedBrowserState(page);
  await mockMapbox(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [{ id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID,
      room_type: "Double Room with Sea View", room_type_category: "double", is_active: true }] }));
  await page.route(`${API}/api/v1/notifications/unread-count**`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) =>
    route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby: [] } }));
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => route.fulfill({ json: OWN }));
  await page.route(`${API}/api/v1/maps/competitors**`, (route) =>
    markersStatus === 200
      ? route.fulfill({ json: markers })
      : route.fulfill({ status: markersStatus, json: { detail: "Σφάλμα διακομιστή" } }));
  await page.route(`${API}/api/v1/competitors/**`, (route) =>
    route.fulfill({ json: COMPETITORS.map((row) => ({ ...row, match_source: "agent" })) }));
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: SUMMARY }));
  await page.route(`${API}/api/v1/market/price-history**`, (route) => route.fulfill({ json: HISTORY }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: restorable ? [JOB] : [] });
    }
    return restorable ? route.fulfill({ json: JOB }) : route.fulfill({ status: 404, json: { detail: "not found" } });
  });
}

const dashboard = (page: Page) => page.getByTestId("market-dashboard");

test("the centre column opens on «Αγορά vs εμείς», the map one tab away", async ({ page }) => {
  await mockMarket(page);
  await page.goto("/map");

  await expect(dashboard(page)).toBeVisible();
  await expect(page.getByTestId("center-tab-dashboard")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".map-shell")).toBeHidden();

  await page.getByTestId("center-tab-map").click();
  await expect(page.locator(".map-shell")).toBeVisible();
  await expect(page.locator(".roomrate-marker-dot")).toHaveCount(3);
  await expect(dashboard(page)).toBeHidden();

  // Keyboard: the arrow keys move between the two tabs.
  await page.getByTestId("center-tab-map").press("ArrowLeft");
  await expect(dashboard(page)).toBeVisible();
  await expect(page.getByTestId("center-tab-dashboard")).toBeFocused();
});

test("market vs us: the summary's figures beside the owner's price", async ({ page }) => {
  await mockMarket(page);
  await page.goto("/map");

  const median = page.getByTestId("kpi-median");
  await expect(median).toContainText("110 €");
  await expect(median).toContainText("90 €");
  // 90 against 110: 18% cheaper.
  await expect(median).toContainText("18% φθηνότερα από τη διάμεση");
  await expect(page.getByTestId("kpi-average")).toContainText("118 €");
  await expect(page.getByTestId("kpi-range")).toContainText("55 € – 210 €");
  // One of the three hotels on screen undercuts 90 €.
  await expect(page.getByTestId("kpi-range")).toContainText("Φθηνότερα από εσάς: 1 από 3 καταλύματα");
  // The owner's own rating is not collected: a dash, never a fake number.
  await expect(page.getByTestId("kpi-rating")).toContainText("8,6");
  await expect(page.getByTestId("kpi-rating")).toContainText("δεν συλλέγεται ακόμη");
  await expect(page.getByTestId("dashboard-scope-hint")).toContainText("12 καταλύματα της αγοράς");

  // One row per hotel, the cheapest package: Alpha at 60 €, 30 € under the owner.
  const table = page.getByTestId("comparison-table");
  await expect(table.locator("tbody tr")).toHaveCount(3);
  const alpha = table.locator('tbody tr[data-hotel-key="' + PROP_ALPHA + '"]');
  await expect(alpha).toContainText("60 €");
  await expect(alpha).toContainText("−30 €");
  await expect(page.getByTestId("price-ladder").locator(".ladder-svg circle.ladder-dot")).toHaveCount(3);
});

test("ticking competitors narrows the comparison to the selected ones", async ({ page }) => {
  await mockMarket(page);
  await page.goto("/map");

  const selectedScope = page.getByTestId("dashboard-scope-selected");
  await expect(selectedScope).toBeDisabled();

  const table = page.getByTestId("comparison-table");
  await table.getByRole("checkbox", { name: "Επιλογή Hotel Beta" }).check();
  await table.getByRole("checkbox", { name: "Επιλογή Hotel Gamma" }).check();
  await expect(selectedScope).toHaveText("Επιλεγμένοι (2)");
  await selectedScope.click();
  await expect(selectedScope).toHaveAttribute("aria-checked", "true");

  // Beta 100 and Gamma 150, from the rows: median 125, average 125.
  await expect(page.getByTestId("kpi-median")).toContainText("125 €");
  await expect(page.getByTestId("kpi-range")).toContainText("100 € – 150 €");
  await expect(page.getByTestId("kpi-range")).toContainText("Φθηνότερα από εσάς: 0 από 2 καταλύματα");
  await expect(page.getByTestId("dashboard-scope-hint")).toContainText("2 από 3 καταλύματα επιλεγμένα");
  // The rest stay on the ladder, hollow.
  await expect(page.getByTestId("price-ladder").locator(".ladder-svg circle.ladder-dot.is-out")).toHaveCount(1);

  // The same ticks as the results column: one notion of «my competitors».
  await expect(page.locator(".competitor-card").filter({ hasText: "Hotel Beta" }).getByRole("checkbox")).toBeChecked();

  // Unticking everything falls back to the whole market instead of an empty screen.
  await table.getByRole("checkbox", { name: "Επιλογή Hotel Beta" }).uncheck();
  await table.getByRole("checkbox", { name: "Επιλογή Hotel Gamma" }).uncheck();
  await expect(page.getByTestId("dashboard-scope-all")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("kpi-median")).toContainText("110 €");
});

test("a competitor's drill-down compares it with the owner and the market", async ({ page }) => {
  await mockMarket(page);
  await page.goto("/map");

  await page.getByRole("link", { name: "Ανάλυση: Hotel Beta" }).click();
  await expect(page).toHaveURL(new RegExp(`/map/competitors/${PROP_BETA}\\?job=${JOB_ID}&room=${ROOM_TYPE_ID}`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Hotel Beta");

  const bars = page.getByTestId("price-bars");
  await expect(bars).toContainText("Εσείς");
  await expect(bars).toContainText("90 €");
  await expect(bars).toContainText("100 €");
  await expect(bars).toContainText("Διάμεση αγοράς");
  await expect(page.getByTestId("detail-kpi-price")).toContainText("+10 €");
  await expect(page.getByTestId("detail-kpi-rating")).toContainText("9,0");
  await expect(page.getByTestId("detail-kpi-context")).toContainText("0,4 km");
  await expect(page.getByTestId("detail-kpi-context")).toContainText("82%");

  // The existing price-history chart, with Beta's own line and its own table column.
  const history = page.getByTestId("detail-history");
  await expect(history.getByTestId("history-focus-key")).toContainText("Hotel Beta");
  await expect(history.locator(".history-svg polyline.history-focus-line")).toHaveCount(1);
  await expect(history.locator(".history-svg rect.history-focus-dot")).toHaveCount(3);
  await history.getByText("Πίνακας τιμών").click();
  await expect(history.getByRole("columnheader", { name: "Hotel Beta" })).toBeVisible();

  // Its rooms and plans, as the matching list shows them.
  await expect(page.getByTestId("detail-rooms")).toContainText("Deluxe Double");

  await page.getByTestId("back-to-dashboard").click();
  await expect(page).toHaveURL(/\/map$/);
  await expect(dashboard(page)).toBeVisible();
});

test("the drill-down says so when the competitor is not in the search", async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/map/competitors/unknown-hotel?job=${JOB_ID}&room=${ROOM_TYPE_ID}`);
  await expect(page.getByTestId("detail-not-found")).toContainText("Ο ανταγωνιστής δεν βρέθηκε σε αυτή την αναζήτηση");
});

test("the drill-down without a job explains how to open it", async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/map/competitors/${PROP_BETA}`);
  await expect(page.getByTestId("detail-error")).toContainText("Ανοίξτε την ανάλυση από τη σύγκριση αγοράς");
});

test("the drill-down reports a failed read and offers a retry", async ({ page }) => {
  await mockMarket(page, { markersStatus: 500 });
  await page.goto(`/map/competitors/${PROP_BETA}?job=${JOB_ID}`);
  await expect(page.getByTestId("detail-error")).toBeVisible();
  await expect(page.getByRole("button", { name: "Δοκιμάστε ξανά" })).toBeVisible();
});

test("before any search the dashboard says what it will show", async ({ page }) => {
  await mockMarket(page, { restorable: false });
  await page.goto("/map");
  await expect(page.getByTestId("dashboard-empty")).toContainText("Η σύγκριση με την αγορά εμφανίζεται μετά την αναζήτηση");
  await expect(page.getByTestId("comparison-table")).toHaveCount(0);
});

test("the dashboard fits a phone-width screen without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockMarket(page);
  await page.goto("/map");
  await expect(page.getByTestId("kpi-median")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
