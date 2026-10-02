# Round 6 — Stream B (map page) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the map page (`/map`) fit the Thursday live demo: one search with the new defaults (40 hotels, 4 nearby areas, 10 km radius) shows live progress and an elapsed timer while the scrape runs, plots the owner's own hotel («Εσείς») with its radius circle, labels every competitor marker with its price and category (same / similar), keeps popups open across repaints, shows distance and category on the cards, offers a «Μόνο ίδια κατηγορία» toggle and a distance sort, replaces the two disagreeing summary boxes with one box driven by `/market/summary`, links a completed search to `/pricing?job=<id>`, and makes the e2e suite hermetic (the notification bell's `unread-count` call is mocked by default).

**Architecture:** Everything lives in the Angular 20 standalone map page (`frontend/src/app/pages/map-page.component.ts`, inline template + inline styles, signals for every piece of post-await state). The backend contract from spec §7 (Stream A, built in parallel) is consumed through the existing `ApiClientService.request()` helper; every new field is additive and optional in the TypeScript types (`frontend/src/app/types/market.ts`) so the page keeps working against an older API. Mapbox GL JS v3 DOM markers are reconciled in place by key (`property_id ?? hotel_name`) instead of being torn down on every repaint; the own-hotel marker and the radius circle (a GeoJSON polygon source with a fill and a line layer) are separate from the competitor markers. Tests are Playwright e2e specs with `**` wildcard mocks for every endpoint the page calls — no real API server is ever contacted.

**Tech Stack:** Angular 20 (standalone components, signals, `@if/@for` control flow, `FormsModule` two-way binding), Mapbox GL JS v3 (`mapboxgl.Marker`, `mapboxgl.Popup`, GeoJSON sources/layers), TypeScript strict, Playwright 1.x e2e (`frontend/e2e`), ESLint (`npm run lint`).

---

## File structure

| File | Role in this stream |
|---|---|
| `frontend/src/app/types/market.ts` | Additive fields: `distance_km`, `category_match`, `booking_url` on `CompetitorMapMarker`/`Competitor`; `room_type_category`, `category_match` on `CompetitorPackage`; `total_records`, `same_category_hotels`, `similar_hotels` on `MarketSummary`; new `OwnPropertyMapInfo`, `NearbyDestinationsResponse`, `ScrapeJobProgress`, `CategoryMatch`, `CompetitorSort` (`+ "distance"`). |
| `frontend/src/app/types/market.ts` (same file — `ScrapeJobResponse` L63-88 and `ScrapeResultSummary` L95-100 live here, there is no separate scrape-jobs types file) | Additive `nearby_destinations`, `radius_km` on `ScrapeJobResponse`; `progress`, `warnings` on `ScrapeResultSummary`. |
| `frontend/src/styles.css` | Map marker/popup CSS is GLOBAL (the component declares no `styles`): `.roomrate-marker-dot*` L799-863, `.roomrate-popup*` L706-797, `.stats-grid` L609-630, `.kpi-strip` L1253-1274, `.match-chip` L904-928. New rules go next to these. |
| `frontend/src/app/pages/map-page.component.ts` | All UI changes (form, progress, own marker + circle, competitor markers, popup, cards, summary, pricing link, warning). Single file: inline template, inline styles, component class. |
| `frontend/e2e/helpers.ts` | Shared e2e helpers: `mockNotificationBell(page)` registered from `seedBrowserState` so every spec is hermetic. |
| `frontend/e2e/map-round6.spec.ts` | New spec for this stream with its own `mockMapRound6(page, options)` helper that mocks every endpoint the map page calls (unread-count, own-property, nearby-destinations, market summary, maps/competitors, competitors list, tracked, scrape-jobs). |
| `frontend/e2e/map-auto-plot.spec.ts`, `frontend/e2e/map-page-polish.spec.ts` | Existing map specs updated for the new defaults/fields (each task lists the exact assertions that change). |

Conventions that apply to every task (from `frontend/AGENTS.md`):

