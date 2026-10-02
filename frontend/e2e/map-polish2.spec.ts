/**
 * Map page polish, round 2 (owner review 2026-09-30):
 *   1. the room the results are compared for is a primary control at the top
 *      of the results column, not a field in the collapsed «Φίλτρα» form;
 *   2. the competitor cards and the map markers point at each other;
 *   3. the header's status chips are not buttons, and nothing overflows the
 *      page at a narrow (~800 px) window.
 *
 * Hermetic: every endpoint is mocked with the wildcard FIRST (Playwright
 * matches LIFO, so each specific route below wins). The e2e map has no
 * WebGL, but its markers are real Mapbox DOM elements, so the assertions read
 * their classes and attributes (as map-round6.spec.ts does).
 */
import { expect, Page, test } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  JOB_ID,
  mockMapbox,
  OWNED_PROPERTY_ID,
  RecordedCall,
  recordCall,
  ROOM_TYPE_ID,
  seedBrowserState,
} from "./helpers";

/** A second room of the same category: a separate comparison set all the same. */
const SECOND_ROOM_ID = "33333333-3333-3333-3333-3333333333dd";
const ROOM_TYPES = [
  { id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID, room_type: "Δίκλινο με Θέα Θάλασσα",
    room_type_category: "double", is_active: true },
  { id: SECOND_ROOM_ID, owned_property_id: OWNED_PROPERTY_ID, room_type: "Οικονομικό Δίκλινο",
    room_type_category: "double", is_active: true },
];

/** Three comparable hotels, far enough apart that their markers do not stack. */
const MARKERS = [
  { hotel_name: "Hotel Keira", property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1", room_package_id: null,
    room_type: "Double Sea View", room_type_category: "double", property_type: "hotel",
    latitude: 36.34, longitude: 28.2, price_per_night_eur: 83, review_score: 8.8, review_count: 211,
    rooms_left: 3, distance_km: 1.1, category_match: "same", booking_url: null, comparable: true },
  { hotel_name: "Hotel Lotos", property_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2", room_package_id: null,
    room_type: "Double Garden", room_type_category: "double", property_type: "hotel",
    latitude: 36.36, longitude: 28.23, price_per_night_eur: 61, review_score: 7.6, review_count: 57,
    rooms_left: 5, distance_km: 2.4, category_match: "same", booking_url: null, comparable: true },
  { hotel_name: "Hotel Mirto", property_id: "cccccccc-cccc-cccc-cccc-ccccccccccc3", room_package_id: null,
    room_type: "Double Economy", room_type_category: "double", property_type: "hotel",
    latitude: 36.38, longitude: 28.26, price_per_night_eur: 47, review_score: 7.1, review_count: 19,
    rooms_left: 1, distance_km: 3.2, category_match: "same", booking_url: null, comparable: true },
];

const COMPLETED_JOB = {
  id: JOB_ID, account_id: CURRENT_USER.account_id, owned_property_id: OWNED_PROPERTY_ID,
  job_type: "competitor_search", room_type_category: "double",
  destination: "Faliraki", raw_destination: "Faliraki, Rhodes", canonical_destination: "faliraki",
  check_in: "2030-06-01", check_out: "2030-06-05", adults: 2, children: 0, rooms: 1,
  filters_payload: { limit: 9 }, nearby_destinations: [] as string[], radius_km: 6,
  status: "completed", requested_at: "2030-05-01T10:00:00Z", started_at: "2030-05-01T10:00:05Z",
  finished_at: "2030-05-01T10:06:00Z", attempt_count: 1, max_attempts: 3, scrape_runs_count: 1,
  result_summary: { version: 1, rows_seen: 9, filter_counts: {}, rows_written: 3, warnings: [] as string[] },
};

const MARKET_SUMMARY = {
  destination: "faliraki", check_in: "2030-06-01", check_out: "2030-06-05",
  total_records: 9, total_hotels: 3, price_min_eur: 47, price_max_eur: 83, price_avg_eur: 64,
  price_median_eur: 61, avg_review_score: 7.8, rooms_left_total: 9,
};

/** One /competitors/ row for Keira, scored by `source`: a statistical read is what triggers the automatic agent run. */
const competitorRows = (source: "agent" | "statistical") => [{
  hotel_name: "Hotel Keira", city: "Faliraki", address: "", property_type: "hotel",
  latitude: 36.34, longitude: 28.2, stars: 4, review_score: 8.8, review_count: 211,
  price_min_eur: 83, price_max_eur: 83, rooms_left: 3, distance_km: 1.1, category_match: "same",
  booking_url: null, match_source: source, best_match_score: source === "agent" ? 88 : 64,
  packages: [{ room_type: "Double Sea View", price_per_night_eur: 83, price_total_eur: 332, meals: "",
    free_cancellation: "", rooms_left: 3, match_score: source === "agent" ? 88 : 64,
    room_type_category: "double", category_match: "same", rate_plan: null,
    match_reasoning: source === "agent" ? "Ίδια χωρητικότητα και θέα." : null, comparable: true }],
}];

