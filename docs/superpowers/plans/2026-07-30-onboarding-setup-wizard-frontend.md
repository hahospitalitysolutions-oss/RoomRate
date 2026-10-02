# Onboarding /setup Wizard Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user lands on the map **with data in it** and always knows what is missing: explicit property confirmation, cost-gated first search, a 5-step checklist, and empty states instead of fake zeros.

**Architecture:** A guarded `/setup` route hosts a 3-step wizard (container + one component per step). `auth-page` keeps calling `auto-setup` for speed but treats the result as a *proposal*: it stores the proposed candidate + discovery job and redirects to `/setup`, where the user confirms or corrects via the reversible-property endpoints. A `SetupProgressService` fed by data the map already fetches drives a collapsible checklist; a reusable `EmptyStateComponent` replaces every fake-zero screen. All new UI text is Greek, icons are inline SVG line icons, zero emoji.

**Tech Stack:** Angular standalone components + signals, Mapbox GL JS (dynamic import), Playwright e2e with fully mocked backend.

**Spec:** `docs/superpowers/specs/2026-07-27-onboarding-ui-design.md` — parts A (wizard), B (empty states), C (checklist), §7 (visual language), §8 (errors), §9 (Playwright). §3.4/§3.5/§9-pytest and §10 are already done (backend plan `2026-07-28-onboarding-backend-endpoints.md`, merged as PR #8; cleanup merged as PR #7).

**Verification baseline before starting:** `cd frontend && npm run build` succeeds and `npm run e2e` → both existing specs pass (`no-second-click.spec.ts`, `live-walkthrough.spec.ts` — the latter needs a live backend and self-skips without one; green means "passed or skipped").

---

## Execution status (2026-08-03 — read this first when resuming)

Executing subagent-driven on branch `feature/onboarding-setup-wizard`.
Checkboxes below are NOT ticked; this block is the progress record.

| Task | State | Key commits |
|---|---|---|
| 1 OnboardingService | done + both reviews closed | `b174a47`, `b9da93c` |
| 2 icons/empty-state/bell | done + both reviews closed | `cbb1794`, `5a8a840` |
| 3 guard/route/container | done + both reviews closed | `d019c70`, `3fd27a7`, `66d76b2` |
| 4 auth-page proposal handoff | done + both reviews closed | `d486e87`, `26c8cf7`, `659cc62` |
| 4b /me cache | done + both reviews closed | `e80cd51`, `30c89f8`, `10577da` |
| 5 property step | done + both reviews closed; fallback create/retry, `/me` reconciliation, immutable attempted-candidate snapshot and lifecycle-safe mini-map are mutation-pinned | `9ffd6b9`, `2adf0a8`, `3e017a5`, `3544722`, `e1074a9`, `6906553` |
| 6 room step | done + both reviews closed; real five-minute deadline, Greek error redaction, polling/retry/destroy/single-flight and one-navigation skip are mutation-pinned | `002bca9`, `126f3dd`, `8bca1b2` |
| 7 cost-gated first search | done + both reviews closed; authoritative consent intent, exact request forwarding, ambiguous paid-POST reconciliation and quota-race protection are mutation-pinned | `77e186b`, `3e24e4d`, `16f892f` |
| 8 setup checklist | done; spec review closed (2 rounds, kill table), quality review findings all fixed but the post-fix re-verification pass was skipped at user request (see Task 8 outcome block); wiring, restore-path feed, orphan scoping, geometry (two-sided), mobile collapse, hide-flag read AND write are mutation-pinned | `a7ec3f7`, `52a7d14`, `c79f2dd`, `129c3e6` |
| 9 empty states | done + both reviews closed; snapshot gate on `competitors()`, idle/ready competitor-list split, snapshot loading branch, hardened emoji guard and both Greek titles are pinned (see Task 9 amendment block for residuals) | `d596f80`, `866d91a`, `b367e65` |
| 10 property settings | done + both reviews closed; the ?change=1 flow (incl. un-stick after replace), both mandated /me-cache invalidation kills, the typed-name delete gate, all 13 property-scoped clears and the no-false-flash property section are pinned | `a582d85`, `27d212c`, `5214078` |
| 11 final verification | **done (2026-08-11).** Build clean; e2e 91+1 stable across 3 consecutive full runs; 5b flake insurance (`59112e8`); backend **565 passed**. **Full live end-to-end PASSED (6.5 min, real backend + Supabase + Apify, account `+smoke0811b`)**: sign-up → auto-setup proposed the real best match (Dimitra Boutique Rooms for the «Rea Hotel»/Faliraki query — the confirmation screen with mini-map + Booking link is exactly why the proposal step exists) → «Ναι, αυτό είναι» → real discovery found 4 rooms → cost gate (Φαληράκι/double) → **real paid competitor search ran to completion** → map with real Faliraki prices (197-275€), checklist «3 από 5», open step «Παρακολούθηση» → pricing rendered a real sparkline (1 run) → settings showed the property → typed-name delete landed back in /setup. Screenshots delivered in-session. Two live observations recorded: (1) tracking 3 competitors does not tick step 4 within the same session (save does not re-feed `setTracked`; ticks on next map load — known recorded residual, observed live); (2) the «3+» threshold of step 4 can exceed a small market's real competitor count (Faliraki double: 2-3 candidates). **Residual blockers/records:** chip task_79d4ce48 («Δείξε άλλα» live: `scout_cache_hours=0` + 45s GET abort); smoke leftovers on the dev project: `+smoke0807b` (unconfirmed), `+smoke0807c`/`d` and `+smoke0811` (property, no room), `+smoke0811b` (property deleted by the script). The temporary `live-smoke.spec.ts` was deleted after the run as planned | `59112e8` |

Current frontend baseline: **91 passed + 1 skipped** (`npx playwright test`
from `frontend/`); lint and production build clean. The skipped test is the
pre-existing live walkthrough. Backend code remains untouched; its last known
baseline is 565 tests. Chip task_c0280b15 (dead `scrape-jobs/` mock pattern
in scenario 5b) is running in a separate session — if it lands a commit,
rebase-check before touching `setup-wizard.spec.ts`.

**Resume order: Task 11 (final verification), then
superpowers:finishing-a-development-branch.** Tasks 5–10 are closed; do not
reopen or rewrite them unless a new regression is found.
Execution style: superpowers:subagent-driven-development (implementer per
task, then spec review with a mutation kill table, then quality review;
every finding is also folded back into this plan).

## Standing rules (learned executing the backend plan — they apply here too)

1. **Mocked routes record their calls.** Every Playwright `page.route` handler that the test later reasons about pushes `{path, method, body}` into an array the test asserts on. A mock that records nothing cannot verify forwarding — and the cost-guard scenarios below are *entirely* about which calls did NOT happen.
2. **Test values must be non-default.** When a spec asserts a forwarded field, seed it with a value the code would not produce by accident (e.g. a candidate that is *not* `candidates[0]` for the "user picked a different one" test).
3. **Contracts live where the consumer reads them.** For components that is the `@Input()`/`@Output()` surface plus the doc comment on the class — not buried body comments.
4. **Angular signals rule (frontend/AGENTS.md):** any state written after an `await` or inside a timer/WebSocket callback MUST be a signal. Every component below follows it; reviewers should treat a plain-field write after `await` as a defect.
5. **No emoji anywhere.** Greek UI text, line SVG icons only.
6. **`seedBrowserState`'s workflow-storage seeds are dead once a session binds.** `WorkflowStorageService.resolveKey` returns `key:authSubject` for every non-draft key after `bindToSubject`, so the helper's unscoped seeds (`roomrate_last_competitor_job_id`, …) are never read post-login — `no-second-click` only works because `/me` re-supplies the same values. Any test that needs workflow state after login must seed the subject-scoped form (`roomrate_…:e2e-user`) via `addInitScript`. (Found executing Task 8; the helper itself is a candidate for a later cleanup task.)

## What this plan deliberately does not do

- **No Greek translation of the pre-existing pages** (auth, map, pricing, settings headers are English today). That is a separate string-inventory plan; bundling ~3,000 template lines of renames here would drown the wizard. The three §7 term renames ship with the screens that use them when that plan runs. Everything *this* plan creates or visibly touches ships in Greek.
- No multi-property support, no i18n infrastructure, no email/push, no settings redesign beyond the one new section (spec §11).
- No backend changes of any kind.
- **Known residual (Task 4b review):** a session replaced via cross-tab
  supabase storage sync bypasses `submit()`'s cache invalidation — the
  multi-tab sibling of the fixed bootstrap bug. Systemic fix is
  invalidate-on-signOut inside `AuthService`; deliberately left for a future
  task, recorded here so it is a decision, not an omission.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `frontend/src/app/services/onboarding.service.ts` | The only place that talks to the onboarding/scrape-job endpoints | Create |
| `frontend/src/app/services/setup-progress.service.ts` | Computes checklist state from data pages already fetched | Create |
| `frontend/src/app/services/workflow-storage.service.ts` | Persisted workflow state | Modify: 3 new keys |
| `frontend/src/app/types/market.ts` | API types | Modify: add `OwnedPropertyOnboardingResponse` |
| `frontend/src/app/components/icon.component.ts` | Every line SVG icon as inline template (`icons.ts` role from the spec) | Create |
| `frontend/src/app/components/empty-state.component.ts` | Reusable icon+title+explanation+optional action | Create |
| `frontend/src/app/components/notification-bell.component.ts` | Bell | Modify: emoji → `<app-icon name="bell">` |
| `frontend/src/app/guards/setup.guard.ts` | Redirect half-onboarded users into `/setup`, complete users out of it | Create |
| `frontend/src/app/app.routes.ts` | Routes | Modify: `/setup` + guard wiring |
| `frontend/src/app/pages/setup-page.component.ts` | Wizard container: current step, data loading, no per-step API logic | Create |
| `frontend/src/app/onboarding/setup-property-step.component.ts` | Step 1: confirm/correct the property, mini map | Create |
| `frontend/src/app/onboarding/setup-room-step.component.ts` | Step 2: pick the room being priced | Create |
| `frontend/src/app/onboarding/setup-search-step.component.ts` | Step 3: cost-gated first search | Create |
| `frontend/src/app/onboarding/setup-checklist.component.ts` | Collapsible 5-step progress bar on the map | Create |
| `frontend/src/app/pages/auth-page.component.ts` | Sign-up keeps auto-setup, stops pretending it finished | Modify |
| `frontend/src/app/pages/map-page.component.ts` | Hosts checklist; empty states instead of zero-stats | Modify |
| `frontend/src/app/pages/pricing-page.component.ts` | Price-history empty state | Modify |
| `frontend/src/app/pages/settings-page.component.ts` | «Το κατάλυμά μου» section (view / change / delete) | Modify |
| `frontend/e2e/helpers.ts` | Session seeding + route-mock helpers shared by specs | Create (extracted) |
| `frontend/e2e/setup-wizard.spec.ts` | Spec §9 scenarios 1-6 | Create |
| `frontend/e2e/empty-states-and-settings.spec.ts` | Spec §9 scenarios 7-9 | Create |

There is no unit-test runner in `frontend/` (only Playwright). TDD here means: **write the failing e2e spec first, watch it fail, implement, watch it pass.** Playwright starts the dev server itself (`playwright.config.ts` `webServer`), and mocks every network call, so no backend is needed. Run a single spec with:

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```

---

### Task 1: Types, storage keys, and the frontend OnboardingService

**Files:**
- Modify: `frontend/src/app/types/market.ts`
- Modify: `frontend/src/app/services/workflow-storage.service.ts`
- Create: `frontend/src/app/services/onboarding.service.ts`

No behavior change yet, so the verification for this task is the type-checker (`npm run build`) plus the unchanged e2e suite.

- [ ] **Step 1: Add the response type**

In `frontend/src/app/types/market.ts`, after `AutomaticSetupResponse`:

```typescript
export type OwnedPropertyOnboardingResponse = {
  owned_property_id: string;
  discovery_job: ScrapeJobResponse;
};
```

- [ ] **Step 2: Add the storage keys**

In `frontend/src/app/services/workflow-storage.service.ts`, extend `STORAGE_KEYS` (all three are account-scoped, so they do NOT go into `GLOBAL_DRAFT_KEYS`):

```typescript
  lastCompetitorJobId: "roomrate_last_competitor_job_id",
  lastCompetitorSelection: "roomrate_last_competitor_selection",
  pendingCandidate: "roomrate_pending_candidate",
  pendingDiscoveryJobId: "roomrate_pending_discovery_job_id",
  pendingSetupError: "roomrate_pending_setup_error",
  checklistHidden: "roomrate_checklist_hidden",
```

(The first two lines already exist — shown for anchoring; add the last four.)

- [ ] **Step 3: Create the service**

`frontend/src/app/services/onboarding.service.ts`:

```typescript
import { Injectable } from "@angular/core";

import {
  AutomaticSetupResponse,
  CurrentUser,
  OwnedPropertyOnboardingResponse,
  OwnedPropertyRoomType,
  PropertyCandidate,
  ScrapeJobResponse,
  SelectedRoomTypeResponse,
} from "../types/market";
import { ApiClientService } from "./api-client.service";

/**
 * Typed surface for the onboarding, /me, and scrape-job endpoints.
 *
 * Wizard steps, the setup guard, settings and every page's /me read reach
 * these endpoints only through this service — never via URL strings — so
 * those flows survive an endpoint change with a one-file edit. map-page and
 * pricing-page still hold their own URL strings for their OTHER endpoints
 * (competitors, market, scrape-jobs). Apart from the /me navigation cache
 * below, methods are thin typed delegations.
 */
@Injectable({ providedIn: "root" })
export class OnboardingService {
  constructor(private readonly api: ApiClientService) {}

  currentUser(): Promise<CurrentUser> {
    return this.api.get<CurrentUser>("/api/v1/me");
  }

  autoSetup(body: {
    property_name: string;
    location: string;
    check_in: string;
    check_out: string;
    adults: number;
    children: number;
    rooms: number;
    limit: number;
  }): Promise<AutomaticSetupResponse> {
    return this.api.post<AutomaticSetupResponse>("/api/v1/onboarding/auto-setup", body);
  }

  /** Ordered best match first — `candidates[0]` is the backend's pick. */
  propertyCandidates(query: {
    property_name: string;
    location: string;
    check_in: string;
    check_out: string;
    limit?: number;
  }): Promise<PropertyCandidate[]> {
    const params = new URLSearchParams({
      property_name: query.property_name,
      location: query.location,
      check_in: query.check_in,
      check_out: query.check_out,
      limit: String(query.limit ?? 8),
    });
    return this.api.get<PropertyCandidate[]>("/api/v1/onboarding/property-candidates", params);
  }

  /** 202: swaps the property, keeps its id, queues a fresh discovery job. */
  replaceOwnedProperty(
    ownedPropertyId: string,
    body: {
      display_name: string;
      booking_url: string;
      city: string;
      raw_destination?: string | null;
      address?: string | null;
      country?: string | null;
      property_type?: string | null;
      latitude?: number | null;
      longitude?: number | null;
      check_in: string;
      check_out: string;
    },
  ): Promise<OwnedPropertyOnboardingResponse> {
    return this.api.put<OwnedPropertyOnboardingResponse>(
      `/api/v1/onboarding/owned-property/${ownedPropertyId}`,
      body,
    );
  }

  /** 204; cascades room types/competitors/alerts, keeps market history. */
  deleteOwnedProperty(ownedPropertyId: string): Promise<void> {
    return this.api.delete(`/api/v1/onboarding/owned-property/${ownedPropertyId}`);
  }

  roomTypes(ownedPropertyId: string): Promise<OwnedPropertyRoomType[]> {
    return this.api.get<OwnedPropertyRoomType[]>(
      `/api/v1/onboarding/owned-property/${ownedPropertyId}/room-types`,
    );
  }

  selectRoomType(ownedPropertyId: string, roomTypeCategory: string): Promise<SelectedRoomTypeResponse> {
    return this.api.put<SelectedRoomTypeResponse>(
      `/api/v1/onboarding/owned-property/${ownedPropertyId}/selected-room-type`,
      { room_type_category: roomTypeCategory },
    );
  }

  getScrapeJob(jobId: string): Promise<ScrapeJobResponse> {
    return this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${jobId}`);
  }

  startCompetitorSearch(body: {
    owned_property_id: string;
    room_type_category: string;
    destination: string;
    raw_destination?: string | null;
    check_in: string;
    check_out: string;
    adults: number;
    children: number;
    rooms: number;
    filters_payload: Record<string, unknown>;
  }): Promise<ScrapeJobResponse> {
    return this.api.post<ScrapeJobResponse>("/api/v1/scrape-jobs/", {
      ...body,
      job_type: "competitor_search",
    });
  }
}
```

- [ ] **Step 4: Verify the build**

```bash
cd frontend && npm run build
```
Expected: success, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/types/market.ts frontend/src/app/services/workflow-storage.service.ts frontend/src/app/services/onboarding.service.ts
git commit -m "Add frontend OnboardingService and wizard storage keys

One typed surface for every onboarding/scrape-job endpoint, so the /setup
wizard components never hold URL strings, plus the storage keys that carry
the auto-setup proposal from sign-up into the wizard."
```

---

### Task 2: IconComponent, EmptyStateComponent, and the bell

**Files:**
- Create: `frontend/src/app/components/icon.component.ts`
- Create: `frontend/src/app/components/empty-state.component.ts`
- Modify: `frontend/src/app/components/notification-bell.component.ts`

- [ ] **Step 1: The icon set**

`frontend/src/app/components/icon.component.ts` — every icon inline, one `stroke-width`, `currentColor` so CSS controls color. (`[innerHTML]` would be stripped by Angular sanitization; an `ngSwitch` template avoids the sanitizer entirely.)

```typescript
import { ChangeDetectionStrategy, Component, Input } from "@angular/core";
import { CommonModule } from "@angular/common";

export type IconName =
  | "bell"
  | "building"
  | "bed"
  | "search"
  | "map-pin"
  | "chart"
  | "check"
  | "chevron-down"
  | "alert"
  | "refresh"
  | "trash";

/**
 * The app's entire line-icon set as inline SVG templates (spec §7: no emoji,
 * unified stroke). Add icons here, never as emoji or per-component SVG.
 */
@Component({
  selector: "app-icon",
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [ngSwitch]="name" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
         [attr.width]="size" [attr.height]="size" aria-hidden="true">
      <g *ngSwitchCase="'bell'"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></g>
      <g *ngSwitchCase="'building'"><rect x="4" y="3" width="16" height="18" rx="1"/><path d="M9 7h1m4 0h1M9 11h1m4 0h1M9 15h1m4 0h1M10 21v-3h4v3"/></g>
      <g *ngSwitchCase="'bed'"><path d="M3 7v11m0-4h18m0 4v-7a2 2 0 0 0-2-2H8"/><circle cx="6" cy="9.5" r="1"/></g>
      <g *ngSwitchCase="'search'"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></g>
      <g *ngSwitchCase="'map-pin'"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/></g>
      <g *ngSwitchCase="'chart'"><path d="M3 3v18h18"/><path d="m7 15 4-5 3 3 5-7"/></g>
      <g *ngSwitchCase="'check'"><path d="m5 13 4 4L19 7"/></g>
      <g *ngSwitchCase="'chevron-down'"><path d="m6 9 6 6 6-6"/></g>
      <g *ngSwitchCase="'alert'"><circle cx="12" cy="12" r="9"/><path d="M12 8v4m0 4h.01"/></g>
      <g *ngSwitchCase="'refresh'"><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8m0-5v5h-5M21 12a9 9 0 0 1-15.4 6.4L3 16m0 5v-5h5"/></g>
      <g *ngSwitchCase="'trash'"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6"/></g>
    </svg>
  `,
  // Without this every consumer inherits the inline line-box's phantom
  // descender space and reaches for its own vertical-align fix.
  styles: [":host { display: inline-flex; } svg { display: block; }"],
})
export class IconComponent {
  @Input({ required: true }) name!: IconName;
  @Input() size = 20;
}
```

- [ ] **Step 2: The empty state**

`frontend/src/app/components/empty-state.component.ts`:

```typescript
import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from "@angular/core";
import { CommonModule } from "@angular/common";

import { IconComponent, IconName } from "./icon.component";

/**
 * Replaces fake-zero screens (spec §6): an icon, a title, an explanation of
 * WHY there is nothing here yet, and optionally the one action that fixes it.
 * Never render a metric as "0" when the truth is "no data yet".
 */
@Component({
  selector: "app-empty-state",
  standalone: true,
  imports: [CommonModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="empty-state">
      <app-icon [name]="icon" [size]="28"></app-icon>
      <strong>{{ title }}</strong>
      <p>{{ explanation }}</p>
      <button *ngIf="actionLabel" class="secondary-button" type="button" (click)="action.emit()">
        {{ actionLabel }}
      </button>
    </div>
  `,
  styles: [`
    .empty-state {
      display: flex; flex-direction: column; align-items: center; gap: 0.5rem;
      padding: 1.5rem 1rem; text-align: center; color: #475569;
    }
    .empty-state strong { color: var(--foreground); }
    .empty-state p { margin: 0; max-width: 32rem; font-size: 0.9rem; }
  `],
})
export class EmptyStateComponent {
  @Input({ required: true }) icon!: IconName;
  @Input({ required: true }) title!: string;
  @Input({ required: true }) explanation!: string;
  @Input() actionLabel = "";
  @Output() action = new EventEmitter<void>();
}
```

- [ ] **Step 3: Replace the bell emoji**

In `frontend/src/app/components/notification-bell.component.ts`: add `IconComponent` to the `imports` array of the component decorator, import it (`import { IconComponent } from "./icon.component";`), and replace the line

```html
        <span aria-hidden="true">&#128276;</span>
```

with

```html
        <app-icon name="bell" [size]="18"></app-icon>
```

- [ ] **Step 4: Verify build + existing e2e**

```bash
cd frontend && npm run build && npm run e2e
```
Expected: build succeeds; `no-second-click.spec.ts` green (the bell renders as SVG, nothing else changed).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/icon.component.ts frontend/src/app/components/empty-state.component.ts frontend/src/app/components/notification-bell.component.ts
git commit -m "Add the line-icon set and EmptyStateComponent, drop the bell emoji

Spec 7: icons are inline SVG with one stroke width, zero emoji. The empty
state carries an explanation of why data is missing instead of fake zeros."
```

---

### Task 3: e2e helpers, setupGuard, /setup route, and the wizard container

**Files:**
- Create: `frontend/e2e/helpers.ts`
- Modify: `frontend/e2e/no-second-click.spec.ts` (imports only)
- Create: `frontend/src/app/guards/setup.guard.ts`
- Modify: `frontend/src/app/app.routes.ts`
- Create: `frontend/src/app/pages/setup-page.component.ts`
- Create: `frontend/src/app/onboarding/setup-property-step.component.ts` (minimal, replaced in Task 5)
- Create: `frontend/src/app/onboarding/setup-room-step.component.ts` (minimal, replaced in Task 6)
- Create: `frontend/src/app/onboarding/setup-search-step.component.ts` (minimal, replaced in Task 7)
- Create: `frontend/e2e/setup-wizard.spec.ts` (scenarios 1 and 6 here; later tasks extend it)

- [ ] **Step 1: Extract the shared e2e helpers**

Create `frontend/e2e/helpers.ts` by **moving** (not copying) from `no-second-click.spec.ts`: the constants `API`, `SUPABASE_STORAGE_KEY`, `ACCOUNT_ID`, `OWNED_PROPERTY_ID`, `JOB_ID`, `ROOM_TYPE_ID`, `CURRENT_USER`, and the `seedBrowserState(page)` function — all verbatim, each gaining `export`. Then in `no-second-click.spec.ts` delete the moved code and import instead:

```typescript
import {
  ACCOUNT_ID,
  API,
  CURRENT_USER,
  JOB_ID,
  OWNED_PROPERTY_ID,
  ROOM_TYPE_ID,
  seedBrowserState,
  SUPABASE_STORAGE_KEY,
} from "./helpers";
```

Constants used only by `no-second-click.spec.ts` (`PROP_ALPHA`, `PROP_BETA`, `ROOM_TYPES`, `COMPLETED_JOB`, `MAP_MARKERS`, `MARKET_SUMMARY`, `PRICE_HISTORY`, `RECOMMENDATION`, `NOTIFICATIONS`) stay where they are. Additionally add to `helpers.ts` a session-only seeder for wizard tests (fresh user, no workflow keys) and a call recorder:

```typescript
import { Page, Request, Route } from "@playwright/test";

/** Seed only the Supabase session — a brand-new user with no workflow state. */
export async function seedSessionOnly(page: Page): Promise<void> {
  await page.addInitScript((storageKey) => {
    const session = {
      access_token: "e2e-fake-access-token",
      refresh_token: "e2e-fake-refresh-token",
      token_type: "bearer",
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: "e2e-user", aud: "authenticated", role: "authenticated", email: "e2e@roomrate.test" },
    };
    window.localStorage.setItem(storageKey, JSON.stringify(session));
  }, SUPABASE_STORAGE_KEY);
}

/** Record every mocked call so specs can assert what did (not) happen. */
export type RecordedCall = { method: string; path: string; body: unknown };

export function recordCall(calls: RecordedCall[], route: Route): void {
  const request = route.request();
  calls.push({
    method: request.method(),
    path: new URL(request.url()).pathname,
    body: safePostJson(request),
  });
}

function safePostJson(request: Request): unknown {
  try {
    return request.postDataJSON();
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Run the existing suite to prove the extraction is behavior-neutral**

```bash
cd frontend && npm run e2e
```
Expected: same results as the Task 2 run. If `no-second-click` fails, the move broke an import — fix before continuing.

- [ ] **Step 3: Write the failing guard scenarios**

Create `frontend/e2e/setup-wizard.spec.ts`:

```typescript
/**
 * Spec §9 scenarios for the /setup wizard. Backend fully mocked; the only
 * moving part is the Angular app. Each test names the spec item it pins.
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, OWNED_PROPERTY_ID, seedSessionOnly } from "./helpers";

/** A brand-new account: signed in, but nothing onboarded yet. */
const FRESH_USER = {
  ...CURRENT_USER,
  onboarding_complete: false,
  owned_property_id: null,
  property_name: null,
  destination: null,
  raw_destination: null,
  canonical_destination: null,
  selected_room_type_category: null,
};

async function mockMe(page: Page, user: unknown): Promise<void> {
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: user }));
}

