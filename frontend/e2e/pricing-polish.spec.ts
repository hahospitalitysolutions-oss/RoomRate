/**
 * Pricing page polish: the room select as the page's primary choice, the
 * recommendation card's headline-first layout, the recommendation asked
 * automatically when the page opens from a search (`?job=`), and a compact
 * low-data panel in place of a near-empty history chart.
 *
 * Every backend surface the page touches is mocked with `**` globs behind a
 * loud 404 catch-all, so a live API on :8000 can never leak into these tests.
 */
import { expect, Page, test } from "@playwright/test";

import { CURRENT_USER, OWNED_PROPERTY_ID, ROOM_TYPE_ID, seedBrowserState } from "./helpers";

// Run timestamps are absolute instants the chart renders as local dates.
test.use({ timezoneId: "Europe/Athens" });

const LINKED_JOB_ID = "44444444-4444-4444-4444-4444444444cc";
const SECOND_ROOM_ID = "33333333-3333-3333-3333-3333333333dd";

const ROOM_TYPES = [
  {
    id: ROOM_TYPE_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Δίκλινο Δωμάτιο",
    room_type_category: "double",
    is_active: true,
  },
  {
    id: SECOND_ROOM_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Οικονομικό Δίκλινο",
    room_type_category: "double",
    is_active: true,
  },
];

/** A /market/price-history payload with one competitor price per (run, hotel); runs given OLDEST first. */
function priceHistory(runs: Array<{ observedAt: string; prices: number[] }>): Record<string, unknown> {
  return {
    canonical_destination: "faliraki",
    check_in: "2030-07-15",
    check_out: "2030-07-18",
    points: runs.flatMap((run, position) =>
      run.prices.map((price, hotel) => ({
        // run_index 1 is the newest run.
        run_index: runs.length - position,
        observed_at: run.observedAt,
        hotel_name: `Hotel ${hotel + 1}`,
        min_price_eur: price,
      })),
    ),
  };
}

const TWO_RUNS = priceHistory([
  { observedAt: "2026-09-14T07:00:00Z", prices: [96, 104, 112] },
  { observedAt: "2026-09-14T15:00:00Z", prices: [100, 108, 116] },
]);

const THREE_RUNS = priceHistory([
  { observedAt: "2026-09-12T07:00:00Z", prices: [96, 104, 112] },
  { observedAt: "2026-09-13T07:00:00Z", prices: [100, 108, 116] },
  { observedAt: "2026-09-14T07:00:00Z", prices: [92, 100, 108] },
]);

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

/** A valid PriceRecommendation; tests override only what they pin. */
function recommendation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    recommended_price_eur: 114,
    price_range_low_eur: 104.4,
    price_range_high_eur: 124.2,
    confidence: "high",
    reasoning:
      "Η τιμή τοποθετείται λίγο πάνω από τη διάμεσο της αγοράς. "
      + "Οι ανταγωνιστές με παρόμοια βαθμολογία κινούνται στα 110-120 €. "
      + "Η ζήτηση για αυτές τις ημερομηνίες ανεβαίνει.",
    key_factors: ["Διάμεσος αγοράς 110 €", "Φθηνότερα από εσάς: 7 από 11"],
    source: "agent",
    ...overrides,
  };
}

/** A POST /api/v1/agents/price-recommendation body. */
function pricingResponse(rec: Record<string, unknown> = recommendation()): Record<string, unknown> {
  return {
    statistics: {
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
    },
    recommendation: rec,
    recommendation_available: true,
    audit_id: "55555555-5555-5555-5555-5555555555cc",
    generated_at: "2026-09-15T09:00:00Z",
    cached: false,
    model_version: "claude-agent-v2",
    prompt_version: "2026-09-15.v2",
    own_price_source: "booking_live",
  };
}

