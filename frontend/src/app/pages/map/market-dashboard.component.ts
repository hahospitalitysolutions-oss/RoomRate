/**
 * «Αγορά vs εμείς»: the comparison dashboard that took the map's place as
 * the market page's centre column (the map is still one tab away).
 *
 * After a search it sets the competitors' aggregates beside the owner's own
 * figures: the market's median and average price against the owner's price,
 * the price range with how many hotels undercut the owner, the rating, a
 * price ladder of every hotel, and a per-hotel table whose rows tick hotels
 * into the examined set and open each hotel's drill-down page.
 *
 * Scope: «Όλη η αγορά» reads /market/summary's figures (the ONE summary the
 * results column shows too); «Επιλεγμένοι» reads the ticked hotels' own rows.
 * The ticks are the page's existing selection (the same ones «Προσθήκη
 * επιλεγμένων στην παρακολούθηση» saves), so there is one notion of «my
 * competitors» on the page, not two.
 *
 * The host is the page's `<section class="market-dashboard">` (attribute
 * selector). Every input is a value the page already holds as a signal, so
 * the dashboard owns nothing but its scope and the ladder's readout.
 */
import { CommonModule } from "@angular/common";
import { Component, computed, input, output, signal } from "@angular/core";
import { RouterLink } from "@angular/router";

import { EmptyStateComponent } from "../../components/empty-state.component";
import { MarketHistoryComponent } from "./market-history.component";
import type { CompetitorMapMarker, MarketSummary } from "../../types/market";
import { formatDistance, formatEuro } from "./competitor-format";
import {
  aggregateHotels,
  aggregateMarket,
  buildPriceLadder,
  collapseToHotels,
  compareLabel,
  formatOptionalEuro,
  formatPricePosition,
  formatRating,
  formatSignedEuro,
  HotelComparisonRow,
  LadderDot,
  OwnReference,
  pricePosition,
} from "./market-comparison";
import type { LoadingState } from "./map-page.constants";
import {
  buildAvailability,
  buildMarketTrend,
  buildValueScatter,
  distanceBands,
  ratingBands,
  ValueDot,
  valueSentence,
} from "./market-insights";
import type { PriceHistorySeries } from "../../types/pricing";

export type DashboardScope = "all" | "selected";

