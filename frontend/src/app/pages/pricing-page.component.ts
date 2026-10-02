import { CommonModule } from "@angular/common";
import { Component, computed, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, RouterLink } from "@angular/router";

import { EmptyStateComponent } from "../components/empty-state.component";
import { NotificationBellComponent } from "../components/notification-bell.component";
import { NotificationToastsComponent } from "../components/notification-toasts.component";
import { ApiClientService } from "../services/api-client.service";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { MarketSummary, OwnedPropertyRoomType, ScrapeJobResponse } from "../types/market";
import {
  OwnPriceSource,
  PriceHistorySeries,
  PricePosition,
  PriceRecommendationResponse,
} from "../types/pricing";
import { defaultStayDates, isPastLocalDate } from "../utils/date-defaults";

type LoadingState = "idle" | "loading" | "ready" | "error";

/** The market form. Occupancy stays a string: that is what the inputs hold. */
type PricingFilters = {
  check_in: string;
  check_out: string;
  adults: string;
  children: string;
  rooms: string;
};

/** One scrape run as the chart draws it. */
type HistoryRunPoint = {
  index: number;
  x: number;
  y: number;
  hitX: number;
  hitWidth: number;
  medianLabel: string;
  p25Label: string;
  p75Label: string;
  // "" when the run has too few competitors for a meaningful spread.
  rangeLabel: string;
  competitorCount: number;
  competitorLabel: string;
  dateLabel: string;
  timeLabel: string;
  stampLabel: string;
  ariaLabel: string;
  valueY: number;
  tooltipLeftPct: number;
  tooltipTopPct: number;
  tooltipAlign: "start" | "middle" | "end";
  tooltipBelow: boolean;
  showAxisLabel: boolean;
  isLabelled: boolean;
};

/** One scrape run after the per-competitor prices are collapsed. */
type AggregatedRun = {
  runIndex: number;
  observedAt: string | null;
  median: number;
  // null when the run has too few competitors for a meaningful spread.
  p25: number | null;
  p75: number | null;
  competitorCount: number;
};

type PriceHistoryChartView = {
  viewBox: string;
  plotLeft: number;
  plotRight: number;
  plotTop: number;
  plotBottom: number;
  plotHeight: number;
  captionY: number;
  dateLabelY: number;
  timeLabelY: number;
  yTicks: Array<{ y: number; label: string }>;
  bandSegments: string[];
  // "" in small-sample mode: a line through 1-2 dots reads as a trend that
  // the data cannot support.
  linePoints: string;
  points: HistoryRunPoint[];
  labelledPoints: HistoryRunPoint[];
  axisPoints: HistoryRunPoint[];
  smallSample: boolean;
  hasBand: boolean;
  summaryLabel: string;
  ariaLabel: string;
};

// Chart canvas in user units. The SVG keeps its aspect ratio while scaling to
// the container (no preserveAspectRatio="none": that stretch distorted every
// circle and every glyph), so these units are effectively device pixels on a
// desktop-width card.
const CHART_WIDTH = 560;
const CHART_HEIGHT = 240;
const PLOT_LEFT = 62;
const PLOT_RIGHT = 542;
const PLOT_TOP = 30;
const PLOT_BOTTOM = 194;
// Keeps the first/last dot's price label inside the canvas.
const DOT_INSET = 26;
// Beyond this the dots (and their hit targets) stop being separable; the
// summary line says how many runs were left out.
const MAX_PLOTTED_RUNS = 12;
// Up to this many runs every dot carries its price; past it only the first,
// last, cheapest and dearest are labelled and the rest live in the tooltip
// and the table view.
const MAX_LABELLED_RUNS = 4;
const MAX_X_TICKS = 6;
// 1-2 runs cannot show a trend, so the page shows the low-data panel instead
// of a chart.
const SMALL_SAMPLE_RUNS = 2;
// A P25-P75 band needs both a history to spread across and enough
// competitors per run for quartiles to mean anything.
const MIN_BAND_RUNS = 3;
const MIN_BAND_COMPETITORS = 3;
// The y domain is padded instead of stretched edge to edge, so a two-euro
// wobble no longer fills the canvas and reads as a collapse.
const DOMAIN_PAD_RATIO = 0.15;
// Sub-euro movement is not a story. Below this span the domain widens to an
// absolute window instead of a percentage of almost nothing.
const MIN_DOMAIN_SPAN = 2;
// Round tick values, searched finest-first until a step leaves 2-3 of them
// inside the domain.
const TICK_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
const MIN_LABEL_GAP_X = 64;
const MIN_LABEL_GAP_Y = 26;
const DEFAULT_STAY = defaultStayDates();

