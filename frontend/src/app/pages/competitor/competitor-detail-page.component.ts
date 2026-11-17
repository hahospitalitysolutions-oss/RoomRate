/**
 * Drill-down for one competitor (route `/map/competitors/:key`): the hotel
 * against the owner and the market for the search the dashboard was showing.
 *
 * Opened from «Αγορά vs εμείς» with the job and the owner's room in the
 * query (`job`, `room`, `comparable=all` when «Μόνο συγκρίσιμα» was off), so
 * it reads the same result set the dashboard did. `:key` is the dashboard's
 * hotel key (hotelKey(): the property id, else the hotel name).
 *
 * Everything here comes from endpoints the market and pricing pages already
 * read; nothing is computed that the API does not back:
 *  - the hotel's rows and the market rows: GET /api/v1/maps/competitors
 *  - the owner's price for the job:         GET /api/v1/maps/own-property
 *  - the market figures:                    GET /api/v1/market/summary
 *  - the hotel's rooms, plans and scores:   GET /api/v1/competitors/ (match_room)
 *  - the price history, with this hotel's line over the market median:
 *                                           GET /api/v1/market/price-history
 *
 * Each section loads, fails and empties on its own: a failed history read
 * never blanks the head-to-head, and vice versa. All state written after an
 * await is a signal (AGENTS.md).
 */
import { CommonModule } from "@angular/common";
import { Component, computed, OnInit, signal } from "@angular/core";
import { ActivatedRoute, RouterLink } from "@angular/router";

import { AppHeaderComponent } from "../../components/app-header.component";
import { EmptyStateComponent } from "../../components/empty-state.component";
import { ApiClientService } from "../../services/api-client.service";
import { OnboardingService } from "../../services/onboarding.service";
import { WorkflowStorageService } from "../../services/workflow-storage.service";
import type {
  Competitor,
  CompetitorMapMarker,
  MarketSummary,
  OwnedPropertyRoomType,
  OwnPropertyMapInfo,
  ScrapeJobResponse,
} from "../../types/market";
import type { PriceHistorySeries } from "../../types/pricing";
import { formatDistance, formatEuro, formatScore, hotelKey, matchChipClass, roomsLeftLabel } from "../map/competitor-format";
import { JOB_RESULT_READ_LIMIT, LoadingState } from "../map/map-page.constants";
import { buildRoomGroups, planKey, type RoomPlanGroup } from "../map/room-groups";
import {
  aggregateMarket,
  collapseToHotels,
  compareLabel,
  formatOptionalEuro,
  formatRating,
  formatSignedEuro,
  ownSourceLabel,
  resolveOwnReference,
} from "../map/market-comparison";
import { MatchedCompetitorCardComponent } from "../map/matched-competitor-card.component";
import { buildHistoryChart, PriceHistoryChartView } from "../pricing/price-history-chart";
import { PriceHistoryCardComponent } from "../pricing/price-history-card.component";

type PriceBar = { label: string; value: number; valueLabel: string; widthPct: number; kind: "own" | "competitor" | "market" };

