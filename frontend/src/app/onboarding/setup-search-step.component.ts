import { CommonModule } from "@angular/common";
import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from "@angular/core";

import { IconComponent } from "../components/icon.component";
import { CompetitorSearchRequest, OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { ScrapeJobResponse } from "../types/market";
import { defaultStayDates } from "../utils/date-defaults";
import { pollScrapeJob } from "./scrape-job-poller";
import {
  buildAuthoritativeSearchIntent,
  classifySearchPostFailure,
} from "./search-intent";
import { findNewMatchingSearchJob } from "./search-job-reconciliation";

const SEARCH_POLL_INTERVAL_MS = 5_000;
const SEARCH_TIMEOUT_MS = 5 * 60_000;
const PREPARATION_ERROR = "Δεν μπορέσαμε να προετοιμάσουμε με ασφάλεια την αναζήτηση. Δοκιμάστε ξανά.";
const START_ERROR = "Δεν μπορέσαμε να ξεκινήσουμε την αναζήτηση. Δοκιμάστε ξανά.";
const VALIDATION_ERROR = "Τα στοιχεία της αναζήτησης δεν είναι έγκυρα. Επαναφορτώστε τα στοιχεία πριν δοκιμάσετε ξανά.";
const QUOTA_ERROR = "Έχετε φτάσει το όριο αναζητήσεων. Δοκιμάστε ξανά αργότερα.";
const RECONCILE_ERROR = "Δεν μπορέσαμε να επιβεβαιώσουμε αν η αναζήτηση ξεκίνησε. Δοκιμάστε ξανά.";
const POLL_ERROR = "Δεν μπορέσαμε να ελέγξουμε την αναζήτηση. Δοκιμάστε ξανά.";
const TIMEOUT_ERROR = "Η αναζήτηση διαρκεί ασυνήθιστα πολύ. Μπορείτε να συνεχίσετε στον χάρτη και να επιστρέψετε αργότερα.";
type PreparationStatus = "idle" | "loading" | "ready" | "error";
type RetryAction = "start" | "prepare" | "direct" | "reconcile";
type PostContext = "ordinary" | "reconciled-no-match";

/**
 * Step 3 cost gate for the first paid competitor search.
 *
 * Contract: summary and POST share one immutable request loaded from `/me`;
 * stale workflow values never reach paid fields. No POST occurs during init,
 * preparation retry or postponement. Ambiguous POST failures reconcile against
 * the pre-POST job baseline. Initial deterministic HTTP failures never
 * reconcile; a quota response inside reconciliation preserves that lineage.
 */
@Component({
  selector: "app-setup-search-step",
  standalone: true,
  imports: [CommonModule, IconComponent],
  template: `
    <section class="panel setup-step">
      <div *ngIf="preparationStatus() === 'loading' && !running()" class="progress-panel" data-testid="search-preparation">
        <strong>Προετοιμασία της αναζήτησης</strong>
        <span>Επιβεβαιώνουμε τα στοιχεία που θα χρησιμοποιηθούν πριν επιτραπεί οποιαδήποτε χρεώσιμη ενέργεια.</span>
      </div>

      <ng-container *ngIf="preparationStatus() === 'ready' && !running() && searchIntent() as intent">
        <div class="search-summary">
          <div><span>Περιοχή</span><strong data-testid="search-destination">{{ intent.destination }}</strong></div>
          <div><span>Τύπος δωματίου</span><strong data-testid="search-room-category">{{ intent.room_type_category }}</strong></div>
          <div><span>Άφιξη</span><strong data-testid="search-check-in">{{ intent.check_in }}</strong></div>
          <div><span>Αναχώρηση</span><strong data-testid="search-check-out">{{ intent.check_out }}</strong></div>
          <div><span>Έως ανταγωνιστές</span><strong>8</strong></div>
          <div><span>Διάρκεια</span><strong>2–4 λεπτά</strong></div>
        </div>
        <p class="muted" data-testid="cost-statement">
          Κάθε αναζήτηση αντλεί ζωντανά δεδομένα τιμών από το Booking και έχει κόστος.
          Δεν ξεκινά τίποτα χωρίς το δικό σας κλικ.
        </p>
        <div class="setup-actions">
          <button class="primary-button" type="button" (click)="start()">Ξεκίνα την αναζήτηση</button>
          <button class="secondary-button" type="button" (click)="postpone()">Αργότερα</button>
        </div>
      </ng-container>

      <div *ngIf="running()" class="progress-panel" data-testid="search-progress" aria-live="polite">
        <strong>{{ milestone() }}</strong>
        <span>Μπορείτε να φύγετε από αυτή τη σελίδα — θα ειδοποιηθείτε όταν ολοκληρωθεί.</span>
      </div>

      <div *ngIf="error() && !running()" class="alert alert-error" aria-live="assertive">
        <app-icon name="alert" [size]="16"></app-icon><span>{{ error() }}</span>
        <button *ngIf="retryAction() === 'prepare'" class="text-button" type="button" (click)="prepareIntent()">Επαναφόρτωση στοιχείων</button>
        <button *ngIf="retryAction() === 'direct' || retryAction() === 'reconcile'" class="text-button" type="button" (click)="start()">Δοκιμάστε ξανά αργότερα</button>
        <button *ngIf="retryAction() === 'start'" class="text-button" type="button" (click)="start()">Δοκιμάστε ξανά</button>
        <button class="text-button" type="button" (click)="postpone()">Συνέχεια στον χάρτη</button>
      </div>
    </section>
  `,
  styles: [`
    .search-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 0.75rem; }
    .search-summary span { display: block; color: #64748b; font-size: 0.8rem; }
    .alert-error { align-items: center; flex-wrap: wrap; }
  `],
})
export class SetupSearchStepComponent implements OnInit, OnDestroy {
  @Input() ownedPropertyId = "";
  @Output() finished = new EventEmitter<void>();
  @Output() postponed = new EventEmitter<void>();

  readonly searchIntent = signal<CompetitorSearchRequest | null>(null);
  readonly preparationStatus = signal<PreparationStatus>("idle");
  readonly running = signal(false);
  readonly milestone = signal("");
  readonly error = signal("");
  readonly retryAction = signal<RetryAction>("prepare");

  private readonly baselineJobIds = signal<ReadonlySet<string>>(new Set());
  private readonly uncertainPost = signal(false);
  private readonly activeJobId = signal("");
  private readonly stay = defaultStayDates();
  private readonly destroyedPromise: Promise<void>;
  private resolveDestroyed!: () => void;
  private destroyed = false;
  private navigationEmitted = false;
  private runToken = 0;
  private preparationToken = 0;

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
  ) {
    this.destroyedPromise = new Promise<void>((resolve) => { this.resolveDestroyed = resolve; });
  }

  async ngOnInit(): Promise<void> {
    await this.prepareIntent();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.runToken += 1;
    this.preparationToken += 1;
    this.resolveDestroyed();
  }

  async prepareIntent(): Promise<void> {
    if (this.destroyed || this.running() || this.preparationStatus() === "loading") return;
    const token = ++this.preparationToken;
    this.preparationStatus.set("loading");
    this.searchIntent.set(null);
    this.error.set("");
    this.retryAction.set("prepare");
    try {
      this.onboarding.invalidateCurrentUser();
      const me = await this.onboarding.currentUser();
      if (!this.isPreparationActive(token)) return;
      this.searchIntent.set(buildAuthoritativeSearchIntent(me, this.ownedPropertyId, this.stay));
      this.preparationStatus.set("ready");
      this.retryAction.set("start");
    } catch {
      if (!this.isPreparationActive(token)) return;
      this.preparationStatus.set("error");
      this.error.set(PREPARATION_ERROR);
    }
  }

  async start(): Promise<void> {
    const intended = this.searchIntent();
    if (this.destroyed || this.running() || this.navigationEmitted || !intended) return;
    const token = ++this.runToken;
    const retryAction = this.retryAction();
    this.running.set(true);
    this.error.set("");
    this.retryAction.set("start");
    this.milestone.set("Προετοιμασία της αναζήτησης");
    try {
      let jobId: string | null = this.activeJobId();
      if (!jobId) {
        jobId = retryAction === "direct"
          ? await this.postIntended(intended, token, "ordinary")
          : retryAction === "reconcile" || this.uncertainPost()
            ? await this.reconcileOrPost(intended, token)
            : await this.prepareAndPost(intended, token);
      }
      if (!jobId || !this.isActive(token)) return;
      this.activeJobId.set(jobId);
      this.workflow.set("lastCompetitorJobId", jobId);
      const completed = await this.pollUntilDone(jobId, token);
      if (completed && this.isActive(token) && !this.navigationEmitted) {
        this.navigationEmitted = true;
        this.finished.emit();
      }
    } catch (err) {
      if (this.isActive(token)) this.handleError(err);
    } finally {
      if (this.isActive(token)) this.running.set(false);
    }
  }

  postpone(): void {
    if (!this.destroyed && !this.running() && !this.navigationEmitted) {
      this.navigationEmitted = true;
      this.postponed.emit();
    }
  }

  private async prepareAndPost(intended: CompetitorSearchRequest, token: number): Promise<string | null> {
    this.milestone.set("Επιβεβαίωση ότι δεν υπάρχει διπλή αναζήτηση");
    const recent = await this.listRecent(false);
    if (!this.isActive(token)) return null;
    this.baselineJobIds.set(new Set(recent.map((job) => job.id)));
    return this.postIntended(intended, token, "ordinary");
  }

  private async reconcileOrPost(intended: CompetitorSearchRequest, token: number): Promise<string | null> {
    this.milestone.set("Έλεγχος της προηγούμενης προσπάθειας");
    const recent = await this.listRecent(true);
    if (!this.isActive(token)) return null;
    const committed = findNewMatchingSearchJob(recent, this.baselineJobIds(), intended);
    if (committed) {
      this.uncertainPost.set(false);
      return committed.id;
    }
    this.baselineJobIds.set(new Set(recent.map((job) => job.id)));
    this.uncertainPost.set(false);
    return this.postIntended(intended, token, "reconciled-no-match");
  }

  private async listRecent(reconciling: boolean): Promise<ScrapeJobResponse[]> {
    try {
      return await this.onboarding.recentScrapeJobs(50);
    } catch {
      throw reconciling ? new ReconciliationError() : new StartError();
    }
  }

  private async postIntended(
    intended: CompetitorSearchRequest,
    token: number,
    context: PostContext,
  ): Promise<string | null> {
    this.milestone.set("Εκκίνηση της αναζήτησης");
    try {
      const job = await this.onboarding.startCompetitorSearch(intended);
      if (!this.isActive(token)) return null;
      this.uncertainPost.set(false);
      return job.id;
    } catch (error) {
      if (!this.isActive(token)) return null;
      const failure = classifySearchPostFailure(error);
      if (failure === "quota" && context === "reconciled-no-match") {
        this.uncertainPost.set(true);
        throw new ReconciliationQuotaError();
      }
      this.uncertainPost.set(failure === "ambiguous");
      if (failure === "validation") throw new ValidationError();
      if (failure === "quota") throw new QuotaError();
      throw new StartError();
    }
  }

  private async pollUntilDone(jobId: string, token: number): Promise<boolean> {
    const outcome = await pollScrapeJob({
      getJob: () => this.onboarding.getScrapeJob(jobId),
      timeoutMs: SEARCH_TIMEOUT_MS,
      intervalMs: SEARCH_POLL_INTERVAL_MS,
      cancellation: this.destroyedPromise,
      milestones: ["Αναζήτηση καταλυμάτων στην περιοχή", "Ανάγνωση τιμών δωματίων", "Ταίριασμα με το δικό σας δωμάτιο"],
      pollsPerMilestone: 8,
      onMilestone: (value) => { if (this.isActive(token)) this.milestone.set(value); },
    });
    if (!this.isActive(token) || outcome === "cancelled") return false;
    if (outcome === "completed") return true;
    if (outcome === "expired") throw new SearchTimeoutError();
    if (outcome === "terminal") this.activeJobId.set("");
    throw new PollError();
  }

  private handleError(error: unknown): void {
    if (error instanceof ValidationError) {
      this.searchIntent.set(null);
      this.preparationStatus.set("error");
      this.retryAction.set("prepare");
      this.error.set(VALIDATION_ERROR);
      return;
    }
    if (error instanceof ReconciliationQuotaError) this.retryAction.set("reconcile");
    else if (error instanceof QuotaError) this.retryAction.set("direct");
    else this.retryAction.set("start");
    if (error instanceof QuotaError) this.error.set(QUOTA_ERROR);
    else if (error instanceof ReconciliationError) this.error.set(RECONCILE_ERROR);
    else if (error instanceof SearchTimeoutError) this.error.set(TIMEOUT_ERROR);
    else if (error instanceof PollError) this.error.set(POLL_ERROR);
    else this.error.set(START_ERROR);
  }

  private isActive(token: number): boolean {
    return !this.destroyed && token === this.runToken;
  }

  private isPreparationActive(token: number): boolean {
    return !this.destroyed && token === this.preparationToken;
  }
}

class ReconciliationError extends Error {}
class StartError extends Error {}
class ValidationError extends Error {}
class QuotaError extends Error {}
class ReconciliationQuotaError extends QuotaError {}
class PollError extends Error {}
class SearchTimeoutError extends Error {}