type MapMocks = {
  /** Every read of the market endpoints, in order: path + query. */
  reads: Array<{ path: string; query: URLSearchParams }>;
  /** Every agents POST (the body lives here). */
  calls: RecordedCall[];
};

async function mockMap(page: Page): Promise<MapMocks> {
  const mocks: MapMocks = { reads: [], calls: [] };
  const record = (url: URL) => mocks.reads.push({ path: url.pathname, query: url.searchParams });
  // Only the first room has agent verdicts for the job: switching runs the agent for the second.
  const judgedRooms = new Set<string>([ROOM_TYPE_ID]);

  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO: the wildcard FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: ROOM_TYPES }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) =>
    route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby: [] } }));
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => route.fulfill({ json: null }));
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    record(new URL(route.request().url()));
    return route.fulfill({ json: MARKERS });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => {
    record(new URL(route.request().url()));
    return route.fulfill({ json: MARKET_SUMMARY });
  });
  await page.route(`${API}/api/v1/market/amenities**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/competitors/**`, (route) => {
    const room = new URL(route.request().url()).searchParams.get("owned_room_type_id") ?? "";
    return route.fulfill({ json: competitorRows(judgedRooms.has(room) ? "agent" : "statistical") });
  });
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) =>
    new URL(route.request().url()).pathname.endsWith("/scrape-jobs/")
      ? route.fulfill({ json: [COMPLETED_JOB] })
      : route.fulfill({ json: COMPLETED_JOB }));
  await page.route(`${API}/api/v1/agents/room-matches`, (route) => {
    recordCall(mocks.calls, route);
    const body = route.request().postDataJSON() as { owned_room_type_id: string };
    judgedRooms.add(body.owned_room_type_id);
    return route.fulfill({ json: { status: "completed", matches_written: 3, source: "agent" } });
  });
  return mocks;
}

const readsOf = (reads: MapMocks["reads"], suffix: string) =>
  reads.filter((read) => read.path.endsWith(suffix)).map((read) => read.query);
const roomMatchPosts = (calls: RecordedCall[]) =>
  calls.filter((call) => call.method === "POST" && call.path === "/api/v1/agents/room-matches");
const roomSelect = (page: Page) => page.locator(".results-sidebar").getByLabel("Σύγκριση για");

async function openMap(page: Page): Promise<void> {
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
}

test("the room is chosen at the top of the results column, and the filters no longer duplicate it", async ({ page }) => {
  const { reads, calls } = await mockMap(page);

  await openMap(page);
  // A restored search keeps the filters collapsed; the room switch is on screen all the same.
  await expect(page.locator(".filters-sidebar")).toHaveCount(0);
  const select = roomSelect(page);
  await expect(select).toBeVisible();
  await expect(select).toHaveValue(ROOM_TYPE_ID);
  await expect(select.locator("option")).toHaveText([
    "Επιλέξτε το δωμάτιό σας",
    "Δίκλινο με Θέα Θάλασσα (double)",
    "Οικονομικό Δίκλινο (double)",
  ]);
  // Above the market summary it scopes.
  const selectBox = (await select.boundingBox())!;
  const summaryBox = (await page.getByTestId("market-summary").boundingBox())!;
  expect(selectBox.y + selectBox.height).toBeLessThanOrEqual(summaryBox.y);

  // ONE room control on the page: the filters form lost its copy.
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  await expect(page.locator(".filters-sidebar select")).toHaveCount(0);
  await expect(page.getByLabel("Σύγκριση για")).toHaveCount(1);

  // Same behaviour as before: every read is for the chosen room, and a room
  // without verdicts for the job gets its automatic agent run.
  const markerReadsBefore = readsOf(reads, "/maps/competitors").length;
  await select.selectOption(SECOND_ROOM_ID);
  await expect.poll(() => readsOf(reads, "/maps/competitors").length).toBeGreaterThan(markerReadsBefore);
  expect(readsOf(reads, "/maps/competitors").at(-1)?.get("owned_room_type_id")).toBe(SECOND_ROOM_ID);
  await expect.poll(() => roomMatchPosts(calls).length).toBe(1);
  expect(roomMatchPosts(calls)[0].body).toEqual({ scrape_job_id: JOB_ID, owned_room_type_id: SECOND_ROOM_ID });
  await expect(select).toHaveValue(SECOND_ROOM_ID);
});

const markers = (page: Page) => page.locator(".roomrate-marker-dot");
const markerFor = (page: Page, hotel: string) => page.locator(`.roomrate-marker-dot[aria-label*="${hotel}"]`);
const cardFor = (page: Page, hotel: string) => page.locator(".competitor-card", { hasText: hotel });

