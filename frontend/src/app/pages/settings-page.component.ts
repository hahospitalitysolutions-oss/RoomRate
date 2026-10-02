import { CommonModule } from "@angular/common";
import { Component, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Router, RouterLink } from "@angular/router";

import { NotificationBellComponent } from "../components/notification-bell.component";
import { NotificationToastsComponent } from "../components/notification-toasts.component";
import { ApiClientService } from "../services/api-client.service";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { AlertRule, AlertRuleCreate } from "../types/notifications";
import { ScheduleConfig, ScheduleConfigUpdate } from "../types/schedule";

type LoadingState = "idle" | "loading" | "ready" | "error";
type ScheduleForm = {
  enabled: boolean;
  frequency_hours: number;
  /** Greek local hour (Europe/Athens), the API's `hour_local`. */
  hour_athens: number;
  lead_days: number;
  nights: number;
  adults: number;
  children: number;
  rooms: number;
};

/**
 * Current offset of Greek time from UTC in whole hours (+2 in winter, +3 in
 * summer), for talking to an API that predates `hour_local` (it only knows
 * `hour_utc`). Read from the browser's time-zone database through Intl instead of
 * hard-coded, so the daylight-saving switch is handled without a date library.
 * Always Europe/Athens, never the browser's own zone: the hotel is in Greece
 * even when its owner opens the page from abroad.
 */
function athensUtcOffsetHours(at: Date = new Date()): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Athens",
    // h23, not hour12:false -- some engines render midnight as "24" otherwise.
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(at);
  const part = (type: Intl.DateTimeFormatPartTypes): number => Number(parts.find((p) => p.type === type)?.value);
  const athensWallClockAsUtc = Date.UTC(part("year"), part("month") - 1, part("day"), part("hour"), part("minute"));
  // Rounding absorbs the seconds/milliseconds the formatted parts drop.
  return Math.round((athensWallClockAsUtc - at.getTime()) / 3_600_000);
}

/** Keeps an hour inside 0-23 after adding or removing the offset (e.g. 22 UTC + 3 -> 1). */
function wrapHour(hour: number): number {
  return ((hour % 24) + 24) % 24;
}

/** Non-numeric input passes through untouched so validate() can still reject it. */
function utcHourToAthens(hourUtc: number): number {
  return Number.isFinite(hourUtc) ? wrapHour(hourUtc + athensUtcOffsetHours()) : hourUtc;
}

function athensHourToUtc(hourAthens: number): number {
  return Number.isFinite(hourAthens) ? wrapHour(hourAthens - athensUtcOffsetHours()) : hourAthens;
}

/**
 * Account settings: recurring scrape schedule, competitor price-alert rules,
 * and the owned property itself.
 *
 * The property section owns its own `propertyStatus` load state, decoupled
 * from the schedule's `status()`: it reads /me once (through
 * OnboardingService's cache, so a guarded navigation right after this page
 * costs nothing extra) to show name/destination, loaded CONCURRENTLY with the
 * alert rules rather than after them. This matters because "no property" and
 * "still loading" are different claims -- gating the section on the
 * (usually faster) schedule load would flash the "no property" text while
 * /me is still in flight, and a schedule 500 must not hide property
 * management entirely. "Αλλαγή καταλύματος" re-enters `/setup` at step 1 via
 * `?change=1` (setup.guard.ts lets a complete user through on that query
 * param); "Διαγραφή καταλύματος" demands the exact property name be typed
 * before the DELETE fires, then clears every property-scoped workflow key
 * (destination, drafts, competitor selection, room category -- thirteen in
 * all) that would otherwise leak into whatever property comes next or
 * auto-fire a paid Booking search for the deleted name, and restarts the
 * wizard. A /me failure here only shows a neutral error line in this
 * section -- it is independent of the schedule/alerts forms above and never
 * blocks them.
 */
