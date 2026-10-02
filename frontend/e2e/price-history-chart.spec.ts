/**
 * Queue §4.2: the market price-history chart.
 *
 * Pins the redesign's honesty rules: a padded y domain (no full-height
 * min-max stretch), a real y axis, date+time per run on x, the P25-P75 band
 * only where it is meaningful, a low-data panel instead of a chart for 1-2
 * runs (no trend is drawn through them), and Greek labels with hover AND keyboard readouts
 * that never gate a value (the table view carries every number).
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, OWNED_PROPERTY_ID, ROOM_TYPE_ID, seedBrowserState } from "./helpers";

// Every date/time assertion below is an absolute UTC instant rendered in local
// time, so the zone is pinned instead of inherited from the runner's machine.
test.use({ timezoneId: "Europe/Athens" });

const ROOM_TYPES = [
  {
    id: ROOM_TYPE_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Deluxe Δίκλινο Δωμάτιο με θέα στη Θάλασσα",
    room_type_category: "double",
    is_active: true,
  },
];

/** One scrape run: when it ran, and every competitor's cheapest nightly price. */
type RunFixture = { observedAt: string; prices: number[] };

/**
 * Build a /market/price-history payload from runs given OLDEST first.
 *
 * The API numbers runs with run_index 1 = newest, so the indices are assigned
 * backwards here — a component that sorted by index instead of by observed_at
 * would draw this fixture reversed.
 */
function priceHistory(runs: RunFixture[]): unknown {
  const points = runs.flatMap((run, runPosition) =>
    run.prices.map((price, hotelPosition) => ({
      run_index: runs.length - runPosition,
      observed_at: run.observedAt,
      property_id: null,
      hotel_name: `Hotel ${hotelPosition + 1}`,
      min_price_eur: price,
    })),
  );
  return {
    canonical_destination: "faliraki",
    // Deliberately not the page's own default stay window.
    check_in: "2026-09-14",
    check_out: "2026-09-19",
    points,
  };
}

/**
 * Four runs of TWO competitors each: medians 100, 104, 96, 108.
 *
 * Two prices are too few for quartiles, so no band is drawn and the y domain
 * is the medians alone — which is what makes this fixture able to see the
 * padding. (With a band in play its quartiles widen the domain and would keep
 * the dots off the edges even under full-stretch scaling.)
 */
const BANDLESS_RUNS: RunFixture[] = [
  { observedAt: "2026-07-01T05:00:00Z", prices: [88, 112] },
  { observedAt: "2026-07-08T05:00:00Z", prices: [92, 116] },
  { observedAt: "2026-07-15T05:00:00Z", prices: [84, 108] },
  { observedAt: "2026-07-22T05:00:00Z", prices: [96, 120] },
];

/** Five weekly runs, four competitors each: medians 100, 104, 96, 108, 102. */
const FIVE_RUNS: RunFixture[] = [
  { observedAt: "2026-07-01T05:00:00Z", prices: [88, 96, 104, 132] },
  { observedAt: "2026-07-08T05:00:00Z", prices: [92, 100, 108, 140] },
  { observedAt: "2026-07-15T05:00:00Z", prices: [84, 92, 100, 128] },
  { observedAt: "2026-07-22T05:00:00Z", prices: [96, 104, 112, 144] },
  { observedAt: "2026-07-29T05:00:00Z", prices: [90, 98, 106, 134] },
];

async function mockPricingPage(page: Page, series: unknown): Promise<void> {
  await seedBrowserState(page);
  // The bell opens /ws/alerts on load; accept and ignore it.
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: ROOM_TYPES }),
  );
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) =>
    route.fulfill({ json: { count: 0 } }),
  );
  await page.route(`${API}/api/v1/notifications/ws-ticket`, (route) =>
    route.fulfill({ json: { ticket: "e2e-one-time-ticket", expires_in_seconds: 60 } }),
  );
  await page.route(`${API}/api/v1/market/price-history**`, (route) => route.fulfill({ json: series }));
}

