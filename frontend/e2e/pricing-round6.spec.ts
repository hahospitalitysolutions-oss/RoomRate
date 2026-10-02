/**
 * Round 6 §5.1: the pricing page opens on the latest competitor search and
 * reads its statistics in the owner's terms.
 *
 * Prefill — `?job=<id>` (the map's link to this page) wins over the last
 * search remembered in workflow storage. Only a completed competitor search
 * whose check-in is still ahead is replayed; anything else keeps today's
 * defaults without a note (a past check-in is refused by the backend, and a
 * failed or running search has no market behind it). The note goes away as
 * soon as the owner edits a prefilled field.
 *
 * Texts — «Φθηνότερα από εσάς: k από n καταλύματα» replaces the P-percentile,
 * a missing 7- or 30-day trend says what it waits for, the reference price
 * names its source, a basis line names the hotels behind the statistics, and
 * every note is read once.
 *
 * Every backend surface the page touches is mocked with `**` globs behind a
 * loud 404 catch-all, so a live API on :8000 can never leak into these tests.
 */
import { expect, Locator, Page, test } from "@playwright/test";

import { CURRENT_USER, OWNED_PROPERTY_ID, ROOM_TYPE_ID, seedBrowserState } from "./helpers";

// finished_at is an absolute instant the note renders as a local date.
test.use({ timezoneId: "Europe/Athens" });

const LINKED_JOB_ID = "44444444-4444-4444-4444-4444444444aa";
const STORED_JOB_ID = "44444444-4444-4444-4444-4444444444bb";

const ROOM_TYPES = [
  {
    id: ROOM_TYPE_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Δίκλινο Δωμάτιο",
    room_type_category: "double",
    is_active: true,
  },
  {
    id: "33333333-3333-3333-3333-3333333333bb",
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Δίκλινο με Δύο Μονά Κρεβάτια",
    room_type_category: "twin",
    is_active: true,
  },
];

const PRICE_HISTORY = {
  canonical_destination: "faliraki",
  check_in: "2030-07-15",
  check_out: "2030-07-18",
  points: [
    { run_index: 1, observed_at: "2026-09-14T10:04:00Z", hotel_name: "Hotel Alpha", min_price_eur: 96 },
    { run_index: 1, observed_at: "2026-09-14T10:04:00Z", hotel_name: "Hotel Beta", min_price_eur: 124 },
  ],
};

const MARKET_SUMMARY = {
  destination: "faliraki",
  check_in: "2030-07-15",
  check_out: "2030-07-18",
  total_records: 30,
  total_hotels: 11,
  price_min_eur: 80,
  price_max_eur: 190,
  price_avg_eur: 121,
  price_median_eur: 110,
  avg_review_score: 8.6,
  rooms_left_total: 40,
};

/** A valid Round 6 PriceStatistics; tests override only what they pin. */
function statistics(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sample_runs: 3,
    sample_days: 2,
    own_reference_price_eur: 118,
    market_median_eur: 110,
    market_p25_eur: 95,
    market_p75_eur: 130,
    own_position_percentile: 64,
    position: { cheaper_than_you: 7, total: 11 },
    stats_scope: { same_category: 11, similar: 0, used: "same" },
    trend_7d_pct: 2.5,
    trend_30d_pct: null,
    lead_time_days: 21,
    statistical_recommendation_eur: 112,
    notes: [],
    ...overrides,
  };
}

/** A valid statistical PriceRecommendation; tests override only what they pin. */
function recommendation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    recommended_price_eur: 114,
    price_range_low_eur: 104,
    price_range_high_eur: 124,
    confidence: "medium",
    reasoning:
      "Στατιστική σύσταση (χωρίς AI): η διάμεση τιμή της τρέχουσας αγοράς, προσαρμοσμένη στην τάση των 7 ημερών "
      + "και περιορισμένη στο εύρος P25–P75 των ανταγωνιστών.",
    key_factors: ["Διάμεσος αγοράς 110 €", "Φθηνότερα από εσάς: 7 από 11"],
    source: "statistical",
    ...overrides,
  };
}