@Component({
  selector: "app-competitor-detail-page",
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    AppHeaderComponent,
    EmptyStateComponent,
    MatchedCompetitorCardComponent,
    PriceHistoryCardComponent,
  ],
  template: `
    <main class="page-shell">
      <app-header>
        <p class="eyebrow">Ανάλυση ανταγωνιστή</p>
        <h1>{{ hotel()?.hotelName || "Ανταγωνιστής" }}</h1>
        <p class="app-band-subtitle">{{ stayLabel() || "Σύγκριση με το κατάλυμά σας και την αγορά." }}</p>
      </app-header>

      <section class="page-body competitor-detail">
        <nav class="detail-nav" aria-label="Πλοήγηση">
          <a class="back-link" routerLink="/map" data-testid="back-to-dashboard">&larr; Πίσω στη σύγκριση αγοράς</a>
          <a
            *ngIf="bookingUrl() as url"
            class="text-button"
            [href]="url"
            target="_blank"
            rel="noopener noreferrer"
          >Άνοιγμα στο Booking</a>
        </nav>

        <div *ngIf="status() === 'loading'" class="panel" role="status" data-testid="detail-loading">
          <p class="muted">Φόρτωση στοιχείων ανταγωνιστή…</p>
        </div>

        <div *ngIf="status() === 'error'" class="panel">
          <p class="alert alert-error" role="alert" data-testid="detail-error">{{ error() }}</p>
          <button type="button" class="secondary-button" (click)="load()">Δοκιμάστε ξανά</button>
        </div>

        <div *ngIf="status() === 'ready' && !hotel()" class="panel" data-testid="detail-not-found">
          <app-empty-state
            icon="search"
            title="Ο ανταγωνιστής δεν βρέθηκε σε αυτή την αναζήτηση"
            explanation="Η αναζήτηση που ανοίξατε δεν έχει πια αποτελέσματα για αυτό το κατάλυμα. Επιστρέψτε στη σύγκριση αγοράς και επιλέξτε ξανά."
          ></app-empty-state>
        </div>

        <ng-container *ngIf="status() === 'ready' && hotel() as competitor">
          <!-- Head to head: the hotel, the owner and the market median on one price scale. -->
          <section class="panel" aria-labelledby="h2h-title" data-testid="detail-head-to-head">
            <div class="section-title">
              <h2 id="h2h-title">Τιμή ανά βράδυ</h2>
            </div>
            <p class="muted pricing-hint">
              Η φθηνότερη τιμή του καταλύματος στην αναζήτηση, δίπλα στη δική σας και στη διάμεση της αγοράς.
            </p>
            <dl class="price-bars" data-testid="price-bars">
              <div *ngFor="let bar of priceBars()" class="price-bar-row" [ngClass]="'is-' + bar.kind">
                <dt>{{ bar.label }}</dt>
                <dd>
                  <span class="price-bar-track" aria-hidden="true"><span class="price-bar" [style.width.%]="bar.widthPct"></span></span>
                  <strong>{{ bar.valueLabel }}</strong>
                </dd>
              </div>
            </dl>
            <p *ngIf="own().price === null" class="map-notice">
              Δεν υπάρχει ακόμη δική σας τιμή για αυτό το δωμάτιο: η σύγκριση γίνεται μόνο με την αγορά.
            </p>

            <div class="versus-grid">
              <article class="versus-card" data-testid="detail-kpi-price">
                <h3>Τιμή</h3>
                <dl>
                  <div><dt>Ανταγωνιστής</dt><dd>{{ formatEuro(competitor.price) }}</dd></div>
                  <div class="is-own"><dt>Εσείς</dt><dd>{{ formatOptionalEuro(own().price) }}</dd></div>
                </dl>
                <p *ngIf="priceGap()" class="versus-gap">{{ priceGap() }}</p>
              </article>
              <article class="versus-card" data-testid="detail-kpi-market">
                <h3>Σε σχέση με την αγορά</h3>
                <dl>
                  <div><dt>Ανταγωνιστής</dt><dd>{{ formatEuro(competitor.price) }}</dd></div>
                  <div><dt>Διάμεση αγοράς</dt><dd>{{ formatOptionalEuro(market()?.median) }}</dd></div>
                </dl>
                <p *ngIf="compareLabel(competitor.price, market()?.median ?? null) as gap" class="versus-gap">{{ gap }} από τη διάμεση</p>
              </article>
              <article class="versus-card" data-testid="detail-kpi-rating">
                <h3>Βαθμολογία επισκεπτών</h3>
                <dl>
                  <div><dt>Ανταγωνιστής</dt><dd>{{ formatRating(competitor.reviewScore) }}</dd></div>
                  <div><dt>Μέση αγοράς</dt><dd>{{ formatRating(market()?.avgReview) }}</dd></div>
                </dl>
                <p class="versus-gap muted">{{ competitor.reviewCount }} κριτικές · η δική σας βαθμολογία δεν συλλέγεται ακόμη.</p>
              </article>
              <article class="versus-card" data-testid="detail-kpi-context">
                <h3>Θέση και διαθεσιμότητα</h3>
                <dl>
                  <div><dt>Απόσταση από εσάς</dt><dd>{{ formatDistance(competitor.distanceKm) || "—" }}</dd></div>
                  <div><dt>Διαθεσιμότητα</dt><dd>{{ roomsLeft() !== null ? roomsLeftLabel(roomsLeft() ?? 0) : "—" }}</dd></div>
                </dl>
                <p *ngIf="matched()?.best_match_score != null" class="versus-gap">
                  Ταίριασμα με το δωμάτιό σας
                  <span class="match-chip" [ngClass]="matchChipClass(matched()?.best_match_score)">{{ formatScore(matched()?.best_match_score) }}</span>
                </p>
              </article>
            </div>
            <p class="muted pricing-hint dashboard-source">
              {{ ownSourceLabel(own().source) }}<ng-container *ngIf="own().roomLabel"> · {{ own().roomLabel }}</ng-container>.
            </p>
          </section>

          <!-- The existing price-history chart, with this hotel's own line over the market median. -->
          <div
            class="panel history-card"
            appPriceHistoryCard
            data-testid="detail-history"
            [historyView]="history()"
            [status]="historyStatus()"
            [error]="historyError()"
            (refreshRequest)="loadHistory()"
          ></div>
          <p *ngIf="historyStatus() === 'ready' && history() && !history()?.hasFocus" class="map-notice detail-history-note" data-testid="detail-history-not-in-basis">
            Το κατάλυμα δεν είναι στη βάση σύγκρισης του ιστορικού για το δωμάτιό σας, οπότε το γράφημα δείχνει μόνο τη διάμεση της αγοράς.
          </p>

          <!-- Its rooms and rate plans, as the room-matching list shows them. -->
          <section class="panel" aria-labelledby="rooms-title" data-testid="detail-rooms">
            <div class="section-title">
              <h2 id="rooms-title">Δωμάτια και πλάνα τιμών</h2>
            </div>
            <p *ngIf="matchedStatus() === 'loading'" class="muted" role="status">Φόρτωση δωματίων…</p>
            <p *ngIf="matchedStatus() === 'error'" class="alert alert-error" role="alert">{{ matchedError() }}</p>
            <app-empty-state
              *ngIf="matchedStatus() === 'ready' && !matched()"
              icon="bed"
              title="Δεν υπάρχουν λεπτομέρειες δωματίων"
              explanation="Η αναζήτηση δεν κράτησε πακέτα για αυτό το κατάλυμα που να αντιστοιχούν στο δωμάτιό σας."
            ></app-empty-state>
            <div *ngIf="matched() as hotelDetail" class="matched-list">
              <article
                appMatchedCompetitorCard
                class="matched-card"
                [hotel]="hotelDetail"
                [groups]="roomGroups()"
                [hotelIndex]="0"
                [expandedPlanKeys]="expandedPlanKeys()"
                (plansToggle)="togglePlans($event)"
              ></article>
            </div>
          </section>
        </ng-container>
      </section>
    </main>
  `,
})
export class CompetitorDetailPageComponent implements OnInit {
  readonly status = signal<LoadingState>("loading");
  readonly error = signal("");
  readonly job = signal<ScrapeJobResponse | null>(null);
  private readonly rows = signal<CompetitorMapMarker[]>([]);
  private readonly ownProperty = signal<OwnPropertyMapInfo | null>(null);
  private readonly ownRoom = signal<OwnedPropertyRoomType | null>(null);
  private readonly summary = signal<MarketSummary | null>(null);
  readonly matched = signal<Competitor | null>(null);
  readonly matchedStatus = signal<LoadingState>("idle");
  readonly matchedError = signal("");
  readonly history = signal<PriceHistoryChartView | null>(null);
  readonly historyStatus = signal<LoadingState>("idle");
  readonly historyError = signal("");
  readonly expandedPlanKeys = signal<Set<string>>(new Set());