test("the y axis is padded so no run sits pinned to the top or bottom edge", async ({ page }) => {
  await mockPricingPage(page, priceHistory(BANDLESS_RUNS));

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");
  await expect(chart.locator("circle.history-dot")).toHaveCount(4);
  await expect(chart.locator("polygon.history-band")).toHaveCount(0);

  // 2-3 ticks carry the scale (the plan's rule) instead of a bare line.
  const tickCount = await chart.locator("text.history-y-tick").count();
  expect(tickCount).toBeGreaterThanOrEqual(2);
  expect(tickCount).toBeLessThanOrEqual(3);

  // The hit bands span exactly the plot area, so they carry its bounds.
  const plotTop = Number(await chart.locator("rect.history-hit").first().getAttribute("y"));
  const plotHeight = Number(await chart.locator("rect.history-hit").first().getAttribute("height"));
  const dotYs = await chart
    .locator("circle.history-dot")
    .evaluateAll((dots) => dots.map((dot) => Number(dot.getAttribute("cy"))));

  expect(Math.min(...dotYs)).toBeGreaterThan(plotTop);
  expect(Math.max(...dotYs)).toBeLessThan(plotTop + plotHeight);
  // The old full-stretch scaling made every wobble look extreme: min and max
  // landed on the plot edges, spending 100% of the height on any spread.
  expect(Math.max(...dotYs) - Math.min(...dotYs)).toBeLessThan(plotHeight * 0.85);
});

test("three or more runs draw the trend line and the P25-P75 band", async ({ page }) => {
  await mockPricingPage(page, priceHistory(FIVE_RUNS));

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");

  await expect(chart.locator("polyline.history-line")).toHaveCount(1);
  await expect(chart.locator("polygon.history-band")).toHaveCount(1);

  // The band widens the domain, so its own edges are what the padding has to
  // keep off the plot bounds here.
  const plotTop = Number(await chart.locator("rect.history-hit").first().getAttribute("y"));
  const plotHeight = Number(await chart.locator("rect.history-hit").first().getAttribute("height"));
  const bandYs = (await chart.locator("polygon.history-band").first().getAttribute("points"))!
    .split(" ")
    .map((pair) => Number(pair.split(",")[1]));
  expect(Math.min(...bandYs)).toBeGreaterThan(plotTop);
  expect(Math.max(...bandYs)).toBeLessThan(plotTop + plotHeight);

  await expect(page.getByText("Εύρος P25–P75 ανταγωνιστών")).toBeVisible();
  // Three runs and up are a chart, never the low-data panel.
  await expect(page.getByTestId("history-low-data")).toHaveCount(0);
});

test("a run with fewer than three competitors breaks the band instead of faking it", async ({
  page,
}) => {
  const withThinRun = FIVE_RUNS.map((run, index) =>
    index === 2 ? { ...run, prices: [84, 128] } : run,
  );
  await mockPricingPage(page, priceHistory(withThinRun));

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");

  // Quartiles of two prices are not a spread: the band stops before that run
  // and restarts after it rather than interpolating across the gap.
  await expect(chart.locator("circle.history-dot")).toHaveCount(5);
  await expect(chart.locator("polygon.history-band")).toHaveCount(2);
});

test("two runs replace the chart with the low-data panel, which still names the spread", async ({
  page,
}) => {
  await mockPricingPage(
    page,
    priceHistory([
      { observedAt: "2026-07-01T05:00:00Z", prices: [88, 96, 104, 132] },
      { observedAt: "2026-07-29T05:00:00Z", prices: [96, 104, 112, 144] },
    ]),
  );

  await page.goto("/pricing");
  const panel = page.getByTestId("history-low-data");

  // Two dots cannot carry a trend, so no chart (and no line through them) is drawn.
  await expect(panel).toContainText("Χρειάζονται αναζητήσεις σε διαφορετικές ημέρες για να φανεί τάση.");
  await expect(panel.getByText("2 αναζητήσεις · εύρος 100 € – 108 €")).toBeVisible();
  await expect(page.locator(".history-svg")).toHaveCount(0);
});

test("a near-flat history stays near-flat instead of filling the canvas", async ({ page }) => {
  await mockPricingPage(
    page,
    priceHistory([
      { observedAt: "2026-07-01T05:00:00Z", prices: [99.5, 100.5] },
      { observedAt: "2026-07-08T05:00:00Z", prices: [99.9, 100.9] },
      { observedAt: "2026-07-15T05:00:00Z", prices: [99.7, 100.7] },
    ]),
  );

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");
  await expect(chart.locator("circle.history-dot")).toHaveCount(3);

  // 40 cents of movement gets an absolute window, not 15% of almost nothing:
  // round ticks still exist and stay distinct...
  await expect(chart.locator("text.history-y-tick")).toHaveText(["95 €", "100 €", "105 €"]);
  // ...and the dots read as the flat line they are.
  const plotHeight = Number(await chart.locator("rect.history-hit").first().getAttribute("height"));
  const dotYs = await chart
    .locator("circle.history-dot")
    .evaluateAll((dots) => dots.map((dot) => Number(dot.getAttribute("cy"))));
  expect(Math.max(...dotYs) - Math.min(...dotYs)).toBeLessThan(plotHeight * 0.15);
});