// Async-written state is signal-based: every fetch continuation runs outside
// the Angular zone (supabase-js getSession holds a navigator.locks lock that
// zone.js cannot patch), and signal writes schedule change detection anyway.
// That includes the date/occupancy filters: the prefill from the latest
// competitor search writes them after awaiting the job.
@Component({
  selector: "app-pricing-page",
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, EmptyStateComponent, NotificationBellComponent, NotificationToastsComponent],
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
            <div *ngIf="recommendation() as rec" class="panel recommendation-card">
              <div class="recommendation-headline">
                <div class="recommendation-figure">
                  <h2 class="recommendation-label">Προτεινόμενη τιμή ανά βράδυ</h2>
                  <div class="recommendation-price">{{ formatEuro(rec.recommended_price_eur) }}</div>
                  <p class="recommendation-range">
                    Εύρος {{ formatEuroFloor(rec.price_range_low_eur) }} &ndash; {{ formatEuroCeil(rec.price_range_high_eur) }}
                  </p>
                </div>
                <div class="chip-row recommendation-pills">
                  <span class="confidence-pill" [ngClass]="confidenceChipClass()" data-testid="confidence-pill">
                    {{ confidenceLabel() }}
                  </span>
                  <span class="source-chip" data-testid="source-pill">
                    {{ rec.source === "agent" ? "AI agent" : "Στατιστική" }}
                  </span>
                </div>
              </div>
              <p *ngIf="smallComparableSample()" class="alert small-sample-notice">
                {{ smallSampleNotice() }}
              </p>
              <p *ngIf="reasoningSummary()" class="recommendation-summary">{{ reasoningSummary() }}</p>
              <ul *ngIf="rec.key_factors.length" class="key-factors" aria-label="Βασικοί παράγοντες">
                <li *ngFor="let factor of rec.key_factors" class="factor-chip">{{ factor }}</li>
              </ul>
              <!-- A one-sentence reasoning IS its summary: a toggle would only
                   repeat it, so it appears when there is more to read. -->
              <div *ngIf="hasMoreReasoning()" class="reasoning-disclosure">
                <button
                  type="button"
                  class="text-button reasoning-toggle"
                  [attr.aria-expanded]="reasoningOpen()"
                  aria-controls="recommendation-reasoning"
                  (click)="reasoningOpen.set(!reasoningOpen())"
                >
                  Γιατί αυτή η τιμή;
                </button>
                <p *ngIf="reasoningOpen()" id="recommendation-reasoning" class="recommendation-reasoning">
                  {{ rec.reasoning }}
                </p>
              </div>
              <p *ngIf="result()!.audit_id || result()!.cached" class="recommendation-footer">
                <span *ngIf="result()!.cached">Από προσωρινή μνήμη</span>
                <span *ngIf="result()!.audit_id">
                  Ελεγμένη απόφαση {{ result()!.audit_id }}<ng-container *ngIf="result()!.model_version"> · {{ result()!.model_version }}</ng-container>
                </span>
              </p>
            </div>

            <div *ngIf="result()" class="panel">
              <div class="section-title">
                <h2>Στατιστικά αγοράς</h2>
              </div>
              <p *ngIf="statsScopeLabel()" class="muted pricing-hint">{{ statsScopeLabel() }}</p>
              <p *ngIf="cancellationClassNote()" class="muted pricing-hint" data-testid="cancellation-class-note">
                {{ cancellationClassNote() }}
              </p>
              <div class="stats-grid">
                <div *ngFor="let entry of statisticEntries()">
                  <span>{{ entry.label }}</span>
                  <strong>{{ entry.value }}</strong>
                </div>
              </div>
              <!-- Without a recommendation the not-enough-data card lists the notes. -->
              <ul *ngIf="result()!.recommendation_available && statisticNotes().length" class="stat-notes">
                <li *ngFor="let note of statisticNotes()" class="muted">{{ note }}</li>
              </ul>
            </div>
            </ng-container>

            <div class="panel history-card">
              <div class="section-title">
                <h2>Ιστορικό τιμών αγοράς</h2>
                <button class="text-button" type="button" [disabled]="historyStatus() === 'loading'" (click)="loadHistory()">
                  Ανανέωση
                </button>
              </div>
              <!-- A refetch keeps the previous chart on screen (dimmed) instead
                   of collapsing the card into a loading line and back. -->
              <div *ngIf="historyStatus() === 'loading' && !history()" class="notification-empty">
                Φόρτωση ιστορικού τιμών...
              </div>
              <div *ngIf="historyStatus() === 'error'" class="alert alert-error">{{ historyError() }}</div>
              <div *ngIf="historyStatus() === 'ready' && !history()">
                <app-empty-state
                  icon="chart"
                  title="Δεν υπάρχει ακόμη ιστορικό τιμών"
                  explanation="Το ιστορικό χτίζεται από τις αναζητήσεις σας: χρειάζονται τουλάχιστον δύο ολοκληρωμένες αναζητήσεις για να φανεί μεταβολή."
                ></app-empty-state>
              </div>
              <!-- 1-2 runs cannot show a trend: a compact panel says what would,
                   and links to the setting that builds a history day by day. -->
              <div
                *ngIf="lowDataHistory() as chart"
                class="history-low-data"
                [class.is-refreshing]="historyStatus() === 'loading'"
                data-testid="history-low-data"
              >
                <p class="history-low-data-text">Χρειάζονται αναζητήσεις σε διαφορετικές ημέρες για να φανεί τάση.</p>
                <p class="muted pricing-hint">{{ chart.summaryLabel }}</p>
                <a class="history-low-data-cta" routerLink="/settings" fragment="schedule">
                  Ρύθμιση ημερήσιας αυτόματης αναζήτησης
                </a>
              </div>
              <ng-container *ngIf="fullHistory() as chart">
                <div class="history-scroll">
                  <div class="history-chart" [class.is-refreshing]="historyStatus() === 'loading'">
                    <svg
                      class="history-svg"
                      [attr.viewBox]="chart.viewBox"
                      role="group"
                      [attr.aria-label]="chart.ariaLabel"
                    >
                      <line
                        *ngFor="let tick of chart.yTicks"
                        class="history-gridline"
                        [attr.x1]="chart.plotLeft"
                        [attr.x2]="chart.plotRight"
                        [attr.y1]="tick.y"
                        [attr.y2]="tick.y"
                      />
                      <text
                        *ngFor="let tick of chart.yTicks"
                        class="history-y-tick"
                        [attr.x]="chart.plotLeft - 8"
                        [attr.y]="tick.y + 4"
                        text-anchor="end"
                      >{{ tick.label }}</text>
                      <text class="history-axis-caption" x="6" [attr.y]="chart.captionY">&euro;/βράδυ</text>

                      <polygon
                        *ngFor="let band of chart.bandSegments"
                        class="history-band"
                        [attr.points]="band"
                      />
                      <polyline *ngIf="chart.linePoints" class="history-line" [attr.points]="chart.linePoints" />

                      <line
                        *ngIf="activePoint() as active"
                        class="history-crosshair"
                        [attr.x1]="active.x"
                        [attr.x2]="active.x"
                        [attr.y1]="chart.plotTop"
                        [attr.y2]="chart.plotBottom"
                      />

                      <circle
                        *ngFor="let point of chart.points"
                        class="history-dot"
                        [class.is-active]="point.index === activeRun()"
                        [attr.cx]="point.x"
                        [attr.cy]="point.y"
                        [attr.r]="point.index === activeRun() ? 5.5 : 4"
                      />
                      <text
                        *ngFor="let point of chart.labelledPoints"
                        class="history-value"
                        [attr.x]="point.x"
                        [attr.y]="point.valueY"
                        text-anchor="middle"
                      >{{ point.medianLabel }}</text>

                      <ng-container *ngFor="let point of chart.axisPoints">
                        <text
                          class="history-x-tick-date"
                          [attr.x]="point.x"
                          [attr.y]="chart.dateLabelY"
                          text-anchor="middle"
                        >{{ point.dateLabel }}</text>
                        <text
                          class="history-x-tick-time"
                          [attr.x]="point.x"
                          [attr.y]="chart.timeLabelY"
                          text-anchor="middle"
                        >{{ point.timeLabel }}</text>
                      </ng-container>

                      <!-- One focusable band per run: the hit target is the
                           whole column, not the 8px dot, and keyboard focus
                           opens the same readout as hover. -->
                      <rect
                        *ngFor="let point of chart.points"
                        class="history-hit"
                        [attr.x]="point.hitX"
                        [attr.width]="point.hitWidth"
                        [attr.y]="chart.plotTop"
                        [attr.height]="chart.plotHeight"
                        tabindex="0"
                        role="img"
                        [attr.aria-label]="point.ariaLabel"
                        (mouseenter)="activeRun.set(point.index)"
                        (mouseleave)="activeRun.set(null)"
                        (focus)="activeRun.set(point.index)"
                        (blur)="activeRun.set(null)"
                      />
                    </svg>
                    <div
                      *ngIf="activePoint() as active"
                      class="history-tooltip"
                      [ngClass]="'tip-' + active.tooltipAlign"
                      [class.tip-below]="active.tooltipBelow"
                      [style.left.%]="active.tooltipLeftPct"
                      [style.top.%]="active.tooltipTopPct"
                    >
                      <strong>{{ active.medianLabel }}</strong>
                      <span>Διάμεσος αγοράς</span>
                      <span>{{ active.stampLabel }}</span>
                      <span *ngIf="active.rangeLabel">P25&ndash;P75: {{ active.rangeLabel }}</span>
                      <span>{{ active.competitorLabel }}</span>
                    </div>
                  </div>
                </div>

                <div class="history-meta">
                  <span *ngIf="chart.hasBand" class="history-key">
                    <span class="history-key-item">
                      <svg class="history-key-mark" viewBox="0 0 18 8" aria-hidden="true">
                        <line class="history-key-line" x1="1" y1="4" x2="17" y2="4" />
                        <circle class="history-key-dot" cx="9" cy="4" r="3" />
                      </svg>
                      Διάμεσος ανά αναζήτηση
                    </span>
                    <span class="history-key-item">
                      <svg class="history-key-mark" viewBox="0 0 18 8" aria-hidden="true">
                        <rect class="history-key-band" x="1" y="1" width="16" height="6" />
                      </svg>
                      Εύρος P25&ndash;P75 ανταγωνιστών
                    </span>
                  </span>
                  <span>{{ chart.summaryLabel }}</span>
                </div>

                <p class="muted pricing-hint">
                  Κάθε τελεία είναι η διάμεσος από τη φθηνότερη τιμή κάθε ανταγωνιστή σε μία αναζήτηση της αγοράς.
                </p>

                <details class="history-table">
                  <summary>Πίνακας τιμών</summary>
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Αναζήτηση</th>
                        <th scope="col">Διάμεσος</th>
                        <th scope="col">P25</th>
                        <th scope="col">P75</th>
                        <th scope="col">Ανταγωνιστές</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr *ngFor="let point of chart.points">
                        <td>{{ point.stampLabel }}</td>
                        <td>{{ point.medianLabel }}</td>
                        <td>{{ point.p25Label }}</td>
                        <td>{{ point.p75Label }}</td>
                        <td>{{ point.competitorCount }}</td>
                      </tr>
                    </tbody>
                  </table>
                </details>
              </ng-container>
            </div>
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
  /** The card's one-line «why»: the reasoning's first sentence. */
  readonly reasoningSummary = computed(() => firstSentence(this.recommendation()?.reasoning ?? ""));
  /** True when the reasoning says more than its summary, so the toggle has something to open. */
  readonly hasMoreReasoning = computed(() =>
    (this.recommendation()?.reasoning ?? "").trim().length > this.reasoningSummary().length);
  /** «Γιατί αυτή η τιμή;» — closed by default and closed again for every new recommendation. */
  readonly reasoningOpen = signal(false);
  readonly statisticEntries = computed(() => this.buildStatisticEntries(this.result()));
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
  /** «Βάση: …» — which hotels of the latest search back the statistics; "" when unknown. */
  readonly statsScopeLabel = computed(() => {
    const scope = this.result()?.statistics.stats_scope;
    if (!scope) {
      return "";
    }
    // The matching agent's comparable set for the owner's room backed the
    // numbers: counted in hotels, singular/plural built whole (Greek inflects
    // both the adjective and the noun).
    if (scope.used === "agent") {
      const comparable = scope.comparable ?? 0;
      return comparable === 1
        ? "Βάση: 1 συγκρίσιμο κατάλυμα (εκτίμηση AI)"
        : `Βάση: ${comparable} συγκρίσιμα καταλύματα (εκτίμηση AI)`;
    }
    const same = `Βάση: ${scope.same_category} ίδιας κατηγορίας`;
    if (scope.used !== "same_plus_similar") {
      return same;
    }
    return `${same} + ${scope.similar} ${scope.similar === 1 ? "παρόμοιο" : "παρόμοια"}`;
  });
  /**
   * Rate plans (spec §5): said only when the backend really compared within
   * the owner's own cancellation class ("matched"). "all" (no package shared
   * the class, the floor fell back to every package) and an older API without
   * the field both say nothing — the sentence would then be untrue.
   */
  readonly cancellationClassNote = computed(() =>
    this.result()?.statistics.stats_scope?.cancellation_class === "matched"
      ? "Σύγκριση σε τιμές ίδιας πολιτικής ακύρωσης με το δωμάτιό σας."
      : "");

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
  /** 1-2 runs: the low-data panel replaces the near-empty chart. */
  readonly lowDataHistory = computed(() => {
    const chart = this.history();
    return chart?.smallSample ? chart : null;
  });
  /** 3+ runs: the chart itself. */
  readonly fullHistory = computed(() => {
    const chart = this.history();
    return chart && !chart.smallSample ? chart : null;
  });
  /** Run index under the pointer or the keyboard focus; null = no readout. */
  readonly activeRun = signal<number | null>(null);
  readonly activePoint = computed(() => {
    const index = this.activeRun();
    const chart = this.history();
    if (index == null || !chart) {
      return null;
    }
    return chart.points.find((point) => point.index === index) ?? null;
  });

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
    this.reasoningOpen.set(false);
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
      this.activeRun.set(null);
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
      // A redraw invalidates the old run indices, so the readout closes with it.
      this.activeRun.set(null);
      this.history.set(this.buildHistoryChart(series));
      this.historyStatus.set("ready");
    } catch (error) {
      this.historyError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση του ιστορικού τιμών.");
      this.historyStatus.set("error");
      this.history.set(null);
      this.activeRun.set(null);
    }
  }

  /** Υψηλή = green, Μέτρια = amber, anything else = the neutral grey of «Χαμηλή». */
  confidenceChipClass(): string {
    switch (this.recommendation()?.confidence) {
      case "high":
        return "confidence-high";
      case "medium":
        return "confidence-medium";
      default:
        return "confidence-low";
    }
  }

  /**
   * The confidence chip's text, MAPPED from the enum rather than interpolated.
   * The English chip concatenated the raw API value («medium confidence»);
   * in Greek the adjective has to agree with «βεβαιότητα», so an interpolated
   * `{{ rec.confidence }} βεβαιότητα` would both leak the English enum value
   * and be ungrammatical. Falls back to the low label on an unknown value,
   * mirroring confidenceChipClass above — an unrecognised confidence must
   * never read as MORE certain than it is.
   */
  confidenceLabel(): string {
    switch (this.recommendation()?.confidence) {
      case "high":
        return "Υψηλή βεβαιότητα";
      case "medium":
        return "Μέτρια βεβαιότητα";
      default:
        return "Χαμηλή βεβαιότητα";
    }
  }

  formatEuro(value: number | null | undefined): string {
    if (value == null || !Number.isFinite(value)) {
      return "—";
    }
    return new Intl.NumberFormat("el-GR", {
      style: "currency",
      currency: "EUR",
      maximumFractionDigits: 0,
    }).format(value);
  }

  /**
   * Range bounds rounded OUTWARD — floor the low bound, ceil the high bound
   * — instead of each rounding independently to the nearest euro. Nearest-
   * euro rounding can NARROW a range (305.5-306.5 collapsed to "306 € –
   * 307 €", silently dropping the true 305.5-305.99 slice); floor/ceil
   * guarantees the shown range always CONTAINS the true range. Chosen over
   * showing one decimal so the range keeps the same 0-decimal styling as
   * every other price on this card.
   */
  formatEuroFloor(value: number | null | undefined): string {
    if (value == null || !Number.isFinite(value)) {
      return "—";
    }
    return this.formatEuro(Math.floor(value));
  }

  formatEuroCeil(value: number | null | undefined): string {
    if (value == null || !Number.isFinite(value)) {
      return "—";
    }
    return this.formatEuro(Math.ceil(value));
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

  private buildStatisticEntries(
    result: PriceRecommendationResponse | null,
  ): Array<{ label: string; value: string }> {
    const statistics = result?.statistics;
    if (!statistics) {
      return [];
    }
    const position = this.formatPosition(statistics.position);
    return [
      { label: "Διάμεση τιμή αγοράς", value: this.formatEuro(statistics.market_median_eur) },
      { label: "Χαμηλό εύρος αγοράς", value: this.formatEuro(statistics.market_p25_eur) },
      { label: "Υψηλό εύρος αγοράς", value: this.formatEuro(statistics.market_p75_eur) },
      {
        label: this.referencePriceLabel(result.own_price_source),
        value: this.formatEuro(statistics.own_reference_price_eur),
      },
      {
        label: "Θέση σας στην αγορά",
        value: this.smallComparableSample() && statistics.position
          ? `${position} (ενδεικτικό — μικρό δείγμα)`
          : position,
      },
      { label: "Στατιστική βάση αναφοράς", value: this.formatEuro(statistics.statistical_recommendation_eur) },
      // A bare dash read as "no movement"; say what each trend is waiting for.
      {
        label: "Τάση (7 ημερών)",
        value: this.formatPct(statistics.trend_7d_pct, "— (δεν υπάρχει ακόμη συγκρίσιμη αναζήτηση 7+ ημερών)"),
      },
      {
        label: "Τάση (30 ημερών)",
        value: this.formatPct(statistics.trend_30d_pct, "— (δεν υπάρχει ακόμη συγκρίσιμη αναζήτηση 30+ ημερών)"),
      },
      // The label already carries the unit («Ημέρες»), so the value is the bare
      // number: «12 ημέρες» beside it would read «Ημέρες … 12 ημέρες».
      { label: "Ημέρες μέχρι την άφιξη", value: String(statistics.lead_time_days) },
      { label: "Αναζητήσεις που συγκρίθηκαν", value: String(statistics.sample_runs) },
    ];
  }

  private formatPct(value: number | null | undefined, missing = "—"): string {
    if (value == null || !Number.isFinite(value)) {
      return missing;
    }
    return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
  }

  /** «Φθηνότερα από εσάς: k από n καταλύματα» — a count the owner can check, not a percentile. */
  private formatPosition(position: PricePosition | null | undefined): string {
    if (!position) {
      return "—";
    }
    const noun = position.total === 1 ? "κατάλυμα" : "καταλύματα";
    return `Φθηνότερα από εσάς: ${position.cheaper_than_you} από ${position.total} ${noun}`;
  }

  /** The reference-price row names where its number came from. */
  private referencePriceLabel(source: OwnPriceSource | null | undefined): string {
    switch (source) {
      case "booking_live":
        return "Η τιμή σας στο Booking για αυτές τις ημερομηνίες";
      case "onboarding_sample":
        return "Τιμή αναφοράς από την εγγραφή";
      default:
        return "Η τιμή αναφοράς σας";
    }
  }

  private readBoundedNumber(value: string, fallback: number, min: number, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(min, Math.min(Math.round(parsed), max));
  }

  /**
   * Turn the raw per-(run, competitor) series into the chart's view model.
   *
   * One dot per scrape run = the median of every competitor's cheapest
   * nightly price in that run; the band behind it is the P25-P75 spread of
   * those same per-competitor prices.
   */
  private buildHistoryChart(series: PriceHistorySeries): PriceHistoryChartView | null {
    const allRuns = this.aggregateRuns(series);
    if (!allRuns.length) {
      return null;
    }
    const runs = allRuns.slice(-MAX_PLOTTED_RUNS);
    const smallSample = runs.length <= SMALL_SAMPLE_RUNS;
    const bandSpans = runs.length < MIN_BAND_RUNS ? [] : this.bandSpans(runs);
    const bandedRuns = new Set<number>(bandSpans.flat());

    // The y domain covers only what is actually drawn, padded on both sides so
    // the extremes never sit pinned to the plot edges.
    const drawnValues = runs.map((run) => run.median);
    for (const index of bandedRuns) {
      drawnValues.push(runs[index].p25 as number, runs[index].p75 as number);
    }
    const [domainMin, domainMax] = this.paddedDomain(drawnValues);
    const plotHeight = PLOT_BOTTOM - PLOT_TOP;
    const scaleY = (value: number): number =>
      round1(PLOT_BOTTOM - ((value - domainMin) / (domainMax - domainMin)) * plotHeight);

    const firstX = PLOT_LEFT + DOT_INSET;
    const lastX = PLOT_RIGHT - DOT_INSET;
    const xs = runs.map((_, index) =>
      runs.length === 1
        ? round1((firstX + lastX) / 2)
        : round1(firstX + (index * (lastX - firstX)) / (runs.length - 1)),
    );
    const ys = runs.map((run) => scaleY(run.median));

    const medians = runs.map((run) => run.median);
    const lowest = Math.min(...medians);
    const highest = Math.max(...medians);
    const labelled = this.pickValueLabels(medians, xs, ys);
    const axisStep = Math.ceil(runs.length / MAX_X_TICKS);

    const points: HistoryRunPoint[] = runs.map((run, index) => {
      const x = xs[index];
      const y = ys[index];
      const hasSpread = run.p25 != null && run.p75 != null;
      // The hit band runs midpoint to midpoint, so the whole column answers
      // the pointer instead of the 8px dot.
      const hitLeft = index === 0 ? PLOT_LEFT : round1((xs[index - 1] + x) / 2);
      const hitRight = index === runs.length - 1 ? PLOT_RIGHT : round1((x + xs[index + 1]) / 2);
      const above = y - 12 >= PLOT_TOP + 4;
      const rangeLabel = hasSpread
        ? `${this.formatEuro(run.p25)} – ${this.formatEuro(run.p75)}`
        : "";
      const competitorLabel = run.competitorCount === 1
        ? "1 ανταγωνιστής"
        : `${run.competitorCount} ανταγωνιστές`;
      const stampLabel = this.formatRunStamp(run.observedAt);
      const medianLabel = this.formatEuro(run.median);
      return {
        index,
        x,
        y,
        hitX: hitLeft,
        hitWidth: round1(hitRight - hitLeft),
        medianLabel,
        p25Label: hasSpread ? this.formatEuro(run.p25) : "—",
        p75Label: hasSpread ? this.formatEuro(run.p75) : "—",
        rangeLabel,
        competitorCount: run.competitorCount,
        competitorLabel,
        dateLabel: this.formatRunDate(run.observedAt),
        timeLabel: this.formatRunTime(run.observedAt),
        stampLabel,
        ariaLabel: [
          stampLabel,
          `διάμεσος ${medianLabel}`,
          ...(rangeLabel ? [`P25–P75 ${rangeLabel}`] : []),
          competitorLabel,
        ].join(" · "),
        valueY: above ? round1(y - 12) : round1(y + 20),
        tooltipLeftPct: round1((x / CHART_WIDTH) * 100),
        tooltipTopPct: round1((y / CHART_HEIGHT) * 100),
        tooltipAlign: x < CHART_WIDTH * 0.2 ? "start" : x > CHART_WIDTH * 0.8 ? "end" : "middle",
        tooltipBelow: y < PLOT_TOP + plotHeight * 0.3,
        // Stepping back from the newest run keeps the latest date labelled
        // and the labels evenly spaced whatever the run count.
        showAxisLabel: (runs.length - 1 - index) % axisStep === 0,
        isLabelled: labelled.has(index),
      };
    });

    const runWord = runs.length === 1 ? "αναζήτηση" : "αναζητήσεις";
    const countLabel = allRuns.length > runs.length
      ? `Τελευταίες ${runs.length} από ${allRuns.length} αναζητήσεις`
      : `${runs.length} ${runWord}`;
    const spreadLabel = lowest === highest
      ? `διάμεσος ${this.formatEuro(lowest)}`
      : `εύρος ${this.formatEuro(lowest)} – ${this.formatEuro(highest)}`;

    return {
      viewBox: `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`,
      plotLeft: PLOT_LEFT,
      plotRight: PLOT_RIGHT,
      plotTop: PLOT_TOP,
      plotBottom: PLOT_BOTTOM,
      plotHeight,
      captionY: PLOT_TOP - 12,
      dateLabelY: PLOT_BOTTOM + 22,
      timeLabelY: PLOT_BOTTOM + 36,
      yTicks: this.buildYTicks(domainMin, domainMax).map((value) => ({
        y: scaleY(value),
        label: this.formatEuro(value),
      })),
      bandSegments: bandSpans.map((span) => this.bandPolygon(span, runs, xs, scaleY)),
      // 1-2 runs get dots only: a line between them would read as a trend.
      linePoints: smallSample ? "" : points.map((point) => `${point.x},${point.y}`).join(" "),
      points,
      labelledPoints: points.filter((point) => point.isLabelled),
      axisPoints: points.filter((point) => point.showAxisLabel),
      smallSample,
      hasBand: bandSpans.length > 0,
      summaryLabel: `${countLabel} · ${spreadLabel}`,
      ariaLabel: `Ιστορικό τιμών αγοράς: ${countLabel.toLowerCase()}, ${spreadLabel}.`,
    };
  }

  /** Collapse the per-(run, competitor) points into one chronological run list. */
  private aggregateRuns(series: PriceHistorySeries): AggregatedRun[] {
    const byRun = new Map<number, { prices: number[]; observedAt: string | null }>();
    for (const point of series.points || []) {
      if (point.min_price_eur == null || !Number.isFinite(point.min_price_eur)) {
        continue;
      }
      const entry = byRun.get(point.run_index) || { prices: [], observedAt: null };
      entry.prices.push(point.min_price_eur);
      if (point.observed_at && (!entry.observedAt || point.observed_at < entry.observedAt)) {
        entry.observedAt = point.observed_at;
      }
      byRun.set(point.run_index, entry);
    }
    return Array.from(byRun.entries())
      .map(([runIndex, entry]) => {
        const sorted = [...entry.prices].sort((a, b) => a - b);
        // Quartiles of one or two prices are not a spread — they would draw a
        // confident-looking band around what is really a single observation.
        const hasSpread = sorted.length >= MIN_BAND_COMPETITORS;
        return {
          runIndex,
          observedAt: entry.observedAt,
          median: quantile(sorted, 0.5),
          p25: hasSpread ? quantile(sorted, 0.25) : null,
          p75: hasSpread ? quantile(sorted, 0.75) : null,
          competitorCount: sorted.length,
        };
      })
      .sort((a, b) => {
        if (a.observedAt && b.observedAt) {
          return a.observedAt.localeCompare(b.observedAt);
        }
        // run_index 1 is the newest run, so descending index = chronological.
        return b.runIndex - a.runIndex;
      });
  }

  /**
   * Group the runs into the stretches the band may span.
   *
   * A run without enough competitors breaks the band instead of being
   * interpolated across: the gap is the honest statement that nothing is
   * known about the spread there. A lone qualifying run has no width to draw,
   * so its quartiles stay in the readout and the table only.
   */
  private bandSpans(runs: AggregatedRun[]): number[][] {
    const spans: number[][] = [];
    let current: number[] = [];
    runs.forEach((run, index) => {
      if (run.p25 != null && run.p75 != null) {
        current.push(index);
        return;
      }
      if (current.length >= 2) {
        spans.push(current);
      }
      current = [];
    });
    if (current.length >= 2) {
      spans.push(current);
    }
    return spans;
  }

  /** P75 edge left to right, then the P25 edge back — one closed area. */
  private bandPolygon(
    span: number[],
    runs: AggregatedRun[],
    xs: number[],
    scaleY: (value: number) => number,
  ): string {
    const top = span.map((index) => `${xs[index]},${scaleY(runs[index].p75 as number)}`);
    const bottom = [...span].reverse().map((index) => `${xs[index]},${scaleY(runs[index].p25 as number)}`);
    return [...top, ...bottom].join(" ");
  }

  /**
   * Pad the value range so the chart never stretches its extremes to the edges.
   *
   * A span under MIN_DOMAIN_SPAN — a flat history, or one that moved by a few
   * cents — is widened to an absolute window around its midpoint first. A
   * ratio-only pad there would spend three quarters of the canvas on sub-euro
   * movement (the same lie the old full-stretch scaling told) and would leave
   * the tick search no round value to place. The floor is clamped at zero so a
   * wide spread never opens negative euro space below the cheapest run.
   */
  private paddedDomain(values: number[]): [number, number] {
    let low = Math.min(...values);
    let high = Math.max(...values);
    if (high - low < MIN_DOMAIN_SPAN) {
      const middle = (low + high) / 2;
      const half = Math.max(MIN_DOMAIN_SPAN / 2, Math.abs(middle) * 0.05);
      low = middle - half;
      high = middle + half;
    }
    const pad = (high - low) * DOMAIN_PAD_RATIO;
    return [Math.max(0, low - pad), high + pad];
  }

  /** The 2-3 round values that fit inside the padded domain. */
  private buildYTicks(low: number, high: number): number[] {
    for (const step of TICK_STEPS) {
      const ticks: number[] = [];
      for (let value = Math.ceil(low / step) * step; value <= high && ticks.length <= 3; value += step) {
        ticks.push(round1(value));
      }
      if (ticks.length >= 2 && ticks.length <= 3) {
        return ticks;
      }
    }
    return [round1(low), round1(high)];
  }

  /**
   * Choose which dots carry a visible price.
   *
   * Up to MAX_LABELLED_RUNS runs every dot is labelled (the plan's "dots with
   * their price"); past that a number on every point is unreadable, so only
   * the newest, oldest, cheapest and dearest keep a label — and only where it
   * does not collide with one already placed. Everything else stays reachable
   * through the readout and the table view.
   */
  private pickValueLabels(medians: number[], xs: number[], ys: number[]): Set<number> {
    if (medians.length <= MAX_LABELLED_RUNS) {
      return new Set(medians.map((_, index) => index));
    }
    const highest = medians.indexOf(Math.max(...medians));
    const lowest = medians.indexOf(Math.min(...medians));
    const picked = new Set<number>();
    const placed: Array<{ x: number; y: number }> = [];
    for (const candidate of [medians.length - 1, 0, highest, lowest]) {
      if (picked.has(candidate)) {
        continue;
      }
      const collides = placed.some(
        (mark) =>
          Math.abs(mark.x - xs[candidate]) < MIN_LABEL_GAP_X
          && Math.abs(mark.y - ys[candidate]) < MIN_LABEL_GAP_Y,
      );
      if (collides) {
        continue;
      }
      picked.add(candidate);
      placed.push({ x: xs[candidate], y: ys[candidate] });
    }
    return picked;
  }

  private formatRunDate(observedAt: string | null): string {
    const parsed = parseObserved(observedAt);
    return parsed ? parsed.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit" }) : "—";
  }

  private formatRunTime(observedAt: string | null): string {
    const parsed = parseObserved(observedAt);
    return parsed
      ? parsed.toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hour12: false })
      : "";
  }

  private formatRunStamp(observedAt: string | null): string {
    const parsed = parseObserved(observedAt);
    if (!parsed) {
      return "Άγνωστη ημερομηνία";
    }
    const date = parsed.toLocaleDateString("el-GR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
    return `${date} ${this.formatRunTime(observedAt)}`;
  }
}

