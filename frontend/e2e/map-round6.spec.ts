/**
 * Round 6, stream B (spec §4): the map page for the Thursday live demo.
 * Hermetic: every endpoint the page calls is mocked with a `**` glob, the
 * wildcard first (Playwright matches LIFO, so every specific route below wins).
 * Fixture values are deliberately non-default (limit 12, adults 3, radius 7,
 * distances 1.2/0.4/null): a passing assertion proves the value came from here.
 */
import { expect, Page, Route, test } from "@playwright/test";

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

const PROP_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROP_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const PROP_GAMMA = "cccccccc-cccc-cccc-cccc-cccccccccccc";
// The job a «Εύρεση ανταγωνιστών» click creates, as opposed to JOB_ID (restored on load).
const NEW_JOB_ID = "22222222-2222-2222-2222-2222222222cc";

const MARKER_BASE = {
  room_package_id: null,
  room_type: "Double Room",
  room_type_category: "double",
  property_type: "hotel",
  review_score: 8.4,
  review_count: 120,
  rooms_left: 3,
};
// Alpha: same category, 1.2 km. Beta: SIMILAR (a studio), nearest at 0.4 km.
// Gamma: same category, no distance (no coordinates on the owner's side) and no URL.
const MAP_MARKERS = [
  { ...MARKER_BASE, hotel_name: "Hotel Alpha", property_id: PROP_ALPHA, latitude: 36.34, longitude: 28.2,
    price_per_night_eur: 60, distance_km: 1.2, category_match: "same", booking_url: "https://www.booking.com/hotel/gr/alpha.html" },
  { ...MARKER_BASE, hotel_name: "Hotel Beta", property_id: PROP_BETA, latitude: 36.35, longitude: 28.21,
    room_type: "Studio", room_type_category: "studio",
    price_per_night_eur: 95, distance_km: 0.4, category_match: "similar", booking_url: "https://www.booking.com/hotel/gr/beta.html" },
  { ...MARKER_BASE, hotel_name: "Hotel Gamma", property_id: PROP_GAMMA, latitude: 36.36, longitude: 28.22,
    price_per_night_eur: 150, distance_km: null, category_match: "same", booking_url: null },
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
  nearby_destinations: ["Ιξιά", "Αφάντου"],
  radius_km: 7,
  status: "completed",
  requested_at: "2030-05-01T10:00:00Z",
  started_at: "2030-05-01T10:00:05Z",
  finished_at: "2030-05-01T10:06:00Z",
  attempt_count: 1,
  max_attempts: 3,
  scrape_runs_count: 1,
  result_summary: { version: 1, rows_seen: 20, filter_counts: {}, rows_written: 12, warnings: [] as string[] },
};

const MARKET_SUMMARY = {
  destination: "faliraki", check_in: "2030-06-01", check_out: "2030-06-05",
  total_records: 42, total_hotels: 17, same_category_hotels: 11, similar_hotels: 6,
  price_min_eur: 60, price_max_eur: 240, price_avg_eur: 118, price_median_eur: 110,
  avg_review_score: 8.6, rooms_left_total: 55,
};

const OWN_PROPERTY = {
  display_name: "E2E Test Hotel", latitude: 36.345, longitude: 28.205, radius_km: 10,
  price_per_night_eur: 92, room_type: "Double Room with Sea View", booking_url: "https://www.booking.com/hotel/gr/e2e.html",
};

// `/api/v1/competitors/` rows (the match list), derived from the markers so the two agree.
const COMPETITORS = MAP_MARKERS.map((marker) => ({
  hotel_name: marker.hotel_name, city: "Faliraki", address: "", property_type: "hotel",
  latitude: marker.latitude, longitude: marker.longitude, stars: 3,
  review_score: marker.review_score, review_count: marker.review_count,
  price_min_eur: marker.price_per_night_eur, price_max_eur: marker.price_per_night_eur + 20, rooms_left: 3,
  distance_km: marker.distance_km, category_match: marker.category_match, booking_url: marker.booking_url,
  best_match_score: 80,
  packages: [{ room_type: marker.room_type, price_per_night_eur: marker.price_per_night_eur,
    price_total_eur: marker.price_per_night_eur * 4, meals: "", free_cancellation: "", rooms_left: 3,
    match_score: 80, room_type_category: marker.room_type_category, category_match: marker.category_match }],
}));

/** One recorded read: which endpoint, with which query string. */
type ReadCall = { path: string; query: URLSearchParams };

type Round6Options = {
  /** false = empty job list, nothing restored: the only way to results is a Find click. */
  restorable?: boolean;
  markerRows?: unknown[];
  ownProperty?: unknown;
  nearby?: string[];
  /** Overrides merged into the completed/restored job. */
  job?: Record<string, unknown>;
  /**
   * Successive answers for GET /scrape-jobs/<NEW_JOB_ID>; the last one repeats.
   * A number is an HTTP error status with an empty body (a mid-scrape blip).
   * Mutable on purpose: a test may push the completed body only once it has
   * asserted the in-flight state. Default: completed at once.
   */
  polls?: Array<Record<string, unknown> | number>;
  /** Every scrape-jobs call (the POST payload lives here). */
  calls?: RecordedCall[];
  /** Every read of maps/own-property, maps/competitors, competitors/, market/summary and market/amenities. */
  reads?: ReadCall[];
  /** Every query string sent to onboarding/nearby-destinations (kept apart from `reads`). */
  nearbyReads?: URLSearchParams[];
};

/** The reads of one endpoint (a path suffix such as "/maps/competitors"), oldest first. */
const readsOf = (reads: ReadCall[], pathSuffix: string) =>
  reads.filter((read) => read.path.endsWith(pathSuffix)).map((read) => read.query);
/** The match list's own /competitors/ reads: the automatic agent probe reads it too, without a score floor. */
const matchListReads = (reads: ReadCall[]) =>
  readsOf(reads, "/competitors/").filter((query) => query.has("min_match_score"));