/**
 * A POST /api/v1/agents/price-recommendation body in the shape the Round 6
 * backend really returns: position / stats_scope / sample_days inside
 * `statistics`, own_price_source at the top level, every key present (null
 * when unknown). `recommendation: null` = not enough data.
 */
function pricingResponse(
  parts: {
    statistics?: Record<string, unknown>;
    recommendation?: Record<string, unknown> | null;
    ownPriceSource?: string | null;
  } = {},
): Record<string, unknown> {
  const rec = parts.recommendation === undefined ? recommendation() : parts.recommendation;
  return {
    statistics: parts.statistics ?? statistics(),
    recommendation: rec,
    recommendation_available: rec !== null,
    audit_id: "55555555-5555-5555-5555-5555555555aa",
    generated_at: "2026-09-15T09:00:00Z",
    cached: false,
    model_version: "statistical-v1",
    prompt_version: "2026-09-15.v2",
    own_price_source: parts.ownPriceSource === undefined ? "booking_live" : parts.ownPriceSource,
  };
}

/** A completed competitor search as GET /api/v1/scrape-jobs/{id} returns it. */
function competitorJob(id: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id,
    account_id: CURRENT_USER.account_id,
    owned_property_id: OWNED_PROPERTY_ID,
    job_type: "competitor_search",
    room_type_category: "twin",
    destination: "Faliraki",
    raw_destination: "Faliraki, Rhodes",
    canonical_destination: "faliraki",
    check_in: "2030-07-15",
    check_out: "2030-07-18",
    adults: 3,
    children: 1,
    rooms: 2,
    filters_payload: { limit: 40 },
    nearby_destinations: ["Kallithea", "Afantou"],
    radius_km: 10,
    status: "completed",
    requested_at: "2026-09-14T09:50:00Z",
    started_at: "2026-09-14T09:50:05Z",
    finished_at: "2026-09-14T10:04:00Z",
    error_message: null,
    attempt_count: 1,
    max_attempts: 3,
    next_attempt_at: null,
    scrape_runs_count: 1,
    ...overrides,
  };
}

type PricingMocks = {
  /** Jobs by id; any other id answers 404 like the API. */
  jobs?: Record<string, Record<string, unknown>>;
  /** Seeds workflow storage's last competitor search for the e2e user. */
  storedJobId?: string;
  recommendation?: Record<string, unknown>;
  totalHotels?: number;
  /** Parks every scrape-job lookup (after recording it) until this settles. */
  jobGate?: Promise<void>;
};

type PricingCalls = {
  jobIds: string[];
  historyQueries: URLSearchParams[];
  recommendationBodies: Array<Record<string, unknown>>;
};

