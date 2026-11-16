/**
 * Pure formatting and identity helpers shared by the map page, its cards and
 * the map itself. Moved out of map-page.component.ts unchanged: every one of
 * them was a method that read nothing but its arguments.
 */
import type { CategoryMatch, CompetitorMapMarker } from "../../types/market";

export function formatEuro(value: number): string {
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(value || 0);
}

/** «0,4 km»: Greek decimal comma, one decimal (spec §4.5); empty when the distance is unknown. */
export function formatDistance(km: number | null | undefined): string {
  if (typeof km !== "number" || !Number.isFinite(km)) {
    return "";
  }
  return `${new Intl.NumberFormat("el-GR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(km)} km`;
}

/** «10 km», «7,5 km»: at most one decimal, with the Greek comma. */
export function formatRadius(km: number): string {
  return `${new Intl.NumberFormat("el-GR", { maximumFractionDigits: 1 }).format(km)} km`;
}

/** «Ίδια κατηγορία» / «Παρόμοιο» (spec §3.6); empty when an older API sends no match. */
export function categoryLabel(match: CategoryMatch | null | undefined): string {
  if (match === "same") {
    return "Ίδια κατηγορία";
  }
  return match === "similar" ? "Παρόμοιο" : "";
}

export function roomsLeftLabel(roomsLeft: number): string {
  return roomsLeft === 1 ? "1 διαθέσιμο δωμάτιο" : `${roomsLeft} διαθέσιμα δωμάτια`;
}

export function matchChipClass(score: number | null | undefined): string {
  if (score == null) {
    return "match-chip-low";
  }
  if (score >= 75) {
    return "match-chip-high";
  }
  if (score >= 45) {
    return "match-chip-mid";
  }
  return "match-chip-low";
}

export function formatScore(score: number | null | undefined): string {
  return score == null ? "—" : `${Math.round(score)}%`;
}

/** The two cancellation classes worth a chip; anything unknown gets none rather than a guess. */
export function cancellationChipLabel(type: string | null | undefined): string {
  if (type === "free_cancellation") {
    return "Δωρεάν ακύρωση";
  }
  if (type === "non_refundable") {
    return "Μη επιστρέψιμη";
  }
  return "";
}

export function markerKey(marker: CompetitorMapMarker): string {
  return marker.room_package_id || marker.property_id || `${marker.hotel_name}-${marker.latitude}-${marker.longitude}`;
}

/**
 * The map marker's identity: one marker per hotel, whatever package a row
 * carries (spec §4.5). The cards carry it too, so a card and its marker can
 * find each other.
 */
export function hotelKey(marker: CompetitorMapMarker): string {
  return marker.property_id || marker.hotel_name;
}

export function hasValidCoordinates(marker: CompetitorMapMarker): boolean {
  return Number.isFinite(marker.latitude)
    && Number.isFinite(marker.longitude)
    && marker.latitude >= -90
    && marker.latitude <= 90
    && marker.longitude >= -180
    && marker.longitude <= 180;
}
