/**
 * Spec §9 scenarios for the /setup wizard. Backend fully mocked; the only
 * moving part is the Angular app. Each test names the spec item it pins.
 */
import { expect, Page, test } from "@playwright/test";

import {
  ACCOUNT_ID,
  API,
  CURRENT_USER,
  JOB_ID,
  OWNED_PROPERTY_ID,
  RecordedCall,
  recordCall,
  ROOM_TYPE_ID,
  seedSessionOnly,
} from "./helpers";

/** A brand-new account: signed in, but nothing onboarded yet. */
const FRESH_USER = {
  ...CURRENT_USER,
  onboarding_complete: false,
  owned_property_id: null,
  property_name: null,
  destination: null,
  raw_destination: null,
  canonical_destination: null,
  selected_room_type_category: null,
};

async function mockMe(page: Page, user: unknown): Promise<void> {
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: user }));
}

type Box = { x: number; y: number; width: number; height: number };

/** True iff two boxes overlap by more than touching edges (0px touching is allowed). */
function boxesIntersect(a: Box, b: Box): boolean {
  const aRight = a.x + a.width;
  const aBottom = a.y + a.height;
  const bRight = b.x + b.width;
  const bBottom = b.y + b.height;
  return !(aBottom <= b.y || bBottom <= a.y || aRight <= b.x || bRight <= a.x);
}

test("scenario 1: new user without property is redirected to /setup step 1", async ({ page }) => {
  await seedSessionOnly(page);
  await mockMe(page, FRESH_USER);

  await page.goto("/map");

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
});

test("scenario 1b: property present but no room selected lands on step 2", async ({ page }) => {
  await seedSessionOnly(page);
  await mockMe(page, { ...FRESH_USER, owned_property_id: OWNED_PROPERTY_ID, property_name: "E2E Test Hotel" });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => route.fulfill({ json: [] }));

  await page.goto("/map");

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
});

test("scenario 6: fully onboarded user opening /setup is sent to the map", async ({ page }) => {
  await seedSessionOnly(page);
  // The map behind the redirect loads its own data; give the calls something
  // harmless so the URL assertion is not racing failed fetches. Registered
  // BEFORE the /me mock: Playwright matches routes last-registered-first, so
  // the specific mock must come after the wildcard to win.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, CURRENT_USER);

  await page.goto("/setup");

  await expect(page).toHaveURL(/\/map/);
});

const PROPOSED_CANDIDATE = {
  candidate_key: "rea-hotel",
  display_name: "Rea Hotel",
  booking_url: "https://www.booking.com/hotel/gr/rea.html",
  city: "Faliraki",
  address: "Leoforos Kallitheas 12",
  country: "Greece",
  property_type: "hotel",
  latitude: 36.34,
  longitude: 28.2,
  stars: 3,
  review_score: 8.7,
  review_count: 214,
};

/**
 * Drive the real sign-up flow through the UI: mock supabase's signup
 * endpoint, then fill and submit the real form. Shared by the success and
 * auto-setup-failure scenarios below, which differ only in how they mock
 * the RoomRate backend's auto-setup response.
 */
async function driveSignUp(page: Page): Promise<void> {
  // supabase-js appends `?redirect_to=...` to the signup URL; a plain glob
  // string here (as in the plan's sketch) does not match past the query
  // string and lets the request escape to the real Supabase project, which
  // then rejects the *.test email domain with a 400. A regex has no such
  // anchor and matches regardless of query params.
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/signup/, (route) =>
    route.fulfill({
      json: {
        access_token: "e2e-fake-access-token",
        refresh_token: "e2e-fake-refresh-token",
        token_type: "bearer",
        expires_in: 3600,
        user: { id: "e2e-user", aud: "authenticated", role: "authenticated", email: "e2e@roomrate.test" },
      },
    }),
  );
  await page.goto("/auth");
  await page.getByRole("button", { name: /δημιουργία νέου λογαριασμού/i }).click();
  await page.getByLabel(/όνομα καταλύματος/i).fill("Rea Hotel");
  await page.getByLabel(/^τοποθεσία$/i).fill("Faliraki");
  await page.getByLabel(/email/i).fill("e2e@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-1");
  await page.getByRole("button", { name: /^δημιουργία λογαριασμού$/i }).click();
}

test("sign-up runs auto-setup, then lands on /setup step 1 without polling", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedSessionOnly(page);
  await mockMe(page, FRESH_USER);
  await page.route(`${API}/api/v1/onboarding/auto-setup`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      status: 202,
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        selected_candidate: PROPOSED_CANDIDATE,
        discovery_job: { id: JOB_ID, status: "queued", job_type: "owned_property_room_discovery" },
      },
    });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "queued" } });
  });

  await driveSignUp(page);

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  // Kills Task 4's surviving mutant: an auth-page that never stored the
  // proposal renders step 1's fallback form, not this card.
  await expect(page.getByTestId("proposed-candidate")).toContainText("Rea Hotel");
  // The proposal reached the wizard...
  expect(calls.some((c) => c.path === "/api/v1/onboarding/auto-setup" && c.method === "POST")).toBe(true);
  // ...and the auth page did NOT poll the discovery job (that is step 2's job).
  expect(calls.filter((c) => c.path === `/api/v1/scrape-jobs/${JOB_ID}`).length).toBe(0);
});