test("scenario 1: new user without property is redirected to /setup step 1", async ({ page }) => {
  await seedSessionOnly(page);
  await mockMe(page, FRESH_USER);

  await page.goto("/map");

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
});

test("scenario 1b: property present but no room selected lands on step 2", async ({ page }) => {
  await seedSessionOnly(page);
  await mockMe(page, { ...FRESH_USER, owned_property_id: OWNED_PROPERTY_ID, property_name: "E2E Test Hotel" });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/**`, (route) => route.fulfill({ json: [] }));

  await page.goto("/map");

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
});

test("scenario 6: fully onboarded user opening /setup is sent to the map", async ({ page }) => {
  await seedSessionOnly(page);
  // The map behind the redirect loads its own data; give the calls something
  // harmless so the URL assertion is not racing failed fetches. Registered
  // BEFORE the /me mock: Playwright matches routes last-registered-first, so
  // the specific mock must come after the wildcard to win.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, CURRENT_USER);

  await page.goto("/setup");

  await expect(page).toHaveURL(/\/map/);
});
```

- [ ] **Step 4: Run it to verify it fails**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```
Expected red phase (observed during execution, differs per scenario):
scenario 1 fails landing on `/auth` (a pre-existing `loadCurrentUser` redirect
in map-page bounces incomplete users there); scenario 1b fails landing on
`/map`; scenario 6 PASSES coincidentally — the `**` wildcard already bounces
the nonexistent `/setup` to `/map`, so that test's red phase proves nothing.
Its value starts only once `/setup` exists: it then fails unless the guard's
setup branch actively ejects a complete user.

- [ ] **Step 5: The guard**

`frontend/src/app/guards/setup.guard.ts`:

```typescript
import { inject } from "@angular/core";
import { CanActivateFn, Router } from "@angular/router";

import { OnboardingService } from "../services/onboarding.service";

/**
 * Reads GET /me and routes by what is missing (spec §3.1):
 *
 *   no owned property            -> /setup (wizard opens at step 1)
 *   property but no room chosen  -> /setup (wizard opens at step 2)
 *   both present                 -> normal navigation; /setup itself bounces
 *                                   to /map so the wizard is unreachable when
 *                                   there is nothing left to set up.
 *
 * Ordering vs authGuard: Angular evaluates route guards CONCURRENTLY
 * (prioritizedGuardValue); only their results are ranked, authGuard's first.
 * This guard still never fires a request for an unauthenticated user because
 * AuthService.getAccessToken throws before any fetch when there is no
 * session -- that early throw is the invariant this file relies on.
 *
 * State lives in the backend, so a user who leaves mid-wizard is returned to
 * the right step on the next visit (spec §8, last row). On /me failure the
 * guard lets navigation continue: a 401 has already been handled by
 * ApiClientService (sign-out + /auth redirect), so the catch covers only
 * blips and 5xx -- and the target pages own their error states.
 */
export const setupGuard: CanActivateFn = async (route) => {
  const onboarding = inject(OnboardingService);
  const router = inject(Router);
  const target = route.routeConfig?.path ?? "";
  let complete: boolean;
  try {
    const me = await onboarding.currentUser();
    complete = Boolean(me.owned_property_id) && Boolean(me.selected_room_type_category);
  } catch {
    return true;
  }
  if (target === "setup") {
    return complete ? router.createUrlTree(["/map"]) : true;
  }
  return complete ? true : router.createUrlTree(["/setup"]);
};
```

- [ ] **Step 6: The routes**

`frontend/src/app/app.routes.ts` becomes:

```typescript
import { Routes } from "@angular/router";

import { authGuard } from "./guards/auth.guard";
import { setupGuard } from "./guards/setup.guard";
import { AuthPageComponent } from "./pages/auth-page.component";
import { MapPageComponent } from "./pages/map-page.component";
import { PricingPageComponent } from "./pages/pricing-page.component";
import { SettingsPageComponent } from "./pages/settings-page.component";
import { SetupPageComponent } from "./pages/setup-page.component";

export const routes: Routes = [
  { path: "auth", component: AuthPageComponent },
  { path: "setup", component: SetupPageComponent, canActivate: [authGuard, setupGuard] },
  { path: "map", component: MapPageComponent, canActivate: [authGuard, setupGuard] },
  { path: "pricing", component: PricingPageComponent, canActivate: [authGuard, setupGuard] },
  { path: "settings", component: SettingsPageComponent, canActivate: [authGuard] },
  { path: "", pathMatch: "full", redirectTo: "map" },
  { path: "**", redirectTo: "map" },
];
```

`/settings` deliberately keeps only `authGuard`: a half-onboarded user must still be able to reach «Το κατάλυμά μου» to delete a wrong property (Task 10) without being bounced into the wizard.

- [ ] **Step 7: The wizard container and minimal step components**

`frontend/src/app/pages/setup-page.component.ts`. Container only: it owns the current step, the shared wizard data, and step transitions. Every API call lives in the step components via `OnboardingService`; the container never builds a request body (spec §3.2).

```typescript
import { Component, OnInit, signal } from "@angular/core";
import { CommonModule } from "@angular/common";
import { Router } from "@angular/router";

import { SetupPropertyStepComponent } from "../onboarding/setup-property-step.component";
import { SetupRoomStepComponent } from "../onboarding/setup-room-step.component";
import { SetupSearchStepComponent } from "../onboarding/setup-search-step.component";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { CurrentUser, PropertyCandidate } from "../types/market";

export type WizardStep = 1 | 2 | 3;

@Component({
  selector: "app-setup-page",
  standalone: true,
  imports: [CommonModule, SetupPropertyStepComponent, SetupRoomStepComponent, SetupSearchStepComponent],
  template: `
    <main class="page-shell setup-page">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate</p>
          <h1 data-testid="setup-step-title">Βήμα {{ step() }} από 3 — {{ stepTitle() }}</h1>
          <p class="muted">{{ stepSubtitle() }}</p>
        </div>
      </header>

      <section class="page-body">
        <div *ngIf="status() === 'loading'" class="progress-panel">
          <strong>Φόρτωση του λογαριασμού σας</strong>
        </div>
        <div *ngIf="status() === 'error'" class="alert alert-error">{{ error() }}</div>

        <ng-container *ngIf="status() === 'ready'">
          <app-setup-property-step
            *ngIf="step() === 1"
            [ownedPropertyId]="ownedPropertyId()"
            [proposedCandidate]="proposedCandidate()"
            [draftPropertyName]="draftPropertyName()"
            [draftLocation]="draftLocation()"
            (confirmed)="onPropertyConfirmed($event)"
          ></app-setup-property-step>

          <app-setup-room-step
            *ngIf="step() === 2"
            [ownedPropertyId]="ownedPropertyId()"
            [discoveryJobId]="discoveryJobId()"
            (selected)="onRoomSelected()"
            (skipped)="goToMap()"
          ></app-setup-room-step>

          <app-setup-search-step
            *ngIf="step() === 3"
            [ownedPropertyId]="ownedPropertyId()"
            (finished)="goToMap()"
            (postponed)="goToMap()"
          ></app-setup-search-step>
        </ng-container>
      </section>
    </main>
  `,
})
export class SetupPageComponent implements OnInit {
  readonly step = signal<WizardStep>(1);
  readonly status = signal<"loading" | "ready" | "error">("loading");
  readonly error = signal("");
  readonly ownedPropertyId = signal("");
  readonly proposedCandidate = signal<PropertyCandidate | null>(null);
  readonly discoveryJobId = signal("");
  readonly draftPropertyName = signal("");
  readonly draftLocation = signal("");

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const me = await this.onboarding.currentUser();
      this.applyUser(me);
    } catch (err) {
      this.status.set("error");
      this.error.set(err instanceof Error ? err.message : "Η φόρτωση του λογαριασμού απέτυχε.");
    }
  }

  stepTitle(): string {
    return ({ 1: "Το κατάλυμά σας", 2: "Το δωμάτιο που τιμολογείτε", 3: "Η πρώτη αναζήτηση" } as const)[this.step()];
  }

  stepSubtitle(): string {
    return ({
      1: "Επιβεβαιώστε ποιο κατάλυμα του Booking είστε, ώστε κάθε σύγκριση τιμών να χτίζεται στα σωστά δεδομένα.",
      2: "Διαλέξτε τον τύπο δωματίου που θα συγκρίνεται με την αγορά.",
      3: "Δείτε τι θα τρέξει και πόσο θα διαρκέσει, πριν ξεκινήσει οτιδήποτε.",
    } as const)[this.step()];
  }

  onPropertyConfirmed(event: { ownedPropertyId: string; discoveryJobId: string }): void {
    this.ownedPropertyId.set(event.ownedPropertyId);
    this.discoveryJobId.set(event.discoveryJobId);
    this.workflow.set("pendingCandidate", "");
    this.workflow.set("pendingDiscoveryJobId", event.discoveryJobId);
    this.step.set(2);
  }

  onRoomSelected(): void {
    this.step.set(3);
  }

  goToMap(): void {
    void this.router.navigateByUrl("/map");
  }

  private applyUser(me: CurrentUser): void {
    this.workflow.bindToSubject(me.auth_subject);
    this.draftPropertyName.set(this.workflow.get("draftPropertyName"));
    this.draftLocation.set(this.workflow.get("draftLocation"));
    this.ownedPropertyId.set(me.owned_property_id || "");
    this.discoveryJobId.set(this.workflow.get("pendingDiscoveryJobId"));
    let candidate: PropertyCandidate | null = null;
    const rawCandidate = this.workflow.get("pendingCandidate");
    if (rawCandidate) {
      try {
        candidate = JSON.parse(rawCandidate) as PropertyCandidate;
      } catch {
        // Self-heal: a corrupt value would otherwise re-fail on every load.
        this.workflow.set("pendingCandidate", "");
      }
    }
    this.proposedCandidate.set(candidate);
    // The guard guarantees something is missing. A property that exists but
    // was never explicitly confirmed (the auto-setup proposal is still in
    // storage) starts at step 1 for confirmation; otherwise skip to step 2.
    this.step.set(me.owned_property_id && !candidate ? 2 : 1);
    this.status.set("ready");
  }
}
```

Create the three step components as **minimal versions** — complete files that compile and render their step marker; Tasks 5-7 replace their bodies with the real UI. Do NOT invent extra inputs beyond these.

`frontend/src/app/onboarding/setup-property-step.component.ts`:
```typescript
import { Component, EventEmitter, Input, Output } from "@angular/core";
import { CommonModule } from "@angular/common";