async function mockMapRound6(page: Page, options: Round6Options = {}): Promise<void> {
  const {
    restorable = true, markerRows = MAP_MARKERS, ownProperty = OWN_PROPERTY,
    nearby = ["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"], calls = [], reads = [], nearbyReads = [],
  } = options;
  const job = { ...COMPLETED_JOB, ...(options.job ?? {}) };
  const polls = options.polls ?? [{ ...job, id: NEW_JOB_ID }];
  let pollIndex = 0;
  const record = (route: Route) => {
    const url = new URL(route.request().url());
    reads.push({ path: url.pathname, query: url.searchParams });
  };

  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO: the wildcard FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [{ id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID,
      room_type: "Double Room with Sea View", room_type_category: "double", is_active: true }] }));
  await page.route(`${API}/api/v1/notifications/unread-count**`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) => {
    nearbyReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby } });
  });
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => { record(route); return route.fulfill({ json: ownProperty }); });
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => { record(route); return route.fulfill({ json: markerRows }); });
  await page.route(`${API}/api/v1/competitors/**`, (route) => {
    record(route);
    // The automatic-run probe (no score floor) is told the room already has
    // agent verdicts, so no automatic run joins these scenarios — that run is
    // comparable.spec.ts's subject.
    const probe = !new URL(route.request().url()).searchParams.has("min_match_score");
    return route.fulfill({
      json: probe ? COMPETITORS.map((row) => ({ ...row, match_source: "agent" })) : COMPETITORS,
    });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => { record(route); return route.fulfill({ json: MARKET_SUMMARY }); });
  // An empty facility list: the page falls back to its own twelve options.
  await page.route(`${API}/api/v1/market/amenities**`, (route) => { record(route); return route.fulfill({ json: [] }); });
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    recordCall(calls, route);
    const request = route.request();
    if (request.method() === "POST") {
      return route.fulfill({ json: { ...job, id: NEW_JOB_ID, status: "queued", started_at: null, finished_at: null, result_summary: null } });
    }
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: restorable ? [job] : [] });
    }
    if (path.endsWith(NEW_JOB_ID)) {
      const body = polls[Math.min(pollIndex, polls.length - 1)];
      pollIndex += 1;
      // An empty error body: the page must fall back to its own error text.
      return typeof body === "number" ? route.fulfill({ status: body, json: {} }) : route.fulfill({ json: body });
    }
    return restorable ? route.fulfill({ json: job }) : route.fulfill({ status: 404, json: { detail: "not found" } });
  });
}

const postedJob = (calls: RecordedCall[]) =>
  calls.find((call) => call.method === "POST" && call.path === "/api/v1/scrape-jobs/")?.body as
    Record<string, unknown> | undefined;

test("the form defaults to 40 hotels, caps at 80 and no longer sends deep_crawl_max_items", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await mockMapRound6(page, { restorable: false, calls });

  await page.goto("/map");
  await expect(page.getByText("Δεν έχετε τρέξει ακόμη αναζήτηση")).toBeVisible();
  // No click on «Φίλτρα»: with no previous search the form opens itself.
  const limit = page.getByTestId("limit-input");
  await expect(limit).toHaveValue("40");
  await expect(limit).toHaveAttribute("max", "80");
  // Typed past the cap: the clamp lives in the component, not in the input's max attribute.
  await limit.fill("500");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

  const payload = postedJob(calls)?.["filters_payload"];
  expect(payload).toEqual({ limit: 80, match_mode: "catalog_room_name", room_type: "Double Room with Sea View" });
});

const chipNames = (page: Page) => page.getByTestId("nearby-chip");

test("with no previous search the form opens itself with the nearby areas and a 10 km radius", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const nearbyReads: URLSearchParams[] = [];
  await mockMapRound6(page, { restorable: false, calls, nearbyReads, nearby: ["Καλλιθέα Ρόδου", "Ιξιά", "Κολύμπια"] });

  await page.goto("/map");
  // Nothing to restore, so the form is the one way forward: open without a click.
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  await expect(page.getByRole("button", { name: "Φίλτρα", exact: true })).toHaveCount(0);
  await expect(chipNames(page)).toHaveText(["Καλλιθέα Ρόδου", "Ιξιά", "Κολύμπια"]);
  await expect(page.getByTestId("radius-input")).toHaveValue("10");
  expect(nearbyReads.map((query) => query.get("destination"))).toContain("Faliraki");

  const areaInput = page.getByTestId("nearby-input");
  const addArea = page.getByRole("button", { name: "Προσθήκη", exact: true });
  // Enter adds the typed area at the end and empties the input.
  await areaInput.fill("Αφάντου");
  await areaInput.press("Enter");
  await expect(chipNames(page)).toHaveText(["Καλλιθέα Ρόδου", "Ιξιά", "Κολύμπια", "Αφάντου"]);
  await expect(areaInput).toHaveValue("");
  // A case-insensitive repeat and the main destination itself are refused (button path).
  await areaInput.fill("ιξιά");
  await addArea.click();
  await areaInput.fill("FALIRAKI");
  await addArea.click();
  await expect(chipNames(page)).toHaveText(["Καλλιθέα Ρόδου", "Ιξιά", "Κολύμπια", "Αφάντου"]);
  // × removes one.
  await page.getByRole("button", { name: "Αφαίρεση περιοχής Κολύμπια" }).click();
  await expect(chipNames(page)).toHaveText(["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου"]);
  await page.getByTestId("radius-input").fill("7");

  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  expect(postedJob(calls)?.["nearby_destinations"]).toEqual(["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου"]);
  expect(postedJob(calls)?.["radius_km"]).toBe(7);
});

test("the nearby areas stop at eight", async ({ page }) => {
  await mockMapRound6(page, {
    restorable: false,
    nearby: ["Περιοχή 1", "Περιοχή 2", "Περιοχή 3", "Περιοχή 4", "Περιοχή 5", "Περιοχή 6", "Περιοχή 7"],
  });

  await page.goto("/map");
  await expect(chipNames(page)).toHaveCount(7);
  const areaInput = page.getByTestId("nearby-input");
  await areaInput.fill("Λίνδος");
  await areaInput.press("Enter");
  await expect(chipNames(page)).toHaveCount(8);
  await expect(areaInput).toBeDisabled();
  await expect(page.getByRole("button", { name: "Προσθήκη", exact: true })).toBeDisabled();

  await page.getByRole("button", { name: "Αφαίρεση περιοχής Περιοχή 3" }).click();
  await expect(chipNames(page)).toHaveCount(7);
  await expect(areaInput).toBeEnabled();
});