test("auto-setup failure lands on /setup with the correction form, not an error page", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const createdPropertyId = "55555555-5555-5555-5555-555555555555";
  const createdDiscoveryJobId = "66666666-6666-6666-6666-666666666666";
  let candidateSearches = 0;
  let createAttempts = 0;
  await seedSessionOnly(page);
  // LIFO: register the wildcard first so every specific fake below wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, FRESH_USER);
  await page.route(`${API}/api/v1/onboarding/auto-setup`, (route) =>
    route.fulfill({ status: 500, json: { detail: "Booking search failed" } }),
  );
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    candidateSearches += 1;
    return route.fulfill({
      json: candidateSearches === 1
        ? []
        : [PROPOSED_CANDIDATE, OTHER_CANDIDATE, RACE_CANDIDATE],
    });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property`, (route) => {
    recordCall(calls, route);
    createAttempts += 1;
    if (createAttempts === 1) {
      return route.fulfill({ status: 500, json: { detail: "Property save failed" } });
    }
    return route.fulfill({
      status: 202,
      json: {
        owned_property_id: createdPropertyId,
        discovery_job: { id: createdDiscoveryJobId, status: "queued", job_type: "owned_property_room_discovery" },
      },
    });
  });

  await driveSignUp(page);

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  // Task 4 quality review: the backend's swallowed auto-setup error must
  // still reach the user, verbatim, inside the wizard (spec §8) instead of
  // being silently dropped in favor of a generic message.
  await expect(page.getByTestId("pending-setup-error")).toContainText("Booking search failed");
  // The fallback form carries the sign-up drafts, ready to correct. (The
  // empty candidates mock makes the auto-search render the "no results"
  // empty state alongside it -- guidance, not a dead end, per spec §8.)
  await expect(page.locator('input[name="refineName"]')).toHaveValue("Rea Hotel");
  await expect(page.locator('input[name="refineLocation"]')).toHaveValue("Faliraki");

  // A user search clears the stale setup error and returns a selectable
  // candidate. This is the real failed-auto-setup path: no property id is
  // seeded or returned by /me.
  await page.getByRole("button", { name: "Αναζήτηση ξανά" }).click();
  await expect(page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`)).toBeVisible();
  await expect(page.getByTestId("pending-setup-error")).toHaveCount(0);
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();

  // The first create fails. Retry must repeat POST apply, not launch another
  // potentially minutes-long property-candidates search.
  await expect(page.getByText("Property save failed")).toBeVisible();
  // The ambiguous request belongs to OTHER_CANDIDATE. Neither a second
  // non-default candidate nor the refine fields can silently rewrite its
  // metadata before reconciliation/retry.
  await expect(page.getByTestId(`candidate-${RACE_CANDIDATE.candidate_key}`)).toBeDisabled();
  await expect(page.locator('input[name="refineName"]')).toBeDisabled();
  await expect(page.locator('input[name="refineLocation"]')).toBeDisabled();
  await expect(page.getByRole("button", { name: "Αναζήτηση ξανά" })).toBeDisabled();
  // Mutation drill only: use Angular's dev-mode debug surface (no production
  // hook) to bypass the disabled DOM and change the live selection to a second
  // non-default candidate. Retry must still use the immutable POST snapshot.
  await page.getByTestId(`candidate-${RACE_CANDIDATE.candidate_key}`).evaluate(
    (element, candidateKey) => {
      const debug = (window as unknown as {
        ng?: { getOwningComponent(node: Element): { selectedKey: { set(value: string): void } } };
      }).ng;
      if (!debug) {
        throw new Error("Angular dev-mode debug API is unavailable");
      }
      debug.getOwningComponent(element).selectedKey.set(candidateKey);
    },
    RACE_CANDIDATE.candidate_key,
  );
  const searchesBeforeRetry = calls.filter(
    (call) => call.method === "GET" && call.path === "/api/v1/onboarding/property-candidates",
  ).length;
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  expect(calls.filter((call) => call.method === "GET" && call.path === "/api/v1/onboarding/property-candidates")).toHaveLength(
    searchesBeforeRetry,
  );
  const creates = calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/onboarding/owned-property",
  );
  expect(creates).toHaveLength(2);
  expect(creates.map((call) => (call.body as { display_name: string }).display_name)).toEqual([
    OTHER_CANDIDATE.display_name,
    OTHER_CANDIDATE.display_name,
  ]);
  expect((creates[1].body as { display_name: string }).display_name).toBe(OTHER_CANDIDATE.display_name);
  expect((creates[1].body as { booking_url: string }).booking_url).toBe(OTHER_CANDIDATE.booking_url);
  const consumerStorage = await page.evaluate(() => ({
    ownedPropertyId: window.localStorage.getItem("roomrate_owned_property_id:e2e-user"),
    discoveryJobId: window.localStorage.getItem("roomrate_pending_discovery_job_id:e2e-user"),
    propertyName: window.localStorage.getItem("roomrate_property_name:e2e-user"),
  }));
  expect(consumerStorage).toEqual({
    ownedPropertyId: createdPropertyId,
    discoveryJobId: createdDiscoveryJobId,
    propertyName: OTHER_CANDIDATE.display_name,
  });
});

test("first-property POST committed despite a 500 is reconciled without a duplicate create", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const committedPropertyId = "77777777-7777-7777-7777-777777777777";
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_draft_property_name", "Rea Hotel");
    window.localStorage.setItem("roomrate_draft_location", "Faliraki");
  });
  // LIFO: wildcard first, then every specific recording fake.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    meCalls += 1;
    return route.fulfill({
      json: meCalls === 1
        ? FRESH_USER
        : {
            ...FRESH_USER,
            owned_property_id: committedPropertyId,
            property_name: OTHER_CANDIDATE.display_name,
          },
    });
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [OTHER_CANDIDATE] });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property`, (route) => {
    recordCall(calls, route);
    // Simulate the database commit succeeding but the HTTP response being lost.
    return route.fulfill({ status: 500, json: { detail: "Response lost after commit" } });
  });

  await page.goto("/setup");
  await expect(page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`)).toBeVisible();
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  expect(meCalls).toBe(2); // guard/page cache once, then fresh post-failure reconciliation
  expect(calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/onboarding/owned-property",
  )).toHaveLength(1);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_owned_property_id:e2e-user"),
  )).toBe(committedPropertyId);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_pending_discovery_job_id:e2e-user"),
  )).toBeNull();
  await expect(page.getByText("Response lost after commit")).toHaveCount(0);
});

