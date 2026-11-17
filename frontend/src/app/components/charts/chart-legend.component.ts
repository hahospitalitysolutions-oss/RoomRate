import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, input } from "@angular/core";

import type { LegendItem } from "./chart-types";

/** The key under a chart: one swatch (bar, line, dashed line, band or dot) per series. */
@Component({
  selector: "app-chart-legend",
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="chart-legend" *ngIf="items().length">
      <li *ngFor="let item of items()">
        <svg class="chart-legend-mark" viewBox="0 0 18 10" aria-hidden="true">
          <rect *ngIf="item.mark === 'bar'" [ngClass]="'chart-fill-' + item.color" x="2" y="1" width="14" height="8" rx="2" />
          <rect *ngIf="item.mark === 'band'" class="chart-band" [ngClass]="'chart-fill-' + item.color" x="1" y="1" width="16" height="8" rx="2" />
          <line
            *ngIf="item.mark === 'line' || item.mark === 'dashed'"
            class="chart-line" [ngClass]="'chart-stroke-' + item.color"
            [class.is-dashed]="item.mark === 'dashed'"
            x1="1" y1="5" x2="17" y2="5"
          />
          <circle *ngIf="item.mark === 'dot'" [ngClass]="'chart-fill-' + item.color" cx="9" cy="5" r="4" />
        </svg>
        {{ item.label }}
      </li>
    </ul>
  `,
})
export class ChartLegendComponent {
  readonly items = input.required<LegendItem[]>();
}