async function mockPricingPage(page: Page, mocks: PricingMocks = {}): Promise<PricingCalls> {
  const calls: PricingCalls = { jobIds: [], historyQueries: [], recommendationBodies: [] };
  await seedBrowserState(page);
  if (mocks.storedJobId) {
    // WorkflowStorageService keys are per auth subject; seedBrowserState only
    // writes the unsuffixed key, which the page never reads.
    await page.addInitScript((jobId) => {
      window.localStorage.setItem("roomrate_last_competitor_job_id:e2e-user", jobId);
    }, mocks.storedJobId);
  }
  // The bell opens /ws/alerts on load; accept and ignore it.
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));

  // LIFO: the catch-alls go FIRST so every specific mock registered after them
  // wins; an unmocked call fails loudly instead of reaching a live backend.
  await page.route(/https:\/\/[a-z0-9]+\.supabase\.co\/.*/, (route) => route.abort());
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({
      status: 404,
      json: { detail: `e2e mock missing for ${route.request().method()} ${route.request().url()}` },
    }),
  );
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route("**/api/v1/onboarding/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return path === `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`
      ? route.fulfill({ json: ROOM_TYPES })
      : route.fallback();
  });
  await page.route("**/api/v1/notifications**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/notifications/unread-count") {
      return route.fulfill({ json: { count: 0 } });
    }
    if (path === "/api/v1/notifications/ws-ticket") {
      return route.fulfill({ json: { ticket: "e2e-one-time-ticket", expires_in_seconds: 60 } });
    }
    return route.fulfill({ json: [] });
  });
  await page.route("**/api/v1/scrape-jobs/**", async (route) => {
    const jobId = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    calls.jobIds.push(jobId);
    await mocks.jobGate;
    const job = mocks.jobs?.[jobId];
    return job
      ? route.fulfill({ json: job })
      : route.fulfill({ status: 404, json: { detail: "Η εργασία δεν βρέθηκε." } });
  });
  await page.route("**/api/v1/market/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/market/price-history") {
      calls.historyQueries.push(url.searchParams);
      return route.fulfill({ json: PRICE_HISTORY });
    }
    if (url.pathname === "/api/v1/market/summary") {
      return route.fulfill({ json: { ...MARKET_SUMMARY, total_hotels: mocks.totalHotels ?? 11 } });
    }
    return route.fallback();
  });
  await page.route("**/api/v1/agents/**", (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname !== "/api/v1/agents/price-recommendation" || request.method() !== "POST") {
      return route.fallback();
    }
    calls.recommendationBodies.push(request.postDataJSON());
    return route.fulfill({ json: mocks.recommendation ?? pricingResponse() });
  });
  return calls;
}

function formFields(page: Page) {
  const form = page.locator(".form-panel");
  return {
    checkIn: form.getByLabel("Άφιξη"),
    checkOut: form.getByLabel("Αναχώρηση"),
    adults: form.getByLabel("Ενήλικες"),
    children: form.getByLabel("Παιδιά"),
    rooms: form.getByLabel("Δωμάτια"),
    // The room select is the page's primary choice, above the stay form.
    category: page.getByLabel("Σύγκριση για"),
  };
}

