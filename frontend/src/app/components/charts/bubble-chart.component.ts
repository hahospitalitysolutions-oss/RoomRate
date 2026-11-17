/**
 * Points on two axes with a size for a third value: e.g. room types by
 * bookings (x) and ADR (y), sized by revenue. Each bubble carries its label;
 * with `selectable` a click (or Enter) reports its id.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input, output } from "@angular/core";

import { ChartBase } from "./chart-base";
import { ChartLegendComponent } from "./chart-legend.component";
import { ChartTooltipComponent } from "./chart-tooltip.component";
import { CATEGORY_COLORS, type BubblePoint, type ChartTooltip, type LegendItem, type ValueFormat } from "./chart-types";
import { formatAxis, formatValue, linear, niceTicks } from "./chart-utils";

const PAD_TOP = 18;
const PAD_BOTTOM = 44;
const AXIS_WIDTH = 56;
const MIN_RADIUS = 8;
const MAX_RADIUS = 30;

@Component({
  selector: "app-bubble-chart",
  standalone: true,
  imports: [CommonModule, ChartLegendComponent, ChartTooltipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="chart-frame" *ngIf="view() as chart">
      <svg class="chart-svg" [attr.width]="chart.width" [attr.height]="chart.height" role="group" [attr.aria-label]="chart.ariaLabel">
        <g *ngFor="let tick of chart.yTicks">
          <line class="chart-grid" [attr.x1]="chart.left" [attr.x2]="chart.right" [attr.y1]="tick.y" [attr.y2]="tick.y" />
          <text class="chart-tick" [attr.x]="chart.left - 6" [attr.y]="tick.y + 4" text-anchor="end">{{ tick.label }}</text>
        </g>
        <text
          *ngFor="let tick of chart.xTicks"
          class="chart-tick"
          [attr.x]="tick.x"
          [attr.y]="chart.bottom + 18"
          text-anchor="middle"
        >{{ tick.label }}</text>
        <text class="chart-axis-title" [attr.x]="(chart.left + chart.right) / 2" [attr.y]="chart.height - 4" text-anchor="middle">{{ xLabel() }}</text>
        <text class="chart-axis-title" x="4" [attr.y]="chart.top - 6">{{ yLabel() }}</text>

        <g
          *ngFor="let bubble of chart.bubbles"
          class="bubble"
          [class.is-selected]="bubble.id === selectedId()"
          [class.is-dim]="active() !== null && active() !== bubble.index"
          tabindex="0"
          [attr.role]="selectable() ? 'button' : 'img'"
          [attr.aria-label]="bubble.ariaLabel"
          [attr.aria-pressed]="selectable() ? bubble.id === selectedId() : null"
          (pointerenter)="enter($event, bubble.index)"
          (pointerleave)="leave($event, bubble.index)"
          (click)="choose($event, bubble.index, bubble.id)"
          (keydown.enter)="selectable() && picked.emit(bubble.id)"
          (focus)="show(bubble.index)"
          (blur)="hide(bubble.index)"
        >
          <circle class="bubble-dot" [ngClass]="'chart-fill-' + bubble.color" [attr.cx]="bubble.x" [attr.cy]="bubble.y" [attr.r]="bubble.r" />
          <text *ngIf="!chart.compact" class="bubble-label" [attr.x]="bubble.x" [attr.y]="bubble.y - bubble.r - 5" text-anchor="middle">{{ bubble.label }}</text>
        </g>
      </svg>
      <app-chart-tooltip [tooltip]="tooltip()" [width]="chart.width"></app-chart-tooltip>
    </div>
    <!-- On a phone the labels would collide: the key names the bubbles instead. -->
    <app-chart-legend *ngIf="view()?.compact" [items]="legend()"></app-chart-legend>
  `,
})
export class BubbleChartComponent extends ChartBase {
  readonly label = input.required<string>();
  readonly points = input.required<BubblePoint[]>();
  readonly xLabel = input.required<string>();
  readonly yLabel = input.required<string>();
  readonly sizeLabel = input.required<string>();
  readonly xFormat = input<ValueFormat>("count");
  readonly yFormat = input<ValueFormat>("euro");
  readonly sizeFormat = input<ValueFormat>("euro");
  readonly selectable = input(false);
  readonly selectedId = input<string | null>(null);
  readonly height = input(300);
  readonly picked = output<string>();

  readonly view = computed(() => {
    // A point without both coordinates cannot be placed: left out, and said so in the aria-label.
    const points = this.points().filter((point) => point.x != null && point.y != null);
    if (!points.length) return null;
    const width = this.width();
    const height = this.height();
    const left = AXIS_WIDTH;
    const right = width - 16;
    const top = PAD_TOP;
    const bottom = height - PAD_BOTTOM;
    const xs = points.map((point) => point.x as number);
    const ys = points.map((point) => point.y as number);
    const ticksCount = this.compact() ? 3 : 4;
    const xTicks = niceTicks(0, Math.max(...xs) * 1.1, ticksCount);
    const yTicks = niceTicks(0, Math.max(...ys) * 1.15, ticksCount);
    // Keep the largest bubble inside the plot.
    const xScale = linear(xTicks[0], xTicks[xTicks.length - 1], left + MAX_RADIUS / 2, right - MAX_RADIUS);
    const yScale = linear(yTicks[0], yTicks[yTicks.length - 1], bottom, top);
    const maxSize = Math.max(...points.map((point) => point.size), 0) || 1;
    // Area, not radius, follows the value.
    const radius = (size: number) => MIN_RADIUS + (MAX_RADIUS - MIN_RADIUS) * Math.sqrt(Math.max(size, 0) / maxSize);

    const bubbles = points
      .map((point, index) => {
        const ariaLabel = `${point.label}: ${this.xLabel()} ${formatValue(point.x, this.xFormat())}, `
          + `${this.yLabel()} ${formatValue(point.y, this.yFormat())}, ${this.sizeLabel()} ${formatValue(point.size, this.sizeFormat())}`;
        return {
          index,
          id: point.id,
          label: point.label,
          color: point.color ?? CATEGORY_COLORS[index % CATEGORY_COLORS.length],
          x: xScale(point.x as number),
          y: yScale(point.y as number),
          r: Math.round(radius(point.size) * 10) / 10,
          point,
          ariaLabel,
        };
      })
      // Small bubbles on top, so a large one never hides them.
      .sort((a, b) => b.r - a.r);

    const missing = this.points().length - points.length;
    return {
      width,
      height,
      left,
      right,
      top,
      bottom,
      compact: this.compact(),
      bubbles,
      xTicks: xTicks.map((value) => ({ x: xScale(value), label: formatAxis(value, this.xFormat()) })),
      yTicks: yTicks.map((value) => ({ y: yScale(value), label: formatAxis(value, this.yFormat()) })),
      ariaLabel: `${this.label()}. ${bubbles.map((bubble) => bubble.ariaLabel).join("; ")}.`
        + (missing ? ` ${missing} χωρίς τιμή δεν εμφανίζονται.` : ""),
    };
  });

  readonly legend = computed<LegendItem[]>(() =>
    [...(this.view()?.bubbles ?? [])]
      .sort((a, b) => a.index - b.index)
      .map((bubble) => ({ label: bubble.label, color: bubble.color, mark: "dot" as const })));

  readonly tooltip = computed<ChartTooltip | null>(() => {
    const bubble = this.view()?.bubbles.find((item) => item.index === this.active());
    if (!bubble) return null;
    return {
      title: bubble.label,
      rows: [
        { label: this.xLabel(), value: formatValue(bubble.point.x, this.xFormat()) },
        { label: this.yLabel(), value: formatValue(bubble.point.y, this.yFormat()) },
        { label: this.sizeLabel(), value: formatValue(bubble.point.size, this.sizeFormat()) },
      ],
      x: bubble.x,
      y: bubble.y - bubble.r,
    };
  });

  choose(event: MouseEvent, index: number, id: string): void {
    this.tap(event, index);
    if (this.selectable()) this.picked.emit(id);
  }
}
