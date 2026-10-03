/**
 * Regression tests: data must appear WITHOUT a second click or manual refresh.
 *
 * Why these exist: the auth SDK (supabase-js then, Neon Auth now) resolves
 * getSession() through APIs zone.js cannot always patch (supabase-js used a
 * navigator.locks lock). Every API call
 * awaits getAccessToken() first, so the continuation after that await runs
 * OUTSIDE the Angular zone — plain-field mutations there never triggered
 * change detection and the UI only caught up on the next unrelated click.
 * Component state is signal-based precisely so out-of-zone completions still
 * schedule change detection; these tests pin that behavior in a real browser.
 *
 * The FastAPI backend, the /ws/alerts WebSocket and the auth session are
 * all mocked, so the only moving part is the Angular app itself.
 */

import { expect, Page, Route, test, WebSocketRoute } from "@playwright/test";

import {
  ACCOUNT_ID,
  API,
  CURRENT_USER,
  JOB_ID,
  mockMapbox,
  OWNED_PROPERTY_ID,
  ROOM_TYPE_ID,
  seedBrowserState,
} from "./helpers";

const PROP_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROP_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
// The job a "Find Competitors" click creates, as opposed to JOB_ID, the
// completed job the page restores on load.
const NEW_JOB_ID = "22222222-2222-2222-2222-2222222222bb";

// map-page.component.ts findCompetitors()'s refusal for a check-in that has
// already passed. Shared by the two date tests below on purpose: one asserts
// it is ABSENT after a restore (toHaveCount(0)) and the other asserts it is
// PRESENT when a past date really is in the field. A bare negative proves
// nothing on its own -- any typo in the sentence, or a refusal that moved
// earlier in the function, would make it pass while the page stayed broken.
// Pinned in the same constant, the positive case fails the moment the string
// drifts, so the negative cannot quietly go toothless.
const PAST_CHECK_IN_REFUSAL = "Η άφιξη δεν μπορεί να είναι στο παρελθόν.";