/** The page's default stay (today + 30 days, 4 nights), in the browser's own calendar. */
async function defaultStay(page: Page): Promise<{ checkIn: string; checkOut: string }> {
  return page.evaluate(() => {
    const format = (value: Date) =>
      `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
    const now = new Date();
    const checkIn = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 30);
    const checkOut = new Date(checkIn.getFullYear(), checkIn.getMonth(), checkIn.getDate() + 4);
    return { checkIn: format(checkIn), checkOut: format(checkOut) };
  });
}

test.describe("prefill from the latest competitor search", () => {
  test("?job= opens the form on that search's stay, occupancy and room category", async ({ page }) => {
    const calls = await mockPricingPage(page, {
      jobs: {
        [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID),
        [STORED_JOB_ID]: competitorJob(STORED_JOB_ID, { check_in: "2030-08-01", check_out: "2030-08-05" }),
      },
      storedJobId: STORED_JOB_ID,
    });

    await page.goto(`/pricing?job=${LINKED_JOB_ID}`);

    const fields = formFields(page);
    await expect(page.getByText("Σύμφωνα με την αναζήτηση της 14/09/2026")).toBeVisible();
    await expect(fields.checkIn).toHaveValue("2030-07-15");
    await expect(fields.checkOut).toHaveValue("2030-07-18");
    await expect(fields.adults).toHaveValue("3");
    await expect(fields.children).toHaveValue("1");
    await expect(fields.rooms).toHaveValue("2");
    await expect(fields.category).toHaveValue(ROOM_TYPES[1].id);
    // The link wins: the search remembered in storage is never looked up.
    expect(calls.jobIds).toEqual([LINKED_JOB_ID]);
    // The chart is read for the replayed stay, never first for today's defaults.
    await expect.poll(() => calls.historyQueries.map((query) => query.get("check_in"))).toEqual(["2030-07-15"]);

    // The prefilled values are what the recommendation is asked for — asked
    // automatically, since the page was opened from that search.
    await expect.poll(() => calls.recommendationBodies.length).toBe(1);
    expect(calls.recommendationBodies[0]).toMatchObject({
      owned_property_id: OWNED_PROPERTY_ID,
      room_type_category: "twin",
      check_in: "2030-07-15",
      check_out: "2030-07-18",
      adults: 3,
      children: 1,
      rooms: 2,
    });
  });

  test("without ?job= the last search remembered in storage prefills the form", async ({ page }) => {
    const calls = await mockPricingPage(page, {
      jobs: {
        [STORED_JOB_ID]: competitorJob(STORED_JOB_ID, {
          check_in: "2030-08-01",
          check_out: "2030-08-05",
          adults: 1,
          children: 2,
          rooms: 3,
          finished_at: "2026-09-10T15:30:00Z",
        }),
      },
      storedJobId: STORED_JOB_ID,
    });

    await page.goto("/pricing");

    const fields = formFields(page);
    await expect(page.getByText("Σύμφωνα με την αναζήτηση της 10/09/2026")).toBeVisible();
    await expect(fields.checkIn).toHaveValue("2030-08-01");
    await expect(fields.checkOut).toHaveValue("2030-08-05");
    await expect(fields.adults).toHaveValue("1");
    await expect(fields.children).toHaveValue("2");
    await expect(fields.rooms).toHaveValue("3");
    await expect(fields.category).toHaveValue(ROOM_TYPES[1].id);
    expect(calls.jobIds).toEqual([STORED_JOB_ID]);
  });

  test("a search in a category the property does not offer replays its dates, and the note says only that", async ({ page }) => {
    await mockPricingPage(page, {
      jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID, { room_type_category: "family" }) },
    });

    await page.goto(`/pricing?job=${LINKED_JOB_ID}`);

    const fields = formFields(page);
    await expect(
      page.getByText("Σύμφωνα με τις ημερομηνίες της αναζήτησης της 14/09/2026", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Σύμφωνα με την αναζήτηση της 14/09/2026")).toHaveCount(0);
    await expect(fields.checkIn).toHaveValue("2030-07-15");
    await expect(fields.checkOut).toHaveValue("2030-07-18");
    // The owner's own category stays; the search's «family» is not on offer here.
    await expect(fields.category).toHaveValue(ROOM_TYPE_ID);
  });

  test("editing any prefilled field removes the note, and replaying the search shows it again", async ({ page }) => {
    await mockPricingPage(page, { jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID) } });
    const note = page.getByText("Σύμφωνα με την αναζήτηση της 14/09/2026");
    const edits: Array<{ field: keyof ReturnType<typeof formFields>; prefilled: string; edit: (target: Locator) => Promise<unknown> }> = [
      { field: "checkIn", prefilled: "2030-07-15", edit: (target) => target.fill("2030-07-16") },
      { field: "checkOut", prefilled: "2030-07-18", edit: (target) => target.fill("2030-07-20") },
      { field: "adults", prefilled: "3", edit: (target) => target.fill("4") },
      { field: "children", prefilled: "1", edit: (target) => target.fill("0") },
      { field: "rooms", prefilled: "2", edit: (target) => target.fill("1") },
      { field: "category", prefilled: ROOM_TYPES[1].id, edit: (target) => target.selectOption(ROOM_TYPE_ID) },
    ];

    for (const { field, prefilled, edit } of edits) {
      await test.step(field, async () => {
        // Every visit replays the linked search, so the note is back before each edit.
        await page.goto(`/pricing?job=${LINKED_JOB_ID}`);
        const target = formFields(page)[field];
        await expect(note).toBeVisible();
        await expect(target).toHaveValue(prefilled);

        await edit(target);

        // The form no longer shows that search, so the note must not claim it does.
        await expect(note).toHaveCount(0);
      });
    }
  });

  test("«Λήψη σύστασης» stays disabled until the prefill lands", async ({ page }) => {
    let releaseJob!: () => void;
    const jobGate = new Promise<void>((resolve) => {
      releaseJob = () => resolve();
    });
    // The remembered search (no ?job=): the page waits for the owner's click
    // here, where a ?job= arrival would ask on its own (pricing-polish.spec).
    const calls = await mockPricingPage(page, {
      jobs: { [STORED_JOB_ID]: competitorJob(STORED_JOB_ID) },
      storedJobId: STORED_JOB_ID,
      jobGate,
    });

    await page.goto("/pricing");

    // The job lookup is parked: the room types are in, the form still holds
    // today's defaults, and a click now would ask for the wrong stay.
    const button = page.getByRole("button", { name: "Λήψη σύστασης" });
    await expect.poll(() => calls.jobIds).toEqual([STORED_JOB_ID]);
    await expect(button).toBeDisabled();

    releaseJob();

    await expect(button).toBeEnabled();
    await expect(formFields(page).checkIn).toHaveValue("2030-07-15");
    await button.click();
    await expect.poll(() => calls.recommendationBodies.length).toBe(1);
    expect(calls.recommendationBodies[0]).toMatchObject({ check_in: "2030-07-15", check_out: "2030-07-18" });
  });

  // `requested` is the last path segment of the one scrape-job GET the page makes.
  const keepsDefaults: Array<
    { name: string; url: string; requested: string } & Pick<PricingMocks, "jobs" | "storedJobId">
  > = [
    {
      name: "a failed search linked by ?job=",
      url: `/pricing?job=${LINKED_JOB_ID}`,
      requested: LINKED_JOB_ID,
      jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID, { status: "failed", error_message: "Booking timeout" }) },
    },
    {
      name: "a remembered search that is still running",
      url: "/pricing",
      requested: STORED_JOB_ID,
      storedJobId: STORED_JOB_ID,
      jobs: { [STORED_JOB_ID]: competitorJob(STORED_JOB_ID, { status: "running", finished_at: null }) },
    },
    {
      name: "a completed room discovery linked by ?job=",
      url: `/pricing?job=${LINKED_JOB_ID}`,
      requested: LINKED_JOB_ID,
      jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID, { job_type: "owned_property_room_discovery" }) },
    },
    {
      name: "a remembered search whose check-in has passed",
      url: "/pricing",
      requested: STORED_JOB_ID,
      storedJobId: STORED_JOB_ID,
      jobs: { [STORED_JOB_ID]: competitorJob(STORED_JOB_ID, { check_in: "2020-07-15", check_out: "2020-07-18" }) },
    },
    {
      name: "a job lookup that fails",
      url: `/pricing?job=${LINKED_JOB_ID}`,
      requested: LINKED_JOB_ID,
      jobs: {},
    },
    {
      // Unencoded, the dot segments would resolve the GET to another endpoint.
      name: "a ?job= that is a path (../../me)",
      url: "/pricing?job=../../me",
      requested: "..%2F..%2Fme",
      jobs: {},
    },
  ];

  for (const scenario of keepsDefaults) {
    test(`${scenario.name} keeps today's defaults without a note`, async ({ page }) => {
      const calls = await mockPricingPage(page, { jobs: scenario.jobs, storedJobId: scenario.storedJobId });

      await page.goto(scenario.url);

      // The history read follows the prefill decision, so once it is out the
      // missing note below is a real absence, not an early look.
      await expect.poll(() => calls.historyQueries.length).toBeGreaterThan(0);
      expect(calls.jobIds).toEqual([scenario.requested]);
      const stay = await defaultStay(page);
      const fields = formFields(page);
      await expect(fields.checkIn).toHaveValue(stay.checkIn);
      await expect(fields.checkOut).toHaveValue(stay.checkOut);
      await expect(fields.adults).toHaveValue("2");
      await expect(fields.children).toHaveValue("0");
      await expect(fields.rooms).toHaveValue("1");
      await expect(fields.category).toHaveValue(ROOM_TYPE_ID);
      await expect(page.getByText(/Σύμφωνα με/)).toHaveCount(0);
      expect(calls.historyQueries[0].get("check_in")).toBe(stay.checkIn);
    });
  }
});