@Component({
  selector: "app-settings-page",
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, NotificationBellComponent, NotificationToastsComponent],
  template: `
    <main class="page-shell">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate — Ρυθμίσεις</p>
          <h1>Προγραμματισμένες αναζητήσεις</h1>
        </div>
        <div class="header-actions">
          <a routerLink="/map">Χάρτης</a>
          <a routerLink="/pricing">Τιμολόγηση</a>
          <app-notification-bell />
        </div>
      </header>

      <section class="page-body">
        <div *ngIf="status() === 'loading'" class="progress-panel">
          <strong>Φόρτωση προγράμματος</strong>
          <span>Ανάκτηση της ρύθμισης των επαναλαμβανόμενων αναζητήσεών σας.</span>
        </div>
        <div *ngIf="status() === 'error'" class="alert alert-error">{{ error() }}</div>

        <form *ngIf="status() === 'ready'" id="schedule" class="panel settings-form" (ngSubmit)="save()">
          <div class="section-title">
            <h2>Επαναλαμβανόμενες αναζητήσεις ανταγωνιστών</h2>
          </div>

          <label class="checkbox-row settings-toggle">
            <input type="checkbox" name="enabled" [ngModel]="form().enabled" (ngModelChange)="updateScheduleField('enabled', $event)">
            <span>Αυτόματη αναζήτηση ανταγωνιστών</span>
          </label>
          <div class="alert">
            Όταν είναι ενεργή, το RoomRate ελέγχει μόνο του τις τιμές των ανταγωνιστών σας σε
            τακτά διαστήματα. Έτσι βλέπετε πώς κινείται η αγορά από μέρα σε μέρα και οι προτάσεις
            τιμής γίνονται πιο αξιόπιστες. Κάθε έλεγχος έχει μικρό κόστος — συνήθως αρκεί μία
            φορά την ημέρα.
          </div>

          <div class="two-cols">
            <label>
              <span>Κάθε πόσες ώρες</span>
              <input class="roomrate-input" type="number" name="frequency_hours" min="1" [ngModel]="form().frequency_hours" (ngModelChange)="updateScheduleField('frequency_hours', $event)">
              <small class="muted field-hint">24 = μία φορά την ημέρα</small>
            </label>
            <label>
              <span>Ώρα ελέγχου (ώρα Ελλάδας)</span>
              <!-- Shown and typed in Greek time, the hour the API stores (hour_local). -->
              <input class="roomrate-input" type="number" name="hour_athens" min="0" max="23" [ngModel]="form().hour_athens" (ngModelChange)="updateScheduleField('hour_athens', $event)">
            </label>
          </div>
          <div class="two-cols">
            <label>
              <span>Ημέρες πριν από την άφιξη</span>
              <input class="roomrate-input" type="number" name="lead_days" min="0" [ngModel]="form().lead_days" (ngModelChange)="updateScheduleField('lead_days', $event)">
            </label>
            <label>
              <span>Διανυκτερεύσεις ανά διαμονή</span>
              <input class="roomrate-input" type="number" name="nights" min="1" [ngModel]="form().nights" (ngModelChange)="updateScheduleField('nights', $event)">
            </label>
          </div>
          <div class="three-cols">
            <label>
              <span>Ενήλικες</span>
              <input class="roomrate-input" type="number" name="adults" min="1" [ngModel]="form().adults" (ngModelChange)="updateScheduleField('adults', $event)">
            </label>
            <label>
              <span>Παιδιά</span>
              <input class="roomrate-input" type="number" name="children" min="0" [ngModel]="form().children" (ngModelChange)="updateScheduleField('children', $event)">
            </label>
            <label>
              <span>Δωμάτια</span>
              <input class="roomrate-input" type="number" name="rooms" min="1" [ngModel]="form().rooms" (ngModelChange)="updateScheduleField('rooms', $event)">
            </label>
          </div>

          <div class="stats-grid settings-readonly">
            <div>
              <span>Τελευταία εκτέλεση</span>
              <strong>{{ lastRunLabel() }}</strong>
            </div>
            <div>
              <span>Συνεχόμενες αποτυχίες</span>
              <strong>{{ config()?.consecutive_failures ?? 0 }}</strong>
            </div>
          </div>
          <p *ngIf="(config()?.consecutive_failures ?? 0) >= 3" class="muted">
            Μετά από επαναλαμβανόμενες αποτυχίες ο προγραμματισμός διακόπτεται αυτόματα. Διορθώστε
            την αιτία και ενεργοποιήστε τον ξανά από εδώ.
          </p>

          <button class="primary-button" type="submit" [disabled]="saveStatus() === 'loading'">
            {{ saveStatus() === "loading" ? "Γίνεται αποθήκευση..." : "Αποθήκευση προγράμματος" }}
          </button>
          <div *ngIf="saveMessage()" class="save-feedback" [class.save-feedback-error]="saveHasError()">
            {{ saveMessage() }}
          </div>
        </form>

        <section class="panel settings-form">
          <div class="section-title">
            <h2>Ειδοποιήσεις τιμών ανταγωνιστών</h2>
          </div>
          <p class="muted">
            Δημιουργήστε κανόνες για όλο τον λογαριασμό, για αυξήσεις, μειώσεις ή και τις δύο
            κατευθύνσεις των τιμών των ανταγωνιστών.
          </p>
          <form class="three-cols" (ngSubmit)="createRule()">
            <label>
              <span>Όριο μεταβολής (%)</span>
              <input class="roomrate-input" type="number" name="alert_threshold" min="0.1" max="100" step="0.1" [ngModel]="ruleForm().threshold_pct" (ngModelChange)="updateRuleForm('threshold_pct', $event)">
            </label>
            <label>
              <span>Κατεύθυνση</span>
              <select class="roomrate-input" name="alert_direction" [ngModel]="ruleForm().direction" (ngModelChange)="updateRuleForm('direction', $event)">
                <option value="any">Άνοδος ή πτώση</option>
                <option value="drop">Μόνο πτώση</option>
                <option value="rise">Μόνο άνοδος</option>
              </select>
            </label>
            <button class="primary-button" type="submit" [disabled]="rulesBusy()">Προσθήκη κανόνα ειδοποίησης</button>
          </form>
          <div *ngIf="rulesStatus() === 'loading'" class="notification-empty">Φόρτωση κανόνων ειδοποιήσεων...</div>
          <div *ngIf="rulesError()" class="alert alert-error">{{ rulesError() }}</div>
          <div *ngIf="rulesStatus() === 'ready' && !rules().length" class="notification-empty">
            Δεν υπάρχουν προσαρμοσμένοι κανόνες. Το RoomRate χρησιμοποιεί το προεπιλεγμένο όριο.
          </div>
          <div class="notification-list">
            <article *ngFor="let rule of rules()" class="notification-item">
              <div class="notification-item-top">
                <strong>{{ directionLabel(rule.direction) }} από {{ rule.threshold_pct }}%</strong>
                <span>{{ rule.is_active ? "Ενεργός" : "Σε παύση" }}</span>
              </div>
              <div class="auth-actions">
                <button class="text-button" type="button" [disabled]="rulesBusy()" (click)="toggleRule(rule)">
                  {{ rule.is_active ? "Παύση" : "Ενεργοποίηση" }}
                </button>
                <button class="text-button" type="button" [disabled]="rulesBusy()" (click)="deleteRule(rule)">
                  Διαγραφή
                </button>
              </div>
            </article>
          </div>
        </section>

        <div *ngIf="propertyStatus() === 'loading'" class="progress-panel" data-testid="property-section-loading">
          <strong>Φόρτωση καταλύματος</strong>
        </div>
        <section *ngIf="propertyStatus() !== 'loading'" class="panel property-section">
          <div class="section-title">
            <h2>Το κατάλυμά μου</h2>
          </div>
          <div *ngIf="propertyStatus() === 'error'" class="alert alert-error">
            Δεν ήταν δυνατή η φόρτωση του καταλύματος.
          </div>
          <ng-container *ngIf="propertyStatus() === 'ready'">
            <div *ngIf="propertyName(); else noProperty" class="property-details">
              <p><strong>{{ propertyName() }}</strong></p>
              <p class="muted">{{ propertyDestination() }}</p>
              <div class="setup-actions">
                <button class="secondary-button" type="button" (click)="changeProperty()">Αλλαγή καταλύματος</button>
                <button class="danger-button" type="button" [attr.aria-expanded]="deleteRequested()"
                        (click)="deleteRequested.set(true)">Διαγραφή καταλύματος</button>
              </div>
              <div *ngIf="deleteRequested()" class="delete-confirm">
                <p>
                  Η διαγραφή αφαιρεί οριστικά: τα δωμάτια του καταλύματος, τους παρακολουθούμενους
                  ανταγωνιστές και τους κανόνες ειδοποιήσεων. Διατηρείται το ιστορικό τιμών της αγοράς.
                </p>
                <label>
                  <span>Πληκτρολογήστε το όνομα του καταλύματος για επιβεβαίωση</span>
                  <input class="roomrate-input" data-testid="delete-confirm-input"
                         name="deleteConfirm" [ngModel]="deleteConfirmText()" (ngModelChange)="deleteConfirmText.set($event)">
                </label>
                <div class="setup-actions">
                  <button class="danger-button" type="button"
                          [disabled]="deleteConfirmText().trim() !== propertyName().trim() || deleting()"
                          (click)="deleteProperty()">
                    {{ deleting() ? "Γίνεται διαγραφή..." : "Οριστική διαγραφή" }}
                  </button>
                  <button class="text-button" type="button" [disabled]="deleting()"
                          (click)="deleteRequested.set(false); deleteConfirmText.set(''); deleteError.set('')">Άκυρο</button>
                </div>
                <div *ngIf="deleteError()" class="alert alert-error" aria-live="assertive">{{ deleteError() }}</div>
              </div>
            </div>
            <ng-template #noProperty>
              <p class="muted">Δεν υπάρχει συνδεδεμένο κατάλυμα. Ο οδηγός ρύθμισης θα σας καθοδηγήσει.</p>
            </ng-template>
          </ng-container>
        </section>

        <footer class="auth-actions settings-legal">
          <a routerLink="/privacy">Πολιτική Απορρήτου</a>
          <a routerLink="/terms">Όροι Χρήσης</a>
        </footer>
      </section>

      <app-notification-toasts />
    </main>
  `,
  styles: [`
    .danger-button { background: #b91c1c; color: #fff; border: none; border-radius: 0.375rem; padding: 0.5rem 0.9rem; cursor: pointer; }
    .danger-button:disabled { opacity: 0.5; cursor: not-allowed; }
    .setup-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .delete-confirm { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.75rem; max-width: 28rem; }
    .settings-legal { gap: 1rem; }
    .field-hint { display: block; margin-top: 4px; font-size: 12px; }
    .settings-legal a { color: var(--muted); font-size: 0.85rem; }
  `],
})
export class SettingsPageComponent implements OnInit {
  readonly status = signal<LoadingState>("loading");
  readonly saveStatus = signal<LoadingState>("idle");
  readonly error = signal("");
  readonly saveMessage = signal("");
  readonly saveHasError = signal(false);
  readonly config = signal<ScheduleConfig | null>(null);
  readonly form = signal<ScheduleForm>({
    enabled: false,
    frequency_hours: 24,
    hour_athens: 8,
    lead_days: 30,
    nights: 3,
    adults: 2,
    children: 0,
    rooms: 1,
  });
  readonly rules = signal<AlertRule[]>([]);
  readonly rulesStatus = signal<LoadingState>("loading");
  readonly rulesError = signal("");
  readonly rulesBusy = signal(false);
  readonly ruleForm = signal<AlertRuleCreate>({
    rule_type: "price_change",
    threshold_pct: 10,
    direction: "any",
    is_active: true,
  });
  readonly propertyStatus = signal<LoadingState>("loading");
  readonly propertyName = signal("");
  readonly propertyDestination = signal("");
  readonly deleteRequested = signal(false);
  readonly deleteConfirmText = signal("");
  readonly deleting = signal(false);
  readonly deleteError = signal("");