test("restoring the latest search shows the size, areas and radius it ran with", async ({ page }) => {
  const calls: RecordedCall[] = [];
  // The job ran with 1 area, 7 km and 25 hotels; the server's defaults are 4
  // areas and the form's 10 km / 40. Its areas are real, so all three ride along.
  await mockMapRound6(page, { calls, job: { nearby_destinations: ["Ιξιά"], radius_km: 7, filters_payload: { limit: 25 } } });

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  // A previous search is on screen, so the form stays folded until asked for.
  await expect(page.locator(".filters-sidebar")).toHaveCount(0);
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await expect(chipNames(page)).toHaveText(["Ιξιά"]);
  await expect(page.getByTestId("radius-input")).toHaveValue("7");
  await expect(page.getByTestId("limit-input")).toHaveValue("25");

  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect.poll(() => postedJob(calls)).toBeTruthy();
  expect(postedJob(calls)?.["nearby_destinations"]).toEqual(["Ιξιά"]);
  expect(postedJob(calls)?.["radius_km"]).toBe(7);
});

test("a restored job from before Round 6 keeps the default size, areas and radius", async ({ page }) => {
  // The migration adds both columns without a backfill: older jobs answer null.
  // Their limit (12 in the fixture) never chose today's areas, so it stays out too.
  await mockMapRound6(page, { job: { nearby_destinations: null, radius_km: null }, nearby: ["Ιξιά", "Κολύμπια"] });

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await expect(chipNames(page)).toHaveText(["Ιξιά", "Κολύμπια"]);
  await expect(page.getByTestId("radius-input")).toHaveValue("10");
  await expect(page.getByTestId("limit-input")).toHaveValue("40");
});

test("a restored job without areas keeps the default size, areas and radius but restores the stay", async ({ page }) => {
  // A job created from a pre-Round-6 restore stored an empty list, no radius
  // and the old limit 10. None of that was a choice the user made, so none of
  // it rides along — otherwise every later search silently shrinks to 10.
  await mockMapRound6(page, {
    job: { nearby_destinations: [], radius_km: null, filters_payload: { limit: 10 } },
    nearby: ["Ιξιά", "Κολύμπια"],
  });

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await expect(page.getByTestId("limit-input")).toHaveValue("40");
  await expect(chipNames(page)).toHaveText(["Ιξιά", "Κολύμπια"]);
  await expect(page.getByTestId("radius-input")).toHaveValue("10");
  // The stay itself is the job's own and still restores.
  await expect(page.locator('input[type="date"]').first()).toHaveValue("2030-06-01");
});

test("a running search shows its stage, its counts and an elapsed clock until it completes", async ({ page }) => {
  // Started 2:05 ago, by the job's own clock (spec §4.2: from started_at).
  const startedAt = new Date(Date.now() - 125_000).toISOString();
  const running = { ...COMPLETED_JOB, id: NEW_JOB_ID, status: "running", started_at: startedAt, finished_at: null, result_summary: null };
  const runningWith = (progress: Record<string, unknown>) => ({ ...running, result_summary: { progress } });
  await mockMapRound6(page, {
    restorable: false,
    polls: [
      running,
      runningWith({ stage: "scout", destinations_total: 5, hotels_found: 37, updated_at: startedAt }),
      runningWith({ stage: "deep_crawl", done: 12, total: 48, destinations_total: 5, hotels_found: 61, hotels_in_radius: 48 }),
      runningWith({ stage: "persist", done: 48, total: 48, destinations_total: 5, hotels_found: 61, hotels_in_radius: 48 }),
      { ...COMPLETED_JOB, id: NEW_JOB_ID },
    ],
  });

  await page.goto("/map");
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();

  const progressText = page.getByTestId("scrape-progress-text");
  // No progress in the job yet: the poll loop has nothing to count.
  await expect(progressText).toHaveText("Εκκίνηση…");
  const clock = page.getByTestId("scrape-elapsed");
  await expect(clock).toHaveText(/^2:\d\d$/);
  const firstReading = await clock.textContent();
  await expect.poll(() => clock.textContent()).not.toBe(firstReading);

  // One 5 s poll per stage.
  await expect(progressText).toHaveText("Στάδιο 1/2 — Αναζήτηση καταλυμάτων σε 5 περιοχές… βρέθηκαν 37");
  await expect(progressText).toHaveText("Στάδιο 2/2 — Τιμές δωματίων: 12/48 καταλύματα");
  await expect(progressText).toHaveText("Αποθήκευση αποτελεσμάτων…");

  // Completed: the results replace the progress, clock and all.
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(page.getByTestId("scrape-progress")).toHaveCount(0);
});

