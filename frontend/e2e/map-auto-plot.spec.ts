/**
 * Queue §4.1: a finished search plots EVERY result on the map by itself.
 *
 * Why this is its own file: `no-second-click.spec.ts` pins one specific
 * failure family (out-of-zone continuations must still repaint), and its
 * scenarios are all built around gated chains. What follows is a product
 * behaviour instead — what the map shows once a chain has settled — so it gets
 * its own mocks and its own name.
 *
 * The map used to stay empty until the user ticked cards; the owner himself
 * did not realise his results were already there. Ticking now means only
 * "track this room", and doubles as an optional hide-the-rest filter.
 *
 * Markers are real Mapbox `Marker` DOM elements: `addTo(map)` appends them to
 * the canvas container, which happens without any style or tile ever loading,
 * so `.roomrate-marker-dot` is countable under `mockMapbox`'s empty style
 * (verified empirically before this file was written).
 */
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

const PROP_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROP_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const PROP_GAMMA = "cccccccc-cccc-cccc-cccc-cccccccccccc";
// The job a "Find Competitors" click creates, as opposed to JOB_ID, the
// completed job the page restores on load.
const NEW_JOB_ID = "22222222-2222-2222-2222-2222222222bb";

// Three, so "all of them" is distinguishable from "the first one" and from any
// app default. One price per marker class (low / mid / high): the selected
// state has to stay visible on every class, so no scenario may accidentally
// exercise only one of them.
const MAP_MARKERS = [
  {
    hotel_name: "Hotel Alpha",
    property_id: PROP_ALPHA,
    room_package_id: null,
    room_type: "Double Room",
    room_type_category: "double",
    property_type: "hotel",
    latitude: 36.34,
    longitude: 28.2,
    price_per_night_eur: 60,
    review_score: 8.4,
    review_count: 120,
    rooms_left: 3,
  },
  {
    hotel_name: "Hotel Beta",
    property_id: PROP_BETA,
    room_package_id: null,
    room_type: "Standard Double",
    room_type_category: "double",
    property_type: "hotel",
    latitude: 36.35,
    longitude: 28.21,
    price_per_night_eur: 120,
    review_score: 9.1,
    review_count: 300,
    rooms_left: 1,
  },
  {
    hotel_name: "Hotel Gamma",
    property_id: PROP_GAMMA,
    room_package_id: null,
    room_type: "Superior Double",
    room_type_category: "double",
    property_type: "hotel",
    latitude: 36.36,
    longitude: 28.22,
    price_per_night_eur: 180,
    review_score: 7.9,
    review_count: 45,
    rooms_left: 5,
  },
];

// Deliberately none of the app's own defaults (2 adults / 0 children / 1 room
// / limit 8): a read that quietly fell back to them would still look right.
const RESTORABLE_JOB = {
  id: JOB_ID,
  owned_property_id: OWNED_PROPERTY_ID,
  job_type: "competitor_search",
  room_type_category: "double",
  destination: "Faliraki",
  raw_destination: "Faliraki, Rhodes",
  canonical_destination: "faliraki",
  check_in: "2030-06-01",
  check_out: "2030-06-05",
  adults: 3,
  children: 1,
  rooms: 2,
  filters_payload: { limit: 12 },
  status: "completed",
  scrape_runs_count: 1,
};

const MARKET_SUMMARY = {
  destination: "faliraki",
  check_in: "2030-06-01",
  check_out: "2030-06-05",
  total_records: 42,
  total_hotels: 17,
  price_min_eur: 60,
  price_max_eur: 240,
  price_avg_eur: 118,
  price_median_eur: 110,
  avg_review_score: 8.6,
  rooms_left_total: 55,
};

const markers = (page: Page) => page.locator(".roomrate-marker-dot");
const selectedMarkers = (page: Page) => page.locator('.roomrate-marker-dot[data-selected="true"]');
const markerFor = (page: Page, hotel: string) =>
  page.locator(`.roomrate-marker-dot[aria-label*="${hotel}"]`);
const cardCheckbox = (page: Page, hotel: string) =>
  page.locator(".competitor-card", { hasText: hotel }).locator("input[type=checkbox]");

/**
 * A `/maps/competitors` row as the backend really shapes it: everything else
 * is fixed, but coordinates are nullable — a hotel whose cheapest row has no
 * latitude/longitude is a real result that simply cannot be drawn. Typed so a
 * mock payload that drifts from the response shape is caught, while the
 * coordinate-less rows below stay expressible.
 */
type MarkerRow = Omit<(typeof MAP_MARKERS)[number], "latitude" | "longitude"> & {
  latitude: number | null;
  longitude: number | null;
};

const UNPLOTTABLE_MARKER: MarkerRow = {
  ...MAP_MARKERS[0],
  hotel_name: "Hotel Delta",
  property_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  latitude: null,
  longitude: null,
};

