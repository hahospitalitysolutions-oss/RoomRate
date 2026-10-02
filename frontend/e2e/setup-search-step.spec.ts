import { expect, Page, Route, test } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  JOB_ID,
  OWNED_PROPERTY_ID,
  RecordedCall,
  recordCall,
  seedSessionOnly,
} from "./helpers";

const SEARCH_JOB_ID = "55555555-5555-5555-5555-555555555555";
const OLD_SEARCH_JOB_ID = "44444444-4444-4444-4444-444444444444";
const COLLECTION_PATH = "/api/v1/scrape-jobs/";
const JOB_PATH = `${COLLECTION_PATH}${SEARCH_JOB_ID}`;
const SEARCH_ERROR = "Δεν μπορέσαμε να ξεκινήσουμε την αναζήτηση. Δοκιμάστε ξανά.";
const RECONCILE_ERROR = "Δεν μπορέσαμε να επιβεβαιώσουμε αν η αναζήτηση ξεκίνησε. Δοκιμάστε ξανά.";
const PREPARATION_ERROR = "Δεν μπορέσαμε να προετοιμάσουμε με ασφάλεια την αναζήτηση. Δοκιμάστε ξανά.";
const VALIDATION_ERROR = "Τα στοιχεία της αναζήτησης δεν είναι έγκυρα. Επαναφορτώστε τα στοιχεία πριν δοκιμάσετε ξανά.";
const QUOTA_ERROR = "Έχετε φτάσει το όριο αναζητήσεων. Δοκιμάστε ξανά αργότερα.";
const POLL_ERROR = "Δεν μπορέσαμε να ελέγξουμε την αναζήτηση. Δοκιμάστε ξανά.";
const TIMEOUT_ERROR = "Η αναζήτηση διαρκεί ασυνήθιστα πολύ. Μπορείτε να συνεχίσετε στον χάρτη και να επιστρέψετε αργότερα.";

type HeldRequest = { route: Route; release: () => void };

async function fulfillHeld(
  held: HeldRequest,
  response: Parameters<Route["fulfill"]>[0],
): Promise<void> {
  await held.route.fulfill(response);
  held.release();
}

type ReachSearchOptions = {
  authoritative?: { category: string; destination: string; rawDestination: string };
  preparationFailures?: number;
  waitForReady?: boolean;
};

