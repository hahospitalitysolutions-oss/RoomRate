import { Component, OnInit, signal } from "@angular/core";
import { CommonModule } from "@angular/common";
import { ActivatedRoute, Router } from "@angular/router";

import { SetupPropertyStepComponent } from "../onboarding/setup-property-step.component";
import { SetupRoomStepComponent } from "../onboarding/setup-room-step.component";
import { SetupSearchStepComponent } from "../onboarding/setup-search-step.component";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { CurrentUser, PropertyCandidate } from "../types/market";

export type WizardStep = 1 | 2 | 3;

@Component({
  selector: "app-setup-page",
  standalone: true,
  imports: [CommonModule, SetupPropertyStepComponent, SetupRoomStepComponent, SetupSearchStepComponent],
  template: `
    <main class="page-shell setup-page">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate</p>
          <h1 data-testid="setup-step-title">Βήμα {{ step() }} από 3 — {{ stepTitle() }}</h1>
          <p class="muted">{{ stepSubtitle() }}</p>
        </div>
      </header>

      <section class="page-body">
        <div *ngIf="status() === 'loading'" class="progress-panel">
          <strong>Φόρτωση του λογαριασμού σας</strong>
        </div>
        <div *ngIf="status() === 'error'" class="alert alert-error">{{ error() }}</div>

        <ng-container *ngIf="status() === 'ready'">
          <app-setup-property-step
            *ngIf="step() === 1"
            [ownedPropertyId]="ownedPropertyId()"
            [proposedCandidate]="proposedCandidate()"
            [draftPropertyName]="draftPropertyName()"
            [draftLocation]="draftLocation()"
            (confirmed)="onPropertyConfirmed($event)"
          ></app-setup-property-step>

          <app-setup-room-step
            *ngIf="step() === 2"
            [ownedPropertyId]="ownedPropertyId()"
            [discoveryJobId]="discoveryJobId()"
            (selected)="onRoomSelected()"
            (skipped)="skipRoomStep()"
          ></app-setup-room-step>

          <app-setup-search-step
            *ngIf="step() === 3"
            [ownedPropertyId]="ownedPropertyId()"
            (finished)="goToMap()"
            (postponed)="goToMap()"
          ></app-setup-search-step>
        </ng-container>
      </section>
    </main>
  `,
})
export class SetupPageComponent implements OnInit {
  readonly step = signal<WizardStep>(1);
  readonly status = signal<"loading" | "ready" | "error">("loading");
  readonly error = signal("");
  readonly ownedPropertyId = signal("");
  readonly proposedCandidate = signal<PropertyCandidate | null>(null);
  readonly discoveryJobId = signal("");
  readonly draftPropertyName = signal("");
  readonly draftLocation = signal("");
  private changeRequested = false;

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
    private readonly route: ActivatedRoute,
  ) {
    this.changeRequested = route.snapshot.queryParamMap.get("change") === "1";
  }

  async ngOnInit(): Promise<void> {
    try {
      const me = await this.onboarding.currentUser();
      this.applyUser(me);
    } catch (err) {
      this.status.set("error");
      this.error.set(err instanceof Error ? err.message : "Η φόρτωση του λογαριασμού απέτυχε.");
    }
  }

  stepTitle(): string {
    return ({ 1: "Το κατάλυμά σας", 2: "Το δωμάτιο που τιμολογείτε", 3: "Η πρώτη αναζήτηση" } as const)[this.step()];
  }

  stepSubtitle(): string {
    return ({
      1: "Επιβεβαιώστε ποιο κατάλυμα του Booking είστε, ώστε κάθε σύγκριση τιμών να χτίζεται στα σωστά δεδομένα.",
      2: "Διαλέξτε τον τύπο δωματίου που θα συγκρίνεται με την αγορά.",
      3: "Δείτε τι θα τρέξει και πόσο θα διαρκέσει, πριν ξεκινήσει οτιδήποτε.",
    } as const)[this.step()];
  }

  onPropertyConfirmed(event: { ownedPropertyId: string; discoveryJobId: string }): void {
    this.ownedPropertyId.set(event.ownedPropertyId);
    this.discoveryJobId.set(event.discoveryJobId);
    this.workflow.set("ownedPropertyId", event.ownedPropertyId);
    this.workflow.set("pendingCandidate", "");
    this.workflow.set("pendingDiscoveryJobId", event.discoveryJobId);
    this.step.set(2);
    // Un-stick change mode once the replace commits: leaving ?change=1 in the
    // URL would force step 1 pick mode again on a reload or browser-back at
    // this exact URL, inviting a second replaceOwnedProperty (a second paid
    // discovery job) for a property that was just confirmed.
    this.changeRequested = false;
    void this.router.navigate([], { relativeTo: this.route, queryParams: {}, replaceUrl: true });
  }

  onRoomSelected(): void {
    this.step.set(3);
  }

  goToMap(): void {
    void this.router.navigateByUrl("/map");
  }

  skipRoomStep(): void {
    // Router state exists only on this navigation; later map links resume setup.
    void this.router.navigateByUrl("/map", { state: { allowIncompleteSetup: true } });
  }

  private applyUser(me: CurrentUser): void {
    this.workflow.bindToSubject(me.auth_subject);
    this.draftPropertyName.set(this.workflow.get("draftPropertyName"));
    this.draftLocation.set(this.workflow.get("draftLocation"));
    this.ownedPropertyId.set(me.owned_property_id || "");
    this.discoveryJobId.set(this.workflow.get("pendingDiscoveryJobId"));
    let candidate: PropertyCandidate | null = null;
    const rawCandidate = this.workflow.get("pendingCandidate");
    if (rawCandidate) {
      try {
        candidate = JSON.parse(rawCandidate) as PropertyCandidate;
      } catch {
        // Self-heal: a corrupt value would otherwise re-fail on every load.
        this.workflow.set("pendingCandidate", "");
      }
    }
    this.proposedCandidate.set(candidate);
    // The guard guarantees something is missing. A property that exists but
    // was never explicitly confirmed (the auto-setup proposal is still in
    // storage) starts at step 1 for confirmation; otherwise skip to step 2.
    this.step.set(me.owned_property_id && !candidate ? 2 : 1);
    this.status.set("ready");
    if (this.changeRequested) {
      // Change-property flow: always start at step 1 in pick mode.
      this.proposedCandidate.set(null);
      this.step.set(1);
    }
  }
}
