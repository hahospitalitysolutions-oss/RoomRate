/**
 * One hotel of the room-matching list: its best match score, price span and
 * rating, then its rooms — a single plan inline, several plans of one room
 * collapsed into «από … έως …» with a «Πλάνα τιμών» toggle (spec §5), and
 * the agent's reasoning under a package's chip (spec Α.4).
 *
 * The host is the page's `<article class="matched-card">` (attribute
 * selector). Which lists are open stays with the page (it clears them when a
 * new match load lands), so the card only reads `expandedPlanKeys` and asks
 * for a toggle.
 */
import { CommonModule } from "@angular/common";
import { Component, input, output } from "@angular/core";

import type { Competitor } from "../../types/market";
import {
  cancellationChipLabel,
  effectivePlanPrice,
  formatDistance,
  formatExactEuro,
  formatScore,
  matchChipClass,
  planKey,
} from "./competitor-format";
import type { RoomPlanGroup } from "./map-page.constants";

@Component({
  selector: "article[appMatchedCompetitorCard]",
  standalone: true,
  imports: [CommonModule],
  template: `
    <span class="card-row">
      <strong>{{ hotel().hotel_name }}</strong>
      <span class="match-chip" [ngClass]="matchChipClass(hotel().best_match_score)">
        {{ formatScore(hotel().best_match_score) }}
      </span>
    </span>
    <small>
      {{ formatExactEuro(hotel().price_min_eur) }}&ndash;{{ formatExactEuro(hotel().price_max_eur) }}
      &middot; Βαθμολογία {{ hotel().review_score.toFixed(1) }} ({{ hotel().review_count }})
      <ng-container *ngIf="formatDistance(hotel().distance_km) as distance">&middot; {{ distance }}</ng-container>
    </small>
    <span class="matched-packages">
      <ng-container *ngFor="let group of groups()">
        <ng-container *ngIf="!group.rangeLabel">
          <ng-container *ngFor="let pkg of group.packages">
            <!-- No rate-plan data (old rows): today's plain row. -->
            <ng-container *ngIf="!pkg.rate_plan">
              <span class="card-row compact">
                <small>{{ pkg.room_type }}</small>
                <span class="matched-package-meta">
                  <span *ngIf="pkg.comparable === false" class="rate-plan-chip is-not-comparable" data-testid="not-comparable-chip">Μη συγκρίσιμο</span>
                  <small>{{ formatExactEuro(pkg.price_per_night_eur) }}</small>
                  <span class="match-chip" [ngClass]="matchChipClass(pkg.match_score)">
                    {{ formatScore(pkg.match_score) }}
                  </span>
                </span>
              </span>
              <!-- Agents (spec Α.4): the agent's own line under the chip; statistical rows carry null and get nothing. -->
              <small *ngIf="pkg.match_reasoning" class="match-reasoning" data-testid="match-reasoning">{{ pkg.match_reasoning }}</small>
            </ng-container>
            <!-- A room's ONLY plan (spec §5): the guest-visible price and its
                 chips inline — nothing to collapse, so no range or button. -->
            <span *ngIf="pkg.rate_plan" class="card-row compact">
              <small>{{ pkg.room_type }}</small>
              <span class="rate-plan-row single-plan-row" data-testid="single-plan-row">
                <ng-container *ngTemplateOutlet="planStrip; context: { $implicit: pkg }"></ng-container>
              </span>
            </span>
          </ng-container>
        </ng-container>
        <!-- Spec §5: several rate plans of one room collapse into a range and open on demand. -->
        <ng-container *ngIf="group.rangeLabel">
          <span class="card-row compact">
            <small>{{ group.room_type }}</small>
            <span class="matched-package-meta">
              <small data-testid="room-price-range">{{ group.rangeLabel }}</small>
              <button
                type="button"
                class="text-button plans-toggle"
                [attr.aria-expanded]="plansExpanded(group)"
                (click)="plansToggle.emit(group)"
              >Πλάνα τιμών</button>
            </span>
          </span>
          <span *ngIf="plansExpanded(group)" class="rate-plan-list" data-testid="rate-plan-list">
            <span *ngFor="let pkg of group.packages" class="rate-plan-row" data-testid="rate-plan-row">
              <ng-container *ngTemplateOutlet="planStrip; context: { $implicit: pkg }"></ng-container>
            </span>
          </span>
        </ng-container>
      </ng-container>
    </span>
    <!-- One plan's price + chips, rendered identically wherever a plan shows
         (the opened list and a room's only plan) so the two cannot drift. -->
    <ng-template #planStrip let-pkg>
      <b class="rate-plan-price">{{ formatExactEuro(effectivePlanPrice(pkg)) }}</b>
      <span *ngIf="pkg.rate_plan?.has_genius_discount" class="rate-plan-chip">Genius</span>
      <span *ngIf="pkg.rate_plan?.discount_label" class="rate-plan-chip">{{ pkg.rate_plan?.discount_label }}</span>
      <span *ngIf="cancellationChipLabel(pkg.rate_plan?.cancellation_type) as cancellation" class="rate-plan-chip">
        {{ cancellation }}
      </span>
      <span *ngIf="pkg.rate_plan?.payment_label" class="rate-plan-chip">{{ pkg.rate_plan?.payment_label }}</span>
      <span *ngIf="pkg.comparable === false" class="rate-plan-chip is-not-comparable" data-testid="not-comparable-chip">Μη συγκρίσιμο</span>
      <small *ngIf="pkg.meals" class="rate-plan-meals">{{ pkg.meals }}</small>
      <span class="match-chip" [ngClass]="matchChipClass(pkg.match_score)">
        {{ formatScore(pkg.match_score) }}
      </span>
      <!-- Agents (spec Α.4): full-basis, so it wraps onto its own line under the chip. -->
      <small *ngIf="pkg.match_reasoning" class="match-reasoning" data-testid="match-reasoning">{{ pkg.match_reasoning }}</small>
    </ng-template>
  `,
})
export class MatchedCompetitorCardComponent {
  readonly hotel = input.required<Competitor>();
  /** The hotel's packages grouped into rooms (the page memoizes them per match load). */
  readonly groups = input.required<RoomPlanGroup[]>();
  /** The card's place in the list: with the room, the key of an open «Πλάνα τιμών» list. */
  readonly hotelIndex = input.required<number>();
  readonly expandedPlanKeys = input.required<ReadonlySet<string>>();
  readonly plansToggle = output<RoomPlanGroup>();

  readonly matchChipClass = matchChipClass;
  readonly formatScore = formatScore;
  readonly formatExactEuro = formatExactEuro;
  readonly formatDistance = formatDistance;
  readonly effectivePlanPrice = effectivePlanPrice;
  readonly cancellationChipLabel = cancellationChipLabel;

  plansExpanded(group: RoomPlanGroup): boolean {
    return this.expandedPlanKeys().has(planKey(this.hotelIndex(), group));
  }
}