test("one market summary box reads /market/summary and the header counts hotels", async ({ page }) => {
  const markerReads: URLSearchParams[] = [];
  const summaryReads: URLSearchParams[] = [];
  // Alpha twice (a second package of the same hotel): three hotels, four rows.
  const secondAlphaPackage = { ...MAP_MARKERS[0], room_package_id: "alpha-twin", room_type: "Twin Room", price_per_night_eur: 70 };
  await mockMapRound6(page, { markerRows: [...MAP_MARKERS, secondAlphaPackage] });
  // Registered after the helper: LIFO, so these recording routes win.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    markerReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: [...MAP_MARKERS, secondAlphaPackage] });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => {
    summaryReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: MARKET_SUMMARY });
  });

  await page.goto("/map");

  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  const box = page.getByTestId("market-summary");
  await expect(box).toHaveCount(1);
  await expect(page.getByTestId("market-summary-counts")).toHaveText(
    "Καταλύματα 17 · Καταγραφές 42 · Ίδια κατηγορία 11 · Παρόμοια 6",
  );
  // `\s` before €: el-GR currency formatting puts a no-break space there.
  await expect(box.locator(".kpi-strip > div")).toHaveText([
    /^Ελάχιστη\s*60\s€$/, /^Διάμεση\s*110\s€$/, /^Μέση\s*118\s€$/, /^Μέγιστη\s*240\s€$/, /^Μέση βαθμολογία\s*8\.6$/,
  ]);
  // The two boxes that used to disagree are one: no marker-derived grid, no second title.
  const sidebar = page.locator(".results-sidebar");
  await expect(sidebar.locator(".stats-grid")).toHaveCount(0);
  await expect(sidebar).not.toContainText("Η αγορά της περιοχής");
  await expect(sidebar).not.toContainText("Χαμηλότερη τιμή");

  // Markers and summary read the same whole job, so the header and the box describe one set.
  await expect.poll(() => summaryReads.length).toBeGreaterThan(0);
  expect(markerReads.at(-1)?.get("scrape_job_id")).toBe(JOB_ID);
  expect(markerReads.at(-1)?.get("limit")).toBe(summaryReads.at(-1)?.get("limit"));
});

test("a summary from an older API leaves out the category counts instead of inventing zeros", async ({ page }) => {
  await mockMapRound6(page);
  const { same_category_hotels: _same, similar_hotels: _similar, ...olderSummary } = MARKET_SUMMARY;
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: olderSummary }));

  await page.goto("/map");

  await expect(page.getByTestId("market-summary-counts")).toHaveText("Καταλύματα 17 · Καταγραφές 42");
});

const markers = (page: Page) => page.locator(".roomrate-marker-dot");
const markerFor = (page: Page, hotel: string) => page.locator(`.roomrate-marker-dot[aria-label*="${hotel}"]`);
const cardFor = (page: Page, hotel: string) => page.locator(".competitor-card", { hasText: hotel });
const openPopup = (page: Page) => page.locator(".mapboxgl-popup");

test("an open marker popup survives a card tick, the map filter and a facility re-read", async ({ page }) => {
  const reads: ReadCall[] = [];
  // No «Εσείς»: its 10 km circle zooms the fit out until the markers stack,
  // and this scenario clicks one. The owner's marker has its own scenarios.
  await mockMapRound6(page, { reads, ownProperty: null });
  // Tall enough that the facility list at the foot of the filters form is clickable.
  await page.setViewportSize({ width: 1280, height: 1400 });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  await markerFor(page, "Hotel Beta").click();
  await expect(openPopup(page)).toHaveCount(1);
  await expect(openPopup(page)).toContainText("Hotel Beta");

  // Every repaint used to drop and rebuild every marker, closing the popup with it.
  await cardFor(page, "Hotel Beta").locator("input[type=checkbox]").check();
  await expect(markerFor(page, "Hotel Beta")).toHaveAttribute("data-selected", "true");
  await expect(openPopup(page)).toContainText("Hotel Beta");

  const onlySelected = page.getByTestId("only-selected-toggle");
  await onlySelected.check();
  await expect(markers(page)).toHaveCount(1);
  await expect(openPopup(page)).toContainText("Hotel Beta");
  await onlySelected.uncheck();
  await expect(markers(page)).toHaveCount(3);
  await expect(openPopup(page)).toContainText("Hotel Beta");

  // A facility tick re-reads the whole job; the summary read is the last thing that re-read starts.
  await page.getByRole("button", { name: "Φίλτρα", exact: true }).click();
  await page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" }).locator("input[type=checkbox]").check();
  await expect.poll(() => readsOf(reads, "/market/summary").filter((query) => query.has("amenities")).length).toBe(1);
  await expect(markers(page)).toHaveCount(3);
  await expect(openPopup(page)).toHaveCount(1);
  await expect(openPopup(page)).toContainText("Hotel Beta");
});

test("each marker shows its price, an outline for a similar room and a price band relative to the map", async ({ page }) => {
  // 200/230/260: the old fixed 75/130 thresholds painted all three «high»; the
  // tertiles of what is on the map split them low/mid/high.
  const markerRows = [
    { ...MAP_MARKERS[0], price_per_night_eur: 200 },
    { ...MAP_MARKERS[1], price_per_night_eur: 230 },
    { ...MAP_MARKERS[2], price_per_night_eur: 260 },
  ];
  await mockMapRound6(page, { markerRows });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  // `\s`: el-GR currency formatting puts a no-break space before €.
  await expect(markerFor(page, "Hotel Alpha")).toHaveText(/^200\s€$/);
  await expect(markerFor(page, "Hotel Beta")).toHaveText(/^230\s€$/);
  await expect(markerFor(page, "Hotel Alpha")).toHaveClass(/\broomrate-marker-dot-low\b/);
  await expect(markerFor(page, "Hotel Beta")).toHaveClass(/\broomrate-marker-dot-mid\b/);
  await expect(markerFor(page, "Hotel Gamma")).toHaveClass(/\broomrate-marker-dot-high\b/);
  // Beta is a similar room (outlined); Alpha and Gamma are the owner's category (filled).
  await expect(markerFor(page, "Hotel Beta")).toHaveClass(/\bis-similar\b/);
  await expect(markerFor(page, "Hotel Alpha")).not.toHaveClass(/\bis-similar\b/);
  await expect(markerFor(page, "Hotel Gamma")).not.toHaveClass(/\bis-similar\b/);
  await expect(markerFor(page, "Hotel Beta")).toHaveAttribute("aria-label", /^Hotel Beta, 230\s€ ανά βράδυ, παρόμοιο$/);

  const legend = page.getByTestId("map-legend");
  await expect(legend.locator(".map-legend-item")).toHaveText([
    "Χαμηλή τιμή", "Μεσαία τιμή", "Υψηλή τιμή", "Ίδια κατηγορία", "Παρόμοιο", "Εσείς",
  ]);

  // The bands describe the markers on the map now, not the whole job: with
  // Beta and Gamma alone, 230 is the low end and 260 the high one.
  await cardFor(page, "Hotel Beta").locator("input[type=checkbox]").check();
  await cardFor(page, "Hotel Gamma").locator("input[type=checkbox]").check();
  await page.getByTestId("only-selected-toggle").check();
  await expect(markers(page)).toHaveCount(2);
  await expect(markerFor(page, "Hotel Beta")).toHaveClass(/\broomrate-marker-dot-low\b/);
  await expect(markerFor(page, "Hotel Gamma")).toHaveClass(/\broomrate-marker-dot-high\b/);
});