import { PropertyCandidate } from "../types/market";

@Component({
  selector: "app-setup-property-step",
  standalone: true,
  imports: [CommonModule],
  template: `<section class="panel"><p>Βήμα 1</p></section>`,
})
export class SetupPropertyStepComponent {
  @Input() ownedPropertyId = "";
  @Input() proposedCandidate: PropertyCandidate | null = null;
  @Input() draftPropertyName = "";
  @Input() draftLocation = "";
  @Output() confirmed = new EventEmitter<{ ownedPropertyId: string; discoveryJobId: string }>();
}
```

`frontend/src/app/onboarding/setup-room-step.component.ts`:
```typescript
import { Component, EventEmitter, Input, Output } from "@angular/core";
import { CommonModule } from "@angular/common";

@Component({
  selector: "app-setup-room-step",
  standalone: true,
  imports: [CommonModule],
  template: `<section class="panel"><p>Βήμα 2</p></section>`,
})
export class SetupRoomStepComponent {
  @Input() ownedPropertyId = "";
  @Input() discoveryJobId = "";
  @Output() selected = new EventEmitter<void>();
  @Output() skipped = new EventEmitter<void>();
}
```

`frontend/src/app/onboarding/setup-search-step.component.ts`:
```typescript
import { Component, EventEmitter, Input, Output } from "@angular/core";
import { CommonModule } from "@angular/common";

@Component({
  selector: "app-setup-search-step",
  standalone: true,
  imports: [CommonModule],
  template: `<section class="panel"><p>Βήμα 3</p></section>`,
})
export class SetupSearchStepComponent {
  @Input() ownedPropertyId = "";
  @Output() finished = new EventEmitter<void>();
  @Output() postponed = new EventEmitter<void>();
}
```

- [ ] **Step 8: Run the wizard spec to verify it passes**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```
Expected: 3 passed.

- [ ] **Step 9: Run the whole e2e suite**

```bash
cd frontend && npm run e2e
```
Expected: everything green — `no-second-click` seeds a complete user whose `/me` mock satisfies the new guard on `/map`.

- [ ] **Step 10: Commit**

```bash
git add frontend/e2e/helpers.ts frontend/e2e/no-second-click.spec.ts frontend/e2e/setup-wizard.spec.ts frontend/src/app/guards/setup.guard.ts frontend/src/app/app.routes.ts frontend/src/app/pages/setup-page.component.ts frontend/src/app/onboarding/
git commit -m "Add /setup route, setupGuard and the wizard container

The guard reads /me and routes by what is missing, in both directions:
half-onboarded users cannot reach the map by typing the URL, and fully
onboarded users cannot reach the wizard. Settings keeps only authGuard so
a wrong property can still be deleted from there."
```

---

### Task 4: auth-page keeps auto-setup but stops pretending it finished

**Files:**
- Modify: `frontend/src/app/pages/auth-page.component.ts`
- Modify: `frontend/e2e/setup-wizard.spec.ts` (one scenario added)

The mix flow (spec §3.6): sign-up still calls `auto-setup` so the discovery job runs while the user reads the confirmation screen — but the result becomes a *proposal*. The page stores the proposed candidate + discovery job id and redirects to `/setup`; the polling loop and the «discovering-rooms» stage move into wizard step 2.

- [ ] **Step 1: Write the failing scenario**

Append to `frontend/e2e/setup-wizard.spec.ts` (imports gain `JOB_ID` and `recordCall`, `RecordedCall` from `./helpers`):

```typescript
const PROPOSED_CANDIDATE = {
  candidate_key: "rea-hotel",
  display_name: "Rea Hotel",
  booking_url: "https://www.booking.com/hotel/gr/rea.html",
  city: "Faliraki",
  address: "Leoforos Kallitheas 12",
  country: "Greece",
  property_type: "hotel",
  latitude: 36.34,
  longitude: 28.2,
  stars: 3,
  review_score: 8.7,
  review_count: 214,
};

test("sign-up runs auto-setup, then lands on /setup step 1 without polling", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedSessionOnly(page);
  await mockMe(page, FRESH_USER);
  await page.route(`${API}/api/v1/onboarding/auto-setup`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      status: 202,
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        selected_candidate: PROPOSED_CANDIDATE,
        discovery_job: { id: JOB_ID, status: "queued", job_type: "owned_property_room_discovery" },
      },
    });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: { id: JOB_ID, status: "queued" } });
  });

  // Drive the REAL auth page: seed the sign-up drafts the same way the form
  // does, then call the flow through the UI.
  await page.goto("/auth");
  await page.getByRole("button", { name: /create a new account/i }).click();
  await page.getByLabel(/accommodation name/i).fill("Rea Hotel");
  await page.getByLabel(/location/i).fill("Faliraki");
  await page.getByLabel(/email/i).fill("e2e@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-1");
  await page.getByRole("button", { name: /^create account$/i }).click();

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  // The proposal reached the wizard...
  expect(calls.some((c) => c.path === "/api/v1/onboarding/auto-setup" && c.method === "POST")).toBe(true);
  // ...and the auth page did NOT poll the discovery job (that is step 2's job).
  expect(calls.filter((c) => c.path === `/api/v1/scrape-jobs/${JOB_ID}`).length).toBe(0);
});
```

**Supabase note:** the mocked session from `seedSessionOnly` makes `authService.signUp` unnecessary — but the real page still calls it. Mock the Supabase auth endpoint too, at the top of this test:

```typescript
  // RegExp, not a glob: the real request carries ?redirect_to=..., and a
  // glob-string route does not match past the query -- the request would
  // escape to the real Supabase project (observed as a live 400).
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/signup/, (route) =>
    route.fulfill({
      json: {
        access_token: "e2e-fake-access-token",
        refresh_token: "e2e-fake-refresh-token",
        token_type: "bearer",
        expires_in: 3600,
        user: { id: "e2e-user", aud: "authenticated", role: "authenticated", email: "e2e@roomrate.test" },
      },
    }),
  );
```

If the sign-up flow proves too entangled with supabase-js internals to drive through the UI (e.g. it insists on email confirmation), fall back to asserting the same contract at the seam: seed `pendingCandidate`/`pendingDiscoveryJobId` in localStorage via `addInitScript`, `goto("/setup")`, and assert step 1 renders the proposal — and say so in the implementation report. The load-bearing assertions are the two `calls` checks; keep them in whichever variant survives.

- [ ] **Step 2: Run to verify it fails**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```
Expected: the new scenario FAILS (today the page polls the job and navigates to `/map`).

- [ ] **Step 3: Rewire `continueAfterAuth`**

In `frontend/src/app/pages/auth-page.component.ts`:

1. Replace the `continueAfterAuth` method body with:

```typescript
  private async continueAfterAuth(currentUser: CurrentUser): Promise<void> {
    this.workflow.bindToSubject(currentUser.auth_subject);
    if (currentUser.onboarding_complete && currentUser.owned_property_id) {
      this.storeCurrentUser(currentUser);
      this.setProgress("opening-map");
      await this.router.navigateByUrl("/map");
      return;
    }

    const propertyName = (this.propertyName.trim() || this.workflow.get("draftPropertyName")).trim();
    const location = (this.location.trim() || this.workflow.get("draftLocation")).trim();
    this.workflow.set("draftPropertyName", propertyName);
    this.workflow.set("draftLocation", location);

    // A property already exists (half-finished onboarding): the wizard's own
    // guard logic picks the right step; nothing to create here.
    if (currentUser.owned_property_id) {
      this.storeCurrentUser(currentUser);
      await this.router.navigateByUrl("/setup");
      return;
    }
    if (!propertyName || !location) {
      this.hasError.set(true);
      this.message.set("Συμπληρώστε όνομα καταλύματος και τοποθεσία για να συνεχίσετε.");
      return;
    }

    // Speed + control (spec §3.6): auto-setup starts the discovery job NOW so
    // it runs while the user reads the confirmation screen — but its pick is
    // a PROPOSAL. The wizard confirms or corrects it; nothing here polls.
    this.setProgress("matching-property");
    const stay = defaultStayDates();
    try {
      // Through OnboardingService, not a raw api.post: that class is the one
      // place onboarding URLs live (its own contract).
      const setup = await this.onboarding.autoSetup({
        property_name: propertyName,
        location,
        check_in: stay.checkIn,
        check_out: stay.checkOut,
        adults: 2,
        children: 0,
        rooms: 1,
        limit: 8,
      });
      this.workflow.set("ownedPropertyId", setup.owned_property_id);
      this.workflow.set("propertyName", setup.selected_candidate.display_name);
      this.workflow.set("destination", setup.selected_candidate.city || location);
      this.workflow.set("rawDestination", location);
      this.workflow.set("pendingCandidate", JSON.stringify(setup.selected_candidate));
      this.workflow.set("pendingDiscoveryJobId", setup.discovery_job.id);
      this.workflow.set("pendingSetupError", "");
    } catch (error) {
      // Swallows everything -- no candidate, Booking failure, rate limit or
      // quota 429, timeout, 5xx. NOT a dead end (spec §4 step 1): the
      // wizard's step 1 fallback lets the user fix name/location and search
      // again, and it surfaces this message verbatim (spec §8) so a quota
      // refusal is never reframed as "fix your input".
      this.workflow.set("pendingCandidate", "");
      this.workflow.set("pendingDiscoveryJobId", "");
      this.workflow.set(
        "pendingSetupError",
        error instanceof Error ? error.message : "",
      );
    }
    await this.router.navigateByUrl("/setup");
  }
```

2. Inject `OnboardingService` in the constructor (`private readonly onboarding: OnboardingService`, imported from `../services/onboarding.service`) — the rewritten method calls `this.onboarding.autoSetup(...)`. If nothing else in the file still uses `this.api` afterwards, remove the `ApiClientService` injection and import too; the `/me` call in `submit()` should likewise become `this.onboarding.currentUser()`.

3. Delete the now-unused `pollScrapeJob` method and the `"discovering-rooms"` member of the `AuthStep` union, its entries in `loadingLabel()` and `setProgress()`, and the unused `ScrapeJobResponse`/`AutomaticSetupResponse` imports if nothing else references them. Also delete the `private destroyed` field, `ngOnDestroy`, and the `OnDestroy` interface/import — they existed solely to cancel `pollScrapeJob`'s loop and are write-only dead code without it.

- [ ] **Step 4: Run the spec to verify it passes, then the whole suite**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/pages/auth-page.component.ts frontend/e2e/setup-wizard.spec.ts
git commit -m "Sign-up treats auto-setup as a proposal and hands off to /setup

The discovery job still starts immediately, so the usual (correct-match)
case wastes no time -- but the user now confirms the pick in the wizard
instead of being silently committed to it. Polling moves to wizard step 2;
auto-setup failure stores the drafts and becomes the step 1 fallback."
```

---

### Task 4b: One /me per navigation — mutation-invalidated cache

**Files:**
- Modify: `frontend/src/app/services/onboarding.service.ts`
- Modify: `frontend/src/app/pages/auth-page.component.ts` (one line)
- Modify: `frontend/src/app/pages/map-page.component.ts` (one call site)
- Modify: `frontend/src/app/pages/pricing-page.component.ts` (one call site)
- Modify: `frontend/e2e/setup-wizard.spec.ts` (one scenario)

> **Why (Task 3 quality review).** Every guarded navigation costs two
> serialized `/me` round-trips — the guard's and the destination page's — each
> also paying a supabase `navigator.locks` acquisition. A TTL cache cannot fix
> this safely: any TTL has a race where the user finishes a wizard step and
> navigates before expiry, landing back in the wizard. A promise cache that is
> invalidated by the four mutations that change onboarding state has no such
> window: completing step 2 calls `selectRoomType`, which invalidates, so the
> next guard read is fresh by construction.
>
> **Task 4b review outcome (folded into later tasks).** The invalidation audit
> found all six removal-mutants surviving the then-current suite. Kills were
> distributed: `selectRoomType` → Task 8's 5b; `deleteOwnedProperty` → Task
> 10's scenario 9 (its `/me` re-mock is now mandatory); `replaceOwnedProperty`
> → Task 10's new change-flow scenario; failure-caching and the sign-in
> invalidation → Task 5's two new pins. The audit also exposed the bootstrap
> `/me` read in `submit()` running BEFORE `continueAfterAuth`'s invalidation —
> a cross-account stale read on user switch — fixed by invalidating before
> that read (Step 3 below). Four scenario sketches also carried LIFO
> route-ordering bugs (wildcard registered last shadows every specific mock);
> all repaired in place. **Rule: in Playwright, register the wildcard FIRST.**

- [ ] **Step 1: Write the failing scenario**

Append to `frontend/e2e/setup-wizard.spec.ts`:

```typescript
test("a guarded navigation fetches /me exactly once (guard + page share the cache)", async ({ page }) => {
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    return route.fulfill({ json: CURRENT_USER });
  });

  await page.goto("/map");
  await page.waitForURL(/\/map/);
  // Deterministic anchor: the header renders the /me property name once
  // loadCurrentUser finishes -- no sleep, and a would-be second fetch has
  // its full window before the count is read.
  await expect(page.getByText("E2E Test Hotel").first()).toBeVisible();

  expect(meCalls).toBe(1);
});
```

Run it: expect FAIL with `meCalls` = 2 (guard + map page each fetch).

- [ ] **Step 2: The cache**

In `frontend/src/app/services/onboarding.service.ts`, replace `currentUser()` with:

```typescript
  // At most one /me between mutations: the guard and every page share it,
  // across navigations. Never caches a failure, and every mutation below
  // invalidates it, so a completed wizard step is visible to the next read.
  private meCache: Promise<CurrentUser> | null = null;

  currentUser(): Promise<CurrentUser> {
    return (this.meCache ??= this.api.get<CurrentUser>("/api/v1/me").catch((err) => {
      this.meCache = null;
      throw err;
    }));
  }

  invalidateCurrentUser(): void {
    this.meCache = null;
  }
```

Then make the four onboarding-state mutations invalidate. Convert each to
`async` and add the call after the awaited request, e.g. for `autoSetup`:

```typescript
  async autoSetup(body: { /* unchanged parameter type */ }): Promise<AutomaticSetupResponse> {
    const response = await this.api.post<AutomaticSetupResponse>("/api/v1/onboarding/auto-setup", body);
    this.invalidateCurrentUser();
    return response;
  }
```

