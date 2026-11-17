/**
 * «Πληρότητα» (A5): occupancy by month, ADR vs RevPAR, sold vs unsold nights
 * (/monthly), and what is already booked for the next season (/pace).
 * Closed months read «Κλειστό», never unsold; without room counts the
 * occupancy charts give way to «Ορίστε τα δωμάτια ανά τύπο».
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input, output } from "@angular/core";

import { ComboChartComponent } from "../../components/charts/combo-chart.component";
import { StackedBarChartComponent } from "../../components/charts/stacked-bar-chart.component";
import type { ChartSeries } from "../../components/charts/chart-types";
import { formatCount } from "../../components/charts/chart-utils";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceFilters, PerformanceStatus } from "../../types/performance";
import { formatGreekDate } from "../pricing/pricing-format";
import { closedNote, monthAxis, PerformanceLoader } from "./performance-loader";

/** The season /pace looks at: the next one with stays, or after October the coming year. */
export function paceSeason(status: PerformanceStatus, today = new Date()): number {
  if (status.seasons.length) return status.seasons[0];
  return today.getMonth() > 9 ? today.getFullYear() + 1 : today.getFullYear();
}

@Component({
  selector: "app-performance-occupancy",
  standalone: true,
  imports: [CommonModule, ComboChartComponent, StackedBarChartComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="error()" class="alert alert-error" role="alert" data-testid="performance-occupancy-error">{{ error() }}</p>
    <p *ngIf="loading() && !monthly.data()" class="muted">Φόρτωση…</p>

    <div class="performance-tab-body" [class.is-refreshing]="loading()">
      <div *ngIf="inventoryMissing()" class="alert performance-notice" data-testid="performance-occupancy-inventory">
        <span>Χωρίς τον αριθμό δωματίων ανά τύπο δεν υπολογίζονται πληρότητα, RevPAR και απούλητες νύχτες.</span>
        <button class="text-button" type="button" (click)="openRooms.emit()">Ορίστε τα δωμάτια ανά τύπο</button>
      </div>

      <section *ngIf="occupancyChart() as chart" class="performance-card" data-testid="performance-chart-occupancy">
        <h3>Πληρότητα ανά μήνα</h3>
        <app-combo-chart
          label="Πληρότητα ανά μήνα"
          [categories]="chart.categories"
          [categoryTitles]="chart.titles"
          [bars]="chart.bars"
          [lines]="chart.lines"
          [notes]="chart.notes"
        ></app-combo-chart>
      </section>

      <div class="performance-grid-2">
        <section *ngIf="adrRevparChart() as chart" class="performance-card" data-testid="performance-chart-adr-revpar">
          <h3>Μέση τιμή (ADR) και RevPAR</h3>
          <app-combo-chart
            label="Μέση τιμή και RevPAR ανά μήνα"
            [categories]="chart.categories"
            [categoryTitles]="chart.titles"
            [lines]="chart.lines"
            [notes]="chart.notes"
          ></app-combo-chart>
        </section>

        <section *ngIf="nightsChart() as chart" class="performance-card" data-testid="performance-chart-nights">
          <h3>Νύχτες: πουλημένες και απούλητες</h3>
          <app-stacked-bar-chart
            label="Πουλημένες και απούλητες νύχτες ανά μήνα"
            totalLabel="Διαθέσιμες"
            [categories]="chart.categories"
            [categoryTitles]="chart.titles"
            [series]="chart.series"
            [notes]="chart.notes"
          ></app-stacked-bar-chart>
        </section>
      </div>

      <section *ngIf="paceChart() as chart" class="performance-card" data-testid="performance-pace">
        <h3>Ήδη κλεισμένα για τη σεζόν {{ chart.season }}</h3>
        <p class="muted performance-card-lead">
          Πληρότητα από τις κρατήσεις που υπάρχουν σήμερα ({{ chart.asOf }}), δίπλα σε όσες υπήρχαν την ίδια μέρα πέρυσι.
        </p>
        <p class="performance-pickup" data-testid="performance-pickup">
          Τις τελευταίες 7 ημέρες: <strong>{{ chart.pickupBookings }}</strong> · <strong>{{ chart.pickupNights }}</strong>
        </p>
        <app-combo-chart
          *ngIf="!inventoryMissing()"
          label="Πληρότητα ήδη κλεισμένη ανά μήνα, σε σύγκριση με πέρυσι την ίδια μέρα"
          [categories]="chart.categories"
          [categoryTitles]="chart.titles"
          [bars]="chart.bars"
        ></app-combo-chart>
      </section>
    </div>
  `,
})
export class PerformanceOccupancyComponent {
  readonly ownedPropertyId = input.required<string>();
  /** Bumped by the page when the bookings change: read again. */
  readonly version = input(0);
  readonly filters = input.required<PerformanceFilters>();
  readonly status = input.required<PerformanceStatus>();
  readonly openRooms = output<void>();

  private readonly api = inject(PerformanceApiService);
  private readonly query = computed(() => ({ id: this.ownedPropertyId(), filters: this.filters(), version: this.version() }));
  private readonly paceQuery = computed(() => ({ id: this.ownedPropertyId(), season: paceSeason(this.status()), version: this.version() }));

  readonly monthly = new PerformanceLoader(this.query, ({ id, filters }) => this.api.monthly(id, filters));
  readonly pace = new PerformanceLoader(this.paceQuery, ({ id, season }) => this.api.pace(id, season));

  readonly loading = computed(() => this.monthly.loading() || this.pace.loading());
  readonly error = computed(() => this.monthly.error() || this.pace.error());
  readonly inventoryMissing = computed(() => !this.status().inventory_complete);

  private readonly months = computed(() => this.monthly.data()?.months ?? []);

  readonly occupancyChart = computed(() => {
    const months = this.months();
    if (!months.length || this.inventoryMissing()) return null;
    const bars: ChartSeries[] = [{ label: "Πληρότητα", values: months.map((month) => month.occupancy_pct), color: "sea", format: "pct" }];
    if (months.some((month) => month.last_year?.occupancy_pct != null)) {
      bars.push({ label: "Πέρυσι", values: months.map((month) => month.last_year?.occupancy_pct ?? null), color: "terracotta", format: "pct" });
    }
    return { ...monthAxis(months.map((month) => month.month)), bars, lines: [] as ChartSeries[], notes: months.map(closedNote) };
  });

  readonly adrRevparChart = computed(() => {
    const months = this.months();
    if (!months.length) return null;
    const lines: ChartSeries[] = [
      { label: "ADR", values: months.map((month) => month.adr_eur), color: "sea", format: "euro" },
    ];
    if (!this.inventoryMissing()) {
      lines.push({ label: "RevPAR", values: months.map((month) => month.revpar_eur), color: "terracotta", format: "euro" });
    }
    if (months.some((month) => month.market_median_eur !== null)) {
      lines.push({ label: "Διάμεσος αγοράς", values: months.map((month) => month.market_median_eur), color: "sun", format: "euro", dashed: true });
    }
    return { ...monthAxis(months.map((month) => month.month)), lines, notes: months.map(closedNote) };
  });

  readonly nightsChart = computed(() => {
    const months = this.months();
    if (!months.length) return null;
    const series: ChartSeries[] = [
      { label: "Πουλημένες", values: months.map((month) => month.room_nights_sold), color: "sea", format: "count" },
    ];
    if (!this.inventoryMissing()) {
      series.push({ label: "Απούλητες", values: months.map((month) => month.room_nights_unsold), color: "sea-tint", format: "count" });
    }
    return { ...monthAxis(months.map((month) => month.month)), series, notes: months.map(closedNote) };
  });

  readonly paceChart = computed(() => {
    const pace = this.pace.data();
    if (!pace?.months.length) return null;
    const { bookings, room_nights: nights } = pace.pickup_7d;
    return {
      season: pace.season,
      asOf: formatGreekDate(pace.as_of),
      pickupBookings: `${formatCount(bookings)} ${bookings === 1 ? "κράτηση" : "κρατήσεις"}`,
      pickupNights: `${formatCount(nights)} ${nights === 1 ? "νύχτα" : "νύχτες"}`,
      ...monthAxis(pace.months.map((month) => month.month)),
      bars: [
        { label: "Κλεισμένα σήμερα", values: pace.months.map((month) => month.otb_occupancy_pct), color: "sea", format: "pct" },
        { label: "Πέρυσι την ίδια μέρα", values: pace.months.map((month) => month.last_year_same_day_pct), color: "terracotta", format: "pct" },
      ] as ChartSeries[],
    };
  });
}