/** Ask for a recommendation answered with `response`; returns the «Στατιστικά αγοράς» panel. */
async function openStatistics(
  page: Page,
  response: Record<string, unknown>,
  totalHotels?: number,
): Promise<Locator> {
  await mockPricingPage(page, { recommendation: response, totalHotels });
  await page.goto("/pricing");
  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
  const panel = page.locator(".panel", { has: page.getByRole("heading", { name: "Στατιστικά αγοράς" }) });
  await expect(panel).toBeVisible();
  return panel;
}

/** The value cell of one statistics row, found by its exact label. */
function statValue(panel: Locator, label: string): Locator {
  return panel
    .locator(".stats-grid > div", { has: panel.page().getByText(label, { exact: true }) })
    .locator("strong");
}

test.describe("statistics in the owner's terms", () => {
  test("the market position reads «Φθηνότερα από εσάς: k από n καταλύματα», not a percentile", async ({ page }) => {
    const panel = await openStatistics(
      page,
      pricingResponse({ statistics: statistics({ own_position_percentile: 57, position: { cheaper_than_you: 4, total: 9 } }) }),
    );

    await expect(statValue(panel, "Θέση σας στην αγορά")).toHaveText("Φθηνότερα από εσάς: 4 από 9 καταλύματα");
    await expect(panel).not.toContainText("P57");
  });

  test("a one-hotel market keeps the small-sample hint and says «κατάλυμα»", async ({ page }) => {
    const panel = await openStatistics(
      page,
      pricingResponse({ statistics: statistics({ position: { cheaper_than_you: 0, total: 1 } }) }),
      1,
    );

    await expect(statValue(panel, "Θέση σας στην αγορά")).toHaveText(
      "Φθηνότερα από εσάς: 0 από 1 κατάλυμα (ενδεικτικό — μικρό δείγμα)",
    );
  });

  test("a missing 7-day trend says no comparable search 7+ days back exists yet", async ({ page }) => {
    const panel = await openStatistics(
      page,
      pricingResponse({ statistics: statistics({ trend_7d_pct: null, trend_30d_pct: 3.4 }) }),
    );

    await expect(statValue(panel, "Τάση (7 ημερών)")).toHaveText("— (δεν υπάρχει ακόμη συγκρίσιμη αναζήτηση 7+ ημερών)");
    // Each row explains only its own gap; a known trend is shown as it is.
    await expect(statValue(panel, "Τάση (30 ημερών)")).toHaveText("+3.4%");
  });

  test("a missing 30-day trend says no comparable search 30+ days back exists yet", async ({ page }) => {
    const panel = await openStatistics(
      page,
      pricingResponse({ statistics: statistics({ trend_7d_pct: -1.5, trend_30d_pct: null }) }),
    );

    await expect(statValue(panel, "Τάση (30 ημερών)")).toHaveText("— (δεν υπάρχει ακόμη συγκρίσιμη αναζήτηση 30+ ημερών)");
    await expect(statValue(panel, "Τάση (7 ημερών)")).toHaveText("-1.5%");
  });

  const sourceLabels = [
    {
      source: "booking_live",
      label: "Η τιμή σας στο Booking για αυτές τις ημερομηνίες",
      other: "Τιμή αναφοράς από την εγγραφή",
    },
    {
      source: "onboarding_sample",
      label: "Τιμή αναφοράς από την εγγραφή",
      other: "Η τιμή σας στο Booking για αυτές τις ημερομηνίες",
    },
  ];

  for (const { source, label, other } of sourceLabels) {
    test(`own_price_source «${source}» names the reference price «${label}»`, async ({ page }) => {
      const panel = await openStatistics(
        page,
        pricingResponse({ ownPriceSource: source, statistics: statistics({ own_reference_price_eur: 92 }) }),
      );

      await expect(statValue(panel, label)).toHaveText("92 €");
      await expect(panel.getByText(other)).toHaveCount(0);
      await expect(panel.getByText("Η τιμή αναφοράς σας")).toHaveCount(0);
    });
  }

  test("the basis line counts the same-category hotels behind the statistics", async ({ page }) => {
    const panel = await openStatistics(
      page,
      pricingResponse({ statistics: statistics({ stats_scope: { same_category: 8, similar: 3, used: "same" } }) }),
    );

    await expect(panel.getByText("Βάση: 8 ίδιας κατηγορίας", { exact: true })).toBeVisible();
    // Similar hotels exist but were not used, so the line does not mention them.
    await expect(panel.getByText(/παρόμοι/)).toHaveCount(0);
  });

  const widenedBases = [
    { similar: 4, text: "Βάση: 3 ίδιας κατηγορίας + 4 παρόμοια" },
    { similar: 1, text: "Βάση: 3 ίδιας κατηγορίας + 1 παρόμοιο" },
  ];

  for (const { similar, text } of widenedBases) {
    test(`below 5 same-category hotels the basis line adds the ${similar} similar`, async ({ page }) => {
      const panel = await openStatistics(
        page,
        pricingResponse({
          statistics: statistics({ stats_scope: { same_category: 3, similar, used: "same_plus_similar" } }),
        }),
      );

      await expect(panel.getByText(text, { exact: true })).toBeVisible();
    });
  }

  test("with no recommendation the notes are listed once, in the not-enough-data card", async ({ page }) => {
    const notes = [
      "Δεν υπάρχει ιστορικό τιμών για αυτές τις ημερομηνίες και παραμέτρους.",
      "Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας.",
    ];
    await openStatistics(
      page,
      pricingResponse({
        recommendation: null,
        ownPriceSource: null,
        statistics: statistics({
          sample_runs: 0,
          sample_days: 0,
          own_reference_price_eur: null,
          market_median_eur: null,
          market_p25_eur: null,
          market_p75_eur: null,
          own_position_percentile: null,
          position: null,
          stats_scope: null,
          trend_7d_pct: null,
          statistical_recommendation_eur: null,
          notes,
        }),
      }),
    );

    const card = page.locator(".recommendation-card");
    await expect(card.getByRole("heading", { name: "Δεν υπάρχουν αρκετά δεδομένα για σύσταση" })).toBeVisible();
    await expect(card.locator(".stat-notes li")).toHaveText(notes);
    // ...and nowhere else: the statistics panel does not list them a second time.
    await expect(page.locator(".stat-notes li")).toHaveText(notes);
  });

  test("a note the reasoning already quotes is not listed again", async ({ page }) => {
    const quoted = "Υπάρχει μόνο 1 αναζήτηση στο ιστορικό· οι τάσεις δεν είναι διαθέσιμες.";
    const listed = "Το ιστορικό δεν καλύπτει 30 ημέρες· η τάση 30 ημερών δεν είναι διαθέσιμη.";
    const panel = await openStatistics(
      page,
      pricingResponse({
        statistics: statistics({ notes: [quoted, listed] }),
        recommendation: recommendation({ reasoning: `Στατιστική σύσταση (χωρίς AI). ${quoted}` }),
      }),
    );

    // The full reasoning sits behind «Γιατί αυτή η τιμή;» (the card shows its
    // first sentence only), so it is opened before the quote is looked for.
    await page.getByRole("button", { name: "Γιατί αυτή η τιμή;" }).click();
    await expect(page.locator(".recommendation-reasoning")).toContainText(quoted);
    await expect(panel.locator(".stat-notes li")).toHaveText([listed]);
    await expect(page.locator(".stat-notes li")).toHaveText([listed]);
  });
});
