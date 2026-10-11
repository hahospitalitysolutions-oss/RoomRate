/**
 * The «Ζήτηση πλοίων» panel on /pricing (GET /api/v1/demand/ferries): one
 * cell per arrival day coloured by its level, the tracked routes, the
 * form's check-in outlined, and the states before data exists.
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, OWNED_PROPERTY_ID, seedBrowserState } from "./helpers";

function day(travelDate: string, level: string, sailings = 8, changes: Partial<Record<string, number>> = {}) {
  return {
    travel_date: travelDate,
    sailings,
    availability_known: 0,
    sold_out: 0,
    min_price_eur: 46.5,
    accommodation_options: 44,
    options_change_pct: changes.options ?? null,
    price_change_pct: changes.price ?? null,
    level,
  };
}

const ROUTES = [
  { id: "r1", origin_port: "PIRAEUS", destination_port: "RODOS", status: "auto", sample_sailings: 2 },
  { id: "r2", origin_port: "KOS", destination_port: "RODOS", status: "auto", sample_sailings: 3 },
  { id: "r3", origin_port: "SYMI", destination_port: "RODOS", status: "disabled", sample_sailings: 6 },
];

async function mockPricing(page: Page, ferries: unknown): Promise<void> {
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/notifications/ws-ticket`, (route) =>
    route.fulfill({ json: { ticket: "e2e-one-time-ticket", expires_in_seconds: 60 } }),
  );
  await page.route(`${API}/api/v1/demand/ferries**`, (route) => {
    expect(new URL(route.request().url()).searchParams.get("owned_property_id")).toBe(OWNED_PROPERTY_ID);
    return route.fulfill({ json: ferries });
  });
}

test("ferry demand: a cell per arrival day, high ones marked, routes listed, disabled ones hidden", async ({ page }) => {
  await mockPricing(page, {
    canonical_destination: "faliraki",
    status: "ferry",
    island_port: "RODOS",
    port_distance_km: null,
    resolved_at: "2026-10-11T05:10:00Z",
    routes: ROUTES,
    days: [
      day("2026-10-15", "normal", 12, { options: 0, price: 0 }),
      day("2026-10-16", "high", 13, { options: -32, price: 19 }),
      day("2026-10-17", "high", 12, { options: -32, price: 19 }),
      day("2026-10-18", "low", 10, { options: 0, price: -14 }),
    ],
    observed_on: "2026-10-11",
  });

  await page.goto("/pricing");

  const panel = page.locator(".ferry-demand-panel");
  await expect(panel.getByRole("heading", { name: "Ζήτηση πλοίων" })).toBeVisible();
  await expect(panel).toContainText("προς RODOS");
  await expect(panel.getByText("PIRAEUS → RODOS")).toBeVisible();
  await expect(panel.getByText("SYMI → RODOS")).toHaveCount(0);
  const friday = panel.locator('[data-date="2026-10-16"]');
  await expect(friday).toHaveClass(/demand-high/);
  await expect(friday).toContainText("Παρ 16/10");
  await expect(friday).toContainText("Υψηλή");
  await expect(friday).toHaveAttribute("title", /θέσεις -32% · εισιτήριο \+19% — ζήτηση: Υψηλή/);
  await expect(panel.locator('[data-date="2026-10-18"]')).toContainText("Χαμηλή");
  await expect(panel.getByTestId("ferry-collecting")).toHaveCount(0);
  await expect(panel).toContainText("ενημέρωση 11/10");
});

test("ferry demand: the first days of collection say why there is no level yet", async ({ page }) => {
  await mockPricing(page, {
    canonical_destination: "faliraki", status: "ferry", island_port: "RODOS", port_distance_km: null,
    resolved_at: null, routes: ROUTES, days: [day("2026-10-15", "unknown"), day("2026-10-16", "unknown")],
    observed_on: "2026-10-11",
  });

  await page.goto("/pricing");

  await expect(page.getByTestId("ferry-collecting")).toContainText("μετά από 3 μέρες καθημερινής συλλογής");
  await expect(page.locator('[data-date="2026-10-16"]')).toHaveClass(/demand-unknown/);
});

test("ferry demand: a mainland destination and one not looked up yet each say so", async ({ page }) => {
  await mockPricing(page, {
    canonical_destination: "athina", status: "no_ferry", island_port: null, port_distance_km: null,
    resolved_at: null, routes: [], days: [], observed_on: null,
  });
  await page.goto("/pricing");
  await expect(page.getByTestId("ferry-none")).toBeVisible();
  await expect(page.getByTestId("ferry-days")).toHaveCount(0);

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockPricing(page, {
    canonical_destination: "faliraki", status: null, island_port: null, port_distance_km: null,
    resolved_at: null, routes: [], days: [], observed_on: null,
  });
  await page.goto("/pricing");
  await expect(page.getByTestId("ferry-pending")).toContainText("μέσα στην επόμενη ώρα");
});