test("a delayed create commit becomes visible on retry before any second POST", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const delayedPropertyId = "88888888-8888-8888-8888-888888888888";
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_draft_property_name", "Rea Hotel");
    window.localStorage.setItem("roomrate_draft_location", "Faliraki");
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    meCalls += 1;
    return route.fulfill({
      json: meCalls < 3
        ? FRESH_USER
        : {
            ...FRESH_USER,
            owned_property_id: delayedPropertyId,
            property_name: OTHER_CANDIDATE.display_name,
          },
    });
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [OTHER_CANDIDATE] });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ status: 500, json: { detail: "Commit visibility delayed" } });
  });

  await page.goto("/setup");
  await expect(page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`)).toBeVisible();
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();
  await expect(page.getByText("Commit visibility delayed")).toBeVisible();
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  expect(meCalls).toBe(3); // initial, post-failure absent, retry fresh + committed
  expect(calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/onboarding/owned-property",
  )).toHaveLength(1);
  expect(await page.evaluate(() =>
    window.localStorage.getItem("roomrate_owned_property_id:e2e-user"),
  )).toBe(delayedPropertyId);
});

test("successful first-property create invalidates /me and shows its in-flight label", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const createdPropertyId = "99999999-9999-9999-9999-999999999999";
  const createdJobId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
  let meCalls = 0;
  let createCommitted = false;
  let releaseCreate!: () => void;
  const createGate = new Promise<void>((resolve) => {
    releaseCreate = resolve;
  });
  await seedSessionOnly(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_draft_property_name", "Rea Hotel");
    window.localStorage.setItem("roomrate_draft_location", "Faliraki");
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    meCalls += 1;
    return route.fulfill({
      json: createCommitted
        ? {
            ...CURRENT_USER,
            owned_property_id: createdPropertyId,
            property_name: OTHER_CANDIDATE.display_name,
          }
        : FRESH_USER,
    });
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [OTHER_CANDIDATE, RACE_CANDIDATE] });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property`, async (route) => {
    recordCall(calls, route);
    await createGate;
    createCommitted = true;
    return route.fulfill({
      status: 202,
      json: {
        owned_property_id: createdPropertyId,
        discovery_job: { id: createdJobId, status: "queued", job_type: "owned_property_room_discovery" },
      },
    });
  });

  await page.goto("/setup");
  await expect(page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`)).toBeVisible();
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  const applyButton = page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" });
  await applyButton.click();
  try {
    const savingButton = page.getByRole("button", { name: "Γίνεται αποθήκευση..." });
    await expect(savingButton).toBeVisible();
    await expect(savingButton).toBeDisabled();
    await expect(page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`)).toBeDisabled();
    await expect(page.getByTestId(`candidate-${RACE_CANDIDATE.candidate_key}`)).toBeDisabled();
    await expect(page.locator('input[name="refineName"]')).toBeDisabled();
    await expect(page.locator('input[name="refineLocation"]')).toBeDisabled();
    await expect(page.getByRole("button", { name: "Αναζήτηση ξανά" })).toBeDisabled();
  } finally {
    releaseCreate();
  }

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  // Same Angular injector: popstate exercises the map guard without reloading.
  await page.evaluate(() => {
    window.history.pushState({}, "", "/map");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page).toHaveURL(/\/map/);
  await expect(page.getByText(OTHER_CANDIDATE.display_name).first()).toBeVisible();
  expect(meCalls).toBe(2); // initial cached guard/page read + fresh post-create guard read
  expect(calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/onboarding/owned-property",
  )).toHaveLength(1);
});