/**
 * Mock the map page.
 *
 * `restorable: false` leaves the job list empty, so nothing is restored on
 * load and the only route to results is a real "Find Competitors" click.
 * `trackedProperties` decides which cards come back already ticked — with no
 * stored selection, the tracked list is what restores one. `markers`
 * overrides the result set, so a scenario can hand back rows the map cannot
 * draw.
 */
async function mockMapPage(
  page: Page,
  { restorable = true, trackedProperties = [] as string[], markerRows = MAP_MARKERS as MarkerRow[] } = {},
): Promise<void> {
  await seedBrowserState(page);
  // Mandatory, not cosmetic: an aborted style routes through the map's error
  // handler into setError, which flips the RESULTS status to "error" and
  // removes the empty states and messages these scenarios assert on.
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
          room_type: "Double Room with Sea View",
          room_type_category: "double",
          is_active: true,
        },
      ],
    }),
  );
  // One handler for all three job shapes the page uses: the restore LIST read
  // (seedBrowserState's unscoped job-id key is dead once workflow storage binds
  // to the auth subject, so the list is how the restore finds a job), the POST
  // a Find click makes, and the detail read its poll loop then makes.
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      return route.fulfill({ json: { ...RESTORABLE_JOB, id: NEW_JOB_ID, status: "queued" } });
    }
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: restorable ? [RESTORABLE_JOB] : [] });
    }
    // Completed on the very first poll, so no scenario waits out a poll delay.
    return route.fulfill({
      json: path.endsWith(NEW_JOB_ID) ? { ...RESTORABLE_JOB, id: NEW_JOB_ID } : RESTORABLE_JOB,
    });
  });
  // "**" is required: the real request carries a query string, and a plain
  // (non-glob) route string only matches a request URL exactly.
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => route.fulfill({ json: markerRows }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        room_type_category: "double",
        competitors: trackedProperties.map((propertyId) => ({
          property_id: propertyId,
          room_package_id: null,
        })),
      },
    }),
  );
  await page.route(`${API}/api/v1/market/summary**`, (route) => route.fulfill({ json: MARKET_SUMMARY }));
}

test("a finished search plots every result without a single tick", async ({ page }) => {
  // Nothing restorable and nothing tracked: the map is empty until the click,
  // and no selection exists afterwards — so every marker below is on screen
  // because the search finished, not because anything was selected.
  await mockMapPage(page, { restorable: false });

  await page.goto("/map");
  await expect(page.getByText("Δεν έχετε τρέξει ακόμη αναζήτηση")).toBeVisible();
  // With nothing to filter, the filter switch must not be on screen at all: a
  // control that says the map hides things, offered over an empty map, is a
  // claim about a map that has nothing on it.
  await expect(page.getByTestId("only-selected-toggle")).toHaveCount(0);

  // Nothing was restored, so the filters form opens by itself (Round 6, spec
  // §4.1) and there is no «Φίλτρα» toggle to press first.
  await expect(page.locator(".filters-sidebar")).toBeVisible();
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();

  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(markers(page)).toHaveCount(3);
  await expect(selectedMarkers(page)).toHaveCount(0);
  // ...and no checkbox was involved.
  await expect(cardCheckbox(page, "Hotel Alpha")).not.toBeChecked();
  await expect(cardCheckbox(page, "Hotel Beta")).not.toBeChecked();
  await expect(cardCheckbox(page, "Hotel Gamma")).not.toBeChecked();
  // A map full of results must not be described as an empty one.
  await expect(page.locator(".map-empty-state")).toHaveCount(0);
  await expect(page.locator(".results-sidebar")).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");
  // And the banner must not send the user hunting for a checkbox to see them.
  await expect(page.locator(".filters-sidebar")).toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");
});

test("the restored search plots every result on load, with the tracked one marked", async ({ page }) => {
  await mockMapPage(page, { trackedProperties: [PROP_ALPHA] });

  await page.goto("/map");

  // Zero interactions: the restore chain plots what it restored.
  await expect(markers(page)).toHaveCount(3);
  // Selection still means "tracked", and the restored one is visibly that.
  await expect(cardCheckbox(page, "Hotel Alpha")).toBeChecked();
  await expect(selectedMarkers(page)).toHaveCount(1);
  await expect(markerFor(page, "Hotel Alpha")).toHaveAttribute("data-selected", "true");
  await expect(markerFor(page, "Hotel Beta")).toHaveAttribute("data-selected", "false");
  // The ring is drawn with CSS, which a screen reader cannot see: the label
  // has to say the same thing in words (M-78). The hotel name stays first —
  // markerFor() finds markers by it — and only the selected one carries the
  // suffix, so the two markers are told apart by ear as well as by eye.
  await expect(markerFor(page, "Hotel Alpha")).toHaveAttribute(
    "aria-label",
    /^Hotel Alpha, .+ ανά βράδυ, επιλεγμένο για παρακολούθηση$/,
  );
  await expect(markerFor(page, "Hotel Beta")).toHaveAttribute(
    "aria-label",
    /^Hotel Beta, .+ ανά βράδυ$/,
  );
});

