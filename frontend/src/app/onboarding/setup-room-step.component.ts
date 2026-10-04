import { CommonModule } from "@angular/common";
import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from "@angular/core";

import { EmptyStateComponent } from "../components/empty-state.component";
import { IconComponent } from "../components/icon.component";
import { OnboardingService } from "../services/onboarding.service";
import { OwnedPropertyRoomType, ScrapeJobResponse } from "../types/market";
import { remainingDeadlineMs, settleBeforeDeadline } from "./poll-deadline";

const DISCOVERY_POLL_INTERVAL_MS = 5_000;
const DISCOVERY_TIMEOUT_MS = 5 * 60_000;

/** «Τιμές για 2 ενήλικες, μέση τιμή ανά διανυκτέρευση για 3/11–7/11/2026.» */
function describePriceBasis(job: Pick<ScrapeJobResponse, "check_in" | "check_out" | "adults" | "children" | "rooms">): string {
  if (!job.check_in || !job.check_out || !job.adults) {
    return "";
  }
  const day = (value: string) => {
    const [year, month, date] = value.split("-").map(Number);
    return { year, label: `${date}/${month}` };
  };
  const checkIn = day(job.check_in);
  const checkOut = day(job.check_out);
  const party = [
    job.adults === 1 ? "1 ενήλικα" : `${job.adults} ενήλικες`,
    job.children ? (job.children === 1 ? "1 παιδί" : `${job.children} παιδιά`) : "",
    job.rooms > 1 ? `${job.rooms} δωμάτια` : "",
  ].filter(Boolean).join(", ");
  return `Τιμές για ${party}, μέση τιμή ανά διανυκτέρευση για ${checkIn.label}–${checkOut.label}/${checkOut.year}, όπως τις έδειξε το Booking.`;
}

const LOAD_ERROR = "Δεν μπορέσαμε να φορτώσουμε τα δωμάτια. Δοκιμάστε ξανά.";
const SAVE_ERROR = "Δεν μπορέσαμε να αποθηκεύσουμε το δωμάτιο. Δοκιμάστε ξανά.";
const TIMEOUT_ERROR = "Η ανακάλυψη δωματίων διαρκεί ασυνήθιστα πολύ. Δοκιμάστε ξανά σε λίγο.";
type FailedAction = "load" | "save" | null;

/**
 * Step 2 of `/setup`: waits for discovery and persists the room category that
 * will be compared with the market.
 *
 * Contract: `ownedPropertyId` identifies the property; `discoveryJobId` is
 * polled before its catalog read. `selected` fires only after the PUT and
 * `skipped` leaves setup incomplete. Destroy cancels async work, preventing
 * later rendered-state writes or output emissions.
 */
