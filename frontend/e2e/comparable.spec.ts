/**
 * «Μόνο συγκρίσιμα» and the automatic matching run (owner decisions
 * 2026-09-30 and 2026-11-13): the comparison set is chosen by the matching
 * agent alone, non-comparable rooms are hidden by default, ONE matching pass
 * per job judges every room type of the owner — run by itself once per job
 * per visit — and «Σύγκριση για» is a pure view filter.
 *
 * Hermetic: every endpoint is mocked with the wildcard FIRST (Playwright
 * matches LIFO, so each specific route below wins). Every read and every
 * agents POST is recorded, so the assertions pin what the page SENT, not only
 * what it drew. Fixture values are deliberately non-default (prices 83/61/47,
 * 13 comparable hotels): a passing assertion proves the value came from here.
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

/** A second room of the SAME category as ROOM_TYPE_ID: a separate comparison set all the same. */
const SECOND_ROOM_ID = "33333333-3333-3333-3333-3333333333cc";
const ROOM_TYPES = [
  { id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID, room_type: "Δίκλινο με Θέα Θάλασσα",
    room_type_category: "double", is_active: true },
  { id: SECOND_ROOM_ID, owned_property_id: OWNED_PROPERTY_ID, room_type: "Οικονομικό Δίκλινο",
    room_type_category: "double", is_active: true },
];