test("ticking a card marks its marker and leaves the others on the map", async ({ page }) => {
  await mockMapPage(page);

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  await expect(selectedMarkers(page)).toHaveCount(0);

  await cardCheckbox(page, "Hotel Beta").click();

  await expect(markerFor(page, "Hotel Beta")).toHaveAttribute("data-selected", "true");
  // The point of 4.1: ticking adds a state, it does not become the filter.
  await expect(markers(page)).toHaveCount(3);
  await expect(selectedMarkers(page)).toHaveCount(1);
});

test("the only-selected filter hides the rest and says so when it hides everything", async ({
  page,
}) => {
  await mockMapPage(page, { trackedProperties: [PROP_ALPHA] });

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);

  await page.getByTestId("only-selected-toggle").check();

  await expect(markers(page)).toHaveCount(1);
  await expect(markerFor(page, "Hotel Alpha")).toBeVisible();
  await expect(page.locator(".map-empty-state")).toHaveCount(0);

  // Nothing selected plus the filter on is an empty map the user caused. It
  // must say that, not repeat the "the search found nothing" wording, which
  // would be a lie about a search that returned three rooms.
  await cardCheckbox(page, "Hotel Alpha").uncheck();

  await expect(markers(page)).toHaveCount(0);
  const emptyState = page.locator(".map-empty-state");
  await expect(emptyState).toContainText("Όλα τα αποτελέσματα είναι κρυμμένα");
  await expect(emptyState).not.toContainText("Δεν βρέθηκαν");
  await expect(page.locator(".results-sidebar")).not.toContainText("Δεν βρέθηκαν συγκρίσιμα δωμάτια");

  // Turning the filter back off restores the full plot.
  await page.getByTestId("only-selected-toggle").uncheck();
  await expect(markers(page)).toHaveCount(3);
});

test("the search banner names the filter instead of claiming an empty map is full", async ({
  page,
}) => {
  // The filter deliberately survives a new search (it is the user's own view
  // preference), and a new search clears the selection — so this is the exact
  // state where "all results are on the map" would be said over zero markers.
  // Nothing tracked, so the new search really does leave nothing selected.
  await mockMapPage(page);

  await page.goto("/map");
  await expect(markers(page)).toHaveCount(3);
  await page.getByTestId("only-selected-toggle").check();
  await expect(markers(page)).toHaveCount(0);

  // The banner lives in the filters sidebar, which starts collapsed.
  await page.getByRole("button", { name: "Φίλτρα" }).click();
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();

  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  const banner = page.locator(".filters-sidebar .alert").first();
  await expect(banner).toContainText("Μόνο τα επιλεγμένα στον χάρτη");
  await expect(banner).not.toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");
  // The state the banner is describing: a search that found three rooms and a
  // map showing none of them.
  await expect(markers(page)).toHaveCount(0);

  // ...and the mirror image: once the filter is off, the banner must stop
  // naming a filter that is no longer hiding anything.
  await page.getByTestId("only-selected-toggle").uncheck();
  await expect(markers(page)).toHaveCount(3);
  await expect(banner).toContainText("Όλα τα αποτελέσματα εμφανίζονται στον χάρτη");
});

test("a result without coordinates stays a result but never reaches the map", async ({ page }) => {
  await mockMapPage(page, { markerRows: [...MAP_MARKERS, UNPLOTTABLE_MARKER] });

  await page.goto("/map");

  // It counts, it is listed, it is tickable — it just cannot be drawn.
  await expect(page.locator(".header-actions").getByText("4 ανταγωνιστές")).toBeVisible();
  await expect(cardCheckbox(page, "Hotel Delta")).toBeVisible();
  await expect(markers(page)).toHaveCount(3);
  await expect(markerFor(page, "Hotel Delta")).toHaveCount(0);
  // Three of four drawn is not an empty map.
  await expect(page.locator(".map-empty-state")).toHaveCount(0);
});

test("results that all lack coordinates say so, filter on or off", async ({ page }) => {
  await mockMapPage(page, {
    markerRows: MAP_MARKERS.map((row) => ({ ...row, latitude: null, longitude: null })),
    trackedProperties: [PROP_ALPHA],
  });

  await page.goto("/map");

  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();
  await expect(markers(page)).toHaveCount(0);
  const emptyState = page.locator(".map-empty-state");
  await expect(emptyState).toContainText("δεν έχουν συντεταγμένες");

  // Alpha is tracked, so it IS selected: with the filter on, the map is still
  // empty for the same reason as before. Blaming the filter here would send
  // the user to a switch that changes nothing.
  await expect(cardCheckbox(page, "Hotel Alpha")).toBeChecked();
  await page.getByTestId("only-selected-toggle").check();

  await expect(markers(page)).toHaveCount(0);
  await expect(emptyState).toContainText("δεν έχουν συντεταγμένες");
  await expect(emptyState).not.toContainText("κρυμμένα");
});