Use `try { return await ... } finally { this.invalidateCurrentUser(); }` —
finally, not a call after the await: the server may have committed before a
5xx, and over-invalidating costs one fetch while a stale `/me` routes wrong.
Apply the same shape to `replaceOwnedProperty`, `deleteOwnedProperty` and
`selectRoomType` (parameter and return types unchanged).

- [ ] **Step 3: Invalidate on sign-in and migrate the raw /me call sites**

1. `auth-page.component.ts`: invalidate in TWO places. In `submit()`,
   immediately BEFORE the bootstrap `currentUser()` read (after successful
   sign-in/sign-up): a sign-out -> different sign-in without a reload would
   otherwise bind and store the previous account's cached `/me` — the
   bootstrap read is the one that matters. And as `continueAfterAuth`'s first
   line, defending any future caller of that method.
2. `map-page.component.ts` (`loadCurrentUser`, the `await this.api.get<CurrentUser>("/api/v1/me")` site): replace with `await this.onboarding.currentUser()`, injecting `OnboardingService` if not present.
3. `pricing-page.component.ts`: same replacement at its `/api/v1/me` site.

After this, `grep -rn '"/api/v1/me"' frontend/src/app/pages/` must return only
`auth-page.component.ts` hits that go through the service (or none), and the
service is the single `/me` reader.

- [ ] **Step 4: Run the scenario, then everything**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: the new scenario passes with exactly 1 `/me`; everything else green
(`no-second-click` included — its `/me` mock still answers, just once per
navigation now).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/services/onboarding.service.ts frontend/src/app/pages/auth-page.component.ts frontend/src/app/pages/map-page.component.ts frontend/src/app/pages/pricing-page.component.ts frontend/e2e/setup-wizard.spec.ts
git commit -m "Cache /me behind mutation invalidation; one fetch per navigation

The guard and the destination page each fetched /me serially on every
navigation. A promise cache in OnboardingService collapses them to one;
the four onboarding mutations and each fresh sign-in invalidate it, so a
completed wizard step is visible to the very next guard read -- the race
a TTL could never close."
```

---

### Task 5: Step 1 — «Είσαι εσύ;»

**Files:**
- Modify (replace body): `frontend/src/app/onboarding/setup-property-step.component.ts`
- Modify: `frontend/e2e/setup-wizard.spec.ts` (scenarios 2, 2β, the sign-up card assertion, and the fallback scenario)

> **Two obligations inherited from Task 4's review (not optional).** Its
> mutation audit left two survivors that only this task can kill: (a) an
> auth-page that silently fails to store the proposal — killed by asserting
> the proposal CARD after driving real sign-up, not only after seeding
> localStorage; (b) the auto-setup failure path — killed by the fallback
> scenario below driving a 500 end-to-end instead of seeding empty pendings.
> Both additions are part of this task's Step 1.

> **CLOSED FINDINGS from Task 5's quality review (closed 2026-08-01).**
> The findings below are retained as an audit trail. They were fixed and
> mutation-reviewed in `3e017a5`, `3544722`, `e1074a9` and `6906553`; do not
> treat them as pending work when resuming at Task 8.
>
> **C1 (Critical): the fallback's apply dead-ends when no property exists.**
> `applySelection()` calls `replaceOwnedProperty(this.ownedPropertyId, ...)`
> unconditionally, but after a failed auto-setup there IS no property:
> auth-page stores no id, `/me` has `owned_property_id: null`, the container
> passes `""`, and the PUT goes to `/api/v1/onboarding/owned-property/` with
> an empty path segment → 405 "Method Not Allowed" surfaces in English at the
> exact persona the fallback exists to rescue. Fix: (a) add a
> `createOwnedProperty(body)` wrapper to `OnboardingService` for the existing
> `POST /api/v1/onboarding/owned-property` (same request/response shapes as
> replace; invalidate `/me` in a `try/finally` like its siblings); (b) in
> `applySelection()`, branch: `this.ownedPropertyId` empty → create, else →
> replace; emit `confirmed` with the response's `owned_property_id` either
> way; (c) extend the fallback e2e scenario to drive search → pick → apply
> and assert the POST (currently it stops at the form assertions).
>
> **I1 (Important): the pick-mode mini map never renders, and the
> confirm-mode map instance is orphaned on mode switch.** `renderMap` reads
> the `#miniMap` ViewChild synchronously while the pick-mode div is still
> behind `*ngIf` (during `searching()`), so it resolves undefined and
> early-returns on EVERY pick-mode path; and `if (!this.map)` binds the map
> permanently to the destroyed confirm-mode container. Preferred fix: extract
> a small `MiniMapComponent` (inputs `candidates`, `selectedKey`; creates the
> map in its own `ngAfterViewInit`, removes it in `ngOnDestroy`, re-renders
> markers on input change) so the `*ngIf` branches create/destroy the whole
> component and the lifecycle is correct by construction. Also: re-check
> `destroyed` after the `await import("mapbox-gl")` (a destroy during chunk
> load currently leaks a never-removed map), and re-render markers when
> `selectedKey` changes so the teal highlight follows the pick. Note e2e is
> blind here (no WebGL) — reviewer reasoning is the only net; say so in the
> fix commit.
>
> **Minors to fold into the same fix pass:** (M1) the shared «Δοκιμάστε
> ξανά» retry always re-runs `search()` — after an apply failure it must
> retry the apply, not launch a fresh minutes-long Booking scrape; (M2)
> replace the `hasSearchedOnce` flag with an explicit
> `search(userInitiated: boolean)` — the flag is a proxy that silently
> re-opens the stale-error bug if the auto-search ever early-returns; (M3)
> align the pendingSetupError signal's doc comment with the post-`2adf0a8`
> semantics (a USER search clears the signal too); (M4 optional wording:
> «Δείξε άλλα» → «Εμφάνιση άλλων» costs a plan+test edit — decide, don't
> drift; «Αποθήκευση» in-flight label → «Γίνεται αποθήκευση...»); (M5) a
> defensive `stopMilestones()` at `startMilestones()` entry.

- [ ] **Step 1: Write the failing scenarios**

Append to `frontend/e2e/setup-wizard.spec.ts`. Per standing rule 2, the picked candidate in 2β is the SECOND of the list — a test that picks `candidates[0]` cannot tell "forwarded the user's pick" from "forwarded the backend's default".

```typescript
const OTHER_CANDIDATE = {
  candidate_key: "rea-hotel-annex",
  display_name: "Rea Hotel Annex",
  booking_url: "https://www.booking.com/hotel/gr/rea-annex.html",
  city: "Faliraki",
  address: "Leoforos Kallitheas 14",
  country: "Greece",
  property_type: "hotel",
  latitude: 36.341,
  longitude: 28.201,
  stars: 3,
  review_score: 8.2,
  review_count: 96,
};

/** Seed session + the auto-setup proposal exactly as auth-page stores it. */
async function seedProposal(page: Page): Promise<void> {
  await seedSessionOnly(page);
  await page.addInitScript(
    ({ candidate, jobId, propertyId }) => {
      window.localStorage.setItem("roomrate_pending_candidate:e2e-user", JSON.stringify(candidate));
      window.localStorage.setItem("roomrate_pending_discovery_job_id:e2e-user", jobId);
      window.localStorage.setItem("roomrate_owned_property_id:e2e-user", propertyId);
    },
    { candidate: PROPOSED_CANDIDATE, jobId: JOB_ID, propertyId: OWNED_PROPERTY_ID },
  );
}

test("scenario 2: confirming the proposal reaches step 2 with NO create/replace call", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  await mockMe(page, {
    ...FRESH_USER,
    owned_property_id: OWNED_PROPERTY_ID,
    property_name: "Rea Hotel",
    auth_subject: "e2e-user",
  });
  await page.route(`${API}/api/v1/onboarding/**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "queued" } }),
  );

  await page.goto("/setup");
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  await expect(page.getByText("Rea Hotel")).toBeVisible();
  await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  const writes = calls.filter((c) => c.method === "PUT" || c.method === "POST" || c.method === "DELETE");
  expect(writes).toEqual([]); // the property already exists — confirming costs nothing
});

test("scenario 2β: picking a DIFFERENT candidate PUTs that candidate, not the proposal", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  await mockMe(page, {
    ...FRESH_USER,
    owned_property_id: OWNED_PROPERTY_ID,
    property_name: "Rea Hotel",
    auth_subject: "e2e-user",
  });
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [PROPOSED_CANDIDATE, OTHER_CANDIDATE] });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      status: 202,
      json: {
        owned_property_id: OWNED_PROPERTY_ID,
        discovery_job: { id: JOB_ID, status: "queued", job_type: "owned_property_room_discovery" },
      },
    });
  });
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "queued" } }),
  );

  await page.goto("/setup");
  await page.getByRole("button", { name: "Δείξε άλλα" }).click();
  await expect(page.getByText("Rea Hotel Annex")).toBeVisible();
  await page.getByTestId(`candidate-${OTHER_CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  const put = calls.find(
    (c) => c.method === "PUT" && c.path === `/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}`,
  );
  expect(put).toBeTruthy();
  expect((put!.body as { display_name: string }).display_name).toBe("Rea Hotel Annex");
  expect((put!.body as { booking_url: string }).booking_url).toBe(OTHER_CANDIDATE.booking_url);
});
```

Then the two Task 4 obligations. First, extend the existing sign-up scenario
(`"sign-up runs auto-setup, then lands on /setup without polling"`): append
after its step-title assertion —

```typescript
  // Kills Task 4's surviving mutant: an auth-page that never stored the
  // proposal renders step 1's fallback, not this card.
  await expect(page.getByTestId("proposed-candidate")).toContainText("Rea Hotel");
```

Second, factor the sign-up driving block of that scenario into a local helper
`driveSignUp(page)` (the supabase RegExp mock + form fill + submit click,
exactly as already written there) and add the fallback scenario:

```typescript
test("auto-setup failure lands on /setup with the correction form, not an error page", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedSessionOnly(page);
  await mockMe(page, FRESH_USER);
  await page.route(`${API}/api/v1/onboarding/auto-setup`, (route) =>
    route.fulfill({ status: 500, json: { detail: "Booking search failed" } }),
  );
  await page.route(`${API}/api/v1/onboarding/property-candidates**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });

  await driveSignUp(page);

  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 1");
  // The fallback form carries the sign-up drafts, ready to correct.
  await expect(page.locator('input[name="refineName"]')).toHaveValue("Rea Hotel");
  await expect(page.locator('input[name="refineLocation"]')).toHaveValue("Faliraki");
});
```

(The step-1 component auto-searches in fallback mode; the empty candidates
mock makes that render the «Δεν βρέθηκαν καταλύματα» empty state, which is
the guidance-not-error behaviour spec §8 demands.)

**Also part of this task (Task 4 quality review):** step 1's fallback mode
must render the persisted `pendingSetupError` verbatim above the correction
fields (an alert row with the `alert` icon) and clear the key when a new
search starts -- that is where the backend's 429/quota text finally reaches
the user (spec §8). Display lifetime: the rendered message must SURVIVE the
mount-time auto-search (it explains why the user is in the fallback) but
clear on a user-initiated re-search -- a `hasSearchedOnce` flag in `search()`
distinguishes the two; keeping the old error above fresh results would read
as the new search failing. The fallback scenario should assert it: mock auto-setup's
500 with `detail: "Booking search failed"` and expect that exact text visible
on /setup before the correction.

**Two /me-cache pins (Task 4b review) also land in this task's Step 1:**

```typescript
test("a /me blip is not cached: the page's read refetches", async ({ page }) => {
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    if (meCalls === 1) {
      return route.fulfill({ status: 500, json: { detail: "blip" } });
    }
    return route.fulfill({ json: CURRENT_USER });
  });

  await page.goto("/map");

  // The guard fail-opens on the blip; the page's own read must be a FRESH
  // fetch, not the cached rejection -- a cached rejection would break every
  // later navigation until a hard reload.
  await expect(page).toHaveURL(/\/map/);
  await expect.poll(() => meCalls).toBe(2);
});


test("a second sign-in never reuses the previous account's /me", async ({ page }) => {
  let meBody: unknown = { ...CURRENT_USER };            // user A: complete
  let meCalls = 0;
  await seedSessionOnly(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    meCalls += 1;
    return route.fulfill({ json: meBody });
  });
  // Three realities the first execution discovered (keep them):
  // 1. supabase-js setSession() DECODES the access token as a real JWT --
  //    "e2e-fake-access-token" throws "Invalid JWT structure". Use a
  //    fakeJwt() helper producing an unsigned 3-segment token.
  // 2. setSession() follows up with GET /auth/v1/user -- mock it, or the
  //    request escapes to the live project.
  // 3. map-page's sign-out POSTs /auth/v1/logout -- mock that too.
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/token/, (route) =>
    route.fulfill({
      json: {
        access_token: fakeJwt("e2e-user-b"), refresh_token: "r", token_type: "bearer",
        expires_in: 3600,
        user: { id: "e2e-user-b", aud: "authenticated", role: "authenticated", email: "b@roomrate.test" },
      },
    }),
  );
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/user/, (route) =>
    route.fulfill({ json: { id: "e2e-user-b", aud: "authenticated", role: "authenticated", email: "b@roomrate.test" } }),
  );
  await page.route(/https:\/\/.*\.supabase\.co\/auth\/v1\/logout/, (route) =>
    route.fulfill({ status: 204, body: "" }),
  );

  await page.goto("/map");
  await expect(page).toHaveURL(/\/map/);

  // Sign out (SPA navigation, no reload); the backend now belongs to B.
  // Adapt the sign-out locator to map-page's real template if it differs.
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL(/\/auth/);
  // User B is HALF-onboarded (has a property): a truly-blank B would stop on
  // /auth in continueAfterAuth's missing-drafts branch (pre-existing Task 4
  // behavior; known coverage gap, recorded in the test comment).
  meBody = { ...FRESH_USER, owned_property_id: OWNED_PROPERTY_ID, auth_subject: "e2e-user-b" };
  const callsBefore = meCalls;

  await page.getByLabel(/email/i).fill("b@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-2");
  await page.getByRole("button", { name: /^sign in$/i }).click();

  // B lands in the wizard off a FRESH read -- a stale complete cache would
  // open user A's map for user B.
  await expect(page).toHaveURL(/\/setup/);
  expect(meCalls).toBeGreaterThan(callsBefore);
});
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```
Expected: the two new scenarios and both Task 4 obligations FAIL (the minimal
step renders no candidate card, no buttons, no fallback form).

- [ ] **Step 3: The real step 1 component**

Replace `frontend/src/app/onboarding/setup-property-step.component.ts` entirely with:

```typescript
import { AfterViewInit, Component, ElementRef, EventEmitter, Input, OnDestroy, Output, ViewChild, signal } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import type mapboxgl from "mapbox-gl";

import { environment } from "../../environments/environment";
import { EmptyStateComponent } from "../components/empty-state.component";
import { IconComponent } from "../components/icon.component";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { PropertyCandidate } from "../types/market";
import { defaultStayDates } from "../utils/date-defaults";

/**
 * Step 1: explicit confirmation of WHICH Booking property the user is.
 *
 * Three states: (a) a proposal from auto-setup -> confirm or ask for others,
 * (b) a candidate list -> pick one, PUT replaces the property and re-runs
 * discovery, (c) no proposal (auto-setup failed / no candidates) -> the
 * fallback form corrects name/location and searches again. Confirming the
 * proposal makes NO api call: the property already exists (spec §4).
 */
