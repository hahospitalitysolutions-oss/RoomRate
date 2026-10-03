/**
 * Manual walkthrough of the REAL app against the REAL backend.
 *
 * Not a regression test — a driver that screenshots the live UI. The only
 * thing faked is the Neon Auth browser session (a signed-in user's password is
 * not available here); every /api/v1 call is proxied to the running FastAPI
 * with the internal API key + account header, so the pages render genuine
 * database rows.
 *
 * Run with:  npx playwright test e2e/live-walkthrough.spec.ts
 * Requires:  uvicorn on 127.0.0.1:8000 and ROOMRATE_E2E_API_KEY/ACCOUNT_ID env.
 */

import { expect, Page, test } from "@playwright/test";

import { mockNeonAuth } from "./helpers";

const API = "http://127.0.0.1:8000";
const INTERNAL_KEY = process.env.ROOMRATE_E2E_API_KEY ?? "";
const ACCOUNT_ID = process.env.ROOMRATE_E2E_ACCOUNT_ID ?? "";
const SHOTS = "../output/walkthrough";

test.skip(!INTERNAL_KEY || !ACCOUNT_ID, "needs ROOMRATE_E2E_API_KEY + ROOMRATE_E2E_ACCOUNT_ID");

async function signedInWithLiveBackend(page: Page): Promise<void> {
  // Neon Auth itself is never contacted; the mocked session is enough for the guard.
  await mockNeonAuth(page);

  // Swap the (fake) browser bearer token for the internal key so the real API
  // answers with this account's real rows.
  await page.route(`${API}/**`, async (route) => {
    const request = route.request();
    const headers = {
      ...request.headers(),
      "x-api-key": INTERNAL_KEY,
      "x-roomrate-account-id": ACCOUNT_ID,
    };
    delete headers["authorization"];
    await route.continue({ headers });
  });
}

test("walk the app as a signed-in user", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await signedInWithLiveBackend(page);

  // --- MAP -----------------------------------------------------------------
  await page.goto("/map");
  await expect(page.locator(".competitor-card").first()).toBeVisible({ timeout: 30_000 });
  const competitorCount = await page.locator(".competitor-card").count();
  const headerCount = await page.locator(".header-actions span").first().innerText();
  await page.screenshot({ path: `${SHOTS}/1-map.png`, fullPage: false });

  // Open the filters rail so the search controls are visible in the shot.
  await page.getByRole("button", { name: "Φίλτρα" }).click();
  await expect(page.getByText("Φίλτρα αναζήτησης")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/2-map-filters.png` });

  // --- PRICING -------------------------------------------------------------
  await page.getByRole("link", { name: "Τιμολόγηση" }).click();
  await expect(page.getByText("Ιστορικό τιμών αγοράς")).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${SHOTS}/3-pricing-history.png` });

  await page.getByRole("button", { name: "Λήψη σύστασης" }).click();
  // Either a recommendation card or the explicit "not enough data" panel.
  await expect(
    page.locator(".recommendation-card, .alert-error").first(),
  ).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: `${SHOTS}/4-pricing-recommendation.png`, fullPage: true });

  // --- SETTINGS ------------------------------------------------------------
  await page.getByRole("link", { name: "Ρυθμίσεις" }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/5-settings.png`, fullPage: true });

  // --- NOTIFICATIONS -------------------------------------------------------
  await page.locator(".bell-button").click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/6-notifications.png` });

  console.log(`WALKTHROUGH competitors=${competitorCount} header="${headerCount}"`);
  console.log(`WALKTHROUGH consoleErrors=${consoleErrors.length}`);
  for (const error of consoleErrors.slice(0, 8)) {
    console.log(`  console.error: ${error.slice(0, 200)}`);
  }
});