test("a price label that would collide with a placed one is dropped, not stacked", async ({
  page,
}) => {
  // Eight runs put adjacent dots 61 units apart, and the dearest run sits next
  // to the newest at almost the same height: labelling both would overlap.
  await mockPricingPage(
    page,
    priceHistory(
      [100, 103, 106, 109, 112, 116, 118, 115].map((median, index) => ({
        observedAt: `2026-07-0${index + 1}T05:00:00Z`,
        prices: [median - 12, median + 12],
      })),
    ),
  );

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");
  await expect(chart.locator("circle.history-dot")).toHaveCount(8);

  // Newest and oldest are placed; the dearest run loses its label to the veto
  // rather than being nudged into its neighbour.
  await expect(chart.locator("text.history-value")).toHaveText(["100 €", "115 €"]);
  await page.getByText("Πίνακας τιμών").click();
  await expect(page.locator(".history-table tbody tr").nth(6)).toContainText("118 €");
});

test("a single run is the low-data panel with its median, never a chart", async ({ page }) => {
  await mockPricingPage(
    page,
    priceHistory([{ observedAt: "2026-07-29T05:00:00Z", prices: [96, 104, 112, 144] }]),
  );

  await page.goto("/pricing");
  const panel = page.getByTestId("history-low-data");

  await expect(panel).toContainText("Χρειάζονται αναζητήσεις σε διαφορετικές ημέρες για να φανεί τάση.");
  await expect(panel.getByText("1 αναζήτηση · διάμεσος 108 €")).toBeVisible();
  await expect(page.locator(".history-svg")).toHaveCount(0);
});

test("every run carries its date and time on the x axis", async ({ page }) => {
  await mockPricingPage(page, priceHistory(FIVE_RUNS));

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");

  await expect(chart.locator("text.history-x-tick-date")).toHaveText([
    "01/07",
    "08/07",
    "15/07",
    "22/07",
    "29/07",
  ]);
  // 05:00 UTC is 08:00 in the pinned zone — the run's local clock time, not
  // the raw timestamp.
  await expect(chart.locator("text.history-x-tick-time").first()).toHaveText("08:00");
});

test("hover and keyboard focus open the same Greek readout", async ({ page }) => {
  await mockPricingPage(page, priceHistory(FIVE_RUNS));

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");
  await expect(chart.locator("circle.history-dot")).toHaveCount(5);
  const tooltip = page.locator(".history-tooltip");
  await expect(tooltip).toHaveCount(0);

  // Second run: 104 € is NOT one of the directly labelled dots, so this is the
  // hover layer doing real work.
  await chart.locator("rect.history-hit").nth(1).hover();
  await expect(tooltip).toContainText("104 €");
  await expect(tooltip).toContainText("Διάμεσος");
  await expect(tooltip).toContainText("P25–P75");

  // Keyboard reaches the same readout without a pointer.
  await chart.locator("rect.history-hit").nth(4).focus();
  await expect(tooltip).toContainText("102 €");
  // A screen reader gets the whole readout from the focused band itself.
  await expect(chart.locator("rect.history-hit").nth(4)).toHaveAttribute(
    "aria-label",
    // \s, not a literal space: el-GR currency joins the amount to € with NBSP.
    /διάμεσος 102\s€.*P25–P75 96\s€/,
  );
});

test("the table view carries every value, including the ones the chart does not label", async ({
  page,
}) => {
  await mockPricingPage(page, priceHistory(FIVE_RUNS));

  await page.goto("/pricing");
  await expect(page.locator(".history-svg circle.history-dot")).toHaveCount(5);

  const table = page.locator(".history-table");
  await expect(table.locator("tbody tr")).toHaveCount(5);
  await page.getByText("Πίνακας τιμών").click();
  await expect(table.locator("tbody tr").nth(1)).toBeVisible();
  await expect(table.locator("tbody tr").nth(1)).toContainText("104 €");
});

test("only the most recent twelve runs are plotted, and the summary says so", async ({ page }) => {
  const manyRuns: RunFixture[] = Array.from({ length: 14 }, (_, index) => ({
    observedAt: `2026-07-${String(index + 1).padStart(2, "0")}T05:00:00Z`,
    prices: [88 + index, 96 + index, 104 + index, 132 + index],
  }));
  await mockPricingPage(page, priceHistory(manyRuns));

  await page.goto("/pricing");
  const chart = page.locator(".history-svg");

  await expect(chart.locator("circle.history-dot")).toHaveCount(12);
  await expect(page.getByText("Τελευταίες 12 από 14 αναζητήσεις")).toBeVisible();
  // A number on every dot would be unreadable at this density.
  const labelCount = await chart.locator("text.history-value").count();
  expect(labelCount).toBeLessThanOrEqual(4);
});
