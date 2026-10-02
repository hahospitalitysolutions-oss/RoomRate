import { CommonModule } from "@angular/common";
import { Component, computed, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, RouterLink } from "@angular/router";

import { NotificationBellComponent } from "../components/notification-bell.component";
import { NotificationToastsComponent } from "../components/notification-toasts.component";
import { ApiClientService } from "../services/api-client.service";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { MarketSummary, OwnedPropertyRoomType, ScrapeJobResponse } from "../types/market";
import { PriceHistorySeries, PriceRecommendationResponse } from "../types/pricing";
import { defaultStayDates, isPastLocalDate } from "../utils/date-defaults";
import { MarketStatisticsPanelComponent } from "./pricing/market-statistics-panel.component";
import { PriceHistoryCardComponent } from "./pricing/price-history-card.component";
import { buildHistoryChart, PriceHistoryChartView } from "./pricing/price-history-chart";
import { formatGreekDate, LoadingState } from "./pricing/pricing-format";
import { RecommendationCardComponent } from "./pricing/recommendation-card.component";

/** The market form. Occupancy stays a string: that is what the inputs hold. */
type PricingFilters = {
  check_in: string;
  check_out: string;
  adults: string;
  children: string;
  rooms: string;
};

const DEFAULT_STAY = defaultStayDates();

