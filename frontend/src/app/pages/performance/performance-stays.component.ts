/**
 * «Διαμονή & κρατήσεις» (A7): how long guests stay, how early they book, the
 * two together (heatmap) and the booking window by arrival month (/stay-patterns).
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input } from "@angular/core";

import { ComboChartComponent } from "../../components/charts/combo-chart.component";
import { HeatmapChartComponent } from "../../components/charts/heatmap-chart.component";
import { HorizontalBarChartComponent } from "../../components/charts/horizontal-bar-chart.component";
import type { ChartItem, ChartSeries } from "../../components/charts/chart-types";
import { formatDays, formatPct } from "../../components/charts/chart-utils";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { LeadTimeBucket, LengthOfStayBucket, PerformanceFilters } from "../../types/performance";
import { monthAxis, PerformanceLoader } from "./performance-loader";

const LENGTH_BUCKETS: { id: LengthOfStayBucket; label: string }[] = [
  { id: "1", label: "1 νύχτα" },
  { id: "2-3", label: "2–3 νύχτες" },
  { id: "4-5", label: "4–5 νύχτες" },
  { id: "6-9", label: "6–9 νύχτες" },
  { id: "10+", label: "10+ νύχτες" },
];

const LEAD_BUCKETS: { id: LeadTimeBucket; label: string }[] = [
  { id: "0-7", label: "0–7 ημέρες" },
  { id: "8-30", label: "8–30 ημέρες" },
  { id: "31-60", label: "31–60 ημέρες" },
  { id: "61+", label: "61+ ημέρες" },
];

@Component({
  selector: "app-performance-stays",
  standalone: true,
  imports: [CommonModule, HorizontalBarChartComponent, HeatmapChartComponent, ComboChartComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="patterns.error()" class="alert alert-error" role="alert" data-testid="performance-stays-error">{{ patterns.error() }}</p>
    <p *ngIf="patterns.loading() && !patterns.data()" class="muted">Φόρτωση…</p>

    <div *ngIf="patterns.data() as data" class="performance-tab-body" [class.is-refreshing]="patterns.loading()">
      <section class="performance-kpis performance-kpis-compact" aria-label="Μέσοι όροι">
        <article class="performance-kpi" data-testid="performance-kpi-avg-stay">
          <span class="performance-kpi-label">Μέση διαμονή</span>
          <strong class="performance-kpi-value">{{ nights(data.avg_length_of_stay) }}</strong>
        </article>
        <article class="performance-kpi" data-testid="performance-kpi-avg-lead">
          <span class="performance-kpi-label">Κράτηση πριν την άφιξη</span>
          <strong class="performance-kpi-value">{{ days(data.avg_lead_time_days) }}</strong>
        </article>
        <article class="performance-kpi" data-testid="performance-kpi-avg-guests">
          <span class="performance-kpi-label">Επισκέπτες ανά κράτηση</span>
          <strong class="performance-kpi-value">{{ guests(data.avg_guests) }}</strong>
        </article>
      </section>

      <div class="performance-grid-2">
        <section class="performance-card" data-testid="performance-chart-length">
          <h3>Διάρκεια διαμονής</h3>
          <app-horizontal-bar-chart label="Κρατήσεις ανά διάρκεια διαμονής" [items]="lengthItems()"></app-horizontal-bar-chart>
        </section>
        <section class="performance-card" data-testid="performance-chart-lead">
          <h3>Πόσο νωρίς κλείνουν</h3>
          <app-horizontal-bar-chart label="Κρατήσεις ανά χρόνο πριν την άφιξη" [items]="leadItems()"></app-horizontal-bar-chart>
        </section>
      </div>

      <section class="performance-card" data-testid="performance-chart-heatmap">
        <h3>Διάρκεια και χρόνος κράτησης μαζί</h3>
        <p class="muted performance-card-lead">Κρατήσεις για κάθε συνδυασμό διάρκειας διαμονής και χρόνου πριν την άφιξη.</p>
        <app-heatmap-chart
          label="Κρατήσεις ανά διάρκεια διαμονής και χρόνο πριν την άφιξη"
          rowTitle="Διάρκεια"
          columnTitle="Πριν την άφιξη"
          [rows]="lengthLabels"
          [columns]="leadLabels"
          [values]="matrix()"
        ></app-heatmap-chart>
      </section>

      <section *ngIf="leadTrend() as chart" class="performance-card" data-testid="performance-chart-lead-trend">
        <h3>Χρόνος κράτησης ανά μήνα άφιξης</h3>
        <app-combo-chart
          label="Μέσες ημέρες από την κράτηση ως την άφιξη, ανά μήνα άφιξης"
          [categories]="chart.categories"
          [categoryTitles]="chart.titles"
          [bars]="chart.bars"
        ></app-combo-chart>
      </section>
    </div>
  `,
})
export class PerformanceStaysComponent {
  readonly ownedPropertyId = input.required<string>();
  /** Bumped by the page when the bookings change: read again. */
  readonly version = input(0);
  readonly filters = input.required<PerformanceFilters>();

  private readonly api = inject(PerformanceApiService);
  private readonly query = computed(() => ({ id: this.ownedPropertyId(), filters: this.filters(), version: this.version() }));

  readonly patterns = new PerformanceLoader(this.query, ({ id, filters }) => this.api.stayPatterns(id, filters));

  readonly lengthLabels = LENGTH_BUCKETS.map((bucket) => bucket.label);
  readonly leadLabels = LEAD_BUCKETS.map((bucket) => bucket.label);

  readonly lengthItems = computed<ChartItem[]>(() => {
    const rows = this.patterns.data()?.length_of_stay ?? [];
    return LENGTH_BUCKETS.map((bucket) => {
      const row = rows.find((item) => item.bucket === bucket.id);
      return { id: bucket.id, label: bucket.label, value: row?.bookings ?? 0, hint: formatPct(row?.share_pct ?? 0) };
    });
  });

  readonly leadItems = computed<ChartItem[]>(() => {
    const rows = this.patterns.data()?.lead_time ?? [];
    return LEAD_BUCKETS.map((bucket) => {
      const row = rows.find((item) => item.bucket === bucket.id);
      return { id: bucket.id, label: bucket.label, value: row?.bookings ?? 0, hint: formatPct(row?.share_pct ?? 0), color: "terracotta" };
    });
  });

  readonly matrix = computed(() => {
    const cells = this.patterns.data()?.matrix ?? [];
    return LENGTH_BUCKETS.map((length) => LEAD_BUCKETS.map((lead) =>
      cells.find((cell) => cell.length_bucket === length.id && cell.lead_bucket === lead.id)?.bookings ?? 0));
  });

  readonly leadTrend = computed(() => {
    const months = this.patterns.data()?.monthly_lead_time ?? [];
    if (!months.length) return null;
    return {
      ...monthAxis(months.map((month) => month.month)),
      bars: [{ label: "Μέσες ημέρες πριν την άφιξη", values: months.map((month) => month.avg_lead_time_days), color: "sea", format: "days" }] as ChartSeries[],
    };
  });

  nights(value: number | null): string {
    return value == null ? "—" : `${new Intl.NumberFormat("el-GR", { maximumFractionDigits: 1 }).format(value)} νύχτες`;
  }

  days(value: number | null): string {
    return formatDays(value);
  }

  guests(value: number | null): string {
    return value == null ? "—" : new Intl.NumberFormat("el-GR", { maximumFractionDigits: 1 }).format(value);
  }
}
