/**
 * Queue Round 4.4: the four map-page items that are about telling the truth.
 *
 * Why its own file: `map-auto-plot.spec.ts` pins what the map DRAWS and
 * `no-second-click.spec.ts` pins out-of-zone repaints. What follows is a
 * different family — the checklist's tracking threshold, the auto-picked room,
 * and a Mapbox failure that must stay a MAP failure — so it gets its own mocks
 * and its own name.
 *
 * The helper file is shared with the other suites and stays frozen, so the
 * per-scenario mocking below is inlined here on purpose.
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

/**
 * The scraper's own account of a run that DID write rows.
 *
 * Non-default on purpose: a job with no summary at all cannot tell an empty
 * screen caused by the read-time facility filter from one the scrape itself
 * produced, so the scenario that asserts the difference has to state the "rows
 * were written" half rather than lean on a missing payload.
 */
const ROWS_WERE_WRITTEN_SUMMARY = {
  version: 1,
  rows_seen: 5,
  // Round 6 (spec §3.4): the scraper's first stages are single rooms and capacity.
  filter_counts: { single_rooms: { before: 5, after: 4 }, capacity: { before: 4, after: 3 } },
  rows_written: 3,
};

/**
 * The same account for a run that wrote NOTHING, and the other half of the
 * truth above.
 *
 * `rows_written: 0` is the one shape where the read-time facility filter
 * cannot be what emptied the screen — there were no rows for it to drop — and
 * the single-rooms stage says what really did. Mirrors the fixture above field
 * for field so the two differ only in the numbers the component reasons about.
 */
const NOTHING_WAS_WRITTEN_SUMMARY = {
  version: 1,
  rows_seen: 4,
  filter_counts: { single_rooms: { before: 4, after: 0 } },
  rows_written: 0,
};

/** The 500 a filtered re-read answers with in the scenario that fails one. */
const FILTERED_READ_FAILURE = "Η ανάγνωση των αποτελεσμάτων απέτυχε.";

const PROP_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROP_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const PROP_GAMMA = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const SUITE_ROOM_TYPE_ID = "33333333-3333-3333-3333-3333333333bb";
const SELECTION_PATH = `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`;

const MARKER_TEMPLATE = {
  room_package_id: null,
  room_type: "Double Room",
  room_type_category: "double",
  property_type: "hotel",
  review_score: 8.4,
  review_count: 120,
  rooms_left: 3,
};

const MAP_MARKERS = [
  { ...MARKER_TEMPLATE, hotel_name: "Hotel Alpha", property_id: PROP_ALPHA, latitude: 36.34, longitude: 28.2, price_per_night_eur: 60 },
  { ...MARKER_TEMPLATE, hotel_name: "Hotel Beta", property_id: PROP_BETA, latitude: 36.35, longitude: 28.21, price_per_night_eur: 120 },
  { ...MARKER_TEMPLATE, hotel_name: "Hotel Gamma", property_id: PROP_GAMMA, latitude: 36.36, longitude: 28.22, price_per_night_eur: 180 },
];

// scrape_runs_count 1: step 3 ticks, step 5 does not — so the checklist is
// never complete and never hides itself while these scenarios read it.
const RESTORABLE_JOB = {
  id: JOB_ID,
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
  filters_payload: { limit: 8 },
  status: "completed",
  scrape_runs_count: 1,
};

const MARKET_SUMMARY = {
  destination: "faliraki",
  check_in: "2030-06-01",
  check_out: "2030-06-05",
  total_records: 42,
  total_hotels: 17,
  price_min_eur: 60,
  price_max_eur: 240,
  price_avg_eur: 118,
  price_median_eur: 110,
  avg_review_score: 8.6,
  rooms_left_total: 55,
};