  private key = "";
  private ownedPropertyId = "";
  private roomId = "";
  private comparableOnly = true;

  /** Every hotel of the result set, one row each; the drill-down's hotel among them. */
  private readonly hotels = computed(() => collapseToHotels(this.rows(), new Set()));
  readonly hotel = computed(() => this.hotels().find((hotel) => hotel.key === this.key) ?? null);
  readonly market = computed(() => aggregateMarket(this.summary(), this.hotels().filter((hotel) => hotel.key !== this.key)));
  readonly own = computed(() => resolveOwnReference(this.ownProperty(), this.ownRoom()));
  readonly roomGroups = computed<RoomPlanGroup[]>(() => buildRoomGroups(this.matched()?.packages ?? []));
  readonly bookingUrl = computed(() => {
    const url = this.matched()?.booking_url || this.rows().find((row) => hotelKey(row) === this.key)?.booking_url || "";
    return /^https:\/\//i.test(url) ? url : "";
  });
  readonly roomsLeft = computed(() => {
    const rows = this.rows().filter((row) => hotelKey(row) === this.key);
    return rows.length ? Math.max(...rows.map((row) => row.rooms_left || 0)) : null;
  });
  readonly stayLabel = computed(() => {
    const job = this.job();
    if (!job) {
      return "";
    }
    const guests = job.adults + job.children;
    return `${formatDate(job.check_in)} – ${formatDate(job.check_out)} · ${guests} ${guests === 1 ? "άτομο" : "άτομα"} · ${job.raw_destination || job.destination}`;
  });
  readonly priceGap = computed(() => {
    const hotel = this.hotel();
    const own = this.own().price;
    if (!hotel || own === null) {
      return "";
    }
    const diff = formatSignedEuro(hotel.price - own);
    const words = compareLabel(hotel.price, own);
    return words === "Στο ίδιο επίπεδο" ? "Στο ίδιο επίπεδο με εσάς" : `${diff} · ${words} από εσάς`;
  });
  readonly priceBars = computed<PriceBar[]>(() => {
    const hotel = this.hotel();
    if (!hotel) {
      return [];
    }
    const entries: Array<Omit<PriceBar, "widthPct">> = [
      { label: hotel.hotelName, value: hotel.price, valueLabel: formatEuro(hotel.price), kind: "competitor" },
    ];
    const own = this.own().price;
    if (own !== null) {
      entries.unshift({ label: "Εσείς", value: own, valueLabel: formatEuro(own), kind: "own" });
    }
    const median = this.market()?.median ?? null;
    if (median !== null) {
      entries.push({ label: "Διάμεση αγοράς", value: median, valueLabel: formatEuro(median), kind: "market" });
    }
    const max = Math.max(...entries.map((entry) => entry.value));
    return entries.map((entry) => ({ ...entry, widthPct: max > 0 ? Math.round((entry.value / max) * 1000) / 10 : 0 }));
  });

