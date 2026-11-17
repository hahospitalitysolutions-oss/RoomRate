/**
 * «Προβλέψεις» (A8): actuals vs forecast with its likely range (/forecast),
 * for revenue, bookings or room nights, and the plain-language accuracy.
 * The model is the backend's choice: never offered to the owner, only named
 * in the «Λεπτομέρειες» hint. Under 12 months of history the tab explains.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from "@angular/core";

import { ForecastChartComponent } from "../../components/charts/forecast-chart.component";
import type { ValueFormat } from "../../components/charts/chart-types";
import { formatPct } from "../../components/charts/chart-utils";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { ForecastTarget } from "../../types/performance";
import { monthAxis, PerformanceLoader } from "./performance-loader";

const TARGETS: { id: ForecastTarget; label: string; format: ValueFormat }[] = [
  { id: "revenue", label: "Έσοδα", format: "euro" },
  { id: "bookings", label: "Κρατήσεις", format: "count" },
  { id: "room_nights", label: "Νύχτες", format: "count" },
];

@Component({
  selector: "app-performance-forecast",
  standalone: true,
  imports: [CommonModule, ForecastChartComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="forecast.error()" class="alert alert-error" role="alert" data-testid="performance-forecast-error">{{ forecast.error() }}</p>
    <p *ngIf="forecast.loading() && !forecast.data()" class="muted">Φόρτωση…</p>

    <section *ngIf="forecast.data() as data" class="performance-card" [class.is-refreshing]="forecast.loading()" data-testid="performance-forecast">
      <div class="performance-card-head">
        <h3>Πραγματικά και πρόβλεψη</h3>
        <div class="performance-segmented" role="group" aria-label="Τι προβλέπεται">
          <button
            *ngFor="let option of targets"
            type="button"
            [attr.aria-pressed]="target() === option.id"
            [attr.data-testid]="'performance-forecast-target-' + option.id"
            (click)="target.set(option.id)"
          >{{ option.label }}</button>
        </div>
      </div>

      <ng-container *ngIf="data.status === 'insufficient_history'; else ready">
        <div class="performance-insufficient" data-testid="performance-forecast-insufficient">
          <strong>Χρειάζονται {{ data.history_months_needed }} μήνες ιστορικού για πρόβλεψη</strong>
          <p class="muted">
            Υπάρχουν κρατήσεις για {{ data.history_months }} {{ data.history_months === 1 ? "μήνα" : "μήνες" }}.
            Ανεβάστε και παλαιότερες κρατήσεις, ή η πρόβλεψη θα εμφανιστεί μόλις συμπληρωθεί το ιστορικό.
          </p>
        </div>
      </ng-container>

      <ng-template #ready>
        <p *ngIf="data.accuracy_sentence" class="performance-card-lead" data-testid="performance-forecast-accuracy">
          {{ data.accuracy_sentence }}
          <span
            *ngIf="data.accuracy as accuracy"
            class="performance-advanced"
            tabindex="0"
            [attr.title]="advanced(accuracy)"
            [attr.aria-label]="'Λεπτομέρειες: ' + advanced(accuracy)"
          >Λεπτομέρειες</span>
        </p>
        <app-forecast-chart
          *ngIf="chart() as view"
          [label]="'Πρόβλεψη: ' + targetLabel()"
          [categories]="view.categories"
          [categoryTitles]="view.titles"
          [actual]="view.actual"
          [forecast]="view.forecast"
          [low]="view.low"
          [high]="view.high"
          [format]="view.format"
        ></app-forecast-chart>
      </ng-template>
    </section>
  `,
})
export class PerformanceForecastComponent {
  readonly ownedPropertyId = input.required<string>();
  /** Bumped by the page when the bookings change: read again. */
  readonly version = input(0);

  readonly targets = TARGETS;
  readonly target = signal<ForecastTarget>("revenue");

  private readonly api = inject(PerformanceApiService);
  private readonly query = computed(() => ({ id: this.ownedPropertyId(), target: this.target(), version: this.version() }));

  readonly forecast = new PerformanceLoader(this.query, ({ id, target }) => this.api.forecast(id, target));

  readonly targetLabel = computed(() => TARGETS.find((item) => item.id === this.target())?.label ?? "");

  readonly chart = computed(() => {
    const data = this.forecast.data();
    if (!data || data.status !== "ready" || !data.points.length) return null;
    const points = data.points;
    return {
      ...monthAxis(points.map((point) => point.month)),
      actual: points.map((point) => point.actual),
      forecast: points.map((point) => point.forecast),
      low: points.map((point) => point.low),
      high: points.map((point) => point.high),
      // The answer's own target decides the unit (it may lag the buttons while loading).
      format: TARGETS.find((item) => item.id === data.target)?.format ?? "count",
    };
  });

  advanced(accuracy: { smape_pct: number; model: string }): string {
    return `μοντέλο ${accuracy.model}, sMAPE ${formatPct(accuracy.smape_pct)}`;
  }
}
