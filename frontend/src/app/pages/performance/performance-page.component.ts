/**
 * «Απόδοση»: how the property did, from the owner's own bookings (Excel or
 * the Booking.com export), next to the market prices RoomRate collects.
 *
 * SHELL ONLY. The frontend team builds the tabs, the filter bar, the upload
 * flow and the charts here (pages/performance/**, components/charts/**), on
 * PerformanceApiService: in development it answers from
 * public/mock/performance/*.json until each backend endpoint exists.
 * Plan, task split and file ownership: docs/performance/PLAN.md.
 */
import { CommonModule } from "@angular/common";
import { Component, OnInit, signal } from "@angular/core";

import { AppHeaderComponent } from "../../components/app-header.component";
import { OnboardingService } from "../../services/onboarding.service";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceStatus } from "../../types/performance";

export type PerformanceTab = "overview" | "occupancy" | "rooms" | "stays" | "forecast";

export const PERFORMANCE_TABS: { id: PerformanceTab; label: string }[] = [
  { id: "overview", label: "Επισκόπηση" },
  { id: "occupancy", label: "Πληρότητα" },
  { id: "rooms", label: "Δωμάτια" },
  { id: "stays", label: "Διαμονή & κρατήσεις" },
  { id: "forecast", label: "Προβλέψεις" },
];

@Component({
  selector: "app-performance-page",
  standalone: true,
  imports: [CommonModule, AppHeaderComponent],
  template: `
    <main class="page-shell">
      <app-header>
        <p class="eyebrow">Απόδοση</p>
        <h1>Πώς πάει το κατάλυμα</h1>
        <p class="app-band-subtitle">Πληρότητα, μέση τιμή και έσοδα από τις κρατήσεις σας, δίπλα στις τιμές της αγοράς.</p>
      </app-header>

      <section class="page-body">
        <div role="tablist" aria-label="Ενότητες απόδοσης" class="chip-row">
          <button
            *ngFor="let tab of tabs"
            type="button"
            role="tab"
            class="text-button"
            [attr.aria-selected]="tab.id === activeTab()"
            [attr.data-testid]="'performance-tab-' + tab.id"
            (click)="activeTab.set(tab.id)"
          >{{ tab.label }}</button>
        </div>

        <div class="panel" role="tabpanel" data-testid="performance-shell">
          <p *ngIf="status() === null && !error()" class="muted">Φόρτωση…</p>
          <p *ngIf="error()" class="alert alert-error">{{ error() }}</p>
          <p *ngIf="status() as current" class="muted">
            {{ current.has_data ? current.bookings + " κρατήσεις" : "Δεν υπάρχουν ακόμη κρατήσεις" }}
            · η σελίδα χτίζεται (docs/performance/PLAN.md).
          </p>
        </div>
      </section>
    </main>
  `,
})
export class PerformancePageComponent implements OnInit {
  readonly tabs = PERFORMANCE_TABS;
  readonly activeTab = signal<PerformanceTab>("overview");
  readonly status = signal<PerformanceStatus | null>(null);
  readonly error = signal("");

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly performance: PerformanceApiService,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const me = await this.onboarding.currentUser();
      this.status.set(await this.performance.status(me.owned_property_id ?? ""));
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Η απόδοση δεν είναι διαθέσιμη αυτή τη στιγμή.");
    }
  }
}
