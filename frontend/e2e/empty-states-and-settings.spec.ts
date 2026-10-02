/**
 * Spec §9 scenarios 7-9: empty screens explain instead of showing zeros,
 * and the DOM carries no emoji anywhere.
 */
import { expect, Page, test } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  JOB_ID,
  mockMapbox,
  OWNED_PROPERTY_ID,
  RecordedCall,
  recordCall,
  ROOM_TYPE_ID,
  seedBrowserState,
} from "./helpers";

/**
 * Every emoji block used anywhere near UI symbols, including the old bell,
 * stars (review scores), flag pairs (destinations) and clocks.
 * Deliberately does NOT cover bare ▶ (U+25B6) or ℹ (U+2139): those code
 * points are text-presentation by default. Their emoji-presentation forms
 * (▶️ ℹ️) are still caught by the \u{FE0F} variation-selector clause.
 */
const EMOJI_PATTERN =
  /[\u{1F000}-\u{1FAFF}\u{1F1E6}-\u{1F1FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{231A}-\u{231B}\u{23E9}-\u{23FA}\u{FE0F}\u{200D}]/u;

test("scenario 7: with no data the market snapshot explains instead of showing 0 €", async ({ page }) => {
  await seedBrowserState(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => route.fulfill({ json: [] }));

  await page.goto("/map");

  await expect(page.getByTestId("market-snapshot-empty")).toBeVisible();
  const sidebar = page.locator(".results-sidebar");
  await expect(sidebar).toContainText("Δεν υπάρχουν ακόμη δεδομένα αγοράς");
  await expect(sidebar).toContainText("Δεν έχετε τρέξει ακόμη αναζήτηση");
  await expect(sidebar).not.toContainText("0 €");
  await expect(sidebar).not.toContainText("€0");
});

/**
 * Mock a completed competitor search that put NO cards on the map, where the
 * The completed job carries the scraper's persisted result_summary. The only
 * /market/summary read left is the ordinary market strip; it must never be
 * used as an empty-reason probe.
 *
 * Round 6 (spec §3.4): the scraper no longer cuts rows by room category before
 * storage. Its first two stages are `single_rooms` and `capacity`, and
 * `emptiedBy` says which of them left nothing when `offersWritten` is 0.
 */
async function mockCompletedSearchWithEmptyMap(
  page: Page,
  { offersSeen, offersWritten = 0, emptiedBy = "single_rooms", scrapeRunsCount = 1, resultSummary }: {
    offersSeen: number;
    offersWritten?: number;
    emptiedBy?: "single_rooms" | "capacity";
    scrapeRunsCount?: number;
    resultSummary?: {
      version: number;
      rows_seen: number;
      filter_counts: Record<string, { before: number; after: number }>;
      rows_written: number;
    };
  },
): Promise<URLSearchParams[]> {
  const summaryQueries: URLSearchParams[] = [];
  await seedBrowserState(page);
  // Mandatory here, not cosmetic: these scenarios assert on the sidebar's
  // loading/empty states, and a map style that fails (or reaches the real
  // api.mapbox.com and fails there) drives status to "error" through setError,
  // which removes exactly the states under test.
  await mockMapbox(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [
        {
          id: ROOM_TYPE_ID,
          owned_property_id: OWNED_PROPERTY_ID,
          room_type: "Deluxe Δίκλινο Δωμάτιο με θέα στη Θάλασσα",
          room_type_category: "double",
          is_active: true,
        },
      ],
    }),
  );
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({
      json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] },
    }),
  );
  const restoredJob = {
    id: JOB_ID,
    owned_property_id: OWNED_PROPERTY_ID,
    job_type: "competitor_search",
    room_type_category: "double",
    destination: "Faliraki",
    raw_destination: "Faliraki, Rhodes",
    check_in: "2030-06-01",
    check_out: "2030-06-05",
    // Deliberately none of the app's own defaults (2 adults / 0 children /
    // 1 room / limit 8): a read that quietly fell back to them would still
    // look right otherwise.
    adults: 3,
    children: 1,
    rooms: 2,
    filters_payload: { limit: 12 },
    status: "completed",
    scrape_runs_count: scrapeRunsCount,
    result_summary: resultSummary ?? {
      version: 1,
      rows_seen: offersSeen,
      filter_counts: offersSeen > 0 && offersWritten === 0
        ? { [emptiedBy]: { before: offersSeen, after: 0 } }
        : {},
      rows_written: offersWritten,
    },
  };
  // seedBrowserState's unscoped keys are dead once workflow storage binds to
  // the auth subject, so the page finds this job through the LIST endpoint —
  // same handler answers the detail read with the object form.
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) =>
    route.fulfill({
      json: new URL(route.request().url()).pathname.endsWith("/scrape-jobs/")
        ? [restoredJob]
        : restoredJob,
    }),
  );
  // No cards on the map, whatever the reason.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/market/summary**`, async (route) => {
    const query = new URL(route.request().url()).searchParams;
    summaryQueries.push(query);
    return route.fulfill({
      json: {
        destination: "faliraki",
        check_in: "2030-06-01",
        check_out: "2030-06-05",
        total_records: offersWritten,
        total_hotels: Math.ceil(offersWritten / 2),
        price_min_eur: 0,
        price_max_eur: 0,
        price_avg_eur: 0,
        price_median_eur: 0,
        avg_review_score: 0,
        rooms_left_total: 0,
      },
    });
  });
  return summaryQueries;
}