/** Yesterday in the browser's own local calendar, as `yyyy-mm-dd`. */
function yesterdayLocalDate(): string {
  const date = new Date();
  date.setDate(date.getDate() - 1);
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

const ROOM_TYPES = [
  {
    id: ROOM_TYPE_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Double Room with Sea View",
    room_type_category: "double",
    is_active: true,
  },
];

const COMPLETED_JOB = {
  id: JOB_ID,
  account_id: ACCOUNT_ID,
  owned_property_id: OWNED_PROPERTY_ID,
  job_type: "competitor_search",
  room_type_category: "double",
  destination: "Faliraki",
  raw_destination: "Faliraki, Rhodes",
  canonical_destination: "faliraki",
  check_in: "2026-07-01",
  check_out: "2026-07-05",
  adults: 2,
  children: 0,
  rooms: 1,
  filters_payload: { limit: 8 },
  status: "completed",
  requested_at: "2026-07-20T10:00:00Z",
  started_at: "2026-07-20T10:00:05Z",
  finished_at: "2026-07-20T10:04:00Z",
  error_message: null,
  attempt_count: 1,
  max_attempts: 3,
  next_attempt_at: null,
  scrape_runs_count: 1,
};

const MAP_MARKERS = [
  {
    hotel_name: "Hotel Alpha",
    property_id: PROP_ALPHA,
    room_package_id: null,
    room_type: "Double Room",
    room_type_category: "double",
    property_type: "hotel",
    latitude: 36.34,
    longitude: 28.2,
    price_per_night_eur: 80,
    review_score: 8.4,
    review_count: 120,
    rooms_left: 3,
  },
  {
    hotel_name: "Hotel Beta",
    property_id: PROP_BETA,
    room_package_id: null,
    room_type: "Standard Double",
    room_type_category: "double",
    property_type: "hotel",
    latitude: 36.35,
    longitude: 28.21,
    price_per_night_eur: 120,
    review_score: 9.1,
    review_count: 300,
    rooms_left: 1,
  },
];

const MARKET_SUMMARY = {
  destination: "faliraki",
  check_in: "2026-07-01",
  check_out: "2026-07-05",
  total_records: 42,
  total_hotels: 17,
  price_min_eur: 60,
  price_max_eur: 240,
  price_avg_eur: 118,
  price_median_eur: 110,
  avg_review_score: 8.6,
  rooms_left_total: 55,
};

const PRICE_HISTORY = {
  canonical_destination: "faliraki",
  check_in: "2026-07-01",
  check_out: "2026-07-05",
  points: [
    { run_index: 2, observed_at: "2026-07-10T08:00:00Z", hotel_name: "Hotel Alpha", min_price_eur: 90 },
    { run_index: 2, observed_at: "2026-07-10T08:00:00Z", hotel_name: "Hotel Beta", min_price_eur: 130 },
    { run_index: 1, observed_at: "2026-07-20T08:00:00Z", hotel_name: "Hotel Alpha", min_price_eur: 80 },
    { run_index: 1, observed_at: "2026-07-20T08:00:00Z", hotel_name: "Hotel Beta", min_price_eur: 120 },
  ],
};

const RECOMMENDATION = {
  statistics: {
    sample_runs: 2,
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
  },
  recommendation: {
    recommended_price_eur: 112,
    price_range_low_eur: 98,
    price_range_high_eur: 124,
    confidence: "medium",
    reasoning: "Positioned just above the market median for a strong review score.",
    key_factors: ["Market median EUR 110"],
    source: "agent",
  },
  recommendation_available: true,
};

const NOTIFICATIONS = [
  {
    id: "44444444-4444-4444-4444-444444444401",
    notification_type: "price_change",
    title: "Price drop: Hotel Alpha 12.5%",
    message: "Hotel Alpha moved from €80.00 to €70.00 (-12.5%) for faliraki.",
    payload: null,
    is_read: false,
    created_at: "2026-07-22T09:00:00Z",
  },
  {
    id: "44444444-4444-4444-4444-444444444402",
    notification_type: "price_change",
    title: "Price rise: Hotel Beta 15.0%",
    message: "Hotel Beta moved from €120.00 to €138.00 (+15.0%) for faliraki.",
    payload: null,
    is_read: false,
    created_at: "2026-07-21T09:00:00Z",
  },
];

type MockOptions = {
  unreadCount?: number;
};

/** Mock every backend surface the pages touch; unknown API paths 404 loudly. */
async function mockBackend(page: Page, options: MockOptions = {}): Promise<void> {
  const unreadCount = options.unreadCount ?? 2;

  // The map loads Mapbox GL from the bundle but its network calls (styles,
  // tiles, telemetry) must never leave the test. Abort them. Scenarios that
  // assert on `status` or message() call mockMapbox afterwards, because a
  // FAILED style is not neutral for those — see the helper.
  await page.route(/https:\/\/(api|events)\.mapbox\.com\/.*/, (route) => route.abort());

  await page.route(`${API}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "*" },
        body: JSON.stringify(body),
      });

    if (method === "OPTIONS") {
      return route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET, POST, PUT, OPTIONS",
          "access-control-allow-headers": "authorization, content-type, x-api-key, x-roomrate-account-id",
        },
      });
    }

    if (path === "/api/v1/me") {
      return json(CURRENT_USER);
    }
    if (path === `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`) {
      return json(ROOM_TYPES);
    }
    if (path === `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`) {
      return json({
        owned_property_id: OWNED_PROPERTY_ID,
        selected_room_type_category: "double",
        onboarding_complete: true,
      });
    }
    if (path === `/api/v1/scrape-jobs/${JOB_ID}`) {
      return json(COMPLETED_JOB);
    }
    if (path === "/api/v1/scrape-jobs/" && method === "GET") {
      return json([COMPLETED_JOB]);
    }
    if (path === "/api/v1/market/amenities") {
      return json(["Free WiFi | Swimming pool | Breakfast"]);
    }
    if (path === "/api/v1/maps/competitors") {
      return json(MAP_MARKERS);
    }
    if (path === "/api/v1/tracked/competitors" && method === "GET") {
      return json({
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "double",
        competitors: [{ property_id: PROP_ALPHA, room_package_id: null }],
      });
    }
    if (path === "/api/v1/market/summary") {
      return json(MARKET_SUMMARY);
    }
    if (path === "/api/v1/market/price-history") {
      return json(PRICE_HISTORY);
    }
    if (path === "/api/v1/agents/price-recommendation" && method === "POST") {
      return json(RECOMMENDATION);
    }
    if (path === "/api/v1/notifications/unread-count") {
      return json({ count: unreadCount });
    }
    if (path === "/api/v1/notifications/ws-ticket" && method === "POST") {
      return json({ ticket: "e2e-one-time-ticket", expires_in_seconds: 60 });
    }
    if (path === "/api/v1/notifications" && method === "GET") {
      return json(NOTIFICATIONS);
    }

    // Unknown call = missing mock. Fail loudly instead of hanging the UI.
    return json({ detail: `e2e mock missing for ${method} ${path}` }, 404);
  });
}

/**
 * Accept the /ws/alerts handshake and hand the server side to the test.
 *
 * Awaits the route REGISTRATION before returning (otherwise page.goto races
 * it and the app's first connection attempt can slip through to the real —
 * dead — backend port). The connection promise is wrapped in an object so
 * awaiting the registration does NOT flatten into awaiting the connection.
 */
async function interceptAlertSocket(page: Page): Promise<{ connection: Promise<WebSocketRoute> }> {
  let resolveSocket: (ws: WebSocketRoute) => void;
  const connection = new Promise<WebSocketRoute>((resolve) => {
    resolveSocket = resolve;
  });
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => {
    // The client only ever listens; swallow anything it sends.
    ws.onMessage(() => undefined);
    ws.send(JSON.stringify({ type: "connected", message: "RoomRate alerts connected" }));
    resolveSocket(ws);
  });
  return { connection };
}

/**
 * Answer a "Find Competitors" click with a job that never finishes.
 *
 * The scenarios below all press Find in the middle of another chain and then
 * ask what the screen shows WHILE the new search runs. A job that stays
 * "running" means nothing of the new search's own can overwrite a residual
 * write, so the assertions observe a stable state instead of racing a flash.
 *
 * `gateRestoreLookup` optionally parks the restore chain on its job lookup,
 * and `restoredJob` overrides what that lookup returns (list and detail shapes
 * from one object, like the app's two ways of finding a job).
 */
async function mockRunningSearchJob(
  page: Page,
  options: { gateRestoreLookup?: Promise<void>; restoredJob?: object } = {},
): Promise<{
  restoreLookupRequested: Promise<void>;
  restoreLookupSettled: () => boolean;
  letSupersededChainSettle: () => Promise<void>;
}> {
  const runningJob = { ...COMPLETED_JOB, id: NEW_JOB_ID, status: "running", finished_at: null };
  let polls = 0;
  let lookups = 0;
  let restoreLookupSettled = false;
  let markLookupRequested!: () => void;
  const restoreLookupRequested = new Promise<void>((resolve) => (markLookupRequested = resolve));

  await page.route(`${API}/api/v1/scrape-jobs/**`, async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST") {
      return route.fulfill({ json: runningJob });
    }
    if (url.pathname.endsWith(NEW_JOB_ID)) {
      polls += 1;
      return route.fulfill({ json: runningJob });
    }
    lookups += 1;
    markLookupRequested();
    if (options.gateRestoreLookup && lookups === 1) {
      await options.gateRestoreLookup;
    }
    restoreLookupSettled = true;
    if (!options.restoredJob) {
      // The restored job's detail/list reads stay on mockBackend's wildcard.
      return route.fallback();
    }
    return route.fulfill({
      json: url.pathname.endsWith("/scrape-jobs/") ? [options.restoredJob] : options.restoredJob,
    });
  });

  return {
    restoreLookupRequested,
    restoreLookupSettled: () => restoreLookupSettled,
    // Give the superseded chain every chance to do damage before looking: one
    // more poll request proves a full 5s poll interval of live app time passed
    // since the release, during which that chain's remaining mocked reads
    // would have completed many times over.
    letSupersededChainSettle: async () => {
      const pollsSoFar = polls;
      await expect.poll(() => polls, { timeout: 30_000 }).toBeGreaterThan(pollsSoFar);
    },
  };
}

/**
 * Park the restore chain ON its marker read, condition-held (never timed), and
 * hand back the release. Later reads answer instantly, so only the first
 * chain is held.
 */
async function gateRestoreMarkerRead(
  page: Page,
  response: Parameters<Route["fulfill"]>[0],
): Promise<{ requested: Promise<void>; settled: () => boolean; release: () => void }> {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let markRequested!: () => void;
  const requested = new Promise<void>((resolve) => (markRequested = resolve));
  let settled = false;
  let reads = 0;

  await page.route(`${API}/api/v1/maps/competitors**`, async (route) => {
    reads += 1;
    if (reads > 1) {
      return route.fulfill({ json: MAP_MARKERS });
    }
    markRequested();
    await gate;
    settled = true;
    return route.fulfill(response);
  });

  return { requested, settled: () => settled, release };
}

test.describe("data renders without a second click or manual refresh", () => {
  test.beforeEach(async ({ page }) => {
    await seedBrowserState(page);
  });

  test("map page shows the restored competitor search on load", async ({ page }) => {
    await mockBackend(page);
    await interceptAlertSocket(page);

    await page.goto("/map");

    // Header count, competitor cards and the market snapshot all come from
    // fetches whose continuations run outside the Angular zone. They must
    // appear with ZERO interactions.
    await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();
    await expect(page.getByText("Hotel Alpha")).toBeVisible();
    await expect(page.getByText("Hotel Beta")).toBeVisible();
    // The one market summary box (Round 6, spec §4.7), from /market/summary:
    // its counts line and its prices, lowest 60 €, highest 240 € (el-GR
    // currency formatting puts the symbol after the amount). It replaced the
    // marker-price snapshot (80 € / 120 €) and the separate records strip.
    await expect(page.getByTestId("market-summary-counts")).toHaveText("Καταλύματα 17 · Καταγραφές 42");
    await expect(page.getByTestId("market-summary").getByText("60 €")).toBeVisible();
    await expect(page.getByTestId("market-summary").getByText("240 €")).toBeVisible();
    // The tracked competitor restored into a checked card checkbox.
    await expect(
      page.locator(".competitor-card", { hasText: "Hotel Alpha" }).locator("input[type=checkbox]"),
    ).toBeChecked();
  });

  test("a restored search with past dates does not lock the map", async ({ page }) => {
    // COMPLETED_JOB's stay (2026-07-01 -> 2026-07-05) is in the past relative
    // to any later run of this suite. The backend now rejects a past check_in,
    // so blindly restoring the job's dates left the user staring at data they
    // could not refresh: "Find Competitors" answered "Check-in cannot be in
    // the past" until they hand-edited both date fields.
    await mockBackend(page);
    await interceptAlertSocket(page);

    await page.goto("/map");
    await expect(page.getByText("Hotel Alpha")).toBeVisible();

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    const checkIn = page.locator('input[type="date"]').first();
    const checkOut = page.locator('input[type="date"]').nth(1);
    const today = new Date().toISOString().slice(0, 10);

    // The restored window is rolled forward, keeping its 4-night length.
    await expect(async () => {
      expect(await checkIn.inputValue()).not.toBe("");
      expect(await checkIn.inputValue() >= today).toBe(true);
    }).toPass();
    const nights =
      (Date.parse(await checkOut.inputValue()) - Date.parse(await checkIn.inputValue()))
      / 86_400_000;
    expect(nights).toBe(4);

    // And the primary action works instead of refusing.
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    // Re-derived for the Greek page (Round 5.1): the English sentence is gone
    // from the app, so asserting its absence would pass no matter what. The
    // Greek below is the exact refusal findCompetitors sets when the restored
    // job's past check-in was replayed instead of rolled forward. The test
    // right after this one is its positive anchor.
    await expect(page.getByText(PAST_CHECK_IN_REFUSAL)).toHaveCount(0);
  });

  test("a hand-typed past check-in is refused with that same message", async ({ page }) => {
    // The positive half of the pair. Without it, the toHaveCount(0) above is
    // satisfied by a page that can never show the refusal at all -- including
    // one where an EARLIER guard in findCompetitors (missing property, unset
    // room, blank destination) is what actually stops the click. Reaching this
    // message proves the click travelled past all of those and stopped exactly
    // on the date.
    await mockBackend(page);
    await interceptAlertSocket(page);

    await page.goto("/map");
    await expect(page.getByText("Hotel Alpha")).toBeVisible();

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    const checkIn = page.locator('input[type="date"]').first();
    // Wait for the restore chain's own date write before overwriting it, so
    // this fill cannot be clobbered by a late rollforward.
    await expect(async () => {
      expect(await checkIn.inputValue()).not.toBe("");
    }).toPass();
    await checkIn.fill(yesterdayLocalDate());

    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    await expect(page.getByText(PAST_CHECK_IN_REFUSAL)).toBeVisible();
  });

  test("a competitor card clicked mid-chain stays selected once the load chain settles", async ({ page }) => {
    await mockBackend(page);
    await interceptAlertSocket(page);

    // LIFO: mockBackend's `${API}/**` wildcard above still answers every
    // other call instantly; this specific override (registered after it,
    // `**` because the real request carries a query string) wins for
    // tracked-competitors reads. /api/v1/maps/competitors -- the cards'
    // data source -- stays on the instant wildcard response, so the cards
    // are on screen and clickable DURING this delay: map-page.component.ts's
    // loadMarkers awaits fetchTrackedCompetitors() AFTER competitors() is
    // already populated, which is exactly the live-observed race window.
    // The delayed payload also reports NO tracked competitors -- Hotel
    // Alpha is mockBackend's default tracked competitor, and leaving that
    // in place would let a real tracked-list match masquerade as the click
    // surviving.
    // The race window is CONDITION-held, not timed: the route keeps the
    // tracked response open until the test releases the gate after the
    // click+clear landed. A timed window (2s, then 5s) lost to dev-server
    // contention under parallel workers — the whole setup slipped past it
    // and the scenario went silently toothless.
    let releaseTracked!: () => void;
    const trackedGate = new Promise<void>((resolve) => (releaseTracked = resolve));
    await page.route(`${API}/api/v1/tracked/competitors**`, async (route) => {
      await trackedGate;
      return route.fulfill({
        json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] },
      });
    });
    let trackedSettled = false;
    const trackedResponded = page
      .waitForResponse(
        (response) => response.url().includes("tracked/competitors") && response.request().method() === "GET",
      )
      .then((response) => {
        trackedSettled = true;
        return response;
      });

    await page.goto("/map");

    const firstCardCheckbox = page
      .locator(".competitor-card", { hasText: "Hotel Alpha" })
      .locator("input[type=checkbox]");
    await expect(firstCardCheckbox).toBeVisible();
    await expect(firstCardCheckbox).not.toBeChecked();
    await firstCardCheckbox.click();
    await expect(firstCardCheckbox).toBeChecked();

    // toggleCompetitor persists the click to workflow storage
    // (lastCompetitorSelection) keyed by the job that is currently loading.
    // Left alone, that write round-trips through restoreStoredCompetitorSelection
    // once the delayed fetch below resolves and coincidentally reapplies the
    // very selection the user just made -- self-healing THIS specific race
    // regardless of the guard under test (confirmed empirically while
    // developing this scenario) and leaving applyTrackedCompetitors's own
    // overwrite -- the mechanism the "restored competitor search on load"
    // test above already relies on -- never reached. Clearing the
    // just-written entry removes that side channel so the assertions below
    // observe applyTrackedCompetitors's guarded write, exactly like a
    // session where nothing was ever persisted yet.
    await page.evaluate(() => window.localStorage.removeItem("roomrate_last_competitor_selection:e2e-user"));
    // In-window proof, DOM-free: the click+clear landed while the tracked
    // response was still gated — so the chain provably had not settled and
    // the scenario cannot go silently toothless. (A DOM "Loading" check
    // here proved render-order-sensitive under parallel workers.)
    expect(trackedSettled).toBe(false);
    releaseTracked();

    // Wait for the delayed response, then for a happens-after signal that
    // the synchronous restore/apply step and Angular's re-render which
    // follow it have actually run: status() only flips away from "loading"
    // (and the header switches from "Loading" to the count) strictly after
    // applyTrackedCompetitors returns. A bare toBeChecked() right after the
    // click would pass trivially on its very first poll (it is already
    // checked) without ever observing the chain's later overwrite --
    // confirmed empirically: that assertion alone is not sufficient here,
    // and neither is a fixed sleep guess.
    await trackedResponded;
    await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();

    // Red today: applyTrackedCompetitors wipes this click once the delayed,
    // empty tracked-competitors response lands (no guard checks whether the
    // user already touched the selection).
    await expect(firstCardCheckbox).toBeChecked();
    await expect(page.getByRole("button", { name: "Προσθήκη επιλεγμένων στην παρακολούθηση" })).toBeEnabled();
  });

  test("a competitor card clicked mid-chain survives a failed tracked-competitors fetch", async ({ page }) => {
    await mockBackend(page);
    await interceptAlertSocket(page);

    // Same shape as the scenario above, but the delayed response is a
    // failure instead of an empty success. fetchTrackedCompetitors's catch
    // swallows ANY error (network, 4xx, 5xx) and returns null, which routes
    // applyTrackedCompetitors into its early `!tracked` branch -- the one
    // guarded selectedKeys write no other scenario reaches. This is the
    // path a real backend hiccup takes, so it is exactly where a click
    // surviving matters most.
    // Condition-held window, same mechanism as the sibling scenario.
    let releaseTracked!: () => void;
    const trackedGate = new Promise<void>((resolve) => (releaseTracked = resolve));
    await page.route(`${API}/api/v1/tracked/competitors**`, async (route) => {
      await trackedGate;
      return route.fulfill({ status: 500, json: { detail: "tracked competitors unavailable" } });
    });
    let trackedSettled = false;
    const trackedResponded = page
      .waitForResponse(
        (response) => response.url().includes("tracked/competitors") && response.request().method() === "GET",
      )
      .then((response) => {
        trackedSettled = true;
        return response;
      });

    await page.goto("/map");

    const firstCardCheckbox = page
      .locator(".competitor-card", { hasText: "Hotel Alpha" })
      .locator("input[type=checkbox]");
    await expect(firstCardCheckbox).toBeVisible();
    await expect(firstCardCheckbox).not.toBeChecked();
    await firstCardCheckbox.click();
    await expect(firstCardCheckbox).toBeChecked();

    // Same self-heal defeat as the sibling scenario above -- otherwise
    // restoreStoredCompetitorSelection round-trips the click back in
    // regardless of the guard under test.
    await page.evaluate(() => window.localStorage.removeItem("roomrate_last_competitor_selection:e2e-user"));
    // Same DOM-free in-window proof as the sibling scenario.
    expect(trackedSettled).toBe(false);
    releaseTracked();

    await trackedResponded;
    await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();

    // Red without the guard: applyTrackedCompetitors's `!tracked` branch
    // (reached because the fetch failed, not because it returned an empty
    // list) wipes this click. Nothing else in the component reacts to a
    // failed tracked-competitors fetch -- no error signal, no disabled
    // state -- so the button's [disabled] binding depends only on
    // selectedKeys().size, same as the sibling scenario.
    await expect(firstCardCheckbox).toBeChecked();
    await expect(page.getByRole("button", { name: "Προσθήκη επιλεγμένων στην παρακολούθηση" })).toBeEnabled();
  });

  test("Find pressed mid-restore never shows 'the search finished' while the new job runs", async ({
    page,
  }) => {
    // Live, 2026-08-11 ~23:48 (job b3a1c5e8): clicking Find about a second
    // after /map opened -- while the restore chain of the PREVIOUS job was
    // still running -- left the sidebar saying the search had finished and
    // returned nothing for the 2+ minutes the new job actually ran in the
    // backend. findCompetitors had cleared the list and set status "loading",
    // but the OLD chain's queued loadMarkers tail (competitors + status
    // "ready") landed on top of it. Same "two concurrent chains" residual the
    // Fix-B review recorded, in its status flavour.
    await mockBackend(page);
    // This scenario asserts on `status` itself, which a failed map style would
    // clobber — see mockMapbox in helpers.ts.
    await mockMapbox(page);
    await interceptAlertSocket(page);

    // LIFO: mockBackend's `${API}/**` wildcard is registered first, so these
    // specific overrides win. `**` because the real requests carry query
    // strings / path ids.
    // The restore chain is parked ON its own marker read, so the click below
    // provably lands mid-chain. Its payload is EMPTY, which is what the live
    // job's read returned and what makes the residual write render as
    // "search finished, nothing found" instead of stale cards.
    const restoreMarkers = await gateRestoreMarkerRead(page, { json: [] });
    const newJob = await mockRunningSearchJob(page);
    const jobPosted = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("/api/v1/scrape-jobs/"),
    );

    await page.goto("/map");
    await restoreMarkers.requested;

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    await jobPosted;
    // In-window proof, DOM-free (same as the scenarios above): the click and
    // the job POST landed while the restore chain's marker read was still
    // gated, so the race window is real and cannot go silently toothless.
    expect(restoreMarkers.settled()).toBe(false);
    restoreMarkers.release();
    await newJob.letSupersededChainSettle();

    const sidebar = page.locator(".results-sidebar");
    // Red today: the old chain's status.set("ready") + empty list render the
    // finished-search empty state while the new job is still scraping.
    await expect(sidebar).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
    await expect(sidebar).not.toContainText("Η αναζήτηση ολοκληρώθηκε");
    await expect(sidebar).toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.");
    await expect(page.locator(".header-actions")).toContainText("Φόρτωση");
    // Consistency check on the whole sidebar, not a pin: the superseded chain
    // is dropped at its marker-read guard here and never reaches
    // loadMarketSummary, so this holds trivially. The market strip's own
    // residual write is pinned by "a superseded restore's market strip never
    // lands on the new search" below, which gates that read instead.
    // Re-derived for the one summary box (Round 6): «Καταγραφές 42» is what it
    // renders for MARKET_SUMMARY (it was «42 καταγραφές» in the old strip), and
    // the structural check beside it is independent of the fixture's count —
    // the box must not exist at all while the new search has produced nothing.
    await expect(sidebar).not.toContainText("Καταγραφές 42");
    await expect(sidebar.locator(".market-summary-block")).toHaveCount(0);
  });

  test("Find pressed before the restore finds its job keeps the typed dates and message", async ({
    page,
  }) => {
    // The other half of the same race: when the restore's JOB LOOKUP (not its
    // marker read) is what was still in flight, its tail rewrites the date
    // filters through applyRestoredJobFilters and replaces "Searching..." with
    // "Loaded the last completed search" — on a search that is still running,
    // and with dates the user did not ask for.
    const restoredJob = { ...COMPLETED_JOB, check_in: "2030-06-01", check_out: "2030-06-05" };
    // Distinct from the restored job's window in both directions, so a
    // rewrite is unmistakable whatever today's date is.
    const typedStay = { checkIn: "2030-09-11", checkOut: "2030-09-15" };

    await mockBackend(page);
    await mockMapbox(page);
    await interceptAlertSocket(page);

    let releaseLookup!: () => void;
    const lookupGate = new Promise<void>((resolve) => (releaseLookup = resolve));
    const newJob = await mockRunningSearchJob(page, {
      gateRestoreLookup: lookupGate,
      restoredJob,
    });
    const jobPosted = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("/api/v1/scrape-jobs/"),
    );

    await page.goto("/map");
    await newJob.restoreLookupRequested;

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    const checkIn = page.locator('input[type="date"]').first();
    const checkOut = page.locator('input[type="date"]').nth(1);
    await checkIn.fill(typedStay.checkIn);
    await checkOut.fill(typedStay.checkOut);
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    await jobPosted;
    expect(newJob.restoreLookupSettled()).toBe(false);
    releaseLookup();
    await newJob.letSupersededChainSettle();

    // Red without the guard: the dates jump to the restored job's window...
    await expect(checkIn).toHaveValue(typedStay.checkIn);
    await expect(checkOut).toHaveValue(typedStay.checkOut);
    // ...and the banner announces a restored search over the live one.
    const filters = page.locator(".filters-sidebar");
    await expect(filters).toContainText("Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών");
    // Covers both restore banners, as the English "last completed" did:
    // «Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών...» and the
    // stale-dates «Εμφανίζεται η τελευταία ολοκληρωμένη αναζήτησή σας για...».
    // Re-derived for the Greek page (Round 5.1) — the English substring is
    // gone from the app, so left alone this assertion could never fail again.
    // Deliberately stops before the noun: the stale-dates banner spells it
    // «αναζήτησή σας», so a substring ending in «αναζήτηση» would miss it and
    // silently cover only one of the two banners.
    await expect(filters).not.toContainText("τελευταία ολοκληρωμένη");
    await expect(page.locator(".results-sidebar")).toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.");
  });

  test("a superseded restore that fails does not overwrite the running search's message", async ({
    page,
  }) => {
    // A superseded chain whose read FAILS skips every post-await guard and
    // lands in the restore's catch instead, where the "run Find Competitors to
    // start a live scrape" reset would tell the user nothing is happening
    // while their search is mid-flight.
    await mockBackend(page);
    await mockMapbox(page);
    await interceptAlertSocket(page);

    const restoreMarkers = await gateRestoreMarkerRead(page, {
      status: 500,
      json: { detail: "competitor markers unavailable" },
    });
    const newJob = await mockRunningSearchJob(page);
    const jobPosted = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("/api/v1/scrape-jobs/"),
    );

    await page.goto("/map");
    await restoreMarkers.requested;

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    await jobPosted;
    expect(restoreMarkers.settled()).toBe(false);
    restoreMarkers.release();
    await newJob.letSupersededChainSettle();

    const filters = page.locator(".filters-sidebar");
    await expect(filters).toContainText("Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών");
    // Re-derived for the Greek page (Round 5.1): the restore's catch resets
    // the banner to START_SEARCH_MESSAGE, whose opening clause this is. Long
    // enough to be that banner and nothing else — the idle empty-state opens
    // «Ρυθμίστε τα φίλτρα και ξεκινήστε...», so the shorter prefix would be
    // ambiguous the moment either sentence moved sidebars.
    await expect(filters).not.toContainText("Ρυθμίστε τα φίλτρα και πατήστε «Εύρεση ανταγωνιστών»");
    await expect(page.locator(".results-sidebar")).toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.");
  });

  test("a restore that fails on its own leaves the page idle, not stuck loading", async ({
    page,
  }) => {
    // The non-superseded half of the scenario above: nobody pressed Find, so
    // the restore's catch is the ONLY thing left to answer for the screen.
    // loadMarkers had already flipped status to "loading" before the read, and
    // the catch never put it back — the sidebar sat on "Waiting for the live
    // scrape to finish" and the map on "Searching..." for a search that had
    // failed and was not running.
    await mockBackend(page);
    await mockMapbox(page);
    await interceptAlertSocket(page);

    const restoreMarkers = await gateRestoreMarkerRead(page, {
      status: 500,
      json: { detail: "competitor markers unavailable" },
    });

    await page.goto("/map");
    await restoreMarkers.requested;
    restoreMarkers.release();

    const sidebar = page.locator(".results-sidebar");
    await expect(sidebar).toContainText("Δεν έχετε τρέξει ακόμη αναζήτηση");
    // Both re-derived for the Greek page (Round 5.1). They are the two texts a
    // status left stuck on "loading" would still be showing after the restore
    // failed: the sidebar's searching empty-state and the map overlay's
    // «Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών...». Their English
    // originals no longer exist anywhere in the app.
    await expect(sidebar).not.toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.");
    await expect(page.locator(".map-canvas")).not.toContainText("Γίνεται αναζήτηση συγκρίσιμων δωματίων");
    // The way out has to be usable: a Find click is the whole point of the
    // message the catch leaves behind.
    await page.getByRole("button", { name: "Φίλτρα" }).click();
    await expect(page.locator(".filters-sidebar")).toContainText("Ρυθμίστε τα φίλτρα και πατήστε «Εύρεση ανταγωνιστών»");
    await expect(page.getByRole("button", { name: "Εύρεση ανταγωνιστών" })).toBeEnabled();
  });

  test("a superseded restore's market strip never lands on the new search", async ({ page }) => {
    // The market summary is the residual one await LATER than the status one:
    // loadMarkers fires it after flipping to "ready", so a Find click during
    // that read leaves the previous job's record count on screen next to a
    // search that has produced nothing yet.
    await mockBackend(page);
    await mockMapbox(page);
    await interceptAlertSocket(page);

    // The restore returns cards this time (mockBackend's two markers), which
    // is what skips the other-category probe — so /market/summary is read
    // exactly once here, by the chain about to be superseded.
    let releaseSummary!: () => void;
    const summaryGate = new Promise<void>((resolve) => (releaseSummary = resolve));
    let markSummaryRequested!: () => void;
    const summaryRequested = new Promise<void>((resolve) => (markSummaryRequested = resolve));
    let summarySettled = false;
    await page.route(`${API}/api/v1/market/summary**`, async (route) => {
      markSummaryRequested();
      await summaryGate;
      summarySettled = true;
      return route.fulfill({ json: MARKET_SUMMARY });
    });
    const newJob = await mockRunningSearchJob(page);
    const jobPosted = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("/api/v1/scrape-jobs/"),
    );

    await page.goto("/map");
    // The restore has settled everything EXCEPT the gated summary read.
    await expect(page.locator(".header-actions").getByText("2 ανταγωνιστές")).toBeVisible();
    // The route really matched and is parked in it: without this the summary
    // endpoint could drift out from under the pattern, mockBackend's wildcard
    // would answer instantly, and `summarySettled` (which is only ever set
    // INSIDE this handler) would still read false — a green test that never
    // exercised the guard.
    await summaryRequested;

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    await jobPosted;
    // Requested but not answered: the click provably landed mid-read.
    expect(summarySettled).toBe(false);
    releaseSummary();
    await newJob.letSupersededChainSettle();

    const sidebar = page.locator(".results-sidebar");
    // Red without the guard: findCompetitors cleared the strip on purpose and
    // the superseded chain puts the old job's 42 records back.
    // Re-derived for the one summary box (Round 6): the count now reads
    // «Καταγραφές 42», and the block-level check catches the residual write
    // whatever number the summary carries.
    await expect(sidebar).not.toContainText("Καταγραφές 42");
    await expect(sidebar.locator(".market-summary-block")).toHaveCount(0);
    await expect(sidebar).toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.");
  });

  test("Find pressed while the page is still starting up is not undone by startup", async ({
    page,
  }) => {
    // ngOnInit's tail is a chain of its own: it runs after two awaits, and the
    // Find button is live throughout (property, rooms and dates are all in
    // place once loadCurrentUser resolved). Pressing Find in that window used
    // to be undone by resetCompetitorSearchState — which also re-enables the
    // button, inviting a second, paid Apify job for a search already running —
    // and then repainted by the restore that follows it.
    await mockBackend(page);
    await mockMapbox(page);
    await interceptAlertSocket(page);

    let releaseAmenities!: () => void;
    const amenitiesGate = new Promise<void>((resolve) => (releaseAmenities = resolve));
    let markAmenitiesRequested!: () => void;
    const amenitiesRequested = new Promise<void>((resolve) => (markAmenitiesRequested = resolve));
    let amenitiesSettled = false;
    let amenityReads = 0;
    await page.route(`${API}/api/v1/market/amenities**`, async (route) => {
      amenityReads += 1;
      if (amenityReads > 1) {
        return route.fulfill({ json: ["Free WiFi | Swimming pool | Breakfast"] });
      }
      markAmenitiesRequested();
      await amenitiesGate;
      amenitiesSettled = true;
      return route.fulfill({ json: ["Free WiFi | Swimming pool | Breakfast"] });
    });
    const newJob = await mockRunningSearchJob(page);
    const jobPosted = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("/api/v1/scrape-jobs/"),
    );

    await page.goto("/map");
    await amenitiesRequested;

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
    await jobPosted;
    expect(amenitiesSettled).toBe(false);
    releaseAmenities();
    await newJob.letSupersededChainSettle();

    const filters = page.locator(".filters-sidebar");
    await expect(filters).toContainText("Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών");
    // Covers both restore banners, as the English "last completed" did:
    // «Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών...» and the
    // stale-dates «Εμφανίζεται η τελευταία ολοκληρωμένη αναζήτησή σας για...».
    // Re-derived for the Greek page (Round 5.1) — the English substring is
    // gone from the app, so left alone this assertion could never fail again.
    // Deliberately stops before the noun: the stale-dates banner spells it
    // «αναζήτησή σας», so a substring ending in «αναζήτηση» would miss it and
    // silently cover only one of the two banners.
    await expect(filters).not.toContainText("τελευταία ολοκληρωμένη");
    // The restored job's cards must not appear over a search still running.
    await expect(page.getByText("Hotel Alpha")).toHaveCount(0);
    await expect(page.locator(".results-sidebar")).toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.");
    // And the button stays disabled — a reset that re-enables it is how a
    // second paid job gets started for a search already in flight.
    await expect(page.getByRole("button", { name: "Γίνεται αναζήτηση" })).toBeDisabled();
  });

  test("a new search reads its own job with its own dates, not the restored job's", async ({
    page,
  }) => {
    // Live, 2026-08-16 (job 6510897e): restore a job for 10->14/9, change the
    // dates, run a new search -- and the NEW job's rows were read with the OLD
    // job's dates. The job-scoped SQL matches `rates.check_in = :check_in`, so
    // a scrape that wrote 8 rows rendered as an empty map. `restoredStay` was
    // written once, on restore, and never cleared, while buildMarkerParams
    // preferred it for EVERY job-scoped read.
    const restoredStay = { check_in: "2030-06-01", check_out: "2030-06-05" };
    const newStay = { check_in: "2030-09-11", check_out: "2030-09-15" };
    // Deliberately not the app's defaults (2 adults / 0 children / 1 room /
    // limit 8): a read that quietly fell back to them would still look right.
    const restoredJob = {
      ...COMPLETED_JOB,
      ...restoredStay,
      adults: 3,
      children: 1,
      rooms: 2,
      filters_payload: { limit: 12 },
    };
    // A third window, typed WHILE the job runs (the inputs stay editable
    // during a search): the read that follows must still carry the job's own
    // stay, not whatever the inputs hold by the time it happens.
    const lateTypedStay = { check_in: "2030-12-01", check_out: "2030-12-05" };
    const newJob = { ...restoredJob, id: NEW_JOB_ID, ...newStay };
    const restoredMarkers = [MAP_MARKERS[0]];
    const newMarkers = [{ ...MAP_MARKERS[1], hotel_name: "Hotel Gamma" }];

    await mockBackend(page);
    await interceptAlertSocket(page);

    // LIFO: registered after mockBackend's wildcard, so these win.
    let newJobFinished = false;
    let newJobPolls = 0;
    await page.route(`${API}/api/v1/scrape-jobs/**`, async (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "POST") {
        return route.fulfill({ json: { ...newJob, status: "queued", finished_at: null } });
      }
      if (url.pathname.endsWith(NEW_JOB_ID)) {
        newJobPolls += 1;
        return route.fulfill({
          json: newJobFinished ? newJob : { ...newJob, status: "running", finished_at: null },
        });
      }
      return route.fulfill({
        json: url.pathname.endsWith("/scrape-jobs/") ? [restoredJob] : restoredJob,
      });
    });

    const markerQueries: URLSearchParams[] = [];
    await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
      const query = new URL(route.request().url()).searchParams;
      markerQueries.push(query);
      const job = query.get("scrape_job_id") === NEW_JOB_ID ? newJob : restoredJob;
      // The job-scoped read is date-filtered in SQL (`rates.check_in =
      // :check_in`), so rows scraped for one stay are invisible to a read
      // carrying another. Modelling that here is what makes the wrong-date
      // read show up as the live symptom -- an empty map -- and not just as a
      // recorded query string.
      const readsTheJobsStay =
        query.get("check_in") === job.check_in && query.get("check_out") === job.check_out;
      if (!readsTheJobsStay) {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({ json: job.id === NEW_JOB_ID ? newMarkers : restoredMarkers });
    });

    await page.goto("/map");
    await expect(page.getByText("Hotel Alpha")).toBeVisible();

    await page.getByRole("button", { name: "Φίλτρα" }).click();
    const checkIn = page.locator('input[type="date"]').first();
    const checkOut = page.locator('input[type="date"]').nth(1);
    await checkIn.fill(newStay.check_in);
    await checkOut.fill(newStay.check_out);
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();

    // Mid-search: the job is provably running (it has been polled), and the
    // user moves the dates on. Only THEN does the job complete, so the read
    // below happens with the inputs holding a stay the job knows nothing about.
    await expect.poll(() => newJobPolls).toBeGreaterThan(0);
    await checkIn.fill(lateTypedStay.check_in);
    await checkOut.fill(lateTypedStay.check_out);
    newJobFinished = true;

    // Red today: the new job is read with 2030-06-01, matches no rows, and the
    // user sees an empty map for a scrape that did return data.
    await expect(page.getByText("Hotel Gamma")).toBeVisible();

    const restoredReads = markerQueries.filter((query) => query.get("scrape_job_id") === JOB_ID);
    const newReads = markerQueries.filter((query) => query.get("scrape_job_id") === NEW_JOB_ID);
    expect(newReads.length).toBeGreaterThan(0);
    // The job's own stay — neither the restored job's (2030-06) nor the one
    // the inputs held when the read went out (2030-12).
    expect([newReads[0].get("check_in"), newReads[0].get("check_out")]).toEqual([
      newStay.check_in,
      newStay.check_out,
    ]);
    // ...while the restored job keeps being read with ITS stay, which is what
    // the date-preferring branch was added for in the first place.
    expect(restoredReads.length).toBeGreaterThan(0);
    expect([restoredReads[0].get("check_in"), restoredReads[0].get("check_out")]).toEqual([
      restoredStay.check_in,
      restoredStay.check_out,
    ]);
  });

  test("a facility ticked while a search runs lands on that search's own read", async ({ page }) => {
    // A tick is "re-read the job on screen with the filter as it now stands",
    // and mid-search there is no such job: findCompetitors cleared the job id,
    // so reloadActiveJobForAmenities finds nothing and starts nothing — the
    // running search is not superseded by a click on a checkbox. What carries
    // the filter is the search's OWN read, which builds its params when the
    // job completes, and that is the half that used to be dropped the moment a
    // read was job-scoped: every read after a search is.
    const newJob = { ...COMPLETED_JOB, id: NEW_JOB_ID };
    const newMarkers = [{ ...MAP_MARKERS[1], hotel_name: "Hotel Gamma" }];
    let newJobFinished = false;
    let newJobPolls = 0;

    await mockBackend(page);
    await mockMapbox(page);
    await interceptAlertSocket(page);

    // LIFO: registered after mockBackend's wildcard, so these win.
    await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "POST") {
        return route.fulfill({ json: { ...newJob, status: "queued", finished_at: null } });
      }
      if (url.pathname.endsWith(NEW_JOB_ID)) {
        newJobPolls += 1;
        return route.fulfill({
          json: newJobFinished ? newJob : { ...newJob, status: "running", finished_at: null },
        });
      }
      // The restored job's own reads stay on mockBackend's wildcard.
      return route.fallback();
    });
    const markerQueries: URLSearchParams[] = [];
    await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
      const query = new URL(route.request().url()).searchParams;
      markerQueries.push(query);
      return route.fulfill({ json: query.get("scrape_job_id") === NEW_JOB_ID ? newMarkers : MAP_MARKERS });
    });

    // Taller than the default 720: the facility list is the last thing in the
    // filters sidebar, and at 720 it sits outside that sidebar's scroll clip,
    // where a real click is swallowed.
    await page.setViewportSize({ width: 1280, height: 1400 });
    await page.goto("/map");
    await expect(page.getByText("Hotel Alpha")).toBeVisible();
    await page.getByRole("button", { name: "Φίλτρα" }).click();
    await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();

    // Provably mid-search — the job has been polled at least once — and only
    // after the tick is it allowed to finish.
    await expect.poll(() => newJobPolls).toBeGreaterThan(0);
    await page.locator(".amenity-list .checkbox-row", { hasText: "Δωρεάν WiFi" })
      .locator("input[type=checkbox]").check();
    newJobFinished = true;

    // The search paints its own rows, ticked checkbox and all.
    await expect(page.getByText("Hotel Gamma")).toBeVisible();
    const newJobReads = markerQueries.filter((query) => query.get("scrape_job_id") === NEW_JOB_ID);
    expect(newJobReads.length).toBeGreaterThan(0);
    expect(newJobReads[0].getAll("amenities")).toEqual(["Free WiFi"]);
    // ...and the tick fired no read of its own: mid-search there was no job to
    // re-read, so every filtered read belongs to the search itself.
    const filteredReads = markerQueries.filter((query) => query.getAll("amenities").length > 0);
    expect(filteredReads.every((query) => query.get("scrape_job_id") === NEW_JOB_ID)).toBe(true);
  });

  test("pricing page loads market history on load and a recommendation after one click", async ({
    page,
  }) => {
    await mockBackend(page);
    await interceptAlertSocket(page);

    await page.goto("/pricing");

    // Price history renders from ngOnInit's fetch chain — no clicks.
    // PRICE_HISTORY holds two runs, too few for a trend: the low-data panel
    // stands in for the chart and says what would show one.
    await expect(
      page.getByText("Χρειάζονται αναζητήσεις σε διαφορετικές ημέρες για να φανεί τάση."),
    ).toBeVisible();

    // Exactly ONE click; the recommendation must render from the async
    // completion without any further interaction.
    await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
    await expect(page.locator(".recommendation-card").getByText("Προτεινόμενη τιμή ανά βράδυ")).toBeVisible();
    // el-GR currency formatting: "112 €".
    await expect(page.getByText("112 €")).toBeVisible();
    await expect(page.getByText("Μέτρια βεβαιότητα")).toBeVisible();
    await expect(page.getByText("Διάμεση τιμή αγοράς", { exact: true })).toBeVisible();
  });

  test("notification bell badge shows the unread count on load", async ({ page }) => {
    await mockBackend(page, { unreadCount: 3 });
    await interceptAlertSocket(page);

    await page.goto("/pricing");

    // The badge is populated by an awaited REST call during startup — it must
    // appear without any interaction.
    await expect(page.locator(".bell-badge")).toHaveText("3");
  });

  test("a live WebSocket alert shows a toast and bumps the badge without interaction", async ({
    page,
  }) => {
    await mockBackend(page, { unreadCount: 1 });
    const { connection } = await interceptAlertSocket(page);

    await page.goto("/pricing");
    await expect(page.locator(".bell-badge")).toHaveText("1");

    const socket = await connection;
    socket.send(
      JSON.stringify({
        account_id: ACCOUNT_ID,
        notification: {
          id: "55555555-5555-5555-5555-555555555501",
          notification_type: "price_change",
          title: "Price drop: Hotel Gamma 20.0%",
          message: "Hotel Gamma moved from €100.00 to €80.00 (-20.0%) for faliraki.",
          payload: null,
          is_read: false,
          created_at: "2026-07-23T10:00:00Z",
        },
      }),
    );

    // Toast + badge update arrive over the socket — zero interactions.
    await expect(page.getByText("Price drop: Hotel Gamma 20.0%")).toBeVisible();
    await expect(page.locator(".bell-badge")).toHaveText("2");
  });

  test("opening the bell panel lists notifications after exactly one click", async ({ page }) => {
    await mockBackend(page, { unreadCount: 2 });
    await interceptAlertSocket(page);

    await page.goto("/pricing");
    await expect(page.locator(".bell-badge")).toHaveText("2");

    await page.locator(".bell-button").click();

    // The list is fetched on open; it must render without a second click.
    await expect(page.getByText("Price drop: Hotel Alpha 12.5%")).toBeVisible();
    await expect(page.getByText("Price rise: Hotel Beta 15.0%")).toBeVisible();
  });
});
