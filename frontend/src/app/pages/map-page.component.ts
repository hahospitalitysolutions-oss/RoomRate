import { CommonModule } from "@angular/common";
import { Component, computed, ElementRef, inject, OnDestroy, OnInit, signal, ViewChild } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Router, RouterLink } from "@angular/router";

import { environment } from "../../environments/environment";
import { EmptyStateComponent } from "../components/empty-state.component";
import { IconComponent } from "../components/icon.component";
import { SelectMenuComponent, SelectMenuOption } from "../components/select-menu.component";
import { AppHeaderComponent } from "../components/app-header.component";
import { NotificationToastsComponent } from "../components/notification-toasts.component";
import { SetupChecklistComponent } from "../onboarding/setup-checklist.component";
import { ApiClientService } from "../services/api-client.service";
import { AuthService } from "../services/auth.service";
import { NotificationsService } from "../services/notifications.service";
import { OnboardingService } from "../services/onboarding.service";
import { SetupProgressService } from "../services/setup-progress.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import {
  Competitor,
  CompetitorMapMarker,
  MarketSummary,
  NearbyDestinationsResponse,
  OwnedPropertyRoomType,
  OwnPropertyMapInfo,
  RoomMatchRunRequest,
  RoomMatchRunResponse,
  ScrapeJobProgress,
  ScrapeJobResponse,
  ScrapeResultSummary,
  TrackedCompetitorListResponse,
} from "../types/market";
import { ensureFutureStay, isPastLocalDate } from "../utils/date-defaults";
import { areComparableRoomCategories } from "../utils/room-category";
import { CompetitorCardComponent } from "./map/competitor-card.component";
import { formatEuro, formatRadius, hasValidCoordinates, hotelKey, markerKey } from "./map/competitor-format";
import { CompetitorMapComponent, CompetitorMapSource } from "./map/competitor-map.component";
import { MapFiltersSidebarComponent } from "./map/map-filters-sidebar.component";
import {
  LoadingState,
  MatchSourceState,
  JobScope,
  EmptyResultReason,
  MapFilters,
  MAX_NEARBY_DESTINATIONS,
  DEFAULT_RADIUS_KM,
  MIN_RADIUS_KM,
  MAX_RADIUS_KM,
  SCRAPE_POLL_ATTEMPTS,
  SCRAPE_POLL_INTERVAL_MS,
  SCRAPE_POLL_FAILURE_LIMIT,
  JOB_RESULT_READ_LIMIT,
  ONLY_SELECTED_FILTER_NAME,
  SEARCH_PLOTTED_MESSAGE,
  SEARCH_FILTERED_MESSAGE,
  FILTER_HIDES_ALL_MESSAGE,
  AGENT_UNAVAILABLE_NOTICE,
  AGENT_IN_PROGRESS_NOTICE,
  AGENT_STILL_RUNNING_RETRIES,
  agentRunNotice,
  AUTO_MATCH_PENDING_MESSAGE,
  COMPARABLE_FILTER_NAME,
  COMPARABLE_HIDES_ALL_TITLE,
  COMPARABLE_HIDES_ALL_EXPLANATION,
  COMPARABLE_HIDES_ALL_MESSAGE,
  SEARCH_COMPARABLE_FILTERED_MESSAGE,
  SHOW_ALL_ROOMS_LABEL,
  START_SEARCH_MESSAGE,
  AUTO_PICKED_ROOM_HINT,
  DEFAULT_STAY,
} from "./map/map-page.constants";

// All async-written state is signal-based. Every API call in this component
// awaits getAccessToken() first, and supabase-js getSession() acquires a
// navigator.locks lock that zone.js cannot patch — so the continuation after
// EVERY await runs outside the Angular zone. Plain-field mutations there were
// invisible to change detection (data only appeared after an unrelated
// click); signal writes schedule change detection regardless of zone.
@Component({
  selector: "app-map-page",
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    CompetitorCardComponent,
    CompetitorMapComponent,
    EmptyStateComponent,
    IconComponent,
    MapFiltersSidebarComponent,
    AppHeaderComponent,
    NotificationToastsComponent,
    SetupChecklistComponent,
    SelectMenuComponent,
  ],
  template: `
    <main class="map-page">
      <!--
        The Aegean band (design Δ), compact so the map keeps the height. The
        count chip only reports: it sits in the band's actions, before the
        navigation, where the e2e suite reads it (.header-actions).
      -->
      <app-header [compact]="true">
        <span headerStatus class="status-chip" data-testid="header-count-chip">{{ status() === "loading" ? "Φόρτωση" : competitorCountLabel() }}</span>
        <span headerStatus class="status-chip status-chip-live" data-testid="header-live-chip">Ζωντανά δεδομένα</span>
        <p class="eyebrow">Χάρτης αγοράς</p>
        <h1>{{ propertyName() || filters().destination || "Στοχευμένη ανάλυση δωματίων ανταγωνιστών" }}</h1>
      </app-header>

      <!--
        «Φίλτρα» is a column left of the map, never an overlay on it (owner
        decision 2026-10-06). It starts open; «Απόκρυψη» folds it into a narrow
        rail, still left of the map, whose «Φίλτρα» button opens it again.
      -->
      <section class="map-grid" [class.filters-collapsed]="filtersCollapsed()">
        <aside *ngIf="filtersCollapsed()" class="filters-rail">
          <button
            class="filters-rail-button"
            type="button"
            data-testid="show-filters"
            aria-label="Εμφάνιση φίλτρων"
            (click)="toggleFiltersSidebar()"
          >
            <app-icon name="search" [size]="18"></app-icon>
            <span>Φίλτρα</span>
          </button>
        </aside>
        <aside
          *ngIf="!filtersCollapsed()"
          appMapFiltersSidebar
          class="filters-sidebar"
          [filters]="filters()"
          [nearbyDestinations]="nearbyDestinations()"
          [nearbyDraft]="nearbyDraft()"
          [canAddNearbyDestination]="canAddNearbyDestination()"
          [searching]="actionStatus() === 'loading'"
          [message]="message()"
          [searchWarnings]="searchWarnings()"
          [scrapeInFlight]="scrapeInFlight()"
          [scrapeProgressLabel]="scrapeProgressLabel()"
          [scrapeElapsedLabel]="scrapeElapsedLabel()"
          [error]="error()"
          (collapseRequest)="toggleFiltersSidebar()"
          (filterChange)="setFilter($event.key, $event.value)"
          (nearbyDraftChange)="nearbyDraft.set($event)"
          (nearbyAdd)="addNearbyDestination()"
          (nearbyRemove)="removeNearbyDestination($event)"
          (findRequest)="findCompetitors()"
        >
          <!--
            «Ταίριασμα δωματίου» as its own highlighted card at the foot of the
            «Φίλτρα» column (owner decision 2026-10-05), projected into the
            sidebar so its bindings stay on this page. Since 2026-11-13 the
            matching runs by itself right after every search, for ALL the
            owner's room types in one pass; the card only says where the
            scores come from and offers a re-run for when the AI failed.
            «Σύγκριση για» picks which room type's results are shown.
          -->
          <section class="match-controls is-on" data-testid="match-controls">
            <div class="checkbox-row match-toggle-row">
              <span class="match-toggle-icon" aria-hidden="true"><app-icon name="bed" [size]="20"></app-icon></span>
              <span class="match-toggle-text">
                <strong class="filter-section-label">Ταίριασμα δωματίου</strong>
                <span>Γίνεται αυτόματα μετά από κάθε αναζήτηση, για όλους τους τύπους δωματίων σας.</span>
              </span>
            </div>
            <!-- Agents (spec Α.4): ONE source badge for the scores on screen.
                 «Εκτίμηση AI» is only ever earned by an agent read — a false
                 source here would be worse than no badge at all. -->
            <div class="match-source-row">
              <span class="source-chip" data-testid="match-source-badge">{{ matchSourceLabel() }}</span>
              <button
                type="button"
                class="panel-toggle-button"
                data-testid="reassess-button"
                [disabled]="reassessInFlight() || autoMatchPending() || !canReassess()"
                (click)="reassessMatches()"
              >{{ reassessInFlight() ? "Ταίριασμα σε εξέλιξη…" : "Ξανά ταίριασμα" }}</button>
            </div>
            <div *ngIf="reassessNotice()" class="alert" [class.alert-error]="isAgentFailureNotice(reassessNotice())" data-testid="reassess-notice">{{ reassessNotice() }}</div>
          </section>
        </aside>

        <section class="map-shell">
          <app-setup-checklist></app-setup-checklist>
          <div
            class="map-canvas"
            appCompetitorMap
            [source]="mapSource"
            [emptyMessage]="visibleCompetitors().length ? null : mapEmptyMessage()"
            [ownCoordinatesMissing]="ownCoordinatesMissing()"
            [showLegend]="showMapLegend()"
            (markerClick)="flashCard($event)"
            (popupClose)="releaseCard($event)"
          ></div>
        </section>

        <aside class="results-sidebar">
          <!--
            The room the whole column is read for, as the column's first
            control (owner feedback 2026-09-30): the owner compares per room
            type, so the choice cannot live in the «Φίλτρα» form, which starts
            collapsed. A pure view filter (owner decision 2026-11-13): one
            matching pass already scored the job for every room type, so a
            switch only re-reads the markers, the summary and the scores with
            the room's id — no new search, no new matching run.
          -->
          <label class="compare-room-field">
            <span>Σύγκριση για</span>
            <app-select-menu
              testId="room-select"
              placeholder="Επιλέξτε το δωμάτιό σας"
              [emphasis]="true"
              [options]="roomOptions()"
              [value]="selectedRoomId()"
              (valueChange)="onRoomTypeChange($event)"
            ></app-select-menu>
          </label>
          <!--
            The auto-pick notice sits right under the dropdown it describes, in
            the panel that is always on screen: the filters sidebar starts
            collapsed and the skip path that causes the auto-pick never opens
            it, so a notice in there was seen by nobody.
          -->
          <p *ngIf="autoPickedRoom()" class="map-notice" data-testid="auto-picked-room-hint">
            {{ autoPickedRoomHint }}
          </p>
          <!--
            ONE summary (spec §4.7). There used to be two: this title over stats
            computed from the map sample, and «Η αγορά της περιοχής» from
            /market/summary further down — and the two disagreed on screen. Both
            numbers and prices now come from /market/summary alone.
          -->
          <h2>Σύνοψη αγοράς</h2>
          <div *ngIf="marketSummaryWithData() as summary" class="market-summary-block" data-testid="market-summary">
            <p class="market-summary-counts" data-testid="market-summary-counts">{{ marketCountsLine(summary) }}</p>
            <div class="kpi-strip">
              <div><span>Ελάχιστη</span><strong>{{ formatEuro(summary.price_min_eur) }}</strong></div>
              <div><span>Διάμεση</span><strong>{{ formatEuro(summary.price_median_eur) }}</strong></div>
              <div><span>Μέση</span><strong>{{ formatEuro(summary.price_avg_eur) }}</strong></div>
              <div><span>Μέγιστη</span><strong>{{ formatEuro(summary.price_max_eur) }}</strong></div>
              <div><span>Μέση βαθμολογία</span><strong>{{ (summary.avg_review_score || 0).toFixed(1) }}</strong></div>
            </div>
          </div>
          <ng-container *ngIf="!marketSummaryWithData()">
            <div *ngIf="status() === 'loading'" class="results-empty">
              <strong>Αναζήτηση σε εξέλιξη</strong>
              <span>Τα στατιστικά αγοράς θα εμφανιστούν μόλις ολοκληρωθεί η αναζήτηση.</span>
            </div>
            <!--
              Not while the summary read is still out (the markers land a moment
              earlier), and not over results on the map: «no market data» there
              would be false, whatever happened to the summary read.
            -->
            <app-empty-state
              *ngIf="status() !== 'loading' && marketSummaryStatus() !== 'loading' && !competitors().length"
              data-testid="market-snapshot-empty"
              icon="chart"
              title="Δεν υπάρχουν ακόμη δεδομένα αγοράς"
              explanation="Τα στατιστικά εμφανίζονται μόλις ολοκληρωθεί η πρώτη αναζήτηση ανταγωνιστών για την περιοχή σας."
            ></app-empty-state>
          </ng-container>

          <!-- Spec §4.8: a price recommendation built on the search on screen. -->
          <a
            *ngIf="activeJobId() as jobId"
            class="pricing-link"
            data-testid="pricing-for-search"
            routerLink="/pricing"
            [queryParams]="{ job: jobId }"
          >Σύσταση τιμής για αυτή την αναζήτηση</a>

          <button class="secondary-button" type="button" [disabled]="!selectedKeys().size || actionStatus() === 'loading'" (click)="saveTrackedCompetitors()">
            Προσθήκη επιλεγμένων στην παρακολούθηση
          </button>
          <div *ngIf="saveMessage()" class="save-feedback" [class.save-feedback-error]="saveHasError()">
            {{ saveMessage() }}
          </div>

          <!--
            Rendered only with results on screen: with nothing to filter the
            control would be a dead switch that says the map hides things.
          -->
          <label *ngIf="competitors().length" class="checkbox-row map-filter-toggle">
            <input
              type="checkbox"
              data-testid="only-selected-toggle"
              [checked]="showOnlySelected()"
              (change)="toggleShowOnlySelected()"
            >
            <span>{{ onlySelectedFilterName }}</span>
          </label>
          <!--
            «Μόνο συγκρίσιμα» (owner decision 2026-09-30). Rendered for as long
            as a job is on screen, not only while it has rows: on (the default)
            over a market with nothing comparable, it must stay there to be
            switched off again.
          -->
          <label *ngIf="activeJobId()" class="checkbox-row map-filter-toggle">
            <input
              type="checkbox"
              data-testid="comparable-toggle"
              [checked]="comparableOnly()"
              (change)="toggleComparableOnly()"
            >
            <span>{{ comparableFilterName }}</span>
          </label>
          <!-- The automatic matching run after a search: its pending line, then (on failure) the fallback notice. -->
          <div *ngIf="autoMatchPending()" class="results-empty" data-testid="auto-match-pending">
            <span>{{ autoMatchPendingMessage }}</span>
          </div>
          <div *ngIf="autoMatchNotice()" class="alert" [class.alert-error]="isAgentFailureNotice(autoMatchNotice())" data-testid="auto-match-notice">{{ autoMatchNotice() }}</div>


          <div *ngIf="status() === 'loading'" class="results-empty">
            <strong>Γίνεται αναζήτηση</strong>
            <span>Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.</span>
          </div>
          <div *ngIf="status() === 'ready' && !competitors().length">
            <app-empty-state
              icon="search"
              [title]="emptyResultTitle()"
              [explanation]="emptyResultExplanation()"
              [actionLabel]="emptyResultActionLabel()"
              (action)="onEmptyResultAction()"
            ></app-empty-state>
          </div>
          <div *ngIf="status() === 'idle' && !competitors().length">
            <app-empty-state
              icon="search"
              title="Δεν έχετε τρέξει ακόμη αναζήτηση"
              explanation="Ρυθμίστε τα φίλτρα και ξεκινήστε μια αναζήτηση ανταγωνιστών για να δείτε συγκρίσιμα δωμάτια."
            ></app-empty-state>
          </div>

          <!--
            Map ↔ list linking: hovering or focusing a card raises its hotel's
            marker, clicking it eases the map there; a marker click marks the
            hotel's cards (is-highlighted) while its popup is open and centres
            the first in the list. data-hotel-key is the marker's own key
            (hotelKey).
          -->
          <div class="competitor-list">
            <label
              *ngFor="let competitor of competitors()"
              appCompetitorCard
              class="competitor-card"
              [competitor]="competitor"
              [selected]="selectedKeys().has(markerKey(competitor))"
              [matchScore]="matchScoreFor(competitor.hotel_name)"
              (selectionToggle)="toggleCompetitor(markerKey(competitor))"
              [class.is-highlighted]="highlightedCardKey() === hotelKey(competitor)"
              [attr.data-hotel-key]="hotelKey(competitor)"
              (mouseenter)="highlightMarker(hotelKey(competitor))"
              (mouseleave)="unhighlightMarker(hotelKey(competitor))"
              (focusin)="highlightMarker(hotelKey(competitor))"
              (focusout)="unhighlightMarker(hotelKey(competitor))"
              (click)="easeToMarker($event, hotelKey(competitor))"
            ></label>
          </div>
          <!-- Once under the results, not per card (spec §5): where every shown price comes from. -->
          <p *ngIf="competitors().length" class="price-source-note" data-testid="price-source-note">
            Τιμές επίσημης ιστοσελίδας (desktop, χωρίς σύνδεση)
          </p>

        </aside>
      </section>

      <app-notification-toasts />
    </main>
  `,
})
export class MapPageComponent implements OnInit, OnDestroy {
  // The Mapbox map. Its markers are imperative, so the page still decides
  // WHEN they are redrawn (renderMarkers after a load, a tick or a filter
  // change); what they show is read through mapSource at that moment.
  @ViewChild(CompetitorMapComponent) private mapView?: CompetitorMapComponent;