const DOUBLE_ROOM = {
  id: ROOM_TYPE_ID,
  owned_property_id: OWNED_PROPERTY_ID,
  room_type: "Double Room with Sea View",
  room_type_category: "double",
  is_active: true,
};
const SUITE_ROOM = {
  id: SUITE_ROOM_TYPE_ID,
  owned_property_id: OWNED_PROPERTY_ID,
  room_type: "Junior Suite",
  room_type_category: "suite",
  is_active: true,
};

const cardCheckbox = (page: Page, hotel: string) =>
  page.locator(".competitor-card", { hasText: hotel }).locator("input[type=checkbox]");
const checklist = (page: Page) => page.getByTestId("setup-checklist");

type MockOptions = {
  markerRows?: typeof MAP_MARKERS;
  /** property ids the tracked list reports BEFORE any save. */
  trackedProperties?: string[];
  /** property ids the tracked list reports AFTER a successful save. */
  trackedAfterSave?: string[];
  /** Room catalog; the map auto-picks its first entry when nothing matches. */
  roomCatalog?: unknown[];
  /** What /me claims is selected — a category absent from the catalog forces the auto-pick. */
  selectedRoomTypeCategory?: string | null;
  calls?: RecordedCall[];
  /** false leaves the job list empty, so nothing is restored and no job id scopes the reads. */
  restorable?: boolean;
  /** The restored job's machine-readable result summary, absent unless a scenario needs it. */
  resultSummary?: typeof ROWS_WERE_WRITTEN_SUMMARY;
};