async function reachSearchStep(
  page: Page,
  calls: RecordedCall[],
  options: ReachSearchOptions = {},
): Promise<void> {
  let roomSelected = false;
  let remainingPreparationFailures = options.preparationFailures ?? 0;
  const authoritative = options.authoritative ?? {
    category: "suite",
    destination: "Lindos",
    rawDestination: "Lindos, Rhodes",
  };
  await seedSessionOnly(page);
  await page.addInitScript(
    ({ candidate, discoveryJobId, ownedPropertyId }) => {
      window.localStorage.setItem("roomrate_pending_candidate:e2e-user", JSON.stringify(candidate));
      window.localStorage.setItem("roomrate_pending_discovery_job_id:e2e-user", discoveryJobId);
      window.localStorage.setItem("roomrate_owned_property_id:e2e-user", ownedPropertyId);
      // Deliberately non-default values: exact forwarding must not accidentally
      // fall back to "double" or reuse the candidate's city as raw destination.
      window.localStorage.setItem("roomrate_property_city:e2e-user", "Lindos");
      window.localStorage.setItem("roomrate_property_raw_destination:e2e-user", "Lindos, Rhodes");
      window.localStorage.setItem("roomrate_selected_room_type_category:e2e-user", "suite");
    },
    {
      candidate: {
        candidate_key: "search-step-hotel",
        display_name: "Search Step Hotel",
        booking_url: "https://www.booking.com/hotel/gr/search-step.html",
        city: "Lindos",
        address: "Odos 7",
        latitude: 36.09,
        longitude: 28.08,
      },
      discoveryJobId: JOB_ID,
      ownedPropertyId: OWNED_PROPERTY_ID,
    },
  );

  // Wildcard FIRST; every endpoint-specific recording fake below wins.
  await page.route(`${API}/api/v1/**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    if (roomSelected && remainingPreparationFailures > 0) {
      remainingPreparationFailures -= 1;
      return route.fulfill({ status: 503, json: { detail: "Raw /me preparation failure" } });
    }
    return route.fulfill({
      json: {
        ...CURRENT_USER,
        onboarding_complete: roomSelected,
        property_name: "Search Step Hotel",
        destination: roomSelected ? authoritative.destination : "Lindos",
        raw_destination: roomSelected ? authoritative.rawDestination : "Lindos, Rhodes",
        selected_room_type_category: roomSelected ? authoritative.category : null,
      },
    });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "completed" } });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: [{
        id: "33333333-3333-3333-3333-333333333334",
        owned_property_id: OWNED_PROPERTY_ID,
        room_type: "Family Suite",
        room_type_category: "suite",
        sample_price_per_night_eur: 187,
        is_active: true,
      }],
    });
  });
  await page.route(
    `${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`,
    (route) => {
      recordCall(calls, route);
      roomSelected = true;
      return route.fulfill({
        json: {
          owned_property_id: OWNED_PROPERTY_ID,
          selected_room_type_category: "suite",
          onboarding_complete: true,
        },
      });
    },
  );

  await page.goto("/setup");
  await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();
  await page.getByTestId("room-suite").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  if (options.waitForReady !== false) {
    await expect(page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" })).toBeVisible();
  }
}

function collectionCalls(calls: RecordedCall[], method: string): RecordedCall[] {
  return calls.filter((call) => call.path === COLLECTION_PATH && call.method === method);
}

function searchJob(
  body: Record<string, unknown>,
  status = "queued",
  id = SEARCH_JOB_ID,
): Record<string, unknown> {
  return {
    id,
    account_id: CURRENT_USER.account_id,
    job_type: "competitor_search",
    status,
    requested_at: "2026-08-03T12:00:00Z",
    attempt_count: 0,
    max_attempts: 3,
    scrape_runs_count: status === "completed" ? 1 : 0,
    ...body,
  };
}

test("authoritative /me overrides stale workflow in both summary and exact POST", async ({ page }) => {
  const calls: RecordedCall[] = [];
  let body: Record<string, unknown> = {};
  await reachSearchStep(page, calls, {
    authoritative: {
      category: "double",
      destination: "Rhodes",
      rawDestination: "Rhodes, Greece",
    },
  });
  await expect(page.getByTestId("search-destination")).toHaveText("Rhodes");
  await expect(page.getByTestId("search-room-category")).toHaveText("double");
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    body = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ status: 202, json: searchJob(body) });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob(body, "completed") });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect(page).toHaveURL(/\/map/);
  expect(collectionCalls(calls, "POST")[0].body).toMatchObject({
    room_type_category: "double",
    destination: "Rhodes",
    raw_destination: "Rhodes, Greece",
  });
});

test("a /me preparation blip blocks POST until retry and a separate explicit start", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await reachSearchStep(page, calls, { preparationFailures: 1, waitForReady: false });

  await expect(page.getByText(PREPARATION_ERROR)).toBeVisible();
  await expect(page.getByText("Raw /me preparation failure")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" })).toHaveCount(0);
  expect(collectionCalls(calls, "POST")).toHaveLength(0);
  await page.getByRole("button", { name: "Επαναφόρτωση στοιχείων" }).click();
  await expect(page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" })).toBeVisible();
  expect(collectionCalls(calls, "POST")).toHaveLength(0);
});

test("explicit start forwards the immutable non-default request and is single-flight", async ({ page }) => {
  const calls: RecordedCall[] = [];
  let heldPost: HeldRequest | null = null;
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      return route.fulfill({ json: [] });
    }
    return new Promise<void>((resolve) => { heldPost = { route, release: resolve }; });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob({}, "completed") });
  });

  const checkIn = (await page.getByTestId("search-check-in").textContent())!.trim();
  const checkOut = (await page.getByTestId("search-check-out").textContent())!.trim();

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => collectionCalls(calls, "POST").length).toBe(1);
  await expect(page.getByTestId("search-progress")).toBeVisible();

  const body = collectionCalls(calls, "POST")[0].body as Record<string, unknown>;
  expect(body).toEqual({
    owned_property_id: OWNED_PROPERTY_ID,
    job_type: "competitor_search",
    room_type_category: "suite",
    destination: "Lindos",
    raw_destination: "Lindos, Rhodes",
    check_in: checkIn,
    check_out: checkOut,
    adults: 2,
    children: 0,
    rooms: 1,
    filters_payload: { limit: 8 },
  });
  expect(checkIn).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  expect(checkOut).toMatch(/^\d{4}-\d{2}-\d{2}$/);

  await fulfillHeld(heldPost!, { status: 202, json: searchJob(body) });
  await expect(page).toHaveURL(/\/map/);
  expect(collectionCalls(calls, "POST")).toHaveLength(1);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_last_competitor_job_id:e2e-user"),
  )).toBe(SEARCH_JOB_ID);
});

test("retry reconciles a committed job after its POST response is lost", async ({ page }) => {
  const calls: RecordedCall[] = [];
  let listReads = 0;
  let intendedBody: Record<string, unknown> = {};
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      listReads += 1;
      const oldJob = searchJob(intendedBody, "completed", OLD_SEARCH_JOB_ID);
      return route.fulfill({
        json: listReads === 1 ? [oldJob] : [oldJob, searchJob(intendedBody)],
      });
    }
    intendedBody = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ status: 500, json: { detail: "Provider response was lost" } });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob(intendedBody, "completed") });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect(page.getByText(SEARCH_ERROR)).toBeVisible();
  await expect(page.getByText("Provider response was lost")).toHaveCount(0);
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();

  await expect(page).toHaveURL(/\/map/);
  expect(collectionCalls(calls, "POST")).toHaveLength(1);
  expect(collectionCalls(calls, "GET")).toHaveLength(2);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_last_competitor_job_id:e2e-user"),
  )).toBe(SEARCH_JOB_ID);
});

test("authoritative no-match repeats the exact intent instead of adopting a near match", async ({ page }) => {
  const calls: RecordedCall[] = [];
  let listReads = 0;
  let postWrites = 0;
  let intendedBody: Record<string, unknown> = {};
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      listReads += 1;
      return route.fulfill({
        json: listReads === 1
          ? []
          : [searchJob({ ...intendedBody, adults: 9 }, "queued", OLD_SEARCH_JOB_ID)],
      });
    }
    postWrites += 1;
    intendedBody = route.request().postDataJSON() as Record<string, unknown>;
    return postWrites === 1
      ? route.fulfill({ status: 500, json: { detail: "Lost first response" } })
      : route.fulfill({ status: 202, json: searchJob(intendedBody) });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob(intendedBody, "completed") });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect(page.getByText(SEARCH_ERROR)).toBeVisible();
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();

  await expect(page).toHaveURL(/\/map/);
  const posts = collectionCalls(calls, "POST");
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toEqual(posts[0].body);
});

test("429 after a reconciliation no-match re-lists and adopts the late original job", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const originalJobId = "77777777-7777-7777-7777-777777777777";
  let listReads = 0;
  let postWrites = 0;
  let intendedBody: Record<string, unknown> = {};
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      listReads += 1;
      return route.fulfill({
        json: listReads < 3 ? [] : [searchJob(intendedBody, "queued", originalJobId)],
      });
    }
    postWrites += 1;
    intendedBody = route.request().postDataJSON() as Record<string, unknown>;
    if (postWrites === 1) {
      return route.fulfill({ status: 500, json: { detail: "Raw original response lost" } });
    }
    return route.fulfill({
      status: postWrites === 2 ? 429 : 500,
      json: { detail: "Raw 429 after reconciliation" },
    });
  });
  await page.route(`${API}${COLLECTION_PATH}${originalJobId}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob(intendedBody, "completed", originalJobId) });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect(page.getByText(SEARCH_ERROR)).toBeVisible();
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();
  await expect(page.getByText(QUOTA_ERROR)).toBeVisible();
  await expect(page.getByText("Raw 429 after reconciliation")).toHaveCount(0);
  await page.getByRole("button", { name: "Δοκιμάστε ξανά αργότερα" }).click();

  await expect(page).toHaveURL(/\/map/);
  expect(collectionCalls(calls, "GET")).toHaveLength(3);
  expect(collectionCalls(calls, "POST")).toHaveLength(2);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_last_competitor_job_id:e2e-user"),
  )).toBe(originalJobId);
});