test("a search whose offers were all single rooms says how many it found", async ({
  page,
}) => {
  // When a completed search returns rows that the scraper's first stage all
  // drops — Round 6 (spec §3.4) keeps every category but single rooms for a
  // party of two or more — the sidebar used to claim nothing was found at all:
  // a lie about a scrape that did return results. It must report the real
  // count, and what they were, instead.
  const summaryQueries = await mockCompletedSearchWithEmptyMap(page, { offersSeen: 6 });

  await page.goto("/map");

  const sidebar = page.locator(".results-sidebar");
  // "προσφορές", not "δωμάτια": total_records counts price offers, and one room
  // routinely sells under several of them (the live job: 8 rows, 4 rooms).
  await expect(sidebar).toContainText("Βρέθηκαν 6 προσφορές για μονόκλινα δωμάτια");
  await expect(sidebar).toContainText("όλες όμως για μονόκλινα δωμάτια");
  await expect(sidebar).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  await expect.poll(() => summaryQueries.length).toBe(1);
  expect(summaryQueries[0].has("room_type_category")).toBe(true);
  expect(summaryQueries[0].get("scrape_job_id")).toBe(JOB_ID);
});

test("the payload supplies the truthful title before ready without category probes", async ({ page }) => {
  // The capacity stage this time: every price was for fewer people than asked for.
  const summaryQueries = await mockCompletedSearchWithEmptyMap(page, { offersSeen: 4, emptiedBy: "capacity" });

  await page.goto("/map");

  const sidebar = page.locator(".results-sidebar");
  await expect(sidebar).toContainText("Βρέθηκαν 4 τιμές για λιγότερα άτομα από όσα ζητήσατε");
  await expect(sidebar).toContainText("όλες όμως για λιγότερα άτομα");
  await expect(sidebar).not.toContainText("μονόκλινα");
  await expect.poll(() => summaryQueries.length).toBe(1);
  expect(summaryQueries.every((query) => query.has("room_type_category"))).toBe(true);
});

test("a single dropped offer is reported in the singular", async ({ page }) => {
  await mockCompletedSearchWithEmptyMap(page, { offersSeen: 1 });

  await page.goto("/map");

  await expect(page.locator(".results-sidebar")).toContainText("Βρέθηκε 1 προσφορά για μονόκλινο δωμάτιο");
});

test("a single price for fewer people is reported in the singular", async ({ page }) => {
  await mockCompletedSearchWithEmptyMap(page, { offersSeen: 1, emptiedBy: "capacity" });

  await page.goto("/map");

  await expect(page.locator(".results-sidebar")).toContainText("Βρέθηκε 1 τιμή για λιγότερα άτομα από όσα ζητήσατε");
});

test("a search that really returned nothing keeps saying nothing was found", async ({ page }) => {
  // The counterpart truth: with zero offers of any kind there is no count to
  // report, so the plain wording must stay -- a stale or invented "N single
  // rooms" here would be the same lie in the other direction.
  await mockCompletedSearchWithEmptyMap(page, { offersSeen: 0 });

  await page.goto("/map");

  const sidebar = page.locator(".results-sidebar");
  await expect(sidebar).toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  await expect(sidebar).not.toContainText("μονόκλινα");
  await expect(sidebar).not.toContainText("λιγότερα άτομα");
});