test("failed create plus failed reconciliation keeps the original error and blocks retry POST", async ({ page }) => {
  const calls: RecordedCall[] = [];
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_draft_property_name", "Rea Hotel");
    window.localStorage.setItem("roomrate_draft_location", "Faliraki");
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    meCalls += 1;
    return meCalls === 1
      ? route.fulfill({ json: FRESH_USER })
      : route.fulfill({ status: 503, json: { detail: "Reconciliation unavailable" } });
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [OTHER_CANDIDATE] });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ status: 500, json: { detail: "Original create failure" } });
  });

  await page.goto("/setup");
  await expect(page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`)).toBeVisible();
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();
  await expect(page.getByText("Original create failure")).toBeVisible();
  await expect(page.getByText("Reconciliation unavailable")).toHaveCount(0);

  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();
  await expect(page.getByText("Original create failure")).toBeVisible();
  expect(meCalls).toBe(3); // initial account read + reconciliation per apply attempt
  expect(calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/onboarding/owned-property",
  )).toHaveLength(1);
});

const OTHER_CANDIDATE = {
  candidate_key: "rea-hotel-annex",
  display_name: "Rea Hotel Annex",
  booking_url: "https://www.booking.com/hotel/gr/rea-annex.html",
  city: "Faliraki",
  address: "Leoforos Kallitheas 14",
  country: "Greece",
  property_type: "hotel",
  latitude: 36.341,
  longitude: 28.201,
  stars: 3,
  review_score: 8.2,
  review_count: 96,
};

const RACE_CANDIDATE = {
  candidate_key: "rea-hotel-garden",
  display_name: "Rea Hotel Garden",
  booking_url: "https://www.booking.com/hotel/gr/rea-garden.html",
  city: "Faliraki",
  address: "Leoforos Kallitheas 18",
  country: "Greece",
  property_type: "hotel",
  latitude: 36.343,
  longitude: 28.204,
  stars: 4,
  review_score: 9.1,
  review_count: 71,
};

/** Seed session + the auto-setup proposal exactly as auth-page stores it. */
async function seedProposal(page: Page): Promise<void> {
  await seedSessionOnly(page);
  await page.addInitScript(
    ({ candidate, jobId, propertyId }) => {
      window.localStorage.setItem("roomrate_pending_candidate:e2e-user", JSON.stringify(candidate));
      window.localStorage.setItem("roomrate_pending_discovery_job_id:e2e-user", jobId);
      window.localStorage.setItem("roomrate_owned_property_id:e2e-user", propertyId);
    },
    { candidate: PROPOSED_CANDIDATE, jobId: JOB_ID, propertyId: OWNED_PROPERTY_ID },
  );
}

test("scenario 2: confirming the proposal reaches step 2 with NO create/replace call", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  await mockMe(page, {
    ...FRESH_USER,
    owned_property_id: OWNED_PROPERTY_ID,
    property_name: "Rea Hotel",
    auth_subject: "e2e-user",
  });
  await page.route(`${API}/api/v1/onboarding/**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "queued" } }),
  );

  await page.goto("/setup");
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  // Card-scoped rather than a bare text match: a plain getByText("Rea Hotel")
  // risks a strict-mode collision once other step-1 UI also mentions the
  // property name.
  await expect(page.getByTestId("proposed-candidate")).toContainText("Rea Hotel");
  await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  const writes = calls.filter((c) => c.method === "PUT" || c.method === "POST" || c.method === "DELETE");
  expect(writes).toEqual([]); // the property already exists — confirming costs nothing
});

test("scenario 2β: picking a DIFFERENT candidate PUTs that candidate, not the proposal", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  await mockMe(page, {
    ...FRESH_USER,
    owned_property_id: OWNED_PROPERTY_ID,
    property_name: "Rea Hotel",
    auth_subject: "e2e-user",
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [PROPOSED_CANDIDATE, OTHER_CANDIDATE] });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      status: 202,
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        discovery_job: { id: JOB_ID, status: "queued", job_type: "owned_property_room_discovery" },
      },
    });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "queued" } }),
  );

  await page.goto("/setup");
  await page.getByRole("button", { name: "Δείξε άλλα" }).click();
  await expect(page.getByText("Rea Hotel Annex")).toBeVisible();
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  const put = calls.find(
    (c) => c.method === "PUT" && c.path === `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}`,
  );
  expect(put).toBeTruthy();
  expect((put!.body as { display_name: string }).display_name).toBe("Rea Hotel Annex");
  expect((put!.body as { booking_url: string }).booking_url).toBe(OTHER_CANDIDATE.booking_url);
});

test("scenario 3: picking a room PUTs selected-room-type and reaches step 3", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  // LIFO: the catch-all is registered first so every endpoint-specific
  // recording fake below wins.
  await page.route(`${API}/api/v1/**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: {
        ...FRESH_USER,
        owned_property_id: OWNED_PROPERTY_ID,
        property_name: "Rea Hotel",
        auth_subject: "e2e-user",
      },
    });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "completed", scrape_runs_count: 1 } });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: [
        {
          id: "33333333-3333-3333-3333-333333333333",
          owned_property_id: OWNED_PROPERTY_ID,
          room_type: "Double Room with Sea View",
          room_type_category: "double",
          // Zero is a valid observed price, not missing data.
          sample_price_per_night_eur: 0,
          sample_facilities: "Balcony, Sea view",
          is_active: true,
        },
        {
          id: "33333333-3333-3333-3333-333333333334",
          owned_property_id: OWNED_PROPERTY_ID,
          room_type: "Family Suite",
          room_type_category: "suite",
          sample_price_per_night_eur: 180,
          sample_facilities: "Two bedrooms",
          is_active: true,
        },
      ],
    });
  });
  await page.route(
    `${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`,
    (route) => {
      recordCall(calls, route);
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
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  await expect(page.getByTestId("room-double")).toContainText("0 € / διανυκτέρευση");
  // Non-default pick on purpose: the SECOND room catches a component that
  // forwards rooms[0] instead of the user's choice.
  await page.getByTestId("room-suite").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  const selectedRoomWrites = calls.filter(
    (call) => call.method === "PUT"
      && call.path === `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`,
  );
  expect(selectedRoomWrites).toHaveLength(1);
  expect((selectedRoomWrites[0].body as { room_type_category: string }).room_type_category).toBe("suite");
});

/** Walk a wildcard-first, fully recorded user flow to the Step 3 cost gate. */
async function reachStepThree(page: Page): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];
  let roomSelected = false;
  await seedProposal(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_property_city:e2e-user", "Lindos");
    window.localStorage.setItem("roomrate_property_raw_destination:e2e-user", "Lindos, Rhodes");
    window.localStorage.setItem("roomrate_selected_room_type_category:e2e-user", "suite");
  });
  // LIFO: register the recording wildcard first; every specific fake below
  // wins while unanticipated requests remain visible in `calls`.
  await page.route(`${API}/api/v1/**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: {
        ...FRESH_USER,
        onboarding_complete: roomSelected,
        owned_property_id: OWNED_PROPERTY_ID,
        property_name: "Rea Hotel",
        destination: "Lindos",
        raw_destination: "Lindos, Rhodes",
        auth_subject: "e2e-user",
        selected_room_type_category: roomSelected ? "suite" : null,
      },
    });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "completed", scrape_runs_count: 1 } });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: [{
        id: "33333333-3333-3333-3333-333333333334",
        owned_property_id: OWNED_PROPERTY_ID,
        room_type: "Family Suite",
        room_type_category: "suite",
        sample_price_per_night_eur: 180,
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
  await page.route(`${API}/api/v1/scrape-jobs/`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      status: 202,
      json: {
        id: "55555555-5555-5555-5555-555555555555",
        status: "queued",
        job_type: "competitor_search",
      },
    });
  });

  await page.goto("/setup");
  await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();
  await page.getByTestId("room-suite").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  return calls;
}

test("scenario 4: NO scrape-job POST happens before the explicit click", async ({ page }) => {
  const calls = await reachStepThree(page);

  await expect(page.getByTestId("cost-statement")).toBeVisible();
  await expect(page.getByTestId("cost-statement")).toContainText("ζωντανά δεδομένα τιμών");
  await expect(page.getByTestId("cost-statement")).toContainText("έχει κόστος");
  await expect(page.getByTestId("cost-statement")).toContainText("Δεν ξεκινά τίποτα χωρίς το δικό σας κλικ");
  expect(calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/scrape-jobs/",
  )).toEqual([]);
});

test("scenario 5a: «Αργότερα» goes to the map without starting anything", async ({ page }) => {
  const calls = await reachStepThree(page);
  // The room PUT invalidated /me. Give the post-skip guard a complete account;
  // wildcard remains first so this specific recording fake wins.
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { ...CURRENT_USER, auth_subject: "e2e-user" } });
  });

  await page.getByRole("button", { name: "Αργότερα" }).click();

  await expect(page).toHaveURL(/\/map/);
  expect(calls.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/scrape-jobs/",
  )).toEqual([]);
});

test("a guarded navigation fetches /me exactly once (guard + page share the cache)", async ({ page }) => {
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    return route.fulfill({ json: CURRENT_USER });
  });

  await page.goto("/map");
  await page.waitForURL(/\/map/);
  // Deterministic anchor: the header renders the /me property name once
  // loadCurrentUser finishes -- no sleep, and a would-be second fetch has
  // its full window before the count is read.
  await expect(page.getByText("E2E Test Hotel").first()).toBeVisible();

  expect(meCalls).toBe(1);
});

test("a /me blip is not cached: the page's read refetches", async ({ page }) => {
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    if (meCalls === 1) {
      return route.fulfill({ status: 500, json: { detail: "blip" } });
    }
    return route.fulfill({ json: CURRENT_USER });
  });

  await page.goto("/map");

  // The guard fail-opens on the blip; the page's own read must be a FRESH
  // fetch, not the cached rejection -- a cached rejection would break every
  // later navigation until a hard reload.
  await expect(page).toHaveURL(/\/map/);
  await expect.poll(() => meCalls).toBe(2);
});

/**
 * A structurally-valid (unsigned) JWT for mocked Supabase token responses.
 *
 * `supabase-js`'s real `setSession()` -- exercised for the first time by the
 * scenario below, everything else in this suite seeds a session directly
 * into localStorage -- calls `decodeJWT()` on the access token and throws
 * "Invalid JWT structure" on anything that is not three base64url segments.
 * It never verifies the signature client-side, so the third segment can be
 * anything in the right alphabet.
 */
function fakeJwt(payload: Record<string, unknown>): string {
  const base64url = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${base64url({ alg: "HS256", typ: "JWT" })}.${base64url(payload)}.e2e-fake-signature`;
}

/**
 * Every Supabase endpoint the sign-out -> sign-in-as-B round trip touches.
 *
 * - /token: the sign-in itself.
 * - /user: setSession() decodes the access token above for `exp`, and -- since
 *   it is not expired -- follows up with GET /user to build the session's user
 *   object (AuthService.signIn passes only the two token strings, never a full
 *   session, so supabase-js cannot skip this call).
 * - /logout: map-page's sign-out button calls the real supabase-js signOut(),
 *   which POSTs here before anything else runs; left unmocked it either escapes
 *   to the live project or hangs the test.
 */
