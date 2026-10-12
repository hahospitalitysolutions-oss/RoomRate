/**
 * «Ζήτηση νησιού»: whether RoomRate reads the ferries into the owner's island
 * and the flights landing at its airport each day, at which Greek hour, and
 * «Συλλογή τώρα» (GET/PUT /api/v1/demand/settings, POST /api/v1/demand/collect).
 *
 * Two modes. `settings`: both switches, the hour, save, collect now and the
 * last and next runs. `onboarding` (setup step 3): one button that switches
 * both on and asks for the first collection now; nothing at all is shown for
 * a property that is not on an island, where neither applies.
 *
 * The worker runs the collection within minutes of a request and once a day
 * at the hour (api/scheduler.py run_demand_tick).
 */
import { CommonModule } from "@angular/common";
import { Component, computed, effect, input, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";

import { ApiClientService } from "../services/api-client.service";
import type { DemandCollectResponse, DemandRun, DemandSettings, DemandSettingsUpdate } from "../types/demand";

type LoadState = "loading" | "ready" | "error";

const GREEK_TIME = new Intl.DateTimeFormat("el-GR", {
  timeZone: "Europe/Athens",
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const GREEK_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Athens" });
const GREEK_CLOCK = new Intl.DateTimeFormat("el-GR", {
  timeZone: "Europe/Athens",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

@Component({
  selector: "app-demand-settings",
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <ng-container *ngIf="mode() === 'settings'; else onboardingMode">
      <section class="panel settings-form demand-settings" id="demand" data-testid="demand-settings">
        <div class="section-title">
          <h2>Ζήτηση νησιού: πλοία και πτήσεις</h2>
        </div>
        <p *ngIf="state() === 'loading'" class="muted">Φόρτωση…</p>
        <div *ngIf="state() === 'error'" class="alert alert-error">{{ error() }}</div>

        <ng-container *ngIf="state() === 'ready' && current() as demand">
          <p *ngIf="!applies()" class="muted" data-testid="demand-not-island">
            Το κατάλυμά σας δεν είναι σε νησί, οπότε τα πλοία και οι πτήσεις δεν δείχνουν ζήτηση για τον προορισμό σας.
          </p>
          <ng-container *ngIf="applies()">
            <div class="alert">{{ explanation() }}</div>
            <label *ngIf="demand.ferries_available" class="checkbox-row settings-toggle">
              <input type="checkbox" name="ferries_enabled" data-testid="demand-ferries"
                     [ngModel]="ferries()" (ngModelChange)="ferries.set($event)">
              <span>Πλοία προς το νησί ({{ demand.island }})</span>
            </label>
            <label *ngIf="demand.flights_available" class="checkbox-row settings-toggle">
              <input type="checkbox" name="flights_enabled" data-testid="demand-flights"
                     [ngModel]="flights()" (ngModelChange)="flights.set($event)">
              <span>Πτήσεις στο αεροδρόμιο {{ demand.airport_iata }}</span>
            </label>
            <div class="two-cols">
              <label>
                <span>Ώρα ημερήσιας συλλογής (ώρα Ελλάδας)</span>
                <input class="roomrate-input" type="number" name="demand_hour" min="0" max="23" data-testid="demand-hour"
                       [ngModel]="hour()" (ngModelChange)="hour.set($event)">
              </label>
            </div>

            <div class="stats-grid settings-readonly" data-testid="demand-status">
              <div *ngIf="demand.ferries_available">
                <span>Τελευταία συλλογή πλοίων</span>
                <strong>{{ runLabel(demand.ferries_last_run) }}</strong>
                <small *ngIf="runDetail(demand.ferries_last_run) as detail" class="muted demand-run-detail">{{ detail }}</small>
              </div>
              <div *ngIf="demand.flights_available">
                <span>Τελευταία συλλογή πτήσεων</span>
                <strong>{{ runLabel(demand.flights_last_run) }}</strong>
                <small *ngIf="runDetail(demand.flights_last_run) as detail" class="muted demand-run-detail">{{ detail }}</small>
              </div>
              <div>
                <span>Επόμενη συλλογή</span>
                <strong data-testid="demand-next">{{ nextLabel() }}</strong>
              </div>
            </div>

            <div class="setup-actions">
              <button class="primary-button" type="button" [disabled]="busy()" (click)="save()">
                {{ busy() ? "Γίνεται αποθήκευση..." : "Αποθήκευση" }}
              </button>
              <button class="secondary-button" type="button" data-testid="demand-collect-now"
                      [disabled]="busy() || !anyOn() || demand.collection_pending" (click)="collectNow()">
                {{ demand.collection_pending ? "Η συλλογή ξεκινά σε λίγα λεπτά" : "Συλλογή τώρα" }}
              </button>
            </div>
          </ng-container>
          <div *ngIf="message()" class="save-feedback" [class.save-feedback-error]="messageIsError()" aria-live="polite">
            {{ message() }}
          </div>
        </ng-container>
      </section>
    </ng-container>

    <ng-template #onboardingMode>
      <section *ngIf="state() === 'ready' && applies() && current() as demand" class="panel demand-onboarding" data-testid="demand-onboarding">
        <div class="section-title">
          <h2>Ζήτηση νησιού</h2>
        </div>
        <p class="muted">{{ explanation() }}</p>
        <ng-container *ngIf="!activated(); else activatedNote">
          <div class="setup-actions">
            <button class="primary-button" type="button" data-testid="demand-activate" [disabled]="busy()" (click)="activate()">
              {{ busy() ? "Ενεργοποίηση..." : "Ενεργοποίηση και πρώτη συλλογή τώρα" }}
            </button>
          </div>
          <p class="muted demand-hint">Κάθε μέρα στις {{ hourLabel(demand.hour_local) }}· αλλάζει από τις Ρυθμίσεις.</p>
        </ng-container>
        <ng-template #activatedNote>
          <p class="save-feedback" data-testid="demand-activated" aria-live="polite">
            Ενεργή. Η πρώτη συλλογή ξεκινά μέσα σε λίγα λεπτά και μετά γίνεται κάθε μέρα στις {{ hourLabel(demand.hour_local) }}.
            <a class="text-button" routerLink="/settings" fragment="demand">Ρυθμίσεις</a>
          </p>
        </ng-template>
        <div *ngIf="messageIsError() && message()" class="alert alert-error">{{ message() }}</div>
      </section>
    </ng-template>
  `,
  styles: [`
    .demand-settings .setup-actions, .demand-onboarding .setup-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .demand-settings .setup-actions button, .demand-onboarding .setup-actions button { width: auto; }
    .demand-run-detail { display: block; margin-top: 4px; font-size: 13px; }
    .demand-hint { margin: 0.4rem 0 0; font-size: 0.82rem; }
    .demand-onboarding { margin-top: 1rem; }
  `],
})
export class DemandSettingsComponent {
  readonly ownedPropertyId = input.required<string>();
  readonly mode = input<"settings" | "onboarding">("settings");

  readonly state = signal<LoadState>("loading");
  readonly current = signal<DemandSettings | null>(null);
  readonly ferries = signal(true);
  readonly flights = signal(true);
  readonly hour = signal<number>(8);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly message = signal("");
  readonly messageIsError = signal(false);
  readonly activated = signal(false);

  readonly applies = computed(() => {
    const demand = this.current();
    return Boolean(demand && (demand.ferries_available || demand.flights_available));
  });
  readonly anyOn = computed(() => {
    const demand = this.current();
    return Boolean(demand && ((demand.ferries_available && demand.ferries_enabled) || (demand.flights_available && demand.flights_enabled)));
  });

  constructor(private readonly api: ApiClientService) {
    effect(() => {
      const id = this.ownedPropertyId();
      if (id) {
        void this.load(id);
      }
    });
  }

  explanation(): string {
    const demand = this.current();
    if (!demand) return "";
    const sources = [
      demand.ferries_available ? `τα πλοία προς το νησί σας (${demand.island})` : "",
      demand.flights_available ? `τις πτήσεις στο αεροδρόμιο ${demand.airport_iata}` : "",
    ].filter(Boolean).join(" και ");
    return `Κάθε μέρα το RoomRate διαβάζει ${sources}: όταν γεμίζουν ή προστίθενται δρομολόγια, `
      + "η πρόταση τιμής το λαμβάνει υπόψη. Δεν έχει χρέωση για εσάς.";
  }

  runLabel(run: DemandRun | null): string {
    if (!run) return "Δεν έχει γίνει ακόμη";
    return GREEK_TIME.format(new Date(run.finished_at ?? run.started_at));
  }

  /** Under the time: still running, nothing saved (the source was down), or how much. */
  runDetail(run: DemandRun | null): string {
    if (!run) return "";
    if (!run.finished_at) return "Τρέχει τώρα";
    return run.saved ? `${run.saved} νέα δεδομένα` : "Δεν ήρθαν δεδομένα· δοκιμάστε «Συλλογή τώρα»";
  }

  nextLabel(): string {
    const demand = this.current();
    if (!demand?.next_collection_at || !this.anyOn()) return "Απενεργοποιημένη";
    if (demand.collection_pending) return "Σε λίγα λεπτά";
    const next = new Date(demand.next_collection_at);
    if (next.getTime() <= Date.now() + 60_000) return "Σε λίγα λεπτά";
    const today = GREEK_DAY.format(new Date());
    const tomorrow = GREEK_DAY.format(new Date(Date.now() + 86_400_000));
    const day = GREEK_DAY.format(next);
    const time = GREEK_CLOCK.format(next);
    return day === today ? `Σήμερα ${time}` : day === tomorrow ? `Αύριο ${time}` : GREEK_TIME.format(next);
  }

  hourLabel(hour: number): string {
    return `${String(hour).padStart(2, "0")}:00`;
  }

  async save(): Promise<void> {
    const hour = Number(this.hour());
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
      this.say("Η ώρα πρέπει να είναι ακέραιος από 0 έως 23.", true);
      return;
    }
    const saved = await this.put({ ferries_enabled: this.ferries(), flights_enabled: this.flights(), hour_local: hour });
    if (saved) {
      this.say(this.anyOn()
        ? `Αποθηκεύτηκε. Η συλλογή γίνεται κάθε μέρα στις ${this.hourLabel(saved.hour_local)}.`
        : "Αποθηκεύτηκε. Η ζήτηση νησιού είναι απενεργοποιημένη.", false);
    }
  }

  async collectNow(): Promise<void> {
    this.busy.set(true);
    this.say("", false);
    try {
      const params = new URLSearchParams({ owned_property_id: this.ownedPropertyId() });
      const result = await this.api.post<DemandCollectResponse>(`/api/v1/demand/collect?${params}`, {});
      this.apply(result.settings);
      this.say(result.message, false);
    } catch (error) {
      this.say(error instanceof Error ? error.message : "Δεν ήταν δυνατό να ξεκινήσει η συλλογή.", true);
    } finally {
      this.busy.set(false);
    }
  }

  async activate(): Promise<void> {
    const demand = this.current();
    if (!demand) return;
    const saved = await this.put({
      ferries_enabled: true,
      flights_enabled: true,
      hour_local: demand.hour_local,
      collect_now: true,
    });
    if (saved) {
      this.activated.set(true);
    }
  }

  private async load(id: string): Promise<void> {
    this.state.set("loading");
    try {
      const params = new URLSearchParams({ owned_property_id: id });
      this.apply(await this.api.get<DemandSettings>("/api/v1/demand/settings", params));
      this.state.set("ready");
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση της ζήτησης νησιού.");
      this.state.set("error");
    }
  }

  private async put(body: DemandSettingsUpdate): Promise<DemandSettings | null> {
    this.busy.set(true);
    this.say("", false);
    try {
      const params = new URLSearchParams({ owned_property_id: this.ownedPropertyId() });
      const saved = await this.api.put<DemandSettings>(`/api/v1/demand/settings?${params}`, body);
      this.apply(saved);
      return saved;
    } catch (error) {
      this.say(error instanceof Error ? error.message : "Δεν ήταν δυνατή η αποθήκευση.", true);
      return null;
    } finally {
      this.busy.set(false);
    }
  }

  private apply(demand: DemandSettings): void {
    this.current.set(demand);
    this.ferries.set(demand.ferries_enabled);
    this.flights.set(demand.flights_enabled);
    this.hour.set(demand.hour_local);
  }

  private say(text: string, isError: boolean): void {
    this.message.set(text);
    this.messageIsError.set(isError);
  }
}