test("a marker popup adds the distance, the category and a Booking link when the hotel has them", async ({ page }) => {
  // A real Booking URL carries a query string: its `&` must survive the popup's HTML escaping.
  const betaUrl = "https://www.booking.com/hotel/gr/beta.html?aid=304142&checkin=2030-06-01";
  // No «Εσείς» (see the popup scenario above): its circle would stack the markers clicked here.
  await mockMapRound6(page, {
    ownProperty: null,
    markerRows: [MAP_MARKERS[0], { ...MAP_MARKERS[1], booking_url: betaUrl }, MAP_MARKERS[2]],
  });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  await markerFor(page, "Hotel Beta").click();
  const popup = openPopup(page);
  await expect(popup).toContainText("Hotel Beta");
  // Greek decimal comma, one decimal.
  await expect(popup.locator(".roomrate-popup__distance")).toHaveText("Απόσταση 0,4 km");
  await expect(popup.locator(".roomrate-popup__category")).toHaveText("Παρόμοιο");
  const booking = popup.getByRole("link", { name: "Άνοιγμα στο Booking" });
  await expect(booking).toHaveAttribute("href", betaUrl);
  await expect(booking).toHaveAttribute("target", "_blank");
  await expect(booking).toHaveAttribute("rel", "noopener");
  await popup.getByRole("button", { name: "Close popup" }).click();
  await expect(popup).toHaveCount(0);

  // Gamma: the owner's category, but no distance and no URL — neither line is invented.
  await markerFor(page, "Hotel Gamma").click();
  await expect(popup).toContainText("Hotel Gamma");
  await expect(popup.locator(".roomrate-popup__category")).toHaveText("Ίδια κατηγορία");
  await expect(popup).not.toContainText("Απόσταση");
  await expect(popup.getByRole("link", { name: "Άνοιγμα στο Booking" })).toHaveCount(0);
});

const ownMarker = (page: Page) => page.locator(".roomrate-marker-own");

test("the owner's hotel is on the map as «Εσείς» with its Booking price and its radius circle", async ({ page }) => {
  const reads: ReadCall[] = [];
  // 7.5 km and 92 €: non-default values, and a radius that needs the Greek decimal comma.
  await mockMapRound6(page, { reads, ownProperty: { ...OWN_PROPERTY, radius_km: 7.5 } });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  await expect(ownMarker(page)).toHaveCount(1);
  await expect(ownMarker(page)).toHaveText("Εσείς");
  // «Εσείς» is not a competitor: the competitor markers are still three.
  await expect(ownMarker(page)).not.toHaveClass(/roomrate-marker-dot/);
  // The circle itself is WebGL; the map container carries the radius the map was given.
  await expect(page.locator(".roomrate-map")).toHaveAttribute("data-radius-km", "7.5");
  // Read for the restored job, for this owner.
  const ownReads = readsOf(reads, "/maps/own-property");
  expect(ownReads.at(-1)?.get("owned_property_id")).toBe(OWNED_PROPERTY_ID);
  expect(ownReads.at(-1)?.get("scrape_job_id")).toBe(JOB_ID);

  await ownMarker(page).click();
  const popup = openPopup(page);
  await expect(popup).toContainText("E2E Test Hotel");
  await expect(popup).toContainText(/Η τιμή σας στο Booking: 92\s€/);
  await expect(popup).toContainText("Ακτίνα 7,5 km");
  await expect(page.getByTestId("own-location-notice")).toHaveCount(0);
  await expect(page.getByTestId("map-legend")).toBeVisible();
});

test("an owner the API cannot place gets no «Εσείς» and no circle, and the map says why", async ({ page }) => {
  const reads: ReadCall[] = [];
  // No job to restore: the owner's hotel is still read on page load, without a job id.
  await mockMapRound6(page, {
    restorable: false,
    reads,
    ownProperty: { ...OWN_PROPERTY, latitude: null, longitude: null, radius_km: null, price_per_night_eur: null },
  });

  await page.goto("/map");
  await expect(page.getByTestId("own-location-notice")).toHaveText("Δεν βρέθηκαν συντεταγμένες για το κατάλυμά σας");
  await expect(ownMarker(page)).toHaveCount(0);
  await expect(page.locator(".roomrate-map")).not.toHaveAttribute("data-radius-km");
  const ownReads = readsOf(reads, "/maps/own-property");
  expect(ownReads.length).toBeGreaterThan(0);
  expect(ownReads[0].get("owned_property_id")).toBe(OWNED_PROPERTY_ID);
  expect(ownReads[0].has("scrape_job_id")).toBe(false);
});

test("each card shows the hotel's distance and a category chip", async ({ page }) => {
  await mockMapRound6(page);

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(cardFor(page, "Hotel Alpha").getByTestId("card-distance")).toHaveText("1,2 km");
  await expect(cardFor(page, "Hotel Beta").getByTestId("card-distance")).toHaveText("0,4 km");
  await expect(cardFor(page, "Hotel Alpha").getByTestId("card-category")).toHaveText("Ίδια κατηγορία");
  await expect(cardFor(page, "Hotel Beta").getByTestId("card-category")).toHaveText("Παρόμοιο");
  // Gamma has no distance (the owner cannot be placed against it): no «— km» either.
  await expect(cardFor(page, "Hotel Gamma").getByTestId("card-distance")).toHaveCount(0);
  await expect(cardFor(page, "Hotel Gamma").getByTestId("card-category")).toHaveText("Ίδια κατηγορία");
});

