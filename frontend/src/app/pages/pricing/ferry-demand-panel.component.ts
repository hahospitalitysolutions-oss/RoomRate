/**
 * «Ζήτηση πλοίων»: the ferries into the owner's island over the next three
 * weeks, one cell per arrival day, coloured by the demand of a check-in that
 * day (GET /api/v1/demand/ferries; api/services/ferry_demand_service.py).
 *
 * The level is pickup: each ship against an earlier look at itself (places
 * gone, cheapest fare up), plus sailings added against the same weekday. It
 * reads «—» for the first days of collection, and the panel says why. The
 * day the form's check-in falls on is outlined.
 *
 * The host is the page's `<div class="panel ferry-demand-panel">` itself.
 */
import { CommonModule } from "@angular/common";
import { Component, computed, input } from "@angular/core";
import { RouterLink } from "@angular/router";

import type { DemandLevel, FerryDay, FerryDemandResponse } from "../../types/pricing";
import type { LoadingState } from "./pricing-format";

const WEEKDAYS = ["Κυρ", "Δευ", "Τρί", "Τετ", "Πέμ", "Παρ", "Σάβ"];
const LEVELS: Record<DemandLevel, string> = {
  high: "Υψηλή",
  normal: "Κανονική",
  low: "Χαμηλή",
  unknown: "—",
};
const SHOWN_DAYS = 21;

@Component({
  selector: "div[appFerryDemandPanel]",
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <div class="section-title">
      <h2>Ζήτηση πλοίων</h2>
      <span *ngIf="demand()?.island_port as port" class="muted ferry-port">προς {{ port }}</span>
    </div>

    <p *ngIf="status() === 'loading'" class="muted">Φόρτωση δρομολογίων…</p>
    <p *ngIf="status() === 'error'" class="muted">Η ζήτηση πλοίων δεν είναι διαθέσιμη αυτή τη στιγμή.</p>

    <ng-container *ngIf="status() === 'ready' && demand() as ferries">
      <p *ngIf="ferries.enabled === false" class="muted" data-testid="ferry-off">
        Η ζήτηση πλοίων είναι απενεργοποιημένη. <a class="text-button" routerLink="/settings" fragment="demand">Ενεργοποίηση από τις Ρυθμίσεις</a>
      </p>
      <p *ngIf="ferries.enabled !== false && ferries.status === null" class="muted" data-testid="ferry-pending">
        Το λιμάνι του νησιού σας εντοπίζεται μέσα σε λίγα λεπτά.
        Την ώρα της ημερήσιας συλλογής την ορίζετε στις <a class="text-button" routerLink="/settings" fragment="demand">Ρυθμίσεις</a>.
      </p>
      <p *ngIf="ferries.status === 'no_ferry'" class="muted" data-testid="ferry-none">
        Ο προορισμός σας δεν εξυπηρετείται από πλοία που πουλά η Ferryhopper, οπότε η ζήτηση πλοίων δεν μετρά εδώ.
      </p>
      <ng-container *ngIf="ferries.status === 'ferry' || ferries.status === 'error'">
        <ul class="ferry-routes" aria-label="Γραμμές που παρακολουθούνται">
          <li *ngFor="let route of activeRoutes()" class="factor-chip">{{ route }}</li>
        </ul>
        <p *ngIf="!days().length" class="muted" data-testid="ferry-no-days">
          Τα δρομολόγια συλλέγονται μία φορά τη μέρα· η πρώτη συλλογή δεν έχει γίνει ακόμη.
          <a class="text-button" routerLink="/settings" fragment="demand">Συλλογή τώρα ή αλλαγή ώρας</a>
        </p>
        <ol *ngIf="days().length" class="ferry-days" aria-label="Ζήτηση πλοίων ανά ημέρα άφιξης" data-testid="ferry-days">
          <li
            *ngFor="let day of days()"
            class="ferry-day"
            [ngClass]="['demand-' + day.level, day.travel_date === checkIn() ? 'ferry-day-selected' : '']"
            [attr.title]="dayDetail(day)"
            [attr.data-date]="day.travel_date"
          >
            <span class="ferry-day-date">{{ weekday(day) }} {{ shortDate(day) }}</span>
            <span class="ferry-day-level">{{ levelLabel(day.level) }}</span>
            <span class="ferry-day-sailings">{{ day.sailings }} δρομ.</span>
          </li>
        </ol>
        <p *ngIf="days().length && allUnknown()" class="muted ferry-note" data-testid="ferry-collecting">
          Η ζήτηση φαίνεται μετά από 3 μέρες καθημερινής συλλογής: κάθε πλοίο συγκρίνεται με τον εαυτό του
          (θέσεις που φεύγουν, εισιτήριο που ανεβαίνει).
        </p>
        <p *ngIf="days().length" class="ferry-legend muted">
          <span class="ferry-swatch demand-high"></span> Υψηλή
          <span class="ferry-swatch demand-normal"></span> Κανονική
          <span class="ferry-swatch demand-low"></span> Χαμηλή
          · Παραμονή και ημέρα άφιξης · Πηγή: Ferryhopper<ng-container *ngIf="ferries.observed_on">, {{ observedOn() }}</ng-container>
          · <a class="text-button" routerLink="/settings" fragment="demand">Ρυθμίσεις συλλογής</a>
        </p>
      </ng-container>
    </ng-container>
  `,
})
export class FerryDemandPanelComponent {
  readonly demand = input<FerryDemandResponse | null>(null);
  readonly status = input<LoadingState>("idle");
  /** The form's check-in (YYYY-MM-DD): that arrival day is outlined. */
  readonly checkIn = input("");

  readonly days = computed(() => (this.demand()?.days ?? []).slice(0, SHOWN_DAYS));
  readonly allUnknown = computed(() => this.days().every((day) => day.level === "unknown"));
  readonly activeRoutes = computed(() =>
    (this.demand()?.routes ?? [])
      .filter((route) => route.status !== "disabled")
      .map((route) => `${route.origin_port} → ${route.destination_port}`));
  readonly observedOn = computed(() => {
    const observed = this.demand()?.observed_on;
    return observed ? `ενημέρωση ${observed.slice(8, 10)}/${observed.slice(5, 7)}` : "";
  });

  weekday(day: FerryDay): string {
    return WEEKDAYS[new Date(`${day.travel_date}T12:00:00`).getDay()];
  }

  shortDate(day: FerryDay): string {
    return `${day.travel_date.slice(8, 10)}/${day.travel_date.slice(5, 7)}`;
  }

  levelLabel(level: DemandLevel): string {
    return LEVELS[level] ?? "—";
  }

  /** The cell's hover text: what the colour is made of. */
  dayDetail(day: FerryDay): string {
    const parts = [`${day.sailings} δρομολόγια`];
    if (day.min_price_eur != null) {
      parts.push(`φθηνότερο εισιτήριο ${Math.round(day.min_price_eur)} €`);
    }
    if (day.accommodation_options != null) {
      parts.push(`${day.accommodation_options} θέσεις προς πώληση`);
    }
    if (day.options_change_pct != null) {
      parts.push(`θέσεις ${signed(day.options_change_pct)}`);
    }
    if (day.price_change_pct != null) {
      parts.push(`εισιτήριο ${signed(day.price_change_pct)}`);
    }
    return `${parts.join(" · ")} — ζήτηση: ${LEVELS[day.level] ?? "—"}`;
  }
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${Math.round(value)}%`;
}