@Component({
  selector: "app-setup-room-step",
  standalone: true,
  imports: [CommonModule, EmptyStateComponent, IconComponent],
  template: `
    <section class="panel setup-step">
      <div *ngIf="waiting()" class="progress-panel" data-testid="discovery-progress" aria-live="polite">
        <strong>{{ milestone() }}</strong>
        <span>Διαβάζουμε τον κατάλογο δωματίων του καταλύματός σας από το Booking. Συνήθως χρειάζονται 1–3 λεπτά.</span>
      </div>
      <ng-container *ngIf="!waiting()">
        <app-empty-state
          *ngIf="!rooms().length && !error()"
          icon="bed"
          title="Δεν βρέθηκαν δωμάτια"
          explanation="Το Booking δεν επέστρεψε κατάλογο δωματίων για τις ημερομηνίες που ελέγξαμε. Μπορείτε να δοκιμάσετε ξανά ή να προχωρήσετε στον χάρτη και να επιστρέψετε αργότερα."
          actionLabel="Δοκιμάστε ξανά"
          (action)="reload()"
        ></app-empty-state>
        <div *ngIf="!rooms().length && !error()" class="setup-actions">
          <button class="text-button" type="button" (click)="skip()">Συνέχεια στον χάρτη</button>
        </div>
        <p class="muted room-price-basis" *ngIf="rooms().length && priceBasis()" data-testid="room-price-basis">
          {{ priceBasis() }}
        </p>
        <ul class="room-list" *ngIf="rooms().length">
          <li *ngFor="let room of rooms()">
            <button type="button" class="candidate-option"
              [class.candidate-selected]="selectedCategory() === room.room_type_category"
              [attr.aria-pressed]="selectedCategory() === room.room_type_category"
              [attr.data-testid]="'room-' + room.room_type_category"
              [disabled]="saving()" (click)="chooseRoom(room.room_type_category)">
              <strong>{{ room.room_type }}</strong>
              <span class="muted room-details">
                <span *ngIf="room.sample_price_per_night_eur !== null && room.sample_price_per_night_eur !== undefined">{{ room.sample_price_per_night_eur }} € / διανυκτέρευση</span>
                <span *ngIf="room.sample_facilities">{{ room.sample_facilities }}</span>
              </span>
            </button>
          </li>
        </ul>
        <div class="setup-actions" *ngIf="rooms().length">
          <button class="primary-button" type="button"
                  [disabled]="!selectedCategory() || saving()" (click)="apply()">
            {{ saving() ? "Γίνεται αποθήκευση..." : "Αυτό το δωμάτιο" }}
          </button>
        </div>
        <div *ngIf="error()" class="alert alert-error" aria-live="assertive">
          <app-icon name="alert" [size]="16"></app-icon>
          <span>{{ error() }}</span>
          <button class="text-button" type="button" [disabled]="saving()" (click)="retry()">Δοκιμάστε ξανά</button>
          <button class="text-button" type="button" [disabled]="saving()" (click)="skip()">Συνέχεια στον χάρτη</button>
        </div>
      </ng-container>
    </section>
  `,
  styles: [`
    .room-price-basis { margin: 0 0 0.75rem; }
    .room-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
    .room-details { display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .room-details span + span::before { content: "·"; margin-right: 0.25rem; }
    .alert-error { align-items: center; flex-wrap: wrap; }
  `],
})
export class SetupRoomStepComponent implements OnInit, OnDestroy {
  @Input() ownedPropertyId = "";
  @Input() discoveryJobId = "";
  @Output() selected = new EventEmitter<void>();
  @Output() skipped = new EventEmitter<void>();

  readonly waiting = signal(false);
  readonly milestone = signal("Αναμονή για την ανακάλυψη δωματίων");
  readonly rooms = signal<OwnedPropertyRoomType[]>([]);
  readonly selectedCategory = signal("");
  readonly saving = signal(false);
  readonly error = signal("");
  /** What the sample prices are for (stay and party of the discovery job),
   * so the owner compares them with the same search on Booking. Empty when
   * the catalog is read without a job. */
  readonly priceBasis = signal("");

  private readonly failedAction = signal<FailedAction>(null);
  private readonly pendingDelayCancel = signal<(() => void) | null>(null);
  private readonly pendingRequestCancel = signal<(() => void) | null>(null);
  private destroyed = false;
  private reloadToken = 0;

