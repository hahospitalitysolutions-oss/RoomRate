/**
 * «Απόδοση» shell (pages/performance/): the route, the header link, the tabs,
 * and the development fallback to public/mock/performance/*.json while an
 * endpoint does not exist yet (HTTP 404 «Not Found» from FastAPI).
 * The frontend team replaces this with the real page's specs (A10).
 */
import { expect, test } from "@playwright/test";

import { API, CURRENT_USER, seedBrowserState } from "./helpers";

test("the performance page opens from the header and runs on the samples until the API has the endpoint", async ({ page }) => {
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: { count: 0 } }));
  // No performance router on the backend yet: FastAPI's own 404.
  await page.route(`${API}/api/v1/performance/**`, (route) => route.fulfill({ status: 404, json: { detail: "Not Found" } }));

  await page.goto("/pricing");
  await page.getByRole("link", { name: "Απόδοση" }).click();

  await expect(page).toHaveURL(/\/performance$/);
  await expect(page.getByRole("heading", { name: "Πώς πάει το κατάλυμα" })).toBeVisible();
  for (const tab of ["Επισκόπηση", "Πληρότητα", "Δωμάτια", "Διαμονή & κρατήσεις", "Προβλέψεις"]) {
    await expect(page.getByRole("tab", { name: tab })).toBeVisible();
  }
  // public/mock/performance/status.json
  await expect(page.getByTestId("performance-shell")).toContainText("κρατήσεις");
});