// Async-written state is signal-based: every fetch continuation runs outside
// the Angular zone (supabase-js getSession holds a navigator.locks lock that
// zone.js cannot patch), and signal writes schedule change detection anyway.
// That includes the date/occupancy filters: the prefill from the latest
// competitor search writes them after awaiting the job.
@Component({
  selector: "app-pricing-page",
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    MarketStatisticsPanelComponent,
    NotificationBellComponent,
    NotificationToastsComponent,
    PriceHistoryCardComponent,
    RecommendationCardComponent,
  ],
  template: `
    <main class="page-shell">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate — Τιμολόγηση</p>
          <h1>{{ propertyName() || destination() || "Ανάλυση τιμών" }}</h1>
        </div>
        <div class="header-actions">
          <a routerLink="/map">Χάρτης</a>
          <a routerLink="/settings">Ρυθμίσεις</a>
          <app-notification-bell />
        </div>
      </header>

      <section class="page-body">
        <div *ngIf="contextError()" class="alert alert-error">{{ contextError() }}</div>

        <!-- The room is the page's primary choice: the comparable set, the
             statistics, the chart and the recommendation all belong to it, so
             it sits above the stay form instead of inside it. Keyed by the
             room type's id, not its category: two rooms of one category (two
             doubles, a double and a twin pooled) are two comparison sets and
             must stay two options. -->
        <div class="panel comparison-bar">
          <label class="comparison-bar-label" for="pricing-comparison-room">Σύγκριση για</label>
          <select
            id="pricing-comparison-room"
            class="roomrate-input comparison-bar-select"
            [ngModel]="selectedRoomId()"
            (ngModelChange)="setRoom($event)"
          >
            <option value="">Το επιλεγμένο δωμάτιο αναφοράς</option>
            <option *ngFor="let room of roomTypes()" [value]="room.id">
              {{ room.room_type }} ({{ room.room_type_category }})
            </option>
          </select>
        </div>

        <div class="pricing-grid">
          <aside class="panel form-panel">
            <div class="section-title">
              <h2>Στοιχεία αγοράς</h2>
            </div>
            <p *ngIf="prefillNote()" class="muted pricing-hint">{{ prefillNote() }}</p>
            <label>
              <span>Περιοχή</span>
              <input class="roomrate-input muted-input" [value]="destination()" readonly>
            </label>
            <div class="two-cols">
              <label>
                <span>Άφιξη</span>
                <input class="roomrate-input" type="date" [ngModel]="filters().check_in" (ngModelChange)="setFilter('check_in', $event)">
              </label>
              <label>
                <span>Αναχώρηση</span>
                <input class="roomrate-input" type="date" [ngModel]="filters().check_out" (ngModelChange)="setFilter('check_out', $event)">
              </label>
            </div>
            <div class="three-cols">
              <label>
                <span>Ενήλικες</span>
                <input class="roomrate-input" type="number" min="1" [ngModel]="filters().adults" (ngModelChange)="setFilter('adults', $event)">
              </label>
              <label>
                <span>Παιδιά</span>
                <input class="roomrate-input" type="number" min="0" [ngModel]="filters().children" (ngModelChange)="setFilter('children', $event)">
              </label>
              <label>
                <span>Δωμάτια</span>
                <input class="roomrate-input" type="number" min="1" [ngModel]="filters().rooms" (ngModelChange)="setFilter('rooms', $event)">
              </label>
            </div>

            <button
              class="primary-button"
              type="button"
              [disabled]="recStatus() === 'loading' || !ownedPropertyId() || !contextReady()"
              (click)="getRecommendation()"
            >
              <span *ngIf="recStatus() === 'loading'" class="spin" aria-hidden="true"></span>
              {{ recStatus() === "loading" ? "Ανάλυση αγοράς..." : "Λήψη σύστασης" }}
            </button>
            <p *ngIf="recStatus() === 'loading'" class="muted pricing-hint">
              Ο υβριδικός πράκτορας εξετάζει το ιστορικό τιμών σας και τα στοιχεία της αγοράς. Μπορεί να διαρκέσει έως 30 δευτερόλεπτα.
            </p>
            <div *ngIf="recError()" class="alert alert-error">{{ recError() }}</div>
          </aside>

          <section class="pricing-results">
            <div *ngIf="!result() && recStatus() !== 'loading'" class="results-empty">
              <strong>Δεν υπάρχει ακόμη σύσταση</strong>
              <span>Επιλέξτε ημερομηνίες και πατήστε «Λήψη σύστασης» για να δείτε την προτεινόμενη τιμή ανά βράδυ για το δωμάτιό σας.</span>
            </div>

            <!-- The shape of the card while the answer is on its way. -->
            <div
              *ngIf="recStatus() === 'loading'"
              class="panel recommendation-skeleton"
              aria-busy="true"
              aria-live="polite"
              data-testid="recommendation-skeleton"
            >
              <span class="visually-hidden">Φόρτωση σύστασης τιμής</span>
              <div class="skeleton-headline" aria-hidden="true">
                <div class="skeleton-figure">
                  <span class="skeleton-block skeleton-label"></span>
                  <span class="skeleton-block skeleton-price"></span>
                  <span class="skeleton-block skeleton-range"></span>
                </div>
                <div class="skeleton-pills">
                  <span class="skeleton-block skeleton-pill"></span>
                  <span class="skeleton-block skeleton-pill"></span>
                </div>
              </div>
              <span class="skeleton-block skeleton-line" aria-hidden="true"></span>
              <span class="skeleton-block skeleton-line skeleton-line-short" aria-hidden="true"></span>
            </div>

            <!-- A new request replaces the previous answer with the skeleton
                 rather than leaving stale numbers under it. -->
            <ng-container *ngIf="recStatus() !== 'loading'">
            <div *ngIf="result() && !result()!.recommendation_available" class="panel recommendation-card">
              <div class="section-title">
                <h2>Δεν υπάρχουν αρκετά δεδομένα για σύσταση</h2>
              </div>
              <p class="muted">
                Το RoomRate δεν εφευρίσκει τιμή όταν το ιστορικό της αγοράς δεν την στηρίζει.
                Τρέξτε πρώτα αναζητήσεις ανταγωνιστών (ή ενεργοποιήστε τις προγραμματισμένες αναζητήσεις) για αυτές τις ημερομηνίες.
              </p>
              <ul *ngIf="statisticNotes().length" class="stat-notes">
                <li *ngFor="let note of statisticNotes()" class="muted">{{ note }}</li>
              </ul>
            </div>

            <!-- Headline first (the price, its range, how sure and from where),
                 then one sentence of why; the full reasoning waits behind a
                 toggle and the audit trail stays a quiet footer. -->
            <div
              *ngIf="recommendation() as rec"
              appRecommendationCard
              class="panel recommendation-card"
              [recommendation]="rec"
              [response]="result()!"
              [smallSampleNotice]="smallComparableSample() ? smallSampleNotice() : ''"
            ></div>

            <div
              *ngIf="result() as response"
              appMarketStatisticsPanel
              class="panel"
              [response]="response"
              [smallSample]="smallComparableSample()"
              [notes]="statisticNotes()"
            ></div>
            </ng-container>

            <div
              appPriceHistoryCard
              class="panel history-card"
              [historyView]="history()"
              [status]="historyStatus()"
              [error]="historyError()"
              (refreshRequest)="loadHistory()"
            ></div>
          </section>
        </div>
      </section>

      <app-notification-toasts />
    </main>
  `,
})
export class PricingPageComponent implements OnInit {
  readonly ownedPropertyId = signal("");
  readonly propertyName = signal("");
  readonly destination = signal("");
  readonly roomTypes = signal<OwnedPropertyRoomType[]>([]);
  // The selected room TYPE (its id): the comparison set is the matching
  // agent's verdict for one room, so every request carries its
  // `owned_room_type_id`, and its category is derived from the same row.
  // "" = «Το επιλεγμένο δωμάτιο αναφοράς» (the server's persisted room).
  readonly selectedRoomId = signal("");
  readonly selectedRoom = computed(() =>
    this.roomTypes().find((room) => room.id === this.selectedRoomId()) ?? null);
  readonly selectedCategory = computed(() => this.selectedRoom()?.room_type_category ?? "");
  readonly contextError = signal("");
  /**
   * False until the account context and the prefill have settled. A click
   * before that would ask for today's defaults, and the prefill would then
   * rewrite the form under the recommendation it produced.
   */
  readonly contextReady = signal(false);