/** Keira is judged comparable, Lotos and Mirto are not (only returned with comparable_only=false). */
const ALL_MARKERS = [
  { hotel_name: "Hotel Keira", property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1", room_package_id: null,
    room_type: "Double Sea View", room_type_category: "double", property_type: "hotel",
    latitude: 36.34, longitude: 28.2, price_per_night_eur: 83, review_score: 8.8, review_count: 211,
    rooms_left: 3, distance_km: 1.1, category_match: "same", booking_url: null, comparable: true },
  { hotel_name: "Hotel Lotos", property_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2", room_package_id: null,
    room_type: "Family Suite", room_type_category: "family", property_type: "hotel",
    latitude: 36.35, longitude: 28.21, price_per_night_eur: 61, review_score: 7.6, review_count: 57,
    rooms_left: 5, distance_km: 2.4, category_match: "similar", booking_url: null, comparable: false },
  { hotel_name: "Hotel Mirto", property_id: "cccccccc-cccc-cccc-cccc-ccccccccccc3", room_package_id: null,
    room_type: "Single Economy", room_type_category: "single", property_type: "hotel",
    latitude: 36.36, longitude: 28.22, price_per_night_eur: 47, review_score: 7.1, review_count: 19,
    rooms_left: 1, distance_km: 3.2, category_match: "similar", booking_url: null, comparable: false },
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

/** One /competitors/ row for Keira, scored by `source`. */
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

type ComparableMapOptions = {
  /** Which rooms already carry agent verdicts for the job; `/competitors/` answers agent rows for those. */
  judgedRooms?: Set<string>;
  /** The answer of a run that has rooms to judge: a run body, or an HTTP error status (429 = quota). */
  agentRun?: Record<string, unknown> | number;
  /** When set, the agents POST waits for this promise before answering. */
  agentGate?: { current: Promise<void> | null };
  /** Every read of the market endpoints, in order: path + query. */
  reads?: Array<{ path: string; query: URLSearchParams }>;
  /** Every agents POST (the body lives here). */
  calls?: RecordedCall[];
};

const ALL_ROOMS = ROOM_TYPES.map((room) => room.id);

async function mockComparableMap(page: Page, options: ComparableMapOptions = {}): Promise<void> {
  const {
    judgedRooms = new Set<string>(ALL_ROOMS),
    agentRun = { status: "completed", matches_written: 6, room_types_matched: 2, source: "agent" },
    agentGate = { current: null },
    reads = [],
    calls = [],
  } = options;
  const record = (url: URL) => reads.push({ path: url.pathname, query: url.searchParams });

  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO: the wildcard FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: ROOM_TYPES }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) =>
    route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby: [] } }));
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => route.fulfill({ json: null }));
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    const url = new URL(route.request().url());
    record(url);
    const comparableOnly = url.searchParams.get("comparable_only") !== "false";
    return route.fulfill({ json: comparableOnly ? ALL_MARKERS.filter((row) => row.comparable) : ALL_MARKERS });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => {
    record(new URL(route.request().url()));
    return route.fulfill({ json: MARKET_SUMMARY });
  });
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/competitors/**`, (route) => {
    const url = new URL(route.request().url());
    record(url);
    const room = url.searchParams.get("owned_room_type_id") ?? "";
    return route.fulfill({ json: competitorRows(judgedRooms.has(room) ? "agent" : "statistical") });
  });
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) =>
    new URL(route.request().url()).pathname.endsWith("/scrape-jobs/")
      ? route.fulfill({ json: [COMPLETED_JOB] })
      : route.fulfill({ json: COMPLETED_JOB }));
  await page.route(`${API}/api/v1/agents/room-matches`, async (route) => {
    recordCall(calls, route);
    await agentGate.current;
    const body = route.request().postDataJSON() as { only_missing: boolean };
    // The server's only_missing: every room type judged already is an instant skip.
    if (body.only_missing && ALL_ROOMS.every((room) => judgedRooms.has(room))) {
      return route.fulfill({ json: {
        status: "skipped", matches_written: 0, room_types_matched: 2, skip_reason: "already_matched", source: "agent",
      } });
    }
    if (typeof agentRun === "number") {
      return route.fulfill({ status: agentRun, json: { detail: "Ημερήσιο όριο εκτιμήσεων agent" } });
    }
    // A completed pass leaves verdicts behind for EVERY room type of the job.
    if (agentRun["status"] === "completed") {
      ALL_ROOMS.forEach((room) => judgedRooms.add(room));
    }
    return route.fulfill({ json: agentRun });
  });
}

const readsOf = (reads: Array<{ path: string; query: URLSearchParams }>, suffix: string) =>
  reads.filter((read) => read.path.endsWith(suffix)).map((read) => read.query);
const roomMatchPosts = (calls: RecordedCall[]) =>
  calls.filter((call) => call.method === "POST" && call.path === "/api/v1/agents/room-matches");
// The primary control at the top of the results column, never inside the «Φίλτρα» column.
const roomSelect = (page: Page) => page.locator(".results-sidebar").getByLabel("Σύγκριση για");
const cardFor = (page: Page, hotel: string) => page.locator(".competitor-card", { hasText: hotel });

async function openMap(page: Page): Promise<void> {
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("1 ανταγωνιστής")).toBeVisible();
  // The room switch heads the results column, apart from the permanent «Φίλτρα» column.
  await expect(roomSelect(page)).toBeVisible();
  await expect(page.locator(".filters-sidebar select")).toHaveCount(0);
}

test("«Μόνο συγκρίσιμα» is on by default and off shows every room, the non-comparable ones chipped", async ({ page }) => {
  const reads: Array<{ path: string; query: URLSearchParams }> = [];
  const calls: RecordedCall[] = [];
  await mockComparableMap(page, { reads, calls });

  await openMap(page);
  const toggle = page.getByTestId("comparable-toggle");
  await expect(toggle).toBeChecked();
  await expect(cardFor(page, "Hotel Keira")).toHaveCount(1);
  await expect(cardFor(page, "Hotel Lotos")).toHaveCount(0);
  await expect.poll(() => readsOf(reads, "/market/summary").length).toBe(1);
  for (const endpoint of ["/maps/competitors", "/market/summary", "/competitors/"]) {
    const last = readsOf(reads, endpoint).at(-1)!;
    expect(last.get("comparable_only"), endpoint).toBe("true");
    expect(last.get("owned_room_type_id"), endpoint).toBe(ROOM_TYPE_ID);
    expect(last.has("include_similar"), endpoint).toBe(false);
    expect(last.has("amenities"), endpoint).toBe(false);
  }
  // The restored job gets its one automatic call; every room type is judged
  // already and the screen shows the verdicts, so nothing is re-read.
  await expect.poll(() => roomMatchPosts(calls).length).toBe(1);
  expect(roomMatchPosts(calls)[0].body).toEqual({ scrape_job_id: JOB_ID, only_missing: true });
  await expect(page.getByTestId("match-source-badge")).toHaveText("Εκτίμηση AI");
  expect(readsOf(reads, "/competitors/")).toHaveLength(1);

  await toggle.uncheck();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(cardFor(page, "Hotel Lotos").getByTestId("not-comparable-chip")).toHaveText("Μη συγκρίσιμο");
  await expect(cardFor(page, "Hotel Mirto").getByTestId("not-comparable-chip")).toHaveText("Μη συγκρίσιμο");
  await expect(cardFor(page, "Hotel Keira").getByTestId("not-comparable-chip")).toHaveCount(0);
  await expect.poll(() => readsOf(reads, "/market/summary").length).toBe(2);
  for (const endpoint of ["/maps/competitors", "/market/summary"]) {
    const last = readsOf(reads, endpoint).at(-1)!;
    expect(last.get("comparable_only"), endpoint).toBe("false");
    expect(last.get("owned_room_type_id"), endpoint).toBe(ROOM_TYPE_ID);
  }
  // A view change: no new matching run.
  expect(roomMatchPosts(calls)).toHaveLength(1);
});

test("switching room is a pure view filter: it re-reads with the room's id and runs nothing", async ({ page }) => {
  const reads: Array<{ path: string; query: URLSearchParams }> = [];
  const calls: RecordedCall[] = [];
  await mockComparableMap(page, { reads, calls });

  await openMap(page);
  await expect.poll(() => roomMatchPosts(calls).length).toBe(1);
  await expect.poll(() => readsOf(reads, "/competitors/").length).toBe(1);
  const markerReadsBefore = readsOf(reads, "/maps/competitors").length;

  await roomSelect(page).selectOption(SECOND_ROOM_ID);
  // Every read after the switch is for the new room: markers, summary and scores.
  await expect.poll(() => readsOf(reads, "/maps/competitors").length).toBe(markerReadsBefore + 1);
  for (const endpoint of ["/maps/competitors", "/market/summary", "/competitors/"]) {
    await expect.poll(() => readsOf(reads, endpoint).at(-1)?.get("owned_room_type_id"), endpoint).toBe(SECOND_ROOM_ID);
  }
  // The job's one pass judged this room type too: its scores are the agent's at once.
  await expect(page.getByTestId("match-source-badge")).toHaveText("Εκτίμηση AI");
  await expect(cardFor(page, "Hotel Keira")).toContainText("88%");
  await expect(page.getByTestId("auto-match-pending")).toHaveCount(0);

  await roomSelect(page).selectOption(ROOM_TYPE_ID);
  await expect.poll(() => readsOf(reads, "/maps/competitors").at(-1)?.get("owned_room_type_id")).toBe(ROOM_TYPE_ID);
  await expect(page.locator(".header-actions").getByText("1 ανταγωνιστής")).toBeVisible();
  // No search and no matching run for a room switch.
  expect(roomMatchPosts(calls)).toHaveLength(1);
});

test("a job without verdicts gets one pass for every room type, pending on screen, then a re-read", async ({ page }) => {
  const reads: Array<{ path: string; query: URLSearchParams }> = [];
  const calls: RecordedCall[] = [];
  let releaseRun = () => {};
  const agentGate: { current: Promise<void> | null } = {
    current: new Promise<void>((resolve) => { releaseRun = resolve; }),
  };
  await mockComparableMap(page, { reads, calls, agentGate, judgedRooms: new Set<string>() });

  await openMap(page);
  await expect(page.getByTestId("auto-match-pending"))
    .toHaveText("Ταίριασμα AI για όλους τους τύπους δωματίων σας σε εξέλιξη…");
  await expect.poll(() => roomMatchPosts(calls).length).toBe(1);
  expect(roomMatchPosts(calls)[0].body).toEqual({ scrape_job_id: JOB_ID, only_missing: true });
  // The statistical set stands until the verdicts are in.
  await expect(page.getByTestId("match-source-badge")).toHaveText("Στατιστική εκτίμηση");
  await expect(cardFor(page, "Hotel Keira")).toContainText("64%");
  const markerReadsBefore = readsOf(reads, "/maps/competitors").length;

  releaseRun();
  await expect(page.getByTestId("auto-match-pending")).toHaveCount(0);
  // The completed pass re-reads the job for the room on screen.
  await expect.poll(() => readsOf(reads, "/maps/competitors").length).toBe(markerReadsBefore + 1);
  await expect(page.getByTestId("match-source-badge")).toHaveText("Εκτίμηση AI");
  await expect(cardFor(page, "Hotel Keira")).toContainText("88%");
  await expect(page.getByTestId("auto-match-notice")).toHaveCount(0);

  // The other room type was judged by the same pass: a switch shows its verdicts, posting nothing.
  await roomSelect(page).selectOption(SECOND_ROOM_ID);
  await expect.poll(() => readsOf(reads, "/competitors/").at(-1)?.get("owned_room_type_id")).toBe(SECOND_ROOM_ID);
  await expect(page.getByTestId("match-source-badge")).toHaveText("Εκτίμηση AI");
  expect(roomMatchPosts(calls)).toHaveLength(1);
});

test("a failed automatic run shows the fallback notice and is never posted again", async ({ page }) => {
  const reads: Array<{ path: string; query: URLSearchParams }> = [];
  const calls: RecordedCall[] = [];
  // No room judged yet: the first load itself runs the pass — and the run fails.
  await mockComparableMap(page, {
    reads, calls, judgedRooms: new Set<string>(),
    agentRun: { status: "error", matches_written: 0, source: "agent" },
  });

  await openMap(page);
  await expect(page.getByTestId("auto-match-notice"))
    .toHaveText("Η εκτίμηση AI δεν είναι διαθέσιμη· εμφανίζεται η στατιστική.");
  await expect(page.getByTestId("auto-match-notice")).toHaveClass(/alert-error/);
  expect(roomMatchPosts(calls)).toHaveLength(1);
  expect(roomMatchPosts(calls)[0].body).toEqual({ scrape_job_id: JOB_ID, only_missing: true });
  // The statistical screen stays: the comparable room is still listed.
  await expect(cardFor(page, "Hotel Keira")).toHaveCount(1);

  // Re-reads of the same job (the switch off and on, another room) do not retry the run.
  const toggle = page.getByTestId("comparable-toggle");
  await toggle.uncheck();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await toggle.check();
  await expect(page.locator(".header-actions").getByText("1 ανταγωνιστής")).toBeVisible();
  await roomSelect(page).selectOption(SECOND_ROOM_ID);
  await expect.poll(() => readsOf(reads, "/market/summary").length).toBe(4);
  expect(roomMatchPosts(calls)).toHaveLength(1);
});

test("a run still in progress past the server's waits is asked again, then a quiet notice", async ({ page }) => {
  const calls: RecordedCall[] = [];
  // The search's own run still holds the job past the server's wait, every time.
  await mockComparableMap(page, {
    calls, judgedRooms: new Set<string>(),
    agentRun: { status: "skipped", matches_written: 0, skip_reason: "in_progress", source: "agent" },
  });

  await openMap(page);
  const notice = page.getByTestId("auto-match-notice");
  await expect(notice).toHaveText("Το ταίριασμα AI είναι ακόμη σε εξέλιξη· ανανεώστε τη σελίδα σε λίγο για να το δείτε.");
  await expect(notice).not.toHaveClass(/alert-error/);
  // The first answer plus two more asks: each waits up to 150 s on the server.
  expect(roomMatchPosts(calls)).toHaveLength(3);
  await expect(cardFor(page, "Hotel Keira")).toHaveCount(1);
});

test("the quota (429) is the same notice, without a retry", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await mockComparableMap(page, { calls, judgedRooms: new Set<string>(), agentRun: 429 });

  await openMap(page);
  await expect(page.getByTestId("auto-match-notice"))
    .toHaveText("Η εκτίμηση AI δεν είναι διαθέσιμη· εμφανίζεται η στατιστική.");
  await roomSelect(page).selectOption(SECOND_ROOM_ID);
  await roomSelect(page).selectOption(ROOM_TYPE_ID);
  await expect(page.locator(".header-actions").getByText("1 ανταγωνιστής")).toBeVisible();
  // One run per job, failing once: nothing is retried by itself.
  expect(roomMatchPosts(calls)).toHaveLength(1);
});

// ---------------------------------------------------------------- pricing

type PricingCalls = { recommendationBodies: Array<Record<string, unknown>>; historyQueries: URLSearchParams[] };

async function mockPricing(page: Page, statsScope: Record<string, unknown>): Promise<PricingCalls> {
  const calls: PricingCalls = { recommendationBodies: [], historyQueries: [] };
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  // LIFO: the catch-all FIRST.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: ROOM_TYPES }));
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => route.fulfill({ status: 404, json: { detail: "x" } }));
  await page.route(`${API}/api/v1/market/price-history**`, (route) => {
    calls.historyQueries.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: { canonical_destination: "faliraki", check_in: "2030-07-15",
      check_out: "2030-07-18", points: [] } });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: { ...MARKET_SUMMARY, total_hotels: 13 } }));
  await page.route(`${API}/api/v1/agents/price-recommendation`, (route) => {
    calls.recommendationBodies.push(route.request().postDataJSON());
    return route.fulfill({ json: {
      statistics: {
        sample_runs: 4, sample_days: 3, own_reference_price_eur: 97, market_median_eur: 91,
        market_p25_eur: 78, market_p75_eur: 104, own_position_percentile: 58,
        position: { cheaper_than_you: 6, total: 13 }, stats_scope: statsScope,
        trend_7d_pct: null, trend_30d_pct: null, lead_time_days: 18,
        statistical_recommendation_eur: 93, notes: [],
      },
      recommendation: { recommended_price_eur: 94, price_range_low_eur: 86, price_range_high_eur: 101,
        confidence: "medium", reasoning: "Στατιστική σύσταση.", key_factors: [], source: "statistical" },
      recommendation_available: true, own_price_source: "booking_live",
    } });
  });
  return calls;
}

test("pricing lists both rooms of one category and sends the chosen one's id everywhere", async ({ page }) => {
  const calls = await mockPricing(page, { same_category: 0, similar: 0, used: "agent", comparable: 13 });

  await page.goto("/pricing");
  // The room select is the page's primary choice, above the stay form.
  const select = page.getByLabel("Σύγκριση για");
  // Two doubles, two options: keyed by room id, not merged by category.
  await expect(select.locator("option", { hasText: "Δίκλινο με Θέα Θάλασσα (double)" })).toHaveCount(1);
  await expect(select.locator("option", { hasText: "Οικονομικό Δίκλινο (double)" })).toHaveCount(1);

  await select.selectOption(SECOND_ROOM_ID);
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
  await expect.poll(() => calls.recommendationBodies.length).toBe(1);
  expect(calls.recommendationBodies[0]).toMatchObject({
    owned_property_id: OWNED_PROPERTY_ID,
    owned_room_type_id: SECOND_ROOM_ID,
    room_type_category: "double",
  });
  // The history the recommendation refreshes is for the same room.
  await expect.poll(() => calls.historyQueries.at(-1)?.get("owned_room_type_id")).toBe(SECOND_ROOM_ID);
  // The agent's comparable set backed the statistics: the basis says so.
  await expect(page.getByText("Βάση: 13 συγκρίσιμα καταλύματα (εκτίμηση AI)", { exact: true })).toBeVisible();
});

test("the agent basis line takes the singular for one comparable hotel", async ({ page }) => {
  await mockPricing(page, { same_category: 0, similar: 0, used: "agent", comparable: 1 });

  await page.goto("/pricing");
  await page.getByLabel("Σύγκριση για").selectOption(ROOM_TYPE_ID);
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
  await expect(page.getByText("Βάση: 1 συγκρίσιμο κατάλυμα (εκτίμηση AI)", { exact: true })).toBeVisible();
});
