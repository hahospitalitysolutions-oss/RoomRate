/**
 * «Επισκόπηση» (A4): the period's KPIs with the change from last year and the
 * market median, bookings vs ADR by month, revenue by channel and by country.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input, output } from "@angular/core";

import { ComboChartComponent } from "../../components/charts/combo-chart.component";
import { HorizontalBarChartComponent } from "../../components/charts/horizontal-bar-chart.component";
import { ShareChartComponent } from "../../components/charts/share-chart.component";
import { StackedBarChartComponent } from "../../components/charts/stacked-bar-chart.component";
import { CATEGORY_COLORS, type ChartItem, type ChartSeries } from "../../components/charts/chart-types";
import {
  formatChangePct,
  formatCount,
  formatDays,
  formatEuroCents,
  formatPct,
  formatPts,
} from "../../components/charts/chart-utils";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceFilters, PerformanceKpis } from "../../types/performance";
import { closedNote, monthAxis, PerformanceLoader } from "./performance-loader";

type KpiCard = {
  id: string;
  label: string;
  value: string;
  /** null value: the card explains why instead of showing 0. */
  missing: "inventory" | null;
  change: string | null;
  /** Up is good (green-ish sea), down is terracotta. */
  trend: "up" | "down" | "flat" | null;
  lastYear: string | null;
  note: string | null;
};