  constructor(
    private readonly api: ApiClientService,
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const config = await this.api.get<ScheduleConfig>("/api/v1/schedule");
      this.applyConfig(config);
      this.status.set("ready");
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση της ρύθμισης του προγράμματος.");
      this.status.set("error");
    }
    // Concurrent, not sequential: the property section has its own
    // propertyStatus load state now, so it no longer needs to wait its turn
    // behind the rules fetch.
    await Promise.all([this.loadRules(), this.loadProperty()]);
  }

  async save(): Promise<void> {
    this.saveMessage.set("");
    this.saveHasError.set(false);
    const problem = this.validate();
    if (problem) {
      this.saveMessage.set(problem);
      this.saveHasError.set(true);
      return;
    }
    this.saveStatus.set("loading");
    try {
      const form = this.form();
      const body: ScheduleConfigUpdate = {
        enabled: Boolean(form.enabled),
        frequency_hours: Math.round(Number(form.frequency_hours)),
        hour_local: Math.round(Number(form.hour_athens)),
        // Only for an API that predates hour_local (a rollback, or this bundle
        // deployed first); the current API ignores it next to hour_local.
        hour_utc: athensHourToUtc(Math.round(Number(form.hour_athens))),
        lead_days: Math.round(Number(form.lead_days)),
        nights: Math.round(Number(form.nights)),
        adults: Math.round(Number(form.adults)),
        children: Math.round(Number(form.children)),
        rooms: Math.round(Number(form.rooms)),
      };
      const updated = await this.api.put<ScheduleConfig>("/api/v1/schedule", body);
      this.applyConfig(updated);
      this.saveStatus.set("ready");
      this.saveMessage.set(updated.enabled
        ? "Η αυτόματη αναζήτηση ενεργοποιήθηκε. Θα βλέπετε νέες τιμές ανταγωνιστών σε κάθε έλεγχο."
        : "Το πρόγραμμα αποθηκεύτηκε. Οι επαναλαμβανόμενες αναζητήσεις είναι απενεργοποιημένες.");
    } catch (error) {
      this.saveStatus.set("error");
      this.saveMessage.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η αποθήκευση του προγράμματος.");
      this.saveHasError.set(true);
    }
  }

  updateScheduleField<K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]): void {
    this.form.update((form) => ({ ...form, [key]: value }));
  }

  updateRuleForm<K extends keyof AlertRuleCreate>(key: K, value: AlertRuleCreate[K]): void {
    this.ruleForm.update((form) => ({ ...form, [key]: value }));
  }

  async createRule(): Promise<void> {
    const threshold = Number(this.ruleForm().threshold_pct);
    if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 100) {
      this.rulesError.set("Το όριο ειδοποίησης πρέπει να είναι μεγαλύτερο από 0 και το πολύ 100.");
      return;
    }
    this.rulesBusy.set(true);
    this.rulesError.set("");
    try {
      const created = await this.api.post<AlertRule>("/api/v1/notifications/rules", {
        ...this.ruleForm(),
        threshold_pct: threshold,
      });
      this.rules.update((rules) => [created, ...rules]);
      this.ruleForm.set({
        rule_type: "price_change",
        threshold_pct: 10,
        direction: "any",
        is_active: true,
      });
      this.rulesStatus.set("ready");
    } catch (error) {
      this.rulesError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η δημιουργία του κανόνα ειδοποίησης.");
    } finally {
      this.rulesBusy.set(false);
    }
  }

  async toggleRule(rule: AlertRule): Promise<void> {
    this.rulesBusy.set(true);
    this.rulesError.set("");
    try {
      const updated = await this.api.put<AlertRule>(`/api/v1/notifications/rules/${rule.id}`, {
        is_active: !rule.is_active,
      });
      this.rules.update((rules) => rules.map((entry) => entry.id === updated.id ? updated : entry));
    } catch (error) {
      this.rulesError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η ενημέρωση του κανόνα ειδοποίησης.");
    } finally {
      this.rulesBusy.set(false);
    }
  }

  async deleteRule(rule: AlertRule): Promise<void> {
    if (!window.confirm(`Διαγραφή του κανόνα ειδοποίησης ${rule.threshold_pct}%;`)) {
      return;
    }
    this.rulesBusy.set(true);
    this.rulesError.set("");
    try {
      await this.api.delete(`/api/v1/notifications/rules/${rule.id}`);
      this.rules.update((rules) => rules.filter((entry) => entry.id !== rule.id));
    } catch (error) {
      this.rulesError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η διαγραφή του κανόνα ειδοποίησης.");
    } finally {
      this.rulesBusy.set(false);
    }
  }

  /** Re-enters the wizard at step 1 in pick mode (setup.guard.ts honors `?change=1`). */
  changeProperty(): void {
    void this.router.navigate(["/setup"], { queryParams: { change: "1" } });
  }

  /**
   * Deletes the owned property, gated in the template by typing the exact
   * property name. The backend cascades (rooms, competitors, alert rules)
   * and keeps market price history; this clears every matching client-side
   * workflow key (destination, drafts, competitor selection, room category)
   * so no stale value -- including the deleted property's own name/location,
   * which would otherwise auto-fire a paid Booking search on the wizard's
   * next mount -- can leak into the next property, then restarts the wizard.
   *
   * `deleting` is set FIRST and reset in `finally` so every exit path
   * (including the early "no property" return) clears the busy flag; the
   * navigation happens AFTER the try/catch on a `deleted` success flag so a
   * navigation failure can never be mislabeled as a deletion failure.
   */
  async deleteProperty(): Promise<void> {
    this.deleting.set(true);
    this.deleteError.set("");
    let deleted = false;
    try {
      const me = await this.onboarding.currentUser();
      if (!me.owned_property_id) {
        return;
      }
      await this.onboarding.deleteOwnedProperty(me.owned_property_id);
      // Property-scoped state is gone with the property.
      this.workflow.set("ownedPropertyId", "");
      this.workflow.set("propertyName", "");
      this.workflow.set("destination", "");
      this.workflow.set("rawDestination", "");
      this.workflow.set("canonicalDestination", "");
      this.workflow.set("roomTypeCategory", "");
      this.workflow.set("pendingCandidate", "");
      this.workflow.set("pendingDiscoveryJobId", "");
      this.workflow.set("lastCompetitorJobId", "");
      this.workflow.set("lastCompetitorSelection", "");
      this.workflow.set("draftPropertyName", "");
      this.workflow.set("draftLocation", "");
      this.workflow.set("pendingSetupError", "");
      deleted = true;
    } catch (err) {
      this.deleteError.set(err instanceof Error ? err.message : "Η διαγραφή απέτυχε.");
    } finally {
      this.deleting.set(false);
    }
    if (deleted) {
      await this.router.navigateByUrl("/setup");
    }
  }

  directionLabel(direction: AlertRule["direction"]): string {
    return direction === "drop" ? "Πτώση τιμής" : direction === "rise" ? "Άνοδος τιμής" : "Μεταβολή τιμής";
  }

  lastRunLabel(): string {
    const lastRunAt = this.config()?.last_run_at;
    if (!lastRunAt) {
      return "Ποτέ";
    }
    const parsed = new Date(lastRunAt);
    if (Number.isNaN(parsed.getTime())) {
      return "Ποτέ";
    }
    // el-GR, not en-GB: one date format across a Greek UI (pricing already
    // formats with el-GR).
    return parsed.toLocaleString("el-GR", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  private applyConfig(config: ScheduleConfig): void {
    this.config.set(config);
    this.form.set({
      enabled: config.enabled,
      frequency_hours: config.frequency_hours,
      // The owner's hour as stored, the same all year. An API without
      // hour_local sends only the UTC hour: read it with today's offset.
      hour_athens: config.hour_local ?? utcHourToAthens(config.hour_utc),
      lead_days: config.lead_days,
      nights: config.nights,
      adults: config.adults,
      children: config.children,
      rooms: config.rooms,
    });
  }

  private async loadRules(): Promise<void> {
    this.rulesStatus.set("loading");
    this.rulesError.set("");
    try {
      this.rules.set(await this.api.get<AlertRule[]>("/api/v1/notifications/rules"));
      this.rulesStatus.set("ready");
    } catch (error) {
      this.rulesError.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση των κανόνων ειδοποιήσεων.");
      this.rulesStatus.set("error");
    }
  }

  /**
   * Independent of the schedule/rules loads above (own `propertyStatus`, run
   * concurrently with `loadRules` from `ngOnInit`): a /me failure here must
   * not flip `status()` to "error" and hide the whole page. `propertyStatus`
   * reaches "ready" whenever the /me fetch itself succeeds -- including the
   * no-property case, which is a claim this method CAN make once /me
   * answered -- and "error" only when /me failed, since "no property" is a
   * claim that requires /me to have actually answered.
   */
  private async loadProperty(): Promise<void> {
    this.propertyStatus.set("loading");
    try {
      const me = await this.onboarding.currentUser();
      // settings is the one page that writes workflow keys (on delete)
      // without having gone through /setup or /map first in this session.
      this.workflow.bindToSubject(me.auth_subject);
      this.propertyName.set(me.property_name || "");
      this.propertyDestination.set(me.raw_destination || me.destination || "");
      this.propertyStatus.set("ready");
    } catch {
      this.propertyName.set("");
      this.propertyDestination.set("");
      this.propertyStatus.set("error");
    }
  }

  /** Mirrors the backend's 422 validation so users get instant feedback. */
  private validate(): string | null {
    const numeric = (value: unknown): number => Number(value);
    const form = this.form();
    if (!Number.isFinite(numeric(form.frequency_hours)) || numeric(form.frequency_hours) < 1) {
      return "Η συχνότητα πρέπει να είναι τουλάχιστον 1 ώρα.";
    }
    if (!Number.isFinite(numeric(form.hour_athens)) || numeric(form.hour_athens) < 0 || numeric(form.hour_athens) > 23) {
      return "Η ώρα ελέγχου πρέπει να είναι από 0 έως 23.";
    }
    if (!Number.isFinite(numeric(form.lead_days)) || numeric(form.lead_days) < 0) {
      return "Οι ημέρες πριν από την άφιξη δεν μπορούν να είναι αρνητικές.";
    }
    if (!Number.isFinite(numeric(form.nights)) || numeric(form.nights) < 1) {
      return "Οι διανυκτερεύσεις πρέπει να είναι τουλάχιστον 1.";
    }
    if (!Number.isFinite(numeric(form.adults)) || numeric(form.adults) < 1) {
      return "Οι ενήλικες πρέπει να είναι τουλάχιστον 1.";
    }
    if (!Number.isFinite(numeric(form.children)) || numeric(form.children) < 0) {
      return "Τα παιδιά δεν μπορούν να είναι αρνητικά.";
    }
    if (!Number.isFinite(numeric(form.rooms)) || numeric(form.rooms) < 1) {
      return "Τα δωμάτια πρέπει να είναι τουλάχιστον 1.";
    }
    return null;
  }
}