  readonly filters = signal<PricingFilters>({
    check_in: DEFAULT_STAY.checkIn,
    check_out: DEFAULT_STAY.checkOut,
    adults: "2",
    children: "0",
    rooms: "1",
  });
  /** «Σύμφωνα με την αναζήτηση της …» once the form replays a search; "" otherwise. */
  readonly prefillNote = signal("");

  readonly recStatus = signal<LoadingState>("idle");
  readonly recError = signal("");
  readonly result = signal<PriceRecommendationResponse | null>(null);
  readonly recommendation = computed(() => {
    const response = this.result();
    return response?.recommendation_available ? response.recommendation : null;
  });
  /**
   * The statistics notes, read once: listed by the not-enough-data card when
   * there is no recommendation, otherwise by the statistics panel. A note the
   * reasoning already quotes verbatim is left out (the statistical reasoning
   * used to append every note), so no sentence appears twice on the page.
   */
  readonly statisticNotes = computed(() => {
    const response = this.result();
    const reasoning = response?.recommendation?.reasoning ?? "";
    return (response?.statistics.notes ?? []).filter((note) => note.trim() && !reasoning.includes(note.trim()));
  });
  /**
   * Distinct hotels behind the CURRENT market snapshot, for the small-sample
   * notice below. Best-effort and separate from `result`: the recommendation
   * payload itself has no competitor-count field (`statistics.sample_runs`
   * counts scrape RUNS over time, not competitors within a run, so it cannot
   * stand in here — see loadComparableCompetitors). Null while unknown; the
   * notice simply stays hidden rather than guessing.
   */
  readonly comparableCompetitors = signal<number | null>(null);
  /** ≤2 comparable hotels makes a percentile/position read as false precision. */
  readonly smallComparableSample = computed(() => {
    const count = this.comparableCompetitors();
    return count != null && count > 0 && count <= 2;
  });
  /**
   * The small-sample sentence, built rather than interpolated: Greek inflects
   * the noun AND the adjective, so the single template string produced the
   * ungrammatical «μόνο 1 συγκρίσιμοι ανταγωνιστές» at the smallest sample
   * there is — the exact case the notice exists for. Only ever read behind
   * smallComparableSample(), so the count here is always 1 or 2.
   */
  readonly smallSampleNotice = computed(() => {
    const count = this.comparableCompetitors();
    const sample = count === 1
      ? "μόνο 1 συγκρίσιμος ανταγωνιστής"
      : `μόνο ${count} συγκρίσιμοι ανταγωνιστές`;
    return `Μικρό δείγμα: ${sample} — η θέση/σύσταση είναι ενδεικτική.`;
  });

