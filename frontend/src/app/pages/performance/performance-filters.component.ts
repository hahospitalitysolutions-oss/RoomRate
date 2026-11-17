/**
 * The «Απόδοση» filter bar: period (a season preset or from–to), room type,
 * gross/net amounts and the comparison with last year. It only shows the view
 * and reports changes; the page writes them to the URL.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, input, output, signal } from "@angular/core";

import { SelectMenuComponent, SelectMenuOption } from "../../components/select-menu.component";
import type { AmountBasis, PerformanceStatus } from "../../types/performance";
import { isIsoDate, PerformanceView, seasonOf, seasonRange } from "./performance-filter-state";

const CUSTOM_PERIOD = "custom";

@Component({
  selector: "app-performance-filters",
  standalone: true,
  imports: [CommonModule, SelectMenuComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form class="panel performance-filters" data-testid="performance-filters" (submit)="$event.preventDefault()">
      <label class="performance-filter">
        <span class="performance-filter-label">Περίοδος</span>
        <app-select-menu
          testId="performance-filter-period"
          [options]="periodOptions()"
          [value]="periodValue()"
          (valueChange)="pickPeriod($event)"
        ></app-select-menu>
      </label>

      <div class="performance-filter performance-filter-dates">
        <label>
          <span class="performance-filter-label">Από</span>
          <input
            class="roomrate-input"
            type="date"
            data-testid="performance-filter-from"
            [value]="view().from"
            (change)="pickDates($any($event.target).value, view().to)"
          />
        </label>
        <label>
          <span class="performance-filter-label">Έως</span>
          <input
            class="roomrate-input"
            type="date"
            data-testid="performance-filter-to"
            [value]="view().to"
            (change)="pickDates(view().from, $any($event.target).value)"
          />
        </label>
        <p *ngIf="dateError()" class="performance-filter-error" role="alert" data-testid="performance-filter-date-error">
          {{ dateError() }}
        </p>
      </div>

      <label class="performance-filter">
        <span class="performance-filter-label">Τύπος δωματίου</span>
        <app-select-menu
          testId="performance-filter-room"
          [options]="roomOptions()"
          [value]="view().room"
          (valueChange)="changed.emit({ room: $event })"
        ></app-select-menu>
      </label>

      <div class="performance-filter">
        <span class="performance-filter-label" id="performance-amounts-label">Ποσά</span>
        <div class="performance-segmented" role="group" aria-labelledby="performance-amounts-label">
          <button
            *ngFor="let option of amountOptions"
            type="button"
            [attr.aria-pressed]="view().amounts === option.id"
            [attr.data-testid]="'performance-amounts-' + option.id"
            [title]="option.hint"
            (click)="pickAmounts(option.id)"
          >{{ option.label }}</button>
        </div>
      </div>

      <label class="checkbox-row performance-filter-compare">
        <input
          type="checkbox"
          data-testid="performance-filter-compare"
          [checked]="view().compare"
          (change)="changed.emit({ compare: $any($event.target).checked })"
        />
        <span>Σύγκριση με πέρυσι</span>
      </label>
      <p *ngIf="view().amounts === 'net'" class="performance-net-hint" data-testid="performance-net-hint">
        Καθαρά: μετά από ΦΠΑ, τέλος ανθεκτικότητας και προμήθεια καναλιού (ρυθμίσεις στο «Ανέβασμα κρατήσεων»).
      </p>
    </form>
  `,
})
export class PerformanceFiltersComponent {
  readonly view = input.required<PerformanceView>();
  readonly status = input.required<PerformanceStatus>();
  readonly changed = output<Partial<PerformanceView>>();

  readonly dateError = signal("");

  readonly amountOptions: { id: AmountBasis; label: string; hint: string }[] = [
    { id: "gross", label: "Μικτά", hint: "Ό,τι πλήρωσε ο πελάτης, με ΦΠΑ και τέλος ανθεκτικότητας" },
    { id: "net", label: "Καθαρά", hint: "Μετά από ΦΠΑ, τέλος ανθεκτικότητας και προμήθεια καναλιού" },
  ];

  readonly periodValue = computed(() => {
    const season = seasonOf(this.view().from, this.view().to);
    return season !== null && this.status().seasons.includes(season) ? String(season) : CUSTOM_PERIOD;
  });

  readonly periodOptions = computed<SelectMenuOption[]>(() => {
    const options: SelectMenuOption[] = this.status().seasons.map((season) => ({
      value: String(season),
      label: `Σεζόν ${season}`,
    }));
    if (this.periodValue() === CUSTOM_PERIOD) {
      options.push({ value: CUSTOM_PERIOD, label: "Προσαρμοσμένη περίοδος" });
    }
    return options;
  });

  // "" is a real option (not a placeholder) so the owner can go back to the whole property.
  readonly roomOptions = computed<SelectMenuOption[]>(() => [
    { value: "", label: "Όλο το κατάλυμα" },
    ...this.status().room_types.map((type) => ({ value: type.id, label: type.label })),
  ]);

  pickPeriod(value: string): void {
    const season = Number(value);
    if (value === CUSTOM_PERIOD || !Number.isInteger(season)) return;
    this.dateError.set("");
    this.changed.emit(seasonRange(season));
  }

  pickDates(from: string, to: string): void {
    if (!isIsoDate(from) || !isIsoDate(to)) {
      this.dateError.set("Συμπληρώστε και τις δύο ημερομηνίες.");
      return;
    }
    if (from > to) {
      this.dateError.set("Η αρχή της περιόδου πρέπει να είναι πριν από το τέλος της.");
      return;
    }
    this.dateError.set("");
    this.changed.emit({ from, to });
  }

  pickAmounts(amounts: AmountBasis): void {
    if (amounts !== this.view().amounts) this.changed.emit({ amounts });
  }
}
