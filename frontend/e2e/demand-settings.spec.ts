/**
 * «Ζήτηση νησιού» in /settings (GET/PUT /api/v1/demand/settings, POST
 * /api/v1/demand/collect): the owner switches ferries and flights, picks the
 * Greek hour of the daily collection and asks for one now.
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, OWNED_PROPERTY_ID, seedBrowserState } from "./helpers";

const RHODES = {
  owned_property_id: OWNED_PROPERTY_ID,
  ferries_enabled: true,
  flights_enabled: true,
  hour_local: 8,
  timezone: "Europe/Athens",
  island: "Ρόδος",
  airport_iata: "RHO",
  ferries_available: true,
  flights_available: true,
  collect_requested_at: null,
  collection_pending: false,
  ferries_last_run: { started_at: "2026-10-12T05:00:10Z", finished_at: "2026-10-12T05:03:40Z", saved: 108 },
  flights_last_run: null,
  next_collection_at: "2026-10-13T08:00:00+03:00",
};

type Calls = { puts: Array<Record<string, unknown>>; collects: number };

async function mockSettings(page: Page, demand: Record<string, unknown>, collect?: Record<string, unknown>): Promise<Calls> {
  const calls: Calls = { puts: [], collects: 0 };
  await seedBrowserState(page);
  // LIFO: wildcard first so the specific mocks after it win.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
  await page.route(`${API}/api/v1/notifications/rules`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/demand/settings**`, (route) => {
    expect(new URL(route.request().url()).searchParams.get("owned_property_id")).toBe(OWNED_PROPERTY_ID);
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      calls.puts.push(body);
      return route.fulfill({ json: { ...demand, ...body } });
    }
    return route.fulfill({ json: demand });
  });
  await page.route(`${API}/api/v1/demand/collect**`, (route) => {
    calls.collects += 1;
    return route.fulfill({
      json: collect ?? {
        queued: true,
        message: "Η συλλογή ξεκινά μέσα σε λίγα λεπτά.",
        settings: { ...demand, collection_pending: true, collect_requested_at: "2026-10-12T09:00:00Z" },
      },
    });
  });
  return calls;
}

test("island demand: switches, the Greek hour, save and the last run", async ({ page }) => {
  const calls = await mockSettings(page, RHODES);
  await page.clock.setFixedTime(new Date("2026-10-12T09:00:00Z"));

  await page.goto("/settings");

  const panel = page.getByTestId("demand-settings");
  await expect(panel.getByText("Πλοία προς το νησί (Ρόδος)", { exact: true })).toBeVisible();
  await expect(panel.getByText("Πτήσεις στο αεροδρόμιο RHO", { exact: true })).toBeVisible();
  await expect(panel.getByTestId("demand-hour")).toHaveValue("8");
  await expect(panel.getByTestId("demand-status")).toContainText("108 νέα δεδομένα");
  await expect(panel.getByTestId("demand-status")).toContainText("Δεν έχει γίνει ακόμη");
  await expect(panel.getByTestId("demand-next")).toHaveText("Αύριο 08:00");

  await panel.getByTestId("demand-flights").uncheck();
  await panel.getByTestId("demand-hour").fill("6");
  await panel.getByRole("button", { name: "Αποθήκευση", exact: true }).click();

  await expect(panel.getByText("Αποθηκεύτηκε. Η συλλογή γίνεται κάθε μέρα στις 06:00.")).toBeVisible();
  expect(calls.puts).toEqual([{ ferries_enabled: true, flights_enabled: false, hour_local: 6 }]);
});

test("island demand: «Συλλογή τώρα» queues once and says so", async ({ page }) => {
  const calls = await mockSettings(page, RHODES);

  await page.goto("/settings");
  const panel = page.getByTestId("demand-settings");
  await panel.getByTestId("demand-collect-now").click();

  await expect(panel.getByText("Η συλλογή ξεκινά μέσα σε λίγα λεπτά.")).toBeVisible();
  await expect(panel.getByTestId("demand-collect-now")).toBeDisabled();
  await expect(panel.getByTestId("demand-next")).toHaveText("Σε λίγα λεπτά");
  expect(calls.collects).toBe(1);
});

test("island demand: already collected today is a message, not an error", async ({ page }) => {
  await mockSettings(page, RHODES, {
    queued: false,
    message: "Η συλλογή έγινε σήμερα στις 08:03. Η επόμενη αύριο στις 08:00.",
    settings: RHODES,
  });

  await page.goto("/settings");
  const panel = page.getByTestId("demand-settings");
  await panel.getByTestId("demand-collect-now").click();

  const feedback = panel.getByText("Η συλλογή έγινε σήμερα στις 08:03");
  await expect(feedback).toBeVisible();
  await expect(feedback).not.toHaveClass(/save-feedback-error/);
});

test("island demand: a mainland property is told it does not apply", async ({ page }) => {
  await mockSettings(page, {
    ...RHODES, island: null, airport_iata: null, ferries_available: false, flights_available: false,
    ferries_last_run: null, next_collection_at: null,
  });

  await page.goto("/settings");

  const panel = page.getByTestId("demand-settings");
  await expect(panel.getByTestId("demand-not-island")).toBeVisible();
  await expect(panel.getByTestId("demand-collect-now")).toHaveCount(0);
});

test("island demand: an island without an airport shows only the ferries", async ({ page }) => {
  await mockSettings(page, { ...RHODES, island: "Σύμη", airport_iata: null, flights_available: false });

  await page.goto("/settings");

  const panel = page.getByTestId("demand-settings");
  await expect(panel.getByTestId("demand-ferries")).toBeVisible();
  await expect(panel.getByTestId("demand-flights")).toHaveCount(0);
  await expect(panel).toContainText("τα πλοία προς το νησί σας (Σύμη): όταν γεμίζουν");
});
