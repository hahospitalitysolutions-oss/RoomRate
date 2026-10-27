/**
 * Ranked horizontal bars with the label above and the value printed at the
 * end: long Greek labels (countries, room types) stay readable on a phone.
 * Plain markup, no SVG needed. With `selectable`, a row is a button that
 * reports its id (e.g. a room type to filter by).
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input, output } from "@angular/core";

import type { ChartItem, ValueFormat } from "./chart-types";
import { formatValue } from "./chart-utils";

@Component({
  selector: "app-horizontal-bar-chart",
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="hbar-chart" [attr.aria-label]="label()">
      <li *ngFor="let row of rows()" [class.is-selected]="row.id === selectedId()">
        <ng-container *ngIf="selectable(); else plainRow">
          <button type="button" class="hbar-row" [attr.aria-label]="row.ariaLabel" [attr.aria-pressed]="row.id === selectedId()" (click)="picked.emit(row.id)">
            <ng-container *ngTemplateOutlet="rowBody; context: { $implicit: row }"></ng-container>
          </button>
        </ng-container>
        <ng-template #plainRow>
          <div class="hbar-row" role="img" [attr.aria-label]="row.ariaLabel">
            <ng-container *ngTemplateOutlet="rowBody; context: { $implicit: row }"></ng-container>
          </div>
        </ng-template>
      </li>
    </ul>

    <ng-template #rowBody let-row>
      <span class="hbar-label">
        {{ row.label }}
        <small *ngIf="row.hint">{{ row.hint }}</small>
      </span>
      <span class="hbar-track" aria-hidden="true">
        <span class="hbar-fill" [ngClass]="'chart-bg-' + row.color" [style.width.%]="row.pct"></span>
      </span>
      <span class="hbar-value">{{ row.valueLabel }}</span>
    </ng-template>
  `,
})
export class HorizontalBarChartComponent {
  readonly label = input.required<string>();
  readonly items = input.required<ChartItem[]>();
  readonly format = input<ValueFormat>("count");
  readonly selectable = input(false);
  readonly selectedId = input<string | null>(null);
  readonly picked = output<string>();

  readonly rows = computed(() => {
    const items = this.items();
    const max = Math.max(0, ...items.map((item) => item.value));
    return items.map((item) => {
      const valueLabel = formatValue(item.value, this.format());
      return {
        ...item,
        color: item.color ?? "sea",
        valueLabel,
        // A non-zero value always shows a sliver.
        pct: max > 0 ? Math.max((item.value / max) * 100, item.value > 0 ? 1.5 : 0) : 0,
        ariaLabel: `${item.label}: ${valueLabel}${item.hint ? `, ${item.hint}` : ""}`,
      };
    });
  });
}