/** A completed competitor search as GET /api/v1/scrape-jobs/{id} returns it. */
function competitorJob(id: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id,
    account_id: CURRENT_USER.account_id,
    owned_property_id: OWNED_PROPERTY_ID,
    job_type: "competitor_search",
    room_type_category: "double",
    destination: "Faliraki",
    raw_destination: "Faliraki, Rhodes",
    canonical_destination: "faliraki",
    check_in: "2030-07-15",
    check_out: "2030-07-18",
    adults: 2,
    children: 0,
    rooms: 1,
    filters_payload: { limit: 40 },
    nearby_destinations: [],
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

type PolishMocks = {
  jobs?: Record<string, Record<string, unknown>>;
  /** Seeds workflow storage's last competitor search for the e2e user. */
  storedJobId?: string;
  history?: Record<string, unknown>;
  response?: Record<string, unknown>;
  /** Parks every recommendation POST (after recording it) until this settles. */
  recommendationGate?: Promise<void>;
};

type PolishCalls = {
  /** Every call the tests order against, in the order the page made them. */
  sequence: string[];
  historyQueries: URLSearchParams[];
  recommendationBodies: Array<Record<string, unknown>>;
};

async function mockPricingPage(page: Page, mocks: PolishMocks = {}): Promise<PolishCalls> {
  const calls: PolishCalls = { sequence: [], historyQueries: [], recommendationBodies: [] };
  await seedBrowserState(page);
  if (mocks.storedJobId) {
    // WorkflowStorageService keys are per auth subject.
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
  await page.route("**/api/v1/scrape-jobs/**", (route) => {
    const jobId = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    calls.sequence.push(`job:${jobId}`);
    const job = mocks.jobs?.[jobId];
    return job
      ? route.fulfill({ json: job })
      : route.fulfill({ status: 404, json: { detail: "Η εργασία δεν βρέθηκε." } });
  });
  await page.route("**/api/v1/market/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/market/price-history") {
      calls.sequence.push("history");
      calls.historyQueries.push(url.searchParams);
      return route.fulfill({ json: mocks.history ?? THREE_RUNS });
    }
    if (url.pathname === "/api/v1/market/summary") {
      return route.fulfill({ json: MARKET_SUMMARY });
    }
    return route.fallback();
  });
  await page.route("**/api/v1/agents/**", async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname !== "/api/v1/agents/price-recommendation" || request.method() !== "POST") {
      return route.fallback();
    }
    calls.sequence.push("recommendation");
    calls.recommendationBodies.push(request.postDataJSON());
    await mocks.recommendationGate;
    return route.fulfill({ json: mocks.response ?? pricingResponse() });
  });
  return calls;
}

test.describe("the room select is the page's primary choice", () => {
  test("«Σύγκριση για» sits above the stay form and drives owned_room_type_id", async ({ page }) => {
    const calls = await mockPricingPage(page);

    await page.goto("/pricing");

    const select = page.getByLabel("Σύγκριση για");
    await expect(select).toBeVisible();
    // Above the dates/occupancy form, and no longer inside it.
    await expect(page.locator(".form-panel select")).toHaveCount(0);
    const selectBox = await select.boundingBox();
    const formBox = await page.locator(".form-panel").boundingBox();
    expect(selectBox!.y + selectBox!.height).toBeLessThanOrEqual(formBox!.y);
    // Keyed by room id with the «{room_type} ({category})» label.
    await expect(select.locator("option", { hasText: "Δίκλινο Δωμάτιο (double)" })).toHaveCount(1);
    await expect(select.locator("option", { hasText: "Οικονομικό Δίκλινο (double)" })).toHaveCount(1);
    await expect(select).toHaveValue(ROOM_TYPE_ID);

    await select.selectOption(SECOND_ROOM_ID);
    await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

    await expect.poll(() => calls.recommendationBodies.length).toBe(1);
    expect(calls.recommendationBodies[0]).toMatchObject({
      owned_room_type_id: SECOND_ROOM_ID,
      room_type_category: "double",
    });
    await expect.poll(() => calls.historyQueries.at(-1)?.get("owned_room_type_id")).toBe(SECOND_ROOM_ID);
  });
});