test("a completed zero-row job restores from its summary even without a scrape run", async ({ page }) => {
  await mockCompletedSearchWithEmptyMap(page, { offersSeen: 3, scrapeRunsCount: 0 });

  await page.goto("/map");

  await expect(page.locator(".results-sidebar")).toContainText("Βρέθηκαν 3 προσφορές για μονόκλινα δωμάτια");
});

test("a later filter explains that comparable offers existed before it emptied the result", async ({ page }) => {
  await mockCompletedSearchWithEmptyMap(page, {
    offersSeen: 7,
    resultSummary: {
      version: 1,
      rows_seen: 7,
      // 7 seen, 6 not single, 5 fit the party: the count is what reached the meal filter.
      filter_counts: {
        single_rooms: { before: 7, after: 6 },
        capacity: { before: 6, after: 5 },
        meal: { before: 5, after: 0 },
      },
      rows_written: 0,
    },
  });

  await page.goto("/map");

  const sidebar = page.locator(".results-sidebar");
  // «Συγκρίσιμες», not «της κατηγορίας σας»: since Round 6 the scraper keeps
  // similar categories too, so the count is no longer of the owner's category.
  await expect(sidebar).toContainText("Βρέθηκαν 5 συγκρίσιμες προσφορές");
  await expect(sidebar).not.toContainText("της κατηγορίας σας");
  await expect(sidebar).toContainText("Καμία από αυτές δεν πέρασε τα υπόλοιπα φίλτρα");
  await expect(sidebar).not.toContainText("μονόκλινα");
  await expect(sidebar).not.toContainText("λιγότερα άτομα");
});

test("offers that were written mean the map was not emptied by the scraper's filters", async ({
  page,
}) => {
  // Markers drop hotels whose cheapest row has no coordinates, so an empty map
  // does NOT prove a scraper stage emptied it. With 2 offers written, "all cut
  // as single rooms" is false and must not be said — even though 7 were seen.
  await mockCompletedSearchWithEmptyMap(page, { offersSeen: 7, offersWritten: 2 });

  await page.goto("/map");

  const sidebar = page.locator(".results-sidebar");
  await expect(sidebar).toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  await expect(sidebar).not.toContainText("μονόκλινα");
});

test("scenario 8: the DOM contains no emoji", async ({ page }) => {
  await seedBrowserState(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));

  await page.goto("/map");
  await expect(page.locator("h1").first()).toBeVisible();
  await expect(page.getByTestId("market-snapshot-empty")).toBeVisible();

  const html = await page.evaluate(() => document.documentElement.outerHTML);
  expect(EMOJI_PATTERN.test(html)).toBe(false);
});

test("scenario 9: deleting the property requires typing its name first", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedBrowserState(page);
  // Subject-scoped seeds (resolveKey appends ":e2e-user"): prove deletion
  // clears property-scoped state, not just that the keys were never set.
  await page.addInitScript((jobId) => {
    window.localStorage.setItem("roomrate_selected_room_type_category:e2e-user", "double");
    window.localStorage.setItem("roomrate_last_competitor_job_id:e2e-user", jobId);
    window.localStorage.setItem("roomrate_property_raw_destination:e2e-user", "Faliraki, Rhodes");
    window.localStorage.setItem("roomrate_property_canonical_destination:e2e-user", "faliraki-gr");
  }, JOB_ID);
  // LIFO: wildcard first so the specific mocks after it win.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
  // Empirically required (red phase): the settings page's alert-rules panel
  // does `rules.set(await get<AlertRule[]>(...))` and `*ngFor`s over it. The
  // wildcard's `{}` is not an array, so NgForOf throws NG0900 while checking
  // that EARLIER section of the template -- since Angular's generated update
  // function walks the template top-to-bottom in one pass, the exception
  // aborts before ever reaching the property section's bindings below it,
  // and because rules() never changes, every later CD pass re-throws at the
  // same spot, so the property name/buttons this test needs never render.
  // /api/v1/schedule staying `{}` is harmless: nothing in the component
  // dereferences into its fields without the `?.` the code already uses.
  await page.route(`${API}/api/v1/notifications/rules`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ status: 204, body: "" });
  });
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));

  await page.goto("/settings");
  await page.getByRole("button", { name: "Διαγραφή καταλύματος" }).click();

  const confirmButton = page.getByRole("button", { name: "Οριστική διαγραφή" });
  await expect(confirmButton).toBeDisabled();
  await page.getByTestId("delete-confirm-input").fill("Wrong Name");
  await expect(confirmButton).toBeDisabled();
  await page.getByTestId("delete-confirm-input").fill("E2E Test Hotel");
  await expect(confirmButton).toBeEnabled();
  // MANDATORY under the /me cache: deleteOwnedProperty invalidates it, so the
  // guard's post-delete read on the /setup navigation is FRESH -- it must see
  // the property gone or it bounces /setup back to /map.
  await page.route(`${API}/api/v1/me`, (route) =>
    route.fulfill({
      json: { ...CURRENT_USER, onboarding_complete: false, owned_property_id: null, property_name: null, selected_room_type_category: null },
    }),
  );
  await confirmButton.click();

  // toHaveURL is a web-first assertion that polls; the plain array check
  // below is not. deleteProperty() only navigates to /setup AFTER its DELETE
  // await resolves, so waiting for the URL first guarantees the call is
  // already recorded -- asserting on `calls` before the URL settled raced
  // the in-flight DELETE and flaked under full-suite parallel load (observed:
  // 0 recorded calls under 7 workers, always 1 in isolation).
  await expect(page).toHaveURL(/\/setup/);
  expect(calls.filter((c) => c.method === "DELETE").length).toBe(1);
  // set(key, "") removes the item, so cleared reads back as null.
  expect(await page.evaluate(() => [
    window.localStorage.getItem("roomrate_selected_room_type_category:e2e-user"),
    window.localStorage.getItem("roomrate_last_competitor_job_id:e2e-user"),
    window.localStorage.getItem("roomrate_property_raw_destination:e2e-user"),
    window.localStorage.getItem("roomrate_property_canonical_destination:e2e-user"),
  ])).toEqual([null, null, null, null]);
});