- Any state written after an `await` (or inside a timer / Mapbox callback) is a **signal**; never a plain field read by the template.
- Playwright: register the broad `**` wildcard mocks FIRST — Playwright matches routes LIFO, so a specific route registered later wins; always use `**` globs because requests carry query strings (`?owned_property_id=…`); record request payloads in arrays declared in the test and assert on them; use non-default values in mocks so a passing test proves the value came from the mock, not from a default.
- Commit from the repo root: `cd C:\vscode_code\room_project2; git add <explicit paths>; git commit -m "..."` — the git root is the parent directory, so never `git add -A`.
- Greek UI text only, zero emoji.
- Run a single test: `cd C:\vscode_code\room_project2\frontend; npx playwright test e2e/map-round6.spec.ts -g "<title>"`. The dev server on :4200 is reused (`reuseExistingServer: true`); `ROOMRATE_E2E_PORT` picks another port for a second checkout.
- `npm run lint` is `tsc -p tsconfig.app.json --noEmit` (a type check of the app, not of the specs); run it after every component/type change.
- Facts that override the spec text where they differ: the bell reads `response.count` (`types/notifications.ts` `UnreadCountResponse = { count: number }`), so the hermetic mock body is `{ count: 0 }`, NOT `{ unread: 0 }`. `MarketSummary.total_records` already exists; only `same_category_hotels`/`similar_hotels` are new.

---

### Task 1: Types for the Round 6 contract + hermetic notification-bell mock

**Files:**
- Modify: `frontend/src/app/types/market.ts`
- Modify: `frontend/e2e/helpers.ts`

- [ ] **Step 1: Add the additive types in `market.ts`**

Add near the top (after `CurrentUser`):

```ts
/** Spec §3.6: "same" = the package is in the baseline room's comparable pool, "similar" = anything else (incl. unknown). */
export type CategoryMatch = "same" | "similar";
/** `/api/v1/competitors/` sort keys; "distance" is new in Round 6. */
export type CompetitorSort = "match" | "price" | "distance";

/** Written by the scraper's ProgressReporter into `result_summary.progress` while a job runs (spec §3.5). */
export type ScrapeJobProgress = {
  stage: "scout" | "deep_crawl" | "persist" | string;
  done?: number | null;
  total?: number | null;
  destinations_total?: number | null;
  hotels_found?: number | null;
  hotels_in_radius?: number | null;
  updated_at?: string | null;
};

export type NearbyDestinationsResponse = { destination: string; canonical: string; nearby: string[] };

/** `GET /api/v1/maps/own-property` (spec §3.6). Coordinates may be null: then no «Εσείς» marker is drawn. */
export type OwnPropertyMapInfo = {
  display_name: string;
  latitude: number | null;
  longitude: number | null;
  radius_km: number | null;
  price_per_night_eur: number | null;
  room_type: string | null;
  booking_url: string | null;
};
```

Then extend the existing types — every new field optional so the page keeps working against an older API:

```ts
export type CompetitorMapMarker = {
  /* ...existing fields unchanged... */
  distance_km?: number | null;
  category_match?: CategoryMatch | null;
  booking_url?: string | null;
};

export type ScrapeJobResponse = {
  /* ...existing fields unchanged... */
  nearby_destinations?: string[] | null;
  radius_km?: number | null;
};

export type ScrapeResultSummary = {
  version: number;
  rows_seen: number;
  filter_counts: Record<string, ScrapeResultFilterCount>;
  rows_written: number;
  // While the job RUNS the summary is `{ progress }` alone (the four fields above
  // only arrive with completion); the poll loop reads nothing but `progress`.
  progress?: ScrapeJobProgress | null;
  warnings?: string[] | null;
};

export type MarketSummary = {
  /* ...existing fields unchanged... */
  same_category_hotels?: number | null;
  similar_hotels?: number | null;
};

export type CompetitorPackage = {
  /* ...existing fields unchanged... */
  room_type_category?: string | null;
  category_match?: CategoryMatch | null;
};

export type Competitor = {
  /* ...existing fields unchanged... */
  distance_km?: number | null;
  category_match?: CategoryMatch | null;
  booking_url?: string | null;
};
```

- [ ] **Step 2: Mock the bell from both seeding helpers in `helpers.ts`**

Add after `seedSessionOnly`, and call it as the LAST line of BOTH `seedBrowserState` and `seedSessionOnly` (`await mockNotificationBell(page);`):