test.describe("the recommendation card leads with the price", () => {
  test("headline, range, pills, one summary sentence, factor chips and a closed «Γιατί αυτή η τιμή;»", async ({ page }) => {
    await mockPricingPage(page);
    await page.goto("/pricing");
    await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

    const card = page.locator(".recommendation-card");
    await expect(card.locator(".recommendation-label")).toHaveText("Προτεινόμενη τιμή ανά βράδυ");
    await expect(card.locator(".recommendation-price")).toHaveText("114 €");
    // The range rounds outward: 104.4 → 104, 124.2 → 125.
    await expect(card.locator(".recommendation-range")).toHaveText("Εύρος 104 € – 125 €");
    await expect(card.getByTestId("confidence-pill")).toHaveText("Υψηλή βεβαιότητα");
    await expect(card.getByTestId("source-pill")).toHaveText("AI agent");
    // The headline block comes first: the price sits above the summary sentence.
    const priceBox = await card.locator(".recommendation-price").boundingBox();
    const summaryBox = await card.locator(".recommendation-summary").boundingBox();
    expect(priceBox!.y).toBeLessThan(summaryBox!.y);

    // ONE sentence of why; the rest waits behind the toggle.
    await expect(card.locator(".recommendation-summary")).toHaveText(
      "Η τιμή τοποθετείται λίγο πάνω από τη διάμεσο της αγοράς.",
    );
    await expect(card.locator(".key-factors .factor-chip")).toHaveText([
      "Διάμεσος αγοράς 110 €",
      "Φθηνότερα από εσάς: 7 από 11",
    ]);
    const toggle = card.getByRole("button", { name: "Γιατί αυτή η τιμή;" });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(card.locator(".recommendation-reasoning")).toHaveCount(0);
    await expect(card.getByText("Η ζήτηση για αυτές τις ημερομηνίες ανεβαίνει.")).toHaveCount(0);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(card.locator(".recommendation-reasoning")).toHaveText(
      "Η τιμή τοποθετείται λίγο πάνω από τη διάμεσο της αγοράς. "
      + "Οι ανταγωνιστές με παρόμοια βαθμολογία κινούνται στα 110-120 €. "
      + "Η ζήτηση για αυτές τις ημερομηνίες ανεβαίνει.",
    );
    await toggle.click();
    await expect(card.locator(".recommendation-reasoning")).toHaveCount(0);

    // The audit trail is a small, muted footer.
    const footer = card.locator(".recommendation-footer");
    await expect(footer).toHaveText("Ελεγμένη απόφαση 55555555-5555-5555-5555-5555555555cc · claude-agent-v2");
    const footerSize = await footer.evaluate((node) => parseFloat(getComputedStyle(node).fontSize));
    expect(footerSize).toBeLessThanOrEqual(12);

    // A new recommendation starts with the reasoning closed again.
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
    await expect(card.getByRole("button", { name: "Γιατί αυτή η τιμή;" })).toHaveAttribute("aria-expanded", "false");
  });

  test("the confidence pill is green for Υψηλή, amber for Μέτρια and grey for Χαμηλή", async ({ page }) => {
    const mocks: PolishMocks = {};
    await mockPricingPage(page, mocks);
    const cases = [
      { confidence: "high", label: "Υψηλή βεβαιότητα", background: "rgb(236, 253, 245)", source: "agent", sourceLabel: "AI agent" },
      { confidence: "medium", label: "Μέτρια βεβαιότητα", background: "rgb(255, 251, 235)", source: "statistical", sourceLabel: "Στατιστική" },
      { confidence: "low", label: "Χαμηλή βεβαιότητα", background: "rgb(241, 245, 249)", source: "statistical", sourceLabel: "Στατιστική" },
    ];
    for (const { confidence, label, background, source, sourceLabel } of cases) {
      await test.step(confidence, async () => {
        mocks.response = pricingResponse(recommendation({ confidence, source }));
        await page.goto("/pricing");
        await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
        const pill = page.getByTestId("confidence-pill");
        await expect(pill).toHaveText(label);
        await expect(pill).toHaveCSS("background-color", background);
        await expect(page.getByTestId("source-pill")).toHaveText(sourceLabel);
      });
    }
  });

  test("a one-sentence reasoning is its own summary, rendered as text, with no toggle", async ({ page }) => {
    await mockPricingPage(page, {
      response: pricingResponse(recommendation({
        reasoning: "Στατιστική σύσταση <b>χωρίς AI</b> με βάση τη διάμεσο 110.5 € της αγοράς.",
        key_factors: [],
      })),
    });
    await page.goto("/pricing");
    await page.getByRole("button", { name: "Λήψη σύστασης" }).click();

    const card = page.locator(".recommendation-card");
    // A decimal point is not a sentence end, and markup stays literal text.
    await expect(card.locator(".recommendation-summary")).toHaveText(
      "Στατιστική σύσταση <b>χωρίς AI</b> με βάση τη διάμεσο 110.5 € της αγοράς.",
    );
    await expect(card.locator(".recommendation-summary b")).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Γιατί αυτή η τιμή;" })).toHaveCount(0);
    await expect(card.locator(".key-factors")).toHaveCount(0);
  });
});

