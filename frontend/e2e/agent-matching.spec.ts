/**
 * Agents, Μέρος Α.4 (spec 2026-09-29, reworked 2026-11-13): the «Ταίριασμα
 * δωματίου» card keeps only the source badge and «Ξανά ταίριασμα», which
 * re-runs the matching for EVERY room type of the job; the scores show on the
 * result cards. Hermetic: every endpoint is mocked with the wildcard FIRST
 * (Playwright matches LIFO, so each specific route below wins). Fixture
 * values are deliberately non-default (scores 62/48 vs 91/57, prices 73/58,
 * review 8.7): a passing assertion proves the value came from here.
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

const OURANIA_REASONING = "Ίδια κατηγορία με θέα θάλασσα και σχεδόν ίδια χωρητικότητα.";
const PELAGOS_REASONING = "Μικρότερο στούντιο χωρίς πρωινό· μερική αντιστοιχία παροχών.";

const MAP_MARKERS = [
  { hotel_name: "Hotel Ourania", property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", room_package_id: null,
    room_type: "Double Sea View", room_type_category: "double", property_type: "hotel",
    latitude: 36.34, longitude: 28.2, price_per_night_eur: 73, review_score: 8.7, review_count: 123,
    rooms_left: 2, distance_km: 0.9, category_match: "same", booking_url: null },
  { hotel_name: "Hotel Pelagos", property_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", room_package_id: null,
    room_type: "Studio Garden", room_type_category: "studio", property_type: "hotel",
    latitude: 36.35, longitude: 28.21, price_per_night_eur: 58, review_score: 7.9, review_count: 41,
    rooms_left: 4, distance_km: 2.3, category_match: "similar", booking_url: null },
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
  adults: 2,
  children: 0,
  rooms: 1,
  filters_payload: { limit: 9 },
  nearby_destinations: [] as string[],
  radius_km: 6,
  status: "completed",
  requested_at: "2030-05-01T10:00:00Z",
  started_at: "2030-05-01T10:00:05Z",
  finished_at: "2030-05-01T10:06:00Z",
  attempt_count: 1,
  max_attempts: 3,
  scrape_runs_count: 1,
  result_summary: { version: 1, rows_seen: 9, filter_counts: {}, rows_written: 2, warnings: [] as string[] },
};

const MARKET_SUMMARY = {
  destination: "faliraki", check_in: "2030-06-01", check_out: "2030-06-05",
  total_records: 9, total_hotels: 2, same_category_hotels: 1, similar_hotels: 1,
  price_min_eur: 58, price_max_eur: 121, price_avg_eur: 84, price_median_eur: 79,
  avg_review_score: 8.3, rooms_left_total: 6,
};

/** The two matched hotels of every /competitors/ read; statistical rows carry match_reasoning null. */
const matchedRows = (source: "agent" | "statistical") => [
  {
    hotel_name: "Hotel Ourania", city: "Faliraki", address: "", property_type: "hotel",
    latitude: 36.34, longitude: 28.2, stars: 4, review_score: 8.7, review_count: 123,
    price_min_eur: 73, price_max_eur: 121, rooms_left: 2, distance_km: 0.9,
    category_match: "same", booking_url: null, match_source: source,
    best_match_score: source === "agent" ? 91 : 62,
    packages: [{
      room_type: "Double Sea View", price_per_night_eur: 73, price_total_eur: 292,
      meals: "Πρωινό", free_cancellation: "", rooms_left: 2,
      match_score: source === "agent" ? 91 : 62,
      room_type_category: "double", category_match: "same",
      rate_plan: { discounted_price_per_night_eur: 69, discount_pct: 5, discount_label: "-5%",
        has_genius_discount: false, cancellation_type: "free_cancellation", payment_label: null },
      match_reasoning: source === "agent" ? OURANIA_REASONING : null,
    }],
  },
  {
    hotel_name: "Hotel Pelagos", city: "Faliraki", address: "", property_type: "hotel",
    latitude: 36.35, longitude: 28.21, stars: 2, review_score: 7.9, review_count: 41,
    price_min_eur: 58, price_max_eur: 58, rooms_left: 4, distance_km: 2.3,
    category_match: "similar", booking_url: null, match_source: source,
    best_match_score: source === "agent" ? 57 : 48,
    packages: [{
      room_type: "Studio Garden", price_per_night_eur: 58, price_total_eur: 232,
      meals: "", free_cancellation: "", rooms_left: 4,
      match_score: source === "agent" ? 57 : 48,
      room_type_category: "studio", category_match: "similar",
      rate_plan: null,
      match_reasoning: source === "agent" ? PELAGOS_REASONING : null,
    }],
  },
];