```ts
/**
 * Hermetic e2e (spec §6): the bell in every page header reads
 * `/notifications/unread-count` on mount. Unmocked, the call reaches whatever
 * listens on :8000 — a live API answers 401 and the app reads that as
 * "session expired" (three setup-wizard tests failed that way on 2026-09-15).
 * Registered from the seeding helpers, i.e. BEFORE any route a spec adds, so a
 * spec's own `unread-count` route (notification-bell.spec.ts) still wins: LIFO.
 */
export async function mockNotificationBell(page: Page): Promise<void> {
  await page.route(`${API}/api/v1/notifications/unread-count**`, (route) =>
    route.fulfill({ json: { count: 0 } }));
}
```

- [ ] **Step 3: Verify**

Run: `cd C:\vscode_code\room_project2\frontend; npm run lint; npx playwright test e2e/notification-bell.spec.ts e2e/setup-wizard.spec.ts e2e/map-auto-plot.spec.ts`
Expected: lint clean; all pass (the bell spec's body-specific routes still override the helper's; the wizard's three "session expired" false failures are gone even with the API on :8000).

- [ ] **Step 4: Commit**

```powershell
cd C:\vscode_code\room_project2
git add frontend/src/app/types/market.ts frontend/e2e/helpers.ts
git commit -m "Add the Round 6 map types and mock the notification bell in every e2e seed" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Form defaults — 40 hotels, max 80, no `deep_crawl_max_items` (+ the new spec and its `mockMapRound6` helper)

**Files:**
- Modify: `frontend/src/app/pages/map-page.component.ts` (template L223, `filters` signal L500, `findCompetitors` L1100-1120)
- Create: `frontend/e2e/map-round6.spec.ts`

- [ ] **Step 1: Create `frontend/e2e/map-round6.spec.ts` with the fixtures and the helper (every later task adds its test here)**

```ts
/**
 * Round 6, stream B (spec §4): the map page for the Thursday live demo.
 * Hermetic: every endpoint the page calls is mocked with a `**` glob, the
 * wildcard first (Playwright matches LIFO, so every specific route below wins).
 * Fixture values are deliberately non-default (limit 12, adults 3, radius 7,
 * distances 1.2/0.4/null): a passing assertion proves the value came from here.
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

const PROP_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROP_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const PROP_GAMMA = "cccccccc-cccc-cccc-cccc-cccccccccccc";
// The job a «Εύρεση ανταγωνιστών» click creates, as opposed to JOB_ID (restored on load).
const NEW_JOB_ID = "22222222-2222-2222-2222-2222222222cc";

const MARKER_BASE = {
  room_package_id: null,
  room_type: "Double Room",
  room_type_category: "double",
  property_type: "hotel",
  review_score: 8.4,
  review_count: 120,
  rooms_left: 3,
};
// Alpha: same category, 1.2 km. Beta: SIMILAR (a studio), nearest at 0.4 km.
// Gamma: same category, no distance (no coordinates on the owner's side) and no URL.
const MAP_MARKERS = [
  { ...MARKER_BASE, hotel_name: "Hotel Alpha", property_id: PROP_ALPHA, latitude: 36.34, longitude: 28.2,
    price_per_night_eur: 60, distance_km: 1.2, category_match: "same", booking_url: "https://www.booking.com/hotel/gr/alpha.html" },
  { ...MARKER_BASE, hotel_name: "Hotel Beta", property_id: PROP_BETA, latitude: 36.35, longitude: 28.21,
    room_type: "Studio", room_type_category: "studio",
    price_per_night_eur: 95, distance_km: 0.4, category_match: "similar", booking_url: "https://www.booking.com/hotel/gr/beta.html" },
  { ...MARKER_BASE, hotel_name: "Hotel Gamma", property_id: PROP_GAMMA, latitude: 36.36, longitude: 28.22,
    price_per_night_eur: 150, distance_km: null, category_match: "same", booking_url: null },
];

const COMPLETED_JOB = {
  id: JOB_ID,
  account_id: CURRENT_USER.account_id,
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
  nearby_destinations: ["Ιξιά", "Αφάντου"],
  radius_km: 7,
  status: "completed",
  requested_at: "2030-05-01T10:00:00Z",
  started_at: "2030-05-01T10:00:05Z",
  finished_at: "2030-05-01T10:06:00Z",
  attempt_count: 1,
  max_attempts: 3,
  scrape_runs_count: 1,
  result_summary: { version: 1, rows_seen: 20, filter_counts: {}, rows_written: 12, warnings: [] as string[] },
};

const MARKET_SUMMARY = {
  destination: "faliraki", check_in: "2030-06-01", check_out: "2030-06-05",
  total_records: 42, total_hotels: 17, same_category_hotels: 11, similar_hotels: 6,
  price_min_eur: 60, price_max_eur: 240, price_avg_eur: 118, price_median_eur: 110,
  avg_review_score: 8.6, rooms_left_total: 55,
};

const OWN_PROPERTY = {
  display_name: "E2E Test Hotel", latitude: 36.345, longitude: 28.205, radius_km: 10,
  price_per_night_eur: 92, room_type: "Double Room with Sea View", booking_url: "https://www.booking.com/hotel/gr/e2e.html",
};

// `/api/v1/competitors/` rows (the match list), derived from the markers so the two agree.
const COMPETITORS = MAP_MARKERS.map((marker) => ({
  hotel_name: marker.hotel_name, city: "Faliraki", address: "", property_type: "hotel",
  latitude: marker.latitude, longitude: marker.longitude, stars: 3,
  review_score: marker.review_score, review_count: marker.review_count,
  price_min_eur: marker.price_per_night_eur, price_max_eur: marker.price_per_night_eur + 20, rooms_left: 3,
  distance_km: marker.distance_km, category_match: marker.category_match, booking_url: marker.booking_url,
  best_match_score: 80,
  packages: [{ room_type: marker.room_type, price_per_night_eur: marker.price_per_night_eur,
    price_total_eur: marker.price_per_night_eur * 4, meals: "", free_cancellation: "", rooms_left: 3,
    match_score: 80, room_type_category: marker.room_type_category, category_match: marker.category_match }],
}));

const markers = (page: Page) => page.locator(".roomrate-marker-dot");
const markerFor = (page: Page, hotel: string) => page.locator(`.roomrate-marker-dot[aria-label*="${hotel}"]`);
const cardCheckbox = (page: Page, hotel: string) =>
  page.locator(".competitor-card", { hasText: hotel }).locator("input[type=checkbox]");

type Round6Options = {
  /** false = empty job list, nothing restored: the only way to results is a Find click. */
  restorable?: boolean;
  markerRows?: unknown[];
  ownProperty?: unknown;
  nearby?: string[];
  /** Overrides merged into the completed/restored job. */
  job?: Record<string, unknown>;
  /** Successive bodies for GET /scrape-jobs/<NEW_JOB_ID>; the last one repeats. Default: completed at once. */
  polls?: Array<Record<string, unknown>>;
  /** Every scrape-jobs call (the POST payload lives here). */
  calls?: RecordedCall[];
  /** Every query string sent to maps/own-property, maps/competitors, competitors/ and market/summary. */
  reads?: URLSearchParams[];
};