  readonly historyStatus = signal<LoadingState>("idle");
  readonly historyError = signal("");
  readonly history = signal<PriceHistoryChartView | null>(null);

  private canonicalDestination = "";

  constructor(
    private readonly api: ApiClientService,
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly route: ActivatedRoute,
  ) {}

  async ngOnInit(): Promise<void> {
    let arrivedFromSearch = false;
    try {
      const currentUser = await this.onboarding.currentUser();
      this.workflow.bindToSubject(currentUser.auth_subject);
      this.ownedPropertyId.set(this.workflow.get("ownedPropertyId") || currentUser.owned_property_id || "");
      this.propertyName.set(this.workflow.get("propertyName") || currentUser.property_name || "");
      this.destination.set(this.workflow.get("destination") || currentUser.destination || "");
      this.canonicalDestination = this.workflow.get("canonicalDestination") || currentUser.canonical_destination || "";
      const storedRoomId = this.workflow.get("roomTypeId") || "";
      const storedCategory = this.workflow.get("roomTypeCategory") || currentUser.selected_room_type_category || "";
      if (!this.ownedPropertyId()) {
        this.contextError.set("Ολοκληρώστε τη ρύθμιση του καταλύματος πριν ζητήσετε συστάσεις τιμών.");
        return;
      }
      this.roomTypes.set(await this.api.get<OwnedPropertyRoomType[]>(
        `/api/v1/onboarding/owned-property/${this.ownedPropertyId()}/room-types`,
      ));
      this.selectedRoomId.set(this.pickRoomId(storedCategory, storedRoomId));
      // Before the history read, so the chart is drawn for the replayed stay.
      // prefillFromLatestSearch swallows its own lookup failure.
      arrivedFromSearch = await this.prefillFromLatestSearch();
      void this.loadHistory();
    } catch (error) {
      this.contextError.set(
        error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση των στοιχείων του λογαριασμού.",
      );
    } finally {
      this.contextReady.set(true);
    }
    // Opened from a search (?job=) whose stay was replayed: the owner came for
    // this recommendation, so it is asked once without a click. The history
    // was just read for this very form, so the answer does not re-read it.
    if (arrivedFromSearch) {
      void this.getRecommendation({ refreshHistory: false });
    }
  }

  // Owner edits only (the prefill writes the signals directly): once a field
  // changes, the form no longer shows the search the prefill note names.
  setFilter(key: keyof PricingFilters, value: string): void {
    this.filters.update((filters) => ({ ...filters, [key]: value }));
    this.prefillNote.set("");
  }

  setRoom(roomTypeId: string): void {
    this.selectedRoomId.set(roomTypeId);
    this.prefillNote.set("");
  }

  /**
   * The room of `category` to select: the preferred one (the map's own
   * choice, or the room already selected) when it IS of that category,
   * otherwise the first room of the category; "" when the property offers
   * none, which leaves «Το επιλεγμένο δωμάτιο αναφοράς».
   */
  private pickRoomId(category: string, preferredId: string): string {
    if (!category) {
      return "";
    }
    const rooms = this.roomTypes();
    const preferred = rooms.find((room) => room.id === preferredId && room.room_type_category === category);
    return (preferred ?? rooms.find((room) => room.room_type_category === category))?.id ?? "";
  }