test("a card and its marker point at each other: hover or focus raises the marker, a marker click flashes the card", async ({ page }) => {
  await mockMap(page);

  await openMap(page);
  await expect(markers(page)).toHaveCount(3);
  await expect(page.locator(".roomrate-marker-dot.is-highlighted")).toHaveCount(0);

  // Hover: only that hotel's marker is raised, and it drops again when the pointer leaves.
  await cardFor(page, "Hotel Lotos").hover();
  await expect(markerFor(page, "Hotel Lotos")).toHaveClass(/\bis-highlighted\b/);
  await expect(page.locator(".roomrate-marker-dot.is-highlighted")).toHaveCount(1);
  // Onto the header, which never scrolls under the pointer (the list does when focus moves in it).
  await page.locator(".map-header h1").hover();
  await expect(page.locator(".roomrate-marker-dot.is-highlighted")).toHaveCount(0);

  // Keyboard focus does the same, without a pointer.
  const mirtoCheckbox = cardFor(page, "Hotel Mirto").locator("input[type=checkbox]");
  await mirtoCheckbox.focus();
  await expect(markerFor(page, "Hotel Mirto")).toHaveClass(/\bis-highlighted\b/);
  await mirtoCheckbox.blur();
  await expect(page.locator(".roomrate-marker-dot.is-highlighted")).toHaveCount(0);

  // A marker click marks its card (and only it) for a moment, then lets go.
  await markerFor(page, "Hotel Keira").click();
  await expect(cardFor(page, "Hotel Keira")).toHaveClass(/\bis-highlighted\b/);
  await expect(page.locator(".competitor-card.is-highlighted")).toHaveCount(1);
  await expect(cardFor(page, "Hotel Keira")).toBeInViewport();
  await expect(cardFor(page, "Hotel Keira")).not.toHaveClass(/\bis-highlighted\b/, { timeout: 5000 });

  // Clicking a card (to ease the map there) still ticks it, exactly once.
  await cardFor(page, "Hotel Lotos").locator("strong").click();
  await expect(cardFor(page, "Hotel Lotos").locator("input[type=checkbox]")).toBeChecked();
  await expect(markerFor(page, "Hotel Lotos")).toHaveAttribute("data-selected", "true");
});

/** Nothing on the page reaches past the viewport, and the document cannot scroll sideways. */
async function horizontalOverflow(page: Page): Promise<{ scrollWidth: number; clientWidth: number; wider: string[] }> {
  return page.evaluate(() => {
    const root = document.documentElement;
    const wider = Array.from(document.querySelectorAll(".map-header *, .results-sidebar, .filters-sidebar, .map-grid"))
      .filter((element) => element.getBoundingClientRect().right > root.clientWidth + 0.5)
      .map((element) => element.tagName.toLowerCase() + "." + String(element.className));
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, wider };
  });
}

test("the header chips only report, the links navigate, and an 800 px window does not scroll sideways", async ({ page }) => {
  await mockMap(page);
  // Real catalogues carry long room names; a select is as wide as its longest option.
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [
      { ...ROOM_TYPES[0], room_type: "Δίκλινο Δωμάτιο Superior με Θέα στη Θάλασσα, Ιδιωτικό Μπαλκόνι και Υδρομασάζ" },
      ROOM_TYPES[1],
    ] }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: { count: 12 } }));
  await page.setViewportSize({ width: 800, height: 900 });

  await openMap(page);
  const header = page.locator(".map-header");
  // Status chips: text in spans, nothing to press.
  for (const chip of [header.getByTestId("header-count-chip"), header.getByTestId("header-live-chip")]) {
    await expect(chip).toBeVisible();
    expect(await chip.evaluate((element) => element.tagName)).toBe("SPAN");
    await expect(chip).not.toHaveAttribute("role", /.+/);
    expect(await chip.evaluate((element) => getComputedStyle(element).cursor)).toBe("default");
  }
  await expect(header.getByTestId("header-count-chip")).toHaveText("3 ανταγωνιστές");
  await expect(header.getByRole("button", { name: "3 ανταγωνιστές" })).toHaveCount(0);
  await expect(header.getByRole("button", { name: "Ζωντανά δεδομένα" })).toHaveCount(0);
  // Navigation reads as navigation; the bell and sign-out stay buttons.
  const nav = header.getByRole("navigation", { name: "Πλοήγηση" });
  await expect(nav.getByRole("link", { name: "Τιμολόγηση" })).toHaveAttribute("href", "/pricing");
  await expect(nav.getByRole("link", { name: "Ρυθμίσεις" })).toHaveAttribute("href", "/settings");
  await expect(header.getByRole("button", { name: "Αποσύνδεση" })).toBeVisible();

  // No sideways scroll at 800 px, with the filters closed and open.
  await expect.poll(() => horizontalOverflow(page)).toEqual({ scrollWidth: 800, clientWidth: 800, wider: [] });
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  const opened = await horizontalOverflow(page);
  expect(opened.wider).toEqual([]);
  expect(opened.scrollWidth).toBeLessThanOrEqual(opened.clientWidth);
});
