import { expect, Route, test } from "@playwright/test";

import { API, JOB_ID, OWNED_PROPERTY_ID, RecordedCall, recordCall } from "./helpers";
import {
  LOAD_ERROR,
  openRoomStep,
  pathCalls,
  prepareRoomStep,
  ROOM_CATALOG,
  TIMEOUT_ERROR,
} from "./setup-room-step.helpers";

const JOB_PATH = `/api/v1/scrape-jobs/${JOB_ID}`;
const CATALOG_PATH = `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`;

test("room discovery polls queued to completed before loading the catalog", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await page.clock.install();
  const { withProposal } = await prepareRoomStep(page, calls);
  let jobReads = 0;
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    jobReads += 1;
    return route.fulfill({ json: { id: JOB_ID, status: jobReads === 1 ? "queued" : "completed" } });
  });
  await page.route(`${API}${CATALOG_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: ROOM_CATALOG });
  });

  await openRoomStep(page, withProposal);
  await expect.poll(() => pathCalls(calls, JOB_PATH).length).toBe(1);
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(0);
  await page.clock.fastForward(4_999);
  expect(pathCalls(calls, JOB_PATH)).toHaveLength(1);
  await page.clock.fastForward(1);
  await expect(page.getByTestId("room-double")).toBeVisible();

  const ordered = calls.filter((call) => call.path === JOB_PATH || call.path === CATALOG_PATH);
  expect(ordered.map((call) => call.path)).toEqual([JOB_PATH, JOB_PATH, CATALOG_PATH]);
});

for (const terminalStatus of ["failed", "cancelled"] as const) {
  test(`${terminalStatus} discovery blocks the catalog and retry loads it`, async ({ page }) => {
    const calls: RecordedCall[] = [];
    const { withProposal } = await prepareRoomStep(page, calls);
    let jobReads = 0;
    await page.route(`${API}${JOB_PATH}`, (route) => {
      recordCall(calls, route);
      jobReads += 1;
      return route.fulfill({
        json: {
          id: JOB_ID,
          status: jobReads === 1 ? terminalStatus : "completed",
          error_message: "Provider exploded",
        },
      });
    });
    await page.route(`${API}${CATALOG_PATH}`, (route) => {
      recordCall(calls, route);
      return route.fulfill({ json: ROOM_CATALOG });
    });

    await openRoomStep(page, withProposal);
    await expect(page.getByText(LOAD_ERROR)).toBeVisible();
    await expect(page.getByText("Provider exploded")).toHaveCount(0);
    expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(0);
    await page.getByRole("button", { name: "Δοκιμάστε ξανά" }).click();
    await expect(page.getByTestId("room-double")).toBeVisible();
    expect(pathCalls(calls, JOB_PATH)).toHaveLength(2);
    expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(1);
  });
}

test("no discovery job id fetches the catalog without polling", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const { withProposal } = await prepareRoomStep(page, calls, { discoveryJobId: null });
  await page.route(`${API}${CATALOG_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: ROOM_CATALOG });
  });

  await openRoomStep(page, withProposal);
  await expect(page.getByTestId("room-double")).toBeVisible();
  expect(calls.filter((call) => call.path.startsWith("/api/v1/scrape-jobs/"))).toHaveLength(0);
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(1);
});

test("catalog network details are mapped to the stable Greek load error", async ({ page }) => {
  const calls: RecordedCall[] = [];
  const { withProposal } = await prepareRoomStep(page, calls, {
    discoveryJobId: null,
    withProposal: false,
  });
  await page.route(`${API}${CATALOG_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ status: 500, json: { detail: "Raw catalog failure" } });
  });

  await openRoomStep(page, withProposal);
  await expect(page.getByText(LOAD_ERROR)).toBeVisible();
  await expect(page.getByText("Raw catalog failure")).toHaveCount(0);
});

test("a held poll times out at the monotonic five-minute deadline", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await page.clock.install();
  const { withProposal } = await prepareRoomStep(page, calls);
  let heldRoute: Route | null = null;
  let releaseHandler: (() => void) | null = null;
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    heldRoute = route;
    return new Promise<void>((resolve) => { releaseHandler = resolve; });
  });

  await openRoomStep(page, withProposal);
  await expect.poll(() => pathCalls(calls, JOB_PATH).length).toBe(1);
  await page.clock.fastForward(300_000);
  await expect(page.getByText(TIMEOUT_ERROR)).toBeVisible();
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(0);

  await heldRoute!.fulfill({ json: { id: JOB_ID, status: "completed" } });
  releaseHandler!();
  await expect(page.getByText(TIMEOUT_ERROR)).toBeVisible();
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(0);
});

test("destroying the step during its poll delay prevents another poll", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await page.clock.install();
  const { withProposal } = await prepareRoomStep(page, calls);
  await page.route(`${API}${JOB_PATH}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "queued" } });
  });

  await openRoomStep(page, withProposal);
  await expect.poll(() => pathCalls(calls, JOB_PATH).length).toBe(1);
  await page.evaluate(() => {
    window.history.pushState({}, "", "/auth");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page).toHaveURL(/\/auth/);
  await page.clock.fastForward(10_000);
  expect(pathCalls(calls, JOB_PATH)).toHaveLength(1);
  expect(pathCalls(calls, CATALOG_PATH)).toHaveLength(0);
});