const COMPLETED_RUN = { status: "completed", matches_written: 4, room_types_matched: 2, source: "agent" };
// The page's automatic call (only_missing) for a restored job whose room
// types were all judged already.
const ALREADY_MATCHED = {
  status: "skipped", matches_written: 0, room_types_matched: 2, skip_reason: "already_matched", source: "agent",
};

type AgentMatchingOptions = {
  /** false = empty job list, nothing restored: no completed job on screen. */
  restorable?: boolean;
  /** The answer to every /competitors/ read; swap `current` mid-test to change the NEXT read. */
  matchRows?: { current: unknown[] };
  /** The «Ξανά ταίριασμα» answer: a run body, or an HTTP error status (429 = quota). */
  agentRun?: { current: Record<string, unknown> | number };
  /** When set, the button's POST waits for this promise before answering (pending-state assertions). */
  agentGate?: { current: Promise<void> | null };
  /** Every scrape-jobs and agents call (the POST payload lives here). */
  calls?: RecordedCall[];
  /** Every /competitors/ query string, oldest first. */
  matchReads?: URLSearchParams[];
};

async function mockAgentMatching(page: Page, options: AgentMatchingOptions = {}): Promise<void> {
  const {
    restorable = true,
    matchRows = { current: matchedRows("statistical") },
    agentRun = { current: COMPLETED_RUN },
    agentGate = { current: null },
    calls = [],
    matchReads = [],
  } = options;

  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO: the wildcard FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [{ id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID,
      room_type: "Double Room with Sea View", room_type_category: "double", is_active: true }] }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) =>
    route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby: [] } }));
  // No «Εσείς» marker: the owner's placement plays no part in these scenarios.
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => route.fulfill({ json: null }));
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => route.fulfill({ json: MAP_MARKERS }));
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: MARKET_SUMMARY }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/competitors/**`, (route) => {
    matchReads.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: matchRows.current });
  });
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    recordCall(calls, route);
    if (new URL(route.request().url()).pathname.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: restorable ? [COMPLETED_JOB] : [] });
    }
    return restorable ? route.fulfill({ json: COMPLETED_JOB }) : route.fulfill({ status: 404, json: { detail: "not found" } });
  });
  // The automatic call (only_missing) is told every room type is judged
  // already — that run is comparable.spec.ts's subject; the button's POST
  // (only_missing false) answers with `agentRun`.
  await page.route(`${API}/api/v1/agents/room-matches`, async (route) => {
    recordCall(calls, route);
    if (route.request().postDataJSON()?.only_missing) {
      return route.fulfill({ json: ALREADY_MATCHED });
    }
    await agentGate.current;
    const answer = agentRun.current;
    return typeof answer === "number"
      ? route.fulfill({ status: answer, json: { detail: "Ημερήσιο όριο εκτιμήσεων agent" } })
      : route.fulfill({ json: answer });
  });
}

const badge = (page: Page) => page.getByTestId("match-source-badge");
const reassess = (page: Page) => page.getByTestId("reassess-button");
const cardFor = (page: Page, hotel: string) => page.locator(".competitor-card", { hasText: hotel });
const buttonPosts = (calls: RecordedCall[]) =>
  calls.filter((call) => call.method === "POST" && call.path === "/api/v1/agents/room-matches"
    && !(call.body as { only_missing?: boolean } | undefined)?.only_missing);

/** Open the map on the restored job and wait for its automatic call to settle. */
async function openMap(page: Page, calls: RecordedCall[], matchReads: URLSearchParams[], reads: number): Promise<void> {
  // Tall enough that the match card at the foot of the «Φίλτρα» column is clickable.
  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();
  await expect.poll(() => calls.filter((call) => call.path === "/api/v1/agents/room-matches").length).toBe(1);
  // A statistical screen re-reads once after "already_matched" (its read may
  // predate the verdicts); an agent screen does not.
  await expect.poll(() => matchReads.length).toBe(reads);
}

