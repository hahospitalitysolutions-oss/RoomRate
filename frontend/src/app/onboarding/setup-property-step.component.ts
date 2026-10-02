import { AfterViewInit, Component, EventEmitter, Input, OnDestroy, Output, signal } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";

import { EmptyStateComponent } from "../components/empty-state.component";
import { IconComponent } from "../components/icon.component";
import { OnboardingService, OwnedPropertyOnboardingBody } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { PropertyCandidate } from "../types/market";
import { defaultStayDates } from "../utils/date-defaults";
import { MiniMapComponent } from "./mini-map.component";

type FirstPropertyCreateAttempt = {
  candidate: PropertyCandidate;
  body: OwnedPropertyOnboardingBody;
};

/**
 * Step 1: explicit confirmation of WHICH Booking property the user is.
 *
 * Three states: (a) a proposal from auto-setup -> confirm or ask for others,
 * (b) a candidate list -> pick one, PUT replaces the property and re-runs
 * discovery, (c) no proposal (auto-setup failed / no candidates) -> the
 * fallback form corrects name/location and searches again, also surfacing
 * the swallowed auto-setup failure message verbatim if one is pending
 * (spec §8). Confirming the proposal makes NO api call: the property
 * already exists (spec §4).
 */
@Component({
  selector: "app-setup-property-step",
  standalone: true,
  imports: [CommonModule, FormsModule, EmptyStateComponent, IconComponent, MiniMapComponent],
  template: `
    <section class="panel setup-step">
      <ng-container *ngIf="mode() === 'confirm' && proposedCandidate as candidate">
        <div class="setup-columns">
          <div class="candidate-card" data-testid="proposed-candidate">
            <h2>Βρήκαμε αυτό το κατάλυμα. Είστε εσείς;</h2>
            <strong>{{ candidate.display_name }}</strong>
            <p class="muted">
              {{ candidate.property_type || "Κατάλυμα" }}
              <ng-container *ngIf="candidate.stars"> · {{ candidate.stars }} αστέρια</ng-container>
            </p>
            <p>{{ candidate.address }}<ng-container *ngIf="candidate.city">, {{ candidate.city }}</ng-container></p>
            <p *ngIf="candidate.review_score" class="muted">
              Βαθμολογία {{ candidate.review_score }} ({{ candidate.review_count }} κριτικές)
            </p>
            <a [href]="candidate.booking_url" target="_blank" rel="noopener">Άνοιγμα στο Booking για έλεγχο</a>
          </div>
          <app-mini-map
            class="setup-mini-map"
            [candidates]="[candidate]"
            [selectedKey]="candidate.candidate_key"
          ></app-mini-map>
        </div>
        <div class="setup-actions">
          <button class="primary-button" type="button" (click)="confirmProposal()">Ναι, αυτό είναι</button>
          <button class="secondary-button" type="button" (click)="showOthers()">Δείξε άλλα</button>
        </div>
      </ng-container>

      <ng-container *ngIf="mode() === 'pick'">
        <div *ngIf="searching()" class="progress-panel" data-testid="candidate-progress">
          <strong>{{ searchMilestone() }}</strong>
          <span>Η αναζήτηση στο Booking είναι ζωντανή και μπορεί να διαρκέσει μερικά λεπτά.</span>
        </div>

        <ng-container *ngIf="!searching()">
          <!--
            Also gated on !error(): a mount-time search that really ran and
            FAILED leaves hasSearched false by design, which used to put «δεν
            έχει γίνει αναζήτηση» directly above the red alert reporting that
            very search. The alert below owns that state on its own.
          -->
          <app-empty-state
            *ngIf="!candidates().length && !hasSearched() && !error()"
            icon="search"
            title="Δεν έχει γίνει αναζήτηση ακόμα"
            explanation="Συμπληρώστε όνομα καταλύματος και τοποθεσία και ξεκινήστε την αναζήτηση για να βρείτε το κατάλυμά σας στο Booking."
          ></app-empty-state>

          <app-empty-state
            *ngIf="!candidates().length && hasSearched()"
            icon="search"
            title="Δεν βρέθηκαν καταλύματα"
            explanation="Το Booking δεν επέστρεψε αποτελέσματα για αυτό το όνομα και την τοποθεσία. Διορθώστε τα στοιχεία και δοκιμάστε ξανά."
          ></app-empty-state>

          <div class="setup-columns" *ngIf="candidates().length">
            <ul class="candidate-list">
              <li *ngFor="let candidate of candidates()">
                <button
                  type="button"
                  class="candidate-option"
                  [class.candidate-selected]="selectedKey() === candidate.candidate_key"
                  [disabled]="saving() || createNeedsReconciliation()"
                  [attr.data-testid]="'candidate-' + candidate.candidate_key"
                  (click)="selectedKey.set(candidate.candidate_key)"
                >
                  <strong>{{ candidate.display_name }}</strong>
                  <span class="muted">{{ candidate.address }}<ng-container *ngIf="candidate.review_score"> · {{ candidate.review_score }}</ng-container></span>
                </button>
              </li>
            </ul>
            <app-mini-map
              class="setup-mini-map"
              [candidates]="candidates()"
              [selectedKey]="selectedKey()"
            ></app-mini-map>
          </div>

          <div *ngIf="pendingSetupError()" class="alert alert-error" data-testid="pending-setup-error">
            <app-icon name="alert" [size]="16"></app-icon>
            {{ pendingSetupError() }}
          </div>

          <form class="setup-refine" (ngSubmit)="search(true)">
            <label><span>Όνομα καταλύματος</span>
              <input class="roomrate-input" name="refineName" [(ngModel)]="refineName"
                     [disabled]="saving() || createNeedsReconciliation()">
            </label>
            <label><span>Τοποθεσία</span>
              <input class="roomrate-input" name="refineLocation" [(ngModel)]="refineLocation"
                     [disabled]="saving() || createNeedsReconciliation()">
            </label>
            <button class="secondary-button" type="submit"
                    [disabled]="saving() || createNeedsReconciliation()">Αναζήτηση ξανά</button>
          </form>

          <div class="setup-actions" *ngIf="candidates().length">
            <button
              class="primary-button"
              type="button"
              [disabled]="!selectedKey() || saving() || createNeedsReconciliation()"
              (click)="applySelection()"
            >{{ saving() ? "Γίνεται αποθήκευση..." : "Αυτό είναι το κατάλυμά μου" }}</button>
          </div>
        </ng-container>
      </ng-container>

      <div *ngIf="error()" class="alert alert-error">
        <app-icon name="alert" [size]="16"></app-icon>
        {{ error() }}
        <button class="text-button" type="button" (click)="retryError()">Δοκιμάστε ξανά</button>
      </div>
    </section>
  `,
  styles: [`
    .setup-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: stretch; }
    .setup-mini-map { min-height: 260px; border-radius: 0.5rem; background: #e2e8f0; }
    .candidate-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; max-height: 320px; overflow-y: auto; }
    .candidate-option { width: 100%; text-align: left; padding: 0.6rem 0.8rem; border: 1px solid #cbd5e1; border-radius: 0.5rem; background: #fff; display: flex; flex-direction: column; gap: 0.15rem; cursor: pointer; }
    .candidate-option:disabled { cursor: not-allowed; opacity: 0.65; }
    .candidate-selected { border-color: #0f766e; box-shadow: 0 0 0 1px #0f766e; }
    .setup-actions { display: flex; gap: 0.75rem; margin-top: 1rem; }
    .setup-refine { display: flex; gap: 0.75rem; align-items: end; margin-top: 1rem; flex-wrap: wrap; }
    @media (max-width: 760px) { .setup-columns { grid-template-columns: 1fr; } }
  `],
})
export class SetupPropertyStepComponent implements AfterViewInit, OnDestroy {
  @Input() ownedPropertyId = "";
  @Input() proposedCandidate: PropertyCandidate | null = null;
  @Input() draftPropertyName = "";
  @Input() draftLocation = "";
  @Output() confirmed = new EventEmitter<{ ownedPropertyId: string; discoveryJobId: string }>();