@Component({
  selector: "app-setup-property-step",
  standalone: true,
  imports: [CommonModule, FormsModule, EmptyStateComponent, IconComponent],
  template: `
    <section class="panel setup-step">
      <ng-container *ngIf="mode() === 'confirm' && proposedCandidate as candidate">
        <div class="setup-columns">
          <div class="candidate-card" data-testid="proposed-candidate">
            <h2>Βρήκαμε αυτό το κατάλυμα. Είστε εσείς;</h2>
            <strong>{{ candidate.display_name }}</strong>
            <p class="muted">
              {{ candidate.property_type || "Κατάλυμα" }}
              <ng-container *ngIf="candidate.stars"> · {{ candidate.stars }} αστέρια</ng-container>
            </p>
            <p>{{ candidate.address }}<ng-container *ngIf="candidate.city">, {{ candidate.city }}</ng-container></p>
            <p *ngIf="candidate.review_score" class="muted">
              Βαθμολογία {{ candidate.review_score }} ({{ candidate.review_count }} κριτικές)
            </p>
            <a [href]="candidate.booking_url" target="_blank" rel="noopener">Άνοιγμα στο Booking για έλεγχο</a>
          </div>
          <div #miniMap class="setup-mini-map"></div>
        </div>
        <div class="setup-actions">
          <button class="primary-button" type="button" (click)="confirmProposal()">Ναι, αυτό είναι</button>
          <button class="secondary-button" type="button" (click)="showOthers()">Δείξε άλλα</button>
        </div>
      </ng-container>

      <ng-container *ngIf="mode() === 'pick'">
        <div *ngIf="searching()" class="progress-panel" data-testid="candidate-progress">
          <strong>{{ searchMilestone() }}</strong>
          <span>Η αναζήτηση στο Booking είναι ζωντανή και μπορεί να διαρκέσει μερικά λεπτά.</span>
        </div>

        <ng-container *ngIf="!searching()">
          <app-empty-state
            *ngIf="!candidates().length"
            icon="search"
            title="Δεν βρέθηκαν καταλύματα"
            explanation="Το Booking δεν επέστρεψε αποτελέσματα για αυτό το όνομα και την τοποθεσία. Διορθώστε τα στοιχεία και δοκιμάστε ξανά."
          ></app-empty-state>

          <div class="setup-columns" *ngIf="candidates().length">
            <ul class="candidate-list">
              <li *ngFor="let candidate of candidates()">
                <button
                  type="button"
                  class="candidate-option"
                  [class.candidate-selected]="selectedKey() === candidate.candidate_key"
                  [attr.data-testid]="'candidate-' + candidate.candidate_key"
                  (click)="selectedKey.set(candidate.candidate_key)"
                >
                  <strong>{{ candidate.display_name }}</strong>
                  <span class="muted">{{ candidate.address }}<ng-container *ngIf="candidate.review_score"> · {{ candidate.review_score }}</ng-container></span>
                </button>
              </li>
            </ul>
            <div #miniMap class="setup-mini-map"></div>
          </div>

          <form class="setup-refine" (ngSubmit)="search()">
            <label><span>Όνομα καταλύματος</span>
              <input class="roomrate-input" name="refineName" [(ngModel)]="refineName">
            </label>
            <label><span>Τοποθεσία</span>
              <input class="roomrate-input" name="refineLocation" [(ngModel)]="refineLocation">
            </label>
            <button class="secondary-button" type="submit">Αναζήτηση ξανά</button>
          </form>

          <div class="setup-actions" *ngIf="candidates().length">
            <button
              class="primary-button"
              type="button"
              [disabled]="!selectedKey() || saving()"
              (click)="applySelection()"
            >{{ saving() ? "Αποθήκευση" : "Αυτό είναι το κατάλυμά μου" }}</button>
          </div>
        </ng-container>
      </ng-container>

      <div *ngIf="error()" class="alert alert-error">
        <app-icon name="alert" [size]="16"></app-icon>
        {{ error() }}
        <button class="text-button" type="button" (click)="search()">Δοκιμάστε ξανά</button>
      </div>
    </section>
  `,
  styles: [`
    .setup-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: stretch; }
    .setup-mini-map { min-height: 260px; border-radius: 0.5rem; background: #e2e8f0; }
    .candidate-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; max-height: 320px; overflow-y: auto; }
    .candidate-option { width: 100%; text-align: left; padding: 0.6rem 0.8rem; border: 1px solid #cbd5e1; border-radius: 0.5rem; background: #fff; display: flex; flex-direction: column; gap: 0.15rem; cursor: pointer; }
    .candidate-selected { border-color: #0f766e; box-shadow: 0 0 0 1px #0f766e; }
    .setup-actions { display: flex; gap: 0.75rem; margin-top: 1rem; }
    .setup-refine { display: flex; gap: 0.75rem; align-items: end; margin-top: 1rem; flex-wrap: wrap; }
    @media (max-width: 760px) { .setup-columns { grid-template-columns: 1fr; } }
  `],
})
export class SetupPropertyStepComponent implements AfterViewInit, OnDestroy {
  @Input() ownedPropertyId = "";
  @Input() proposedCandidate: PropertyCandidate | null = null;
  @Input() draftPropertyName = "";
  @Input() draftLocation = "";
  @Output() confirmed = new EventEmitter<{ ownedPropertyId: string; discoveryJobId: string }>();

  @ViewChild("miniMap") miniMapContainer?: ElementRef<HTMLDivElement>;

  readonly mode = signal<"confirm" | "pick">("confirm");
  readonly candidates = signal<PropertyCandidate[]>([]);
  readonly selectedKey = signal("");
  readonly searching = signal(false);
  readonly searchMilestone = signal("");
  readonly saving = signal(false);
  readonly error = signal("");
  refineName = "";
  refineLocation = "";

  private map: mapboxgl.Map | null = null;
  private markers: mapboxgl.Marker[] = [];
  private milestoneTimer: ReturnType<typeof window.setInterval> | null = null;
  private destroyed = false;

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
  ) {}

  ngAfterViewInit(): void {
    // No proposal stored (auto-setup failed or user restarted): straight to
    // the correction form instead of an empty confirm screen.
    if (!this.proposedCandidate) {
      this.mode.set("pick");
      this.refineName = this.draftPropertyName;
      this.refineLocation = this.draftLocation;
      void this.search();
      return;
    }
    this.refineName = this.draftPropertyName || this.proposedCandidate.display_name;
    this.refineLocation = this.draftLocation || this.proposedCandidate.city;
    void this.renderMap([this.proposedCandidate]);
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.stopMilestones();
    this.map?.remove();
  }

  confirmProposal(): void {
    // The property already exists; confirming is free (spec §4 step 1).
    this.confirmed.emit({
      ownedPropertyId: this.ownedPropertyId,
      discoveryJobId: this.workflow.get("pendingDiscoveryJobId"),
    });
  }

  showOthers(): void {
    this.mode.set("pick");
    void this.search();
  }

  async search(): Promise<void> {
    const propertyName = this.refineName.trim();
    const location = this.refineLocation.trim();
    if (!propertyName || !location) {
      this.error.set("Συμπληρώστε όνομα καταλύματος και τοποθεσία.");
      return;
    }
    this.error.set("");
    this.searching.set(true);
    this.startMilestones();
    const stay = defaultStayDates();
    try {
      const results = await this.onboarding.propertyCandidates({
        property_name: propertyName,
        location,
        check_in: stay.checkIn,
        check_out: stay.checkOut,
      });
      this.candidates.set(results);
      // Best match first is the backend's contract; preselect it.
      this.selectedKey.set(results[0]?.candidate_key ?? "");
      this.workflow.set("draftPropertyName", propertyName);
      this.workflow.set("draftLocation", location);
      await this.renderMap(results);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Η αναζήτηση απέτυχε.");
    } finally {
      this.stopMilestones();
      this.searching.set(false);
    }
  }

  async applySelection(): Promise<void> {
    const candidate = this.candidates().find((c) => c.candidate_key === this.selectedKey());
    if (!candidate) {
      return;
    }
    this.saving.set(true);
    this.error.set("");
    const stay = defaultStayDates();
    try {
      const response = await this.onboarding.replaceOwnedProperty(this.ownedPropertyId, {
        display_name: candidate.display_name,
        booking_url: candidate.booking_url,
        city: candidate.city,
        raw_destination: this.refineLocation.trim() || candidate.city,
        address: candidate.address,
        country: candidate.country,
        property_type: candidate.property_type,
        latitude: candidate.latitude,
        longitude: candidate.longitude,
        check_in: stay.checkIn,
        check_out: stay.checkOut,
      });
      this.workflow.set("propertyName", candidate.display_name);
      this.workflow.set("destination", candidate.city);
      this.confirmed.emit({
        ownedPropertyId: response.owned_property_id,
        discoveryJobId: response.discovery_job.id,
      });
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Η αλλαγή καταλύματος απέτυχε.");
    } finally {
      this.saving.set(false);
    }
  }

  /** Milestones instead of a spinner: the Booking scout can take minutes. */
  private startMilestones(): void {
    const milestones = [
      "Σύνδεση με το Booking",
      "Αναζήτηση καταλυμάτων στην περιοχή",
      "Έλεγχος ονομάτων και βαθμολογιών",
      "Σχεδόν έτοιμο — ταξινόμηση αποτελεσμάτων",
    ];
    let index = 0;
    this.searchMilestone.set(milestones[0]);
    this.milestoneTimer = window.setInterval(() => {
      index = Math.min(index + 1, milestones.length - 1);
      this.searchMilestone.set(milestones[index]);
    }, 20_000);
  }

  private stopMilestones(): void {
    if (this.milestoneTimer !== null) {
      window.clearInterval(this.milestoneTimer);
      this.milestoneTimer = null;
    }
  }

  /** Mini map, same dynamic-import pattern as map-page. Token missing -> skip. */
  private async renderMap(candidates: PropertyCandidate[]): Promise<void> {
    const token = environment.mapboxToken;
    const container = this.miniMapContainer?.nativeElement;
    const placeable = candidates.filter((c) => c.latitude != null && c.longitude != null);
    if (!token || !container || !placeable.length || this.destroyed) {
      return;
    }
    const mapboxModule = (await import("mapbox-gl")).default;
    mapboxModule.accessToken = token;
    if (!this.map) {
      this.map = new mapboxModule.Map({
        container,
        style: "mapbox://styles/mapbox/streets-v12",
        center: [placeable[0].longitude!, placeable[0].latitude!],
        zoom: 13,
      });
    }
    this.markers.forEach((marker) => marker.remove());
    this.markers = placeable.map((candidate) =>
      new mapboxModule.Marker({ color: candidate.candidate_key === this.selectedKey() ? "#0f766e" : "#64748b" })
        .setLngLat([candidate.longitude!, candidate.latitude!])
        .addTo(this.map!),
    );
    if (placeable.length > 1) {
      const bounds = new mapboxModule.LngLatBounds();
      placeable.forEach((c) => bounds.extend([c.longitude!, c.latitude!]));
      this.map.fitBounds(bounds, { padding: 40, maxZoom: 14 });
    }
  }
}
```

- [ ] **Step 4: Run the scenarios to verify they pass, then everything**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green. (The mini map never renders in e2e — the dev environment token is present but WebGL may be absent; nothing asserts on the map, only on the card and calls.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/onboarding/setup-property-step.component.ts frontend/e2e/setup-wizard.spec.ts
git commit -m "Step 1 of /setup: confirm or correct the auto-matched property

Confirming the proposal costs zero API calls; picking another candidate
PUTs the replace endpoint and hands the fresh discovery job to step 2.
No proposal degrades to the name/location correction form, so a failed
auto-setup is guidance, not an error page."
```

---

### Task 6: Step 2 — ποιο δωμάτιο τιμολογείς

**Files:**
- Modify (replace body): `frontend/src/app/onboarding/setup-room-step.component.ts`
- Modify: `frontend/e2e/setup-wizard.spec.ts` (scenario 3)

- [ ] **Step 1: Write the failing scenario**

Append to `frontend/e2e/setup-wizard.spec.ts`:

```typescript
test("scenario 3: picking a room PUTs selected-room-type and reaches step 3", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  await mockMe(page, {
    ...FRESH_USER,
    owned_property_id: OWNED_PROPERTY_ID,
    property_name: "Rea Hotel",
    auth_subject: "e2e-user",
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "completed", scrape_runs_count: 1 } }),
  );
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [
        {
          id: "33333333-3333-3333-3333-333333333333",
          owned_property_id: OWNED_PROPERTY_ID,
          room_type: "Double Room with Sea View",
          room_type_category: "double",
          sample_price_per_night_eur: 96,
          sample_facilities: "Balcony, Sea view",
          is_active: true,
        },
        {
          id: "33333333-3333-3333-3333-333333333334",
          owned_property_id: OWNED_PROPERTY_ID,
          room_type: "Family Suite",
          room_type_category: "suite",
          sample_price_per_night_eur: 180,
          sample_facilities: "Two bedrooms",
          is_active: true,
        },
      ],
    }),
  );
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: { owned_property_id: OWNED_PROPERTY_ID, selected_room_type_category: "suite", onboarding_complete: true },
    });
  });

  await page.goto("/setup");
  await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  // Non-default pick on purpose: the SECOND room, so a component hardcoding
  // the first row would fail (standing rule 2).
  await page.getByTestId("room-suite").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).click();

  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  const put = calls.find((c) => c.method === "PUT");
  expect(put).toBeTruthy();
  expect((put!.body as { room_type_category: string }).room_type_category).toBe("suite");
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```
Expected: scenario 3 FAILS (minimal step renders no rooms).

- [ ] **Step 3: The real step 2 component**

Replace `frontend/src/app/onboarding/setup-room-step.component.ts` entirely with:

```typescript
import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from "@angular/core";
import { CommonModule } from "@angular/common";

import { EmptyStateComponent } from "../components/empty-state.component";
import { IconComponent } from "../components/icon.component";
import { OnboardingService } from "../services/onboarding.service";
import { OwnedPropertyRoomType } from "../types/market";

/**
 * Step 2: pick the room type that gets priced against the market.
 *
 * Waits for step 1's discovery job first (milestone panel, not a spinner —
 * in the usual correct-auto-match case the job finished while the user read
 * step 1, so this shows nothing). An empty catalog is explained, with retry
 * and a way forward to the map (spec §4 step 2).
 */
@Component({
  selector: "app-setup-room-step",
  standalone: true,
  imports: [CommonModule, EmptyStateComponent, IconComponent],
  template: `
    <section class="panel setup-step">
      <div *ngIf="waiting()" class="progress-panel" data-testid="discovery-progress">
        <strong>{{ milestone() }}</strong>
        <span>Διαβάζουμε τον κατάλογο δωματίων του καταλύματός σας από το Booking. Συνήθως 1-3 λεπτά.</span>
      </div>

      <ng-container *ngIf="!waiting()">
        <app-empty-state
          *ngIf="!rooms().length && !error()"
          icon="bed"
          title="Δεν βρέθηκαν δωμάτια"
          explanation="Το Booking δεν επέστρεψε κατάλογο δωματίων για τις ημερομηνίες που ελέγξαμε. Μπορείτε να δοκιμάσετε ξανά ή να προχωρήσετε στον χάρτη και να επιστρέψετε αργότερα."
          actionLabel="Δοκιμάστε ξανά"
          (action)="reload()"
        ></app-empty-state>
        <div *ngIf="!rooms().length && !error()" class="setup-actions">
          <button class="text-button" type="button" (click)="skipped.emit()">Συνέχεια στον χάρτη</button>
        </div>

        <ul class="room-list" *ngIf="rooms().length">
          <li *ngFor="let room of rooms()">
            <button
              type="button"
              class="candidate-option"
              [class.candidate-selected]="selectedCategory() === room.room_type_category"
              [attr.data-testid]="'room-' + room.room_type_category"
              (click)="selectedCategory.set(room.room_type_category)"
            >
              <strong>{{ room.room_type }}</strong>
              <span class="muted">
                <ng-container *ngIf="room.sample_price_per_night_eur">{{ room.sample_price_per_night_eur }} € / διανυκτέρευση</ng-container>
                <ng-container *ngIf="room.sample_facilities"> · {{ room.sample_facilities }}</ng-container>
              </span>
            </button>
          </li>
        </ul>

        <div class="setup-actions" *ngIf="rooms().length">
          <button class="primary-button" type="button" [disabled]="!selectedCategory() || saving()" (click)="apply()">
            {{ saving() ? "Αποθήκευση" : "Αυτό το δωμάτιο" }}
          </button>
        </div>
      </ng-container>

      <div *ngIf="error()" class="alert alert-error">
        <app-icon name="alert" [size]="16"></app-icon>
        {{ error() }}
        <button class="text-button" type="button" (click)="reload()">Δοκιμάστε ξανά</button>
        <button class="text-button" type="button" (click)="skipped.emit()">Συνέχεια στον χάρτη</button>
      </div>
    </section>
  `,
  styles: [`
    .room-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  `],
})
export class SetupRoomStepComponent implements OnInit, OnDestroy {
  @Input() ownedPropertyId = "";
  @Input() discoveryJobId = "";
  @Output() selected = new EventEmitter<void>();
  @Output() skipped = new EventEmitter<void>();

  readonly waiting = signal(false);
  readonly milestone = signal("Αναμονή για την ανακάλυψη δωματίων");
  readonly rooms = signal<OwnedPropertyRoomType[]>([]);
  readonly selectedCategory = signal("");
  readonly saving = signal(false);
  readonly error = signal("");

  private destroyed = false;

  constructor(private readonly onboarding: OnboardingService) {}

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
  }

  async reload(): Promise<void> {
    this.error.set("");
    try {
      await this.waitForDiscovery();
      const rooms = await this.onboarding.roomTypes(this.ownedPropertyId);
      this.rooms.set(rooms);
      this.selectedCategory.set(rooms[0]?.room_type_category ?? "");
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Η ανακάλυψη δωματίων απέτυχε.");
    } finally {
      this.waiting.set(false);
    }
  }

  async apply(): Promise<void> {
    if (!this.selectedCategory()) {
      return;
    }
    this.saving.set(true);
    this.error.set("");
    try {
      await this.onboarding.selectRoomType(this.ownedPropertyId, this.selectedCategory());
      this.selected.emit();
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Η αποθήκευση του δωματίου απέτυχε.");
    } finally {
      this.saving.set(false);
    }
  }

  /** Poll step 1's discovery job until it settles; no job id -> nothing to wait for. */
  private async waitForDiscovery(): Promise<void> {
    if (!this.discoveryJobId) {
      return;
    }
    const milestones = ["Άνοιγμα της σελίδας του καταλύματος", "Ανάγνωση τύπων δωματίου", "Κατηγοριοποίηση και τιμές"];
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (this.destroyed) {
        return;
      }
      const job = await this.onboarding.getScrapeJob(this.discoveryJobId);
      if (job.status === "completed") {
        return;
      }
      if (job.status === "failed") {
        throw new Error(job.error_message || "Η ανακάλυψη δωματίων απέτυχε. Δοκιμάστε ξανά.");
      }
      this.waiting.set(true);
      this.milestone.set(milestones[Math.min(Math.floor(attempt / 4), milestones.length - 1)]);
      await new Promise((resolve) => window.setTimeout(resolve, 5000));
    }
    throw new Error("Η ανακάλυψη δωματίων διαρκεί ασυνήθιστα πολύ. Δοκιμάστε ξανά σε λίγο.");
  }
}
```