test.describe("arriving from a search asks for the recommendation by itself", () => {
  test("?job= asks exactly once after the job lookup, with a busy skeleton while it loads", async ({ page }) => {
    let releaseRecommendation!: () => void;
    const recommendationGate = new Promise<void>((resolve) => {
      releaseRecommendation = () => resolve();
    });
    const calls = await mockPricingPage(page, {
      jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID) },
      recommendationGate,
    });

    await page.goto(`/pricing?job=${LINKED_JOB_ID}`);

    // No click: the replayed search is asked for as soon as the prefill lands.
    await expect.poll(() => calls.recommendationBodies.length).toBe(1);
    expect(calls.sequence.indexOf(`job:${LINKED_JOB_ID}`)).toBeGreaterThanOrEqual(0);
    expect(calls.sequence.indexOf("recommendation")).toBeGreaterThan(calls.sequence.indexOf(`job:${LINKED_JOB_ID}`));
    expect(calls.recommendationBodies[0]).toMatchObject({ check_in: "2030-07-15", check_out: "2030-07-18" });

    // While the answer is parked the card's skeleton stands in for it.
    const skeleton = page.getByTestId("recommendation-skeleton");
    await expect(skeleton).toBeVisible();
    await expect(skeleton).toHaveAttribute("aria-busy", "true");
    await expect(page.locator(".recommendation-card")).toHaveCount(0);
    await expect(page.getByText("Δεν υπάρχει ακόμη σύσταση")).toHaveCount(0);

    releaseRecommendation();

    await expect(page.locator(".recommendation-card .recommendation-price")).toHaveText("114 €");
    await expect(skeleton).toHaveCount(0);
    // Once: no second request follows, and the chart (read for this very
    // form on load) is not read a second time.
    await page.waitForTimeout(400);
    expect(calls.recommendationBodies).toHaveLength(1);
    expect(calls.historyQueries).toHaveLength(1);
  });

  const manual: Array<{ name: string; url: string } & Pick<PolishMocks, "jobs" | "storedJobId">> = [
    { name: "without ?job=", url: "/pricing" },
    {
      name: "with only a remembered search",
      url: "/pricing",
      storedJobId: LINKED_JOB_ID,
      jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID) },
    },
    {
      name: "when the ?job= search failed",
      url: `/pricing?job=${LINKED_JOB_ID}`,
      jobs: { [LINKED_JOB_ID]: competitorJob(LINKED_JOB_ID, { status: "failed", finished_at: null }) },
    },
  ];

  for (const scenario of manual) {
    test(`${scenario.name} nothing is asked until «Λήψη σύστασης» is clicked`, async ({ page }) => {
      const calls = await mockPricingPage(page, { jobs: scenario.jobs, storedJobId: scenario.storedJobId });

      await page.goto(scenario.url);

      // The history read follows the prefill decision, so the page has settled.
      await expect.poll(() => calls.historyQueries.length).toBe(1);
      const button = page.getByRole("button", { name: "Λήψη σύστασης" });
      await expect(button).toBeEnabled();
      await page.waitForTimeout(400);
      expect(calls.recommendationBodies).toHaveLength(0);
      await expect(page.getByText("Δεν υπάρχει ακόμη σύσταση")).toBeVisible();
      await expect(page.getByTestId("recommendation-skeleton")).toHaveCount(0);

      await button.click();

      await expect.poll(() => calls.recommendationBodies.length).toBe(1);
      await expect(page.locator(".recommendation-card .recommendation-price")).toHaveText("114 €");
    });
  }
});

test.describe("a history too short for a trend", () => {
  test("≤ 2 runs show the low-data panel with a CTA to the daily automatic search", async ({ page }) => {
    await mockPricingPage(page, { history: TWO_RUNS });

    await page.goto("/pricing");

    const panel = page.getByTestId("history-low-data");
    await expect(panel).toContainText("Χρειάζονται αναζητήσεις σε διαφορετικές ημέρες για να φανεί τάση.");
    await expect(page.locator(".history-svg")).toHaveCount(0);
    const cta = panel.getByRole("link", { name: "Ρύθμιση ημερήσιας αυτόματης αναζήτησης" });
    // Settings' recurring-search form carries id="schedule".
    await expect(cta).toHaveAttribute("href", "/settings#schedule");

    await cta.click();
    await expect(page).toHaveURL((url) => url.pathname === "/settings" && url.hash === "#schedule");
  });

  test("≥ 3 runs keep the chart, without the low-data panel", async ({ page }) => {
    await mockPricingPage(page, { history: THREE_RUNS });

    await page.goto("/pricing");

    const chart = page.locator(".history-svg");
    await expect(chart.locator("circle.history-dot")).toHaveCount(3);
    await expect(chart.locator("polyline.history-line")).toHaveCount(1);
    await expect(page.getByTestId("history-low-data")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Ρύθμιση ημερήσιας αυτόματης αναζήτησης" })).toHaveCount(0);
  });
});