async function mockSupabaseSignInAsUserB(page: Page): Promise<void> {
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/token/, (route) =>
    route.fulfill({
      json: {
        access_token: fakeJwt({
          sub: "e2e-user-b",
          aud: "authenticated",
          role: "authenticated",
          email: "b@roomrate.test",
          exp: Math.floor(Date.now() / 1000) + 3600,
        }),
        refresh_token: "r",
        token_type: "bearer",
        expires_in: 3600,
        user: { id: "e2e-user-b", aud: "authenticated", role: "authenticated", email: "b@roomrate.test" },
      },
    }),
  );
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/user/, (route) =>
    route.fulfill({ json: { id: "e2e-user-b", aud: "authenticated", role: "authenticated", email: "b@roomrate.test" } }),
  );
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/logout/, (route) => route.fulfill({ status: 204 }));
}

test("a second sign-in never reuses the previous account's /me", async ({ page }) => {
  let meBody: unknown = { ...CURRENT_USER };            // user A: complete
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    return route.fulfill({ json: meBody });
  });
  await mockSupabaseSignInAsUserB(page);

  await page.goto("/map");
  await expect(page).toHaveURL(/\/map/);

  // Sign out (SPA navigation, no reload); the backend now belongs to B. The
  // real map-page template has a plain, always-reachable button with this
  // exact accessible name -- no adaptation needed.
  await page.getByRole("button", { name: /αποσύνδεση/i }).click();
  await expect(page).toHaveURL(/\/auth/);
  // B already has a property from a previous session but never finished
  // room selection: continueAfterAuth's "half-onboarded" branch sends it
  // straight to /setup without requiring name/location on the sign-in form.
  // (A property-LESS B with an equally blank form is the scenario below.)
  meBody = { ...FRESH_USER, auth_subject: "e2e-user-b", owned_property_id: OWNED_PROPERTY_ID };
  const callsBefore = meCalls;

  await page.getByLabel(/email/i).fill("b@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-2");
  await page.getByRole("button", { name: /^σύνδεση$/i }).click();

  // B lands in the wizard off a FRESH read -- a stale complete cache would
  // open user A's map for user B.
  await expect(page).toHaveURL(/\/setup/);
  expect(meCalls).toBeGreaterThan(callsBefore);
});

test("scenario 3.3: a blank sign-in of a property-less account continues to the wizard", async ({ page }) => {
  const autoSetupCalls: RecordedCall[] = [];
  let meBody: unknown = { ...CURRENT_USER };            // user A: complete
  await seedSessionOnly(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: meBody }));
  // Recorded, not just stubbed: auto-setup with no name/location is exactly the
  // fall-through this scenario must rule out (the wildcard above would answer
  // it with a body the auth page then swallows, hiding the bug behind a /setup
  // URL that looks right).
  await page.route(`${API}/api/v1/onboarding/auto-setup`, (route) => {
    recordCall(autoSetupCalls, route);
    return route.fulfill({ status: 202, json: {} });
  });
  await mockSupabaseSignInAsUserB(page);

  await page.goto("/map");
  await expect(page).toHaveURL(/\/map/);

  // Sign out clears workflow storage, drafts included -- so B's sign-in below
  // is the real thing: empty form fields AND empty storage.
  await page.getByRole("button", { name: /αποσύνδεση/i }).click();
  await expect(page).toHaveURL(/\/auth/);
  meBody = { ...FRESH_USER, auth_subject: "e2e-user-b" };   // B: no property at all
  await expect(page.getByLabel(/όνομα καταλύματος/i)).toHaveValue("");
  await expect(page.getByLabel(/^τοποθεσία$/i)).toHaveValue("");

  await page.getByLabel(/email/i).fill("b@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-2");
  await page.getByRole("button", { name: /^σύνδεση$/i }).click();

  // No dead end on /auth: the wizard's step 1 opens in pick mode and asks for
  // the name and location itself, with its own search form. Leaving /auth IS
  // the anti-dead-end assertion -- the old «Συμπληρώστε όνομα καταλύματος και
  // τοποθεσία για να συνεχίσετε.» kept the page put, so asserting that text is
  // absent AFTER the navigation would be an assertion that cannot fail.
  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  await expect(page.getByLabel("Όνομα καταλύματος")).toBeVisible();
  await expect(page.getByLabel("Τοποθεσία")).toBeVisible();
  // Nothing to search FROM, so nothing may be searched: an empty auto-setup
  // would burn a live Booking lookup (and a discovery job) on blank input.
  expect(autoSetupCalls).toHaveLength(0);
});

test("scenario 3.3b: that blank sign-in also clears the account's stale property cache", async ({ page }) => {
  let meBody: unknown = { ...CURRENT_USER };            // user A: complete
  await seedSessionOnly(page);
  // B's own leftovers from an earlier session in this browser. They survive
  // the sign-out below: workflow.clear() resolves keys against the subject
  // bound AT THAT MOMENT (A's), so it takes A's "<baseKey>:e2e-user" values
  // and the global drafts, never B's.
  //
  // Why they matter even though /setup renders none of them: map-page
  // (:858-863) and pricing-page (:285-289) read `workflow.get(...) ||
  // currentUser.*` -- the cache WINS over the fresh /me. Left behind, a
  // property that vanished server-side keeps naming itself on B's next map
  // visit. storeCurrentUser() in the /setup branch is what wipes them, and
  // for a property-less account "wipe" is literal: set(key, "") removes it.
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_owned_property_id:e2e-user-b", "stale-property-id");
    window.localStorage.setItem("roomrate_property_name:e2e-user-b", "Παλιό Ξενοδοχείο");
    window.localStorage.setItem("roomrate_property_city:e2e-user-b", "Παλιά Πόλη");
    window.localStorage.setItem("roomrate_property_raw_destination:e2e-user-b", "Παλιά Πόλη, Ρόδος");
    window.localStorage.setItem("roomrate_property_canonical_destination:e2e-user-b", "palia-poli");
    window.localStorage.setItem("roomrate_selected_room_type_category:e2e-user-b", "double");
    // The onboarding leftovers of the same dead property. setup-page reads
    // pendingCandidate to decide step 1's mode, so this one is not just stale
    // cache: left behind it opens «Βρήκαμε αυτό το κατάλυμα. Είστε εσείς;» for
    // an account that owns nothing, and confirming it emits a blank
    // ownedPropertyId into step 2.
    window.localStorage.setItem(
      "roomrate_pending_candidate:e2e-user-b",
      JSON.stringify({
        candidate_key: "stale-candidate",
        display_name: "Παλιό Ξενοδοχείο",
        booking_url: "https://www.booking.com/hotel/gr/palio.html",
        city: "Παλιά Πόλη",
        address: "Οδός 1",
      }),
    );
    window.localStorage.setItem("roomrate_pending_discovery_job_id:e2e-user-b", "stale-job-id");
    window.localStorage.setItem("roomrate_pending_setup_error:e2e-user-b", "Παλιό σφάλμα");
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: meBody }));
  await mockSupabaseSignInAsUserB(page);

  await page.goto("/map");
  await expect(page).toHaveURL(/\/map/);

  await page.getByRole("button", { name: /αποσύνδεση/i }).click();
  await expect(page).toHaveURL(/\/auth/);
  meBody = { ...FRESH_USER, auth_subject: "e2e-user-b" };   // B: no property at all

  await page.getByLabel(/email/i).fill("b@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-2");
  await page.getByRole("button", { name: /^σύνδεση$/i }).click();

  // Landing in the wizard is the anchor: the storage writes happen BEFORE the
  // navigation that produces this URL, so the read below cannot race them.
  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  // PICK mode, not confirm: the stale proposal must not survive as a question
  // about a property this account does not own.
  await expect(page.locator('input[name="refineName"]')).toBeVisible();
  await expect(page.getByTestId("proposed-candidate")).toHaveCount(0);
  await expect(page.getByTestId("pending-setup-error")).toHaveCount(0);
  expect(await page.evaluate(() => ({
    ownedPropertyId: window.localStorage.getItem("roomrate_owned_property_id:e2e-user-b"),
    propertyName: window.localStorage.getItem("roomrate_property_name:e2e-user-b"),
    destination: window.localStorage.getItem("roomrate_property_city:e2e-user-b"),
    rawDestination: window.localStorage.getItem("roomrate_property_raw_destination:e2e-user-b"),
    canonicalDestination: window.localStorage.getItem("roomrate_property_canonical_destination:e2e-user-b"),
    roomTypeCategory: window.localStorage.getItem("roomrate_selected_room_type_category:e2e-user-b"),
    pendingCandidate: window.localStorage.getItem("roomrate_pending_candidate:e2e-user-b"),
    pendingDiscoveryJobId: window.localStorage.getItem("roomrate_pending_discovery_job_id:e2e-user-b"),
    pendingSetupError: window.localStorage.getItem("roomrate_pending_setup_error:e2e-user-b"),
  }))).toEqual({
    ownedPropertyId: null,
    propertyName: null,
    destination: null,
    rawDestination: null,
    canonicalDestination: null,
    roomTypeCategory: null,
    pendingCandidate: null,
    pendingDiscoveryJobId: null,
    pendingSetupError: null,
  });
});