  readonly mode = signal<"confirm" | "pick">("confirm");
  readonly candidates = signal<PropertyCandidate[]>([]);
  readonly selectedKey = signal("");
  readonly searching = signal(false);
  /** True once a search REALLY reached Booking and came back with a result set.
   * The empty candidate list means two different things -- "nobody has searched
   * yet" and "Booking answered with nothing" -- and only this distinguishes
   * them. Deliberately keyed on a completed request, not on the draft fields:
   * arriving with drafts and arriving with blanks both start at zero
   * candidates, and a failed request proves nothing about what Booking holds
   * (the red error speaks for that case). */
  readonly hasSearched = signal(false);
  readonly searchMilestone = signal("");
  readonly saving = signal(false);
  readonly error = signal("");
  readonly failedAction = signal<"search" | "apply" | null>(null);
  readonly createNeedsReconciliation = signal(false);
  private readonly ambiguousCreateError = signal("");
  private readonly attemptedCreate = signal<FirstPropertyCreateAttempt | null>(null);
  /** A swallowed auto-setup failure carried from auth-page (spec §8). Read
   * once when fallback mode mounts. The mount-time automatic search keeps
   * it visible, while an explicit user search clears both signal and key. */
  readonly pendingSetupError = signal("");
  refineName = "";
  refineLocation = "";

