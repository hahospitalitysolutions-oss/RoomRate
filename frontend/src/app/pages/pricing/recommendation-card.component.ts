/**
 * The recommendation card: the price headline, its range, how sure and from
 * where, one sentence of why, the key factors, the full reasoning behind
 * «Γιατί αυτή η τιμή;» and the audit footer.
 *
 * The host is the page's `<div class="panel recommendation-card">` itself
 * (attribute selector). «Γιατί αυτή η τιμή;» is closed by default and closed
 * again for every new recommendation.
 */
import { CommonModule } from "@angular/common";
import { Component, computed, input, linkedSignal } from "@angular/core";

import type { PriceRecommendation, PriceRecommendationResponse } from "../../types/pricing";
import { firstSentence, formatEuro, formatEuroCeil, formatEuroFloor } from "./pricing-format";

@Component({
  selector: "div[appRecommendationCard]",
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="recommendation-headline">
      <div class="recommendation-figure">
        <h2 class="recommendation-label">Προτεινόμενη τιμή ανά βράδυ</h2>
        <div class="recommendation-price">{{ formatEuro(recommendation().recommended_price_eur) }}</div>
        <p class="recommendation-range">
          Εύρος {{ formatEuroFloor(recommendation().price_range_low_eur) }} &ndash; {{ formatEuroCeil(recommendation().price_range_high_eur) }}
        </p>
      </div>
      <div class="chip-row recommendation-pills">
        <span class="confidence-pill" [ngClass]="confidenceChipClass()" data-testid="confidence-pill">
          {{ confidenceLabel() }}
        </span>
        <span class="source-chip" data-testid="source-pill">
          {{ recommendation().source === "agent" ? "AI agent" : "Στατιστική" }}
        </span>
      </div>
    </div>
    <p *ngIf="smallSampleNotice()" class="alert small-sample-notice">
      {{ smallSampleNotice() }}
    </p>
    <p *ngIf="reasoningSummary()" class="recommendation-summary">{{ reasoningSummary() }}</p>
    <ul *ngIf="recommendation().key_factors.length" class="key-factors" aria-label="Βασικοί παράγοντες">
      <li *ngFor="let factor of recommendation().key_factors" class="factor-chip">{{ factor }}</li>
    </ul>
    <!-- A one-sentence reasoning IS its summary: a toggle would only
         repeat it, so it appears when there is more to read. -->
    <div *ngIf="hasMoreReasoning()" class="reasoning-disclosure">
      <button
        type="button"
        class="text-button reasoning-toggle"
        [attr.aria-expanded]="reasoningOpen()"
        aria-controls="recommendation-reasoning"
        (click)="reasoningOpen.set(!reasoningOpen())"
      >
        Γιατί αυτή η τιμή;
      </button>
      <p *ngIf="reasoningOpen()" id="recommendation-reasoning" class="recommendation-reasoning">
        {{ recommendation().reasoning }}
      </p>
    </div>
    <p *ngIf="response().audit_id || response().cached" class="recommendation-footer">
      <span *ngIf="response().cached">Από προσωρινή μνήμη</span>
      <span *ngIf="response().audit_id">
        Ελεγμένη απόφαση {{ response().audit_id }}<ng-container *ngIf="response().model_version"> · {{ response().model_version }}</ng-container>
      </span>
    </p>
  `,
})
export class RecommendationCardComponent {
  readonly recommendation = input.required<PriceRecommendation>();
  /** The whole answer, for the audit footer (audit id, cache, model). */
  readonly response = input.required<PriceRecommendationResponse>();
  /** The small-sample warning, or "" when the sample is not small (or unknown). */
  readonly smallSampleNotice = input("");

  /** The card's one-line «why»: the reasoning's first sentence. */
  readonly reasoningSummary = computed(() => firstSentence(this.recommendation().reasoning ?? ""));
  /** True when the reasoning says more than its summary, so the toggle has something to open. */
  readonly hasMoreReasoning = computed(() =>
    (this.recommendation().reasoning ?? "").trim().length > this.reasoningSummary().length);
  /** «Γιατί αυτή η τιμή;» — closed by default and closed again for every new recommendation. */
  readonly reasoningOpen = linkedSignal({ source: this.recommendation, computation: () => false });

  readonly formatEuro = formatEuro;
  readonly formatEuroFloor = formatEuroFloor;
  readonly formatEuroCeil = formatEuroCeil;

  /** Υψηλή = green, Μέτρια = amber, anything else = the neutral grey of «Χαμηλή». */
  confidenceChipClass(): string {
    switch (this.recommendation().confidence) {
      case "high":
        return "confidence-high";
      case "medium":
        return "confidence-medium";
      default:
        return "confidence-low";
    }
  }

  /**
   * The confidence chip's text, MAPPED from the enum rather than interpolated.
   * The English chip concatenated the raw API value («medium confidence»);
   * in Greek the adjective has to agree with «βεβαιότητα», so an interpolated
   * `{{ rec.confidence }} βεβαιότητα` would both leak the English enum value
   * and be ungrammatical. Falls back to the low label on an unknown value,
   * mirroring confidenceChipClass above — an unrecognised confidence must
   * never read as MORE certain than it is.
   */
  confidenceLabel(): string {
    switch (this.recommendation().confidence) {
      case "high":
        return "Υψηλή βεβαιότητα";
      case "medium":
        return "Μέτρια βεβαιότητα";
      default:
        return "Χαμηλή βεβαιότητα";
    }
  }
}