  /**
   * Open the form on the competitor search the owner came from.
   *
   * `?job=<id>` (the map's link to this page) wins over the last search in
   * workflow storage. Only a completed competitor search whose check-in is
   * still ahead is replayed: a failed or running job has no market behind it,
   * and a past check-in is refused by the backend (rolling it forward would no
   * longer be the search the note names). Anything else — a failed lookup
   * included — keeps today's defaults without a word.
   *
   * Resolves true only when the `?job=` search itself was replayed: that is
   * the arrival from a search the recommendation is then asked for.
   */
  private async prefillFromLatestSearch(): Promise<boolean> {
    const linkedJobId = this.route.snapshot.queryParamMap.get("job");
    const jobId = linkedJobId || this.workflow.get("lastCompetitorJobId");
    if (!jobId) {
      return false;
    }
    let job: ScrapeJobResponse;
    try {
      job = await this.onboarding.getScrapeJob(jobId);
    } catch {
      return false;
    }
    if (
      job?.status !== "completed"
      || job.job_type !== "competitor_search"
      || !job.check_in
      || !job.check_out
      || isPastLocalDate(job.check_in)
    ) {
      return false;
    }
    this.filters.update((filters) => ({
      check_in: job.check_in,
      check_out: job.check_out,
      adults: String(job.adults ?? filters.adults),
      children: String(job.children ?? filters.children),
      rooms: String(job.rooms ?? filters.rooms),
    }));
    // A category this property does not offer would leave the select blank, so
    // the owner's own category stays and the note claims only the dates.
    const categoryReplayed = this.roomTypes().some((room) => room.room_type_category === job.room_type_category);
    if (categoryReplayed) {
      this.selectedRoomId.set(this.pickRoomId(job.room_type_category ?? "", this.selectedRoomId()));
    }
    const searchedOn = formatGreekDate(job.finished_at || job.requested_at);
    const replayed = categoryReplayed ? "την αναζήτηση" : "τις ημερομηνίες της αναζήτησης";
    this.prefillNote.set(searchedOn ? `Σύμφωνα με ${replayed} της ${searchedOn}` : "");
    return Boolean(linkedJobId);
  }

  /**
   * Ask for the recommendation for the form as it stands. `refreshHistory`
   * re-reads the chart afterwards, since the owner may have edited the form
   * since it was drawn; the automatic request on arrival passes false.
   */
  async getRecommendation(options: { refreshHistory?: boolean } = {}): Promise<void> {
    const dateProblem = this.validateDates();
    if (dateProblem) {
      this.recError.set(dateProblem);
      return;
    }
    this.recStatus.set("loading");
    this.recError.set("");
    this.comparableCompetitors.set(null);
    const filters = this.filters();
    try {
      this.result.set(await this.api.post<PriceRecommendationResponse>("/api/v1/agents/price-recommendation", {
        owned_property_id: this.ownedPropertyId(),
        room_type_category: this.selectedCategory() || null,
        // The room whose comparable set backs the statistics; left out for
        // «Το επιλεγμένο δωμάτιο αναφοράς», where the server uses its own.
        ...(this.selectedRoomId() ? { owned_room_type_id: this.selectedRoomId() } : {}),
        check_in: filters.check_in,
        check_out: filters.check_out,
        adults: this.readBoundedNumber(filters.adults, 2, 1, 30),
        children: this.readBoundedNumber(filters.children, 0, 0, 10),
        rooms: this.readBoundedNumber(filters.rooms, 1, 1, 30),
      }));
      this.recStatus.set("ready");
      // Refresh the history chart so it matches the recommendation's dates.
      if (options.refreshHistory !== false) {
        void this.loadHistory();
      }
      // Best-effort comparable-hotel count for the small-sample notice.
      void this.loadComparableCompetitors();
    } catch (error) {
      this.recError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η λήψη σύστασης τιμής.");
      this.recStatus.set("error");
    }
  }

