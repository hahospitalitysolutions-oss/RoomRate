import { expect, Page, test } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  JOB_ID,
  mockMapbox,
  OWNED_PROPERTY_ID,
  ROOM_TYPE_ID,
  seedBrowserState,
} from "./helpers";

const TWIN_ROOM_ID = "33333333-3333-3333-3333-3333333333aa";
const SUITE_ROOM_ID = "33333333-3333-3333-3333-3333333333bb";

async function mockComparableRestore(page: Page, selectedCategory: string, jobCategory: string) {
  const jobScopedQueries: URLSearchParams[] = [];
  await seedBrowserState(page);
  await page.addInitScript(({ selectedCategory, selectedRoomId, jobId }) => {
    window.localStorage.setItem("roomrate_selected_room_type_category:e2e-user", selectedCategory);
    window.localStorage.setItem("roomrate_selected_room_type_id:e2e-user", selectedRoomId);
    window.localStorage.setItem("roomrate_last_competitor_job_id:e2e-user", jobId);
  }, {
    selectedCategory,
    selectedRoomId: selectedCategory === "double" ? ROOM_TYPE_ID : TWIN_ROOM_ID,
    jobId: JOB_ID,
  });
  await mockMapbox(page);
  // Wildcard-first: all focused handlers below win by Playwright's LIFO rule.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({
    json: { ...CURRENT_USER, selected_room_type_category: selectedCategory },
  }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [
        { id: ROOM_TYPE_ID, room_type: "Double Room", room_type_category: "double", is_active: true },
        { id: TWIN_ROOM_ID, room_type: "Twin Room", room_type_category: "twin", is_active: true },
        { id: SUITE_ROOM_ID, room_type: "Suite", room_type_category: "suite", is_active: true },
      ],
    }),
  );
  const job = {
    id: JOB_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    job_type: "competitor_search",
    room_type_category: jobCategory,
    destination: "Faliraki",
    raw_destination: "Faliraki, Rhodes",
    check_in: "2030-06-01",
    check_out: "2030-06-05",
    adults: 2,
    children: 0,
    rooms: 1,
    filters_payload: { limit: 8 },
    status: "completed",
    scrape_runs_count: 1,
    result_summary: { version: 1, rows_seen: 1, filter_counts: {}, rows_written: 1 },
  };
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => route.fulfill({
    json: new URL(route.request().url()).pathname.endsWith("/scrape-jobs/") ? [job] : job,
  }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) => route.fulfill({
    json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: jobCategory, competitors: [] },
  }));
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => {
    jobScopedQueries.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/market/summary**`, (route) => {
    jobScopedQueries.push(new URL(route.request().url()).searchParams);
    return route.fulfill({
      json: { total_records: 0, total_hotels: 0, price_min_eur: 0, price_max_eur: 0,
        price_avg_eur: 0, price_median_eur: 0, avg_review_score: 0, rooms_left_total: 0 },
    });
  });
  return jobScopedQueries;
}

for (const [selectedCategory, jobCategory] of [["double", "twin"], ["twin", "double"]] as const) {
  test(`${selectedCategory} selection restores ${jobCategory} job without changing the selected room`, async ({ page }) => {
    const queries = await mockComparableRestore(page, selectedCategory, jobCategory);

    await page.goto("/map");

    await expect.poll(() => queries.filter((query) => query.get("scrape_job_id") === JOB_ID).length).toBe(2);
    expect(queries.every((query) => query.get("room_type_category") === jobCategory)).toBe(true);
    await expect(page.getByTestId("room-select")).toHaveValue(
      selectedCategory === "double" ? ROOM_TYPE_ID : TWIN_ROOM_ID,
    );
    const storedCategory = await page.evaluate(() =>
      window.localStorage.getItem("roomrate_selected_room_type_category:e2e-user"));
    expect(storedCategory).toBe(selectedCategory);
  });
}

test("suite job is not restored for a double selection", async ({ page }) => {
  const queries = await mockComparableRestore(page, "double", "suite");

  await page.goto("/map");

  await expect(page.locator(".results-sidebar")).toContainText("Δεν έχετε τρέξει ακόμη αναζήτηση");
  expect(queries.some((query) => query.get("scrape_job_id") === JOB_ID)).toBe(false);
});