test("failed authoritative reconciliation blocks a duplicate-risk POST", async ({ page }) => {
  const calls: RecordedCall[] = [];
  let listReads = 0;
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      listReads += 1;
      return listReads === 1
        ? route.fulfill({ json: [] })
        : route.fulfill({ status: 503, json: { detail: "Raw list failure" } });
    }
    return route.fulfill({ status: 500, json: { detail: "Raw POST failure" } });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect(page.getByText(SEARCH_ERROR)).toBeVisible();
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();

  await expect(page.getByText(RECONCILE_ERROR)).toBeVisible();
  await expect(page.getByText("Raw list failure")).toHaveCount(0);
  await expect(page.getByText("Raw POST failure")).toHaveCount(0);
  expect(collectionCalls(calls, "POST")).toHaveLength(1);
});

for (const deterministic of [
  { status: 422, copy: VALIDATION_ERROR, action: "Επαναφόρτωση στοιχείων" },
  { status: 429, copy: QUOTA_ERROR, action: "Δοκιμάστε ξανά αργότερα" },
] as const) {
  test(`${deterministic.status} is deterministic and never enters reconciliation`, async ({ page }) => {
    const calls: RecordedCall[] = [];
    let postWrites = 0;
    let body: Record<string, unknown> = {};
    await reachSearchStep(page, calls);
    await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
      recordCall(calls, route);
      if (route.request().method() === "GET") return route.fulfill({ json: [] });
      postWrites += 1;
      body = route.request().postDataJSON() as Record<string, unknown>;
      if (postWrites === 1) {
        return route.fulfill({
          status: deterministic.status,
          json: { detail: `Raw ${deterministic.status} provider detail` },
        });
      }
      return route.fulfill({ status: 202, json: searchJob(body) });
    });
    await page.route(`${API}${JOB_PATH}`, (route) => {
      recordCall(calls, route);
      return route.fulfill({ json: searchJob(body, "completed") });
    });

    await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
    await expect(page.getByText(deterministic.copy)).toBeVisible();
    await expect(page.getByText(`Raw ${deterministic.status} provider detail`)).toHaveCount(0);
    expect(collectionCalls(calls, "GET")).toHaveLength(1);
    expect(collectionCalls(calls, "POST")).toHaveLength(1);
    await page.getByRole("button", { name: deterministic.action }).click();

    if (deterministic.status === 422) {
      await expect(page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" })).toBeVisible();
      expect(collectionCalls(calls, "GET")).toHaveLength(1);
      expect(collectionCalls(calls, "POST")).toHaveLength(1);
    } else {
      await expect(page).toHaveURL(/\/map/);
      expect(collectionCalls(calls, "GET")).toHaveLength(1);
      expect(collectionCalls(calls, "POST")).toHaveLength(2);
    }
  });
}