test("«Μόνο συγκρίσιμα» is on by default, and turning it off re-reads the job with every room", async ({ page }) => {
  const reads: ReadCall[] = [];
  await mockMapRound6(page, { reads });
  // Registered after the helper (LIFO wins): comparable_only=true drops Beta;
  // false returns everything, Beta carrying the agent's «not comparable».
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const url = new URL(route.request().url());
    reads.push({ path: url.pathname, query: url.searchParams });
    const comparableOnly = url.searchParams.get("comparable_only") === "true";
    const rows = MAP_MARKERS.map((row) => ({ ...row, comparable: row.category_match === "same" }));
    return route.fulfill({ json: comparableOnly ? rows.filter((row) => row.comparable) : rows });
  });
  // Tall enough for the match controls at the foot of the results column.
  await page.setViewportSize({ width: 1280, height: 1400 });

  await page.goto("/map");
  const comparableOnly = page.getByTestId("comparable-toggle");
  await expect(comparableOnly).toBeChecked();
  await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();
  await expect(cardFor(page, "Hotel Beta")).toHaveCount(0);
  // The match list is on, so its read is part of what the switch re-reads.
  await page.locator(".match-controls .checkbox-row", { hasText: "Ταίριασμα με το δικό μου δωμάτιο" })
    .locator("input[type=checkbox]").check();
  await expect.poll(() => matchListReads(reads).length).toBeGreaterThan(0);
  await expect.poll(() => readsOf(reads, "/market/summary").length).toBe(1);
  // Every read carries the switch and the room; the legacy parameter is gone.
  for (const endpoint of ["/maps/competitors", "/market/summary", "/market/amenities"]) {
    const last = readsOf(reads, endpoint).at(-1)!;
    expect(last.get("comparable_only"), endpoint).toBe("true");
    expect(last.get("owned_room_type_id"), endpoint).toBe(ROOM_TYPE_ID);
  }
  expect(matchListReads(reads).at(-1)?.get("comparable_only")).toBe("true");
  expect(reads.some((read) => read.query.has("include_similar"))).toBe(false);

  await comparableOnly.uncheck();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(cardFor(page, "Hotel Beta").getByTestId("not-comparable-chip")).toHaveText("Μη συγκρίσιμο");
  await expect(cardFor(page, "Hotel Alpha").getByTestId("not-comparable-chip")).toHaveCount(0);
  await expect(markers(page)).toHaveCount(3);
  await expect.poll(() => readsOf(reads, "/market/summary").length).toBe(2);
  await expect.poll(() => matchListReads(reads).at(-1)?.get("comparable_only")).toBe("false");
  for (const endpoint of ["/maps/competitors", "/market/summary"]) {
    const last = readsOf(reads, endpoint).at(-1)!;
    expect(last.get("comparable_only"), endpoint).toBe("false");
    // Still the job on screen, not a market-wide read.
    expect(last.get("scrape_job_id"), endpoint).toBe(JOB_ID);
  }
  expect(readsOf(reads, "/market/amenities").at(-1)?.get("comparable_only")).toBe("false");

  await comparableOnly.check();
  await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();
  await expect.poll(() => readsOf(reads, "/market/summary").length).toBe(3);
  expect(readsOf(reads, "/maps/competitors").at(-1)?.get("comparable_only")).toBe("true");
});

test("«Μόνο συγκρίσιμα» re-reads the job without refitting the camera", async ({ page }) => {
  // No «Εσείς»: like the popup-survival test, so the one fit under test is the
  // markers' own and not the owner's later, separately-counted one.
  await mockMapRound6(page, { ownProperty: null });
  // Registered after the helper (LIFO wins): comparable_only=false adds Beta.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const comparableOnly = new URL(route.request().url()).searchParams.get("comparable_only") === "true";
    return route.fulfill({ json: comparableOnly ? MAP_MARKERS.filter((row) => row.category_match === "same") : MAP_MARKERS });
  });
  await page.setViewportSize({ width: 1280, height: 1400 });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(2);
  // The summary is the load chain's last kick-off: once it is on screen, the
  // restored job's own fits (marker render, then the post-load re-render) are
  // done and the count below is the settled load-time value.
  await expect(page.getByTestId("market-summary-counts")).toContainText("Καταλύματα 17");
  const mapContainer = page.locator(".roomrate-map");
  const fitsAfterLoad = await mapContainer.getAttribute("data-fit-count");
  expect(Number(fitsAfterLoad)).toBeGreaterThan(0);
  await markerFor(page, "Hotel Alpha").click();
  await expect(openPopup(page)).toContainText("Hotel Alpha");

  // The toggle re-reads the job already on screen: the rows change, the
  // camera (and the open popup) must stay where the user put them.
  const comparableOnly = page.getByTestId("comparable-toggle");
  await comparableOnly.uncheck();
  await expect(markers(page)).toHaveCount(3);
  await expect(mapContainer).toHaveAttribute("data-fit-count", fitsAfterLoad!);
  await expect(openPopup(page)).toContainText("Hotel Alpha");

  await comparableOnly.check();
  await expect(markers(page)).toHaveCount(2);
  await expect(mapContainer).toHaveAttribute("data-fit-count", fitsAfterLoad!);
});

test("the match list sorts by distance, always for the owner's property", async ({ page }) => {
  const reads: ReadCall[] = [];
  await mockMapRound6(page, { reads });
  await page.setViewportSize({ width: 1280, height: 1400 });

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.locator(".match-controls .checkbox-row", { hasText: "Ταίριασμα με το δικό μου δωμάτιο" })
    .locator("input[type=checkbox]").check();
  const sort = page.locator(".match-controls select");
  await expect(sort.locator("option")).toHaveText(["Καλύτερο ταίριασμα", "Τιμή", "Απόσταση"]);
  await sort.selectOption({ label: "Απόσταση" });

  // `sort=distance` is refused (400) without owned_property_id.
  await expect.poll(() => readsOf(reads, "/competitors/").at(-1)?.get("sort")).toBe("distance");
  const read = readsOf(reads, "/competitors/").at(-1)!;
  expect(read.get("owned_property_id")).toBe(OWNED_PROPERTY_ID);
  expect(read.get("scrape_job_id")).toBe(JOB_ID);
  // The order is the server's; the distance it sorted by is on each matched card.
  await expect(page.locator(".matched-card", { hasText: "Hotel Beta" })).toContainText("0,4 km");
});