/**
 * The first sentence of `text`: up to the first «.», «!», «?» or Greek «;»
 * that is followed by whitespace or the end, so a decimal («110.5 €») never
 * cuts it short. The whole (trimmed) text when it has no such stop.
 */
function firstSentence(text: string): string {
  const trimmed = text.trim();
  // \u037e is the Greek question mark; the ASCII semicolon is left out on
  // purpose: in English text it joins clauses, it does not end a sentence.
  const match = /^[\s\S]*?[.!?\u037e](?=\s|$)/.exec(trimmed);
  return match ? match[0] : trimmed;
}

function parseObserved(observedAt: string | null): Date | null {
  if (!observedAt) {
    return null;
  }
  const parsed = new Date(observedAt);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** An API timestamp as «dd/MM/yyyy» in the viewer's calendar; "" when missing or unreadable. */
function formatGreekDate(stamp: string | null | undefined): string {
  const parsed = parseObserved(stamp ?? null);
  return parsed
    ? parsed.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit", year: "numeric" })
    : "";
}

/** Linear-interpolation quantile over an ASCENDING array (median at q=0.5). */
function quantile(sorted: number[], q: number): number {
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, sorted.length - 1);
  return sorted[lower] + (position - lower) * (sorted[upper] - sorted[lower]);
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}
