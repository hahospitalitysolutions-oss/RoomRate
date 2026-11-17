/**
 * Stacked bars over shared categories: e.g. revenue per channel per month,
 * or sold vs unsold nights. Every series shares one unit; the tooltip lists
 * each part and the total.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input } from "@angular/core";

import { ChartBase } from "./chart-base";
import { ChartLegendComponent } from "./chart-legend.component";
import { ChartTooltipComponent } from "./chart-tooltip.component";
import type { ChartSeries, ChartTooltip, LegendItem } from "./chart-types";
import { formatAxis, formatValue, linear, niceTicks } from "./chart-utils";

const PAD_TOP = 14;
const PAD_BOTTOM = 30;
const AXIS_WIDTH = 56;

@Component({
  selector: "app-stacked-bar-chart",
  standalone: true,
  imports: [CommonModule, ChartLegendComponent, ChartTooltipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="chart-frame" *ngIf="view() as chart">
      <svg class="chart-svg" [attr.width]="chart.width" [attr.height]="chart.height" role="group" [attr.aria-label]="chart.ariaLabel">
        <g *ngFor="let tick of chart.ticks">
          <line class="chart-grid" [attr.x1]="chart.left" [attr.x2]="chart.right" [attr.y1]="tick.y" [attr.y2]="tick.y" />
          <text class="chart-tick" [attr.x]="chart.left - 6" [attr.y]="tick.y + 4" text-anchor="end">{{ tick.label }}</text>
        </g>
        <ng-container *ngFor="let column of chart.columns">
          <rect
            *ngFor="let part of column.parts"
            class="chart-bar" [ngClass]="'chart-fill-' + part.color"
            [class.is-dim]="active() !== null && active() !== column.index"
            [attr.x]="column.x - chart.barWidth / 2"
            [attr.y]="part.y"
            [attr.width]="chart.barWidth"
            [attr.height]="part.height"
          />
          <text *ngIf="column.note" class="chart-note" [attr.x]="column.x" [attr.y]="chart.bottom - 8" text-anchor="middle">{{ column.note }}</text>
          <text *ngIf="column.showLabel" class="chart-tick" [attr.x]="column.x" [attr.y]="chart.bottom + 18" text-anchor="middle">{{ column.label }}</text>
        </ng-container>
        <rect
          *ngFor="let column of chart.columns"
          class="chart-hit"
          [attr.x]="column.x - chart.band / 2"
          [attr.y]="chart.top"
          [attr.width]="chart.band"
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
export class StackedBarChartComponent extends ChartBase {
  readonly label = input.required<string>();
  readonly categories = input.required<string[]>();
  readonly categoryTitles = input<string[] | null>(null);
  /** Bottom to top. */
  readonly series = input.required<ChartSeries[]>();
  readonly notes = input<(string | null)[]>([]);
  /** Label of the sum in the tooltip; "" hides it. */
  readonly totalLabel = input("Σύνολο");
  readonly height = input(260);

  readonly legend = computed<LegendItem[]>(() =>
    this.series().map((series) => ({ label: series.label, color: series.color, mark: "bar" as const })));

  readonly view = computed(() => {
    const categories = this.categories();
    const series = this.series();
    if (!categories.length || !series.length) return null;
    const titles = this.categoryTitles() ?? categories;
    const notes = this.notes();
    const format = series[0].format;
    const width = this.width();
    const height = this.height();
    const left = AXIS_WIDTH;
    const right = width - 12;
    const top = PAD_TOP;
    const bottom = height - PAD_BOTTOM;
    const totals = categories.map((_, index) => series.reduce((sum, item) => sum + Math.max(0, item.values[index] ?? 0), 0));
    const ticks = niceTicks(0, Math.max(0, ...totals), this.compact() ? 3 : 4);
    const scale = linear(0, ticks[ticks.length - 1], bottom, top);
    const band = (right - left) / categories.length;
    const barWidth = Math.max(4, Math.min(band * 0.68, 40));
    const labelStep = Math.ceil(categories.length / Math.max(1, Math.floor((right - left) / 46)));

    const columns = categories.map((label, index) => {
      const x = left + band * (index + 0.5);
      const note = notes[index] ?? null;
      let base = 0;
      const parts = note ? [] : series.flatMap((item) => {
        const value = item.values[index];
        if (value == null || value <= 0) return [];
        const y0 = scale(base);
        base += value;
        const y1 = scale(base);
        return [{ color: item.color, y: y1, height: Math.max(y0 - y1, 1) }];
      });
      const readings = series.map((item) => `${item.label} ${formatValue(item.values[index], format)}`);
      return {
        index,
        x,
        label,
        note,
        parts,
        total: totals[index],
        topY: scale(totals[index]),
        showLabel: (categories.length - 1 - index) % labelStep === 0,
        ariaLabel: `${titles[index]}: ${note ?? `${readings.join(", ")}${this.totalLabel() ? `, ${this.totalLabel()} ${formatValue(totals[index], format)}` : ""}`}`,
      };
    });

    return {
      width,
      height,
      left,
      right,
      top,
      bottom,
      band,
      barWidth,
      columns,
      titles,
      format,
      ticks: ticks.map((value) => ({ y: scale(value), label: formatAxis(value, format) })),
      ariaLabel: `${this.label()}. ${columns.map((column) => column.ariaLabel).join("; ")}.`,
    };
  });

  readonly tooltip = computed<ChartTooltip | null>(() => {
    const chart = this.view();
    const index = this.active();
    if (!chart || index == null || !chart.columns[index]) return null;
    const column = chart.columns[index];
    const rows = column.note
      ? [{ label: "Κατάσταση", value: column.note }]
      : [
          // Top of the stack first, as it reads on screen.
          ...[...this.series()].reverse().map((item) => ({
            label: item.label,
            value: formatValue(item.values[index], chart.format),
            color: item.color,
          })),
          ...(this.totalLabel() ? [{ label: this.totalLabel(), value: formatValue(column.total, chart.format) }] : []),
        ];
    return { title: chart.titles[index], rows, x: column.x, y: Math.min(column.topY, chart.bottom) };
  });
}