test("scenario 3.3c: a blank arrival at step 1 says nothing was searched yet", async ({ page }) => {
  const candidateCalls: RecordedCall[] = [];
  await seedSessionOnly(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, FRESH_USER);
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(candidateCalls, route);
    return route.fulfill({ json: [] });
  });

  await page.goto("/setup");

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  const step = page.locator(".setup-step");
  // Nothing was searched, so nothing may be reported: «Δεν βρέθηκαν
  // καταλύματα» would describe a Booking answer that never came, and the red
  // validation error would blame the user for fields they have not touched.
  await expect(step).toContainText("Δεν έχει γίνει αναζήτηση ακόμα");
  await expect(step).not.toContainText("Δεν βρέθηκαν καταλύματα");
  await expect(step.locator(".alert-error")).toHaveCount(0);
  expect(candidateCalls).toHaveLength(0);

  // The user ACTS with blank fields: now the validation error is earned. Still
  // no request -- an empty query would burn a live Booking lookup.
  await page.getByRole("button", { name: "Αναζήτηση ξανά" }).click();
  await expect(step.locator(".alert-error")).toContainText("Συμπληρώστε όνομα καταλύματος και τοποθεσία.");
  expect(candidateCalls).toHaveLength(0);

  // A search that really ran and really came back empty still says so.
  await page.locator('input[name="refineName"]').fill("Rea Hotel");
  await page.locator('input[name="refineLocation"]').fill("Faliraki");
  await page.getByRole("button", { name: "Αναζήτηση ξανά" }).click();
  await expect(step).toContainText("Δεν βρέθηκαν καταλύματα");
  await expect(step).not.toContainText("Δεν έχει γίνει αναζήτηση ακόμα");
  await expect(step.locator(".alert-error")).toHaveCount(0);
  expect(candidateCalls).toHaveLength(1);
});

test("scenario 3.3d: a mount search that FAILS reports the failure, not «not searched yet»", async ({ page }) => {
  // The drafts carry a name and a location, so the mount-time search really
  // runs -- and when it throws, hasSearched stays false by design (a failed
  // request proves nothing about what Booking holds). That left the page
  // claiming «Δεν έχει γίνει αναζήτηση ακόμα» directly above a red alert
  // saying the search failed: two statements that cannot both be true.
  await seedSessionOnly(page);
  await page.addInitScript(() => {
    // Draft keys are global (not auth-subject scoped) -- see
    // WorkflowStorageService.GLOBAL_DRAFT_KEYS.
    window.localStorage.setItem("roomrate_draft_property_name", "Rea Hotel");
    window.localStorage.setItem("roomrate_draft_location", "Faliraki");
  });
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, FRESH_USER);
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Booking search is unavailable" } }),
  );

  await page.goto("/setup");

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  const step = page.locator(".setup-step");
  await expect(step.locator(".alert-error")).toContainText("Booking search is unavailable");
  await expect(step).not.toContainText("Δεν έχει γίνει αναζήτηση ακόμα");
  // ...and the failure is still not passed off as Booking's empty answer.
  await expect(step).not.toContainText("Δεν βρέθηκαν καταλύματα");
});

