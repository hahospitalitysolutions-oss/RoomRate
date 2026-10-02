import { expect, Page, Route, test } from "@playwright/test";

import { API, JOB_ID, OWNED_PROPERTY_ID, RecordedCall, recordCall } from "./helpers";
import {
  openRoomStep,
  pathCalls,
  prepareRoomStep,
  ROOM_CATALOG,
  SAVE_ERROR,
} from "./setup-room-step.helpers";

const JOB_PATH = `/api/v1/scrape-jobs/${JOB_ID}`;
const CATALOG_PATH = `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`;
const SELECTION_PATH = `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`;

type HeldRequest = { route: Route; release: () => void };

async function mockCompletedDiscovery(page: Page, calls: RecordedCall[]): Promise<void> {
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "completed" } });
  });
  await page.route(`${API}${CATALOG_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: ROOM_CATALOG });
  });
}

async function fulfillHeld(
  held: HeldRequest,
  response: Parameters<Route["fulfill"]>[0],
): Promise<void> {
  await held.route.fulfill(response);
  held.release();
}

test("failed save stays on step 2 and retry repeats only the PUT", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const { withProposal } = await prepareRoomStep(page, calls);
  await mockCompletedDiscovery(page, calls);
  const held: HeldRequest[] = [];
  await page.route(`${API}${SELECTION_PATH}`, (route) => {
    recordCall(calls, route);
    return new Promise<void>((resolve) => held.push({ route, release: resolve }));
  });

  await openRoomStep(page, withProposal);
  await page.getByTestId("room-suite").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).click();
  await expect.poll(() => held.length).toBe(1);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  await fulfillHeld(held[0], { status: 500, json: { detail: "Raw provider save failure" } });

  await expect(page.getByText(SAVE_ERROR)).toBeVisible();
  await expect(page.getByText("Raw provider save failure")).toHaveCount(0);
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();
  await expect.poll(() => held.length).toBe(2);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  expect(pathCalls(calls, JOB_PATH)).toHaveLength(1);
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(1);

  await fulfillHeld(held[1], {
    json: {
      owned_property_id: OWNED_PROPERTY_ID,
      selected_room_type_category: "suite",
      onboarding_complete: true,
    },
  });
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  expect(pathCalls(calls, SELECTION_PATH, "PUT")).toHaveLength(2);
});

test("two clicks while a save is held produce one PUT", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const { withProposal } = await prepareRoomStep(page, calls);
  await mockCompletedDiscovery(page, calls);
  let held: HeldRequest | null = null;
  await page.route(`${API}${SELECTION_PATH}`, (route) => {
    recordCall(calls, route);
    return new Promise<void>((resolve) => { held = { route, release: resolve }; });
  });

  await openRoomStep(page, withProposal);
  await page.getByTestId("room-suite").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => pathCalls(calls, SELECTION_PATH, "PUT").length).toBe(1);
  await expect(page.getByRole("button", { name: "Γίνεται αποθήκευση..." })).toBeDisabled();

  await fulfillHeld(held!, {
    json: {
      owned_property_id: OWNED_PROPERTY_ID,
      selected_room_type_category: "suite",
      onboarding_complete: true,
    },
  });
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  expect(pathCalls(calls, SELECTION_PATH, "PUT")).toHaveLength(1);
});

test("empty catalog retry is single-flight and loads rooms without leaving", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const { withProposal } = await prepareRoomStep(page, calls, {
    discoveryJobId: null,
    withProposal: false,
  });
  let catalogReads = 0;
  const heldReloads: HeldRequest[] = [];
  await page.route(`${API}${CATALOG_PATH}`, (route) => {
    recordCall(calls, route);
    catalogReads += 1;
    if (catalogReads === 1) {
      return route.fulfill({ json: [] });
    }
    return new Promise<void>((resolve) => heldReloads.push({ route, release: resolve }));
  });

  await openRoomStep(page, withProposal);
  await expect(page.getByText("Δεν βρέθηκαν δωμάτια")).toBeVisible();
  await expect(page.getByRole("button", { name: "Συνέχεια στον χάρτη" })).toBeVisible();
  await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => heldReloads.length).toBe(1);
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(2);
  await fulfillHeld(heldReloads[0], { json: ROOM_CATALOG });
  await expect(page.getByTestId("room-double")).toBeVisible();
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(2);
});

test("empty catalog skip reaches the map for this navigation only", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const { withProposal } = await prepareRoomStep(page, calls, {
    discoveryJobId: null,
    withProposal: false,
  });
  await page.route(`${API}${CATALOG_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });

  await openRoomStep(page, withProposal);
  await page.getByRole("button", { name: "Συνέχεια στον χάρτη" }).click();
  await expect(page).toHaveURL(/\/map/);

  // Same-document SPA navigation: the Router extras state belonged only to
  // the skip navigation and cannot leak into a later ordinary map link.
  await page.getByRole("link", { name: "Ρυθμίσεις" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByRole("link", { name: "Χάρτης" }).click();
  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
});