  readonly formatEuro = formatEuro;
  readonly formatOptionalEuro = formatOptionalEuro;
  readonly formatRating = formatRating;
  readonly formatDistance = formatDistance;
  readonly formatScore = formatScore;
  readonly matchChipClass = matchChipClass;
  readonly roomsLeftLabel = roomsLeftLabel;
  readonly compareLabel = compareLabel;
  readonly ownSourceLabel = ownSourceLabel;

  constructor(
    private readonly route: ActivatedRoute,
    private readonly api: ApiClientService,
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
  ) {}

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    const params = this.route.snapshot.paramMap;
    const query = this.route.snapshot.queryParamMap;
    this.key = params.get("key") ?? "";
    this.roomId = query.get("room") ?? "";
    this.comparableOnly = query.get("comparable") !== "all";
    const jobId = query.get("job") ?? "";
    this.status.set("loading");
    this.error.set("");
    if (!this.key || !jobId) {
      this.error.set("Λείπει η αναζήτηση ή ο ανταγωνιστής. Ανοίξτε την ανάλυση από τη σύγκριση αγοράς.");
      this.status.set("error");
      return;
    }
    try {
      const me = await this.onboarding.currentUser();
      this.workflow.bindToSubject(me.auth_subject);
      this.ownedPropertyId = this.workflow.get("ownedPropertyId") || me.owned_property_id || "";
      this.roomId = this.roomId || this.workflow.get("roomTypeId");
      const job = await this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${encodeURIComponent(jobId)}`);
      this.job.set(job);
      const [rows, own, summary, room] = await Promise.all([
        this.api.get<CompetitorMapMarker[]>("/api/v1/maps/competitors", this.jobParams(job)),
        this.readOwnProperty(job),
        this.api.get<MarketSummary>("/api/v1/market/summary", this.jobParams(job)).catch(() => null),
        this.readOwnRoom(),
      ]);
      this.rows.set(Array.isArray(rows) ? rows : []);
      this.ownProperty.set(own);
      this.summary.set(summary && typeof summary === "object" ? summary : null);
      this.ownRoom.set(room);
      this.status.set("ready");
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση του ανταγωνιστή.");
      this.status.set("error");
      return;
    }
    if (this.hotel()) {
      void this.loadMatched();
      void this.loadHistory();
    }
  }

  togglePlans(group: RoomPlanGroup): void {
    const key = planKey(0, group);
    this.expandedPlanKeys.update((keys) => {
      const next = new Set(keys);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  /** The hotel's rooms with the owner's room as reference; a plain read when matching is refused. */
  private async loadMatched(): Promise<void> {
    const job = this.job();
    const hotel = this.hotel();
    if (!job || !hotel) {
      return;
    }
    this.matchedStatus.set("loading");
    this.matchedError.set("");
    const find = (list: Competitor[] | null) =>
      (Array.isArray(list) ? list : []).find((item) => item.hotel_name === hotel.hotelName) ?? null;
    try {
      let list: Competitor[] | null = null;
      if (this.ownedPropertyId) {
        const params = this.jobParams(job);
        params.set("match_room", "true");
        params.set("sort", "price");
        list = await this.api.get<Competitor[]>("/api/v1/competitors/", params).catch(() => null);
      }
      if (!list) {
        list = await this.api.get<Competitor[]>("/api/v1/competitors/", this.jobParams(job));
      }
      this.matched.set(find(list));
      this.matchedStatus.set("ready");
    } catch (error) {
      this.matchedError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση των δωματίων.");
      this.matchedStatus.set("error");
    }
  }

  async loadHistory(): Promise<void> {
    const job = this.job();
    const hotel = this.hotel();
    if (!job || !hotel) {
      return;
    }
    this.historyStatus.set("loading");
    this.historyError.set("");
    try {
      const params = new URLSearchParams({
        destination: job.canonical_destination || job.destination,
        check_in: job.check_in,
        check_out: job.check_out,
        adults: String(job.adults),
        children: String(job.children),
        rooms: String(job.rooms),
      });
      if (job.room_type_category) {
        params.set("room_type_category", job.room_type_category);
      }
      if (this.roomId) {
        params.set("owned_room_type_id", this.roomId);
      }
      if (this.ownedPropertyId) {
        params.set("owned_property_id", this.ownedPropertyId);
      }
      const series = await this.api.get<PriceHistorySeries>("/api/v1/market/price-history", params);
      this.history.set(buildHistoryChart(series, {
        propertyId: hotel.propertyId,
        hotelName: hotel.hotelName,
      }));
      this.historyStatus.set("ready");
    } catch (error) {
      this.historyError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση του ιστορικού τιμών.");
      this.historyStatus.set("error");
      this.history.set(null);
    }
  }

  /** The dashboard's read of the job: the job's own stay and category, the owner's room and the comparison switch. */
  private jobParams(job: ScrapeJobResponse): URLSearchParams {
    const params = new URLSearchParams({
      destination: job.destination,
      check_in: job.check_in,
      check_out: job.check_out,
      adults: String(job.adults),
      children: String(job.children),
      rooms: String(job.rooms),
      limit: JOB_RESULT_READ_LIMIT,
      scrape_job_id: job.id,
      comparable_only: String(this.comparableOnly),
    });
    if (job.room_type_category) {
      params.set("room_type_category", job.room_type_category);
    }
    if (this.ownedPropertyId) {
      params.set("owned_property_id", this.ownedPropertyId);
    }
    if (this.roomId) {
      params.set("owned_room_type_id", this.roomId);
    }
    return params;
  }

  /** Best effort, like the market page: no own price is a state the page explains, not an error. */
  private async readOwnProperty(job: ScrapeJobResponse): Promise<OwnPropertyMapInfo | null> {
    if (!this.ownedPropertyId) {
      return null;
    }
    try {
      const response = await this.api.get<unknown>(
        "/api/v1/maps/own-property",
        new URLSearchParams({ owned_property_id: this.ownedPropertyId, scrape_job_id: job.id }),
      );
      return response && typeof response === "object" && !Array.isArray(response) ? response as OwnPropertyMapInfo : null;
    } catch {
      return null;
    }
  }

  private async readOwnRoom(): Promise<OwnedPropertyRoomType | null> {
    if (!this.ownedPropertyId) {
      return null;
    }
    try {
      const rooms = await this.api.get<OwnedPropertyRoomType[]>(
        `/api/v1/onboarding/owned-property/${this.ownedPropertyId}/room-types`,
      );
      return (Array.isArray(rooms) ? rooms : []).find((room) => room.id === this.roomId) ?? null;
    } catch {
      return null;
    }
  }
}

function formatDate(value: string): string {
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit", year: "numeric" });
}