async function mockMapRound6(page: Page, options: Round6Options = {}): Promise<void> {
  const {
    restorable = true, markerRows = MAP_MARKERS, ownProperty = OWN_PROPERTY,
    nearby = ["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"], calls = [], reads = [],
  } = options;
  const job = { ...COMPLETED_JOB, ...(options.job ?? {}) };
  const polls = options.polls ?? [{ ...job, id: NEW_JOB_ID }];
  let pollIndex = 0;
  const record = (route: Parameters<Parameters<Page["route"]>[1]>[0]) =>
    reads.push(new URL(route.request().url()).searchParams);

  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO: the wildcard FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [{ id: ROOM_TYPE_ID, owned_property_id: OWNED_PROPERTY_ID,
      room_type: "Double Room with Sea View", room_type_category: "double", is_active: true }] }));
  await page.route(`${API}/api/v1/notifications/unread-count**`, (route) => route.fulfill({ json: { count: 0 } }));
  await page.route(`${API}/api/v1/onboarding/nearby-destinations**`, (route) =>
    route.fulfill({ json: { destination: "Faliraki", canonical: "faliraki", nearby } }));
  await page.route(`${API}/api/v1/maps/own-property**`, (route) => { record(route); return route.fulfill({ json: ownProperty }); });
  await page.route(`${API}/api/v1/maps/competitors**`, (route) => { record(route); return route.fulfill({ json: markerRows }); });
  await page.route(`${API}/api/v1/competitors/**`, (route) => { record(route); return route.fulfill({ json: COMPETITORS }); });
  await page.route(`${API}/api/v1/market/summary**`, (route) => { record(route); return route.fulfill({ json: MARKET_SUMMARY }); });
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }));
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => {
    recordCall(calls, route);
    const request = route.request();
    if (request.method() === "POST") {
      return route.fulfill({ json: { ...job, id: NEW_JOB_ID, status: "queued", started_at: null, finished_at: null, result_summary: null } });
    }
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/scrape-jobs/")) {
      return route.fulfill({ json: restorable ? [job] : [] });
    }
    if (path.endsWith(NEW_JOB_ID)) {
      const body = polls[Math.min(pollIndex, polls.length - 1)];
      pollIndex += 1;
      return route.fulfill({ json: body });
    }
    return restorable ? route.fulfill({ json: job }) : route.fulfill({ status: 404, json: { detail: "not found" } });
  });
}
```

- [ ] **Step 2: Add the Task 2 test (fails: value is "8", max "50", `deep_crawl_max_items` present)**

```ts
test("the form defaults to 40 hotels, caps at 80 and no longer sends deep_crawl_max_items", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await mockMapRound6(page, { restorable: false, calls });

  await page.goto("/map");
  await expect(page.getByText("Δεν έχετε τρέξει ακόμη αναζήτηση")).toBeVisible();
  await page.getByRole("button", { name: "Φίλτρα" }).click(); // Task 3 replaces this line (the form opens itself)
  const limit = page.getByTestId("limit-input");
  await expect(limit).toHaveValue("40");
  await expect(limit).toHaveAttribute("max", "80");
  // Typed past the cap: the clamp lives in the component, not in the input's max attribute.
  await limit.fill("500");
  await page.getByRole("button", { name: "Εύρεση ανταγωνιστών" }).click();
  await expect(page.locator(".header-actions").getByText("3 ανταγωνιστές")).toBeVisible();

  const post = calls.find((call) => call.method === "POST" && call.path === "/api/v1/scrape-jobs/");
  const payload = (post?.body as { filters_payload: Record<string, unknown> }).filters_payload;
  expect(payload).toEqual({ limit: 80, match_mode: "catalog_room_name", room_type: "Double Room with Sea View" });
});
```

Run: `cd C:\vscode_code\room_project2\frontend; npx playwright test e2e/map-round6.spec.ts -g "defaults to 40"` → Expected: FAIL on `toHaveValue("40")` (got "8").

- [ ] **Step 3: Change the component**

Template L223 becomes:

```html
            <input class="roomrate-input" min="1" max="80" type="number" data-testid="limit-input" [ngModel]="filters().limit" (ngModelChange)="setFilter('limit', $event)">