  /**
   * Best-effort comparable-hotel count for the CURRENT market snapshot.
   *
   * The price-recommendation response carries no competitor-count field:
   * `statistics.sample_runs` is the number of scrape RUNS over time, not
   * competitors within a run, so a thin `sample_runs` does not imply a thin
   * market and vice versa — using it here would mislabel the wrong axis.
   * `/api/v1/market/summary`'s `total_hotels` (already read the same way on
   * the map page) is the closest honest signal for "how many hotels back
   * this snapshot", queried with the same filters just sent to the
   * recommendation endpoint. A failure here just leaves the count unknown
   * (notice stays hidden) — it must never blank the recommendation itself.
   */
  private async loadComparableCompetitors(): Promise<void> {
    const filters = this.filters();
    if (!this.canonicalDestination || !filters.check_in || !filters.check_out) {
      this.comparableCompetitors.set(null);
      return;
    }
    try {
      const params = new URLSearchParams({
        destination: this.canonicalDestination,
        check_in: filters.check_in,
        check_out: filters.check_out,
        adults: String(this.readBoundedNumber(filters.adults, 2, 1, 30)),
        children: String(this.readBoundedNumber(filters.children, 0, 0, 10)),
        rooms: String(this.readBoundedNumber(filters.rooms, 1, 1, 30)),
      });
      if (this.selectedCategory()) {
        params.set("room_type_category", this.selectedCategory());
      }
      if (this.selectedRoomId()) {
        params.set("owned_room_type_id", this.selectedRoomId());
      }
      if (this.ownedPropertyId()) {
        params.set("owned_property_id", this.ownedPropertyId());
      }
      const summary = await this.api.get<MarketSummary>("/api/v1/market/summary", params);
      this.comparableCompetitors.set(summary.total_hotels);
    } catch {
      this.comparableCompetitors.set(null);
    }
  }

  async loadHistory(): Promise<void> {
    const filters = this.filters();
    if (!this.canonicalDestination || !filters.check_in || !filters.check_out) {
      this.historyStatus.set("ready");
      this.history.set(null);
      return;
    }
    this.historyStatus.set("loading");
    this.historyError.set("");
    try {
      const params = new URLSearchParams({
        destination: this.canonicalDestination,
        check_in: filters.check_in,
        check_out: filters.check_out,
        adults: String(this.readBoundedNumber(filters.adults, 2, 1, 30)),
        children: String(this.readBoundedNumber(filters.children, 0, 0, 10)),
        rooms: String(this.readBoundedNumber(filters.rooms, 1, 1, 30)),
      });
      if (this.selectedCategory()) {
        params.set("room_type_category", this.selectedCategory());
      }
      if (this.selectedRoomId()) {
        params.set("owned_room_type_id", this.selectedRoomId());
      }
      if (this.ownedPropertyId()) {
        params.set("owned_property_id", this.ownedPropertyId());
      }
      const series = await this.api.get<PriceHistorySeries>("/api/v1/market/price-history", params);
      // A redraw closes the card's readout: the old run indices are gone.
      this.history.set(buildHistoryChart(series));
      this.historyStatus.set("ready");
    } catch (error) {
      this.historyError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση του ιστορικού τιμών.");
      this.historyStatus.set("error");
      this.history.set(null);
    }
  }

  private validateDates(): string | null {
    const { check_in, check_out } = this.filters();
    if (!check_in || !check_out) {
      return "Επιλέξτε και ημερομηνία άφιξης και ημερομηνία αναχώρησης.";
    }
    if (check_in >= check_out) {
      return "Η αναχώρηση πρέπει να είναι μετά την άφιξη.";
    }
    if (isPastLocalDate(check_in)) {
      return "Η άφιξη δεν μπορεί να είναι στο παρελθόν.";
    }
    return null;
  }

  private readBoundedNumber(value: string, fallback: number, min: number, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(min, Math.min(Math.round(parsed), max));
  }

}