test("scenario 5b: after «Αργότερα» the map checklist shows 2 done, step 3 open", async ({ page }) => {
  await reachStepThree(page);
  // NOT re-registering a generic `${API}/api/v1/**` wildcard here (same reason
  // as 5c, plus a sharper one this test hit three times in CI): step 3 issues
  // its OWN `/me` read from ngOnInit -> prepareIntent, and that request leaves
  // the browser only after supabase's getSession() resolves, i.e. some ticks
  // AFTER the "Βήμα 3" heading that reachStepThree waits on. A second wildcard
  // registered in that window wins by recency over reachStepThree's `/me` mock,
  // answers `[]`, and buildAuthoritativeSearchIntent throws -> the step renders
  // the preparation-error panel, whose buttons are «Επαναφόρτωση στοιχείων» /
  // «Συνέχεια στον χάρτη» -- «Αργότερα» never appears and the click below hangs
  // until the test times out. reachStepThree's own wildcard already answers
  // every unanticipated path with `[]`, so only the paths this scenario cares
  // about need a fresh registration.
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }),
  );
  await mockMe(page, { ...CURRENT_USER, auth_subject: "e2e-user" }); // property + room now exist

  await page.getByRole("button", { name: "Αργότερα" }).click();

  await expect(page).toHaveURL(/\/map/);
  await expect(page.getByTestId("setup-checklist")).toBeVisible();
  // Two-sided: the map must not overflow the grid (clipping) NOR collapse —
  // its bottom edge coincides with the grid's, and it keeps real height.
  const gridBox = await page.locator(".map-grid").boundingBox();
  const mapBox = await page.locator(".roomrate-map").boundingBox();
  expect(gridBox).not.toBeNull();
  expect(mapBox).not.toBeNull();
  expect(Math.abs(mapBox!.y + mapBox!.height - (gridBox!.y + gridBox!.height))).toBeLessThanOrEqual(1);
  expect(mapBox!.height).toBeGreaterThan(200);
  // The checklist sits at the top of .map-shell; its own overlays (the
  // Filters toggle and the "no results" empty state) must not paint on top
  // of it. jobs=[] means no competitors were restored, so the empty state
  // renders -- and, since Round 6 (spec §4.1), the filters form opens by
  // itself. Folding it back brings the Filters toggle onto the map, so both
  // overlays are measured.
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  await page.getByRole("button", { name: "Απόκρυψη" }).click();
  const checklistBox = await page.getByTestId("setup-checklist").boundingBox();
  const filtersButtonBox = await page.getByRole("button", { name: "Φίλτρα", exact: true }).boundingBox();
  const emptyStateBox = await page.locator(".map-empty-state").boundingBox();
  expect(checklistBox).not.toBeNull();
  expect(filtersButtonBox).not.toBeNull();
  expect(emptyStateBox).not.toBeNull();
  expect(boxesIntersect(checklistBox!, filtersButtonBox!)).toBe(false);
  expect(boxesIntersect(checklistBox!, emptyStateBox!)).toBe(false);
  await expect(page.getByTestId("checklist-progress")).toContainText("2 από 5");
  await expect(page.getByTestId("checklist-open-step")).toContainText("Πρώτη αναζήτηση");
});

test("scenario 5c: the checklist reflects fetched jobs and tracked competitors", async ({ page }) => {
  await reachStepThree(page);
  // NOT re-registering a generic `${API}/api/v1/**` wildcard here: reachStepThree
  // already installed one, and registering a second (newer) one would win by
  // recency over reachStepThree's own /room-types mock, emptying it and wiping
  // selectedRoomType back to "" before restoreLatestCompetitorSearch runs.
  // Only the paths this scenario cares about need a fresh registration.
  //
  // reachStepThree leaves the account on "suite"/"Lindos" (its own room-types
  // mock only offers a suite room, and workflow storage wins over whatever
  // /me reports here), so the restorable job below must match "suite" for
  // canRestoreJob to accept it.
  await mockMe(page, {
    ...CURRENT_USER,
    auth_subject: "e2e-user",
    destination: "Lindos",
    raw_destination: "Lindos, Rhodes",
    canonical_destination: "lindos",
    selected_room_type_category: "suite",
  });
  // "**" is required -- the real request carries "?limit=50", and a plain
  // (non-glob) route string only matches a request URL exactly. Safe to be
  // broad here: by the time these routes are registered, reachStepThree's
  // own setup-flow polling (which used the by-id path) has already finished,
  // and this scenario never seeds a storedJobId to trigger a fresh one.
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) =>
    route.fulfill({
      json: [
        {
          id: "job-checklist-1",
          account_id: ACCOUNT_ID,
          owned_property_id: OWNED_PROPERTY_ID,
          job_type: "competitor_search",
          room_type_category: "suite",
          destination: "Lindos",
          raw_destination: "Lindos, Rhodes",
          canonical_destination: "lindos",
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
        },
        // Orphaned job: the backend nulls owned_property_id on property
        // delete instead of deleting the job (onboarding_repository.py). An
        // account-wide run count would let a brand-new property inherit
        // this job's runs and falsely mark steps 3/5 done.
        {
          id: "job-orphaned-1",
          account_id: ACCOUNT_ID,
          owned_property_id: "prop-orphaned",
          job_type: "competitor_search",
          room_type_category: "suite",
          destination: "Somewhere Else",
          raw_destination: "Somewhere Else, Region",
          canonical_destination: "somewhere-else",
          check_in: "2026-06-01",
          check_out: "2026-06-05",
          adults: 2,
          children: 0,
          rooms: 1,
          filters_payload: {},
          status: "completed",
          requested_at: "2026-06-01T10:00:00Z",
          started_at: "2026-06-01T10:00:05Z",
          finished_at: "2026-06-01T10:04:00Z",
          error_message: null,
          attempt_count: 1,
          max_attempts: 3,
          next_attempt_at: null,
          scrape_runs_count: 5,
        },
      ],
    }),
  );
  // A non-empty markers list is required: applyTrackedCompetitors() only
  // fetches tracked competitors when this.competitors().length is truthy.
  // "**" is required -- the real request carries a query string, and a plain
  // (non-glob) route string only matches a request URL exactly.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) =>
    route.fulfill({
      json: [
        {
          hotel_name: "Hotel Ambrosia",
          property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          room_package_id: null,
          room_type: "Family Suite",
          room_type_category: "suite",
          property_type: "hotel",
          latitude: 36.09,
          longitude: 28.09,
          price_per_night_eur: 180,
          review_score: 8.9,
          review_count: 64,
          rooms_left: 2,
        },
      ],
    }),
  );
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "suite",
        competitors: [
          { property_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", room_package_id: null },
          { property_id: "ffffffff-ffff-ffff-ffff-ffffffffffff", room_package_id: null },
          { property_id: "12121212-1212-1212-1212-121212121212", room_package_id: null },
        ],
      },
    }),
  );

  await page.getByRole("button", { name: "Αργότερα" }).click();

  await expect(page).toHaveURL(/\/map/);
  // 1 run (step 3) + 3 tracked (step 4) + property/room (1, 2) = 4/5; step 5
  // needs a second completed run, which this single job cannot supply.
  await expect(page.getByTestId("checklist-progress")).toContainText("4 από 5");
  await expect(page.getByTestId("checklist-open-step")).toContainText("Πρώτη σύσταση τιμής");
});

/**
 * The "restore latest search" shortcut (findRestorableCompetitorJob) fetches
 * ONE job by its stored id and returns early on a match -- bypassing the
 * `/api/v1/scrape-jobs/` LIST call the checklist normally learns from. A
 * returning user whose last search is restored this way provably completed
 * a search, so step 3 must still read as done.
 */
