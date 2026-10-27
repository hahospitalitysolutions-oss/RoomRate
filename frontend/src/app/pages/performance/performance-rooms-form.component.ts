/**
 * «Δωμάτια ανά τύπο» (A9): the room count of each type (/inventory), with an
 * optional from–to when it changed within the year, and the closed periods
 * (/closures) of the property or of one type. Owners who upload the
 * Booking.com export fill this in: the export has no room counts.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input, OnInit, output, signal } from "@angular/core";

import { SelectMenuComponent, SelectMenuOption } from "../../components/select-menu.component";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceStatus, PropertyClosures, RoomInventory } from "../../types/performance";
import { isIsoDate } from "./performance-filter-state";

type InventoryRow = RoomInventory["room_types"][number];
type ClosureRow = PropertyClosures["closures"][number];

const WHOLE_PROPERTY = "";

@Component({
  selector: "app-performance-rooms-form",
  standalone: true,
  imports: [CommonModule, SelectMenuComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="error()" class="alert alert-error" role="alert" data-testid="performance-rooms-form-error">{{ error() }}</p>
    <p *ngIf="loading()" class="muted">Φόρτωση…</p>

    <form *ngIf="inventory() as rows" class="performance-fieldset" data-testid="performance-inventory-form" novalidate (submit)="$event.preventDefault(); saveInventory()">
      <h3>Δωμάτια ανά τύπο</h3>
      <p class="muted performance-fieldset-hint">
        Πόσα δωμάτια έχει κάθε τύπος. Χωρίς αυτό δεν υπολογίζονται πληρότητα και RevPAR.
        Αν ο αριθμός άλλαξε μέσα στη χρονιά, συμπληρώστε και το διάστημα.
      </p>
      <div class="performance-inventory-row" *ngFor="let row of rows; index as i" [attr.data-testid]="'performance-inventory-' + row.room_type_id">
        <span class="performance-mapping-label">{{ row.label }}</span>
        <label>
          <span class="performance-filter-label">Δωμάτια</span>
          <input
            class="roomrate-input"
            type="number"
            min="0"
            step="1"
            required
            [attr.data-testid]="'performance-inventory-rooms-' + i"
            [value]="row.rooms ?? ''"
            (input)="patchInventory(i, { rooms: toCount($event) })"
          />
        </label>
        <label>
          <span class="performance-filter-label">Από (προαιρετικό)</span>
          <input class="roomrate-input" type="date" [value]="row.valid_from ?? ''" (change)="patchInventory(i, { valid_from: dateOrNull($event) })" />
        </label>
        <label>
          <span class="performance-filter-label">Έως (προαιρετικό)</span>
          <input class="roomrate-input" type="date" [value]="row.valid_to ?? ''" (change)="patchInventory(i, { valid_to: dateOrNull($event) })" />
        </label>
      </div>
      <p *ngIf="inventoryError()" class="performance-filter-error" role="alert">{{ inventoryError() }}</p>
      <div class="performance-actions">
        <button class="secondary-button" type="submit" data-testid="performance-inventory-save" [disabled]="busy()">Αποθήκευση δωματίων</button>
        <span *ngIf="saved() === 'inventory'" class="muted" role="status">Αποθηκεύτηκαν.</span>
      </div>
    </form>

    <form *ngIf="closures() as rows" class="performance-fieldset" data-testid="performance-closures-form" novalidate (submit)="$event.preventDefault(); saveClosures()">
      <h3>Κλειστές περίοδοι</h3>
      <p class="muted performance-fieldset-hint">
        Όταν το κατάλυμα (ή κάποια δωμάτια) ήταν κλειστά: αυτές οι νύχτες δεν μετράνε ως απούλητες.
      </p>
      <p *ngIf="!rows.length" class="muted">Καμία κλειστή περίοδος.</p>
      <div class="performance-closure-row" *ngFor="let row of rows; index as i" [attr.data-testid]="'performance-closure-' + i">
        <label>
          <span class="performance-filter-label">Από</span>
          <input class="roomrate-input" type="date" required [value]="row.date_from" (change)="patchClosure(i, { date_from: $any($event.target).value })" />
        </label>
        <label>
          <span class="performance-filter-label">Έως</span>
          <input class="roomrate-input" type="date" required [value]="row.date_to" (change)="patchClosure(i, { date_to: $any($event.target).value })" />
        </label>
        <label>
          <span class="performance-filter-label">Τι έκλεισε</span>
          <app-select-menu
            [options]="scopeOptions()"
            [value]="row.room_type_id ?? ''"
            (valueChange)="patchClosure(i, { room_type_id: $event || null, rooms: $event ? row.rooms : null })"
          ></app-select-menu>
        </label>
        <label *ngIf="row.room_type_id">
          <span class="performance-filter-label">Δωμάτια (κενό = όλα)</span>
          <input class="roomrate-input" type="number" min="1" step="1" [value]="row.rooms ?? ''" (input)="patchClosure(i, { rooms: toCount($event) })" />
        </label>
        <label class="performance-closure-reason">
          <span class="performance-filter-label">Λόγος (προαιρετικό)</span>
          <input class="roomrate-input" type="text" maxlength="120" [value]="row.reason ?? ''" (input)="patchClosure(i, { reason: $any($event.target).value || null })" />
        </label>
        <button class="text-button performance-danger-button" type="button" [attr.aria-label]="'Αφαίρεση περιόδου ' + (i + 1)" (click)="removeClosure(i)">Αφαίρεση</button>
      </div>
      <p *ngIf="closuresError()" class="performance-filter-error" role="alert">{{ closuresError() }}</p>
      <div class="performance-actions">
        <button class="text-button" type="button" data-testid="performance-closure-add" (click)="addClosure()">+ Προσθήκη περιόδου</button>
        <button class="secondary-button" type="submit" data-testid="performance-closures-save" [disabled]="busy()">Αποθήκευση περιόδων</button>
        <span *ngIf="saved() === 'closures'" class="muted" role="status">Αποθηκεύτηκαν.</span>
      </div>
    </form>
  `,
})
export class PerformanceRoomsFormComponent implements OnInit {
  readonly ownedPropertyId = input.required<string>();
  readonly status = input.required<PerformanceStatus>();
  /** After a save: the new /status (inventory_complete may have changed). */
  readonly statusChanged = output<PerformanceStatus>();

  private readonly api = inject(PerformanceApiService);

  readonly inventory = signal<InventoryRow[] | null>(null);
  readonly closures = signal<ClosureRow[] | null>(null);
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly inventoryError = signal("");
  readonly closuresError = signal("");
  readonly saved = signal<"inventory" | "closures" | null>(null);

  readonly scopeOptions = computed<SelectMenuOption[]>(() => [
    { value: WHOLE_PROPERTY, label: "Όλο το κατάλυμα" },
    ...this.status().room_types.map((type) => ({ value: type.id, label: type.label })),
  ]);

  async ngOnInit(): Promise<void> {
    const id = this.ownedPropertyId();
    try {
      const [inventory, closures] = await Promise.all([this.api.inventory(id), this.api.closures(id)]);
      this.inventory.set(inventory.room_types);
      this.closures.set(closures.closures);
    } catch (error) {
      this.error.set(message(error));
    } finally {
      this.loading.set(false);
    }
  }

  patchInventory(index: number, change: Partial<InventoryRow>): void {
    this.inventory.update((rows) => rows?.map((row, i) => (i === index ? { ...row, ...change } : row)) ?? rows);
    this.saved.set(null);
  }

  patchClosure(index: number, change: Partial<ClosureRow>): void {
    this.closures.update((rows) => rows?.map((row, i) => (i === index ? { ...row, ...change } : row)) ?? rows);
    this.saved.set(null);
  }

  addClosure(): void {
    this.closures.update((rows) => [...(rows ?? []), { date_from: "", date_to: "", room_type_id: null, rooms: null, reason: null }]);
  }

  removeClosure(index: number): void {
    this.closures.update((rows) => rows?.filter((_, i) => i !== index) ?? rows);
    this.saved.set(null);
  }

  async saveInventory(): Promise<void> {
    const rows = this.inventory();
    if (!rows) return;
    const problem = rows.find((row) => row.rooms === null)
      ? "Συμπληρώστε τα δωμάτια κάθε τύπου (0 αν δεν τον έχετε πια)."
      : rows.find((row) => !!row.valid_from !== !!row.valid_to || (row.valid_from && row.valid_to && row.valid_from > row.valid_to))
        ? "Το διάστημα θέλει και «Από» και «Έως», με την αρχή πριν από το τέλος."
        : "";
    this.inventoryError.set(problem);
    if (problem) return;
    await this.save("inventory", async (id) => {
      this.inventory.set((await this.api.saveInventory(id, { room_types: rows })).room_types);
    });
  }

  async saveClosures(): Promise<void> {
    const rows = this.closures();
    if (!rows) return;
    const invalid = rows.some((row) => !isIsoDate(row.date_from) || !isIsoDate(row.date_to) || row.date_from > row.date_to);
    this.closuresError.set(invalid ? "Κάθε περίοδος θέλει «Από» και «Έως», με την αρχή πριν από το τέλος." : "");
    if (invalid) return;
    await this.save("closures", async (id) => {
      this.closures.set((await this.api.saveClosures(id, { closures: rows })).closures);
    });
  }

  toCount(event: Event): number | null {
    const raw = (event.target as HTMLInputElement).value;
    const value = Number(raw);
    return raw === "" || !Number.isFinite(value) ? null : Math.max(0, Math.round(value));
  }

  dateOrNull(event: Event): string | null {
    const value = (event.target as HTMLInputElement).value;
    return isIsoDate(value) ? value : null;
  }

  private async save(what: "inventory" | "closures", task: (id: string) => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set("");
    try {
      const id = this.ownedPropertyId();
      await task(id);
      this.saved.set(what);
      this.statusChanged.emit(await this.api.status(id));
    } catch (error) {
      this.error.set(message(error));
    } finally {
      this.busy.set(false);
    }
  }
}

function message(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Κάτι πήγε στραβά. Δοκιμάστε ξανά.";
}
