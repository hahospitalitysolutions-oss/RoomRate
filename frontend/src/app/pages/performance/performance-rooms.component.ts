/**
 * «Δωμάτια» (A6): every room type side by side (/room-types): a table, revenue
 * and bookings per type, ADR vs bookings sized by revenue, and the revenue
 * share. Picking a type (row, bar or bubble) sets the page's room filter.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input, output } from "@angular/core";

import { BubbleChartComponent } from "../../components/charts/bubble-chart.component";
import { HorizontalBarChartComponent } from "../../components/charts/horizontal-bar-chart.component";
import { ShareChartComponent } from "../../components/charts/share-chart.component";
import { CATEGORY_COLORS, type BubblePoint, type ChartItem } from "../../components/charts/chart-types";
import { formatCount, formatEuroCents, formatPct } from "../../components/charts/chart-utils";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceFilters } from "../../types/performance";
import { PerformanceLoader } from "./performance-loader";

@Component({
  selector: "app-performance-rooms",
  standalone: true,
  imports: [CommonModule, HorizontalBarChartComponent, BubbleChartComponent, ShareChartComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="roomTypes.error()" class="alert alert-error" role="alert" data-testid="performance-rooms-error">{{ roomTypes.error() }}</p>
    <p *ngIf="roomTypes.loading() && !roomTypes.data()" class="muted">Φόρτωση…</p>

    <div class="performance-tab-body" [class.is-refreshing]="roomTypes.loading()" *ngIf="rows().length">
      <section class="performance-card" data-testid="performance-room-table">
        <h3>Ανά τύπο δωματίου</h3>
        <p class="muted performance-card-lead">Πατήστε έναν τύπο για να δείτε μόνο αυτόν σε όλες τις ενότητες.</p>
        <div class="performance-table-scroll">
          <table class="performance-table">
            <thead>
              <tr>
                <th scope="col">Τύπος</th>
                <th scope="col">Δωμάτια</th>
                <th scope="col">Πληρότητα</th>
                <th scope="col">ADR</th>
                <th scope="col">RevPAR</th>
                <th scope="col">Έσοδα</th>
                <th scope="col">Μερίδιο</th>
                <th scope="col">Κρατήσεις</th>
                <th scope="col">Νύχτες</th>
                <th scope="col">Ακυρώσεις</th>
                <th scope="col">Αγορά</th>
              </tr>
            </thead>
            <tbody>
              <tr *ngFor="let row of rows()" [class.is-selected]="row.id === selectedRoom()" [attr.data-testid]="'performance-room-row-' + row.id">
                <th scope="row">
                  <button type="button" class="text-button" [attr.aria-pressed]="row.id === selectedRoom()" (click)="pick(row.id)">
                    {{ row.label }}
                  </button>
                </th>
                <td>
                  <ng-container *ngIf="row.rooms !== null; else noRooms">{{ row.rooms }}</ng-container>
                  <ng-template #noRooms>
                    <button type="button" class="text-button performance-inline-missing" (click)="openRooms.emit()">Ορισμός</button>
                  </ng-template>
                </td>
                <td>{{ row.occupancy }}</td>
                <td>{{ row.adr }}</td>
                <td>{{ row.revpar }}</td>
                <td>{{ row.revenue }}</td>
                <td>{{ row.share }}</td>
                <td>{{ row.bookings }}</td>
                <td>{{ row.nights }}</td>
                <td>{{ row.cancellations }}</td>
                <td>{{ row.market }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="performance-grid-2">
        <section class="performance-card" data-testid="performance-chart-room-revenue">
          <h3>Έσοδα ανά τύπο</h3>
          <app-horizontal-bar-chart
            label="Έσοδα ανά τύπο δωματίου"
            format="euro"
            [items]="revenueItems()"
            [selectable]="true"
            [selectedId]="selectedRoom() || null"
            (picked)="pick($event)"
          ></app-horizontal-bar-chart>
        </section>
        <section class="performance-card" data-testid="performance-chart-room-bookings">
          <h3>Κρατήσεις ανά τύπο</h3>
          <app-horizontal-bar-chart
            label="Κρατήσεις ανά τύπο δωματίου"
            format="count"
            [items]="bookingItems()"
            [selectable]="true"
            [selectedId]="selectedRoom() || null"
            (picked)="pick($event)"
          ></app-horizontal-bar-chart>
        </section>
      </div>

      <div class="performance-grid-2">
        <section class="performance-card" data-testid="performance-chart-room-bubble">
          <h3>Μέση τιμή και κρατήσεις</h3>
          <p class="muted performance-card-lead">Όσο μεγαλύτερος ο κύκλος, τόσο περισσότερα τα έσοδα.</p>
          <app-bubble-chart
            label="Τύποι δωματίων: κρατήσεις, μέση τιμή και έσοδα"
            xLabel="Κρατήσεις"
            yLabel="ADR"
            sizeLabel="Έσοδα"
            xFormat="count"
            yFormat="euro"
            sizeFormat="euro"
            [points]="bubbles()"
            [selectable]="true"
            [selectedId]="selectedRoom() || null"
            (picked)="pick($event)"
          ></app-bubble-chart>
        </section>
        <section class="performance-card" data-testid="performance-chart-room-share">
          <h3>Μερίδιο εσόδων</h3>
          <app-share-chart label="Μερίδιο εσόδων ανά τύπο δωματίου" [items]="shareItems()"></app-share-chart>
        </section>
      </div>
    </div>
  `,
})
export class PerformanceRoomsComponent {
  readonly ownedPropertyId = input.required<string>();
  /** Bumped by the page when the bookings change: read again. */
  readonly version = input(0);
  readonly filters = input.required<PerformanceFilters>();
  /** The page's room filter ("" = whole property). */
  readonly selectedRoom = input("");
  readonly roomSelected = output<string>();
  readonly openRooms = output<void>();

  private readonly api = inject(PerformanceApiService);
  // /room-types ignores room_type_id: picking a type must not reload this tab.
  private readonly query = computed(
    () => ({ id: this.ownedPropertyId(), filters: { ...this.filters(), room_type_id: null }, version: this.version() }),
    { equal: (a, b) => a.id === b.id && a.version === b.version && JSON.stringify(a.filters) === JSON.stringify(b.filters) },
  );

  readonly roomTypes = new PerformanceLoader(this.query, ({ id, filters }) => this.api.roomTypes(id, filters));

  private readonly types = computed(() => this.roomTypes.data()?.room_types ?? []);

  readonly rows = computed(() => this.types().map((type) => ({
    id: type.id,
    label: type.label,
    rooms: type.rooms,
    occupancy: formatPct(type.occupancy_pct),
    adr: formatEuroCents(type.adr_eur),
    revpar: formatEuroCents(type.revpar_eur),
    revenue: formatEuroCents(type.revenue_eur),
    share: formatPct(type.revenue_share_pct),
    bookings: formatCount(type.bookings),
    nights: formatCount(type.room_nights_sold),
    cancellations: formatPct(type.cancellations_pct),
    market: formatEuroCents(type.market_median_eur),
  })));

  readonly revenueItems = computed<ChartItem[]>(() => this.types().map((type, index) => ({
    id: type.id,
    label: type.label,
    value: type.revenue_eur,
    hint: `${formatPct(type.revenue_share_pct)} των εσόδων`,
    color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
  })));

  readonly bookingItems = computed<ChartItem[]>(() => this.types().map((type, index) => ({
    id: type.id,
    label: type.label,
    value: type.bookings,
    hint: `${formatCount(type.room_nights_sold)} νύχτες`,
    color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
  })));

  readonly bubbles = computed<BubblePoint[]>(() => this.types().map((type, index) => ({
    id: type.id,
    label: type.label,
    x: type.bookings,
    y: type.adr_eur,
    size: type.revenue_eur,
    color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
  })));

  readonly shareItems = computed<ChartItem[]>(() => this.types().map((type, index) => ({
    id: type.id,
    label: type.label,
    value: type.revenue_share_pct,
    hint: formatEuroCents(type.revenue_eur),
    color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
  })));

  /** Picking the selected type again goes back to the whole property. */
  pick(id: string): void {
    this.roomSelected.emit(id === this.selectedRoom() ? "" : id);
  }
}