  constructor(private readonly onboarding: OnboardingService) {}
  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.reloadToken += 1;
    this.pendingDelayCancel()?.();
    this.pendingRequestCancel()?.();
  }

  async reload(): Promise<void> {
    if (this.destroyed || this.waiting() || this.saving()) {
      return;
    }

    const token = ++this.reloadToken;
    const previousSelection = this.selectedCategory();
    this.waiting.set(true);
    this.milestone.set(
      this.discoveryJobId ? "Αναμονή για την ανακάλυψη δωματίων" : "Φόρτωση διαθέσιμων δωματίων",
    );
    this.error.set("");
    this.failedAction.set(null);
    try {
      if (!this.ownedPropertyId) {
        throw new Error("Δεν βρέθηκε κατάλυμα για τη φόρτωση των δωματίων.");
      }

      const discoverySettled = await this.waitForDiscovery(token);
      if (!discoverySettled || !this.isActive(token)) {
        return;
      }
      const rooms = await this.onboarding.roomTypes(this.ownedPropertyId);
      if (!this.isActive(token)) {
        return;
      }

      this.rooms.set(rooms);
      this.selectedCategory.set(
        rooms.some((room) => room.room_type_category === previousSelection)
          ? previousSelection
          : (rooms[0]?.room_type_category ?? ""),
      );
    } catch (err) {
      if (!this.isActive(token)) {
        return;
      }
      this.failedAction.set("load");
      this.error.set(err instanceof DiscoveryTimeoutError ? TIMEOUT_ERROR : LOAD_ERROR);
    } finally {
      if (this.isActive(token)) {
        this.waiting.set(false);
      }
    }
  }

  async apply(): Promise<void> {
    const roomTypeCategory = this.selectedCategory();
    if (this.destroyed || this.waiting() || this.saving() || !roomTypeCategory) {
      return;
    }
    this.saving.set(true);
    this.error.set("");
    this.failedAction.set(null);
    try {
      await this.onboarding.selectRoomType(this.ownedPropertyId, roomTypeCategory);
      if (this.destroyed) {
        return;
      }
      this.selected.emit();
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      this.failedAction.set("save");
      this.error.set(SAVE_ERROR);
    } finally {
      if (!this.destroyed) {
        this.saving.set(false);
      }
    }
  }

  chooseRoom(roomTypeCategory: string): void {
    if (!this.destroyed && !this.saving()) {
      this.selectedCategory.set(roomTypeCategory);
    }
  }

  retry(): void {
    if (this.destroyed || this.waiting() || this.saving()) {
      return;
    }
    if (this.failedAction() === "save") {
      void this.apply();
      return;
    }
    void this.reload();
  }

  skip(): void {
    if (!this.destroyed && !this.waiting() && !this.saving()) {
      this.skipped.emit();
    }
  }

  /** Polls step 1's discovery job; no id means there is nothing to await. */
  private async waitForDiscovery(token: number): Promise<boolean> {
    if (!this.discoveryJobId) {
      return true;
    }
    const deadline = performance.now() + DISCOVERY_TIMEOUT_MS;
    const milestones = [
      "Άνοιγμα της σελίδας του καταλύματος",
      "Ανάγνωση τύπων δωματίου",
      "Κατηγοριοποίηση και τιμές",
    ];
    let pollIndex = 0;
    while (true) {
      if (!this.isActive(token)) {
        return false;
      }
      const cancellation = new Promise<void>((resolve) => this.pendingRequestCancel.set(resolve));
      const outcome = await settleBeforeDeadline(
        () => this.onboarding.getScrapeJob(this.discoveryJobId),
        deadline,
        cancellation,
      );
      if (!this.isActive(token)) {
        return false;
      }
      this.pendingRequestCancel.set(null);
      if (outcome.kind === "cancelled") {
        return false;
      }
      if (outcome.kind === "expired") {
        throw new DiscoveryTimeoutError();
      }
      if (outcome.kind === "rejected") {
        throw new Error(LOAD_ERROR);
      }
      const job = outcome.value;
      if (job.status === "completed") {
        this.priceBasis.set(describePriceBasis(job));
        return true;
      }
      if (job.status === "failed") {
        throw new Error(LOAD_ERROR);
      }
      if (job.status !== "queued" && job.status !== "running") {
        throw new Error(LOAD_ERROR);
      }
      this.milestone.set(
        milestones[Math.min(Math.floor(pollIndex / 4), milestones.length - 1)],
      );
      pollIndex += 1;
      const remaining = remainingDeadlineMs(deadline);
      if (remaining <= 0) {
        throw new DiscoveryTimeoutError();
      }
      const elapsed = await this.delay(Math.min(DISCOVERY_POLL_INTERVAL_MS, remaining));
      if (!elapsed || !this.isActive(token)) {
        return false;
      }
      if (remainingDeadlineMs(deadline) <= 0) {
        throw new DiscoveryTimeoutError();
      }
    }
  }

  private delay(milliseconds: number): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      let settled = false;
      const finish = (elapsed: boolean): void => {
        if (settled) {
          return;
        }
        settled = true;
        this.pendingDelayCancel.set(null);
        resolve(elapsed);
      };
      const timer = window.setTimeout(() => finish(true), milliseconds);
      this.pendingDelayCancel.set(() => {
        window.clearTimeout(timer);
        finish(false);
      });
    });
  }

  private isActive(token: number): boolean {
    return !this.destroyed && token === this.reloadToken;
  }
}

class DiscoveryTimeoutError extends Error {}
