import { Injectable, computed, signal } from "@angular/core";

import { CurrentUser, ScrapeJobResponse, TrackedCompetitorListResponse } from "../types/market";

export type ChecklistStep = {
  id: number;
  label: string;
  why: string;
  done: boolean;
};

/**
 * Checklist state (spec §5), computed ONLY from data the map already
 * fetches — the setters below are called at the map's existing fetch sites,
 * so the checklist adds zero requests to the critical path.
 *
 * Sources of truth per step (steps 3 and 5 are scoped to the CURRENT
 * property: the backend nulls a scrape job's owned_property_id instead of
 * deleting the job when a property is removed, so an account-wide run count
 * would let an orphaned job from a deleted property fake progress on a
 * brand-new one):
 *   1 property connected   -> me.owned_property_id
 *   2 room chosen          -> me.selected_room_type_category
 *   3 first search         -> a completed competitor_search for the current
 *                             property with runs > 0
 *   4 tracking              -> tracked list length vs min(3, available), see
 *                             TRACKING_TARGET below
 *   5 first recommendation -> at least 2 completed competitor_search runs
 *                             for the current property (the recommendation
 *                             needs a second run to compare)
 */

/** What tracking asks for in a market that can supply it (spec §5). */
const TRACKING_TARGET = 3;

@Injectable({ providedIn: "root" })
export class SetupProgressService {
  private readonly user = signal<CurrentUser | null>(null);
  private readonly jobs = signal<ScrapeJobResponse[]>([]);
  private readonly tracked = signal<TrackedCompetitorListResponse | null>(null);
  // Competitors the CURRENT result set could actually have tracked — the map
  // feeds it the rows that carry a property_id, because those are exactly the
  // ones "Add selected to tracked" can save (the others are dropped there).
  // The destination market's hotel count would be the wrong denominator: the
  // user cannot tick a hotel the search did not return. 0 means "no result set
  // known yet", never "this market has none" — see requiredTracked.
  private readonly availableCompetitors = signal(0);

  readonly steps = computed<ChecklistStep[]>(() => {
    const me = this.user();
    // A job's owned_property_id is optional/nullable (orphaned jobs null it
    // out), so a strict === against me?.owned_property_id never matches an
    // orphan even when the current user has no property yet either.
    const runs = this.jobs()
      .filter((job) =>
        job.job_type === "competitor_search"
        && job.status === "completed"
        && Boolean(me?.owned_property_id)
        && job.owned_property_id === me?.owned_property_id,
      )
      .reduce((total, job) => total + (job.scrape_runs_count || 0), 0);
    const trackedCount = this.tracked()?.competitors.length ?? 0;
    const available = this.availableCompetitors();
    const required = this.requiredTracked(available);
    return [
      { id: 1, label: "Σύνδεση καταλύματος", why: "Κάθε σύγκριση τιμών βασίζεται στο σωστό κατάλυμα.", done: Boolean(me?.owned_property_id) },
      { id: 2, label: "Επιλογή δωματίου", why: "Η σύγκριση με την αγορά γίνεται για συγκεκριμένο τύπο δωματίου.", done: Boolean(me?.selected_room_type_category) },
      { id: 3, label: "Πρώτη αναζήτηση", why: "Χωρίς αναζήτηση δεν υπάρχουν τιμές ανταγωνιστών στον χάρτη.", done: runs >= 1 },
      { id: 4, label: this.trackingLabel(required), why: this.trackingWhy(required, available), done: trackedCount >= required },
      { id: 5, label: "Πρώτη σύσταση τιμής", why: "Η σύσταση χρειάζεται δεύτερη αναζήτηση ώστε να υπάρχει σημείο σύγκρισης.", done: runs >= 2 },
    ];
  });

  /**
   * How many tracked rooms step 4 asks for: min(3, available).
   *
   * Owner decision 2026-08-23: a real small market (Faliraki/double returns
   * 2-3 rooms) made a flat "3+" unreachable, so the checklist could never
   * finish for the users the product is being sold to. With NO result set
   * loaded the count is 0, which is not a claim that the market is empty — the
   * target stays 3 there, so an untouched map never auto-completes the step.
   */
  private requiredTracked(available: number): number {
    return available > 0 ? Math.min(TRACKING_TARGET, available) : TRACKING_TARGET;
  }

  /** The label states the real requirement — the old "3+" would now lie. */
  private trackingLabel(required: number): string {
    return required === 1
      ? "Παρακολούθηση 1 ανταγωνιστή"
      : `Παρακολούθηση ${required} ανταγωνιστών`;
  }

  private trackingWhy(required: number, available: number): string {
    const alerts = "Οι ειδοποιήσεις τιμών αφορούν όσους παρακολουθείτε.";
    if (required >= TRACKING_TARGET) {
      return alerts;
    }
    // Small market: say why the number dropped, or the lower bar reads as an
    // arbitrary change of mind. "μπορούν να παρακολουθηθούν", not "βρήκε":
    // setAvailableCompetitors is fed the property_id-BEARING rows only, so
    // this count can be smaller than the result count the sidebar shows, and
    // "the search found X" would contradict the list next to it.
    const found = available === 1
      ? "Από την τελευταία αναζήτηση μπορεί να παρακολουθηθεί 1 ανταγωνιστής, οπότε το βήμα ολοκληρώνεται με αυτόν."
      : `Από την τελευταία αναζήτηση μπορούν να παρακολουθηθούν ${available} ανταγωνιστές, οπότε το βήμα ολοκληρώνεται με αυτούς.`;
    return `${found} ${alerts}`;
  }

  readonly doneCount = computed(() => this.steps().filter((step) => step.done).length);
  readonly nextOpenStep = computed(() => this.steps().find((step) => !step.done) ?? null);
  readonly complete = computed(() => this.doneCount() === this.steps().length);

  setUser(user: CurrentUser): void {
    this.user.set(user);
  }

  setJobs(jobs: ScrapeJobResponse[]): void {
    this.jobs.set(jobs);
  }

  setTracked(response: TrackedCompetitorListResponse): void {
    this.tracked.set(response);
  }

  /** Trackable rows in the result set on screen (0 = none loaded). */
  setAvailableCompetitors(count: number): void {
    this.availableCompetitors.set(Math.max(0, count));
  }

  /**
   * Sign-out clears account state so a next sign-in cannot inherit 5/5.
   * No extra guard is needed in the checklist's hidden(): once user is
   * null, steps 1-2 are undone, so complete() is false until a fresh /me
   * lands for whoever signs in next.
   */
  reset(): void {
    this.user.set(null);
    this.jobs.set([]);
    this.tracked.set(null);
    this.availableCompetitors.set(0);
  }
}
