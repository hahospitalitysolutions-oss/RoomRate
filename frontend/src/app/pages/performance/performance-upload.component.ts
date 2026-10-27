/**
 * «Ανέβασμα κρατήσεων» (A3): format, net-amount settings and file → preview
 * (summary, errors by row, room label mapping, sample) → commit; the import
 * history and «Διαγραφή όλων των κρατήσεων». Nothing is stored before the
 * commit; the backend keeps a preview for 24 hours.
 */
import { CommonModule } from "@angular/common";
import { ChangeDetectionStrategy, Component, computed, inject, input, OnInit, output, signal } from "@angular/core";

import { SelectMenuComponent, SelectMenuOption } from "../../components/select-menu.component";
import { formatCount, formatEuroCents } from "../../components/charts/chart-utils";
import { PerformanceApiService } from "../../services/performance-api.service";
import type {
  ImportCommitResult,
  ImportFormat,
  ImportPreview,
  ImportSummary,
  PerformanceStatus,
  RevenueSettings,
} from "../../types/performance";
import { formatGreekDate } from "../pricing/pricing-format";

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const IGNORE = "__ignore__";

const FORMATS: { id: ImportFormat; label: string; hint: string }[] = [
  { id: "template", label: "Πρότυπο RoomRate", hint: "Το Excel που κατεβάζετε από εδώ, συμπληρωμένο." },
  { id: "booking", label: "Export του Booking.com", hint: "Οι κρατήσεις όπως τις κατεβάζετε από το extranet, χωρίς αλλαγές." },
];