test("change property from settings: the replace invalidates /me for the next navigation", async ({ page }) => {
  const CANDIDATE = {
    candidate_key: "villa-nea", display_name: "Villa Nea",
    booking_url: "https://www.booking.com/hotel/gr/villa-nea.html",
    city: "Faliraki", address: "Odos 1", country: "Greece", property_type: "villa",
    latitude: 36.34, longitude: 28.2, stars: null, review_score: 8.1, review_count: 40,
  };
  let meCalls = 0;
  await seedBrowserState(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    return route.fulfill({ json: CURRENT_USER });
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) =>
    route.fulfill({ json: [CANDIDATE] }),
  );
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}`, (route) =>
    route.fulfill({
      status: 202,
      json: { owned_property_id: OWNED_PROPERTY_ID, discovery_job: { id: JOB_ID, status: "queued", job_type: "owned_property_room_discovery" } },
    }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "completed", scrape_runs_count: 1 } }),
  );
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );

  await page.goto("/settings");
  await page.getByRole("button", { name: "Αλλαγή καταλύματος" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  const callsBeforeReplace = meCalls;
  // Change mode lands in "pick" with no proposal AND empty draft name/location
  // (those are only ever seeded during first-time sign-up, spec §8) -- the
  // step's own mount-time auto-search returns early on blank fields instead of
  // calling the API, so the candidate list starts empty. Drive its real
  // refine form (same locators as setup-wizard.spec.ts) to populate it.
  await page.locator('input[name="refineName"]').fill(CANDIDATE.display_name);
  await page.locator('input[name="refineLocation"]').fill(CANDIDATE.city);
  await page.getByRole("button", { name: "Αναζήτηση ξανά" }).click();
  await expect(page.getByTestId(`candidate-${CANDIDATE.candidate_key}`)).toBeVisible();
  await page.getByTestId(`candidate-${CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  // Sticky change-mode pin: onPropertyConfirmed must strip ?change=1 once the
  // replace commits, or a reload/back-nav at this URL forces step 1 again and
  // invites a second (paid) replaceOwnedProperty/discovery job.
  await expect(page).not.toHaveURL(/change=1/);
  await page.getByRole("button", { name: "Συνέχεια στον χάρτη" }).click();

  await expect(page).toHaveURL(/\/map/);
  // The PUT invalidated the cache, so the post-replace guard read is fresh.
  expect(meCalls).toBeGreaterThan(callsBeforeReplace);
});