@Component({
  selector: "app-performance-overview",
  standalone: true,
  imports: [CommonModule, ComboChartComponent, StackedBarChartComponent, ShareChartComponent, HorizontalBarChartComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="error()" class="alert alert-error" role="alert" data-testid="performance-overview-error">{{ error() }}</p>
    <p *ngIf="loading() && !summary.data()" class="muted">Φόρτωση…</p>

    <div class="performance-tab-body" [class.is-refreshing]="loading()">
      <section *ngIf="cards() as kpis" class="performance-kpis" aria-label="Δείκτες περιόδου">
        <article *ngFor="let card of kpis" class="performance-kpi" [attr.data-testid]="'performance-kpi-' + card.id">
          <span class="performance-kpi-label">{{ card.label }}</span>
          <ng-container *ngIf="!card.missing; else missingValue">
            <strong class="performance-kpi-value">{{ card.value }}</strong>
            <span *ngIf="card.change" class="performance-kpi-change" [ngClass]="'is-' + card.trend">
              {{ card.change }} <small>από πέρυσι</small>
            </span>
            <span *ngIf="!card.change && card.lastYear" class="performance-kpi-note">Πέρυσι: {{ card.lastYear }}</span>
            <span *ngIf="card.note" class="performance-kpi-note">{{ card.note }}</span>
          </ng-container>
          <ng-template #missingValue>
            <button class="text-button performance-kpi-missing" type="button" (click)="openRooms.emit()">
              Ορίστε τα δωμάτια ανά τύπο
            </button>
          </ng-template>
        </article>
      </section>

      <section *ngIf="bookingsVsAdr() as chart" class="performance-card" data-testid="performance-chart-bookings-adr">
        <h3>Κρατήσεις και μέση τιμή (ADR) ανά μήνα</h3>
        <app-combo-chart
          label="Κρατήσεις και μέση τιμή ανά μήνα"
          [categories]="chart.categories"
          [categoryTitles]="chart.titles"
          [bars]="chart.bars"
          [lines]="chart.lines"
          [notes]="chart.notes"
        ></app-combo-chart>
      </section>

      <div class="performance-grid-2">
        <section *ngIf="channelMonths() as chart" class="performance-card" data-testid="performance-chart-channels">
          <h3>Έσοδα ανά κανάλι ανά μήνα</h3>
          <app-stacked-bar-chart
            label="Έσοδα ανά κανάλι ανά μήνα"
            [categories]="chart.categories"
            [categoryTitles]="chart.titles"
            [series]="chart.series"
          ></app-stacked-bar-chart>
        </section>

        <section *ngIf="channelShares().length" class="performance-card" data-testid="performance-chart-channel-share">
          <h3>Μερίδιο καναλιών (νύχτες)</h3>
          <app-share-chart label="Μερίδιο καναλιών στις νύχτες που πουλήθηκαν" [items]="channelShares()"></app-share-chart>
        </section>
      </div>

      <section *ngIf="countryItems().length" class="performance-card" data-testid="performance-chart-countries">
        <h3>Χώρες</h3>
        <app-horizontal-bar-chart label="Έσοδα ανά χώρα" format="euro" [items]="countryItems()"></app-horizontal-bar-chart>
      </section>
    </div>
  `,
})
export class PerformanceOverviewComponent {
  readonly ownedPropertyId = input.required<string>();
  /** Bumped by the page when the bookings change: read again. */
  readonly version = input(0);
  readonly filters = input.required<PerformanceFilters>();
  readonly openRooms = output<void>();

  private readonly api = inject(PerformanceApiService);
  private readonly query = computed(() => ({ id: this.ownedPropertyId(), filters: this.filters(), version: this.version() }));

  readonly summary = new PerformanceLoader(this.query, ({ id, filters }) => this.api.summary(id, filters));
  readonly monthly = new PerformanceLoader(this.query, ({ id, filters }) => this.api.monthly(id, filters));
  readonly channels = new PerformanceLoader(this.query, ({ id, filters }) => this.api.channels(id, filters));
  readonly countries = new PerformanceLoader(this.query, ({ id, filters }) => this.api.countries(id, filters));

  private readonly loaders = [this.summary, this.monthly, this.channels, this.countries];
  readonly loading = computed(() => this.loaders.some((loader) => loader.loading()));
  /** One message is enough: the calls fail together. */
  readonly error = computed(() => this.loaders.map((loader) => loader.error()).find(Boolean) ?? "");

  readonly cards = computed<KpiCard[] | null>(() => {
    const summary = this.summary.data();
    if (!summary) return null;
    const { kpis, last_year: lastYear, delta } = summary;
    const trend = (value: number | null | undefined) =>
      value == null ? null : value > 0 ? "up" : value < 0 ? "down" : "flat";
    const before = (pick: (k: PerformanceKpis) => string) => (lastYear ? pick(lastYear) : null);
    return [
      {
        id: "occupancy",
        label: "Πληρότητα",
        value: formatPct(kpis.occupancy_pct),
        missing: kpis.occupancy_pct === null ? "inventory" : null,
        change: delta?.occupancy_pts != null ? formatPts(delta.occupancy_pts) : null,
        trend: trend(delta?.occupancy_pts),
        lastYear: before((k) => formatPct(k.occupancy_pct)),
        note: null,
      },
      {
        id: "adr",
        label: "Μέση τιμή (ADR)",
        value: formatEuroCents(kpis.adr_eur),
        missing: null,
        change: delta?.adr_pct != null ? formatChangePct(delta.adr_pct) : null,
        trend: trend(delta?.adr_pct),
        lastYear: before((k) => formatEuroCents(k.adr_eur)),
        note: summary.market_median_eur !== null ? `Διάμεσος αγοράς: ${formatEuroCents(summary.market_median_eur)}` : null,
      },
      {
        id: "revpar",
        label: "RevPAR",
        value: formatEuroCents(kpis.revpar_eur),
        missing: kpis.revpar_eur === null ? "inventory" : null,
        change: delta?.revpar_pct != null ? formatChangePct(delta.revpar_pct) : null,
        trend: trend(delta?.revpar_pct),
        lastYear: before((k) => formatEuroCents(k.revpar_eur)),
        note: null,
      },
      {
        id: "revenue",
        label: "Έσοδα",
        value: formatEuroCents(kpis.revenue_eur),
        missing: null,
        change: delta?.revenue_pct != null ? formatChangePct(delta.revenue_pct) : null,
        trend: trend(delta?.revenue_pct),
        lastYear: before((k) => formatEuroCents(k.revenue_eur)),
        note: kpis.commission_eur > 0 ? `Προμήθειες: ${formatEuroCents(kpis.commission_eur)}` : null,
      },
      plain("bookings", "Κρατήσεις", formatCount(kpis.bookings), before((k) => formatCount(k.bookings))),
      plain(
        "nights",
        "Νύχτες που πουλήθηκαν",
        formatCount(kpis.room_nights_sold),
        before((k) => formatCount(k.room_nights_sold)),
        kpis.room_nights_available !== null ? `από ${formatCount(kpis.room_nights_available)} διαθέσιμες` : null,
      ),
      plain("cancellations", "Ακυρώσεις", formatPct(kpis.cancellations_pct), before((k) => formatPct(k.cancellations_pct))),
      plain("length-of-stay", "Μέση διαμονή", nights(kpis.avg_length_of_stay), before((k) => nights(k.avg_length_of_stay))),
      plain("lead-time", "Κράτηση πριν την άφιξη", formatDays(kpis.avg_lead_time_days), before((k) => formatDays(k.avg_lead_time_days))),
    ];
  });

  readonly bookingsVsAdr = computed(() => {
    const monthly = this.monthly.data();
    if (!monthly?.months.length) return null;
    const months = monthly.months;
    const lines: ChartSeries[] = [
      // Terracotta stays for last year: this year's ADR line is ink over the sea bars.
      { label: "ADR", values: months.map((month) => month.adr_eur), color: "ink", format: "euro" },
    ];
    if (months.some((month) => month.last_year?.adr_eur != null)) {
      lines.push({ label: "ADR πέρυσι", values: months.map((month) => month.last_year?.adr_eur ?? null), color: "terracotta", format: "euro", dashed: true });
    }
    if (months.some((month) => month.market_median_eur !== null)) {
      lines.push({ label: "Διάμεσος αγοράς", values: months.map((month) => month.market_median_eur), color: "sun", format: "euro", dashed: true });
    }
    return {
      ...monthAxis(months.map((month) => month.month)),
      bars: [{ label: "Κρατήσεις", values: months.map((month) => month.bookings), color: "sea", format: "count" }] as ChartSeries[],
      lines,
      notes: months.map(closedNote),
    };
  });

  readonly channelMonths = computed(() => {
    const channels = this.channels.data();
    if (!channels?.monthly.length) return null;
    const names = channels.totals.map((total) => total.channel);
    return {
      ...monthAxis(channels.monthly.map((month) => month.month)),
      series: names.map((name, index): ChartSeries => ({
        label: name,
        values: channels.monthly.map((month) => month.revenue_by_channel[name] ?? null),
        color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
        format: "euro",
      })),
    };
  });

  readonly channelShares = computed<ChartItem[]>(() =>
    (this.channels.data()?.totals ?? []).map((total, index) => ({
      id: total.channel,
      label: total.channel,
      value: total.share_pct,
      hint: `${formatEuroCents(total.revenue_eur)} · ${formatCount(total.bookings)} κρατήσεις`,
      color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
    })));

  readonly countryItems = computed<ChartItem[]>(() =>
    (this.countries.data()?.countries ?? []).map((country) => ({
      id: country.country,
      label: country.country,
      value: country.revenue_eur,
      hint: `${formatCount(country.bookings)} κρατήσεις · ${formatPct(country.share_pct)}`,
      // «Άγνωστη» / «Λοιπές» are not countries: a quieter colour.
      color: country.country === "Άγνωστη" || country.country === "Λοιπές" ? "muted" : "sea",
    })));
}

function plain(id: string, label: string, value: string, lastYear: string | null, note: string | null = null): KpiCard {
  return { id, label, value, missing: null, change: null, trend: null, lastYear, note };
}

function nights(value: number | null): string {
  return value == null ? "—" : `${new Intl.NumberFormat("el-GR", { maximumFractionDigits: 1 }).format(value)} νύχτες`;
}
