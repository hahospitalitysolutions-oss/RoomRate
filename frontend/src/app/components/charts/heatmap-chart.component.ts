/**
 * A grid of counts (e.g. bookings by length of stay × lead time) as a real
 * table: every cell prints its number, its shade (sea, by share of the
 * largest cell) only makes the pattern visible at a glance.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input } from "@angular/core";

import type { ValueFormat } from "./chart-types";
import { formatValue } from "./chart-utils";

@Component({
  selector: "app-heatmap-chart",
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="heatmap-scroll">
      <table class="heatmap-chart" [attr.aria-label]="label()">
        <thead>
          <tr>
            <th scope="col" class="heatmap-corner">
              {{ rowTitle() }}<span *ngIf="rowTitle() && columnTitle()"> / </span>{{ columnTitle() }}
            </th>
            <th *ngFor="let column of columns()" scope="col">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr *ngFor="let row of grid(); index as r">
            <th scope="row">{{ rows()[r] }}</th>
            <td
              *ngFor="let cell of row; index as c"
              [class.is-strong]="cell.strength > 0.55"
              [style.--heat]="cell.strength"
              [attr.title]="rows()[r] + ' · ' + columns()[c] + ': ' + cell.label"
            >{{ cell.label }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="heatmap-scale" aria-hidden="true">
      <span>Λιγότερες</span><i class="heatmap-scale-bar"></i><span>Περισσότερες</span>
    </p>
  `,
})
export class HeatmapChartComponent {
  readonly label = input.required<string>();
  readonly rows = input.required<string[]>();
  readonly columns = input.required<string[]>();
  /** values[row][column]. */
  readonly values = input.required<number[][]>();
  readonly rowTitle = input("");
  readonly columnTitle = input("");
  readonly format = input<ValueFormat>("count");

  readonly grid = computed(() => {
    const values = this.values();
    const max = Math.max(0, ...values.flat());
    return values.map((row) => row.map((value) => ({
      label: formatValue(value, this.format()),
      // A zero cell stays blank; any other keeps a visible tint.
      strength: max > 0 && value > 0 ? Math.round((0.12 + 0.88 * (value / max)) * 100) / 100 : 0,
    })));
  });
}