test("the card keeps only the badge and the re-run: no switch, score slider or sort", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  await mockAgentMatching(page, { matchRows: { current: matchedRows("agent") }, calls, matchReads });

  await openMap(page, calls, matchReads, 1);
  const card = page.locator(".filters-sidebar .match-controls");
  await expect(card).toContainText("Ταίριασμα δωματίου");
  await expect(card).toContainText("για όλους τους τύπους δωματίων σας");
  await expect(card.locator("input")).toHaveCount(0);
  await expect(card.locator("select, app-select-menu")).toHaveCount(0);
  await expect(page.locator(".matched-card")).toHaveCount(0);
  await expect(reassess(page)).toHaveText("Ξανά ταίριασμα");
  // The scores read has no floor and the best match first.
  expect(matchReads[0].get("min_match_score")).toBe("0");
  expect(matchReads[0].get("sort")).toBe("match");
  expect(matchReads[0].get("match_room")).toBe("true");
});

test("statistical rows earn the statistical badge and their scores on the result cards", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  await mockAgentMatching(page, { calls, matchReads });

  await openMap(page, calls, matchReads, 2);
  await expect(badge(page)).toHaveText("Στατιστική εκτίμηση");
  await expect(cardFor(page, "Hotel Ourania")).toContainText("62%");
  await expect(cardFor(page, "Hotel Pelagos")).toContainText("48%");
});

test("agent rows earn the AI badge and their scores on the result cards", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  await mockAgentMatching(page, { matchRows: { current: matchedRows("agent") }, calls, matchReads });

  await openMap(page, calls, matchReads, 1);
  await expect(badge(page)).toHaveText("Εκτίμηση AI");
  await expect(cardFor(page, "Hotel Ourania")).toContainText("91%");
  await expect(cardFor(page, "Hotel Pelagos")).toContainText("57%");
});

test("a read that mixes the two sources earns the partial AI badge", async ({ page }) => {
  // Ourania scored by the agent, Pelagos statistically — one legitimate read, two sources.
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  const mixedRows = [matchedRows("agent")[0], matchedRows("statistical")[1]];
  await mockAgentMatching(page, { matchRows: { current: mixedRows }, calls, matchReads });

  await openMap(page, calls, matchReads, 1);
  await expect(badge(page)).toHaveText("Εκτίμηση AI (μερική)");
  await expect(cardFor(page, "Hotel Ourania")).toContainText("91%");
  await expect(cardFor(page, "Hotel Pelagos")).toContainText("48%");
});

