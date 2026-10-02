/**
 * One competitor result in the results column: the tracking checkbox, price,
 * room, category chip, distance, rating, availability and (with room
 * matching on) the match chip.
 *
 * The host is the page's `<label class="competitor-card">` itself (attribute
 * selector): the page keeps the map ↔ list linking on it (hover, focus and
 * click, the highlight class and data-hotel-key), and the label keeps the
 * browser's own click-to-tick behaviour for the checkbox inside it.
 */
import { CommonModule } from "@angular/common";
import { Component, input, output } from "@angular/core";

import type { CompetitorMapMarker } from "../../types/market";
import { categoryLabel, formatDistance, formatEuro, formatScore, matchChipClass, roomsLeftLabel } from "./competitor-format";

@Component({
  selector: "label[appCompetitorCard]",
  standalone: true,
  imports: [CommonModule],
  template: `
    <input
      type="checkbox"
      [checked]="selected()"
      (change)="selectionToggle.emit()"
    >
    <span class="competitor-body">
      <span class="card-row">
        <strong>{{ competitor().hotel_name }}</strong>
        <b>{{ formatEuro(competitor().price_per_night_eur) }}</b>
      </span>
      <small>{{ competitor().room_type || competitor().room_type_category || competitor().property_type }}</small>
      <!-- Spec §4.6: the category chip and the distance, each only when the API sent it. -->
      <span
        *ngIf="categoryLabel(competitor().category_match) || formatDistance(competitor().distance_km) || competitor().comparable === false"
        class="card-row compact card-tags"
      >
        <span
          *ngIf="categoryLabel(competitor().category_match) as category"
          class="category-chip"
          [class.is-similar]="competitor().category_match === 'similar'"
          data-testid="card-category"
        >{{ category }}</span>
        <!-- «Μόνο συγκρίσιμα» off: the agent's "not comparable" verdict, muted. -->
        <span
          *ngIf="competitor().comparable === false"
          class="category-chip is-not-comparable"
          data-testid="not-comparable-chip"
        >Μη συγκρίσιμο</span>
        <small *ngIf="formatDistance(competitor().distance_km) as distance" data-testid="card-distance">{{ distance }}</small>
      </span>
      <span class="card-row compact">
        <small>Βαθμολογία {{ competitor().review_score.toFixed(1) }} ({{ competitor().review_count }})</small>
        <small>{{ roomsLeftLabel(competitor().rooms_left) }}</small>
      </span>
      <span *ngIf="matchScore() !== null" class="card-row compact">
        <small>Ταίριασμα</small>
        <span class="match-chip" [ngClass]="matchChipClass(matchScore())">
          {{ formatScore(matchScore()) }}
        </span>
      </span>
    </span>
  `,
})
export class CompetitorCardComponent {
  readonly competitor = input.required<CompetitorMapMarker>();
  /** Ticked for tracking. */
  readonly selected = input.required<boolean>();
  /** The hotel's match score while room matching is on and scored it; null hides the row. */
  readonly matchScore = input<number | null>(null);
  readonly selectionToggle = output<void>();

  readonly formatEuro = formatEuro;
  readonly categoryLabel = categoryLabel;
  readonly formatDistance = formatDistance;
  readonly roomsLeftLabel = roomsLeftLabel;
  readonly matchChipClass = matchChipClass;
  readonly formatScore = formatScore;
}