async function mockMapPage(page: Page, options: MockOptions = {}): Promise<void> {
  const {
    markerRows = MAP_MARKERS,
    trackedProperties = [],
    trackedAfterSave,
    roomCatalog = [DOUBLE_ROOM],
    selectedRoomTypeCategory = "double",
    calls,
    restorable = true,
    resultSummary,
  } = options;
  let saved = false;

  await seedBrowserState(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => {
    if (calls) {
      recordCall(calls, route);
    }
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/me`, (route) => {
    if (calls) {
      recordCall(calls, route);
    }
    return route.fulfill({
      json: { ...CURRENT_USER, selected_room_type_category: selectedRoomTypeCategory },
    });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) => {
    if (calls) {
      recordCall(calls, route);
    }
    return route.fulfill({ json: roomCatalog });
  });
  await page.route(`${API}${SELECTION_PATH}`, (route) => {
    if (calls) {
      recordCall(calls, route);
    }
    return route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        selected_room_type_category: "double",
        onboarding_complete: true,
      },
    });
  });
  const job = resultSummary ? { ...RESTORABLE_JOB, result_summary: resultSummary } : RESTORABLE_JOB;
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    // A "Find competitors" click, answered with a job that is already
    // completed: the scenarios that press it are about what the page says once
    // a search has finished, not about polling (no-second-click owns that).
    // Checked before the list branch, because the POST goes to the very path
    // the list is read from.
    if (route.request().method() === "POST") {
      return route.fulfill({ json: job });
    }
    if (path.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: restorable ? [job] : [] });
    }
    return route.fulfill({ json: job });
  });
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => route.fulfill({ json: markerRows }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) => {
    if (calls) {
      recordCall(calls, route);
    }
    if (route.request().method() === "POST") {
      saved = true;
      return route.fulfill({ json: { saved_count: (trackedAfterSave ?? trackedProperties).length } });
    }
    const propertyIds = saved ? (trackedAfterSave ?? trackedProperties) : trackedProperties;
    return route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "double",
        competitors: propertyIds.map((property_id) => ({ property_id, room_package_id: null })),
      },
    });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: MARKET_SUMMARY }));
}

test.describe("checklist tracking threshold follows the market that exists", () => {
  test("a two-competitor market completes the step at two", async ({ page }) => {
    // Faliraki/double really returns 2-3 rooms: "track 3+" was unreachable
    // there, so the checklist could never finish for a real small-market user.
    await mockMapPage(page, {
      markerRows: MAP_MARKERS.slice(0, 2),
      trackedProperties: [PROP_ALPHA, PROP_BETA],
    });
    await mockMapbox(page);

    await page.goto("/map");
    await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();

    // The text must say what it now requires, not the unreachable 3.
    await expect(checklist(page)).toContainText("Παρακολούθηση 2 ανταγωνιστών");
    await expect(checklist(page)).not.toContainText("Παρακολούθηση 3");
    await expect(page.getByTestId("checklist-progress")).toHaveText("4 από 5 βήματα");
    await expect(page.getByTestId("checklist-open-step")).toHaveText(/Πρώτη σύσταση τιμής/);
  });

  test("a three-competitor market still asks for three", async ({ page }) => {
    // The rule is min(3, available), not "whatever is tracked": two out of
    // three tracked leaves the step open, and the label keeps saying 3.
    await mockMapPage(page, { trackedProperties: [PROP_ALPHA, PROP_BETA] });
    await mockMapbox(page);

    await page.goto("/map");
    await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

    await expect(checklist(page)).toContainText("Παρακολούθηση 3 ανταγωνιστών");
    await expect(page.getByTestId("checklist-progress")).toHaveText("3 από 5 βήματα");
    await expect(page.getByTestId("checklist-open-step")).toHaveText(/Παρακολούθηση 3 ανταγωνιστών/);
  });
});

test("saving tracked competitors ticks the checklist step without a reload", async ({ page }) => {
  // The save used to leave the checklist stale until the next map load: the
  // POST never re-fed the progress service the way the load path does.
  await mockMapPage(page, {
    trackedProperties: [],
    trackedAfterSave: [PROP_ALPHA, PROP_BETA, PROP_GAMMA],
  });
  await mockMapbox(page);

  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(page.getByTestId("checklist-progress")).toHaveText("3 από 5 βήματα");

  await cardCheckbox(page, "Hotel Alpha").check();
  await cardCheckbox(page, "Hotel Beta").check();
  await cardCheckbox(page, "Hotel Gamma").check();
  await page.getByRole("button", { name: "Προσθήκη επιλεγμένων στην παρακολούθηση" }).click();

  await expect(page.getByText("Προστέθηκαν 3 δωμάτια ανταγωνιστών στην παρακολούθηση")).toBeVisible();
  // No reload anywhere above.
  await expect(page.getByTestId("checklist-progress")).toHaveText("4 από 5 βήματα");
});

test("a facility filter reads in Greek and travels to the backend in English", async ({ page }) => {
  // The checkbox text and the `amenities` query value used to be one English
  // string. Round 5.1 split them (`{ value, label }`) because translating the
  // shared string would have quietly matched nothing on the backend — an empty
  // result set, not an error. This pins both halves in one scenario: the label
  // the hotelier reads is Greek, and the value that leaves the browser is the
  // untouched Booking facility name.
  //
  // `restorable: true` (the default) is load-bearing, and it used to be the
  // opposite: this pin was written around a page with NO job on screen, because
  // buildMarkerParams appended the filter only to reads that were not scoped to
  // a scrape job. That premise WAS the bug — every read after a search is
  // job-scoped, so the facility checkboxes reached the backend nowhere the
  // hotelier could see. A restored job is the page's normal state, and both of
  // its reads have to carry the filter while staying scoped to the job.
  const markerReads: URLSearchParams[] = [];
  const summaryReads: URLSearchParams[] = [];
  await mockMapPage(page);
  await mockMapbox(page);
  // Registered AFTER mockMapPage: Playwright resolves routes LIFO, so these
  // recording handlers win over the suite's plain ones for the same patterns.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    markerReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: MAP_MARKERS });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => {
    summaryReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: MARKET_SUMMARY });
  });

  // Taller than the default 720px: the facility list and the room-matching
  // controls are the LAST things in their sidebars, and at 720 they sit
  // outside their scroll container's clip, where a real click is swallowed by
  // the sidebar. The scenario is about the value/label split, not about
  // scrolling, so it gives itself the room to click for real.
  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα" }).click();

  const wifi = page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" });
  await expect(wifi).toBeVisible();
  // No English left on the control the user is reading.
  await expect(page.locator(".amenity-list")).not.toContainText("Free WiFi");

  // The restore's own reads carried no filter; everything asserted below is
  // about the reads the tick itself causes.
  //
  // The marker read is already in: the count above cannot be on screen without
  // it. The summary read is fired and forgotten (loadMarketSummary is not
  // awaited), so it is waited FOR rather than assumed — a baseline captured at
  // 0 would be beaten by the restore's own summary read landing late, and the
  // "the tick re-read the summary too" assertion below would pass without the
  // tick having done anything.
  const markersBeforeTick = markerReads.length;
  await expect.poll(() => summaryReads.length).toBe(1);
  const summariesBeforeTick = summaryReads.length;
  await wifi.locator("input[type=checkbox]").check();

  // A tick the page never re-reads for is a dead control: the rows on screen
  // are the unfiltered ones until both job-scoped reads run again.
  await expect.poll(() => markerReads.length).toBeGreaterThan(markersBeforeTick);
  await expect.poll(() => summaryReads.length).toBeGreaterThan(summariesBeforeTick);
  for (const read of [markerReads.at(-1)!, summaryReads.at(-1)!]) {
    // What leaves the browser is the Booking facility name, never the label.
    expect(read.getAll("amenities")).toEqual(["Free WiFi"]);
    // ...and it travels ON the job-scoped read rather than instead of it.
    expect(read.get("scrape_job_id")).toBe(JOB_ID);
  }
});

test("a facility filter that hides every row says so instead of blaming the search", async ({ page }) => {
  // The facility checkboxes are a READ filter: the job's rows are all still
  // there, and the user's own tick is what empties the screen. Answering that
  // with «Δεν βρέθηκαν συγκρίσιμα δωμάτια» blames the market for the user's
  // click, and the other-category wording blames their room catalog — both send
  // them to re-run an Apify-costing search that would return exactly the same
  // rows. The job's summary says 3 rows were written, so neither is true here.
  //
  // Two rooms in the catalog because the dropdown is the third control that
  // moves the facility selection — the two checkbox handlers re-read the job,
  // and it did not (see the change of room below).
  const amenityReads: URLSearchParams[] = [];
  await mockMapPage(page, {
    resultSummary: ROWS_WERE_WRITTEN_SUMMARY,
    roomCatalog: [DOUBLE_ROOM, SUITE_ROOM],
  });
  await mockMapbox(page);
  // The backend applies `amenities` when the rows are READ, so one and the same
  // completed job answers with rows or with nothing, by the filter alone.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const filtered = new URL(route.request().url()).searchParams.getAll("amenities").length > 0;
    return route.fulfill({ json: filtered ? [] : MAP_MARKERS });
  });
  // Recorded so the room change below is waited FOR, not waited after: it is
  // the one step whose correct outcome is "nothing on screen moves", and a
  // bare assertion would pass before the handler had even run.
  await page.route(`${API}/api/v1/market/amenities**`, (route) => {
    amenityReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: [] });
  });

  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα" }).click();

  // A search of the user's own, because the banner this scenario also pins
  // belongs to one: the restore leaves a different banner up, and that one is
  // true whatever the filter does. The mocked job comes back already
  // completed, so the click is one step, not a poll.
  const banner = page.locator(".filters-sidebar .alert:not(.alert-error)");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(banner).toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");

  const wifi = page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" })
    .locator("input[type=checkbox]");
  await wifi.check();

  const results = page.locator(".results-sidebar");
  await expect(results).toContainText("Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα");
  // Neither of the two answers this state used to give: nothing found, or
  // every offer cut by the scraper's own stages (Round 6: single rooms, capacity).
  await expect(results).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  await expect(results).not.toContainText("όλες όμως για");
  // The map is where the user is looking, and it says the same thing.
  await expect(page.locator(".map-empty-state")).toContainText("Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα");
  // The banner is the third place the same screen is described, and it used to
  // go on claiming the opposite: only the map filter ever refreshed it, so
  // «Όλα τα αποτελέσματα εμφανίζονται στον χάρτη» stayed up over an empty map.
  await expect(banner).toContainText("τα φίλτρα παροχών δεν αφήνουν κανένα στον χάρτη");
  await expect(banner).not.toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");

  // Changing the room used to wipe the ticks without re-reading the rows they
  // filter: the boxes went blank while the screen kept showing the FILTERED
  // (empty) result set, so this job — 3 rows written, by its own summary —
  // answered «Δεν βρέθηκαν συγκρίσιμα δωμάτια» and sent the user to re-run an
  // Apify-costing search for rows that were already there.
  const amenityReadsBeforeRoomChange = amenityReads.length;
  await page.getByTestId("room-select").selectOption({ label: "Junior Suite (suite)" });
  await expect.poll(() => amenityReads.length).toBeGreaterThan(amenityReadsBeforeRoomChange);
  await expect(wifi).toBeChecked();
  await expect(results).toContainText("Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα");
  await expect(results).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");

  // The way out is one click from the message itself, and it really brings the
  // rows back — the filter is undone on the backend read, not just on screen.
  await results.getByRole("button", { name: "Καθαρισμός φίλτρων παροχών" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(results).not.toContainText("Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα");
  await expect(cardCheckbox(page, "Hotel Alpha")).toBeVisible();
  // ...and the banner comes back with them, from the same mechanism.
  await expect(banner).toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");
});

test("a filtered re-read that fails is the read's error, never the filter's doing", async ({ page }) => {
  // A failed re-read leaves exactly the shape the amenity branch recognises:
  // ticks on screen, no rows, and the job still behind them. setError only
  // moves the RESULTS status to "error" — so, ungated, the map answered a 500
  // with «Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα» and sent the user to
  // untick boxes while the real failure sat in the sidebar beside it.
  await mockMapPage(page, { resultSummary: ROWS_WERE_WRITTEN_SUMMARY });
  await mockMapbox(page);
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const filtered = new URL(route.request().url()).searchParams.getAll("amenities").length > 0;
    return filtered
      ? route.fulfill({ status: 500, json: { detail: FILTERED_READ_FAILURE } })
      : route.fulfill({ json: MAP_MARKERS });
  });

  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα" }).click();
  await page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" })
    .locator("input[type=checkbox]").check();

  // The error slot is where the failure is reported, and it appearing is also
  // what holds everything below: it is written by the catch of the very read
  // the assertions are about.
  await expect(page.locator(".filters-sidebar .alert-error")).toContainText(FILTERED_READ_FAILURE);
  // The map says nothing about facilities: the filter did not empty this
  // screen, a failed read did, and there is nothing to untick that would help.
  await expect(page.locator(".map-empty-state")).not.toContainText("Τα φίλτρα παροχών κρύβουν");
  await expect(page.locator(".results-sidebar")).not.toContainText("Καθαρισμός φίλτρων παροχών");
});

test("a filter tick on a job that wrote nothing keeps the scrape's own answer", async ({ page }) => {
  // The other side of the same line. `rows_written: 0` is the one summary the
  // facility filter cannot be blamed for: the scrape wrote no rows, so none
  // were dropped when they were read back, and the single-rooms stage says what
  // really happened. Mutating that half of amenityFilterHidesEverything to
  // `true` makes a tick claim the filter did it — blaming the user's click for
  // a market that only had single rooms.
  const filteredSummaryReads: URLSearchParams[] = [];
  await mockMapPage(page, { markerRows: [], resultSummary: NOTHING_WAS_WRITTEN_SUMMARY });
  await mockMapbox(page);
  await page.route(`${API}/api/v1/market/summary**`, (route) => {
    const params = new URL(route.request().url()).searchParams;
    if (params.getAll("amenities").length) {
      filteredSummaryReads.push(params);
    }
    return route.fulfill({ json: MARKET_SUMMARY });
  });

  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  const results = page.locator(".results-sidebar");
  await expect(results).toContainText("Βρέθηκαν 4 προσφορές για μονόκλινα δωμάτια");
  await page.getByRole("button", { name: "Φίλτρα" }).click();
  await page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" })
    .locator("input[type=checkbox]").check();

  // Held on the re-read the tick causes: the summary read is the LAST thing
  // loadMarkers starts, so once a FILTERED one has gone out the post-tick
  // screen is the one being read — no timer, and no way to assert the pre-tick
  // render by accident.
  await expect.poll(() => filteredSummaryReads.length).toBeGreaterThan(0);

  await expect(results).toContainText("Βρέθηκαν 4 προσφορές για μονόκλινα δωμάτια");
  await expect(results).not.toContainText("Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα");
  await expect(results.getByRole("button", { name: "Καθαρισμός φίλτρων παροχών" })).toHaveCount(0);
  await expect(page.locator(".map-empty-state")).not.toContainText("Τα φίλτρα παροχών κρύβουν");
});

test("a search run with the filter on says so instead of blaming the exclusion", async ({ page }) => {
  // findCompetitors writes the banner itself, and its own "nothing came back"
  // sentence blames the own-property exclusion — which is not what emptied
  // this screen. Same contradiction as the banner above, reached from the
  // other side: the user reacts to an empty filtered screen by pressing Find.
  await mockMapPage(page, { resultSummary: ROWS_WERE_WRITTEN_SUMMARY });
  await mockMapbox(page);
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const filtered = new URL(route.request().url()).searchParams.getAll("amenities").length > 0;
    return route.fulfill({ json: filtered ? [] : MAP_MARKERS });
  });

  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα" }).click();
  await page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" })
    .locator("input[type=checkbox]").check();
  await expect(page.locator(".results-sidebar")).toContainText("Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα");

  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  const banner = page.locator(".filters-sidebar .alert:not(.alert-error)");
  await expect(banner).toContainText("τα φίλτρα παροχών δεν αφήνουν κανένα στον χάρτη");
  await expect(banner).not.toContainText("αφού εξαιρέθηκε το δικό σας κατάλυμα");
});

test.describe("the auto-picked room says so and goes through the service", () => {
  test("auto-pick announces itself, invalidates /me, and clears when the user chooses", async ({
    page,
  }) => {
    const calls: RecordedCall[] = [];
    // /me claims a category the catalog no longer has, so ensureSelectedRoom
    // falls back to the catalog's first room — the same silent auto-pick the
    // room-step skip produces.
    await mockMapPage(page, {
      roomCatalog: [DOUBLE_ROOM, SUITE_ROOM],
      selectedRoomTypeCategory: "family",
      calls,
    });
    await mockMapbox(page);

    await page.goto("/map");
    await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

    // No Filters click here, on purpose: the filters sidebar starts collapsed
    // and the real skip path never expands it, so a notice that only lives in
    // there is a notice nobody reads. It has to be legible from the panel that
    // is always on screen.
    await expect(page.getByRole("button", { name: "Φίλτρα" })).toBeVisible();
    const hint = page.locator(".results-sidebar").getByTestId("auto-picked-room-hint");
    await expect(hint).toContainText("Επιλέχθηκε αυτόματα το πρώτο δωμάτιο του καταλόγου");

    // Routed through OnboardingService.selectRoomType: same PUT, plus the /me
    // invalidation the raw api.put never did.
    await expect
      .poll(() => calls.filter((call) => call.path === SELECTION_PATH && call.method === "PUT").length)
      .toBe(1);
    const putIndex = calls.findIndex((call) => call.path === SELECTION_PATH && call.method === "PUT");

    // The user makes the choice their own: the notice has nothing left to say.
    // The room dropdown sits above it in the results column, filters closed.
    await page.getByTestId("room-select").selectOption({ label: "Junior Suite (suite)" });
    await expect(page.getByTestId("auto-picked-room-hint")).toHaveCount(0);

    // A later page reading /me must not be served the pre-PUT cache.
    await page.getByRole("link", { name: "Ρυθμίσεις" }).click();
    await expect(page).toHaveURL(/\/settings/);
    await expect
      .poll(() => calls.slice(putIndex + 1).filter((call) => call.path === "/api/v1/me").length)
      .toBeGreaterThan(0);

    // ...and it does not follow the user around. What carries the choice back
    // is the workflow-stored roomTypeId, NOT the persisted PUT: this suite's
    // /me keeps answering "family" no matter what was PUT, so on return
    // ensureSelectedRoom finds no matching category and it is the stored room
    // id that matches instead — which is equally "somebody chose this", so no
    // auto-pick is reported.
    await page.goBack();
    // The double job cannot be restored for the suite room, so the form opens
    // by itself (Round 6, spec §4.1). Clicking «Φίλτρα» raced that: under load
    // the button was already gone when the click landed.
    await expect(page.locator(".filters-sidebar")).toBeVisible();
    await expect(page.getByTestId("auto-picked-room-hint")).toHaveCount(0);
  });

  test("a room the account really selected is announced to nobody", async ({ page }) => {
    await mockMapPage(page, { roomCatalog: [DOUBLE_ROOM, SUITE_ROOM] });
    await mockMapbox(page);

    await page.goto("/map");
    await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
    await page.getByRole("button", { name: "Φίλτρα" }).click();

    await expect(page.getByTestId("auto-picked-room-hint")).toHaveCount(0);
  });
});

test("a Mapbox failure stays a map failure and never blanks the results", async ({ page }) => {
  // No mockMapbox here on purpose: every Mapbox call fails, which is what the
  // style-load error handler reacts to. It used to route through setError,
  // flipping the RESULTS status to "error" and wiping the sidebar for a purely
  // presentational failure (chip task_8fecd58c).
  await mockMapPage(page);
  await page.route(/https:\/\/(api|events)\.mapbox\.com\/.*/, (route) => route.abort());

  await page.goto("/map");

  // The results are untouched: count, cards and market snapshot all stand.
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(cardCheckbox(page, "Hotel Alpha")).toBeVisible();
  // The one market summary box (Round 6) replaced the marker-price grid.
  await expect(page.getByTestId("market-summary")).toBeVisible();
  // Re-derived for the Greek sidebar (Round 5.1): the English substring can no
  // longer appear anywhere, so left alone this would pass over any regression.
  // «Αναμονή για την ολοκλήρωση» is the searching empty-state's own wording —
  // it is on screen exactly when the RESULTS status is "loading", which is
  // what a map failure routed back through setError would produce.
  await expect(page.locator(".results-sidebar")).not.toContainText("Αναμονή για την ολοκλήρωση");

  // The map area owns the failure, in Greek, and says what it does not affect.
  const notice = page.getByTestId("map-error-notice");
  await expect(notice).toContainText("Ο χάρτης δεν μπόρεσε να φορτώσει");
  await expect(notice).toContainText("Τα αποτελέσματα δεν επηρεάζονται");

  // ...and the results error slot stays empty: the restored-search banner is
  // still what the filters sidebar says.
  await page.getByRole("button", { name: "Φίλτρα" }).click();
  await expect(page.locator(".filters-sidebar .alert-error")).toHaveCount(0);
  await expect(page.locator(".filters-sidebar")).toContainText("Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών");
});