- [ ] **Step 4: Run the scenarios, then everything**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green. Scenario 3's job mock answers `completed` on the first poll, so the milestone panel never blocks the test.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/onboarding/setup-room-step.component.ts frontend/e2e/setup-wizard.spec.ts
git commit -m "Step 2 of /setup: pick the room being priced

Waits out step 1's discovery job with milestones (usually already done by
the time the user arrives), renders the catalog with sample price and
facilities for recognisability, and treats an empty catalog as guidance
with retry/skip instead of an error."
```

---

### Task 7: Step 3 — η πρώτη αναζήτηση, με φρουρά κόστους

**Files:**
- Modify (replace body): `frontend/src/app/onboarding/setup-search-step.component.ts`
- Modify: `frontend/e2e/setup-wizard.spec.ts` (scenarios 4 and 5a)

> **Deliberate simplification vs spec §4:** the spec sketches the step-3
> summary «με χάρτη και ακτίνα». This plan renders the region as text in the
> summary grid instead of mounting a third Mapbox instance for a static
> illustration — the cost gate's substance (what runs, dates, count, duration,
> cost statement, nothing before the click) is untouched. If the reviewer or
> user wants the radius map, it is an additive change to this component only.

- [ ] **Step 1: Write the failing scenarios**

Append to `frontend/e2e/setup-wizard.spec.ts`:

```typescript
/** Walk a fully-mocked user to step 3 and return the recorded calls. */
async function reachStepThree(page: Page): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];
  await seedProposal(page);
  await mockMe(page, {
    ...FRESH_USER,
    owned_property_id: OWNED_PROPERTY_ID,
    property_name: "Rea Hotel",
    destination: "Faliraki",
    raw_destination: "Faliraki",
    auth_subject: "e2e-user",
  });
  await page.route(`${API}/api/v1/scrape-jobs/${JOB_ID}`, (route) =>
    route.fulfill({ json: { id: JOB_ID, status: "completed", scrape_runs_count: 1 } }),
  );
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({
      json: [{
        id: "33333333-3333-3333-3333-333333333333",
        owned_property_id: OWNED_PROPERTY_ID,
        room_type: "Double Room",
        room_type_category: "double",
        sample_price_per_night_eur: 96,
        is_active: true,
      }],
    }),
  );
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/selected-room-type`, (route) =>
    route.fulfill({
      json: { owned_property_id: OWNED_PROPERTY_ID, selected_room_type_category: "double", onboarding_complete: true },
    }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      status: 202,
      json: { id: "55555555-5555-5555-5555-555555555555", status: "queued", job_type: "competitor_search" },
    });
  });

  await page.goto("/setup");
  await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();
  await page.getByTestId("room-double").click();
  await page.getByRole("button", { name: "Αυτό το δωμάτιο" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 3");
  return calls;
}

test("scenario 4: NO scrape-job POST happens before the explicit click", async ({ page }) => {
  const calls = await reachStepThree(page);

  // The cost statement is on screen and nothing was charged yet.
  await expect(page.getByTestId("cost-statement")).toBeVisible();
  expect(calls.filter((c) => c.method === "POST" && c.path === "/api/v1/scrape-jobs/")).toEqual([]);
});

test("scenario 5a: «Αργότερα» goes to the map without starting anything", async ({ page }) => {
  const calls = await reachStepThree(page);
  // Wildcard FIRST, /me re-mock AFTER (LIFO): steps 1-2 completed the
  // account, and the guard's post-«Αργότερα» read is FRESH (selectRoomType
  // invalidated the cache) -- it must see a complete user or it bounces
  // straight back to /setup.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMe(page, { ...CURRENT_USER, auth_subject: "e2e-user" });

  await page.getByRole("button", { name: "Αργότερα" }).click();

  await expect(page).toHaveURL(/\/map/);
  expect(calls.filter((c) => c.method === "POST" && c.path === "/api/v1/scrape-jobs/")).toEqual([]);
});
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list
```
Expected: both FAIL (`cost-statement` and the buttons do not exist yet).

- [ ] **Step 3: The real step 3 component**

Replace `frontend/src/app/onboarding/setup-search-step.component.ts` entirely with:

```typescript
import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from "@angular/core";
import { CommonModule } from "@angular/common";

import { IconComponent } from "../components/icon.component";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { defaultStayDates } from "../utils/date-defaults";

/**
 * Step 3: the cost gate. Everything the first search will do is stated
 * BEFORE any click, and absolutely nothing is posted until the user clicks
 * «Ξεκίνα την αναζήτηση» (spec success criterion 2). «Αργότερα» is a full
 * exit to the map; the checklist keeps the step visibly open.
 */
@Component({
  selector: "app-setup-search-step",
  standalone: true,
  imports: [CommonModule, IconComponent],
  template: `
    <section class="panel setup-step">
      <ng-container *ngIf="!running()">
        <div class="search-summary">
          <div><span>Περιοχή</span><strong>{{ destination() }}</strong></div>
          <div><span>Ημερομηνίες</span><strong>{{ checkIn() }} έως {{ checkOut() }}</strong></div>
          <div><span>Έως ανταγωνιστές</span><strong>8</strong></div>
          <div><span>Διάρκεια</span><strong>2-4 λεπτά</strong></div>
        </div>
        <p class="muted" data-testid="cost-statement">
          Κάθε αναζήτηση τραβά ζωντανά δεδομένα τιμών από το Booking και έχει κόστος.
          Δεν ξεκινά τίποτα χωρίς το δικό σας κλικ.
        </p>
        <div class="setup-actions">
          <button class="primary-button" type="button" (click)="start()">Ξεκίνα την αναζήτηση</button>
          <button class="secondary-button" type="button" (click)="postponed.emit()">Αργότερα</button>
        </div>
      </ng-container>

      <div *ngIf="running()" class="progress-panel" data-testid="search-progress">
        <strong>{{ milestone() }}</strong>
        <span>Μπορείτε να φύγετε από αυτή τη σελίδα — θα ειδοποιηθείτε όταν ολοκληρωθεί.</span>
      </div>

      <div *ngIf="error()" class="alert alert-error">
        <app-icon name="alert" [size]="16"></app-icon>
        {{ error() }}
        <button class="text-button" type="button" (click)="start()">Δοκιμάστε ξανά</button>
        <button class="text-button" type="button" (click)="postponed.emit()">Συνέχεια στον χάρτη</button>
      </div>
    </section>
  `,
  styles: [`
    .search-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 0.75rem; }
    .search-summary span { display: block; color: #64748b; font-size: 0.8rem; }
  `],
})
export class SetupSearchStepComponent implements OnInit, OnDestroy {
  @Input() ownedPropertyId = "";
  @Output() finished = new EventEmitter<void>();
  @Output() postponed = new EventEmitter<void>();

  readonly running = signal(false);
  readonly milestone = signal("");
  readonly error = signal("");
  readonly destination = signal("");
  readonly checkIn = signal("");
  readonly checkOut = signal("");

  private destroyed = false;

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
  ) {}

  ngOnInit(): void {
    const stay = defaultStayDates();
    this.checkIn.set(stay.checkIn);
    this.checkOut.set(stay.checkOut);
    this.destination.set(this.workflow.get("destination") || this.workflow.get("draftLocation"));
  }

  ngOnDestroy(): void {
    this.destroyed = true;
  }

  async start(): Promise<void> {
    this.error.set("");
    this.running.set(true);
    this.milestone.set("Εκκίνηση της αναζήτησης");
    const stay = defaultStayDates();
    try {
      const job = await this.onboarding.startCompetitorSearch({
        owned_property_id: this.ownedPropertyId,
        room_type_category: this.workflow.get("roomTypeCategory") || "double",
        destination: this.workflow.get("destination") || this.workflow.get("draftLocation"),
        raw_destination: this.workflow.get("rawDestination") || this.workflow.get("draftLocation"),
        check_in: stay.checkIn,
        check_out: stay.checkOut,
        adults: 2,
        children: 0,
        rooms: 1,
        filters_payload: { limit: 8 },
      });
      this.workflow.set("lastCompetitorJobId", job.id);
      await this.pollUntilDone(job.id);
      this.finished.emit();
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Η αναζήτηση απέτυχε.");
      this.running.set(false);
    }
  }

  private async pollUntilDone(jobId: string): Promise<void> {
    const milestones = ["Αναζήτηση καταλυμάτων στην περιοχή", "Ανάγνωση τιμών δωματίων", "Ταίριασμα με το δικό σας δωμάτιο"];
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (this.destroyed) {
        return;
      }
      const job = await this.onboarding.getScrapeJob(jobId);
      if (job.status === "completed") {
        return;
      }
      if (job.status === "failed") {
        throw new Error(job.error_message || "Η αναζήτηση απέτυχε.");
      }
      this.milestone.set(milestones[Math.min(Math.floor(attempt / 8), milestones.length - 1)]);
      await new Promise((resolve) => window.setTimeout(resolve, 5000));
    }
    throw new Error("Η αναζήτηση διαρκεί ασυνήθιστα πολύ. Μπορείτε να συνεχίσετε στον χάρτη — θα ειδοποιηθείτε όταν ολοκληρωθεί.");
  }
}
```

- [ ] **Step 4: Run the scenarios, then everything**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/onboarding/setup-search-step.component.ts frontend/e2e/setup-wizard.spec.ts
git commit -m "Step 3 of /setup: cost-gated first competitor search

Area, dates, competitor count, duration and an explicit cost statement are
on screen BEFORE anything runs; the POST fires only on the user's click,
and the e2e cost-guard asserts the absence of that call, not just the
happy path."
```

---

### Task 8: SetupProgressService and the checklist on the map

**Files:**
- Create: `frontend/src/app/services/setup-progress.service.ts`
- Create: `frontend/src/app/onboarding/setup-checklist.component.ts`
- Modify: `frontend/src/app/pages/map-page.component.ts` (feed the service, host the checklist)
- Modify: `frontend/e2e/setup-wizard.spec.ts` (scenario 5b)

**Reading of spec §9 scenario 5 («χάρτης με το checklist στο 3/5»):** after «Αργότερα», steps 1-2 are complete and step 3 («Πρώτη αναζήτηση») is the *next open step* — the checklist reads «Βήμα 3 από 5» as its call to action with 2 ticks. It cannot mean "3 complete": nothing has run a competitor search yet.

- [ ] **Step 1: Write the failing scenario**

Append to `frontend/e2e/setup-wizard.spec.ts`:

```typescript
test("scenario 5b: after «Αργότερα» the map checklist shows 2 done, step 3 open", async ({ page }) => {
  await reachStepThree(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/scrape-jobs/`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/tracked/competitors**`, (route) =>
    route.fulfill({ json: { owned_property_id: OWNED_PROPERTY_ID, room_type_category: "double", competitors: [] } }),
  );
  await mockMe(page, { ...CURRENT_USER, auth_subject: "e2e-user" }); // property + room now exist

  await page.getByRole("button", { name: "Αργότερα" }).click();

  await expect(page).toHaveURL(/\/map/);
  await expect(page.getByTestId("setup-checklist")).toBeVisible();
  await expect(page.getByTestId("checklist-progress")).toContainText("2 από 5");
  await expect(page.getByTestId("checklist-open-step")).toContainText("Πρώτη αναζήτηση");
});
```

Run it: `cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list` → the new scenario FAILS (no checklist exists).

- [ ] **Step 2: The service**

`frontend/src/app/services/setup-progress.service.ts`:

```typescript
import { Injectable, computed, signal } from "@angular/core";

import { CurrentUser, ScrapeJobResponse, TrackedCompetitorListResponse } from "../types/market";

export type ChecklistStep = {
  id: number;
  label: string;
  why: string;
  done: boolean;
};

/**
 * Checklist state (spec §5), computed ONLY from data the map already
 * fetches — the setters below are called at the map's existing fetch sites,
 * so the checklist adds zero requests to the critical path.
 *
 * Sources of truth per step:
 *   1 property connected   -> me.owned_property_id
 *   2 room chosen          -> me.selected_room_type_category
 *   3 first search         -> a completed competitor_search with runs > 0
 *   4 tracking 3+          -> tracked competitors list length
 *   5 first recommendation -> at least 2 completed competitor_search runs
 *                             (the recommendation needs a second run to compare)
 */
@Injectable({ providedIn: "root" })
export class SetupProgressService {
  private readonly user = signal<CurrentUser | null>(null);
  private readonly completedSearchRuns = signal(0);
  private readonly trackedCount = signal(0);

  readonly steps = computed<ChecklistStep[]>(() => {
    const me = this.user();
    const runs = this.completedSearchRuns();
    return [
      { id: 1, label: "Κατάλυμα συνδέθηκε", why: "Κάθε σύγκριση τιμών χτίζεται πάνω στο σωστό κατάλυμα.", done: Boolean(me?.owned_property_id) },
      { id: 2, label: "Δωμάτιο επιλέχθηκε", why: "Η αγορά συγκρίνεται με έναν συγκεκριμένο τύπο δωματίου.", done: Boolean(me?.selected_room_type_category) },
      { id: 3, label: "Πρώτη αναζήτηση", why: "Χωρίς αναζήτηση δεν υπάρχουν τιμές ανταγωνιστών στον χάρτη.", done: runs >= 1 },
      { id: 4, label: "Παρακολούθηση 3+ ανταγωνιστών", why: "Οι ειδοποιήσεις τιμών αφορούν όσους παρακολουθείτε.", done: this.trackedCount() >= 3 },
      { id: 5, label: "Πρώτη σύσταση τιμής", why: "Η σύσταση χρειάζεται δεύτερη αναζήτηση για να συγκρίνει.", done: runs >= 2 },
    ];
  });

  readonly doneCount = computed(() => this.steps().filter((step) => step.done).length);
  readonly nextOpenStep = computed(() => this.steps().find((step) => !step.done) ?? null);
  readonly complete = computed(() => this.doneCount() === 5);

  setUser(user: CurrentUser): void {
    this.user.set(user);
  }

  setJobs(jobs: ScrapeJobResponse[]): void {
    const runs = jobs
      .filter((job) => job.job_type === "competitor_search" && job.status === "completed")
      .reduce((total, job) => total + (job.scrape_runs_count || 0), 0);
    this.completedSearchRuns.set(runs);
  }

  setTracked(response: TrackedCompetitorListResponse): void {
    this.trackedCount.set(response.competitors.length);
  }
}
```

- [ ] **Step 3: The checklist component**

`frontend/src/app/onboarding/setup-checklist.component.ts`:

```typescript
import { Component, signal } from "@angular/core";
import { CommonModule } from "@angular/common";

import { IconComponent } from "../components/icon.component";
import { SetupProgressService } from "../services/setup-progress.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";

/**
 * Collapsible progress bar at the top of the map (spec §5). Each open step
 * explains WHY it matters. At 5/5 it hides permanently (per user, via
 * WorkflowStorageService). Thin progress bar, no checkbox glyphs (spec §7).
 */
@Component({
  selector: "app-setup-checklist",
  standalone: true,
  imports: [CommonModule, IconComponent],
  template: `
    <section
      *ngIf="!hidden()"
      class="setup-checklist"
      data-testid="setup-checklist"
    >
      <button class="checklist-header" type="button" (click)="collapsed.set(!collapsed())">
        <span data-testid="checklist-progress">{{ progress.doneCount() }} από 5 βήματα</span>
        <span class="checklist-bar"><span class="checklist-bar-fill" [style.width.%]="progress.doneCount() * 20"></span></span>
        <app-icon name="chevron-down" [size]="16"></app-icon>
      </button>
      <ul *ngIf="!collapsed()" class="checklist-steps">
        <li *ngFor="let step of progress.steps()" [class.step-done]="step.done">
          <app-icon *ngIf="step.done" name="check" [size]="14"></app-icon>
          <div>
            <strong [attr.data-testid]="step === progress.nextOpenStep() ? 'checklist-open-step' : null">
              {{ step.label }}
            </strong>
            <p *ngIf="!step.done" class="muted">{{ step.why }}</p>
          </div>
        </li>
      </ul>
    </section>
  `,
  styles: [`
    .setup-checklist { background: #fff; border: 1px solid #e2e8f0; border-radius: 0.5rem; margin-bottom: 0.75rem; }
    .checklist-header { width: 100%; display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 0.9rem; background: none; border: none; cursor: pointer; }
    .checklist-bar { flex: 1; height: 4px; border-radius: 2px; background: #e2e8f0; overflow: hidden; }
    .checklist-bar-fill { display: block; height: 100%; background: #0f766e; transition: width 0.3s; }
    .checklist-steps { list-style: none; margin: 0; padding: 0.25rem 0.9rem 0.75rem; display: flex; flex-direction: column; gap: 0.4rem; }
    .checklist-steps li { display: flex; gap: 0.5rem; align-items: baseline; }
    .checklist-steps p { margin: 0.1rem 0 0; font-size: 0.82rem; }
    .step-done strong { color: #64748b; font-weight: 500; }
  `],
})
export class SetupChecklistComponent {
  readonly collapsed = signal(false);

  constructor(
    readonly progress: SetupProgressService,
    private readonly workflow: WorkflowStorageService,
  ) {}

  hidden(): boolean {
    if (this.workflow.get("checklistHidden") === "1") {
      return true;
    }
    if (this.progress.complete()) {
      // 5/5: hide permanently for this user (spec §5).
      this.workflow.set("checklistHidden", "1");
      return true;
    }
    return false;
  }
}
```

- [ ] **Step 4: Feed the service from the map's existing fetches**

In `frontend/src/app/pages/map-page.component.ts`:

1. Imports: add `SetupChecklistComponent` and `SetupProgressService`; add `SetupChecklistComponent` to the component's `imports` array; inject `readonly setupProgress: SetupProgressService` in the constructor (public — the template does not use it, but keeping the injection visible beside the other services matches the file's style).
2. Template: as the FIRST child INSIDE `<section class="map-shell">`, immediately before `<div #mapContainer class="roomrate-map">`, add:

```html
        <app-setup-checklist></app-setup-checklist>
```

> **Amended after execution.** The original wording ("right before the map
> `<section>`") placed the checklist as a 4th direct child of `.map-grid`
> (grid-template-columns: 330px minmax(0,1fr) 390px) — auto-placement then
> shoves the Mapbox canvas into the 390px results column and wraps the
> results sidebar to a second row. Verified by isolated reproduction during
> execution; the inside-`.map-shell` placement is the correct reading of
> "checklist at the top of the map". Requires the `.map-shell`/`.roomrate-map`
> CSS change below (also amended), or the map clips/collapses.

2b. In `frontend/src/styles.css` (found by the map-clipping review): `.map-shell` gains `display: flex; flex-direction: column;` and `.roomrate-map` replaces `height: 100%` with `flex: 1; height: auto; min-height: 0;`. Without this, `.roomrate-map` keeps claiming the full shell height and `.map-grid`'s `overflow: hidden` clips the map's bottom 249px whenever the checklist (expanded by default for every mid-onboarding user) stacks above it. Pinned two-sided in scenario 5b: the map's bottom edge must coincide with the grid's (±1px) AND keep >200px height — one-sided "no overflow" stayed green with a zero-height map when only the `.map-shell` half of the rule was dropped.

3. After `const currentUser = await this.onboarding.currentUser();` (in `loadCurrentUser` — the site Task 4b migrated) add:

```typescript
      this.setupProgress.setUser(currentUser);
```

4. After `const jobs = await this.api.get<ScrapeJobResponse[]>("/api/v1/scrape-jobs/", new URLSearchParams({ limit: "50" }));` (currently line ~858) add:

```typescript
      this.setupProgress.setJobs(jobs);
```

5. While in `loadCurrentUser`, DELETE the pre-existing redirect
`if (!currentUser.onboarding_complete && !this.ownedPropertyId) { await this.router.navigateByUrl("/auth"); ... }`
(currently lines ~734-737). It predates the setup guard, which now intercepts
incomplete users before this component activates — and if it ever did fire
(guard /me blip + page /me success), it would send a mid-onboarding user to
/auth instead of /setup. The guard owns this routing now.

6. After `const tracked = await this.api.get<TrackedCompetitorListResponse>("/api/v1/tracked/competitors", params);` (currently line ~1030) add:

```typescript
      this.setupProgress.setTracked(tracked);
```

7. **(Amended after execution — restore-path feed.)** `findRestorableCompetitorJob` short-circuits before the jobs-list fetch when the stored `lastCompetitorJobId` resolves and `canRestoreJob` passes — which requires exactly the evidence that step 3 is done (`completed` + `competitor_search` + runs > 0). Feed the service on that branch too, or every returning user sees step 3 open precisely when it is provably done:

```typescript
      if (this.canRestoreJob(storedJob)) {
        this.setupProgress.setJobs([storedJob]);
        return storedJob;
      }
```

Accepted limitation (recorded, do not chase): the single restored job undercounts runs summed across multiple jobs, so step 5 can read 4/5 where a full list read would say 5/5 — the call to action it produces (run a second search) is still the right one, and any list-path load corrects it.

8. **(Amended after the quality round — tracked fetch is unconditional.)** The tracked-competitors GET + `setTracked` feed live in a `fetchTrackedCompetitors()` that runs on EVERY map load; only the apply-to-cards half keeps the old `!restoredSelection` / `competitors().length` gating. The original wiring fed `setTracked` solely inside `applyTrackedCompetitors`, which the restore path skips for any user with a stored competitor selection — i.e. step 4 never ticked for exactly the users who completed it (probe: «3 από 5» with 3 tracked competitors). Cost: one extra DB-read GET on the restore path; the original "zero new requests" claim is amended to "zero new Apify-costing requests".

- [ ] **Step 5: Run the scenario, then everything**

```bash
cd frontend && npx playwright test e2e/setup-wizard.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green. `no-second-click` still passes: its seeded user is complete but its mocked jobs/tracked data leave the checklist below 5/5 — the checklist renders collapsed-open above the map and asserts nothing there; if any of its locators become ambiguous because of the new section, fix the locator in THAT spec (report it), never by weakening the checklist.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/services/setup-progress.service.ts frontend/src/app/onboarding/setup-checklist.component.ts frontend/src/app/pages/map-page.component.ts frontend/e2e/setup-wizard.spec.ts
git commit -m "Add the 5-step setup checklist to the map

Progress is computed from data the map already fetches (me, the jobs list,
tracked competitors), so the checklist costs zero extra requests. Each open
step says why it matters; at 5/5 it hides permanently per user."
```

> **Task 8 review outcome (folded in).** Scenario 5b alone left the wiring
> unpinned: deleting `setJobs`/`setTracked` survived (its fixtures are empty,
> so post-call equals initial), as did ignoring the stored hide flag. Three
> scenarios were added: **5c** (one completed `competitor_search` with
> `scrape_runs_count: 1` + 3 tracked competitors → «4 από 5», open step
> «Πρώτη σύσταση τιμής» — kills both wiring mutants), a **restore-path**
> scenario (subject-scoped `roomrate_last_competitor_job_id:e2e-user` seed →
> «4 από 5»; also killed the vacuous-feed mutant `setJobs([])`), and a
> **hide-flag** scenario (`roomrate_checklist_hidden:e2e-user = "1"` →
> checklist count 0). Test-construction facts the restore scenarios forced:
> a non-empty room-types mock is mandatory (`ensureSelectedRoom` wipes the
> selection on `[]` and the restore guard never fires); the job fixture needs
> `owned_property_id` + `room_type_category` matching the account's room
> ("suite" after `reachStepThree`) or `canRestoreJob` rejects it; tracked
> entries are `{ property_id, room_package_id }`; `/maps/competitors` must be
> non-empty for `applyTrackedCompetitors` to run at all; `scrape-jobs/` and
> `maps/competitors` mocks need `**` (query strings defeat non-glob strings);
> and a scenario built on `reachStepThree` must NOT re-register its own
> wildcard (it would shadow the older specific room-types mock by LIFO).
>
> Residuals, recorded as decisions: (1) re-adding the deleted `/auth`
> redirect is a near-equivalent mutant — unreachable without engineering a
> guard/page `/me` divergence; not pinned. (2) Reverting only the
> `.roomrate-map` half of the CSS fix renders correctly via flex-shrink —
> equivalent mutant, not chased. (3) `hidden()` reads localStorage every CD
> cycle and writes it once at 5/5 from template evaluation — spec-authored,
> idempotent, left. (4) Scenario 5b's own `scrape-jobs/` mock is dead code
> (missing `**`) — harmless, both it and the wildcard return `[]`; chip
> task_c0280b15 exists for it.
>
> **Quality round (`129c3e6`), all findings fixed:** tracked fetch made
> unconditional so step 4 ticks for returning users (Critical; see amended
> item 8 above); map overlays (`.show-filters-button`, `.map-empty-state`)
> re-anchored into a new `.map-canvas` box so they stop painting over the
> checklist (`.map-shell` lost `position: relative`, non-intersection
> pinned in 5b); `SetupProgressService.reset()` wired into the app's
> SIGNED_OUT branch so a shared browser cannot write the permanent hide
> flag for the next account; run counts property-scoped
> (`job.owned_property_id === me?.owned_property_id`, plus a
> `Boolean(me?.owned_property_id)` guard so null never matches orphaned
> null — backend nulls, not deletes, jobs on property delete), pinned by an
> orphaned-job fixture in 5c; checklist defaults collapsed at ≤820px
> (mobile kept only a 151px map otherwise), pinned at 390×844; native
> Greek step labels («Σύνδεση καταλύματος», «Επιλογή δωματίου») and why
> texts; `aria-expanded` + chevron rotation on the toggle; step count
> derived from `steps().length` everywhere; 5/5 hide-flag WRITE pinned
> separately from the hidden state (kill-check proved hiding alone cannot
> detect a dropped write). Final state: **86 passed + 1 skipped**,
> independently re-run by the controller; build and tsc clean. The
> post-fix quality re-verification pass was **skipped at user request** —
> per-fix red/green and kill-check evidence exists in the transcript, but
> no reviewer has independently re-read `129c3e6`; if a regression
> surfaces in Tasks 9-11, start there.

---

### Task 9: Empty states — no fake zeros

**Files:**
- Modify: `frontend/src/app/pages/map-page.component.ts` (market snapshot + competitor list)
- Modify: `frontend/src/app/pages/pricing-page.component.ts` (price history)
- Create: `frontend/e2e/empty-states-and-settings.spec.ts` (scenarios 7 and 8)

- [ ] **Step 1: Write the failing scenarios**

Create `frontend/e2e/empty-states-and-settings.spec.ts`:

```typescript
/**
 * Spec §9 scenarios 7-9: empty screens explain instead of showing zeros,
 * and the DOM carries no emoji anywhere.
 */
import { expect, test } from "@playwright/test";

import { API, CURRENT_USER, OWNED_PROPERTY_ID, seedBrowserState } from "./helpers";

/** Every emoji block used anywhere near UI symbols, including the old bell. */
const EMOJI_PATTERN = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u;

test("scenario 7: with no data the market snapshot explains instead of showing 0 €", async ({ page }) => {
  await seedBrowserState(page);
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/onboarding/owned-property/${OWNED_PROPERTY_ID}/room-types`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`${API}/api/v1/scrape-jobs/`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));

  await page.goto("/map");

  await expect(page.getByTestId("market-snapshot-empty")).toBeVisible();
  const sidebar = page.locator(".results-sidebar");
  await expect(sidebar).not.toContainText("0 €");
  await expect(sidebar).not.toContainText("€0");
});