test("a returning user's restored search feeds the checklist (step 3 counts as done)", async ({ page }) => {
  await seedSessionOnly(page);
  // The storedJobId shortcut only fires once WorkflowStorageService has
  // bound to a subject, which reads THIS subject-scoped key -- an unscoped
  // "roomrate_last_competitor_job_id" (as seedBrowserState in helpers.ts
  // writes) is never read once a session is bound and is not a valid seed
  // for this path.
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_last_competitor_job_id:e2e-user", "job-checklist-1");
  });
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(/https:\/\/(api|events)\.mapbox\.com\/.*/, (route) => route.abort());
  await mockMe(page, CURRENT_USER);
  // A non-empty room-types list is required: an empty list makes
  // ensureSelectedRoom() wipe selectedRoomType back to "", which fails
  // restoreLatestCompetitorSearch's guard before it ever restores anything.
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [{
        id: ROOM_TYPE_ID,
        owned_property_id: OWNED_PROPERTY_ID,
        room_type: "Double Room with Sea View",
        room_type_category: "double",
        is_active: true,
      }],
    }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/job-checklist-1`, (route) =>
    route.fulfill({
      json: {
        id: "job-checklist-1",
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
      },
    }),
  );
  // A non-empty markers list is required: applyTrackedCompetitors() only
  // fetches tracked competitors when this.competitors().length is truthy.
  // "**" is required: the real request carries a query string, and a plain
  // (non-glob) route string only matches a request URL exactly.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) =>
    route.fulfill({
      json: [
        {
          hotel_name: "Hotel Ambrosia",
          property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
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
      ],
    }),
  );
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "double",
        competitors: [
          { property_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", room_package_id: null },
          { property_id: "cccccccc-cccc-cccc-cccc-cccccccccccc", room_package_id: null },
          { property_id: "dddddddd-dddd-dddd-dddd-dddddddddddd", room_package_id: null },
        ],
      },
    }),
  );

  await page.goto("/map");

  await expect(page).toHaveURL(/\/map/);
  // 1 restored run (step 3) + 3 tracked (step 4) + property/room (1, 2) = 4/5;
  // step 5 needs a second run, which this single restored job cannot supply.
  await expect(page.getByTestId("checklist-progress")).toContainText("4 από 5");
});

/**
 * applyTrackedCompetitors() is the ONLY caller of setupProgress.setTracked,
 * and loadMarkers skips it whenever restoreStoredCompetitorSelection(jobId)
 * returns true -- which it does for ANY stored lastCompetitorSelection whose
 * jobId matches the restored job, and toggleCompetitor persists exactly that
 * key on every click. So every user who ever tracked a competitor reloads
 * into a checklist that still tells them to track competitors.
 */
test("a returning user with a saved competitor selection still ticks step 4", async ({ page }) => {
  await seedSessionOnly(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_last_competitor_job_id:e2e-user", "job-checklist-1");
    // Matches persistCompetitorSelection's exact shape: { jobId, keys }.
    // The key matches the mocked marker's markerKey() (room_package_id is
    // null, so it falls back to property_id) so the restore is realistic.
    window.localStorage.setItem(
      "roomrate_last_competitor_selection:e2e-user",
      JSON.stringify({ jobId: "job-checklist-1", keys: ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"] }),
    );
  });
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(/https:\/\/(api|events)\.mapbox\.com\/.*/, (route) => route.abort());
  await mockMe(page, CURRENT_USER);
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [{
        id: ROOM_TYPE_ID,
        owned_property_id: OWNED_PROPERTY_ID,
        room_type: "Double Room with Sea View",
        room_type_category: "double",
        is_active: true,
      }],
    }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/job-checklist-1`, (route) =>
    route.fulfill({
      json: {
        id: "job-checklist-1",
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
      },
    }),
  );
  await page.route(`${API}/api/v1/maps/competitors**`, (route) =>
    route.fulfill({
      json: [
        {
          hotel_name: "Hotel Ambrosia",
          property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
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
      ],
    }),
  );
  const trackedCalls: RecordedCall[] = [];
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) => {
    recordCall(trackedCalls, route);
    return route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "double",
        competitors: [
          { property_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", room_package_id: null },
          { property_id: "cccccccc-cccc-cccc-cccc-cccccccccccc", room_package_id: null },
          { property_id: "dddddddd-dddd-dddd-dddd-dddddddddddd", room_package_id: null },
        ],
      },
    });
  });

  await page.goto("/map");

  await expect(page).toHaveURL(/\/map/);
  await expect(page.getByTestId("checklist-progress")).toContainText("4 από 5");
  expect(trackedCalls.length).toBeGreaterThan(0);
});

test("the stored hide flag keeps the checklist off the map", async ({ page }) => {
  await seedSessionOnly(page); // yields subject "e2e-user", matching CURRENT_USER.auth_subject
  // WorkflowStorageService.STORAGE_KEYS.checklistHidden = "roomrate_checklist_hidden",
  // and non-draft keys are read as "<baseKey>:<authSubject>" once bound.
  await page.addInitScript(() => {
    window.localStorage.setItem("roomrate_checklist_hidden:e2e-user", "1");
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, CURRENT_USER);

  await page.goto("/map");
  await expect(page).toHaveURL(/\/map/);
  await expect(page.locator("h1").first()).toBeVisible();

  await expect(page.getByTestId("setup-checklist")).toHaveCount(0);
});

test("the checklist starts collapsed on small screens, leaving room for the map", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, CURRENT_USER);

  await page.goto("/map");

  await expect(page).toHaveURL(/\/map/);
  await expect(page.getByTestId("setup-checklist")).toBeVisible();
  // Collapsed by default: the steps list (*ngIf="!collapsed()") is absent.
  await expect(page.locator(".checklist-steps")).toHaveCount(0);
  const mapBox = await page.locator(".roomrate-map").boundingBox();
  expect(mapBox).not.toBeNull();
  expect(mapBox!.height).toBeGreaterThan(300);
});

test("reaching 5/5 writes the stored hide flag for next time", async ({ page }) => {
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, CURRENT_USER);
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [{
        id: ROOM_TYPE_ID,
        owned_property_id: OWNED_PROPERTY_ID,
        room_type: "Double Room with Sea View",
        room_type_category: "double",
        is_active: true,
      }],
    }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) =>
    route.fulfill({
      json: [
        {
          id: "job-full-1",
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
          scrape_runs_count: 2,
        },
      ],
    }),
  );
  await page.route(`${API}/api/v1/maps/competitors**`, (route) =>
    route.fulfill({
      json: [
        {
          hotel_name: "Hotel Ambrosia",
          property_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
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
      ],
    }),
  );
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "double",
        competitors: [
          { property_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", room_package_id: null },
          { property_id: "cccccccc-cccc-cccc-cccc-cccccccccccc", room_package_id: null },
          { property_id: "dddddddd-dddd-dddd-dddd-dddddddddddd", room_package_id: null },
        ],
      },
    }),
  );

  await page.goto("/map");

  await expect(page).toHaveURL(/\/map/);
  // 5/5: property, room, 2 runs (steps 3 + 5), 3 tracked (step 4) -> hides.
  await expect(page.getByTestId("setup-checklist")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("roomrate_checklist_hidden:e2e-user"))).toBe("1");
});
