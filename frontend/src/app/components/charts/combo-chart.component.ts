/**
 * Bars and lines over shared categories (months): bars on the left axis,
 * lines on the right one when their unit differs (e.g. bookings vs ADR), or
 * on the left when there are no bars. A category can carry a note instead of
 * values («Κλειστό»), and a null value is a gap, never a zero.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input } from "@angular/core";

import { ChartBase } from "./chart-base";
import { ChartLegendComponent } from "./chart-legend.component";
import { ChartTooltipComponent } from "./chart-tooltip.component";
import type { ChartSeries, ChartTooltip, LegendItem } from "./chart-types";
import { formatAxis, formatValue, linear, lineSegments, niceTicks, present } from "./chart-utils";

const PAD_TOP = 14;
const PAD_BOTTOM = 30;
const AXIS_WIDTH = 52;

@Component({
  selector: "app-combo-chart",
  standalone: true,
  imports: [CommonModule, ChartLegendComponent, ChartTooltipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="chart-frame" *ngIf="view() as chart">
      <svg class="chart-svg" [attr.width]="chart.width" [attr.height]="chart.height" role="group" [attr.aria-label]="chart.ariaLabel">
        <g *ngFor="let tick of chart.leftTicks">
          <line class="chart-grid" [attr.x1]="chart.left" [attr.x2]="chart.right" [attr.y1]="tick.y" [attr.y2]="tick.y" />
          <text class="chart-tick" [attr.x]="chart.left - 6" [attr.y]="tick.y + 4" text-anchor="end">{{ tick.label }}</text>
        </g>
        <text
          *ngFor="let tick of chart.rightTicks"
          class="chart-tick"
          [attr.x]="chart.right + 6"
          [attr.y]="tick.y + 4"
        >{{ tick.label }}</text>

        <ng-container *ngFor="let column of chart.columns">
          <rect
            *ngFor="let bar of column.bars"
            class="chart-bar" [ngClass]="'chart-fill-' + bar.color"
            [class.is-dim]="active() !== null && active() !== column.index"
            [attr.x]="bar.x"
            [attr.y]="bar.y"
            [attr.width]="bar.width"
            [attr.height]="bar.height"
            rx="2"
          />
          <text *ngIf="column.note" class="chart-note" [attr.x]="column.x" [attr.y]="chart.bottom - 8" text-anchor="middle">{{ column.note }}</text>
          <text *ngIf="column.showLabel" class="chart-tick" [attr.x]="column.x" [attr.y]="chart.bottom + 18" text-anchor="middle">{{ column.label }}</text>
        </ng-container>

        <ng-container *ngFor="let line of chart.lines">
          <polyline
            *ngFor="let segment of line.segments"
            class="chart-line" [ngClass]="'chart-stroke-' + line.color"
            [class.is-dashed]="line.dashed"
            [attr.points]="segment"
          />
          <ng-container *ngFor="let point of line.points">
            <circle
              *ngIf="point"
              class="chart-dot" [ngClass]="'chart-fill-' + line.color"
              [attr.cx]="point.x"
              [attr.cy]="point.y"
              [attr.r]="active() === point.index ? 5 : 3"
            />
          </ng-container>
        </ng-container>

        <rect
          *ngFor="let column of chart.columns"
          class="chart-hit"
          [attr.x]="column.hitX"
          [attr.y]="chart.top"
          [attr.width]="column.hitWidth"
          [attr.height]="chart.bottom - chart.top"
          tabindex="0"
          role="img"
          [attr.aria-label]="column.ariaLabel"
          (pointerenter)="enter($event, column.index)"
          (pointerleave)="leave($event, column.index)"
          (click)="tap($event, column.index)"
          (focus)="show(column.index)"
          (blur)="hide(column.index)"
        />
      </svg>
      <app-chart-tooltip [tooltip]="tooltip()" [width]="chart.width"></app-chart-tooltip>
    </div>
    <app-chart-legend [items]="legend()"></app-chart-legend>
  `,
})
export class ComboChartComponent extends ChartBase {
  /** What the chart shows, for screen readers: «Κρατήσεις και ADR ανά μήνα». */
  readonly label = input.required<string>();
  /** Short axis labels, one per category («Μάι»). */
  readonly categories = input.required<string[]>();
  /** Long labels for the tooltip («Μάιος 2026»); defaults to `categories`. */
  readonly categoryTitles = input<string[] | null>(null);
  readonly bars = input<ChartSeries[]>([]);
  readonly lines = input<ChartSeries[]>([]);
  /** Shown in place of a category's values, e.g. «Κλειστό». */
  readonly notes = input<(string | null)[]>([]);
  readonly height = input(260);

  readonly legend = computed<LegendItem[]>(() => [
    ...this.bars().map((series) => ({ label: series.label, color: series.color, mark: "bar" as const })),
    ...this.lines().map((series) => ({ label: series.label, color: series.color, mark: series.dashed ? "dashed" as const : "line" as const })),
  ]);

  readonly view = computed(() => {
    const categories = this.categories();
    if (!categories.length) return null;
    const bars = this.bars();
    const lines = this.lines();
    const titles = this.categoryTitles() ?? categories;
    const notes = this.notes();
    const width = this.width();
    const height = this.height();
    // Lines get their own axis only next to bars of another unit.
    const dualAxis = bars.length > 0 && lines.length > 0 && lines[0].format !== bars[0].format;
    const left = AXIS_WIDTH;
    const right = width - (dualAxis ? AXIS_WIDTH : 12);
    const top = PAD_TOP;
    const bottom = height - PAD_BOTTOM;

    const leftSeries = dualAxis ? bars : [...bars, ...lines];
    const leftValues = present(leftSeries.flatMap((series) => series.values));
    const leftTicks = niceTicks(Math.min(0, ...leftValues), Math.max(0, ...leftValues), this.compact() ? 3 : 4);
    const leftScale = linear(leftTicks[0], leftTicks[leftTicks.length - 1], bottom, top);
    const rightValues = present(lines.flatMap((series) => series.values));
    const rightTicks = dualAxis ? niceTicks(Math.min(0, ...rightValues), Math.max(0, ...rightValues), this.compact() ? 3 : 4) : [];
    const rightScale = dualAxis ? linear(rightTicks[0], rightTicks[rightTicks.length - 1], bottom, top) : leftScale;
    const leftFormat = (leftSeries[0] ?? lines[0]).format;

    const band = (right - left) / categories.length;
    const groupWidth = Math.min(band * 0.72, 30 * Math.max(bars.length, 1) + 8);
    const barWidth = bars.length ? groupWidth / bars.length : 0;
    const labelStep = Math.ceil(categories.length / Math.max(1, Math.floor((right - left) / 46)));
    const zeroY = leftScale(0);

    const columns = categories.map((label, index) => {
      const x = left + band * (index + 0.5);
      const note = notes[index] ?? null;
      const readings = [...bars, ...lines].map((series) => `${series.label} ${formatValue(series.values[index], series.format)}`);
      return {
        index,
        x,
        label,
        note,
        showLabel: (categories.length - 1 - index) % labelStep === 0,
        hitX: x - band / 2,
        hitWidth: band,
        ariaLabel: `${titles[index]}: ${note ? note : readings.join(", ")}`,
        bars: note ? [] : bars.flatMap((series, barIndex) => {
          const value = series.values[index];
          if (value == null) return [];
          const y = leftScale(value);
          return [{
            color: series.color,
            x: x - groupWidth / 2 + barIndex * barWidth + 1,
            y: Math.min(y, zeroY),
            width: Math.max(barWidth - 2, 1),
            height: Math.max(Math.abs(zeroY - y), value === 0 ? 0 : 1),
          }];
        }),
      };
    });

    const drawnLines = lines.map((series) => {
      const scale = dualAxis ? rightScale : leftScale;
      const points = series.values.map((value, index) =>
        value == null || notes[index] ? null : { index, x: columns[index].x, y: scale(value) });
      return { color: series.color, dashed: !!series.dashed, points, segments: lineSegments(points) };
    });

    const summary = columns.map((column) => column.ariaLabel).join("; ");
    return {
      width,
      height,
      left,
      right,
      top,
      bottom,
      columns,
      lines: drawnLines,
      leftTicks: leftTicks.map((value) => ({ y: leftScale(value), label: formatAxis(value, leftFormat) })),
      rightTicks: rightTicks.map((value) => ({ y: rightScale(value), label: formatAxis(value, lines[0].format) })),
      ariaLabel: `${this.label()}. ${summary}.`,
      titles,
    };
  });

  readonly tooltip = computed<ChartTooltip | null>(() => {
    const chart = this.view();
    const index = this.active();
    if (!chart || index == null || !chart.columns[index]) return null;
    const column = chart.columns[index];
    const series = [...this.bars(), ...this.lines()];
    const ys = [
      ...column.bars.map((bar) => bar.y),
      ...chart.lines.map((line) => line.points[index]?.y ?? chart.bottom),
    ];
    return {
      title: chart.titles[index],
      rows: column.note
        ? [{ label: "Κατάσταση", value: column.note }]
        : series.map((item) => ({ label: item.label, value: formatValue(item.values[index], item.format), color: item.color })),
      x: column.x,
      y: Math.min(chart.bottom, ...ys),
    };
  });
}
