/**
 * Shares of a whole as a donut, with the key beside it listing every part and
 * its percentage (the donut alone is hard to read exactly). `value` of each
 * item is its share in % (0–100); `hint` carries the amount behind it.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input } from "@angular/core";

import { ChartBase } from "./chart-base";
import { ChartTooltipComponent } from "./chart-tooltip.component";
import { CATEGORY_COLORS, type ChartItem, type ChartTooltip } from "./chart-types";
import { formatPct, round1 } from "./chart-utils";

const SIZE = 180;
const RADIUS = 80;
const THICKNESS = 30;

@Component({
  selector: "app-share-chart",
  standalone: true,
  imports: [CommonModule, ChartTooltipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="share-chart">
      <div class="chart-frame share-chart-donut">
        <svg [attr.width]="size" [attr.height]="size" [attr.viewBox]="'0 0 ' + size + ' ' + size" role="group" [attr.aria-label]="ariaLabel()">
          <path
            *ngFor="let slice of slices()"
            class="share-slice" [ngClass]="'chart-fill-' + slice.color"
            [class.is-dim]="active() !== null && active() !== slice.index"
            [attr.d]="slice.path"
            tabindex="0"
            role="img"
            [attr.aria-label]="slice.ariaLabel"
            (pointerenter)="enter($event, slice.index)"
            (pointerleave)="leave($event, slice.index)"
            (click)="tap($event, slice.index)"
            (focus)="show(slice.index)"
            (blur)="hide(slice.index)"
          />
          <text *ngIf="centerLabel()" class="share-center" [attr.x]="size / 2" [attr.y]="size / 2 + 5" text-anchor="middle">{{ centerLabel() }}</text>
        </svg>
        <app-chart-tooltip [tooltip]="tooltip()" [width]="size"></app-chart-tooltip>
      </div>
      <ul class="share-chart-key">
        <li *ngFor="let slice of slices()" [class.is-active]="active() === slice.index">
          <i class="chart-tooltip-swatch" [ngClass]="'chart-bg-' + slice.color" aria-hidden="true"></i>
          <span class="share-key-label">{{ slice.label }}<small *ngIf="slice.hint">{{ slice.hint }}</small></span>
          <b>{{ slice.pctLabel }}</b>
        </li>
      </ul>
    </div>
  `,
})
export class ShareChartComponent extends ChartBase {
  readonly label = input.required<string>();
  readonly items = input.required<ChartItem[]>();
  /** Short text in the hole, e.g. the total. */
  readonly centerLabel = input("");

  readonly size = SIZE;

  readonly slices = computed(() => {
    const items = this.items().filter((item) => item.value > 0);
    const total = items.reduce((sum, item) => sum + item.value, 0) || 1;
    let angle = -Math.PI / 2;
    return items.map((item, index) => {
      const sweep = (item.value / total) * Math.PI * 2;
      const path = arc(angle, angle + sweep);
      angle += sweep;
      const pctLabel = formatPct(item.value);
      return {
        index,
        label: item.label,
        hint: item.hint ?? "",
        color: item.color ?? CATEGORY_COLORS[index % CATEGORY_COLORS.length],
        path,
        pctLabel,
        mid: angle - sweep / 2,
        ariaLabel: `${item.label}: ${pctLabel}${item.hint ? `, ${item.hint}` : ""}`,
      };
    });
  });

  readonly ariaLabel = computed(() => `${this.label()}. ${this.slices().map((slice) => slice.ariaLabel).join("; ")}.`);

  readonly tooltip = computed<ChartTooltip | null>(() => {
    const slice = this.slices().find((item) => item.index === this.active());
    if (!slice) return null;
    const rows = [{ label: "Μερίδιο", value: slice.pctLabel, color: slice.color }];
    if (slice.hint) rows.push({ label: "Ποσό", value: slice.hint, color: slice.color });
    const reach = RADIUS - THICKNESS / 2;
    return {
      title: slice.label,
      rows,
      x: round1(SIZE / 2 + Math.cos(slice.mid) * reach),
      y: round1(SIZE / 2 + Math.sin(slice.mid) * reach),
    };
  });
}

/** A donut slice between two angles (radians, clockwise from 3 o'clock). */
function arc(start: number, end: number): string {
  const center = SIZE / 2;
  const inner = RADIUS - THICKNESS;
  // A full circle cannot be one arc: stop just short of it.
  const stop = end - start >= Math.PI * 2 ? end - 0.0001 : end;
  const large = stop - start > Math.PI ? 1 : 0;
  const point = (radius: number, angle: number) =>
    `${round1(center + Math.cos(angle) * radius)} ${round1(center + Math.sin(angle) * radius)}`;
  return [
    `M ${point(RADIUS, start)}`,
    `A ${RADIUS} ${RADIUS} 0 ${large} 1 ${point(RADIUS, stop)}`,
    `L ${point(inner, stop)}`,
    `A ${inner} ${inner} 0 ${large} 0 ${point(inner, start)}`,
    "Z",
  ].join(" ");
}