test("the button re-runs the whole job, shows its pending state and the re-read flips scores and badge", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  const matchRows = { current: matchedRows("statistical") as unknown[] };
  let releaseRun = () => {};
  const agentGate: { current: Promise<void> | null } = {
    current: new Promise<void>((resolve) => { releaseRun = resolve; }),
  };
  await mockAgentMatching(page, { matchRows, agentGate, calls, matchReads });

  await openMap(page, calls, matchReads, 2);
  await expect(badge(page)).toHaveText("Στατιστική εκτίμηση");
  await expect(cardFor(page, "Hotel Ourania")).toContainText("62%");

  const button = reassess(page);
  await expect(button).toBeEnabled();
  await button.click();
  // The POST is held by the gate: the pending state is what is on screen.
  await expect(button).toHaveText("Ταίριασμα σε εξέλιξη…");
  await expect(button).toBeDisabled();

  // The completed run replaced the rows on the server: the re-read serves agent rows.
  matchRows.current = matchedRows("agent");
  releaseRun();

  await expect(badge(page)).toHaveText("Εκτίμηση AI");
  await expect(cardFor(page, "Hotel Ourania")).toContainText("91%");
  await expect(button).toHaveText("Ξανά ταίριασμα");
  await expect(button).toBeEnabled();
  await expect(page.getByTestId("reassess-notice")).toHaveCount(0);

  // The whole job rode in the POST body: every room type is replaced, not only the one on screen.
  expect(buttonPosts(calls).map((call) => call.body)).toEqual([{ scrape_job_id: JOB_ID, only_missing: false }]);
  // The flip came from a fresh read of the same job.
  expect(matchReads.length).toBe(3);
  const lastRead = matchReads.at(-1)!;
  expect(lastRead.get("scrape_job_id")).toBe(JOB_ID);
  expect(lastRead.get("owned_room_type_id")).toBe(ROOM_TYPE_ID);
});

test("a failed run raises the fallback notice and the scores on screen stand", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  // status "error" writes nothing (spec Α.5); 429 is the spent daily quota.
  const agentRun: { current: Record<string, unknown> | number } =
    { current: { status: "error", matches_written: 0, source: "agent" } };
  await mockAgentMatching(page, { agentRun, calls, matchReads });

  await openMap(page, calls, matchReads, 2);
  await expect(cardFor(page, "Hotel Ourania")).toContainText("62%");

  await reassess(page).click();
  await expect(page.getByTestId("reassess-notice"))
    .toHaveText("Η εκτίμηση AI δεν είναι διαθέσιμη· εμφανίζεται η στατιστική.");
  // The failure changed nothing on screen: badge, cards and button all stand.
  await expect(badge(page)).toHaveText("Στατιστική εκτίμηση");
  await expect(cardFor(page, "Hotel Ourania")).toContainText("62%");
  await expect(reassess(page)).toHaveText("Ξανά ταίριασμα");
  await expect(reassess(page)).toBeEnabled();
  // No pointless re-read either: a failed run cannot have changed the rows.
  expect(matchReads.length).toBe(2);

  // The quota path (429) raises the same sentence.
  agentRun.current = 429;
  await reassess(page).click();
  await expect(page.getByTestId("reassess-notice"))
    .toHaveText("Η εκτίμηση AI δεν είναι διαθέσιμη· εμφανίζεται η στατιστική.");
  expect(buttonPosts(calls)).toHaveLength(2);
});

test("a re-run still in progress elsewhere says so quietly and re-reads nothing", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const matchReads: URLSearchParams[] = [];
  const agentRun: { current: Record<string, unknown> | number } = {
    current: { status: "skipped", matches_written: 0, skip_reason: "in_progress", source: "agent" },
  };
  await mockAgentMatching(page, { agentRun, calls, matchReads });

  await openMap(page, calls, matchReads, 2);

  await reassess(page).click();
  const notice = page.getByTestId("reassess-notice");
  await expect(notice).toHaveText("Το ταίριασμα AI είναι ακόμη σε εξέλιξη· ανανεώστε τη σελίδα σε λίγο για να το δείτε.");
  await expect(notice).not.toHaveClass(/alert-error/);
  await expect(reassess(page)).toBeEnabled();
  expect(matchReads.length).toBe(2);
});

test("without a completed job on screen the button is disabled", async ({ page }) => {
  await mockAgentMatching(page, { restorable: false });

  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.goto("/map");
  // Nothing restored: no job on screen, only the empty state.
  await expect(page.getByText("Δεν έχετε τρέξει ακόμη αναζήτηση")).toBeVisible();
  // «Ταίριασμα δωματίου» sits under the filters in the «Φίλτρα» column.
  await expect(page.locator(".filters-sidebar .match-controls")).toBeVisible();
  await expect(badge(page)).toHaveText("Στατιστική εκτίμηση");
  await expect(reassess(page)).toBeDisabled();
});
