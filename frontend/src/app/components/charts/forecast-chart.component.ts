/**
 * Actuals as a solid line, the forecast as a dashed one that picks up where
 * the actuals end, and the forecast's low–high range as a band behind it.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input } from "@angular/core";

import { ChartBase } from "./chart-base";
import { ChartLegendComponent } from "./chart-legend.component";
import { ChartTooltipComponent } from "./chart-tooltip.component";
import type { ChartTooltip, LegendItem, TooltipRow, ValueFormat } from "./chart-types";
import { formatAxis, formatValue, linear, lineSegments, niceTicks, present } from "./chart-utils";

const PAD_TOP = 14;
const PAD_BOTTOM = 30;
const AXIS_WIDTH = 56;

@Component({
  selector: "app-forecast-chart",
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
        <polygon *ngFor="let band of chart.bands" class="chart-band chart-fill-sun" [attr.points]="band" />
        <line
          *ngIf="chart.dividerX !== null"
          class="chart-divider"
          [attr.x1]="chart.dividerX"
          [attr.x2]="chart.dividerX"
          [attr.y1]="chart.top"
          [attr.y2]="chart.bottom"
        />
        <polyline *ngFor="let segment of chart.actualSegments" class="chart-line chart-stroke-sea" [attr.points]="segment" />
        <polyline *ngFor="let segment of chart.forecastSegments" class="chart-line chart-stroke-terracotta is-dashed" [attr.points]="segment" />
        <ng-container *ngFor="let column of chart.columns">
          <circle
            *ngIf="column.actualY !== null"
            class="chart-dot chart-fill-sea"
            [attr.cx]="column.x"
            [attr.cy]="column.actualY"
            [attr.r]="active() === column.index ? 5 : 3"
          />
          <circle
            *ngIf="column.forecastY !== null"
            class="chart-dot chart-dot-hollow chart-stroke-terracotta"
            [attr.cx]="column.x"
            [attr.cy]="column.forecastY"
            [attr.r]="active() === column.index ? 5 : 3"
          />
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
    <app-chart-legend [items]="legend"></app-chart-legend>
  `,
})
export class ForecastChartComponent extends ChartBase {
  readonly label = input.required<string>();
  readonly categories = input.required<string[]>();
  readonly categoryTitles = input<string[] | null>(null);
  readonly actual = input.required<(number | null)[]>();
  readonly forecast = input.required<(number | null)[]>();
  readonly low = input<(number | null)[]>([]);
  readonly high = input<(number | null)[]>([]);
  readonly format = input<ValueFormat>("euro");
  readonly height = input(280);

  readonly legend: LegendItem[] = [
    { label: "Πραγματικά", color: "sea", mark: "line" },
    { label: "Πρόβλεψη", color: "terracotta", mark: "dashed" },
    { label: "Πιθανό εύρος", color: "sun", mark: "band" },
  ];

  readonly view = computed(() => {
    const categories = this.categories();
    if (!categories.length) return null;
    const titles = this.categoryTitles() ?? categories;
    const actual = this.actual();
    const forecast = this.forecast();
    const low = this.low();
    const high = this.high();
    const format = this.format();
    const width = this.width();
    const height = this.height();
    const left = AXIS_WIDTH;
    const right = width - 12;
    const top = PAD_TOP;
    const bottom = height - PAD_BOTTOM;
    const values = present([...actual, ...forecast, ...low, ...high]);
    const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values), this.compact() ? 3 : 4);
    const scale = linear(ticks[0], ticks[ticks.length - 1], bottom, top);
    const band = (right - left) / categories.length;
    const xOf = (index: number) => left + band * (index + 0.5);
    const labelStep = Math.ceil(categories.length / Math.max(1, Math.floor((right - left) / 46)));

    const actualPoints = actual.map((value, index) => (value == null ? null : { x: xOf(index), y: scale(value) }));
    // The forecast line starts from the last actual, so the two read as one story.
    const forecastPoints = forecast.map((value, index) => {
      if (value != null) return { x: xOf(index), y: scale(value) };
      const next = forecast[index + 1];
      return next != null && actual[index] != null ? actualPoints[index] : null;
    });

    const bandSpans: string[] = [];
    let span: number[] = [];
    const closeSpan = () => {
      if (span.length >= 2) {
        const upper = span.map((index) => `${xOf(index)},${scale(high[index] as number)}`);
        const lower = [...span].reverse().map((index) => `${xOf(index)},${scale(low[index] as number)}`);
        bandSpans.push([...upper, ...lower].join(" "));
      }
      span = [];
    };
    categories.forEach((_, index) => {
      if (low[index] != null && high[index] != null) span.push(index);
      else closeSpan();
    });
    closeSpan();

    const firstForecast = forecast.findIndex((value) => value != null);
    const columns = categories.map((label, index) => {
      const readings = [
        actual[index] != null ? `πραγματικά ${formatValue(actual[index], format)}` : "",
        forecast[index] != null ? `πρόβλεψη ${formatValue(forecast[index], format)}` : "",
        low[index] != null && high[index] != null
          ? `εύρος ${formatValue(low[index], format)} έως ${formatValue(high[index], format)}`
          : "",
      ].filter(Boolean);
      return {
        index,
        label,
        x: xOf(index),
        actualY: actual[index] == null ? null : scale(actual[index] as number),
        forecastY: forecast[index] == null ? null : scale(forecast[index] as number),
        highY: high[index] == null ? null : scale(high[index] as number),
        showLabel: index % labelStep === 0,
        ariaLabel: `${titles[index]}: ${readings.join(", ") || "χωρίς τιμή"}`,
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
      columns,
      titles,
      format,
      ticks: ticks.map((value) => ({ y: scale(value), label: formatAxis(value, format) })),
      bands: bandSpans,
      actualSegments: lineSegments(actualPoints),
      forecastSegments: lineSegments(forecastPoints),
      dividerX: firstForecast > 0 ? xOf(firstForecast) - band / 2 : null,
      ariaLabel: `${this.label()}. ${columns.map((column) => column.ariaLabel).join("; ")}.`,
    };
  });

  readonly tooltip = computed<ChartTooltip | null>(() => {
    const chart = this.view();
    const index = this.active();
    if (!chart || index == null || !chart.columns[index]) return null;
    const column = chart.columns[index];
    const low = this.low()[index];
    const high = this.high()[index];
    const rows: TooltipRow[] = [];
    if (this.actual()[index] != null) rows.push({ label: "Πραγματικά", value: formatValue(this.actual()[index], chart.format), color: "sea" });
    if (this.forecast()[index] != null) rows.push({ label: "Πρόβλεψη", value: formatValue(this.forecast()[index], chart.format), color: "terracotta" });
    if (low != null && high != null) {
      rows.push({ label: "Πιθανό εύρος", value: `${formatValue(low, chart.format)} – ${formatValue(high, chart.format)}`, color: "sun" });
    }
    if (!rows.length) rows.push({ label: "Τιμή", value: "—" });
    const ys = [column.actualY, column.forecastY, column.highY].filter((y): y is number => y !== null);
    return { title: chart.titles[index], rows, x: column.x, y: Math.min(chart.bottom, ...ys) };
  });
}
