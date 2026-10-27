import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input } from "@angular/core";

import type { ChartTooltip } from "./chart-types";

/**
 * The readout over a chart: a title and value rows, anchored at a point in
 * px. It flips to stay inside the frame (start/end near the edges, below
 * near the top).
 */
@Component({
  selector: "app-chart-tooltip",
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      *ngIf="tooltip() as tip"
      class="chart-tooltip"
      role="status"
      [ngClass]="'tip-' + align()"
      [class.tip-below]="tip.y < 70"
      [style.left.px]="tip.x"
      [style.top.px]="tip.y"
    >
      <strong>{{ tip.title }}</strong>
      <span *ngFor="let row of tip.rows" class="chart-tooltip-row">
        <i *ngIf="row.color" class="chart-tooltip-swatch" [ngClass]="'chart-bg-' + row.color" aria-hidden="true"></i>
        {{ row.label }}: <b>{{ row.value }}</b>
      </span>
    </div>
  `,
})
export class ChartTooltipComponent {
  readonly tooltip = input<ChartTooltip | null>(null);
  /** The frame width, to keep the tooltip inside it. */
  readonly width = input.required<number>();

  readonly align = computed(() => {
    const x = this.tooltip()?.x ?? 0;
    return x < this.width() * 0.25 ? "start" : x > this.width() * 0.75 ? "end" : "middle";
  });
}