@Component({
  selector: "section[appMarketDashboard]",
  standalone: true,
  imports: [CommonModule, RouterLink, EmptyStateComponent, MarketHistoryComponent],
  template: `
    <header class="dashboard-head">
      <div>
        <h2 id="dashboard-title">Αγορά vs εμείς</h2>
        <p class="muted pricing-hint" data-testid="dashboard-scope-hint">{{ scopeHint() }}</p>
      </div>
      <div class="segmented" role="radiogroup" aria-label="Ανταγωνιστές που εξετάζονται" data-testid="dashboard-scope">
        <button
          type="button"
          role="radio"
          class="segmented-option"
          [attr.aria-checked]="effectiveScope() === 'all'"
          [class.is-active]="effectiveScope() === 'all'"
          data-testid="dashboard-scope-all"
          (click)="scope.set('all')"
        >Όλη η αγορά</button>
        <button
          type="button"
          role="radio"
          class="segmented-option"
          [attr.aria-checked]="effectiveScope() === 'selected'"
          [class.is-active]="effectiveScope() === 'selected'"
          [disabled]="!selectedHotels().length"
          [attr.title]="selectedHotels().length ? null : 'Τσεκάρετε ανταγωνιστές στον πίνακα ή στη δεξιά στήλη'"
          data-testid="dashboard-scope-selected"
          (click)="scope.set('selected')"
        >Επιλεγμένοι ({{ selectedHotels().length }})</button>
      </div>
    </header>

    <!-- Loading: the search (or the restore) is still out, nothing to compare yet. -->
    <div *ngIf="status() === 'loading' && !hotels().length" class="results-empty" role="status" data-testid="dashboard-loading">
      <strong>Γίνεται αναζήτηση</strong>
      <span>Η σύγκριση με την αγορά θα εμφανιστεί μόλις ολοκληρωθεί η αναζήτηση.</span>
    </div>

    <!-- The error text itself is in the «Φίλτρα» column, by the action that caused it; said once, not twice. -->
    <div *ngIf="status() === 'error'" class="alert alert-error" role="alert" data-testid="dashboard-error">
      Η σύγκριση δεν είναι διαθέσιμη αυτή τη στιγμή. Η αιτία εμφανίζεται στη στήλη «Φίλτρα».
    </div>

    <!-- Empty: no search yet, or a search with nothing to compare (the results column says why). -->
    <app-empty-state
      *ngIf="status() !== 'loading' && status() !== 'error' && !hotels().length"
      data-testid="dashboard-empty"
      icon="chart"
      [title]="emptyTitle()"
      [explanation]="emptyExplanation()"
    ></app-empty-state>

    <ng-container *ngIf="hotels().length">
      <p *ngIf="ownReference().price === null" class="map-notice" data-testid="dashboard-no-own-price">
        Δεν υπάρχει ακόμη δική σας τιμή για αυτό το δωμάτιο, οπότε εμφανίζονται μόνο τα στοιχεία της αγοράς.
        Η τιμή σας προκύπτει από το Booking όταν η αναζήτηση βρει το κατάλυμά σας ή από την τιμή αναφοράς της εγγραφής.
      </p>

      <!-- The headline comparisons: the market's figure, the owner's, and the gap in words. -->
      <div class="versus-grid" data-testid="dashboard-kpis" [class.is-refreshing]="status() === 'loading' || summaryStatus() === 'loading'">
        <article class="versus-card is-own-card" data-testid="kpi-own">
          <h3>Η τιμή σας</h3>
          <strong class="versus-value">{{ formatOptionalEuro(ownReference().price) }}</strong>
          <p class="versus-gap muted">{{ ownSourceShort(ownReference().source) }}</p>
        </article>
        <article class="versus-card" data-testid="kpi-median">
          <h3>Διάμεση αγοράς</h3>
          <strong class="versus-value">{{ formatOptionalEuro(aggregate()?.median) }}</strong>
          <p *ngIf="compareLabel(ownReference().price, aggregate()?.median ?? null) as gap" class="versus-gap">Εσείς: {{ gap }}</p>
        </article>
        <article class="versus-card" data-testid="kpi-average">
          <h3>Μέση αγοράς</h3>
          <strong class="versus-value">{{ formatOptionalEuro(aggregate()?.avg) }}</strong>
          <p *ngIf="compareLabel(ownReference().price, aggregate()?.avg ?? null) as gap" class="versus-gap">Εσείς: {{ gap }}</p>
        </article>
        <article class="versus-card" data-testid="kpi-range">
          <h3>Εύρος αγοράς</h3>
          <strong class="versus-value">{{ formatOptionalEuro(aggregate()?.min) }} – {{ formatOptionalEuro(aggregate()?.max) }}</strong>
          <p *ngIf="position()" class="versus-gap">{{ formatPricePosition(position()) }}</p>
        </article>
      </div>
      <p *ngIf="effectiveScope() === 'selected'" class="muted pricing-hint dashboard-source">
        Αγορά: η φθηνότερη τιμή κάθε επιλεγμένου καταλύματος.
      </p>

      <!-- «Κλίμακα τιμών»: every hotel on one price axis, the owner and the median as reference marks. -->
      <figure *ngIf="ladder() as chart" class="panel-figure price-ladder" data-testid="price-ladder">
        <figcaption class="figure-title">
          <strong>Κλίμακα τιμών</strong>
          <span class="ladder-key">
            <span class="ladder-key-item"><svg viewBox="0 0 12 12" aria-hidden="true"><circle class="ladder-dot" cx="6" cy="6" r="5" /></svg>Ανταγωνιστής</span>
            <span *ngIf="effectiveScope() === 'selected'" class="ladder-key-item"><svg viewBox="0 0 12 12" aria-hidden="true"><circle class="ladder-dot is-out" cx="6" cy="6" r="4.5" /></svg>Εκτός επιλογής</span>
            <span *ngIf="chart.own" class="ladder-key-item"><svg viewBox="0 0 12 12" aria-hidden="true"><rect class="ladder-own-mark" x="1" y="1" width="10" height="10" transform="rotate(45 6 6)" /></svg>Εσείς</span>
            <span *ngIf="chart.median" class="ladder-key-item"><svg viewBox="0 0 14 12" aria-hidden="true"><line class="ladder-median" x1="7" y1="0" x2="7" y2="12" /></svg>Διάμεση αγοράς</span>
          </span>
        </figcaption>
        <div class="ladder-chart">
          <svg class="ladder-svg" [attr.viewBox]="chart.viewBox" role="img" [attr.aria-label]="chart.ariaLabel">
            <line class="ladder-axis" [attr.x1]="chart.plotLeft" [attr.x2]="chart.plotRight" [attr.y1]="chart.axisY" [attr.y2]="chart.axisY" />
            <ng-container *ngFor="let tick of chart.ticks">
              <line class="ladder-tick" [attr.x1]="tick.x" [attr.x2]="tick.x" [attr.y1]="chart.axisY" [attr.y2]="chart.axisY + 4" />
              <text class="ladder-tick-label" [attr.x]="tick.x" [attr.y]="chart.axisY + 18" text-anchor="middle">{{ tick.label }}</text>
            </ng-container>
            <ng-container *ngIf="chart.median as median">
              <line class="ladder-median" [attr.x1]="median.x" [attr.x2]="median.x" y1="22" [attr.y2]="chart.axisY" />
              <text class="ladder-ref-label" [attr.x]="median.x" y="14" [attr.text-anchor]="anchorFor(median.x, chart)">Διάμεση {{ median.label }}</text>
            </ng-container>
            <circle
              *ngFor="let dot of chart.dots; trackBy: trackDot"
              class="ladder-dot"
              [class.is-out]="!dot.inScope"
              [class.is-active]="activeDot()?.key === dot.key"
              [attr.cx]="dot.x"
              [attr.cy]="dot.y"
              r="5"
              (mouseenter)="activeDot.set(dot)"
              (mouseleave)="activeDot.set(null)"
            />
            <ng-container *ngIf="chart.own as own">
              <line class="ladder-own-line" [attr.x1]="own.x" [attr.x2]="own.x" y1="22" [attr.y2]="chart.axisY" />
              <rect class="ladder-own-mark" [attr.x]="own.x - 6" [attr.y]="chart.axisY - 6" width="12" height="12" [attr.transform]="'rotate(45 ' + own.x + ' ' + chart.axisY + ')'" />
              <text class="ladder-ref-label is-own" [attr.x]="own.x" [attr.y]="chart.median && Math.abs(chart.median.x - own.x) < 90 ? 28 : 14" [attr.text-anchor]="anchorFor(own.x, chart)">Εσείς {{ own.label }}</text>
            </ng-container>
          </svg>
          <div
            *ngIf="activeDot() as dot"
            class="history-tooltip"
            [ngClass]="dot.x < chart.width * 0.2 ? 'tip-start' : dot.x > chart.width * 0.8 ? 'tip-end' : ''"
            [style.left.%]="(dot.x / chart.width) * 100"
            [style.top.%]="(dot.y / chart.height) * 100"
          >
            <strong>{{ dot.priceLabel }}</strong>
            <span>{{ dot.hotelName }}</span>
            <span *ngIf="ownReference().price !== null">{{ formatSignedEuro(dot.price - (ownReference().price ?? 0)) }} από εσάς</span>
          </div>
        </div>
      </figure>

      <!-- «Τιμή, βαθμολογία, απόσταση»: what the market charges for what it offers. -->
      <h3 class="dashboard-section-title">Τιμή, βαθμολογία, απόσταση</h3>
      <div class="insight-grid">
        <figure *ngIf="scatter() as chart" class="panel-figure" data-testid="value-scatter">
          <figcaption class="figure-title">
            <strong>Τιμή και βαθμολογία</strong>
            <span class="ladder-key">
              <span class="ladder-key-item"><svg viewBox="0 0 12 12" aria-hidden="true"><circle class="ladder-dot" cx="6" cy="6" r="5" /></svg>Ανταγωνιστής</span>
              <span *ngIf="chart.own" class="ladder-key-item"><svg viewBox="0 0 14 12" aria-hidden="true"><line class="ladder-own-line" x1="0" y1="6" x2="14" y2="6" /></svg>Εσείς</span>
              <span *ngIf="chart.median" class="ladder-key-item"><svg viewBox="0 0 14 12" aria-hidden="true"><line class="ladder-median" x1="0" y1="6" x2="14" y2="6" /></svg>Διάμεση</span>
            </span>
          </figcaption>
          <div class="scatter-chart">
            <svg class="scatter-svg" [attr.viewBox]="chart.viewBox" role="img" [attr.aria-label]="chart.ariaLabel">
              <ng-container *ngFor="let tick of chart.yTicks">
                <line class="scatter-grid" [attr.x1]="chart.plotLeft" [attr.x2]="chart.plotRight" [attr.y1]="tick.y" [attr.y2]="tick.y" />
                <text class="ladder-tick-label" [attr.x]="chart.plotLeft - 8" [attr.y]="tick.y + 4" text-anchor="end">{{ tick.label }}</text>
              </ng-container>
              <text
                *ngFor="let tick of chart.xTicks"
                class="ladder-tick-label"
                [attr.x]="tick.x"
                [attr.y]="chart.plotBottom + 18"
                text-anchor="middle"
              >{{ tick.label }}</text>
              <text class="ladder-tick-label" [attr.x]="chart.plotRight" [attr.y]="chart.plotBottom + 34" text-anchor="end">Βαθμολογία επισκεπτών</text>
              <line *ngIf="chart.median as median" class="ladder-median" [attr.x1]="chart.plotLeft" [attr.x2]="chart.plotRight" [attr.y1]="median.y" [attr.y2]="median.y" />
              <line *ngIf="chart.own as own" class="ladder-own-line" [attr.x1]="chart.plotLeft" [attr.x2]="chart.plotRight" [attr.y1]="own.y" [attr.y2]="own.y" />
              <circle
                *ngFor="let dot of chart.dots; trackBy: trackValueDot"
                class="ladder-dot"
                [class.is-out]="!dot.inScope"
                [class.is-active]="activeValueDot()?.key === dot.key"
                [attr.cx]="dot.x"
                [attr.cy]="dot.y"
                r="5"
                (mouseenter)="activeValueDot.set(dot)"
                (mouseleave)="activeValueDot.set(null)"
              />
            </svg>
            <div
              *ngIf="activeValueDot() as dot"
              class="history-tooltip"
              [ngClass]="dot.x < chart.width * 0.2 ? 'tip-start' : dot.x > chart.width * 0.8 ? 'tip-end' : ''"
              [style.left.%]="(dot.x / chart.width) * 100"
              [style.top.%]="(dot.y / chart.height) * 100"
            >
              <strong>{{ dot.priceLabel }}</strong>
              <span>{{ dot.hotelName }}</span>
              <span>Βαθμολογία {{ dot.ratingLabel }}</span>
            </div>
          </div>
          <p *ngIf="valueLine()" class="versus-gap" data-testid="value-sentence">{{ valueLine() }}</p>
          <p *ngIf="chart.unrated" class="muted pricing-hint">
            {{ chart.unrated }} {{ chart.unrated === 1 ? "κατάλυμα δεν έχει" : "καταλύματα δεν έχουν" }} βαθμολογία και δεν εμφανίζονται.
          </p>
        </figure>

        <figure *ngIf="bandGroups().length" class="panel-figure" data-testid="price-bands">
          <figcaption class="figure-title"><strong>Διάμεση τιμή ανά ζώνη</strong></figcaption>
          <ng-container *ngFor="let group of bandGroups()">
            <p class="band-group-title">{{ group.title }}</p>
            <dl class="price-bars" [attr.data-testid]="'bands-' + group.id">
              <div *ngIf="ownReference().price !== null" class="price-bar-row is-own">
                <dt>Εσείς</dt>
                <dd>
                  <span class="price-bar-track" aria-hidden="true"><span class="price-bar" [style.width.%]="group.ownWidth"></span></span>
                  <strong>{{ formatOptionalEuro(ownReference().price) }}</strong>
                </dd>
              </div>
              <div *ngFor="let band of group.rows" class="price-bar-row is-market" [attr.title]="band.gap || null">
                <dt>{{ band.label }} <span class="muted">({{ band.count }})</span></dt>
                <dd>
                  <span class="price-bar-track" aria-hidden="true"><span class="price-bar" [style.width.%]="band.widthPct"></span></span>
                  <strong>{{ band.medianLabel }}</strong>
                </dd>
              </div>
            </dl>
          </ng-container>
        </figure>
      </div>

      <!-- «Τάση και διαθεσιμότητα»: where the market is heading, and how much of it is left. -->
      <h3 class="dashboard-section-title">Τάση και διαθεσιμότητα</h3>
      <div class="insight-grid">
        <article class="versus-card" data-testid="market-trend">
          <h3>Διάμεση τιμή αγοράς ανά αναζήτηση</h3>
          <ng-container *ngIf="trend() as current; else noTrend">
            <div class="trend-head">
              <strong class="versus-value">{{ current.latestLabel }}</strong>
              <svg class="trend-spark" viewBox="0 0 120 36" preserveAspectRatio="none" role="img" [attr.aria-label]="'Διάμεση τιμή σε ' + current.runs + ' αναζητήσεις'">
                <polyline class="trend-line" [attr.points]="current.sparkline" />
              </svg>
            </div>
            <p class="versus-gap trend-change" [ngClass]="'is-' + current.direction">{{ current.sinceLast }}</p>
            <p *ngIf="current.sinceFirst" class="versus-gap muted">{{ current.sinceFirst }} · {{ current.runs }} αναζητήσεις</p>
          </ng-container>
          <ng-template #noTrend>
            <p class="versus-gap muted">Η τάση φαίνεται από τη δεύτερη αναζήτηση για τις ίδιες ημερομηνίες.</p>
          </ng-template>
        </article>
        <article *ngIf="availability() as current" class="versus-card" data-testid="market-availability">
          <h3>Διαθεσιμότητα</h3>
          <strong class="versus-value">{{ current.scarce }} από {{ current.total }}</strong>
          <p class="versus-gap">καταλύματα με 1-2 δωμάτια ελεύθερα ({{ current.scarcePct }}%). {{ current.sentence }}</p>
          <p *ngIf="current.scarce && current.scarce < current.total" class="versus-gap muted">
            Διάμεση τιμή: {{ current.scarceMedianLabel }} όσα γεμίζουν, {{ current.restMedianLabel }} τα υπόλοιπα.
          </p>
        </article>
      </div>

      <!-- Per hotel: tick to examine, open to drill down. The table is also the chart's accessible view. -->
      <div class="comparison-table-wrap">
        <table class="comparison-table" data-testid="comparison-table" aria-describedby="dashboard-title">
          <caption class="visually-hidden">Ανταγωνιστές με την τιμή τους δίπλα στη δική σας</caption>
          <thead>
            <tr>
              <th scope="col"><span class="visually-hidden">Επιλογή</span></th>
              <th scope="col">Κατάλυμα</th>
              <th scope="col" class="num">Τιμή/βράδυ</th>
              <th scope="col">Διαφορά από εσάς</th>
              <th scope="col" class="num">Βαθμολογία</th>
              <th scope="col" class="num">Απόσταση</th>
              <th scope="col"><span class="visually-hidden">Ανάλυση</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              *ngFor="let hotel of tableHotels(); trackBy: trackHotel"
              [class.is-selected]="hotel.selected"
              [class.is-active]="activeDot()?.key === hotel.key"
              [attr.data-hotel-key]="hotel.key"
            >
              <td>
                <input
                  type="checkbox"
                  [checked]="hotel.selected"
                  [attr.aria-label]="'Επιλογή ' + hotel.hotelName"
                  (change)="hotelToggle.emit(hotel.key)"
                >
              </td>
              <th scope="row">
                <span class="table-hotel">{{ hotel.hotelName }}</span>
                <small class="muted">{{ hotel.roomType }}<ng-container *ngIf="!hotel.comparable"> · Μη συγκρίσιμο</ng-container></small>
              </th>
              <td class="num"><strong>{{ formatEuro(hotel.price) }}</strong></td>
              <td>
                <span *ngIf="diffFor(hotel) as diff; else noDiff" class="diff-cell">
                  <span class="diff-track" aria-hidden="true">
                    <span
                      class="diff-bar"
                      [class.is-above]="diff.value > 0"
                      [style.width.%]="diff.widthPct"
                    ></span>
                  </span>
                  <span class="diff-label">{{ diff.label }}</span>
                </span>
                <ng-template #noDiff><span class="muted">—</span></ng-template>
              </td>
              <td class="num">{{ formatRating(hotel.reviewScore) }}</td>
              <td class="num">{{ formatDistance(hotel.distanceKm) || "—" }}</td>
              <td>
                <a
                  *ngIf="jobId()"
                  class="drill-link"
                  data-testid="competitor-drilldown-link"
                  [routerLink]="['/map/competitors', hotel.key]"
                  [queryParams]="drillParams()"
                  [attr.aria-label]="'Ανάλυση: ' + hotel.hotelName"
                >Ανάλυση</a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </ng-container>

    <!-- The season so far: the owner's average price against the market, from their own bookings. -->
    <app-market-history></app-market-history>
  `,
})
export class MarketDashboardComponent {
  readonly status = input<LoadingState>("idle");
  readonly summaryStatus = input<LoadingState>("idle");
  readonly competitors = input<readonly CompetitorMapMarker[]>([]);
  readonly selectedKeys = input<ReadonlySet<string>>(new Set());
  readonly marketSummary = input<MarketSummary | null>(null);
  readonly ownReference = input<OwnReference>({ price: null, source: null, roomLabel: "" });
  /** The same market's earlier searches, for «Τάση». */
  readonly marketHistory = input<PriceHistorySeries | null>(null);
  /** The job and room the drill-down reads; no link without a job. */
  readonly jobId = input("");
  readonly roomId = input("");
  readonly comparableOnly = input(true);
  /** The empty state's wording, from the page (it knows whether a search has run). */
  readonly emptyTitle = input("Η σύγκριση με την αγορά εμφανίζεται μετά την αναζήτηση");
  readonly emptyExplanation = input("");