test("scenario 8: the DOM contains no emoji", async ({ page }) => {
  await seedBrowserState(page);
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));

  await page.goto("/map");
  await expect(page.locator("h1").first()).toBeVisible();

  const text = await page.evaluate(() => document.body.innerText);
  expect(EMOJI_PATTERN.test(text)).toBe(false);
});
```

Run: `cd frontend && npx playwright test e2e/empty-states-and-settings.spec.ts --reporter=list` → scenario 7 FAILS (the stats grid renders zeros). Scenario 8 passes already (the bell was fixed in Task 2) — keep it as the pin.

- [ ] **Step 2: Market snapshot empty state**

In `frontend/src/app/pages/map-page.component.ts`, template: the sidebar block

```html
          <h2>Market snapshot</h2>
          <div class="stats-grid">
            <div><span>Lowest</span><strong>{{ formatEuro(marketStats().minPrice) }}</strong></div>
            <div><span>Highest</span><strong>{{ formatEuro(marketStats().maxPrice) }}</strong></div>
            <div><span>Average</span><strong>{{ formatEuro(marketStats().averagePrice) }}</strong></div>
            <div><span>Review avg</span><strong>{{ marketStats().averageReview.toFixed(1) }}</strong></div>
          </div>
```

becomes

```html
          <h2>Market snapshot</h2>
          <div class="stats-grid" *ngIf="competitors().length; else marketSnapshotEmpty">
            <div><span>Lowest</span><strong>{{ formatEuro(marketStats().minPrice) }}</strong></div>
            <div><span>Highest</span><strong>{{ formatEuro(marketStats().maxPrice) }}</strong></div>
            <div><span>Average</span><strong>{{ formatEuro(marketStats().averagePrice) }}</strong></div>
            <div><span>Review avg</span><strong>{{ marketStats().averageReview.toFixed(1) }}</strong></div>
          </div>
          <ng-template #marketSnapshotEmpty>
            <div data-testid="market-snapshot-empty">
              <app-empty-state
                icon="chart"
                title="Δεν υπάρχουν ακόμη δεδομένα αγοράς"
                explanation="Τα στατιστικά εμφανίζονται μόλις ολοκληρωθεί η πρώτη αναζήτηση ανταγωνιστών για την περιοχή σας."
              ></app-empty-state>
            </div>
          </ng-template>
