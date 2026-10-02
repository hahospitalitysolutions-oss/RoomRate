/**
 * «Στατιστικά αγοράς»: which hotels back the numbers, whether the comparison
 * kept to the owner's cancellation class, the statistic rows (median, range,
 * reference price, position, baseline, trends, lead time, runs) and, when
 * there is a recommendation, the statistics' notes.
 *
 * The host is the page's statistics `<div class="panel">` itself (attribute
 * selector). The notes come from the page: without a recommendation the
 * not-enough-data card lists them instead, so the page reads them once.
 */
import { CommonModule } from "@angular/common";
import { Component, computed, input } from "@angular/core";

import type { OwnPriceSource, PricePosition, PriceRecommendationResponse } from "../../types/pricing";
import { formatEuro } from "./pricing-format";

@Component({
  selector: "div[appMarketStatisticsPanel]",
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="section-title">
      <h2>Στατιστικά αγοράς</h2>
    </div>
    <p *ngIf="statsScopeLabel()" class="muted pricing-hint">{{ statsScopeLabel() }}</p>
    <p *ngIf="cancellationClassNote()" class="muted pricing-hint" data-testid="cancellation-class-note">
      {{ cancellationClassNote() }}
    </p>
    <div class="stats-grid">
      <div *ngFor="let entry of statisticEntries()">
        <span>{{ entry.label }}</span>
        <strong>{{ entry.value }}</strong>
      </div>
    </div>
    <!-- Without a recommendation the not-enough-data card lists the notes. -->
    <ul *ngIf="response().recommendation_available && notes().length" class="stat-notes">
      <li *ngFor="let note of notes()" class="muted">{{ note }}</li>
    </ul>
  `,
})
export class MarketStatisticsPanelComponent {
  readonly response = input.required<PriceRecommendationResponse>();
  /** ≤2 comparable hotels: the position row says it is only indicative. */
  readonly smallSample = input(false);
  readonly notes = input<string[]>([]);

  readonly statisticEntries = computed(() => buildStatisticEntries(this.response(), this.smallSample()));
  /** «Βάση: …» — which hotels of the latest search back the statistics; "" when unknown. */
  readonly statsScopeLabel = computed(() => {
    const scope = this.response().statistics.stats_scope;
    if (!scope) {
      return "";
    }
    // The matching agent's comparable set for the owner's room backed the
    // numbers: counted in hotels, singular/plural built whole (Greek inflects
    // both the adjective and the noun).
    if (scope.used === "agent") {
      const comparable = scope.comparable ?? 0;
      return comparable === 1
        ? "Βάση: 1 συγκρίσιμο κατάλυμα (εκτίμηση AI)"
        : `Βάση: ${comparable} συγκρίσιμα καταλύματα (εκτίμηση AI)`;
    }
    const same = `Βάση: ${scope.same_category} ίδιας κατηγορίας`;
    if (scope.used !== "same_plus_similar") {
      return same;
    }
    return `${same} + ${scope.similar} ${scope.similar === 1 ? "παρόμοιο" : "παρόμοια"}`;
  });
  /**
   * Rate plans (spec §5): said only when the backend really compared within
   * the owner's own cancellation class ("matched"). "all" (no package shared
   * the class, the floor fell back to every package) and an older API without
   * the field both say nothing — the sentence would then be untrue.
   */
  readonly cancellationClassNote = computed(() =>
    this.response().statistics.stats_scope?.cancellation_class === "matched"
      ? "Σύγκριση σε τιμές ίδιας πολιτικής ακύρωσης με το δωμάτιό σας."
      : "");
}

function buildStatisticEntries(
  result: PriceRecommendationResponse,
  smallSample: boolean,
): Array<{ label: string; value: string }> {
  const statistics = result.statistics;
  if (!statistics) {
    return [];
  }
  const position = formatPosition(statistics.position);
  return [
    { label: "Διάμεση τιμή αγοράς", value: formatEuro(statistics.market_median_eur) },
    { label: "Χαμηλό εύρος αγοράς", value: formatEuro(statistics.market_p25_eur) },
    { label: "Υψηλό εύρος αγοράς", value: formatEuro(statistics.market_p75_eur) },
    {
      label: referencePriceLabel(result.own_price_source),
      value: formatEuro(statistics.own_reference_price_eur),
    },
    {
      label: "Θέση σας στην αγορά",
      value: smallSample && statistics.position
        ? `${position} (ενδεικτικό — μικρό δείγμα)`
        : position,
    },
    { label: "Στατιστική βάση αναφοράς", value: formatEuro(statistics.statistical_recommendation_eur) },
    // A bare dash read as "no movement"; say what each trend is waiting for.
    {
      label: "Τάση (7 ημερών)",
      value: formatPct(statistics.trend_7d_pct, "— (δεν υπάρχει ακόμη συγκρίσιμη αναζήτηση 7+ ημερών)"),
    },
    {
      label: "Τάση (30 ημερών)",
      value: formatPct(statistics.trend_30d_pct, "— (δεν υπάρχει ακόμη συγκρίσιμη αναζήτηση 30+ ημερών)"),
    },
    // The label already carries the unit («Ημέρες»), so the value is the bare
    // number: «12 ημέρες» beside it would read «Ημέρες … 12 ημέρες».
    { label: "Ημέρες μέχρι την άφιξη", value: String(statistics.lead_time_days) },
    { label: "Αναζητήσεις που συγκρίθηκαν", value: String(statistics.sample_runs) },
  ];
}

function formatPct(value: number | null | undefined, missing = "—"): string {
  if (value == null || !Number.isFinite(value)) {
    return missing;
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** «Φθηνότερα από εσάς: k από n καταλύματα» — a count the owner can check, not a percentile. */
function formatPosition(position: PricePosition | null | undefined): string {
  if (!position) {
    return "—";
  }
  const noun = position.total === 1 ? "κατάλυμα" : "καταλύματα";
  return `Φθηνότερα από εσάς: ${position.cheaper_than_you} από ${position.total} ${noun}`;
}

/** The reference-price row names where its number came from. */
function referencePriceLabel(source: OwnPriceSource | null | undefined): string {
  switch (source) {
    case "booking_live":
      return "Η τιμή σας στο Booking για αυτές τις ημερομηνίες";
    case "onboarding_sample":
      return "Τιμή αναφοράς από την εγγραφή";
    default:
      return "Η τιμή αναφοράς σας";
  }
}