test("a completed search links to a price recommendation for that very job", async ({ page }) => {
  await mockMapRound6(page, { restorable: false });

  await page.goto("/map");
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  // Nothing searched yet: nothing to recommend a price from.
  await expect(page.getByTestId("pricing-for-search")).toHaveCount(0);
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

  const link = page.getByRole("link", { name: "Σύσταση τιμής για αυτή την αναζήτηση" });
  await expect(link).toHaveAttribute("href", `/pricing?job=${NEW_JOB_ID}`);
  await link.click();
  await expect(page).toHaveURL((url) => url.pathname === "/pricing" && url.searchParams.get("job") === NEW_JOB_ID);
});

test("a restored search links to the pricing page with its own job", async ({ page }) => {
  await mockMapRound6(page);

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(page.getByTestId("pricing-for-search")).toHaveAttribute("href", `/pricing?job=${JOB_ID}`);
});

test("the scrape's warnings are explained under the results banner", async ({ page }) => {
  // 7.5 km on the job: the sentence has to take the radius from the job, with the Greek comma.
  const warnings = ["radius_excluded_all", "nearby_scout_failed:Ιξιά", "radius_skipped_no_coordinates", "a_future_warning"];
  await mockMapRound6(page, {
    restorable: false,
    job: { radius_km: 7.5, result_summary: { ...COMPLETED_JOB.result_summary, warnings } },
  });

  await page.goto("/map");
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  await expect(page.getByTestId("search-warning")).toHaveCount(0);
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

  await expect(page.getByTestId("search-warning")).toHaveText([
    "Κανένα κατάλυμα δεν βρέθηκε μέσα στην ακτίνα των 7,5 km. Ελέγξτε τη θέση του καταλύματός σας ή μεγαλώστε την ακτίνα.",
    "Η αναζήτηση στην περιοχή Ιξιά δεν ολοκληρώθηκε· τα υπόλοιπα αποτελέσματα εμφανίζονται κανονικά.",
    "Η ακτίνα δεν εφαρμόστηκε: το κατάλυμά σας δεν έχει συντεταγμένες.",
  ]);
  // A code this page does not know is not shown raw.
  await expect(page.locator(".filters-sidebar")).not.toContainText("a_future_warning");
  // Under the banner that describes the results.
  const banner = page.locator(".filters-sidebar .alert").first();
  await expect(banner).toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");
  const bannerBox = await banner.boundingBox();
  const firstWarningBox = await page.getByTestId("search-warning").first().boundingBox();
  expect(firstWarningBox!.y).toBeGreaterThan(bannerBox!.y);
});

test("an empty result names the Round 6 stage that emptied it", async ({ page }) => {
  // Nine prices seen, none for a single room, none for 2 of the 3 adults per room
  // (spec §3.4: the capacity stage compares the party per room).
  const resultSummary = {
    version: 1, rows_seen: 9, rows_written: 0, warnings: [],
    filter_counts: { single_rooms: { before: 9, after: 9 }, capacity: { before: 9, after: 0 } },
  };
  await mockMapRound6(page, { markerRows: [], job: { result_summary: resultSummary } });

  await page.goto("/map");
  const results = page.locator(".results-sidebar");
  await expect(results).toContainText("Βρέθηκαν 9 τιμές για λιγότερα άτομα από όσα ζητήσατε");
  await expect(results).toContainText("Η αναζήτηση ολοκληρώθηκε και επέστρεψε τιμές, όλες όμως για λιγότερα άτομα");
  await expect(results).not.toContainText("άλλης κατηγορίας");
});

test("one failed poll mid-scrape keeps the run and its progress alive", async ({ page }) => {
  // Same 5 s-wait shortener as the 360-poll test: only the poll interval.
  await page.addInitScript(() => {
    const realSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) =>
      realSetTimeout(handler, delay === 5000 ? 0 : delay, ...args)) as typeof window.setTimeout;
  });
  const running = { ...COMPLETED_JOB, id: NEW_JOB_ID, status: "running", finished_at: null, result_summary: null };
  // running → a 502 blip → running WITH progress, held until the test lets go:
  // reaching the stage text at all proves the loop rode over the failure.
  const polls: Array<Record<string, unknown> | number> = [
    running,
    502,
    { ...running, result_summary: { progress: { stage: "deep_crawl", done: 12, total: 48 } } },
  ];
  await mockMapRound6(page, { restorable: false, polls });

  await page.goto("/map");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.getByTestId("scrape-progress-text")).toHaveText("Στάδιο 2/2 — Τιμές δωματίων: 12/48 καταλύματα");
  await expect(page.locator(".alert-error")).toHaveCount(0);

  // Only now may the job complete: the mock repeats the last poll body.
  polls.push({ ...COMPLETED_JOB, id: NEW_JOB_ID });
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(markers(page)).toHaveCount(3);
  await expect(page.getByTestId("scrape-progress")).toHaveCount(0);
  await expect(page.locator(".alert-error")).toHaveCount(0);
});

test("five failed polls in a row give up with the API error text", async ({ page }) => {
  await page.addInitScript(() => {
    const realSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) =>
      realSetTimeout(handler, delay === 5000 ? 0 : delay, ...args)) as typeof window.setTimeout;
  });
  const calls: RecordedCall[] = [];
  await mockMapRound6(page, { restorable: false, calls, polls: [502] });
  const polls = () => calls.filter((call) => call.method === "GET" && call.path.endsWith(NEW_JOB_ID)).length;

  await page.goto("/map");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".filters-sidebar .alert-error")).toContainText("Το αίτημα προς το RoomRate API απέτυχε.");
  // Exactly five reads: the give-up came from the consecutive-failure limit, not the 360 cap.
  expect(polls()).toBe(5);
  await expect(page.getByTestId("scrape-progress")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Εύρεση ανταγωνιστών" })).toBeEnabled();
});

