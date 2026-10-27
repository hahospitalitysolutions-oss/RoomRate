/**
 * «Απόδοση» A4–A8: every tab on the sample answers of public/mock/performance/,
 * the filters reaching the API, and the nulls the page must never show as 0
 * (missing room counts, closed months, no comparison, no history).
 */
import { readFileSync } from "node:fs";

import { expect, Page, Request, test } from "@playwright/test";

import { API, CURRENT_USER, seedBrowserState } from "./helpers";

const SEA_VIEW = "11111111-1111-4111-8111-000000000001";

function sample(name: string): any {
  return JSON.parse(readFileSync(`public/mock/performance/${name}.json`, "utf-8"));
}

/** Every performance call answers its sample, or the override for that endpoint. */
async function openPerformance(page: Page, url: string, overrides: Record<string, unknown> = {}): Promise<Request[]> {
  const calls: Request[] = [];
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/performance/**`, (route) => {
    calls.push(route.request());
    const name = new URL(route.request().url()).pathname.split("/performance/")[1];
    return route.fulfill({ json: name in overrides ? overrides[name] : sample(name) });
  });
  await page.goto(url);
  return calls;
}

function lastQuery(calls: Request[], endpoint: string): URLSearchParams {
  const call = [...calls].reverse().find((request) => new URL(request.url()).pathname.endsWith(`/${endpoint}`));
  return new URL(call!.url()).searchParams;
}

test("overview: KPI cards with the change from last year, the market median and the charts", async ({ page }) => {
  const calls = await openPerformance(page, "/performance");

  await expect(page.getByTestId("performance-kpi-occupancy")).toContainText("73,6%");
  await expect(page.getByTestId("performance-kpi-occupancy")).toContainText("+4,9 μον.");
  await expect(page.getByTestId("performance-kpi-adr")).toContainText("117,66 €");
  await expect(page.getByTestId("performance-kpi-adr")).toContainText("+5,6%");
  await expect(page.getByTestId("performance-kpi-adr")).toContainText("Διάμεσος αγοράς: 125,00 €");
  await expect(page.getByTestId("performance-kpi-revenue")).toContainText("191.314,00 €");

  const bookingsAdr = page.getByTestId("performance-chart-bookings-adr");
  await expect(bookingsAdr.getByRole("group")).toHaveAttribute("aria-label", /Αύγουστος 2026: Κρατήσεις 83, ADR 141,00\s€/);
  await bookingsAdr.getByRole("img", { name: /^Αύγουστος 2026/ }).hover();
  await expect(bookingsAdr.locator(".chart-tooltip")).toContainText("Αύγουστος 2026");
  await expect(bookingsAdr.locator(".chart-tooltip")).toContainText("141,00 €");

  await expect(page.getByTestId("performance-chart-channels")).toContainText("Booking.com");
  await expect(page.getByTestId("performance-chart-channel-share")).toContainText("58,0%");
  await expect(page.getByTestId("performance-chart-countries")).toContainText("Ελλάδα");

  // The view's filters are what the API is asked.
  const query = lastQuery(calls, "summary");
  expect(query.get("amounts")).toBe("gross");
  expect(query.get("compare")).toBe("last_year");
});

test("the filters reach every analysis call", async ({ page }) => {
  const calls = await openPerformance(page, "/performance");
  await expect(page.getByTestId("performance-kpi-occupancy")).toBeVisible();

  await page.getByTestId("performance-amounts-net").click();
  await expect(page.getByTestId("performance-net-hint")).toBeVisible();
  await expect.poll(() => lastQuery(calls, "summary").get("amounts")).toBe("net");
  expect(lastQuery(calls, "monthly").get("amounts")).toBe("net");

  await page.getByTestId("performance-filter-room").selectOption(SEA_VIEW);
  await expect.poll(() => lastQuery(calls, "summary").get("room_type_id")).toBe(SEA_VIEW);

  await page.getByTestId("performance-filter-compare").uncheck();
  await expect.poll(() => lastQuery(calls, "channels").get("compare")).toBe("none");
});

test("without room counts occupancy and RevPAR ask for them instead of showing 0%", async ({ page }) => {
  const summary = sample("summary");
  summary.kpis = { ...summary.kpis, occupancy_pct: null, revpar_eur: null, room_nights_available: null };
  summary.delta = { ...summary.delta, occupancy_pts: null, revpar_pct: null };
  const status = { ...sample("status"), inventory_complete: false };
  status.room_types = status.room_types.map((type: { rooms: number | null }, index: number) => (index === 3 ? { ...type, rooms: null } : type));
  await openPerformance(page, "/performance", { summary, status });

  const occupancy = page.getByTestId("performance-kpi-occupancy");
  await expect(occupancy).toContainText("Ορίστε τα δωμάτια ανά τύπο");
  await expect(occupancy).not.toContainText("0%");
  await expect(page.getByTestId("performance-kpi-revpar")).toContainText("Ορίστε τα δωμάτια ανά τύπο");
  await occupancy.getByRole("button", { name: "Ορίστε τα δωμάτια ανά τύπο" }).click();
  await expect(page.getByTestId("performance-rooms-panel")).toBeVisible();
});

test("without a comparison there is no change from last year", async ({ page }) => {
  await openPerformance(page, "/performance?compare=none", { summary: { ...sample("summary"), last_year: null, delta: null, market_median_eur: null } });

  await expect(page.getByTestId("performance-kpi-occupancy")).toContainText("73,6%");
  await expect(page.getByTestId("performance-kpi-occupancy")).not.toContainText("από πέρυσι");
  await expect(page.getByTestId("performance-kpi-adr")).not.toContainText("Διάμεσος αγοράς");
});

test("occupancy: closed months read «Κλειστό», and the next season's pace", async ({ page }) => {
  const monthly = sample("monthly");
  monthly.months.push({
    month: "2026-11",
    occupancy_pct: null,
    adr_eur: null,
    revpar_eur: null,
    revenue_eur: 0,
    bookings: 0,
    room_nights_sold: 0,
    room_nights_available: 0,
    room_nights_unsold: null,
    closed_days: 30,
    market_median_eur: null,
    last_year: null,
  });
  await openPerformance(page, "/performance?tab=occupancy", { monthly });

  const occupancy = page.getByTestId("performance-chart-occupancy");
  await expect(occupancy).toContainText("Κλειστό");
  await expect(occupancy.getByRole("group")).toHaveAttribute("aria-label", /Νοέμβριος 2026: Κλειστό/);
  await expect(page.getByTestId("performance-chart-nights")).toContainText("Κλειστό");
  await expect(page.getByTestId("performance-chart-adr-revpar")).toBeVisible();

  const pace = page.getByTestId("performance-pace");
  await expect(pace).toContainText("σεζόν 2027");
  await expect(page.getByTestId("performance-pickup")).toContainText("9 κρατήσεις · 38 νύχτες");
});

test("rooms: the table, and picking a type filters the whole page", async ({ page }) => {
  const calls = await openPerformance(page, "/performance?tab=rooms");

  const row = page.getByTestId(`performance-room-row-${SEA_VIEW}`);
  await expect(row).toContainText("79,0%");
  await expect(row).toContainText("87.967,00 €");
  await expect(page.getByTestId("performance-chart-room-bubble").getByRole("button", { name: /Σουίτα/ })).toBeVisible();

  await row.getByRole("button", { name: "Δίκλινο με θέα θάλασσα" }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("room")).toBe(SEA_VIEW);
  await expect(page.getByTestId("performance-filter-room")).toHaveValue(SEA_VIEW);
  // /room-types ignores the room filter: picking a type does not ask it again.
  expect(calls.filter((request) => request.url().includes("/room-types")).length).toBe(1);

  await page.getByTestId("performance-tab-overview").click();
  await expect.poll(() => lastQuery(calls, "summary").get("room_type_id")).toBe(SEA_VIEW);
});

test("stays: length of stay, booking window and the heatmap", async ({ page }) => {
  await openPerformance(page, "/performance?tab=stays");

  await expect(page.getByTestId("performance-kpi-avg-stay")).toContainText("4,2 νύχτες");
  await expect(page.getByTestId("performance-chart-length")).toContainText("4–5 νύχτες");
  await expect(page.getByTestId("performance-chart-lead")).toContainText("61+ ημέρες");
  const heatmap = page.getByTestId("performance-chart-heatmap");
  await expect(heatmap.getByRole("table")).toBeVisible();
  await expect(heatmap.getByRole("row", { name: /4–5 νύχτες/ })).toContainText("56");
  await expect(page.getByTestId("performance-chart-lead-trend")).toBeVisible();
});

test("forecast: the accuracy sentence, the target choice, and too little history", async ({ page }) => {
  const calls = await openPerformance(page, "/performance?tab=forecast");

  await expect(page.getByTestId("performance-forecast-accuracy")).toContainText("απείχαν κατά μέσο όρο 8%");
  await expect(page.getByTestId("performance-forecast").getByRole("group", { name: /Πρόβλεψη: Έσοδα/ })).toBeVisible();
  // No model choice for the owner.
  await expect(page.getByTestId("performance-forecast")).not.toContainText("SARIMA");

  await page.getByTestId("performance-forecast-target-bookings").click();
  await expect.poll(() => lastQuery(calls, "forecast").get("target")).toBe("bookings");
});

test("forecast: under 12 months of history the tab explains instead of drawing", async ({ page }) => {
  await openPerformance(page, "/performance?tab=forecast", { forecast: sample("forecast-insufficient") });

  await expect(page.getByTestId("performance-forecast-insufficient")).toContainText("Χρειάζονται 12 μήνες ιστορικού");
  await expect(page.getByTestId("performance-forecast-insufficient")).toContainText("6 μήνες");
  await expect(page.getByTestId("performance-forecast").locator(".chart-svg")).toHaveCount(0);
});

test("an error from an analysis call is shown as it comes", async ({ page }) => {
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/performance/**`, (route) => {
    const name = new URL(route.request().url()).pathname.split("/performance/")[1];
    return name === "status"
      ? route.fulfill({ json: sample("status") })
      : route.fulfill({ status: 400, json: { detail: "Η περίοδος είναι πάνω από 3 χρόνια." } });
  });
  await page.goto("/performance");

  await expect(page.getByTestId("performance-overview-error")).toHaveText("Η περίοδος είναι πάνω από 3 χρόνια.");
});
