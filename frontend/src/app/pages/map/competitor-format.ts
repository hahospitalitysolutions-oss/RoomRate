/**
 * Pure formatting and identity helpers shared by the map page, its cards and
 * the map itself. Moved out of map-page.component.ts unchanged: every one of
 * them was a method that read nothing but its arguments.
 */
import type { CategoryMatch, CompetitorMapMarker, CompetitorPackage } from "../../types/market";
import type { RoomPlanGroup } from "./map-page.constants";

export function formatEuro(value: number): string {
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(value || 0);
}

/**
 * The ONE format for every price on a matched card — the header min/max,
 * the room ranges and the plan rows: whole euros drop the cents (110 €),
 * anything fractional keeps exactly two (87,50 €). One card must never mix
 * a rounded 88 € with the 87,50 € it stands for, and two plans a few cents
 * apart must never LOOK identical.
 */
export function formatExactEuro(value: number): string {
  const amount = value || 0;
  const decimals = Number.isInteger(amount) ? 0 : 2;
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(amount);
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

/**
 * The price the guest actually sees for a package (spec §5): the plan's
 * discounted per-night price, falling back to the main column when the plan
 * carries no discounted one (or predates the rate-plan columns).
 */
export function effectivePlanPrice(pkg: CompetitorPackage): number {
  return pkg.rate_plan?.discounted_price_per_night_eur ?? pkg.price_per_night_eur;
}

/** One group per room_type, rooms in order of first appearance, packages in server order. */
export function buildRoomGroups(packages: CompetitorPackage[]): RoomPlanGroup[] {
  const byRoom = new Map<string, CompetitorPackage[]>();
  for (const pkg of packages) {
    const room = byRoom.get(pkg.room_type);
    if (room) {
      room.push(pkg);
    } else {
      byRoom.set(pkg.room_type, [pkg]);
    }
  }
  return [...byRoom.entries()].map(([room_type, roomPackages]) => ({
    room_type,
    packages: roomPackages,
    rangeLabel: roomRangeLabel(roomPackages),
  }));
}

/**
 * «από {min} € έως {max} €» over the room's effective prices (spec §5) —
 * only for a room with MORE THAN ONE package and at least some rate-plan
 * data. Old rows (rate_plan null everywhere) keep today's rendering.
 */
function roomRangeLabel(packages: CompetitorPackage[]): string | null {
  if (packages.length < 2 || !packages.some((pkg) => pkg.rate_plan)) {
    return null;
  }
  const prices = packages.map((pkg) => effectivePlanPrice(pkg));
  return `από ${formatExactEuro(Math.min(...prices))} έως ${formatExactEuro(Math.max(...prices))}`;
}

// The card's index, not the hotel's name: Competitor rows carry no property
// id, and one name can legitimately appear twice in a list.
export function planKey(hotelIndex: number, group: RoomPlanGroup): string {
  return `${hotelIndex}|${group.room_type}`;
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