test("a running search is polled for 30 minutes (360 polls) before the page gives up", async ({ page }) => {
  // A live Faliraki scrape with four nearby areas takes 10-11 minutes and Apify
  // can be slower on the day: the page must wait as long as the backend's hard
  // timeout (1800 s). Only the 5 s wait between polls is shortened, so 360 polls
  // take seconds; every other timer keeps its real length.
  await page.addInitScript(() => {
    const realSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) =>
      realSetTimeout(handler, delay === 5000 ? 0 : delay, ...args)) as typeof window.setTimeout;
  });
  const calls: RecordedCall[] = [];
  const running = { ...COMPLETED_JOB, id: NEW_JOB_ID, status: "running", finished_at: null, result_summary: null };
  await mockMapRound6(page, { restorable: false, calls, polls: [running] });
  const polls = () => calls.filter((call) => call.method === "GET" && call.path.endsWith(NEW_JOB_ID)).length;

  await page.goto("/map");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  // No mid-run "no error yet" check: with the 5 s wait shortened to 0 ms all
  // 360 polls can finish between two expect.poll samples, making it racy.
  // Exactly 360 reads before the give-up already proves the page did not stop
  // at the old 180 cap (or anywhere earlier).
  await expect(page.locator(".filters-sidebar .alert-error")).toContainText("εκτελείται ακόμη", { timeout: 30_000 });
  expect(polls()).toBe(360);
});

test("zoomed out, competitor markers become dots without their price while «Εσείς» keeps its label", async ({ page }) => {
  // A tight Faliraki-centre cluster and no radius: the fit stays at zoom 14, so
  // the map's own zoom state is «not zoomed out» and never changes under the
  // forced state below.
  const cluster = [
    { ...MAP_MARKERS[0], latitude: 36.34, longitude: 28.2 },
    { ...MAP_MARKERS[1], latitude: 36.3403, longitude: 28.2003 },
    { ...MAP_MARKERS[2], latitude: 36.3406, longitude: 28.2006 },
  ];
  await mockMapRound6(page, {
    markerRows: cluster,
    ownProperty: { ...OWN_PROPERTY, latitude: 36.3402, longitude: 28.2001, radius_km: null },
  });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  await expect(ownMarker(page)).toHaveCount(1);
  const map = page.locator(".roomrate-map");
  // zoomend writes the state onto the container; at 14 the prices show.
  await expect(map).toHaveAttribute("data-zoomed-out", "false");
  const alpha = markerFor(page, "Hotel Alpha");
  const alphaPrice = alpha.locator(".price-pill");
  await expect(alphaPrice).toBeVisible();
  await expect(alphaPrice).toHaveText(/^60\s€$/);

  // The e2e map is never really zoomed out: force the state the map writes below 13.5.
  await map.evaluate((element) => {
    element.classList.add("is-zoomed-out");
    element.setAttribute("data-zoomed-out", "true");
  });
  await expect(alphaPrice).toBeHidden();
  expect((await alpha.boundingBox())!.width).toBeLessThan(20);
  // The price is still in the label, and «Εσείς» keeps its own.
  await expect(alpha).toHaveAttribute("aria-label", /^Hotel Alpha, 60\s€ ανά βράδυ/);
  await expect(ownMarker(page)).toBeVisible();
  await expect(ownMarker(page)).toHaveText("Εσείς");
  expect((await ownMarker(page).boundingBox())!.width).toBeGreaterThan(30);

  await map.evaluate((element) => {
    element.classList.remove("is-zoomed-out");
    element.setAttribute("data-zoomed-out", "false");
  });
  await expect(alphaPrice).toBeVisible();
});

test("offers a later filter emptied are counted as comparable, one in the singular", async ({ page }) => {
  // One price kept by the single-rooms and capacity stages, then cut by the room-name match.
  const resultSummary = {
    version: 1, rows_seen: 1, rows_written: 0, warnings: [],
    filter_counts: { single_rooms: { before: 1, after: 1 }, capacity: { before: 1, after: 1 }, room_name: { before: 1, after: 0 } },
  };
  await mockMapRound6(page, { markerRows: [], job: { result_summary: resultSummary } });

  await page.goto("/map");
  const results = page.locator(".results-sidebar");
  await expect(results).toContainText("Βρέθηκε 1 συγκρίσιμη προσφορά");
  await expect(results).toContainText("Καμία από αυτές δεν πέρασε τα υπόλοιπα φίλτρα");
  // The scraper keeps similar categories now: the count is not «of your category».
  await expect(results).not.toContainText("της κατηγορίας σας");
});

test("when «Μόνο συγκρίσιμα» leaves nothing it says so, and one click shows every room", async ({ page }) => {
  const reads: ReadCall[] = [];
  await mockMapRound6(page, { restorable: false, reads });
  // The server's answer to comparable_only=true: the agent judged nothing comparable.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const url = new URL(route.request().url());
    reads.push({ path: url.pathname, query: url.searchParams });
    return route.fulfill({ json: url.searchParams.get("comparable_only") === "true" ? [] : MAP_MARKERS });
  });

  await page.goto("/map");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".header-actions").getByText("0 ανταγωνιστές")).toBeVisible();
  const comparableOnly = page.getByTestId("comparable-toggle");
  await expect(comparableOnly).toBeChecked();
  const banner = page.locator(".filters-sidebar .alert:not(.alert-error)");
  const results = page.locator(".results-sidebar");
  // Nothing comparable, said in the sidebar, over the map and in the banner
  // — each pointing at the switch, since the rows do exist.
  await expect(results).toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  await expect(results).toContainText("κανένα δεν κρίθηκε συγκρίσιμο με το δωμάτιό σας");
  await expect(page.locator(".map-empty-state")).toContainText("Κανένα συγκρίσιμο δωμάτιο στον χάρτη.");
  await expect(banner).toContainText("Απενεργοποιήστε το «Μόνο συγκρίσιμα»");
  await expect(banner).not.toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");

  await results.getByRole("button", { name: "Εμφάνιση όλων" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(comparableOnly).not.toBeChecked();
  expect(readsOf(reads, "/maps/competitors").at(-1)?.get("comparable_only")).toBe("false");
  await expect(results).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  await expect(banner).toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");
});
