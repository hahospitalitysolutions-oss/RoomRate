/**
 * «Απόδοση» A2: the tabs and the filter bar live in the URL
 * (`?tab=&from=&to=&room=&amounts=&compare=`), so a link, a reload and
 * back/forward open the same view; the empty state before any upload and the
 * notice while room counts are missing. /status answers from the samples in
 * public/mock/performance/.
 */
import { readFileSync } from "node:fs";

import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, seedBrowserState } from "./helpers";

const MOCK = "public/mock/performance";
const SEA_VIEW = "11111111-1111-4111-8111-000000000001";
/** The default period: the newest season of status.json (2027, 2026, 2025) that has started. */
const DEFAULT_SEASON = [2027, 2026, 2025].find((season) => season <= new Date().getFullYear()) ?? 2027;

async function openPerformance(page: Page, url: string, statusFile = "status.json", patch: object = {}): Promise<void> {
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: { count: 0 } }));
  const status = JSON.parse(readFileSync(`${MOCK}/${statusFile}`, "utf-8"));
  await page.route(`${API}/api/v1/performance/status**`, (route) => route.fulfill({ json: { ...status, ...patch } }));
  await page.goto(url);
}

function query(page: Page): URLSearchParams {
  return new URL(page.url()).searchParams;
}

test("the filters write the view to the URL and a reload opens the same view", async ({ page }) => {
  await openPerformance(page, "/performance?mock=0");

  // Default: the newest season that has started, gross, compared with last year.
  await expect(page.getByTestId("performance-filter-from")).toHaveValue(`${DEFAULT_SEASON}-01-01`);
  await expect(page.getByTestId("performance-filter-to")).toHaveValue(`${DEFAULT_SEASON}-12-31`);
  await expect(page.getByTestId("performance-amounts-gross")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("performance-filter-compare")).toBeChecked();
  await expect(page.getByRole("tab", { name: "Επισκόπηση" })).toHaveAttribute("aria-selected", "true");

  await page.getByTestId("performance-filter-period").selectOption("2025");
  await expect.poll(() => query(page).get("from")).toBe("2025-01-01");
  expect(query(page).get("to")).toBe("2025-12-31");

  await page.getByTestId("performance-filter-room").selectOption(SEA_VIEW);
  await expect.poll(() => query(page).get("room")).toBe(SEA_VIEW);

  await page.getByTestId("performance-amounts-net").click();
  await expect.poll(() => query(page).get("amounts")).toBe("net");

  await page.getByTestId("performance-filter-compare").uncheck();
  await expect.poll(() => query(page).get("compare")).toBe("none");

  await page.getByRole("tab", { name: "Δωμάτια" }).click();
  await expect.poll(() => query(page).get("tab")).toBe("rooms");
  // Unrelated params (e.g. mock) survive every change.
  expect(query(page).get("mock")).toBe("0");

  await page.reload();
  await expect(page.getByRole("tab", { name: "Δωμάτια" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("performance-tabpanel-rooms")).toBeAttached();
  await expect(page.getByTestId("performance-filter-from")).toHaveValue("2025-01-01");
  await expect(page.getByTestId("performance-filter-room")).toHaveValue(SEA_VIEW);
  await expect(page.getByTestId("performance-amounts-net")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("performance-filter-compare")).not.toBeChecked();

  // Back undoes the tab change, through the URL.
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Επισκόπηση" })).toHaveAttribute("aria-selected", "true");
});

test("a custom period is kept, and a reversed one is refused with a message", async ({ page }) => {
  await openPerformance(page, "/performance");

  await page.getByTestId("performance-filter-from").fill("2026-06-01");
  await page.getByTestId("performance-filter-to").fill("2026-08-31");
  await expect.poll(() => query(page).get("to")).toBe("2026-08-31");
  expect(query(page).get("from")).toBe("2026-06-01");
  await expect(page.getByTestId("performance-filter-period")).toHaveValue("custom");

  await page.getByTestId("performance-filter-from").fill("2026-09-15");
  await expect(page.getByTestId("performance-filter-date-error")).toContainText("πριν από το τέλος");
  expect(query(page).get("from")).toBe("2026-06-01");
});

test("invalid values in the URL fall back to the defaults", async ({ page }) => {
  await openPerformance(page, "/performance?tab=nope&from=2026-02-30&to=2026-03-01&room=unknown&amounts=x&compare=maybe");

  await expect(page.getByRole("tab", { name: "Επισκόπηση" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("performance-filter-from")).toHaveValue(`${DEFAULT_SEASON}-01-01`);
  await expect(page.getByTestId("performance-filter-room")).toHaveValue("");
  await expect(page.getByTestId("performance-amounts-gross")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("performance-filter-compare")).toBeChecked();
});

test("the tabs follow the arrow keys", async ({ page }) => {
  await openPerformance(page, "/performance");

  await page.getByRole("tab", { name: "Επισκόπηση" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Πληρότητα" })).toBeFocused();
  await expect.poll(() => query(page).get("tab")).toBe("occupancy");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await expect.poll(() => query(page).get("tab")).toBe("forecast");
});

test("before any upload the page offers the upload and the template, without filters or tabs", async ({ page }) => {
  await openPerformance(page, "/performance", "status-empty.json");

  const empty = page.getByTestId("performance-empty");
  await expect(empty).toContainText("Δεν υπάρχουν ακόμη κρατήσεις");
  await expect(empty.getByRole("button", { name: "Ανέβασμα κρατήσεων" })).toBeVisible();
  await expect(empty.getByRole("button", { name: "Κατέβασε το πρότυπο" })).toBeVisible();
  await expect(page.getByTestId("performance-filters")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);

  await empty.getByRole("button", { name: "Ανέβασμα κρατήσεων" }).click();
  await expect(page.getByTestId("performance-upload-panel")).toBeVisible();
});

test("the template downloads as RoomRate_kratiseis.xlsx", async ({ page }) => {
  await openPerformance(page, "/performance", "status-empty.json");
  await page.route(`${API}/api/v1/performance/template.xlsx**`, (route) =>
    route.fulfill({
      body: "xlsx",
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }));

  const download = page.waitForEvent("download");
  await page.getByTestId("performance-empty").getByRole("button", { name: "Κατέβασε το πρότυπο" }).click();
  expect((await download).suggestedFilename()).toBe("RoomRate_kratiseis.xlsx");
});

test("missing room counts raise the notice that leads to the rooms form", async ({ page }) => {
  await openPerformance(page, "/performance", "status.json", {
    inventory_complete: false,
    room_types: [
      { id: SEA_VIEW, label: "Δίκλινο με θέα θάλασσα", rooms: 5 },
      { id: "11111111-1111-4111-8111-000000000004", label: "Σουίτα", rooms: null },
    ],
  });

  const notice = page.getByTestId("performance-inventory-notice");
  await expect(notice).toContainText("Ορίστε τα δωμάτια ανά τύπο");
  await expect(notice).toContainText("Λείπει για: Σουίτα.");
  await notice.getByRole("button", { name: "Ορισμός δωματίων" }).click();
  await expect(page.getByTestId("performance-rooms-panel")).toBeVisible();
});

test("an API error is shown as it comes", async ({ page }) => {
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/performance/status**`, (route) =>
    route.fulfill({ status: 404, json: { detail: "Δεν βρέθηκε το κατάλυμα." } }));

  await page.goto("/performance");
  await expect(page.getByTestId("performance-error")).toHaveText("Δεν βρέθηκε το κατάλυμα.");
});