@Component({
  selector: "app-performance-upload",
  standalone: true,
  imports: [CommonModule, SelectMenuComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p *ngIf="error()" class="alert alert-error" role="alert" data-testid="performance-upload-error">{{ error() }}</p>
    <p *ngIf="result() as done" class="alert" role="status" data-testid="performance-upload-result">
      Αποθηκεύτηκαν {{ count(done.imported) }} νέες κρατήσεις, ενημερώθηκαν {{ count(done.updated) }}<ng-container *ngIf="done.skipped">,
      παραλείφθηκαν {{ count(done.skipped) }} γραμμές με λάθη</ng-container>.
    </p>

    <!-- Step 1: format, settings, file. -->
    <form *ngIf="!preview()" class="performance-upload-form" (submit)="$event.preventDefault(); check()">
      <fieldset class="performance-fieldset">
        <legend>Τι αρχείο ανεβάζετε;</legend>
        <label *ngFor="let option of formats" class="performance-choice">
          <input
            type="radio"
            name="performance-format"
            [value]="option.id"
            [checked]="format() === option.id"
            [attr.data-testid]="'performance-upload-format-' + option.id"
            (change)="format.set(option.id)"
          />
          <span><strong>{{ option.label }}</strong><small>{{ option.hint }}</small></span>
        </label>
        <button class="text-button" type="button" data-testid="performance-upload-template" (click)="templateRequested.emit()">
          Κατέβασε το πρότυπο
        </button>
      </fieldset>

      <fieldset *ngIf="settings() as current" class="performance-fieldset" data-testid="performance-revenue-settings">
        <legend>Καθαρά ποσά</legend>
        <p class="muted performance-fieldset-hint">
          Τα καθαρά έσοδα βγαίνουν από τα μικτά μείον ΦΠΑ, τέλος ανθεκτικότητας και προμήθεια καναλιού.
        </p>
        <div class="performance-settings-row">
          <label>
            <span class="performance-filter-label">ΦΠΑ διαμονής (%)</span>
            <input
              class="roomrate-input"
              type="number"
              min="0"
              max="100"
              step="0.1"
              data-testid="performance-settings-vat"
              [value]="current.vat_pct"
              (input)="patchSettings({ vat_pct: toNumber($event) })"
            />
          </label>
          <label>
            <span class="performance-filter-label">Τέλος ανθεκτικότητας (€ / δωμάτιο / νύχτα)</span>
            <input
              class="roomrate-input"
              type="number"
              min="0"
              step="0.01"
              data-testid="performance-settings-fee"
              [value]="current.resilience_fee_eur"
              (input)="patchSettings({ resilience_fee_eur: toNumber($event) })"
            />
          </label>
        </div>
        <label class="checkbox-row">
          <input
            type="checkbox"
            data-testid="performance-settings-fee-included"
            [checked]="current.fee_included_in_price"
            (change)="patchSettings({ fee_included_in_price: $any($event.target).checked })"
          />
          <span>Η τιμή του πελάτη περιλαμβάνει το τέλος</span>
        </label>
        <div class="performance-actions">
          <button
            class="text-button"
            type="button"
            data-testid="performance-settings-save"
            [disabled]="!settingsDirty() || settingsBusy()"
            (click)="saveSettings()"
          >Αποθήκευση ρυθμίσεων</button>
          <span *ngIf="settingsSaved()" class="muted" role="status">Αποθηκεύτηκαν.</span>
        </div>
      </fieldset>

      <fieldset class="performance-fieldset">
        <legend>Αρχείο</legend>
        <input
          type="file"
          accept=".xlsx,.csv"
          data-testid="performance-upload-file"
          (change)="pickFile($event)"
        />
        <p class="muted performance-fieldset-hint">.xlsx ή .csv, έως 5 MB. Ονόματα και στοιχεία επικοινωνίας πελατών δεν διαβάζονται.</p>
      </fieldset>

      <button class="secondary-button" type="submit" data-testid="performance-upload-check" [disabled]="!file() || busy()">
        {{ busy() ? "Έλεγχος…" : "Έλεγχος αρχείου" }}
      </button>
    </form>

    <!-- Step 2: preview, mapping, commit. -->
    <section *ngIf="preview() as current" class="performance-preview" data-testid="performance-upload-preview">
      <p *ngIf="current.format !== format()" class="alert performance-notice" data-testid="performance-upload-format-detected">
        Το αρχείο αναγνωρίστηκε ως «{{ formatLabel(current.format) }}» και διαβάστηκε έτσι.
      </p>

      <dl class="performance-preview-summary">
        <div><dt>Αρχείο</dt><dd>{{ current.filename }}</dd></div>
        <div><dt>Γραμμές</dt><dd>{{ count(current.rows_total) }}</dd></div>
        <div><dt>Νέες κρατήσεις</dt><dd data-testid="performance-preview-new">{{ count(current.new) }}</dd></div>
        <div><dt>Ήδη αποθηκευμένες (θα ενημερωθούν)</dt><dd>{{ count(current.updated) }}</dd></div>
        <div><dt>Ακυρωμένες</dt><dd>{{ count(current.cancelled) }}</dd></div>
        <div><dt>Γραμμές με λάθη</dt><dd>{{ count(current.errors.length) }}</dd></div>
      </dl>

      <div *ngIf="current.errors.length" class="performance-preview-block" data-testid="performance-preview-errors">
        <h3>Λάθη</h3>
        <p class="muted performance-fieldset-hint">Αυτές οι γραμμές δεν θα αποθηκευτούν. Διορθώστε το αρχείο και ανεβάστε το ξανά, ή παραλείψτε τις.</p>
        <ul class="performance-error-list">
          <li *ngFor="let item of current.errors">
            <strong>Γραμμή {{ item.row }}</strong><span *ngIf="item.booking_ref"> (κράτηση {{ item.booking_ref }})</span>: {{ item.message }}
          </li>
        </ul>
      </div>

      <div class="performance-preview-block" data-testid="performance-preview-mapping">
        <h3>Τύποι δωματίων</h3>
        <p class="muted performance-fieldset-hint">Σε ποιον τύπο του RoomRate αντιστοιχεί κάθε όνομα δωματίου του αρχείου.</p>
        <div class="performance-mapping-row" *ngFor="let row of current.mapping" [attr.data-testid]="'performance-mapping-' + row.label">
          <span class="performance-mapping-label">
            {{ row.label }}
            <small>{{ count(row.bookings) }} {{ row.bookings === 1 ? "κράτηση" : "κρατήσεις" }}</small>
          </span>
          <app-select-menu
            [testId]="'performance-mapping-select-' + row.label"
            placeholder="Επιλέξτε τύπο"
            [options]="mappingOptions()"
            [value]="row.ignored ? ignoreValue : (row.room_type_id ?? '')"
            (valueChange)="map(row.label, $event)"
          ></app-select-menu>
          <span *ngIf="row.ignored" class="performance-tag" data-testid="performance-mapping-ignored">Αγνοείται</span>
          <span *ngIf="!row.ignored && !row.room_type_id && row.bookings > 0" class="performance-tag is-warning">Χωρίς αντιστοίχιση</span>
        </div>
      </div>

      <details *ngIf="current.sample.length" class="performance-preview-block performance-sample">
        <summary>Δείγμα από το αρχείο</summary>
        <div class="performance-table-scroll">
          <table class="performance-table">
            <thead>
              <tr>
                <th scope="col">Κράτηση</th>
                <th scope="col">Άφιξη</th>
                <th scope="col">Αναχώρηση</th>
                <th scope="col">Νύχτες</th>
                <th scope="col">Δωμάτιο</th>
                <th scope="col">Μικτά</th>
                <th scope="col">Προμήθεια</th>
                <th scope="col">Καθαρά</th>
                <th scope="col">Κανάλι</th>
                <th scope="col">Κατάσταση</th>
              </tr>
            </thead>
            <tbody>
              <tr *ngFor="let row of current.sample" [class.has-error]="row.error">
                <th scope="row">{{ row.booking_ref }}</th>
                <td>{{ date(row.arrival) }}</td>
                <td>{{ date(row.departure) }}</td>
                <td>{{ row.nights ?? "—" }}</td>
                <td>{{ row.room_label }}</td>
                <td>{{ euro(row.gross_eur) }}</td>
                <td>{{ euro(row.commission_eur) }}</td>
                <td>{{ euro(row.net_eur) }}</td>
                <td>{{ row.channel }}</td>
                <td>{{ row.error || statusLabel(row.status) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <label *ngIf="current.errors.length" class="checkbox-row">
        <input type="checkbox" data-testid="performance-upload-skip-errors" [checked]="skipErrors()" (change)="skipErrors.set($any($event.target).checked)" />
        <span>Παράλειψη γραμμών με λάθη</span>
      </label>
      <p *ngIf="!current.ready" class="muted" data-testid="performance-upload-not-ready">
        Αντιστοιχίστε ή αγνοήστε κάθε τύπο δωματίου με κρατήσεις για να συνεχίσετε.
      </p>

      <div class="performance-actions">
        <button class="secondary-button" type="button" data-testid="performance-upload-commit" [disabled]="!canCommit()" (click)="commit()">
          {{ busy() ? "Αποθήκευση…" : "Αποθήκευση κρατήσεων" }}
        </button>
        <button class="text-button" type="button" data-testid="performance-upload-cancel" [disabled]="busy()" (click)="reset()">Άλλο αρχείο</button>
      </div>
    </section>

    <!-- History and the reset. -->
    <section class="performance-preview-block" data-testid="performance-imports">
      <h3>Προηγούμενα ανεβάσματα</h3>
      <p *ngIf="!history().length" class="muted">Δεν υπάρχουν ακόμη.</p>
      <ul *ngIf="history().length" class="performance-history">
        <li *ngFor="let item of history()">
          <span>{{ item.filename }} <small>{{ formatLabel(item.format) }}</small></span>
          <span>{{ date(item.committed_at) }} · {{ count(item.bookings_imported) }} κρατήσεις</span>
        </li>
      </ul>

      <div *ngIf="status().has_data" class="performance-danger">
        <button
          *ngIf="!confirmDelete()"
          class="text-button performance-danger-button"
          type="button"
          data-testid="performance-delete-all"
          (click)="confirmDelete.set(true)"
        >Διαγραφή όλων των κρατήσεων</button>
        <div *ngIf="confirmDelete()" class="alert alert-error" role="alertdialog" aria-label="Επιβεβαίωση διαγραφής">
          <p>
            Θα διαγραφούν και οι {{ count(status().bookings) }} κρατήσεις του καταλύματος. Οι ρυθμίσεις, τα δωμάτια και οι
            κλειστές περίοδοι μένουν. Δεν αναιρείται.
          </p>
          <div class="performance-actions">
            <button class="secondary-button performance-danger-confirm" type="button" data-testid="performance-delete-confirm" [disabled]="busy()" (click)="deleteAll()">
              Ναι, διαγραφή όλων
            </button>
            <button class="text-button" type="button" (click)="confirmDelete.set(false)">Άκυρο</button>
          </div>
        </div>
      </div>
    </section>
  `,
})
export class PerformanceUploadComponent implements OnInit {
  readonly ownedPropertyId = input.required<string>();
  readonly status = input.required<PerformanceStatus>();
  /** After a commit or a delete: the new /status, so the page can redraw. */
  readonly statusChanged = output<PerformanceStatus>();
  readonly templateRequested = output<void>();

  private readonly api = inject(PerformanceApiService);

  readonly formats = FORMATS;
  readonly ignoreValue = IGNORE;
  readonly format = signal<ImportFormat>("booking");
  readonly file = signal<File | null>(null);
  readonly preview = signal<ImportPreview | null>(null);
  readonly result = signal<ImportCommitResult | null>(null);
  readonly history = signal<ImportSummary[]>([]);
  readonly skipErrors = signal(false);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly confirmDelete = signal(false);

  readonly settings = signal<RevenueSettings | null>(null);
  readonly settingsDirty = signal(false);
  readonly settingsBusy = signal(false);
  readonly settingsSaved = signal(false);

  readonly mappingOptions = computed<SelectMenuOption[]>(() => [
    ...this.status().room_types.map((type) => ({ value: type.id, label: type.label })),
    { value: IGNORE, label: "Αγνοείται (να μη μετρήσουν)" },
  ]);

  /** A double click must not commit twice (the second would answer 409). */
  readonly canCommit = computed(() => {
    const preview = this.preview();
    if (!preview || this.busy() || !preview.ready) return false;
    return !preview.errors.length || this.skipErrors();
  });

  async ngOnInit(): Promise<void> {
    const id = this.ownedPropertyId();
    const [settings, history] = await Promise.allSettled([this.api.revenueSettings(id), this.api.imports(id)]);
    if (settings.status === "fulfilled") this.settings.set(settings.value);
    if (history.status === "fulfilled") this.history.set(history.value);
    const failed = [settings, history].find((outcome) => outcome.status === "rejected");
    if (failed) this.error.set(message(failed.reason));
  }

  pickFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0] ?? null;
    this.error.set("");
    this.result.set(null);
    if (file && file.size > MAX_FILE_BYTES) {
      this.file.set(null);
      this.error.set("Το αρχείο είναι πάνω από 5 MB.");
      return;
    }
    this.file.set(file);
  }

  async check(): Promise<void> {
    const file = this.file();
    if (!file || this.busy()) return;
    await this.run(async () => {
      this.preview.set(await this.api.uploadImport(this.ownedPropertyId(), file, this.format()));
      this.skipErrors.set(false);
    });
  }

  async map(label: string, value: string): Promise<void> {
    const preview = this.preview();
    if (!preview || this.busy()) return;
    const mapping = preview.mapping.map((row) => {
      if (row.label !== label) return { label: row.label, room_type_id: row.room_type_id, ignore: row.ignored };
      return value === IGNORE
        ? { label, room_type_id: null, ignore: true }
        : { label, room_type_id: value || null, ignore: false };
    });
    await this.run(async () => {
      this.preview.set(await this.api.updateMapping(this.ownedPropertyId(), preview.id, { mapping }));
    });
  }

  async commit(): Promise<void> {
    const preview = this.preview();
    if (!preview || !this.canCommit()) return;
    await this.run(async () => {
      const done = await this.api.commitImport(this.ownedPropertyId(), preview.id, { skip_errors: this.skipErrors() });
      this.result.set(done);
      this.preview.set(null);
      this.file.set(null);
      this.statusChanged.emit(done.status);
      this.history.set(await this.api.imports(this.ownedPropertyId()));
    });
  }

  async deleteAll(): Promise<void> {
    await this.run(async () => {
      await this.api.deleteAllBookings(this.ownedPropertyId());
      this.confirmDelete.set(false);
      this.result.set(null);
      this.statusChanged.emit(await this.api.status(this.ownedPropertyId()));
    });
  }

  reset(): void {
    this.preview.set(null);
    this.file.set(null);
    this.error.set("");
  }

  patchSettings(change: Partial<RevenueSettings>): void {
    const current = this.settings();
    if (!current) return;
    this.settings.set({ ...current, ...change });
    this.settingsDirty.set(true);
    this.settingsSaved.set(false);
  }

  async saveSettings(): Promise<void> {
    const current = this.settings();
    if (!current) return;
    this.settingsBusy.set(true);
    this.error.set("");
    try {
      this.settings.set(await this.api.saveRevenueSettings(this.ownedPropertyId(), current));
      this.settingsDirty.set(false);
      this.settingsSaved.set(true);
    } catch (error) {
      this.error.set(message(error));
    } finally {
      this.settingsBusy.set(false);
    }
  }

  formatLabel(format: ImportFormat): string {
    return FORMATS.find((option) => option.id === format)?.label ?? format;
  }

  statusLabel(status: string): string {
    return status === "cancelled" ? "Ακυρωμένη" : status === "no_show" ? "Δεν εμφανίστηκε" : "Επιβεβαιωμένη";
  }

  toNumber(event: Event): number {
    const value = Number((event.target as HTMLInputElement).value);
    return Number.isFinite(value) ? value : 0;
  }

  count(value: number): string {
    return formatCount(value);
  }

  euro(value: number | null): string {
    return formatEuroCents(value);
  }

  date(value: string | null): string {
    return value ? formatGreekDate(value) : "—";
  }

  private async run(task: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set("");
    try {
      await task();
    } catch (error) {
      this.error.set(message(error));
    } finally {
      this.busy.set(false);
    }
  }
}

/** The API's Greek `detail` as it comes (404 expired preview, 409 already stored, 413, 422…). */
function message(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Κάτι πήγε στραβά. Δοκιμάστε ξανά.";
}
