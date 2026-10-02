/**
 * The «Ιστορικό τιμών αγοράς» card: a loading line, an error, an empty
 * state, the compact low-data panel for 1-2 searches, or the chart itself —
 * medians, the P25-P75 band, a readout on hover or keyboard focus, the key
 * and a table view.
 *
 * The host is the page's `<div class="panel history-card">` itself
 * (attribute selector). The page loads the series and builds the chart's
 * view model (price-history-chart.ts); the card draws it and owns only the
 * readout, which closes whenever the chart is redrawn: a redraw invalidates
 * the old run indices.
 */
import { CommonModule } from "@angular/common";
import { Component, computed, input, linkedSignal, output } from "@angular/core";
import { RouterLink } from "@angular/router";

import { EmptyStateComponent } from "../../components/empty-state.component";
import type { PriceHistoryChartView } from "./price-history-chart";
import type { LoadingState } from "./pricing-format";

@Component({
  selector: "div[appPriceHistoryCard]",
  standalone: true,
  imports: [CommonModule, RouterLink, EmptyStateComponent],
  template: `
    <div class="section-title">
      <h2>Ιστορικό τιμών αγοράς</h2>
      <button class="text-button" type="button" [disabled]="status() === 'loading'" (click)="refreshRequest.emit()">
        Ανανέωση
      </button>
    </div>
    <!-- A refetch keeps the previous chart on screen (dimmed) instead
         of collapsing the card into a loading line and back. -->
    <div *ngIf="status() === 'loading' && !historyView()" class="notification-empty">
      Φόρτωση ιστορικού τιμών...
    </div>
    <div *ngIf="status() === 'error'" class="alert alert-error">{{ error() }}</div>
    <div *ngIf="status() === 'ready' && !historyView()">
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
      [class.is-refreshing]="status() === 'loading'"
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
        <div class="history-chart" [class.is-refreshing]="status() === 'loading'">
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
  `,
})
export class PriceHistoryCardComponent {
  /** The chart's view model, or null when there is nothing to draw. */
  readonly historyView = input<PriceHistoryChartView | null>(null);
  readonly status = input<LoadingState>("idle");
  readonly error = input("");
  /** «Ανανέωση»: the page reads the series again. */
  readonly refreshRequest = output<void>();

  /** 1-2 runs: the low-data panel replaces the near-empty chart. */
  readonly lowDataHistory = computed(() => {
    const chart = this.historyView();
    return chart?.smallSample ? chart : null;
  });
  /** 3+ runs: the chart itself. */
  readonly fullHistory = computed(() => {
    const chart = this.historyView();
    return chart && !chart.smallSample ? chart : null;
  });
  /** Run index under the pointer or the keyboard focus; null = no readout. A redraw closes it. */
  readonly activeRun = linkedSignal<PriceHistoryChartView | null, number | null>({
    source: this.historyView,
    computation: () => null,
  });
  readonly activePoint = computed(() => {
    const index = this.activeRun();
    const chart = this.historyView();
    if (index == null || !chart) {
      return null;
    }
    return chart.points.find((point) => point.index === index) ?? null;
  });
}
