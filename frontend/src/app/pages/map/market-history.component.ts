/**
 * «Η τιμή σας και η αγορά»: the owner's average price (ADR, from their own
 * bookings) against the market's median for the same stays, by month and by
 * room type (a table: room names are too long for a chart axis on a phone). It sits under «Αγορά vs εμείς»: the dashboard answers «where am
 * I today», this answers «where have I been all season». The «Απόδοση» page
 * keeps the owner's own figures only; every market comparison lives here.
 *
 * It reads the «Απόδοση» calls for the season «Απόδοση» opens on (whole
 * property, gross amounts). Without uploaded bookings it says so in one line
 * and links to the upload.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, OnInit, signal } from "@angular/core";
import { RouterLink } from "@angular/router";

import { ComboChartComponent } from "../../components/charts/combo-chart.component";
import type { ChartSeries } from "../../components/charts/chart-types";
import { formatEuroCents } from "../../components/charts/chart-utils";
import { OnboardingService } from "../../services/onboarding.service";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceFilters, PerformanceStatus } from "../../types/performance";
import { compareLabel } from "./market-comparison";
import { defaultSeason, seasonRange } from "../performance/performance-filter-state";
import { monthAxis, PerformanceLoader } from "../performance/performance-loader";

@Component({
  selector: "app-market-history",
  standalone: true,
  imports: [CommonModule, RouterLink, ComboChartComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *ngIf="status() as current">
      <p *ngIf="!current.has_data" class="muted pricing-hint" data-testid="market-history-empty">
        Ανεβάστε τις κρατήσεις σας στην <a routerLink="/performance">Απόδοση</a>
        για να δείτε τη μέση τιμή σας δίπλα στην αγορά, μήνα με μήνα.
      </p>

      <ng-container *ngIf="current.has_data">
        <p *ngIf="error()" class="alert alert-error" role="alert" data-testid="market-history-error">{{ error() }}</p>

        <figure *ngIf="monthChart() as chart" class="panel-figure" data-testid="market-history-months">
          <figcaption class="figure-title">
            <strong>Η μέση τιμή σας και η αγορά, ανά μήνα</strong>
            <span class="muted">{{ chart.season }}</span>
          </figcaption>
          <p *ngIf="chart.gap" class="versus-gap">{{ chart.gap }}</p>
          <app-combo-chart
            label="Η μέση τιμή σας και η διάμεση τιμή της αγοράς ανά μήνα"
            [categories]="chart.categories"
            [categoryTitles]="chart.titles"
            [lines]="chart.lines"
          ></app-combo-chart>
        </figure>

        <figure *ngIf="roomRows().length" class="panel-figure" data-testid="market-history-rooms">
          <figcaption class="figure-title">
            <strong>Ανά τύπο δωματίου</strong>
          </figcaption>
          <div class="performance-table-scroll">
            <table class="performance-table is-compact">
              <thead>
                <tr>
                  <th scope="col">Τύπος</th>
                  <th scope="col">Εσείς (ADR)</th>
                  <th scope="col">Διάμεση αγοράς</th>
                  <th scope="col">Διαφορά</th>
                </tr>
              </thead>
              <tbody>
                <tr *ngFor="let row of roomRows()">
                  <th scope="row">{{ row.label }}</th>
                  <td>{{ row.own }}</td>
                  <td>{{ row.market }}</td>
                  <td>{{ row.gap }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </figure>
      </ng-container>
    </ng-container>
  `,
})
export class MarketHistoryComponent implements OnInit {
  private readonly onboarding = inject(OnboardingService);
  private readonly api = inject(PerformanceApiService);

  readonly status = signal<PerformanceStatus | null>(null);
  private readonly ownedPropertyId = signal("");

  private readonly query = computed(() => {
    const status = this.status();
    if (!status?.has_data || !this.ownedPropertyId()) return null;
    const { from, to } = seasonRange(defaultSeason(status));
    const filters: PerformanceFilters = { date_from: from, date_to: to, room_type_id: null, amounts: "gross", compare: "none" };
    return { id: this.ownedPropertyId(), filters };
  });

  readonly monthly = new PerformanceLoader(this.query, ({ id, filters }) => this.api.monthly(id, filters));
  readonly roomTypes = new PerformanceLoader(this.query, ({ id, filters }) => this.api.roomTypes(id, filters));
  readonly error = computed(() => this.monthly.error() || this.roomTypes.error());

  readonly monthChart = computed(() => {
    const months = (this.monthly.data()?.months ?? []).filter((month) => month.adr_eur !== null || month.market_median_eur !== null);
    if (!months.some((month) => month.market_median_eur !== null)) return null;
    const lines: ChartSeries[] = [
      { label: "Εσείς (ADR)", values: months.map((month) => month.adr_eur), color: "terracotta", format: "euro" },
      { label: "Διάμεση αγοράς", values: months.map((month) => month.market_median_eur), color: "sea", format: "euro", dashed: true },
    ];
    return {
      season: months[0].month.slice(0, 4),
      gap: seasonGap(months),
      ...monthAxis(months.map((month) => month.month)),
      lines,
    };
  });

  readonly roomRows = computed(() =>
    (this.roomTypes.data()?.room_types ?? [])
      .filter((type) => type.market_median_eur !== null)
      .map((type) => ({
        label: type.label,
        own: formatEuroCents(type.adr_eur),
        market: formatEuroCents(type.market_median_eur),
        gap: compareLabel(type.adr_eur, type.market_median_eur) || "—",
      })));

  async ngOnInit(): Promise<void> {
    try {
      const me = await this.onboarding.currentUser();
      if (!me.owned_property_id) return;
      this.ownedPropertyId.set(me.owned_property_id);
      this.status.set(await this.api.status(me.owned_property_id));
    } catch {
      // Secondary to the dashboard: without «Απόδοση» data the section just stays hidden.
    }
  }
}

/** «Στη σεζόν είστε κατά μέσο όρο 12,00 € πάνω από την αγορά», over the months that have both figures. */
function seasonGap(months: { adr_eur: number | null; market_median_eur: number | null }[]): string | null {
  const paired = months.filter((month) => month.adr_eur !== null && month.market_median_eur !== null);
  if (!paired.length) return null;
  const diff = paired.reduce((sum, month) => sum + (month.adr_eur! - month.market_median_eur!), 0) / paired.length;
  if (Math.abs(diff) < 1) return "Στη σεζόν είστε κατά μέσο όρο στο ίδιο επίπεδο με την αγορά.";
  return `Στη σεζόν είστε κατά μέσο όρο ${formatEuroCents(Math.abs(diff))} ${diff > 0 ? "πάνω" : "κάτω"} από την αγορά.`;
}