  private milestoneTimer: ReturnType<typeof window.setInterval> | null = null;

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
  ) {}

  ngAfterViewInit(): void {
    // No proposal stored (auto-setup failed or user restarted): straight to
    // the correction form instead of an empty confirm screen. The search below
    // is the draft flow -- it really runs when the drafts carry a name and a
    // location, and bails silently into the not-searched-yet state when they
    // do not (a blank sign-in has nothing to search FROM).
    if (!this.proposedCandidate) {
      this.mode.set("pick");
      this.pendingSetupError.set(this.workflow.get("pendingSetupError"));
      this.refineName = this.draftPropertyName;
      this.refineLocation = this.draftLocation;
      void this.search(false);
      return;
    }
    this.refineName = this.draftPropertyName || this.proposedCandidate.display_name;
    this.refineLocation = this.draftLocation || this.proposedCandidate.city;
  }

  ngOnDestroy(): void {
    this.stopMilestones();
  }

  confirmProposal(): void {
    // The property already exists; confirming is free (spec §4 step 1).
    this.confirmed.emit({
      ownedPropertyId: this.ownedPropertyId,
      discoveryJobId: this.workflow.get("pendingDiscoveryJobId"),
    });
  }

  showOthers(): void {
    this.mode.set("pick");
    void this.search(true);
  }

  async search(userInitiated: boolean): Promise<void> {
    const propertyName = this.refineName.trim();
    const location = this.refineLocation.trim();
    if (!propertyName || !location) {
      // Only a user who ACTED can be told they got it wrong. The mount-time
      // auto-search runs for everyone who lands here without a proposal, so
      // raising the validation error there accused people of leaving blank
      // fields they had never been shown -- next to a "no results" state for a
      // search that never left the browser. Incomplete input bails into the
      // not-searched-yet state instead (hasSearched stays false).
      if (userInitiated) {
        this.error.set("Συμπληρώστε όνομα καταλύματος και τοποθεσία.");
        this.failedAction.set("search");
      }
      return;
    }
    this.error.set("");
    this.failedAction.set(null);
    // A new search means the old auto-setup failure is no longer "pending":
    // clear the persisted key so a later reload does not resurrect it. The
    // signal survives the mount-time AUTO-search (the user must see why they
    // landed in the fallback) but a user-initiated re-search replaces the
    // outcome -- keeping the old error above fresh results would read as the
    // new search failing.
    this.workflow.set("pendingSetupError", "");
    if (userInitiated) {
      this.pendingSetupError.set("");
    }
    this.searching.set(true);
    this.startMilestones();
    const stay = defaultStayDates();
    try {
      const results = await this.onboarding.propertyCandidates({
        property_name: propertyName,
        location,
        check_in: stay.checkIn,
        check_out: stay.checkOut,
      });
      this.candidates.set(results);
      // Set only here, on a response: from now on an empty list IS Booking's
      // answer, so "Δεν βρέθηκαν καταλύματα" becomes true to say.
      this.hasSearched.set(true);
      // Best match first is the backend's contract; preselect it.
      this.selectedKey.set(results[0]?.candidate_key ?? "");
      this.workflow.set("draftPropertyName", propertyName);
      this.workflow.set("draftLocation", location);
    } catch (err) {
      this.failedAction.set("search");
      this.error.set(err instanceof Error ? err.message : "Η αναζήτηση απέτυχε.");
    } finally {
      this.stopMilestones();
      this.searching.set(false);
    }
  }

  async applySelection(): Promise<void> {
    const candidate = this.candidates().find((c) => c.candidate_key === this.selectedKey());
    if (!candidate) {
      return;
    }
    this.saving.set(true);
    this.error.set("");
    this.failedAction.set(null);
    const stay = defaultStayDates();
    const body = {
      display_name: candidate.display_name,
      booking_url: candidate.booking_url,
      city: candidate.city,
      raw_destination: this.refineLocation.trim() || candidate.city,
      address: candidate.address,
      country: candidate.country,
      property_type: candidate.property_type,
      latitude: candidate.latitude,
      longitude: candidate.longitude,
      check_in: stay.checkIn,
      check_out: stay.checkOut,
    };
    const creatingFirstProperty = !this.ownedPropertyId;
    let createAttempt: FirstPropertyCreateAttempt = {
      candidate: { ...candidate },
      body: { ...body },
    };
    try {
      if (creatingFirstProperty && this.createNeedsReconciliation()) {
        const previousAttempt = this.attemptedCreate();
        if (!previousAttempt) {
          this.restoreAmbiguousCreateError();
          return;
        }
        createAttempt = previousAttempt;
        const reconciliation = await this.reconcileFirstProperty();
        if (reconciliation === "committed") {
          return;
        }
        if (reconciliation === "unknown") {
          // Safe default: keep the original POST error and do not risk a
          // duplicate while the authoritative account state is unavailable.
          this.restoreAmbiguousCreateError();
          return;
        }
      } else if (creatingFirstProperty) {
        // Snapshot before POST: later UI/model changes can never alter the
        // metadata used to reconcile or retry this exact create attempt.
        this.attemptedCreate.set(createAttempt);
      }

      const response = !creatingFirstProperty
        ? await this.onboarding.replaceOwnedProperty(this.ownedPropertyId, body)
        : await this.onboarding.createOwnedProperty(createAttempt.body);
      this.createNeedsReconciliation.set(false);
      this.ambiguousCreateError.set("");
      this.attemptedCreate.set(null);
      this.completeSelection(
        creatingFirstProperty ? createAttempt.candidate : candidate,
        response.owned_property_id,
        response.discovery_job.id,
      );
    } catch (err) {
      const originalError = err instanceof Error ? err.message : "Η αποθήκευση του καταλύματος απέτυχε.";
      if (creatingFirstProperty) {
        // A failed response does not prove the POST rolled back. createOwnedProperty
        // invalidated /me in finally; force one more fresh read before offering
        // a retry that could otherwise create a duplicate property.
        this.createNeedsReconciliation.set(true);
        this.ambiguousCreateError.set(originalError);
        const reconciliation = await this.reconcileFirstProperty();
        if (reconciliation === "committed") {
          return;
        }
      }
      this.failedAction.set("apply");
      // Reconciliation failures deliberately do not replace the actionable
      // error from the original create request.
      this.error.set(originalError);
    } finally {
      this.saving.set(false);
    }
  }

  retryError(): void {
    if (this.failedAction() === "apply") {
      void this.applySelection();
      return;
    }
    void this.search(true);
  }

  private async reconcileFirstProperty(): Promise<"committed" | "absent" | "unknown"> {
    const attemptedCreate = this.attemptedCreate();
    if (!attemptedCreate) {
      return "unknown";
    }
    this.onboarding.invalidateCurrentUser();
    try {
      const me = await this.onboarding.currentUser();
      if (!me.owned_property_id) {
        return "absent";
      }
      this.createNeedsReconciliation.set(false);
      this.ambiguousCreateError.set("");
      this.attemptedCreate.set(null);
      // The create committed but its response was lost. Step 2 can read the
      // room catalog directly when no discovery job id is available.
      this.completeSelection(attemptedCreate.candidate, me.owned_property_id, "");
      return "committed";
    } catch {
      return "unknown";
    }
  }

  private restoreAmbiguousCreateError(): void {
    this.failedAction.set("apply");
    this.error.set(this.ambiguousCreateError() || "Η αποθήκευση του καταλύματος απέτυχε.");
  }

  private completeSelection(
    candidate: PropertyCandidate,
    ownedPropertyId: string,
    discoveryJobId: string,
  ): void {
    this.workflow.set("propertyName", candidate.display_name);
    this.workflow.set("destination", candidate.city);
    this.confirmed.emit({ ownedPropertyId, discoveryJobId });
  }

  /** Milestones instead of a spinner: the Booking scout can take minutes. */
  private startMilestones(): void {
    this.stopMilestones();
    const milestones = [
      "Σύνδεση με το Booking",
      "Αναζήτηση καταλυμάτων στην περιοχή",
      "Έλεγχος ονομάτων και βαθμολογιών",
      "Σχεδόν έτοιμο — ταξινόμηση αποτελεσμάτων",
    ];
    let index = 0;
    this.searchMilestone.set(milestones[0]);
    this.milestoneTimer = window.setInterval(() => {
      index = Math.min(index + 1, milestones.length - 1);
      this.searchMilestone.set(milestones[index]);
    }, 20_000);
  }

  private stopMilestones(): void {
    if (this.milestoneTimer !== null) {
      window.clearInterval(this.milestoneTimer);
      this.milestoneTimer = null;
    }
  }

}