test("a failed active job retry clears it and starts exactly one new job", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const secondJobId = "66666666-6666-6666-6666-666666666666";
  let postWrites = 0;
  let postBody: Record<string, unknown> = {};
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    postWrites += 1;
    postBody = route.request().postDataJSON() as Record<string, unknown>;
    const id = postWrites === 1 ? SEARCH_JOB_ID : secondJobId;
    return route.fulfill({ status: 202, json: searchJob(postBody, "queued", id) });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: { ...searchJob(postBody, "failed"), error_message: "Raw terminal provider failure" },
    });
  });
  await page.route(`${API}${COLLECTION_PATH}${secondJobId}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob(postBody, "completed", secondJobId) });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect(page.getByText(POLL_ERROR)).toBeVisible();
  await expect(page.getByText("Raw terminal provider failure")).toHaveCount(0);
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();

  await expect(page).toHaveURL(/\/map/);
  expect(collectionCalls(calls, "POST")).toHaveLength(2);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_last_competitor_job_id:e2e-user"),
  )).toBe(secondJobId);
});

for (const failure of ["failed", "cancelled", "mystery", "rejected"] as const) {
  test(`poll ${failure} maps to stable Greek guidance without provider detail`, async ({ page }) => {
    const calls: RecordedCall[] = [];
    let body: Record<string, unknown> = {};
    await reachSearchStep(page, calls);
    await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
      recordCall(calls, route);
      if (route.request().method() === "GET") return route.fulfill({ json: [] });
      body = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({ status: 202, json: searchJob(body) });
    });
    await page.route(`${API}${JOB_PATH}`, (route) => {
      recordCall(calls, route);
      if (failure === "rejected") {
        return route.fulfill({ status: 503, json: { detail: "Raw poll GET failure" } });
      }
      return route.fulfill({
        json: { ...searchJob(body, failure), error_message: "Raw durable provider failure" },
      });
    });

    await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
    await expect(page.getByText(POLL_ERROR)).toBeVisible();
    await expect(page.getByText(/Raw (poll GET|durable provider) failure/)).toHaveCount(0);
  });
}

test("a held search poll stops at the real five-minute deadline", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await page.clock.install();
  let heldPoll: HeldRequest | null = null;
  let postBody: Record<string, unknown> = {};
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      return route.fulfill({ json: [] });
    }
    postBody = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ status: 202, json: searchJob(postBody) });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return new Promise<void>((resolve) => { heldPoll = { route, release: resolve }; });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect.poll(() => calls.filter((call) => call.path === JOB_PATH).length).toBe(1);
  await page.clock.fastForward(300_000);
  await expect(page.getByText(TIMEOUT_ERROR)).toBeVisible();

  await fulfillHeld(heldPoll!, { json: searchJob(postBody, "completed") });
  await expect(page).toHaveURL(/\/setup/);
  expect(calls.filter((call) => call.path === JOB_PATH)).toHaveLength(1);
});

test("destroy during a poll delay prevents another read or late navigation", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await page.clock.install();
  let postBody: Record<string, unknown> = {};
  await reachSearchStep(page, calls);
  await page.route(`${API}${COLLECTION_PATH}**`, (route) => {
    recordCall(calls, route);
    if (route.request().method() === "GET") {
      return route.fulfill({ json: [] });
    }
    postBody = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ status: 202, json: searchJob(postBody) });
  });
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: searchJob(postBody, "queued") });
  });

  await page.getByRole("button", { name: "Ξεκίνα την αναζήτηση" }).click();
  await expect.poll(() => calls.filter((call) => call.path === JOB_PATH).length).toBe(1);
  await page.evaluate(() => {
    window.history.pushState({}, "", "/auth");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page).toHaveURL(/\/auth/);
  await page.clock.fastForward(10_000);
  expect(calls.filter((call) => call.path === JOB_PATH)).toHaveLength(1);
  await expect(page).toHaveURL(/\/auth/);
});