```

`filters` signal (L500): `limit: "40",` (spec §3.1: the server default moved from 25 to 40; the page used to ask for 8).

`findCompetitors` (L1100-1101 and the payload L1115-1120): delete `deepCrawlMaxItems` entirely — the server now derives it from `hotel_cap` (spec §3.1) — and clamp to the new bounds:

```ts
    const resultLimit = this.readBoundedNumber(this.filters().limit, 40, 1, 80);
    /* ... */
        filters_payload: {
          limit: resultLimit,
          match_mode: "catalog_room_name",
          room_type: baselineRoom.room_type,
        },
```

- [ ] **Step 4: Verify**

Run: `cd C:\vscode_code\room_project2\frontend; npm run lint; npx playwright test e2e/map-round6.spec.ts e2e/map-auto-plot.spec.ts e2e/no-second-click.spec.ts` → Expected: all pass (no existing spec asserts the limit input's value or the `deep_crawl_max_items` field; `filters_payload: { limit: 8/12 }` in their fixtures is job DATA the page restores, not a default).

- [ ] **Step 5: Commit**

```powershell
cd C:\vscode_code\room_project2
git add frontend/src/app/pages/map-page.component.ts frontend/e2e/map-round6.spec.ts
git commit -m "Default the map search to 40 hotels, cap it at 80 and stop sending deep_crawl_max_items" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