  /** A hotel's tick in the table: the page toggles the hotel's rows. */
  readonly hotelToggle = output<string>();

  readonly scope = signal<DashboardScope>("all");
  readonly activeDot = signal<LadderDot | null>(null);
  readonly activeValueDot = signal<ValueDot | null>(null);

  readonly hotels = computed(() => collapseToHotels(this.competitors(), this.selectedKeys()));
  readonly selectedHotels = computed(() => this.hotels().filter((hotel) => hotel.selected));
  /** «Επιλεγμένοι» with nothing ticked falls back to the whole market rather than an empty screen. */
  readonly effectiveScope = computed<DashboardScope>(() =>
    this.scope() === "selected" && this.selectedHotels().length ? "selected" : "all");
  readonly scopeHotels = computed(() => (this.effectiveScope() === "selected" ? this.selectedHotels() : this.hotels()));
  readonly aggregate = computed(() =>
    this.effectiveScope() === "selected"
      ? aggregateHotels(this.selectedHotels())
      : aggregateMarket(this.marketSummary(), this.hotels()));
  readonly position = computed(() => pricePosition(this.scopeHotels(), this.ownReference().price));
  readonly ladder = computed(() => {
    const scopeKeys = new Set(this.scopeHotels().map((hotel) => hotel.key));
    return buildPriceLadder(
      this.hotels(),
      (hotel) => scopeKeys.has(hotel.key),
      this.ownReference().price,
      this.aggregate()?.median ?? null,
    );
  });
  readonly scatter = computed(() => {
    const scopeKeys = new Set(this.scopeHotels().map((hotel) => hotel.key));
    return buildValueScatter(this.hotels(), (hotel) => scopeKeys.has(hotel.key), this.ownReference().price, this.aggregate()?.median ?? null);
  });
  readonly valueLine = computed(() => valueSentence(this.scopeHotels(), this.ownReference().price));
  /** Median price by distance and by rating, each with the owner's price as the first bar. */
  readonly bandGroups = computed(() => {
    const own = this.ownReference().price;
    return [
      { id: "distance", title: "Απόσταση από εσάς", rows: distanceBands(this.scopeHotels(), own) },
      { id: "rating", title: "Βαθμολογία επισκεπτών", rows: ratingBands(this.scopeHotels(), own) },
    ]
      .filter((group) => group.rows.length)
      .map((group) => {
        const top = Math.max(own ?? 0, ...group.rows.map((row) => row.median));
        return { ...group, ownWidth: own !== null && top > 0 ? Math.round((own / top) * 1000) / 10 : 0 };
      });
  });
  readonly trend = computed(() => buildMarketTrend(this.marketHistory()));
  readonly availability = computed(() => buildAvailability(this.scopeHotels()));
  /** The examined hotels first, then the rest, each cheapest first. */
  readonly tableHotels = computed(() => {
    if (this.effectiveScope() === "all") {
      return this.hotels();
    }
    return [...this.selectedHotels(), ...this.hotels().filter((hotel) => !hotel.selected)];
  });
  private readonly maxAbsDiff = computed(() => {
    const own = this.ownReference().price;
    if (own === null) {
      return 0;
    }
    return Math.max(0, ...this.hotels().map((hotel) => Math.abs(hotel.price - own)));
  });
  readonly scopeHint = computed(() => {
    const total = this.hotels().length;
    if (!total) {
      return "Τα στοιχεία των ανταγωνιστών δίπλα στα δικά σας, μετά από κάθε αναζήτηση.";
    }
    const aggregate = this.aggregate();
    const distance = (aggregate?.avgDistanceKm != null ? ` · μέση απόσταση ${formatDistance(aggregate.avgDistanceKm)}` : "")
      + (aggregate?.avgReview != null ? ` · μέση βαθμολογία ${formatRating(aggregate.avgReview)}` : "");
    if (this.effectiveScope() === "selected") {
      const count = this.selectedHotels().length;
      return `${count} από ${total} ${total === 1 ? "κατάλυμα" : "καταλύματα"} επιλεγμένα${distance}`;
    }
    const hotels = aggregate?.hotels ?? total;
    return `${hotels} ${hotels === 1 ? "κατάλυμα" : "καταλύματα"} της αγοράς${distance}`;
  });
  readonly drillParams = computed(() => {
    const params: Record<string, string> = { job: this.jobId() };
    if (this.roomId()) {
      params["room"] = this.roomId();
    }
    if (!this.comparableOnly()) {
      params["comparable"] = "all";
    }
    return params;
  });