```

Add `EmptyStateComponent` to the component's `imports` array and its TS import line. The competitor results list already has a `results-empty` block for the loading state; where the list renders with zero competitors while `status() === 'ready'`, replace any bare "no results" markup with the same `<app-empty-state icon="search" ...>` pattern (title «Δεν βρέθηκαν συγκρίσιμα δωμάτια», explanation «Η αναζήτηση ολοκληρώθηκε αλλά δεν επέστρεψε δωμάτια συγκρίσιμα με το δικό σας για αυτές τις ημερομηνίες.»). If no such ready-and-empty markup exists (the section simply renders nothing), add the empty state as the `*ngIf="status() === 'ready' && !competitors().length"` branch of the results section.

> **Amended after execution.** (1) The gate above originally read
> `visibleCompetitors().length` — a plan bug: after a first search with
> nothing tracked and nothing selected, `visibleCompetitors()` is empty
> while `competitors()` holds N rows, so the snapshot claimed «Δεν
> υπάρχουν ακόμη δεδομένα αγοράς» beside a full results list (the exact
> fake-emptiness this task exists to kill). Gate on `competitors().length`
> — which is also what `marketStats()` computes from. (2) This task's
> scenario sketches originally registered the wildcard LAST — the same
> LIFO bug Task 4b's review documented; corrected to wildcard-FIRST, and
> `scrape-jobs/**` needs the glob suffix for `?limit=50`. (3) In the real
> pricing page the series is not stored raw: `sparkline()` is null when
> `buildSparkline` finds no usable runs, and a ready-and-empty English
> block already existed — the edit is markup replacement inside the
> existing `historyStatus() === 'ready' && !sparkline()` condition, not a
> new `#historyEmpty` template. (4) Scenario 7 also pins both Greek
> empty-state titles as `toContainText` on the sidebar; the pricing empty
> state is deliberately NOT pinned by a scenario (deferred to Task 11's
> smoke — recipe recorded there) to avoid adding parallel-load to an
> already flaky suite.
>
> **Quality round.** The controller's execution-time instruction to keep
> the pre-existing wide condition (`status() !== 'loading'`) on the
> competitor-list block was WRONG — the plan's ready-only condition above
> is load-bearing for the copy («Η αναζήτηση ολοκληρώθηκε...» must never
> render to a user who has not searched; `idle` is the default state on
> every fresh /map load). Shipped shape: two sibling blocks — ready+empty
> keeps the completed-search copy; idle+empty says «Δεν έχετε τρέξει
> ακόμη αναζήτηση» / «Ρυθμίστε τα φίλτρα και ξεκινήστε μια αναζήτηση
> ανταγωνιστών για να δείτε συγκρίσιμα δωμάτια.»; `error` renders neither
> (the alert owns that state). Scenario 7 pins the IDLE title (its page
> never searches). The snapshot else-template gained a loading branch
> («Αναζήτηση σε εξέλιξη») so a running search stops claiming «δεν
> υπάρχουν δεδομένα», with the `market-snapshot-empty` testid moved onto
> the `app-empty-state` itself; the emoji-guard regex was widened to the
> blocks a hotel app actually risks (⭐ stars, flag pairs, clocks — the
> old range caught none of them); scenario 8 scans full markup with a
> liveness anchor; `app-empty-state` no longer stacks on the legacy
> `results-empty` card styles.
>
> Residuals (decisions, not omissions): the ready-state competitor copy
> and the snapshot loading branch are UNPINNED (pinning either needs a
> mid-flight or completed-search fixture — load the flaky suite cannot
> afford; revisit after Task 11 fixes the 5b flake). `resultHint` is
> write-only dead code whose state strings document the status machine —
> delete only after a future round confirms nothing revives it.

- [ ] **Step 3: Price-history empty state**

In `frontend/src/app/pages/pricing-page.component.ts`: locate the template block that renders the series fetched by `GET /api/v1/market/price-history` (the signal set near line ~351). Wrap its chart/point rendering in `*ngIf="<series signal>()?.points?.length; else historyEmpty"` (use the actual signal name found in the file) and add:

```html
        <ng-template #historyEmpty>
          <app-empty-state
            icon="chart"
            title="Δεν υπάρχει ακόμη ιστορικό τιμών"
            explanation="Το ιστορικό χτίζεται από τις αναζητήσεις σας: χρειάζονται τουλάχιστον δύο ολοκληρωμένες αναζητήσεις για να φανεί μεταβολή."
          ></app-empty-state>
        </ng-template>
```

Add `EmptyStateComponent` to that component's imports too. (Notifications already render an explanatory empty text — spec §6 counts it as aligned; leave it.)

- [ ] **Step 4: Run the spec, then everything**

```bash
cd frontend && npx playwright test e2e/empty-states-and-settings.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green — including `no-second-click`, whose seeded flow has real competitor data, so the snapshot renders numbers exactly as before.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/pages/map-page.component.ts frontend/src/app/pages/pricing-page.component.ts frontend/e2e/empty-states-and-settings.spec.ts
git commit -m "Explain empty screens instead of rendering zero metrics

Market snapshot, competitor results and price history now show what is
missing and why when there is no data; a 0 EUR that is really 'no data yet'
no longer exists. E2e pins both the explanation and the absence of 0-euro
text, plus a DOM-wide no-emoji assertion."
```

---

### Task 10: Settings — «Το κατάλυμά μου»

**Files:**
- Modify: `frontend/src/app/guards/setup.guard.ts` (allow `?change=1` through)
- Modify: `frontend/src/app/pages/setup-page.component.ts` (honor `?change=1`)
- Modify: `frontend/src/app/pages/settings-page.component.ts`
- Modify: `frontend/e2e/empty-states-and-settings.spec.ts` (scenario 9)

- [ ] **Step 1: Write the failing scenario**

Append to `frontend/e2e/empty-states-and-settings.spec.ts` (imports gain `RecordedCall`, `recordCall`):

```typescript
test("scenario 9: deleting the property requires typing its name first", async ({ page }) => {
  const calls: RecordedCall[] = [];
  await seedBrowserState(page);
  // LIFO: wildcard first so the specific mocks after it win.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
  await page.route(`${API}/api/v1/settings/**`, (route) => route.fulfill({ json: {} }));
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

  await expect(page).toHaveURL(/\/setup/);
  expect(calls.filter((c) => c.method === "DELETE").length).toBe(1);
});
```

> **Amended after execution (scenario 9 corrections, all execution-verified):**
> (1) **Assertion order flipped** — the plan's original order (non-retrying
> DELETE-count before the polling URL wait) raced the in-flight DELETE under
> parallel workers; navigation cannot begin before the DELETE await resolves,
> so URL-first is strictly correct and the count keeps full teeth.
> (2) The sketch's `settings/**` mock referenced a NONEXISTENT endpoint —
> dropped; the page actually calls `/api/v1/schedule` (object) and
> `/api/v1/notifications/rules` (ARRAY — the wildcard's `{}` feeds a
> non-iterable to `*ngFor`, NG0900 aborts every render pass and the property
> section never binds; a specific `rules → []` mock is REQUIRED).
> (3) Scenario 9 also seeds subject-scoped `roomrate_selected_room_type_category:e2e-user`
> and `roomrate_last_competitor_job_id:e2e-user` up front and asserts both
> are gone after landing in /setup — without this, dropping the 7 workflow
> clears passes silently, and map/pricing read those keys as
> `workflow.get(...) || currentUser...`, so the deleted property's state
> would win over the next property's server truth.
> (4) In the change flow, drafts are blank (they are only seeded during
> first-time sign-up), so the property step's mount auto-search early-returns
> — the test drives the real refine form (`refineName`/`refineLocation` +
> «Αναζήτηση ξανά») instead of assuming an auto-populated list.

Also add the change-flow pin (Task 4b review — the only scenario anywhere
that kills a dropped `replaceOwnedProperty` invalidation). Define a local
candidate const for it:

```typescript
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
  await page.getByTestId(`candidate-${CANDIDATE.candidate_key}`).click();
  await page.getByRole("button", { name: "Αυτό είναι το κατάλυμά μου" }).click();
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
  await page.getByRole("button", { name: "Συνέχεια στον χάρτη" }).click();

  await expect(page).toHaveURL(/\/map/);
  // The PUT invalidated the cache, so the post-replace guard read is fresh.
  expect(meCalls).toBeGreaterThan(callsBeforeReplace);
});
```

Run: `cd frontend && npx playwright test e2e/empty-states-and-settings.spec.ts --reporter=list` → FAILS (the section does not exist). The `/me` re-mock before the confirm click is not optional: Task 4b's cache makes the guard's post-delete read fresh by construction, so a mock still reporting a property would bounce the navigation.

- [ ] **Step 2: Let a complete user reach step 1 deliberately**

In `frontend/src/app/guards/setup.guard.ts`, replace the `if (target === "setup")` block with:

```typescript
  if (target === "setup") {
    // «Αλλαγή καταλύματος» από τις ρυθμίσεις: a complete user may re-enter
    // the wizard explicitly; accidental visits still bounce to the map.
    if (route.queryParamMap.get("change") === "1") {
      return true;
    }
    return complete ? router.createUrlTree(["/map"]) : true;
  }
```

In `frontend/src/app/pages/setup-page.component.ts`: inject `route: ActivatedRoute` in the constructor (import from `@angular/router`), store `private readonly changeRequested = this.route.snapshot.queryParamMap.get("change") === "1";` as a field initialized in the constructor body (`this.changeRequested = route.snapshot.queryParamMap.get("change") === "1";` with the field declared `private changeRequested = false;`), and at the END of `applyUser` add:

```typescript
    if (this.changeRequested) {
      // Change-property flow: always start at step 1 in pick mode.
      this.proposedCandidate.set(null);
      this.step.set(1);
    }
```

- [ ] **Step 3: The settings section**

In `frontend/src/app/pages/settings-page.component.ts`, add a new `<section class="panel">` in the template AFTER the existing settings form (inside `page-body`), plus the supporting code. Template block:

```html
        <section *ngIf="status() === 'ready'" class="panel property-section">
          <div class="section-title">
            <h2>Το κατάλυμά μου</h2>
          </div>
          <div *ngIf="propertyName(); else noProperty" class="property-details">
            <p><strong>{{ propertyName() }}</strong></p>
            <p class="muted">{{ propertyDestination() }}</p>
            <div class="setup-actions">
              <button class="secondary-button" type="button" (click)="changeProperty()">Αλλαγή καταλύματος</button>
              <button class="danger-button" type="button" (click)="deleteRequested.set(true)">Διαγραφή καταλύματος</button>
            </div>
            <div *ngIf="deleteRequested()" class="delete-confirm">
              <p>
                Η διαγραφή αφαιρεί οριστικά: τα δωμάτια του καταλύματος, τους παρακολουθούμενους
                ανταγωνιστές και τους κανόνες ειδοποιήσεων. Διατηρείται το ιστορικό τιμών της αγοράς.
              </p>
              <label>
                <span>Πληκτρολογήστε το όνομα του καταλύματος για επιβεβαίωση</span>
                <input class="roomrate-input" data-testid="delete-confirm-input"
                       name="deleteConfirm" [ngModel]="deleteConfirmText()" (ngModelChange)="deleteConfirmText.set($event)">
              </label>
              <button class="danger-button" type="button"
                      [disabled]="deleteConfirmText() !== propertyName() || deleting()"
                      (click)="deleteProperty()">
                {{ deleting() ? "Διαγραφή" : "Οριστική διαγραφή" }}
              </button>
              <div *ngIf="deleteError()" class="alert alert-error">{{ deleteError() }}</div>
            </div>
          </div>
          <ng-template #noProperty>
            <p class="muted">Δεν υπάρχει συνδεδεμένο κατάλυμα. Ο οδηγός ρύθμισης θα σας καθοδηγήσει.</p>
          </ng-template>
        </section>
```

Supporting code in the component class (signals per the AGENTS.md rule — every write below happens after an `await`):

```typescript
  readonly propertyName = signal("");
  readonly propertyDestination = signal("");
  readonly deleteRequested = signal(false);
  readonly deleteConfirmText = signal("");
  readonly deleting = signal(false);
  readonly deleteError = signal("");
```

Populate the two display signals where the page already loads its data (or, if the page does not fetch `/me` today, add a call in `ngOnInit` via `OnboardingService.currentUser()` and set both):

```typescript
      const me = await this.onboarding.currentUser();
      this.propertyName.set(me.property_name || "");
      this.propertyDestination.set(me.raw_destination || me.destination || "");
```

Inject `OnboardingService`, `WorkflowStorageService` and `Router` (whichever are not already injected). Methods:

```typescript
  changeProperty(): void {
    void this.router.navigate(["/setup"], { queryParams: { change: "1" } });
  }

  async deleteProperty(): Promise<void> {
    const me = await this.onboarding.currentUser();
    if (!me.owned_property_id) {
      return;
    }
    this.deleting.set(true);
    this.deleteError.set("");
    try {
      await this.onboarding.deleteOwnedProperty(me.owned_property_id);
      // Property-scoped state is gone with the property.
      this.workflow.set("ownedPropertyId", "");
      this.workflow.set("propertyName", "");
      this.workflow.set("roomTypeCategory", "");
      this.workflow.set("pendingCandidate", "");
      this.workflow.set("pendingDiscoveryJobId", "");
      this.workflow.set("lastCompetitorJobId", "");
      this.workflow.set("lastCompetitorSelection", "");
      this.workflow.set("destination", "");
      this.workflow.set("rawDestination", "");
      this.workflow.set("canonicalDestination", "");
      this.workflow.set("draftPropertyName", "");
      this.workflow.set("draftLocation", "");
      this.workflow.set("pendingSetupError", "");
      await this.router.navigateByUrl("/setup");
    } catch (err) {
      this.deleteError.set(err instanceof Error ? err.message : "Η διαγραφή απέτυχε.");
    } finally {
      this.deleting.set(false);
    }
  }
```

> **Amended after the quality round (the original 7-key list was a plan
> bug).** Six more keys must clear: map/pricing read `rawDestination`/
> `canonicalDestination` as `workflow.get(...) || currentUser...`, so the
> DELETED property's destination would drive the next property's competitor
> searches and market history until the next sign-in; and uncleared
> `draftPropertyName`/`draftLocation` make the wizard's pick-mode mount
> auto-fire a LIVE paid Booking scrape for the just-deleted name.
> (`roomTypeId` self-heals via `ensureSelectedRoom`; `checklistHidden` is a
> UI preference, deliberately kept.) Scenario 9 seeds and asserts the
> destination keys too. Shipped shape also differs from the sketch above in
> four quality-driven ways: (1) the section owns a `propertyStatus` signal —
> gated on it, NOT on the schedule load's `status()`, so a /schedule 500
> cannot hide property management, and «Δεν υπάρχει συνδεδεμένο κατάλυμα»
> renders only after /me actually answers (no false flash; pinned by a
> delayed-/me scenario); property and rules load concurrently. (2)
> `deleteProperty` sets `deleting` before its first await, binds the
> workflow subject after the /me read, and navigates OUTSIDE the try/catch
> via a success flag so a navigation hiccup can never render «Η διαγραφή
> απέτυχε.» after a successful delete. (3) The confirm flow has «Άκυρο»,
> `aria-expanded` on the toggle, `aria-live` on the error, trimmed name
> matching, and a «Γίνεται διαγραφή...» busy label. (4) `onPropertyConfirmed`
> strips `?change=1` (replaceUrl) so reload/back after a committed replace
> cannot re-enter pick mode and fire a second paid discovery job.

Add `FormsModule` to the component imports if not present, and a `danger-button` style if the stylesheet lacks one:

```css
    .danger-button { background: #b91c1c; color: #fff; border: none; border-radius: 0.375rem; padding: 0.5rem 0.9rem; cursor: pointer; }
    .danger-button:disabled { opacity: 0.5; cursor: not-allowed; }
```

- [ ] **Step 4: Run the spec, then everything**

```bash
cd frontend && npx playwright test e2e/empty-states-and-settings.spec.ts --reporter=list && npm run e2e && npm run build
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/guards/setup.guard.ts frontend/src/app/pages/setup-page.component.ts frontend/src/app/pages/settings-page.component.ts frontend/e2e/empty-states-and-settings.spec.ts
git commit -m "Settings: view, change and delete the owned property

Change re-enters wizard step 1 explicitly (guard honors ?change=1 so a
complete user is not bounced). Delete demands typing the property name,
states what is lost and what survives, clears property-scoped local state
and lands on /setup to start over."
```

---

### Task 11: Final verification

**Files:** none modified.

- [ ] **Step 1: Full frontend verification**

```bash
cd frontend && npm run build && npm run e2e
```
Expected: build clean; ALL specs green — `setup-wizard.spec.ts` (8 scenarios), `empty-states-and-settings.spec.ts` (3 scenarios), `no-second-click.spec.ts` unchanged and green, `live-walkthrough.spec.ts` passed-or-skipped.

- [ ] **Step 2: Backend untouched**

```bash
python -m pytest api/tests -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/c3ffff9f-476e-4cf5-90da-c1bcf2b50c9a/scratchpad/pytest-tmp"
```
Expected: **565 passed** — this plan changed nothing under `api/`.

- [ ] **Step 3: Manual smoke script (run against the live app, report findings)**

With backend + `npm run dev` running: sign up fresh → confirm redirect to `/setup` with the proposal → «Δείξε άλλα» → pick → step 2 shows rooms → pick → step 3 shows the cost gate → «Αργότερα» → map shows the checklist at 2/5 with step 3 open → settings shows the property → delete with typed name → back in `/setup`. Also: open `/pricing` on an account with zero searches and confirm the Greek price-history empty state renders (its only automated pin was deferred — see below). Anything that diverges from this script is a finding, not a doc note.

**Known suite issues to resolve or consciously accept here (from Task 8/9 reviews):**

1. **Scenario 5b flake under load.** `setup-wizard.spec.ts` scenario 5b intermittently times out (60s) at the «Αργότερα» click when the FULL suite runs `fullyParallel` on many cores against the single shared dev server (~2 in 6 runs at 89 tests; 0 in 6 at 86). Product code is not the cause (verified by controlled runs at both commits). Mitigations to pick from: raise that test's timeout, make `reachStepThree` cheaper, or cap `workers` in `playwright.config.ts`. Do not ship Task 11 with a known 1-in-3 red rate.
2. **Scenario 8 scans dev-server DOM.** Its `outerHTML` no-emoji sweep ran
   clean against the dev bundle (inlined component styles). If Task 11 runs
   the spec against `ng build` output, re-confirm the no-false-positive
   claim there instead of assuming it carries over.
3. **Pricing empty-state pin (deferred from Task 9).** If it earns its cost after the flake is fixed, the recipe: `seedBrowserState` (its `canonical_destination` seed satisfies `loadHistory()`'s guard) → wildcard `${API}/api/v1/**` → `[]` FIRST → `${API}/api/v1/me` → `CURRENT_USER` → `${API}/api/v1/market/price-history**` → `{ check_in, check_out, points: [] }` (the wildcard's `[]` alone will NOT work — `buildSparkline` throws on it and renders the error alert, not the empty state) → goto `/pricing` → expect «Δεν υπάρχει ακόμη ιστορικό τιμών».

## Done criteria

- [x] `cd frontend && npm run build` → clean
- [x] `cd frontend && npm run e2e` → all specs green (final baseline: 91 passed + 1 self-skipping live walkthrough — the plan's original count grew through review-round pins)
- [x] `python -m pytest api/tests -q --basetemp=<scratchpad>/pytest-tmp` → 565 passed (backend untouched)
- [x] No emoji in the DOM (pinned by scenario 8); every new UI string Greek
- [x] The manual smoke script above walks end to end — live 2026-08-11 with a real paid search; sole exception: the «Δείξε άλλα» leg, blocked by the recorded live-only backend/timeout issue (chip task_79d4ce48), covered by the mocked suite until that lands

## What this plan deliberately does not do (recap)

The Greek translation of pre-existing pages (auth/map/pricing/settings labels), multi-property, i18n infrastructure, notifications redesign. The first of these is the natural next plan; its §7 term table («Market P25» → «Χαμηλό εύρος αγοράς», «Lead time» → «Ημέρες μέχρι την άφιξη», «Sample runs» → «Αναζητήσεις που συγκρίθηκαν») lives in the spec.