test("settings: the property section shows a loading state instead of a false empty flash", async ({ page }) => {
  await seedBrowserState(page);
  // LIFO: wildcard first so the specific mocks after it win.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
  await page.route(`${API}/api/v1/notifications/rules`, (route) => route.fulfill({ json: [] }));
  // /me is deliberately slow: the property section must show its OWN loading
  // placeholder for this window, not the "no property" template gated on the
  // (fast) schedule load -- that would be a lie for however long /me takes.
  await page.route(`${API}/api/v1/me`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    return route.fulfill({ json: CURRENT_USER });
  });

  await page.goto("/settings");

  await expect(page.getByTestId("property-section-loading")).toBeVisible();
  await expect(page.getByText("Δεν υπάρχει συνδεδεμένο κατάλυμα")).toHaveCount(0);
  // toBeVisible polls (default 10s), so this waits out the rest of the delay.
  await expect(page.getByText("E2E Test Hotel")).toBeVisible();
  await expect(page.getByText("Δεν υπάρχει συνδεδεμένο κατάλυμα")).toHaveCount(0);
});

test("settings: the legal footer links to the privacy policy and the terms", async ({ page }) => {
  // Rendered, not followed: the claim is that both links exist with the right
  // Greek text and the right targets. Navigating would couple this spec to
  // /privacy and /terms being routed, which is a separate concern.
  await seedBrowserState(page);
  // LIFO: wildcard first so the specific mocks after it win. The rules route
  // must answer an array -- see the NG0900 note in scenario 9.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
  await page.route(`${API}/api/v1/notifications/rules`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));

  await page.goto("/settings");

  await expect(page.getByRole("link", { name: "Πολιτική Απορρήτου" })).toHaveAttribute("href", "/privacy");
  await expect(page.getByRole("link", { name: "Όροι Χρήσης" })).toHaveAttribute("href", "/terms");
});

test.describe("settings: the check hour is in Greek time", () => {
  // A browser far from Greece: the field must follow Europe/Athens itself,
  // not whatever zone the owner's laptop happens to be in.
  test.use({ timezoneId: "America/New_York" });

  test("the stored UTC hour is shown and saved as the Greek hour, across daylight saving", async ({ page }) => {
    const SCHEDULE = {
      account_id: "e2e-account", enabled: true, frequency_hours: 24, hour_utc: 5,
      lead_days: 30, nights: 3, adults: 2, children: 0, rooms: 1,
      consecutive_failures: 0, last_run_at: null,
    };
    const savedBodies: Array<Record<string, unknown>> = [];
    await seedBrowserState(page);
    // LIFO: wildcard first so the specific mocks after it win. The rules route
    // must answer an array -- see the NG0900 note in scenario 9.
    await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
    await page.route(`${API}/api/v1/notifications/rules`, (route) => route.fulfill({ json: [] }));
    await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
    await page.route(`${API}/api/v1/schedule`, (route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        savedBodies.push(body);
        return route.fulfill({ json: { ...SCHEDULE, ...body } });
      }
      return route.fulfill({ json: SCHEDULE });
    });
    const hourField = page.getByLabel("Ώρα ελέγχου (ώρα Ελλάδας)");
    const saveButton = page.getByRole("button", { name: "Αποθήκευση προγράμματος" });

    // Summer: Greece is UTC+3, so the stored 05:00 UTC is 08:00 Greek time.
    await page.clock.setFixedTime(new Date("2026-07-15T09:00:00Z"));
    await page.goto("/settings");
    await expect(hourField).toHaveValue("8");
    await saveButton.click();
    await expect(page.getByText("Η αυτόματη αναζήτηση ενεργοποιήθηκε. Θα βλέπετε νέες τιμές ανταγωνιστών σε κάθε έλεγχο.")).toBeVisible();
    expect(savedBodies.at(-1)?.["hour_utc"]).toBe(5);

    // Wrap-around: 01:00 Greek time is 22:00 UTC the previous day.
    await hourField.fill("1");
    await saveButton.click();
    await expect.poll(() => savedBodies.length).toBe(2);
    expect(savedBodies.at(-1)?.["hour_utc"]).toBe(22);
    await expect(hourField).toHaveValue("1");

    // Winter: Greece is UTC+2, so the same stored 05:00 UTC is 07:00.
    await page.clock.setFixedTime(new Date("2026-01-15T09:00:00Z"));
    await page.reload();
    await expect(hourField).toHaveValue("7");
    await saveButton.click();
    await expect.poll(() => savedBodies.length).toBe(3);
    expect(savedBodies.at(-1)?.["hour_utc"]).toBe(5);
  });
});
