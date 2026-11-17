/**
 * Room/rate-plan grouping for the matched competitor card and the competitor
 * detail page. Kept here after main dropped it from competitor-format.ts and
 * map-page.constants.ts (room matching for all types, PR #27).
 */
import type { CompetitorPackage } from "../../types/market";

export type RoomPlanGroup = {
  room_type: string;
  packages: CompetitorPackage[];
  rangeLabel: string | null;
};


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