  mapboxToken = environment.mapboxToken;
  // Bound to the filter checkbox's label, so the control and the two messages
  // that name it cannot drift apart.
  readonly onlySelectedFilterName = ONLY_SELECTED_FILTER_NAME;
  readonly comparableFilterName = COMPARABLE_FILTER_NAME;
  readonly autoPickedRoomHint = AUTO_PICKED_ROOM_HINT;
  readonly competitors = signal<CompetitorMapMarker[]>([]);
  readonly selectedKeys = signal<Set<string>>(new Set());
  // Optional "hide everything I did not tick" filter over the map. A finished
  // search plots ALL of its results (owner-observed 2026-08-22: nobody, the
  // owner included, discovered that the map only ever showed ticked cards), so
  // this is the way back to the narrow view — off by default, and deliberately
  // NOT reset by a new search or by resetCompetitorSearchState: it is the
  // user's own view preference, and silently re-showing everything they chose
  // to hide would undo a deliberate click. A signal, not a plain field: the
  // template and visibleCompetitors read it. It is only ever written by a
  // synchronous click handler, so it needs no chain-generation guard — the
  // guards exist for writes queued behind an await.
  readonly showOnlySelected = signal(false);
  // «Μόνο συγκρίσιμα» (owner decision 2026-09-30): ON by default, so every
  // read shows only the rooms the matching agent judged comparable with the
  // selected room; off, everything comes back with a `comparable` verdict per
  // package. Every competitors/markers/summary read sends it as
  // `comparable_only`. Written only by its own click handler.
  readonly comparableOnly = signal(true);
  // Jobs the automatic matching run has already been started for in this
  // visit, so each job is posted at most ONCE per page session and a failed
  // run is never retried by itself. A signal: it is written around awaits
  // (AGENTS.md).
  private readonly autoMatchDecided = signal<ReadonlySet<string>>(new Set());
  // The automatic run's pending line and its fallback notice: both written
  // after awaits, so signals.
  readonly autoMatchPending = signal(false);
  readonly autoMatchNotice = signal("");
  readonly autoMatchPendingMessage = AUTO_MATCH_PENDING_MESSAGE;
  // True once the user has touched competitor selection during this
  // navigation (toggleCompetitor). The load/restore chain's own selectedKeys
  // clears and its stored-selection/tracked-competitors apply below all
  // defer to it, because a click landing while that chain is still settling
  // was silently wiped by the chain's own writes (live-proven 2026-08-11:
  // the same click stuck once the chain had fully settled). Reset wherever
  // selection legitimately starts over: a fresh search and the initial
  // property/room context reset.
  private readonly userTouchedSelection = signal(false);
  readonly roomTypes = signal<OwnedPropertyRoomType[]>([]);
  readonly propertyName = signal("");
  readonly selectedRoomId = signal("");
  readonly selectedRoomType = signal("");
  readonly filtersCollapsed = signal(false);
  readonly status = signal<LoadingState>("idle");
  readonly actionStatus = signal<LoadingState>("idle");
  readonly message = signal(START_SEARCH_MESSAGE);
  // Machine-readable truth emitted by the scraper and persisted atomically
  // with job completion. A signal because it is written after async polling.
  private readonly activeResultSummary = signal<ScrapeResultSummary | null>(null);
  readonly saveMessage = signal("");
  readonly saveHasError = signal(false);
  readonly error = signal("");
  // True while the room in the dropdown was chosen by ensureSelectedRoom
  // rather than by the user or the wizard (see there). Starts false with every
  // navigation, so the notice cannot outlive the visit it describes.
  readonly autoPickedRoom = signal(false);
  readonly filters = signal<MapFilters>({
    destination: "",
    check_in: DEFAULT_STAY.checkIn,
    check_out: DEFAULT_STAY.checkOut,
    adults: "2",
    children: "0",
    rooms: "1",
    // Spec §3.1: the server default moved from 25 to 40; the page used to ask for 8.
    limit: "40",
    radius_km: String(DEFAULT_RADIUS_KM),
  });
  // «Γειτονικές περιοχές» (spec §4.1): prefilled from the server's defaults for
  // the destination, replaced by a restored job's own list. Signals: both
  // writers run after an await.
  readonly nearbyDestinations = signal<string[]>([]);
  readonly nearbyDraft = signal("");
  readonly maxNearbyDestinations = MAX_NEARBY_DESTINATIONS;
  readonly canAddNearbyDestination = computed(() => this.nearbyDestinations().length < MAX_NEARBY_DESTINATIONS);
  // True once the user added or removed an area, so late server defaults never
  // overwrite a list the user already shaped.
  private readonly nearbyEdited = signal(false);
  // Progress of the search this page started (spec §4.2): true from the Find
  // click until its job completes or fails. The progress comes from each poll
  // and the clock from a 1 s interval — both outside the zone, so signals.
  readonly scrapeInFlight = signal(false);
  readonly scrapeProgress = signal<ScrapeJobProgress | null>(null);
  private readonly scrapeClockStartedAt = signal<number | null>(null);
  private readonly scrapeClockNow = signal(Date.now());
  private scrapeClockTimer: ReturnType<typeof window.setInterval> | null = null;
  readonly scrapeProgressLabel = computed(() => this.describeScrapeProgress(this.scrapeProgress()));
  readonly scrapeElapsedLabel = computed(() => {
    const startedAt = this.scrapeClockStartedAt();
    if (startedAt === null) {
      return "";
    }
    const seconds = Math.max(0, Math.floor((this.scrapeClockNow() - startedAt) / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  });
  readonly marketSummary = signal<MarketSummary | null>(null);
  // Where the shown match scores come from (spec Α.4). Written after the
  // /competitors/ await — outside the zone, so a signal like its siblings.
  // "statistical" until a read proves otherwise: that is the honest default
  // (spec Α.5), and «Εκτίμηση AI» is only ever earned by agent rows.
  readonly matchSource = signal<MatchSourceState>("statistical");
  readonly matchSourceLabel = computed(() => {
    const source = this.matchSource();
    if (source === "agent") {
      return "Εκτίμηση AI";
    }
    return source === "partial" ? "Εκτίμηση AI (μερική)" : "Στατιστική εκτίμηση";
  });
  // «Ξανά ταίριασμα» (spec Α.1): in-flight flag and fallback notice are
  // both written around awaits, so both are signals.
  readonly reassessInFlight = signal(false);
  readonly reassessNotice = signal("");
  // The POST needs a completed job (activeJobId is only ever set for one);
  // it scores every room type of the property, whichever is on screen.
  readonly canReassess = computed(() => Boolean(this.activeJobId()));
  readonly roomOptions = computed<SelectMenuOption[]>(() =>
    this.roomTypes().map((item) => ({
      value: item.id,
      label: `${item.room_type} (${item.room_type_category})`,
      display: item.room_type,
      hint: item.room_type_category,
    })),
  );
  readonly selectedOwnedRoom = computed(() =>
    this.roomTypes().find((item) => item.id === this.selectedRoomId())
      || this.roomTypes().find((item) => item.room_type_category === this.selectedRoomType()),
  );
  /**
   * True when «Μόνο συγκρίσιμα» is the only thing that can explain an empty
   * screen: `comparable_only=true` is a READ filter, so a job whose rows
   * exist can come back empty because of the switch alone. A job carrying NO
   * summary counts too (older jobs, and the ones this page restores from a
   * list); `rows_written === 0` falls through — the scrape itself wrote
   * nothing, and the summary-based reasoning below has the truthful answer.
   * "ready" is load-bearing: a FAILED read leaves the same shape behind, and
   * nothing is known about what the filter hides until a read has succeeded.
   */
  private readonly comparableFilterHidesEverything = computed(() => {
    if (
      this.status() !== "ready"
      || !this.comparableOnly()
      || this.competitors().length
      || !this.activeScrapeJobId()
    ) {
      return false;
    }
    const summary = this.activeResultSummary();
    return !summary || summary.version !== 1 || summary.rows_written > 0;
  });

  private readonly emptyResultReason = computed<EmptyResultReason | null>(() => {
    // Asked first: it requires `rows_written > 0` and every branch below
    // requires `rows_written === 0`, and reversed, a job whose rows were
    // written and then filtered out on the read would fall out of here with
    // NO reason at all. It also answers the cases the summary cannot: a
    // missing or non-v1 summary reaches nothing below.
    if (this.comparableFilterHidesEverything()) {
      return { kind: "comparable" };
    }
    const summary = this.activeResultSummary();
    if (!summary || summary.version !== 1 || summary.rows_written !== 0) {
      return null;
    }
    const counts = summary.filter_counts ?? {};
    // The two stages before storage, in the scraper's own order: the first one
    // that left nothing is the answer, with how many offers it was handed.
    for (const kind of ["single_rooms", "capacity"] as const) {
      const stage = counts[kind];
      if (stage && stage.before > 0 && stage.after === 0) {
        return { kind, count: stage.before };
      }
    }
    // `room_name` and `amenities` stay listed: jobs from before 2026-11-13
    // still carry their counts.
    const remainingFilters = ["room_name", "meal", "free_cancellation", "amenities"];
    const emptiedLater = remainingFilters.some((name) => {
      const stage = counts[name];
      return Boolean(stage && stage.before > 0 && stage.after === 0);
    });
    // What reached those later filters: the offers the first two stages kept.
    const keptCount = counts["capacity"]?.after ?? counts["single_rooms"]?.after ?? summary.rows_seen;
    return emptiedLater && keptCount > 0
      ? { kind: "remaining-filters" as const, count: keptCount }
      : null;
  });

  readonly emptyResultTitle = computed(() => {
    const reason = this.emptyResultReason();
    if (!reason) {
      return "Δεν βρέθηκαν συγκρίσιμα δωμάτια";
    }
    if (reason.kind === "comparable") {
      return COMPARABLE_HIDES_ALL_TITLE;
    }
    const count = reason.count;
    if (reason.kind === "remaining-filters") {
      // «Συγκρίσιμες», no longer «της κατηγορίας σας»: since Round 6 the scraper
      // keeps similar categories too, so these offers are not all the owner's.
      return count === 1
        ? "Βρέθηκε 1 συγκρίσιμη προσφορά"
        : `Βρέθηκαν ${count} συγκρίσιμες προσφορές`;
    }
    if (reason.kind === "single_rooms") {
      return count === 1
        ? "Βρέθηκε 1 προσφορά για μονόκλινο δωμάτιο"
        : `Βρέθηκαν ${count} προσφορές για μονόκλινα δωμάτια`;
    }
    return count === 1
      ? "Βρέθηκε 1 τιμή για λιγότερα άτομα από όσα ζητήσατε"
      : `Βρέθηκαν ${count} τιμές για λιγότερα άτομα από όσα ζητήσατε`;
  });
  // The only one of these empty states the user can undo from where they are
  // standing — the read filter — so the only one that offers a button
  // (onEmptyResultAction). Empty elsewhere, and the empty-state component
  // renders no button for an empty label.
  readonly emptyResultActionLabel = computed(() =>
    this.emptyResultReason()?.kind === "comparable" ? SHOW_ALL_ROOMS_LABEL : "",
  );
  readonly emptyResultExplanation = computed(() => {
    const reason = this.emptyResultReason();
    if (reason?.kind === "comparable") {
      return COMPARABLE_HIDES_ALL_EXPLANATION;
    }
    // Both pre-storage stages answer in the same sentence, naming what the
    // offers were; the way forward is a search that reaches more of the market.
    if (reason?.kind === "single_rooms" || reason?.kind === "capacity") {
      const offers = reason.kind === "single_rooms" ? "μονόκλινα δωμάτια" : "λιγότερα άτομα από όσα ζητήσατε";
      return `Η αναζήτηση ολοκληρώθηκε και επέστρεψε τιμές, όλες όμως για ${offers}. `
        + "Δοκιμάστε άλλες ημερομηνίες ή περισσότερες γειτονικές περιοχές και τρέξτε ξανά την αναζήτηση.";
    }
    if (reason?.kind === "remaining-filters") {
      return "Καμία από αυτές δεν πέρασε τα υπόλοιπα φίλτρα ονόματος, γευμάτων, ακύρωσης ή παροχών.";
    }
    return "Η αναζήτηση ολοκληρώθηκε αλλά δεν επέστρεψε δωμάτια συγκρίσιμα με το δικό σας για αυτές τις ημερομηνίες.";
  });
  // What the map actually plots. Every result with usable coordinates goes on
  // the map as soon as a search finishes; ticking a card selects it for
  // TRACKING, not for display. `showOnlySelected` is the one way selection
  // narrows the map, and it is off unless the user turned it on.
  //
  // Coordinates are filtered here rather than in renderMarkers so the map's
  // empty-state overlay is driven by what can really be drawn: results that
  // all lack coordinates leave a genuinely empty map, and the overlay has to
  // say so instead of staying hidden behind a non-zero count.
  readonly plottableCompetitors = computed(() =>
    this.competitors().filter((competitor) => hasValidCoordinates(competitor)),
  );
  readonly visibleCompetitors = computed(() => {
    const plottable = this.plottableCompetitors();
    if (!this.showOnlySelected()) {
      return plottable;
    }
    return plottable.filter((competitor) => this.selectedKeys().has(this.markerKey(competitor)));
  });
  // «Εσείς» (spec §4.3): the owner's own hotel from /maps/own-property, for the
  // job on screen or for no job. Written after an await, so a signal; null
  // until it is read, and after a failed read (said nowhere: best effort).
  readonly ownProperty = signal<OwnPropertyMapInfo | null>(null);
  readonly ownLocation = computed(() => {
    const own = this.ownProperty();
    if (!own || typeof own.latitude !== "number" || typeof own.longitude !== "number") {
      return null;
    }
    const valid = Number.isFinite(own.latitude) && Number.isFinite(own.longitude)
      && Math.abs(own.latitude) <= 90 && Math.abs(own.longitude) <= 180;
    return valid ? { lng: own.longitude, lat: own.latitude } : null;
  });
  // The API knows the hotel and cannot place it: the map says so instead of
  // leaving the owner to wonder where «Εσείς» went.
  readonly ownCoordinatesMissing = computed(() => Boolean(this.ownProperty()) && !this.ownLocation());
  readonly showMapLegend = computed(() =>
    Boolean(this.mapboxToken) && (this.visibleCompetitors().length > 0 || this.ownLocation() !== null));
  // The summary box renders only a summary that describes something: a read
  // that came back empty (or not as an object at all) leaves the empty states.
  readonly marketSummaryWithData = computed(() => {
    const summary = this.marketSummary();
    return summary && (summary.total_hotels > 0 || summary.total_records > 0) ? summary : null;
  });
  // Whether the summary read for the result on screen is still out. Written
  // around an await, so a signal; the empty state waits for it (see template).
  readonly marketSummaryStatus = signal<LoadingState>("idle");
  // The scrape's own warnings about the job on screen, as sentences (spec §7).
  // A code this page does not know yet is left out rather than shown raw.
  readonly searchWarnings = computed(() => {
    const warnings = this.activeResultSummary()?.warnings;
    return (Array.isArray(warnings) ? warnings : [])
      .map((code) => this.describeSearchWarning(code))
      .filter((sentence) => Boolean(sentence));
  });

  private ownedPropertyId = "";
  private rawDestination = "";
  // Best match score per hotel name, for the result cards and the popups. A
  // signal: written after the /competitors/ await, outside the zone, and the
  // cards read it from the template.
  private readonly hotelMatchScores = signal<ReadonlyMap<string, number>>(new Map());
  // The match-score read loadMarkers started last, so the automatic run can
  // judge the badge it produced instead of racing it.
  private matchScoresLoad: Promise<void> = Promise.resolve();
  // Map ↔ list linking, keyed by hotelKey: the card marked after a marker
  // click, for as long as that marker's popup stays open (the map raises the
  // marker of a hovered card itself). A signal: it is written from Mapbox DOM
  // listeners (AGENTS.md), and the template reads it.
  readonly highlightedCardKey = signal<string | null>(null);
  private readonly hostElement: ElementRef<HTMLElement> = inject(ElementRef);
  // What the map draws, read by the map at render time.
  readonly mapSource: CompetitorMapSource = {
    visibleCompetitors: () => this.visibleCompetitors(),
    selectedKeys: () => this.selectedKeys(),
    ownProperty: () => this.ownProperty(),
    ownLocation: () => this.ownLocation(),
    popupMatchScore: (hotelName) => this.matchScoreFor(hotelName),
  };
  // The card/marker identities and the one format the page itself renders.
  readonly markerKey = markerKey;
  readonly hotelKey = hotelKey;
  readonly formatEuro = formatEuro;
  // Signals, not plain fields (chip task_a934b51e): both are written after an
  // await inside the load/restore chains, i.e. outside the Angular zone, so
  // AGENTS.md puts them on the signal side of the line even though nothing
  // renders them directly today. The conversion is behaviour-neutral — it only
  // removes the standing invitation to read them from a template later and get
  // a stale value.
  private readonly activeScrapeJobId = signal("");
  // The job on screen, for the template: «Μόνο συγκρίσιμα» and the link to
  // its price recommendation exist only while there is one. Only ever set for
  // a completed job (loadMarkers runs for nothing else).
  readonly activeJobId = this.activeScrapeJobId.asReadonly();
  // Id of the load chain that owns the screen. findCompetitors bumps it FIRST,
  // so every write still queued behind an await in an older chain (the restore
  // chain on load, the tail of a previous search) is dropped instead of
  // landing on the new search. Live-proven 2026-08-11 (job b3a1c5e8): a Find
  // click ~1s after /map opened left "the search finished and returned
  // nothing" on screen for the 2+ minutes the new job really ran, because the
  // OLD chain's loadMarkers tail (competitors + status "ready") overwrote the
  // fresh "loading". Deliberately a plain counter, not a signal: it is never
  // rendered and never read from the template, so the AGENTS.md rule (signals
  // so out-of-zone writes still trigger change detection) does not apply — the
  // writes it gates are themselves signals.
  private chainGeneration = 0;
  // True when the restored job's stay had already passed and the date filters
  // were rolled forward, so the banner can say the shown prices are historic.
  private restoredStayIsStale = false;
  // The stay the job behind the CURRENT markers was scraped for, always set
  // from that job itself (see loadMarkers). Job-scoped marker, summary and
  // match reads must use it, because the date inputs routinely hold something
  // else: a restored past window rolled forward, or the dates the user typed
  // for the next search. It used to be written once, on restore, and never
  // cleared — so a new search's rows were read with the RESTORED job's dates
  // and a scrape that wrote 8 rows rendered as an empty map (live, job
  // 6510897e). Null whenever no job-scoped result is on screen. A signal for
  // the same reason as its sibling activeScrapeJobId above.
  private readonly activeJobScope = signal<JobScope | null>(null);
  private saveMessageTimeout: ReturnType<typeof window.setTimeout> | null = null;
  // Set by ngOnDestroy so long-running poll loops stop with the component.
  private destroyed = false;

  constructor(
    private readonly api: ApiClientService,
    private readonly auth: AuthService,
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
    private readonly notifications: NotificationsService,
    readonly setupProgress: SetupProgressService,
  ) {}

  async ngOnInit(): Promise<void> {
    // Startup is a chain like any other: everything below runs after two
    // awaits, and the Find button is already live once loadCurrentUser
    // resolved (property, rooms and dates are all in place). A click landing
    // in that window owns the screen from then on — otherwise the reset below
    // undoes the search that click started, re-enables the button (inviting a
    // second, Apify-costing job for a search already running) and lets the
    // restore repaint the previous job over the live one. Guarded by
    // generation rather than by actionStatus, so it is the same "who owns the
    // screen" question the rest of this component answers; actionStatus is a
    // rendering of that answer, not the answer itself.
    const generation = this.chainGeneration;
    await this.loadCurrentUser();
    // The nearby defaults land BEFORE the restore starts, so a restored job's
    // own areas always overwrite them rather than racing them.
    await this.loadNearbyDefaults(generation);
    if (!this.isCurrentChain(generation)) {
      return;
    }
    this.resetCompetitorSearchState();
    // Reuse the last completed scrape instead of forcing a fresh (Apify-costing)
    // live search on every navigation back to the map.
    await this.restoreLatestCompetitorSearch(generation);
    // Nothing restored (no job, or its read failed): the owner's hotel still
    // goes on the map (spec §4.3). A restored job read it in loadMarkers.
    if (this.isCurrentChain(generation) && !this.activeScrapeJobId()) {
      void this.loadOwnProperty("", generation);
    }
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    if (this.saveMessageTimeout) {
      window.clearTimeout(this.saveMessageTimeout);
    }
    this.stopScrapeProgress();
  }

  /**
   * The one way the result set changes.
   *
   * The checklist's tracking step now asks for min(3, available), so the
   * available count has to move with this list or the step would demand a
   * number this market cannot supply (Faliraki/double really returns 2-3
   * rooms). Only rows carrying a property_id count: saveTrackedCompetitors
   * drops the others, so they are not available to track.
   */
  private setCompetitors(rows: CompetitorMapMarker[]): void {
    this.competitors.set(rows);
    this.setupProgress.setAvailableCompetitors(rows.filter((row) => row.property_id).length);
  }

  /** A card under the pointer or keyboard focus: its hotel's marker is raised and outlined. */
  highlightMarker(key: string): void {
    this.mapView?.highlightMarker(key);
  }

  /** Only the card that raised the marker lowers it (a late mouseleave must not undo a newer focus). */
  unhighlightMarker(key: string): void {
    this.mapView?.unhighlightMarker(key);
  }

  /**
   * A card click eases the map to its hotel's marker, at the current zoom (the
   * user chose it; nothing zooms out from under them). The card is a label, so
   * the browser replays the click on its checkbox and that copy bubbles back
   * here: it is skipped, as is a click on the checkbox itself — ticking stays
   * what it always was.
   */
  easeToMarker(event: Event, key: string): void {
    this.mapView?.easeToMarker(event, key);
  }

  /**
   * A marker click: its hotel's cards stay marked while the popup is open, and
   * the first one scrolls to the middle of the list, so the list says which
   * result the map just opened. Runs in a Mapbox DOM listener, hence the
   * signal; a newer click moves the mark.
   */
  flashCard(key: string): void {
    this.highlightedCardKey.set(key);
    const escaped = CSS.escape(key);
    const host = this.hostElement.nativeElement;
    const card = host.querySelector<HTMLElement>(`.competitor-card[data-hotel-key="${escaped}"]`);
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /** Its popup closed: the hotel's cards let go, unless a newer click already moved the mark. */
  releaseCard(key: string): void {
    if (this.highlightedCardKey() === key) {
      this.highlightedCardKey.set(null);
    }
  }

  setFilter(key: keyof MapFilters, value: string): void {
    this.filters.update((filters) => ({ ...filters, [key]: value }));
  }

  /**
   * Add the typed area as a chip. Refused (the text stays in the input) when
   * it repeats a chip in any letter case or names the main destination itself —
   * the server would drop both silently, so the chip would be a lie.
   */
  addNearbyDestination(): void {
    const area = this.nearbyDraft().trim();
    if (!area || !this.canAddNearbyDestination()) {
      return;
    }
    const key = this.destinationKey(area);
    if (this.isMainDestination(area) || this.nearbyDestinations().some((item) => this.destinationKey(item) === key)) {
      return;
    }
    this.nearbyDestinations.update((areas) => [...areas, area]);
    this.nearbyDraft.set("");
    this.nearbyEdited.set(true);
  }

  removeNearbyDestination(area: string): void {
    this.nearbyDestinations.update((areas) => areas.filter((item) => item !== area));
    this.nearbyEdited.set(true);
  }

  private destinationKey(value: string): string {
    return value.trim().toLocaleLowerCase("el-GR");
  }

  private isMainDestination(area: string): boolean {
    const key = this.destinationKey(area);
    return [this.filters().destination, this.rawDestination]
      .some((destination) => Boolean(destination) && this.destinationKey(destination) === key);
  }

  /** Trimmed, blank-free, case-insensitively unique, never the main destination, at most 8. */
  private normalizeNearbyDestinations(areas: unknown[]): string[] {
    const seen = new Set<string>();
    const normalized: string[] = [];
    for (const item of areas) {
      const area = typeof item === "string" ? item.trim() : "";
      const key = this.destinationKey(area);
      if (!area || seen.has(key) || this.isMainDestination(area)) {
        continue;
      }
      seen.add(key);
      normalized.push(area);
    }
    return normalized.slice(0, MAX_NEARBY_DESTINATIONS);
  }

  /**
   * Prefill the chips with the server's default neighbouring areas.
   *
   * Best effort: an older API (404) or a failed read leaves the list empty and
   * the search runs on the main destination alone. Dropped when a Find click
   * took the screen or the user already edited the list.
   */
  private async loadNearbyDefaults(generation: number): Promise<void> {
    const destination = this.filters().destination.trim();
    if (!destination) {
      return;
    }
    try {
      const response = await this.api.get<NearbyDestinationsResponse>(
        "/api/v1/onboarding/nearby-destinations",
        new URLSearchParams({ destination }),
      );
      if (!this.isCurrentChain(generation) || this.nearbyEdited()) {
        return;
      }
      this.nearbyDestinations.set(this.normalizeNearbyDestinations(Array.isArray(response?.nearby) ? response.nearby : []));
    } catch {
      // Nothing to prefill; the chips stay as they are.
    }
  }

  /**
   * Why the map is empty — one honest answer per reason it can be.
   *
   * Results are plotted automatically now, so "tick a card to see them" is no
   * longer true of any of these states. The remaining reasons are distinct and
   * must not be collapsed into each other: the user's own filter hiding
   * everything is not the same as a search finding nothing, and neither is the
   * same as results that carry no coordinates.
   */
  mapEmptyMessage(): string {
    if (this.status() === "loading") {
      return "Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών...";
    }
    if (this.competitors().length) {
      // Checked BEFORE the filter: a result set nothing can draw stays that
      // whatever the filter is set to, and blaming the filter there would send
      // the user to a switch that would change nothing.
      if (!this.plottableCompetitors().length) {
        return "Τα αποτελέσματα δεν έχουν συντεταγμένες, οπότε δεν μπορούν να μπουν στον χάρτη.";
      }
      // Drawable rooms exist and none of them is on screen, so the user's own
      // filter is the only thing left that can be hiding them.
      return FILTER_HIDES_ALL_MESSAGE;
    }
    // Nothing came back at all, and «Μόνο συγκρίσιμα» is what dropped it: the
    // rows exist in the job behind this screen. Said on the map too, not only
    // in the sidebar, because this is a state the user caused and can undo —
    // the generic "see the right-hand column" below is for the reasons they
    // cannot.
    if (this.comparableFilterHidesEverything()) {
      return COMPARABLE_HIDES_ALL_MESSAGE;
    }
    if (this.status() === "ready") {
      // Deliberately not "the search found nothing": a search CAN finish with
      // offers that the scraper's own stages kept off the map (see
      // emptyResultTitle). The sidebar carries that distinction; here we only
      // state what is true of the map itself.
      return "Κανένα δωμάτιο δεν μπορεί να μπει στον χάρτη. Δείτε τη δεξιά στήλη για λεπτομέρειες.";
    }
    return "Τρέξτε μια αναζήτηση ανταγωνιστών για να δείτε όλα τα αποτελέσματα στον χάρτη.";
  }

  /** Narrow the map to the ticked rooms, or open it back up to all results. */
  toggleShowOnlySelected(): void {
    this.showOnlySelected.update((only) => !only);
    this.refreshSearchResultMessage();
    // Markers are created imperatively, so the computed change alone repaints
    // nothing on the map itself.
    this.mapView?.renderMarkers();
  }

  /** The banner for a search that returned rooms, true for the filters as they are. */
  private searchResultMessage(): string {
    // The read filter is asked first because it is the banner that can be
    // true with nothing on the map: the other two describe what IS plotted,
    // and «Όλα τα αποτελέσματα εμφανίζονται στον χάρτη» over an empty map is a
    // lie the user can see through.
    if (this.comparableFilterHidesEverything()) {
      return SEARCH_COMPARABLE_FILTERED_MESSAGE;
    }
    return this.showOnlySelected() ? SEARCH_FILTERED_MESSAGE : SEARCH_PLOTTED_MESSAGE;
  }

  /**
   * Keep a search-result banner true when the filters change under it.
   *
   * Rewrites the banner ONLY when it is one of this component's own four
   * search-result texts. Every other banner — the restored-search one, the
   * "run a search" prompt — describes something the filters have no bearing
   * on, and overwriting those would lose what they say.
   *
   * Called from both filters that can empty a screen full of results: the map
   * filter (synchronously, from its own click) and «Μόνο συγκρίσιμα» (after
   * its re-read, which is the only moment its effect is known).
   */
  private refreshSearchResultMessage(): void {
    const current = this.message();
    if (
      current === SEARCH_PLOTTED_MESSAGE
      || current === SEARCH_FILTERED_MESSAGE
      || current === SEARCH_COMPARABLE_FILTERED_MESSAGE
    ) {
      this.message.set(this.searchResultMessage());
    }
  }

  /**
   * The three counted phrases the page renders, each built whole in TS.
   *
   * Greek inflects the noun with the number, so "N " + noun cannot be
   * concatenated in the template the way English allows — «1 ανταγωνιστές»
   * is wrong in a way «1 competitors» is not. Same shape as
   * SetupProgressService.trackingLabel(): one method, one sentence out.
   * Zero takes the plural, which is what Greek does.
   */
  competitorCountLabel(): string {
    // Hotels, not rows (spec §4.7): the same hotel under two packages is one
    // competitor, as it is one «Καταλύματα» in the summary box.
    const count = new Set(this.competitors().map((competitor) => competitor.property_id || competitor.hotel_name)).size;
    return count === 1 ? "1 ανταγωνιστής" : `${count} ανταγωνιστές`;
  }

  /**
   * «Καταλύματα N · Καταγραφές M · Ίδια κατηγορία K · Παρόμοια L» (spec §4.7).
   * Labels before numbers, so no noun has to agree with a count. The category
   * split is left out when the API predates it rather than shown as zeros.
   */
  marketCountsLine(summary: MarketSummary): string {
    const parts = [`Καταλύματα ${summary.total_hotels}`, `Καταγραφές ${summary.total_records}`];
    if (typeof summary.same_category_hotels === "number") {
      parts.push(`Ίδια κατηγορία ${summary.same_category_hotels}`);
    }
    if (typeof summary.similar_hotels === "number") {
      parts.push(`Παρόμοια ${summary.similar_hotels}`);
    }
    return parts.join(" · ");
  }

  /**
   * «Μόνο συγκρίσιμα» on or off (owner decision 2026-09-30).
   *
   * A filter the server applies while READING (`comparable_only`), so the job
   * on screen is read again.
   */
  async toggleComparableOnly(): Promise<void> {
    this.comparableOnly.update((only) => !only);
    await this.reloadActiveJob("Δεν ήταν δυνατή η εφαρμογή του φίλτρου συγκρίσιμων.");
  }

  /**
   * The empty state's one button: switch «Μόνο συγκρίσιμα» off, which
   * re-reads the job on screen.
   */
  onEmptyResultAction(): void {
    if (this.emptyResultReason()?.kind === "comparable" && this.comparableOnly()) {
      void this.toggleComparableOnly();
    }
  }

  /**
   * Re-read the job on screen with the read scope as it now stands: the room
   * type in «Σύγκριση για» and «Μόνο συγκρίσιμα».
   *
   * Both are applied by the BACKEND while reading the job's rows, so until
   * those rows are fetched again nothing on screen changes. Routed through
   * loadMarkers rather than a bare marker read so the three stay one result
   * set — it re-runs the summary and the match-score read with the same
   * params.
   *
   * The generation is read HERE, the way refreshRoomMatches defaults it: the
   * click starts no chain of its own, it joins whichever chain owns the screen,
   * so a Find pressed while this read is in flight still drops the whole tail.
   * Bumping the generation instead would cancel a restore that has not yet
   * published its job id — and this would then find nothing to re-read.
   */
  private async reloadActiveJob(failureMessage: string): Promise<void> {
    const scrapeJobId = this.activeScrapeJobId();
    const jobScope = this.activeJobScope();
    // Nothing job-scoped on screen. The next read to run — a Find, the restore
    // chain, the unscoped match read — builds its own params and picks the new
    // selection up by itself.
    if (!scrapeJobId || !jobScope) {
      return;
    }
    const generation = this.chainGeneration;
    try {
      // The job's own machine-readable truth is handed back in: it belongs to
      // the job, not to the read, and loadMarkers would otherwise blank it and
      // take the empty-state explanation down with it.
      await this.loadMarkers(scrapeJobId, generation, jobScope, this.activeResultSummary());
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // The banner is rewritten HERE and nowhere earlier: whether the new
      // selection empties the screen is only known once its rows are back.
      // A signal write after an await, like every other write in this chain.
      this.refreshSearchResultMessage();
    } catch (error) {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // loadMarkers flipped the status to "loading" before its read. Left
      // there, the sidebar waits forever for a read that already failed; sent
      // to "ready" it would show an empty result list, and the empty state
      // would blame the filter for a failure that is not the filter's.
      this.setError(error instanceof Error ? error.message : failureMessage);
    }
  }

  async onRoomTypeChange(roomTypeId: string): Promise<void> {
    // The user just made the choice their own, so the auto-pick notice has
    // nothing left to report (it is also gone on the next navigation, since
    // the signal starts false with the component).
    this.autoPickedRoom.set(false);
    this.selectedRoomId.set(roomTypeId);
    const room = this.selectedOwnedRoom();
    this.selectedRoomType.set(room?.room_type_category || "");
    this.workflow.set("roomTypeId", roomTypeId);
    this.workflow.set("roomTypeCategory", this.selectedRoomType());
    this.persistSelectedRoomType();
    // A pure view filter (owner decision 2026-11-13): every read carries
    // `owned_room_type_id`, and the comparable set is the agent's verdict for
    // ONE room type — another room type is another set over the same job, so
    // the markers, the summary and the scores are read again. The job's one
    // matching pass already judged every room type: no search, no run.
    await this.reloadActiveJob("Δεν ήταν δυνατή η φόρτωση των συγκρίσιμων για αυτό το δωμάτιο.");
  }

  /**
   * Sync the room selection to the backend.
   *
   * "Match my room" requires the persisted `selected_room_type_category` (GET
   * /competitors match_room=true 404s without it). The old comment here also
   * claimed nothing else writes it "since the onboarding page was unrouted" —
   * stale since Round 3: the wizard's room step (setup-room-step.component)
   * writes it on every ordinary onboarding, through the same service call
   * below.
   *
   * Routed through OnboardingService.selectRoomType rather than a raw PUT
   * (Εκκρεμότητα 8, owner decision 2026-08-23): the service invalidates the
   * shared /me cache that every page and the setup guard read. A raw PUT left
   * the database holding the new room while the next /me reader was served a
   * cache still naming the old one. Best effort — matching simply stays
   * unavailable if it fails.
   */
  private persistSelectedRoomType(): void {
    if (!this.ownedPropertyId || !this.selectedRoomType()) {
      return;
    }
    void this.onboarding
      .selectRoomType(this.ownedPropertyId, this.selectedRoomType())
      .catch(() => undefined);
  }

  toggleCompetitor(key: string): void {
    this.selectedKeys.update((selected) => {
      const next = new Set(selected);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
    // User-initiated: arm the guard so the load/restore chain's own writes
    // (still possibly in flight) stop clobbering this selection.
    this.userTouchedSelection.set(true);
    this.persistCompetitorSelection();
    this.mapView?.renderMarkers();
  }

  async findCompetitors(): Promise<void> {
    if (!this.ownedPropertyId) {
      this.setError("Ολοκληρώστε τη ρύθμιση του καταλύματος πριν αναζητήσετε ανταγωνιστές.");
      return;
    }
    const baselineRoom = this.selectedOwnedRoom();
    if (!baselineRoom || !this.selectedRoomType()) {
      this.setError("Επιλέξτε το δωμάτιό σας πριν αναζητήσετε ανταγωνιστές.");
      return;
    }
    if (!this.filters().destination.trim()) {
      this.setError("Λείπει η τοποθεσία του καταλύματος. Συνδεθείτε ξανά δηλώνοντας όνομα καταλύματος και τοποθεσία.");
      return;
    }
    if (isPastLocalDate(this.filters().check_in)) {
      this.setError("Η άφιξη δεν μπορεί να είναι στο παρελθόν.");
      return;
    }
    if (this.filters().check_out <= this.filters().check_in) {
      this.setError("Η αναχώρηση πρέπει να είναι μετά την άφιξη.");
      return;
    }

    // Supersede every chain still in flight BEFORE clearing state, so none of
    // their queued writes can land on this search. Nothing else bumps the
    // generation, and the button is [disabled] while actionStatus is
    // "loading", so this run cannot be superseded in turn — its own late
    // writes (message/actionStatus below) need no guard.
    this.chainGeneration += 1;
    const generation = this.chainGeneration;
    this.actionStatus.set("loading");
    this.status.set("loading");
    this.message.set("Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών. Τα αποτελέσματα θα εμφανιστούν μόλις ολοκληρωθεί.");
    this.saveMessage.set("");
    this.saveHasError.set(false);
    this.error.set("");
    this.setCompetitors([]);
    this.activeResultSummary.set(null);
    this.selectedKeys.set(new Set());
    // A fresh search legitimately starts selection over -- disarm the guard
    // so this run's own load chain (loadMarkers below) can restore/apply
    // normally instead of finding itself blocked by a stale prior touch.
    this.userTouchedSelection.set(false);
    this.activeScrapeJobId.set("");
    // No job-scoped result on screen any more, so no job stay either: reads
    // between here and the new job's own load must fall back to the filters.
    this.activeJobScope.set(null);
    this.mapView?.resetFit();
    this.marketSummary.set(null);
    this.marketSummaryStatus.set("idle");
    this.clearMatchScores();
    this.autoMatchNotice.set("");
    this.reassessNotice.set("");
    this.mapView?.clearMarkers();
    // No deep_crawl_max_items any more: the server derives it from its own
    // hotel cap, which also counts the nearby areas (spec §3.1).
    const resultLimit = this.readBoundedNumber(this.filters().limit, 40, 1, 80);
    // «Εκκίνηση…» from the click itself: the POST alone can take a moment.
    this.scrapeProgress.set(null);
    this.scrapeInFlight.set(true);
    // The block renders under the Find button, which sits at the foot of a
    // long, scrolling form: bring it into view once it is on the page. Centred,
    // because it grows by a line once the stage-2 counts arrive.
    window.setTimeout(() => this.hostElement.nativeElement.querySelector<HTMLElement>(".scrape-progress")
      ?.scrollIntoView({ block: "center" }), 0);

    try {
      const job = await this.api.post<ScrapeJobResponse>("/api/v1/scrape-jobs/", {
        owned_property_id: this.ownedPropertyId,
        job_type: "competitor_search",
        room_type_category: this.selectedRoomType(),
        destination: this.filters().destination,
        raw_destination: this.rawDestination || this.filters().destination,
        check_in: this.filters().check_in,
        check_out: this.filters().check_out,
        adults: Number(this.filters().adults),
        children: Number(this.filters().children),
        rooms: Number(this.filters().rooms),
        // Spec §3.1: the radius centre is resolved by the server, never sent from here.
        nearby_destinations: this.nearbyDestinations(),
        radius_km: this.readBoundedNumber(this.filters().radius_km, DEFAULT_RADIUS_KM, MIN_RADIUS_KM, MAX_RADIUS_KM),
        // No room-name filter (owner decision 2026-11-13): the search stores
        // every room of every competitor, and the matching that follows
        // judges each one against ALL the owner's room types.
        filters_payload: {
          limit: resultLimit,
        },
      });

      this.trackScrapeProgress(job);
      const completedJob = await this.pollScrapeJob(job.id);
      if (this.destroyed) {
        return;
      }
      this.workflow.set("lastCompetitorJobId", completedJob.id);
      // The stay comes from the job the backend actually ran, not from the
      // date inputs: those stay editable while a search runs, and they still
      // held a restored job's window in the live failure this fixes.
      await this.loadMarkers(completedJob.id, generation, {
        checkIn: completedJob.check_in,
        checkOut: completedJob.check_out,
        roomTypeCategory: completedJob.room_type_category || this.selectedRoomType(),
        radiusKm: completedJob.radius_km ?? null,
      }, completedJob.result_summary);
      // Filter-aware, for both filters that survive a search. This run cleared
      // the ticked cards, so with the map filter on the map is empty — saying
      // the results are all on it would be a lie the user can see through.
      // «Μόνο συγκρίσιμα» survives too and empties the screen from the backend
      // side, which is why it is asked about before the sentence below: that
      // one blames the own-property exclusion, and a filtered-out result set
      // was not excluded, it was filtered.
      this.message.set(this.competitors().length || this.comparableFilterHidesEverything()
        ? this.searchResultMessage()
        : "Δεν βρέθηκαν δωμάτια ανταγωνιστών αφού εξαιρέθηκε το δικό σας κατάλυμα.");
      this.actionStatus.set("ready");
    } catch (error) {
      // The POST itself may be what failed, before any poll could stop it.
      this.stopScrapeProgress();
      if (this.destroyed) {
        return;
      }
      this.setError(error instanceof Error ? error.message : "Δεν ήταν δυνατή η εκτέλεση της αναζήτησης ανταγωνιστών.");
      this.actionStatus.set("error");
    }
  }

  async saveTrackedCompetitors(): Promise<void> {
    this.saveMessage.set("");
    this.saveHasError.set(false);
    if (!this.ownedPropertyId || !this.selectedRoomType()) {
      this.showSaveMessage("Επιλέξτε κατάλυμα και τύπο δωματίου πριν αποθηκεύσετε ανταγωνιστές προς παρακολούθηση.", true);
      return;
    }
    if (!this.selectedKeys().size) {
      this.showSaveMessage("Επιλέξτε τουλάχιστον έναν ανταγωνιστή πριν την αποθήκευση.", true);
      return;
    }
    const competitors = this.competitors()
      .filter((competitor) => this.selectedKeys().has(this.markerKey(competitor)) && competitor.property_id)
      .map((competitor) => ({
        property_id: competitor.property_id,
        room_package_id: competitor.room_package_id || null,
      }));
    if (!competitors.length) {
      this.showSaveMessage("Στις επιλεγμένες καταχωρίσεις λείπει το αναγνωριστικό καταλύματος, οπότε δεν μπορούν ακόμη να αποθηκευτούν.", true);
      return;
    }

    try {
      const response = await this.api.post<{ saved_count: number }>("/api/v1/tracked/competitors", {
        owned_property_id: this.ownedPropertyId,
        room_type_category: this.selectedRoomType(),
        competitors,
      });
      this.showSaveMessage(this.trackedSavedMessage(response.saved_count), false);
      // Re-feed the checklist from the same source the load path uses: the
      // tracking step used to stay untickable until the next map load, because
      // nothing after a save called setTracked. Re-READ rather than count what
      // was just sent — the save keeps existing tracked rooms, so saved_count
      // is not the list length.
      await this.fetchTrackedCompetitors();
    } catch (error) {
      this.showSaveMessage(error instanceof Error ? error.message : "Δεν ήταν δυνατή η αποθήκευση των ανταγωνιστών προς παρακολούθηση.", true);
    }
  }

  /**
   * The save confirmation, built whole for the same reason as the count
   * labels above: Greek inflects the verb AND the noun with the number, so
   * the English `room${s}` trick has no equivalent to splice in.
   */
  private trackedSavedMessage(savedCount: number): string {
    const kept = "Τα ήδη παρακολουθούμενα δωμάτια διατηρήθηκαν.";
    return savedCount === 1
      ? `Προστέθηκε 1 δωμάτιο ανταγωνιστή στην παρακολούθηση. ${kept}`
      : `Προστέθηκαν ${savedCount} δωμάτια ανταγωνιστών στην παρακολούθηση. ${kept}`;
  }


  toggleFiltersSidebar(): void {
    this.filtersCollapsed.update((collapsed) => !collapsed);
    // The map's column changes width: let Mapbox re-measure its canvas.
    window.setTimeout(() => this.mapView?.resize(), 0);
  }

  matchScoreFor(hotelName: string): number | null {
    const score = this.hotelMatchScores().get(hotelName);
    return typeof score === "number" ? score : null;
  }

  /**
   * The match scores of the job on screen for the room type in «Σύγκριση
   * για»: the best score per hotel (competitor cards, map popups) and the
   * source badge. No score floor, best match first — the slider and the sort
   * are gone (owner decision 2026-11-13). Best effort: a failed read leaves
   * the cards without scores and the badge on «Στατιστική εκτίμηση».
   *
   * `generation` defaults to the current chain; loadMarkers passes its own so
   * a superseded load cannot refill the scores findCompetitors just cleared.
   */
  async refreshRoomMatches(generation: number = this.chainGeneration): Promise<void> {
    if (!this.ownedPropertyId || !this.selectedRoomType() || !this.filters().destination.trim()) {
      return;
    }
    try {
      const params = this.buildMarkerParams(this.activeScrapeJobId() || undefined);
      // The match read keeps its own row cap (the map and summary read the whole job).
      params.set("limit", "500");
      params.set("match_room", "true");
      params.set("min_match_score", "0");
      params.set("sort", "match");
      const answer = await this.api.get<Competitor[]>("/api/v1/competitors/", params);
      if (!this.isCurrentChain(generation)) {
        return;
      }
      const hotels = Array.isArray(answer) ? answer : [];
      this.hotelMatchScores.set(new Map(
        hotels
          .filter((hotel) => typeof hotel.best_match_score === "number")
          .map((hotel) => [hotel.hotel_name, hotel.best_match_score as number]),
      ));
      this.matchSource.set(this.deriveMatchSource(hotels));
    } catch {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      this.hotelMatchScores.set(new Map());
      this.matchSource.set("statistical");
    }
    // Refresh popups so they show the scores just loaded (or none).
    this.mapView?.renderMarkers();
  }

  private clearMatchScores(): void {
    this.hotelMatchScores.set(new Map());
    this.matchSource.set("statistical");
  }

  /**
   * «Ξανά ταίριασμα» (spec Α.1): one matching pass over the job on screen
   * for EVERY room type, replacing their verdicts, then a re-read of the job
   * so the comparison set, the scores and the badge reflect what it wrote.
   * Every failure — status "error" or "skipped", the 429 quota, a transport
   * error — only raises the fallback notice (spec Α.5): the screen is
   * untouched and keeps the scores it already shows. A run still in
   * progress elsewhere (skip_reason "in_progress") is said as such, quietly.
   */
  async reassessMatches(): Promise<void> {
    const scrapeJobId = this.activeJobId();
    if (!scrapeJobId || this.reassessInFlight()) {
      return;
    }
    this.reassessInFlight.set(true);
    this.reassessNotice.set("");
    try {
      const body: RoomMatchRunRequest = { scrape_job_id: scrapeJobId, only_missing: false };
      const run = await this.api.post<RoomMatchRunResponse>("/api/v1/agents/room-matches", body);
      if (run?.status === "completed") {
        if (this.activeScrapeJobId() === scrapeJobId) {
          await this.reloadActiveJob("Δεν ήταν δυνατή η φόρτωση των νέων ταιριασμάτων.");
        }
      } else {
        this.reassessNotice.set(agentRunNotice(run));
      }
    } catch {
      this.reassessNotice.set(AGENT_UNAVAILABLE_NOTICE);
    } finally {
      this.reassessInFlight.set(false);
    }
  }

  /** Red for a failed agent run; the quiet style for one still in progress. */
  isAgentFailureNotice(notice: string): boolean {
    return notice !== AGENT_IN_PROGRESS_NOTICE;
  }

  /**
   * The match source badge (spec Α.4). «Εκτίμηση AI» must never be a
   * false claim, so only a non-empty read whose EVERY row says agent earns
   * it. A read that mixes agent rows with statistical ones (or with rows an
   * older API sent without the field) says so — «Εκτίμηση AI (μερική)» —
   * and a read with no agent row at all, the empty list included, is what
   * the scores then are: statistical.
   */
  private deriveMatchSource(hotels: Competitor[]): MatchSourceState {
    const agentRows = hotels.filter((hotel) => hotel.match_source === "agent").length;
    if (!agentRows) {
      return "statistical";
    }
    return agentRows === hotels.length ? "agent" : "partial";
  }

  /**
   * The automatic matching run (owner decision 2026-11-13): right after a
   * search — and once for any job this visit brings on screen — ONE pass
   * judges every competitor room against ALL the owner's room types. It is
   * posted with `only_missing`, so the server scores only the room types
   * without verdicts (none: an instant "already_matched") and, when the
   * post-scrape run is already scoring the job, waits for that run instead
   * of paying for a second one. The screen shows the statistical set in the
   * meantime and is read again once the verdicts are in.
   *
   * At most ONCE per job per visit: the job is marked before the await, and
   * a failure (error, skipped, 429, transport) is only the fallback notice,
   * never an automatic retry — except "in_progress", which means the other
   * run is still going after the server's wait, and is asked again a few
   * times before the notice says so.
   */
  private async runJobMatching(scrapeJobId: string, generation: number): Promise<void> {
    if (!scrapeJobId || !this.ownedPropertyId || this.autoMatchDecided().has(scrapeJobId)) {
      return;
    }
    this.autoMatchDecided.update((decided) => new Set(decided).add(scrapeJobId));
    const screenMovedOn = () =>
      !this.isCurrentChain(generation) || this.activeScrapeJobId() !== scrapeJobId;

    this.autoMatchPending.set(true);
    this.autoMatchNotice.set("");
    let run: RoomMatchRunResponse | null = null;
    try {
      const body: RoomMatchRunRequest = { scrape_job_id: scrapeJobId, only_missing: true };
      for (let attempt = 0; attempt <= AGENT_STILL_RUNNING_RETRIES; attempt += 1) {
        run = await this.api.post<RoomMatchRunResponse>("/api/v1/agents/room-matches", body);
        if (run?.skip_reason !== "in_progress" || screenMovedOn()) {
          break;
        }
      }
    } catch {
      run = null;
    } finally {
      this.autoMatchPending.set(false);
    }
    if (screenMovedOn()) {
      return;
    }
    const verdictsReady = run?.status === "completed"
      || (run?.status === "skipped" && run.skip_reason === "already_matched");
    if (!verdictsReady) {
      this.autoMatchNotice.set(agentRunNotice(run));
      return;
    }
    // Every room type was already judged: re-read only when the screen does
    // not show those verdicts yet (its read landed before they were written).
    if (run?.status === "skipped") {
      await this.matchScoresLoad;
      if (screenMovedOn() || this.matchSource() !== "statistical") {
        return;
      }
    }
    // The verdicts changed, so every read of this job is stale: the
    // comparable markers, the summary and the scores.
    await this.reloadActiveJob("Δεν ήταν δυνατή η φόρτωση των συγκρίσιμων για αυτό το δωμάτιο.");
  }

  private async loadCurrentUser(): Promise<void> {
    try {
      const currentUser = await this.onboarding.currentUser();
      this.setupProgress.setUser(currentUser);
      this.workflow.bindToSubject(currentUser.auth_subject);
      this.ownedPropertyId = this.workflow.get("ownedPropertyId") || currentUser.owned_property_id || "";
      this.propertyName.set(this.workflow.get("propertyName") || currentUser.property_name || "");
      this.rawDestination = this.workflow.get("rawDestination") || currentUser.raw_destination || "";
      this.selectedRoomType.set(this.workflow.get("roomTypeCategory") || currentUser.selected_room_type_category || "");
      this.selectedRoomId.set(this.workflow.get("roomTypeId"));
      this.setFilter("destination", this.workflow.get("destination") || currentUser.destination || "");

      if (this.ownedPropertyId) {
        this.roomTypes.set(await this.api.get<OwnedPropertyRoomType[]>(
          `/api/v1/onboarding/owned-property/${this.ownedPropertyId}/room-types`,
        ));
        this.ensureSelectedRoom();
      }
    } catch (error) {
      this.setError(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση των στοιχείων του λογαριασμού.");
    }
  }

  private ensureSelectedRoom(): void {
    if (!this.roomTypes().length) {
      this.selectedRoomId.set("");
      this.selectedRoomType.set("");
      this.autoPickedRoom.set(false);
      return;
    }
    const storedRoom = this.roomTypes().find((room) => room.id === this.selectedRoomId());
    const categoryRoom = this.roomTypes().find((room) => room.room_type_category === this.selectedRoomType());
    const selected = storedRoom || categoryRoom || this.roomTypes()[0];
    // Neither the stored room nor the stored category matched anything in the
    // catalog, so nobody chose what is about to be selected and persisted —
    // the page picked the first room by itself (the room-step "Αργότερα" skip
    // is the usual way in). Announced rather than silent: the whole market
    // comparison is scoped to this room.
    this.autoPickedRoom.set(!storedRoom && !categoryRoom);
    this.selectedRoomId.set(selected.id);
    this.selectedRoomType.set(selected.room_type_category);
    this.workflow.set("roomTypeId", selected.id);
    this.workflow.set("roomTypeCategory", selected.room_type_category);
    this.persistSelectedRoomType();
  }

  private async pollScrapeJob(jobId: string): Promise<ScrapeJobResponse> {
    let consecutiveFailures = 0;
    try {
      for (let attempt = 0; attempt < SCRAPE_POLL_ATTEMPTS; attempt += 1) {
        // Stop polling when the user navigates away — otherwise this loop keeps
        // firing requests (and later mutating destroyed-component state) for up
        // to 30 minutes.
        if (this.destroyed) {
          throw new Error("Η αναζήτηση ανταγωνιστών ακυρώθηκε λόγω πλοήγησης.");
        }
        let job: ScrapeJobResponse;
        try {
          job = await this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${jobId}`);
        } catch (error) {
          // One failed read mid-scrape used to abort the whole wait, show the
          // error banner and re-enable «Εύρεση» while the job kept running.
          // Keep the last shown progress, wait the normal interval and ask
          // again; give up only once the failure run says the server is gone.
          consecutiveFailures += 1;
          if (consecutiveFailures >= SCRAPE_POLL_FAILURE_LIMIT) {
            throw error;
          }
          await new Promise((resolve) => window.setTimeout(resolve, SCRAPE_POLL_INTERVAL_MS));
          continue;
        }
        consecutiveFailures = 0;
        if (job.status === "completed") {
          return job;
        }
        if (job.status === "failed") {
          throw new Error(job.error_message || "Η αναζήτηση ανταγωνιστών απέτυχε.");
        }
        this.trackScrapeProgress(job);
        await new Promise((resolve) => window.setTimeout(resolve, SCRAPE_POLL_INTERVAL_MS));
      }
      throw new Error("Η αναζήτηση ανταγωνιστών εκτελείται ακόμη. Αφήστε τον διακομιστή ανοιχτό και περιμένετε να ολοκληρωθεί.");
    } finally {
      // Completed, failed, timed out or abandoned: the progress and its clock
      // describe a job that is no longer running either way.
      this.stopScrapeProgress();
    }
  }

  /**
   * Take the progress and the clock's start from one job read (spec §4.2).
   *
   * The progress is replaced, not merged: a job retried after a failure starts
   * over, and «Εκκίνηση…» is then the truth. The clock counts from
   * `started_at`, or from `requested_at` while the job is still queued.
   */
  private trackScrapeProgress(job: ScrapeJobResponse): void {
    // A poll answer that lands after ngOnDestroy must not restart the clock
    // interval below: stopScrapeProgress has already run, and nothing would
    // ever clear a timer started now.
    if (this.destroyed) {
      return;
    }
    this.scrapeProgress.set(job.result_summary?.progress ?? null);
    const startedAt = Date.parse(job.started_at || job.requested_at || "");
    if (!Number.isFinite(startedAt)) {
      return;
    }
    this.scrapeClockStartedAt.set(startedAt);
    this.scrapeClockNow.set(Date.now());
    if (!this.scrapeClockTimer) {
      this.scrapeClockTimer = window.setInterval(() => this.scrapeClockNow.set(Date.now()), 1000);
    }
  }

  private stopScrapeProgress(): void {
    if (this.scrapeClockTimer) {
      window.clearInterval(this.scrapeClockTimer);
      this.scrapeClockTimer = null;
    }
    this.scrapeInFlight.set(false);
    this.scrapeProgress.set(null);
    this.scrapeClockStartedAt.set(null);
  }

  /**
   * The progress sentence, built whole because Greek inflects the noun with
   * the number (see competitorCountLabel): «σε 1 περιοχή», «βρέθηκε 1».
   */
  private describeScrapeProgress(progress: ScrapeJobProgress | null): string {
    const count = (value: number | null | undefined) => (typeof value === "number" && Number.isFinite(value) ? value : 0);
    switch (progress?.stage) {
      case "scout": {
        const areas = count(progress.destinations_total);
        const found = count(progress.hotels_found);
        return `Στάδιο 1/2 — Αναζήτηση καταλυμάτων σε ${areas} ${areas === 1 ? "περιοχή" : "περιοχές"}… `
          + (found === 1 ? "βρέθηκε 1" : `βρέθηκαν ${found}`);
      }
      case "deep_crawl": {
        const total = count(progress.total);
        return `Στάδιο 2/2 — Τιμές δωματίων: ${count(progress.done)}/${total} ${total === 1 ? "κατάλυμα" : "καταλύματα"}`;
      }
      case "persist":
        return "Αποθήκευση αποτελεσμάτων…";
      default:
        return "Εκκίνηση…";
    }
  }

  /**
   * Load the map/sidebar for one chain.
   *
   * `generation` is the caller's chain id: every write here belongs to that
   * chain and is skipped once a newer one (a Find click) has taken the screen.
   * A superseded chain is dropped at the entry check below, so this method
   * never starts a read — nor touches the entry state — for one. Callers check
   * too, which saves the call entirely; the entry check is what makes that a
   * property of this method rather than of its two current call sites.
   *
   * `jobStay` is the stay `scrapeJobId` was scraped for, read from the job
   * itself. Both are required: a job-scoped read carrying anything other than
   * its own job's dates matches no stored rows, and typing that as "required"
   * is what stops the next caller from reintroducing it.
   */
  private async loadMarkers(
    scrapeJobId: string,
    generation: number,
    jobScope: JobScope,
    resultSummary?: ScrapeResultSummary | null,
  ): Promise<void> {
    if (!this.isCurrentChain(generation)) {
      return;
    }
    // A re-read of the job already on screen (a room switch, «Μόνο
    // συγκρίσιμα») keeps its
    // markers, and an open popup with them: renderMarkers reconciles them with
    // the new rows below. Any other job starts from an empty map.
    const rereadsJobOnScreen = scrapeJobId === this.activeScrapeJobId();
    this.activeJobScope.set(jobScope);
    this.activeResultSummary.set(resultSummary || null);
    const params = this.buildMarkerParams(scrapeJobId);
    this.status.set("loading");
    this.setCompetitors([]);
    // Chain-initiated: skip if the user has already touched selection this
    // navigation, so a click does not get wiped by the chain's own reset.
    if (!this.userTouchedSelection()) {
      this.selectedKeys.set(new Set());
    }
    if (!rereadsJobOnScreen) {
      this.mapView?.clearMarkers();
    }
    const nextCompetitors = await this.api.get<CompetitorMapMarker[]>("/api/v1/maps/competitors", params);
    // Find was pressed while this read was in flight: everything below
    // describes a job the user has already moved on from, so drop the whole
    // tail rather than repaint the new search with it.
    if (!this.isCurrentChain(generation)) {
      return;
    }
    this.setCompetitors(Array.isArray(nextCompetitors) ? nextCompetitors : []);
    this.activeScrapeJobId.set(scrapeJobId);
    // The owner's hotel for THIS job (its radius and own Booking price belong
    // to the job). A re-read of the same job changes neither, so it reads nothing.
    if (!rereadsJobOnScreen) {
      void this.loadOwnProperty(scrapeJobId, generation);
    }
    // Chain-initiated: same guard as above.
    if (!this.userTouchedSelection()) {
      this.selectedKeys.set(new Set());
    }
    // Unconditional: the checklist's tracked-competitors step needs an
    // accurate count on every load, regardless of whether a stored selection
    // is about to be restored below or whether any competitors came back.
    const tracked = await this.fetchTrackedCompetitors();
    if (!this.isCurrentChain(generation)) {
      return;
    }
    const restoredSelection = this.restoreStoredCompetitorSelection(scrapeJobId);
    if (!restoredSelection) {
      this.applyTrackedCompetitors(tracked);
    }
    // Only a job NEW to the screen earns a fresh camera fit. A re-read of the
    // job already shown (a room switch, «Μόνο συγκρίσιμα») changes the
    // rows, not the ground they stand on — resetting here yanked the map away
    // from wherever the user had panned or zoomed it.
    if (!rereadsJobOnScreen) {
      this.mapView?.resetFit();
    }
    this.status.set("ready");
    this.mapView?.renderMarkers();
    // The strip and match scores describe the same result set as the markers.
    void this.loadMarketSummary(scrapeJobId, generation);
    this.matchScoresLoad = this.refreshRoomMatches(generation);
    // A job new to the screen (a fresh search, a restore) gets its one
    // automatic matching run for every room type; a re-read (a room switch,
    // «Μόνο συγκρίσιμα») is a view change and runs nothing.
    if (!rereadsJobOnScreen) {
      void this.runJobMatching(scrapeJobId, generation);
    }
  }

  /** True while `generation` is still the chain that owns the screen. */
  private isCurrentChain(generation: number): boolean {
    return generation === this.chainGeneration;
  }

  /** `generation` is the startup chain this restore belongs to (see ngOnInit). */
  private async restoreLatestCompetitorSearch(generation: number): Promise<void> {
    if (!this.ownedPropertyId || !this.selectedRoomType() || !this.filters().destination) {
      return;
    }

    try {
      const job = await this.findRestorableCompetitorJob();
      // Find pressed while the job lookup was in flight: this restore lost the
      // screen. Its filter/date rewrite below would otherwise drag the running
      // search back to the old job's stay, and its message would claim a
      // restored search over a live one.
      if (!this.isCurrentChain(generation)) {
        return;
      }
      if (!job) {
        // No previous search to show (spec §4.1): the «Φίλτρα» column is the
        // way forward.
        return;
      }

      this.applyRestoredJobFilters(job);
      this.workflow.set("lastCompetitorJobId", job.id);
      this.message.set(
        this.restoredStayIsStale
          ? `Εμφανίζεται η τελευταία ολοκληρωμένη αναζήτησή σας για ${job.check_in} έως ${job.check_out}. `
            + "Οι ημερομηνίες αυτές έχουν παρέλθει, οπότε τα φίλτρα μετακινήθηκαν στο επόμενο διαθέσιμο "
            + "διάστημα — πατήστε «Εύρεση ανταγωνιστών» για ζωντανές τιμές."
          : "Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών. Χρησιμοποιήστε την «Εύρεση ανταγωνιστών» μόνο όταν θέλετε φρέσκα ζωντανά δεδομένα.",
      );
      await this.loadMarkers(job.id, generation, {
        checkIn: job.check_in,
        checkOut: job.check_out,
        roomTypeCategory: job.room_type_category || this.selectedRoomType(),
        radiusKm: job.radius_km ?? null,
      }, job.result_summary);
    } catch {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // This restore still owns the screen and just failed. loadMarkers had
      // already flipped status to "loading" before its read, and leaving it
      // there left the sidebar promising a scrape that had failed and the map
      // saying "Searching..." forever. Idle is what is true: nothing is
      // running, nothing was restored, and the message below is the way out.
      this.status.set("idle");
      this.setCompetitors([]);
      this.activeResultSummary.set(null);
      // No job-scoped result on screen, so no job id or job stay either: a
      // later read must not carry the failed job's identity.
      this.activeScrapeJobId.set("");
      this.activeJobScope.set(null);
      this.message.set(START_SEARCH_MESSAGE);
    }
  }

  private async findRestorableCompetitorJob(): Promise<ScrapeJobResponse | null> {
    const storedJobId = this.workflow.get("lastCompetitorJobId");
    if (storedJobId) {
      try {
        const storedJob = await this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${storedJobId}`);
        if (this.canRestoreJob(storedJob)) {
          this.setupProgress.setJobs([storedJob]);
          return storedJob;
        }
      } catch {
        this.workflow.set("lastCompetitorJobId", null);
      }
    }

    try {
      const jobs = await this.api.get<ScrapeJobResponse[]>("/api/v1/scrape-jobs/", new URLSearchParams({ limit: "50" }));
      this.setupProgress.setJobs(jobs);
      return jobs.find((job) => this.canRestoreJob(job)) || null;
    } catch {
      return null;
    }
  }

  private canRestoreJob(job: ScrapeJobResponse): boolean {
    return job.status === "completed"
      && job.job_type === "competitor_search"
      && (job.scrape_runs_count > 0 || Boolean(job.result_summary))
      && Boolean(job.owned_property_id)
      && job.owned_property_id === this.ownedPropertyId
      && Boolean(job.room_type_category)
      && areComparableRoomCategories(job.room_type_category, this.selectedRoomType());
  }

  private applyRestoredJobFilters(job: ScrapeJobResponse): void {
    // The job's own dates may already have passed, and the backend refuses a
    // past check-in — replaying them verbatim would leave every action
    // failing validation. Roll the window forward, keeping its length.
    const stay = ensureFutureStay(job.check_in, job.check_out);
    this.restoredStayIsStale = stay.checkIn !== job.check_in;
    this.filters.update((filters) => ({
      ...filters,
      destination: job.destination || filters.destination,
      check_in: stay.checkIn,
      check_out: stay.checkOut,
      adults: String(job.adults),
      children: String(job.children),
      rooms: String(job.rooms),
    }));
    this.rawDestination = job.raw_destination || this.rawDestination;

    // The search-size settings travel together (spec §4.1): only a job that
    // actually ran with nearby areas restores its limit, areas and radius. A
    // job answering null (pre-Round 6, no backfill) or an empty list (a search
    // inherited from such a restore) never chose those values — replaying its
    // limit would silently shrink every later search — so the Round-6 defaults
    // already on the form stay: limit 40, 10 km, and the areas loadNearbyDefaults
    // prefilled before this restore started (ngOnInit orders it that way).
    if (Array.isArray(job.nearby_destinations) && job.nearby_destinations.length > 0) {
      const limit = this.readPositiveFilterValue(job.filters_payload["limit"]);
      if (limit) {
        this.setFilter("limit", limit);
      }
      this.nearbyDestinations.set(this.normalizeNearbyDestinations(job.nearby_destinations));
      const radius = this.readPositiveFilterValue(job.radius_km);
      if (radius) {
        this.setFilter("radius_km", radius);
      }
    }

    // A pooled double<->twin restore is valid for reading, but the job's
    // historical exact bucket must not silently replace the room the user has
    // selected today. Exact restores keep the existing synchronization.
    if (job.room_type_category && job.room_type_category === this.selectedRoomType()) {
      this.selectedRoomType.set(job.room_type_category);
      const matchingRoom = this.roomTypes().find((room) => room.room_type_category === job.room_type_category);
      if (matchingRoom) {
        this.selectedRoomId.set(matchingRoom.id);
        this.workflow.set("roomTypeId", matchingRoom.id);
      }
      this.workflow.set("roomTypeCategory", job.room_type_category);
    }
  }

  private readPositiveFilterValue(value: unknown): string {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? String(parsed) : "";
  }

  private readBoundedNumber(value: string, fallback: number, min: number, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(min, Math.min(Math.round(parsed), max));
  }

  private persistCompetitorSelection(): void {
    if (!this.activeScrapeJobId()) {
      return;
    }
    this.workflow.set("lastCompetitorSelection", JSON.stringify({
      jobId: this.activeScrapeJobId(),
      keys: Array.from(this.selectedKeys()),
    }));
  }

  private restoreStoredCompetitorSelection(scrapeJobId: string): boolean {
    const rawSelection = this.workflow.get("lastCompetitorSelection");
    if (!rawSelection) {
      return false;
    }

    try {
      const parsed = JSON.parse(rawSelection) as { jobId?: unknown; keys?: unknown };
      if (parsed.jobId !== scrapeJobId || !Array.isArray(parsed.keys)) {
        return false;
      }
      const availableKeys = new Set(this.competitors().map((competitor) => this.markerKey(competitor)));
      // Chain-initiated: skip if the user has already touched selection this
      // navigation, so this restore does not overwrite their click.
      if (!this.userTouchedSelection()) {
        this.selectedKeys.set(new Set(
          parsed.keys
            .filter((key): key is string => typeof key === "string")
            .filter((key) => availableKeys.has(key)),
        ));
      }
      return true;
    } catch {
      this.workflow.set("lastCompetitorSelection", null);
      return false;
    }
  }

  private async loadMarketSummary(scrapeJobId: string, generation: number): Promise<void> {
    this.marketSummaryStatus.set("loading");
    try {
      // Same params, row limit included, as the marker read (see
      // JOB_RESULT_READ_LIMIT): the box and the header describe one set.
      const params = this.buildMarkerParams(scrapeJobId);
      const summary = await this.api.get<MarketSummary>("/api/v1/market/summary", params);
      // A superseded chain's strip would describe the previous job while the
      // new search runs — findCompetitors cleared it on purpose.
      if (!this.isCurrentChain(generation)) {
        return;
      }
      this.marketSummary.set(summary);
      this.marketSummaryStatus.set("ready");
    } catch {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // The strip is supplementary; a failed summary never blocks the map.
      this.marketSummary.set(null);
      this.marketSummaryStatus.set("error");
    }
  }

  /**
   * Fetches tracked competitors and feeds SetupProgressService. Runs on
   * EVERY map load, independent of applyTrackedCompetitors below: the
   * checklist's tracked-competitors step must reflect reality even when a
   * stored selection means the fetched list is never applied to the cards.
   */
  private async fetchTrackedCompetitors(): Promise<TrackedCompetitorListResponse | null> {
    if (!this.ownedPropertyId || !this.selectedRoomType()) {
      return null;
    }
    try {
      const params = new URLSearchParams({
        owned_property_id: this.ownedPropertyId,
        room_type_category: this.selectedRoomType(),
      });
      const tracked = await this.api.get<TrackedCompetitorListResponse>("/api/v1/tracked/competitors", params);
      this.setupProgress.setTracked(tracked);
      return tracked;
    } catch {
      return null;
    }
  }

  private applyTrackedCompetitors(tracked: TrackedCompetitorListResponse | null): void {
    // Chain-initiated: both writes below skip if the user has already
    // touched selection this navigation, so this apply does not overwrite
    // their click.
    if (!tracked || !this.competitors().length) {
      if (!this.userTouchedSelection()) {
        this.selectedKeys.set(new Set());
      }
      return;
    }
    const trackedPropertyIds = new Set(tracked.competitors.map((competitor) => competitor.property_id));
    const trackedPackageIds = new Set(
      tracked.competitors
        .map((competitor) => competitor.room_package_id)
        .filter((id): id is string => Boolean(id)),
    );
    if (!this.userTouchedSelection()) {
      this.selectedKeys.set(new Set(
        this.competitors()
          .filter((competitor) =>
            Boolean(competitor.room_package_id && trackedPackageIds.has(competitor.room_package_id))
            || Boolean(competitor.property_id && trackedPropertyIds.has(competitor.property_id)),
          )
          .map((competitor) => this.markerKey(competitor)),
      ));
    }
  }

  private buildMarkerParams(scrapeJobId?: string): URLSearchParams {
    const filters = this.filters();
    // Reads about a specific job must use THAT job's stay, not the filters:
    // the filters hold the window for the NEXT search (a restored past stay
    // rolled forward, or dates the user has since typed), which matches none
    // of the job's stored rows.
    const jobScope = scrapeJobId ? this.activeJobScope() : null;
    const stay = jobScope || {
      checkIn: filters.check_in,
      checkOut: filters.check_out,
    };
    const params = new URLSearchParams({
      destination: filters.destination,
      check_in: stay.checkIn,
      check_out: stay.checkOut,
      adults: filters.adults,
      children: filters.children,
      rooms: filters.rooms,
      room_type_category: jobScope?.roomTypeCategory || this.selectedRoomType(),
      // A ROW limit on the API side, not a hotel count: «Πλήθος ανταγωνιστών»
      // (the form's limit) sized the scrape, and reading it back as rows showed
      // a handful of a Round 6 job's hotels (each has several packages).
      limit: JOB_RESULT_READ_LIMIT,
    });
    if (this.ownedPropertyId) {
      params.set("owned_property_id", this.ownedPropertyId);
    }
    if (scrapeJobId) {
      params.set("scrape_job_id", scrapeJobId);
    }
    this.appendComparableScope(params);
    return params;
  }

  /**
   * «Μόνο συγκρίσιμα» and the room it is judged against, on every read of the
   * market (competitors, markers, summary). Always sent, both
   * values: the comparison set belongs to ONE of the owner's rooms, so a
   * switch of room is a different set even over the same job. The legacy
   * `include_similar` is not sent any more.
   */
  private appendComparableScope(params: URLSearchParams): void {
    params.set("comparable_only", String(this.comparableOnly()));
    const ownedRoomTypeId = this.selectedRoomId();
    if (ownedRoomTypeId) {
      params.set("owned_room_type_id", ownedRoomTypeId);
    }
  }

  /**
   * Read the owner's own hotel for the map (spec §4.3): «Εσείς», its radius
   * circle and its own Booking price, for `scrapeJobId` ("" = no job).
   *
   * Best effort: a failed read (an older API, a hiccup) leaves the map without
   * «Εσείς» and says nothing — the competitors are what the page is for.
   * Coordinates that come back null are a different answer, and the map says
   * so (ownCoordinatesMissing).
   */
  private async loadOwnProperty(scrapeJobId: string, generation: number): Promise<void> {
    if (!this.ownedPropertyId) {
      return;
    }
    const params = new URLSearchParams({ owned_property_id: this.ownedPropertyId });
    if (scrapeJobId) {
      params.set("scrape_job_id", scrapeJobId);
    }
    let own: OwnPropertyMapInfo | null = null;
    try {
      own = this.readOwnProperty(await this.api.get<unknown>("/api/v1/maps/own-property", params));
    } catch {
      own = null;
    }
    // A Find click took the screen while this read was out: its answer
    // describes the job the user has moved on from.
    if (!this.isCurrentChain(generation)) {
      return;
    }
    this.ownProperty.set(own);
    this.mapView?.renderOwnProperty();
  }

  /** The response as OwnPropertyMapInfo, or null for anything else (a mock's `[]`, an HTML error page). */
  private readOwnProperty(response: unknown): OwnPropertyMapInfo | null {
    if (!response || typeof response !== "object" || Array.isArray(response)) {
      return null;
    }
    const value = response as Record<string, unknown>;
    if (typeof value["display_name"] !== "string") {
      return null;
    }
    const numberOrNull = (field: string) =>
      typeof value[field] === "number" && Number.isFinite(value[field]) ? (value[field] as number) : null;
    const stringOrNull = (field: string) => (typeof value[field] === "string" ? (value[field] as string) : null);
    return {
      display_name: value["display_name"] as string,
      latitude: numberOrNull("latitude"),
      longitude: numberOrNull("longitude"),
      radius_km: numberOrNull("radius_km"),
      price_per_night_eur: numberOrNull("price_per_night_eur"),
      room_type: stringOrNull("room_type"),
      booking_url: stringOrNull("booking_url"),
    };
  }

  private describeSearchWarning(code: unknown): string {
    if (typeof code !== "string") {
      return "";
    }
    if (code === "radius_skipped_no_coordinates") {
      return "Η ακτίνα δεν εφαρμόστηκε: το κατάλυμά σας δεν έχει συντεταγμένες.";
    }
    if (code === "radius_excluded_all") {
      // The radius the job ran with; the form's value for a job that predates it.
      const radiusKm = this.activeJobScope()?.radiusKm
        ?? this.readBoundedNumber(this.filters().radius_km, DEFAULT_RADIUS_KM, MIN_RADIUS_KM, MAX_RADIUS_KM);
      return `Κανένα κατάλυμα δεν βρέθηκε μέσα στην ακτίνα των ${formatRadius(radiusKm)}. `
        + "Ελέγξτε τη θέση του καταλύματός σας ή μεγαλώστε την ακτίνα.";
    }
    const nearbyPrefix = "nearby_scout_failed:";
    if (code.startsWith(nearbyPrefix) && code.slice(nearbyPrefix.length).trim()) {
      return `Η αναζήτηση στην περιοχή ${code.slice(nearbyPrefix.length).trim()} δεν ολοκληρώθηκε· `
        + "τα υπόλοιπα αποτελέσματα εμφανίζονται κανονικά.";
    }
    return "";
  }

  private resetCompetitorSearchState(): void {
    this.status.set("idle");
    this.actionStatus.set("idle");
    this.setCompetitors([]);
    this.activeResultSummary.set(null);
    this.selectedKeys.set(new Set());
    // Establishing property/room context for this navigation legitimately
    // starts selection over -- disarm the guard so the restore chain that
    // follows (restoreLatestCompetitorSearch) can apply normally.
    this.userTouchedSelection.set(false);
    this.activeScrapeJobId.set("");
    this.activeJobScope.set(null);
    this.mapView?.resetFit();
    this.marketSummary.set(null);
    this.marketSummaryStatus.set("idle");
    this.clearMatchScores();
    // Deliberately keep lastCompetitorJobId/lastCompetitorSelection: they feed
    // restoreLatestCompetitorSearch (stale entries clean themselves up there).
    this.message.set(START_SEARCH_MESSAGE);
    this.mapView?.clearMarkers();
    // «Εσείς» and its circle start over with the page's context; the restore
    // (or ngOnInit, with no job) reads them again.
    this.ownProperty.set(null);
    this.mapView?.renderOwnProperty();
  }

  private showSaveMessage(message: string, hasError: boolean): void {
    if (this.saveMessageTimeout) {
      window.clearTimeout(this.saveMessageTimeout);
      this.saveMessageTimeout = null;
    }
    this.saveHasError.set(hasError);
    this.saveMessage.set(message);
    if (!hasError) {
      this.saveMessageTimeout = window.setTimeout(() => {
        this.saveMessage.set("");
        this.saveMessageTimeout = null;
      }, 4200);
    }
  }

  private setError(message: string): void {
    this.error.set(message);
    this.status.set("error");
    this.message.set("");
    // A failed re-read of the job on screen kept its markers (see loadMarkers)
    // while the rows went: bring the map back in line with the list.
    this.mapView?.renderMarkers();
  }
}