  readonly Math = Math;
  readonly formatEuro = formatEuro;
  readonly formatDistance = formatDistance;
  readonly formatOptionalEuro = formatOptionalEuro;
  readonly formatPricePosition = formatPricePosition;
  readonly formatRating = formatRating;
  readonly formatSignedEuro = formatSignedEuro;
  readonly compareLabel = compareLabel;
  ownSourceShort(source: OwnReference["source"]): string {
    if (source === "booking_live") return "Στο Booking, για αυτές τις ημερομηνίες";
    if (source === "onboarding_sample") return "Τιμή αναφοράς από την εγγραφή";
    return "Δεν υπάρχει ακόμη τιμή σας";
  }

  diffFor(hotel: HotelComparisonRow): { value: number; label: string; widthPct: number } | null {
    const own = this.ownReference().price;
    if (own === null) {
      return null;
    }
    const value = hotel.price - own;
    const max = this.maxAbsDiff();
    return {
      value,
      label: formatSignedEuro(value),
      // Half the track each way from the centre line.
      widthPct: max > 0 ? Math.round((Math.abs(value) / max) * 50 * 10) / 10 : 0,
    };
  }

  anchorFor(x: number, chart: { width: number }): "start" | "middle" | "end" {
    if (x < chart.width * 0.15) {
      return "start";
    }
    return x > chart.width * 0.85 ? "end" : "middle";
  }

  trackHotel(_: number, hotel: HotelComparisonRow): string {
    return hotel.key;
  }

  trackValueDot(_: number, dot: ValueDot): string {
    return dot.key;
  }

  trackDot(_: number, dot: LadderDot): string {
    return dot.key;
  }
}
