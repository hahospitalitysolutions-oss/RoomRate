/**
 * «Αγορά vs εμείς»: the pure aggregations behind the comparison dashboard of
 * the market page and the competitor drill-down.
 *
 * Nothing here reads the network. The rows are the ones the page already
 * holds (`/api/v1/maps/competitors` for the job on screen), the market
 * figures for «Όλη η αγορά» are `/api/v1/market/summary`'s own (the ONE
 * summary of spec §4.7, so the dashboard and the results column can never
 * disagree), and the owner's side is the price the API already knows: the
 * live Booking price of the job, else the onboarding sample price.
 *
 * What the API does not know is said, never invented: the owner's own
 * review score is not collected, so the dashboard shows a dash with the
 * reason instead of a number.
 */
import type { CompetitorMapMarker, MarketSummary, OwnedPropertyRoomType, OwnPropertyMapInfo } from "../../types/market";
import { formatEuro, hotelKey, markerKey } from "./competitor-format";

/** One competitor hotel as the dashboard reads it: its rows collapsed. */
export type HotelComparisonRow = {
  /** hotelKey(): the map marker's identity, and the drill-down's route key. */
  key: string;
  hotelName: string;
  propertyId: string | null;
  /** The cheapest of the hotel's rows: one price per hotel, like the summary. */
  price: number;
  roomType: string;
  reviewScore: number | null;
  reviewCount: number;
  distanceKm: number | null;
  /** The most rooms any of the hotel's rows has left (Booking's «X left»); 0 when unknown. */
  roomsLeft: number;
  /** Any of the hotel's rows is ticked. */
  selected: boolean;
  /** False only when the agent judged every row of the hotel not comparable. */
  comparable: boolean;
};

export type OwnPriceSourceKind = "booking_live" | "onboarding_sample";

/** The owner's side of the comparison. */
export type OwnReference = {
  price: number | null;
  source: OwnPriceSourceKind | null;
  roomLabel: string;
};

export type MarketAggregate = {
  hotels: number;
  min: number | null;
  median: number | null;
  avg: number | null;
  max: number | null;
  avgReview: number | null;
  avgDistanceKm: number | null;
  /** "summary": the server's /market/summary; "rows": computed from the rows on screen. */
  basis: "summary" | "rows";
};

/** Price position: how many hotels of the scope undercut the owner's price. */
export type PricePositionCount = { cheaper: number; total: number };

/** Collapse result rows (one per package) into one row per hotel, cheapest first. */
export function collapseToHotels(
  rows: readonly CompetitorMapMarker[],
  selectedKeys: ReadonlySet<string>,
): HotelComparisonRow[] {
  const byHotel = new Map<string, HotelComparisonRow>();
  for (const row of rows) {
    const price = Number(row.price_per_night_eur);
    if (!Number.isFinite(price) || price <= 0) {
      continue;
    }
    const key = hotelKey(row);
    const selected = selectedKeys.has(markerKey(row));
    const comparable = row.comparable !== false;
    const distance = typeof row.distance_km === "number" && Number.isFinite(row.distance_km) ? row.distance_km : null;
    const review = typeof row.review_score === "number" && row.review_score > 0 ? row.review_score : null;
    const existing = byHotel.get(key);
    if (!existing) {
      byHotel.set(key, {
        key,
        hotelName: row.hotel_name,
        propertyId: row.property_id ?? null,
        price,
        roomType: row.room_type || row.room_type_category || row.property_type || "",
        reviewScore: review,
        reviewCount: row.review_count || 0,
        distanceKm: distance,
        roomsLeft: Number(row.rooms_left) || 0,
        selected,
        comparable,
      });
      continue;
    }
    existing.selected = existing.selected || selected;
    existing.roomsLeft = Math.max(existing.roomsLeft, Number(row.rooms_left) || 0);
    existing.comparable = existing.comparable || comparable;
    if (price < existing.price) {
      existing.price = price;
      existing.roomType = row.room_type || existing.roomType;
    }
    if (existing.distanceKm === null && distance !== null) {
      existing.distanceKm = distance;
    }
    if (existing.reviewScore === null && review !== null) {
      existing.reviewScore = review;
      existing.reviewCount = row.review_count || 0;
    }
  }
  return [...byHotel.values()].sort((a, b) => a.price - b.price || a.hotelName.localeCompare(b.hotelName, "el"));
}

/** The figures of a set of hotels, computed from the rows themselves. */
export function aggregateHotels(hotels: readonly HotelComparisonRow[]): MarketAggregate | null {
  if (!hotels.length) {
    return null;
  }
  const prices = hotels.map((hotel) => hotel.price).sort((a, b) => a - b);
  const reviews = hotels.map((hotel) => hotel.reviewScore).filter((value): value is number => value !== null);
  const distances = hotels.map((hotel) => hotel.distanceKm).filter((value): value is number => value !== null);
  return {
    hotels: hotels.length,
    min: prices[0],
    median: quantile(prices, 0.5),
    avg: mean(prices),
    max: prices[prices.length - 1],
    avgReview: reviews.length ? mean(reviews) : null,
    avgDistanceKm: distances.length ? mean(distances) : null,
    basis: "rows",
  };
}

/**
 * «Όλη η αγορά»: the server's summary for the prices, the count and the
 * rating, so the dashboard says exactly what «Σύνοψη αγοράς» says. The
 * distance is not in the summary, so it comes from the rows. Without a
 * summary with data, the rows answer alone.
 */
export function aggregateMarket(
  summary: MarketSummary | null,
  hotels: readonly HotelComparisonRow[],
): MarketAggregate | null {
  const fromRows = aggregateHotels(hotels);
  if (!summary || summary.total_hotels <= 0) {
    return fromRows;
  }
  return {
    hotels: summary.total_hotels,
    min: finiteOrNull(summary.price_min_eur),
    median: finiteOrNull(summary.price_median_eur),
    avg: finiteOrNull(summary.price_avg_eur),
    max: finiteOrNull(summary.price_max_eur),
    avgReview: summary.avg_review_score > 0 ? summary.avg_review_score : null,
    avgDistanceKm: fromRows?.avgDistanceKm ?? null,
    basis: "summary",
  };
}

/** The owner's price: the job's live Booking price first, else the onboarding sample. */
export function resolveOwnReference(
  own: OwnPropertyMapInfo | null,
  room: OwnedPropertyRoomType | null | undefined,
): OwnReference {
  const live = own?.price_per_night_eur;
  if (typeof live === "number" && Number.isFinite(live) && live > 0) {
    return { price: live, source: "booking_live", roomLabel: own?.room_type || room?.room_type || "" };
  }
  const sample = room?.sample_price_per_night_eur;
  if (typeof sample === "number" && Number.isFinite(sample) && sample > 0) {
    return { price: sample, source: "onboarding_sample", roomLabel: room?.room_type || "" };
  }
  return { price: null, source: null, roomLabel: room?.room_type || own?.room_type || "" };
}

/** Where the owner's number came from, in the words the pricing page uses. */
export function ownSourceLabel(source: OwnPriceSourceKind | null): string {
  if (source === "booking_live") {
    return "Η τιμή σας στο Booking για αυτές τις ημερομηνίες";
  }
  if (source === "onboarding_sample") {
    return "Τιμή αναφοράς από την εγγραφή";
  }
  return "Δεν υπάρχει ακόμη τιμή σας για σύγκριση";
}

export function pricePosition(hotels: readonly HotelComparisonRow[], ownPrice: number | null): PricePositionCount | null {
  if (ownPrice === null || !hotels.length) {
    return null;
  }
  return { cheaper: hotels.filter((hotel) => hotel.price < ownPrice).length, total: hotels.length };
}

/** «Φθηνότερα από εσάς: 3 από 12 καταλύματα» (the pricing page's own wording). */
export function formatPricePosition(position: PricePositionCount | null): string {
  if (!position) {
    return "—";
  }
  const noun = position.total === 1 ? "κατάλυμα" : "καταλύματα";
  return `Φθηνότερα από εσάς: ${position.cheaper} από ${position.total} ${noun}`;
}

/**
 * The owner against a market figure, as words: «12% ακριβότερα», «8%
 * φθηνότερα», «Στο ίδιο επίπεδο» within 1%. "" when either side is missing.
 * Deliberately not colour-coded: a higher price is not by itself bad news.
 */
export function compareLabel(own: number | null, market: number | null): string {
  if (own === null || market === null || market <= 0) {
    return "";
  }
  const pct = ((own - market) / market) * 100;
  if (Math.abs(pct) < 1) {
    return "Στο ίδιο επίπεδο";
  }
  const rounded = Math.round(Math.abs(pct));
  return pct > 0 ? `${rounded}% ακριβότερα` : `${rounded}% φθηνότερα`;
}

/** «+12 €» / «−8 €» / «0 €»: a competitor's price minus the owner's. "" without an own price. */
export function formatSignedEuro(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "";
  }
  const rounded = Math.round(value);
  if (rounded === 0) {
    return formatEuro(0);
  }
  return `${rounded > 0 ? "+" : "−"}${formatEuro(Math.abs(rounded))}`;
}

export function formatRating(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? new Intl.NumberFormat("el-GR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value)
    : "—";
}

export function formatOptionalEuro(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? formatEuro(value) : "—";
}

// ---------------------------------------------------------------------------
// «Κλίμακα τιμών»: one dot per hotel on a price axis, the owner's price and
// the market median as reference marks. Dots that would overlap stack upward
// instead of hiding each other.
// ---------------------------------------------------------------------------

export type LadderDot = {
  key: string;
  hotelName: string;
  price: number;
  priceLabel: string;
  x: number;
  y: number;
  /** Part of the scope being examined (always true for «Όλη η αγορά»). */
  inScope: boolean;
  ariaLabel: string;
};

export type PriceLadderView = {
  viewBox: string;
  width: number;
  height: number;
  plotLeft: number;
  plotRight: number;
  axisY: number;
  ticks: Array<{ x: number; label: string }>;
  dots: LadderDot[];
  own: { x: number; label: string } | null;
  median: { x: number; label: string } | null;
  ariaLabel: string;
};

const LADDER_WIDTH = 560;
const LADDER_LEFT = 24;
const LADDER_RIGHT = 536;
const LADDER_TOP = 34;
const DOT_RADIUS = 5;
const DOT_STEP = 13;
const DOT_MIN_GAP = 12;
const MAX_STACK = 6;
const TICK_STEPS = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000];

export function buildPriceLadder(
  hotels: readonly HotelComparisonRow[],
  inScope: (hotel: HotelComparisonRow) => boolean,
  ownPrice: number | null,
  median: number | null,
): PriceLadderView | null {
  if (!hotels.length) {
    return null;
  }
  const values = hotels.map((hotel) => hotel.price);
  if (ownPrice !== null) {
    values.push(ownPrice);
  }
  if (median !== null) {
    values.push(median);
  }
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (high - low < 10) {
    low -= 5;
    high += 5;
  }
  const pad = (high - low) * 0.06;
  low = Math.max(0, low - pad);
  high += pad;
  const scaleX = (value: number): number =>
    round1(LADDER_LEFT + ((value - low) / (high - low)) * (LADDER_RIGHT - LADDER_LEFT));

  // Stack dots that would overlap: each takes the lowest row whose last dot
  // is far enough to its left. Past MAX_STACK rows they share the top row.
  const lastXByRow: number[] = [];
  const placed = [...hotels].sort((a, b) => a.price - b.price).map((hotel) => {
    const x = scaleX(hotel.price);
    let row = lastXByRow.findIndex((lastX) => x - lastX >= DOT_MIN_GAP);
    if (row === -1) {
      row = lastXByRow.length < MAX_STACK ? lastXByRow.length : MAX_STACK - 1;
    }
    lastXByRow[row] = x;
    return { hotel, x, row };
  });
  const rows = Math.max(1, lastXByRow.length);
  const axisY = LADDER_TOP + rows * DOT_STEP + 8;
  const height = axisY + 30;

  const dots: LadderDot[] = placed.map(({ hotel, x, row }) => {
    const priceLabel = formatEuro(hotel.price);
    return {
      key: hotel.key,
      hotelName: hotel.hotelName,
      price: hotel.price,
      priceLabel,
      x,
      y: axisY - DOT_RADIUS - 4 - row * DOT_STEP,
      inScope: inScope(hotel),
      ariaLabel: `${hotel.hotelName}: ${priceLabel}`,
    };
  });

  const ownLabel = ownPrice !== null ? formatEuro(ownPrice) : "";
  const medianLabel = median !== null ? formatEuro(median) : "";
  const parts = [`${hotels.length} καταλύματα από ${formatEuro(Math.min(...hotels.map((h) => h.price)))} έως ${formatEuro(Math.max(...hotels.map((h) => h.price)))}`];
  if (median !== null) {
    parts.push(`διάμεση τιμή ${medianLabel}`);
  }
  if (ownPrice !== null) {
    parts.push(`η τιμή σας ${ownLabel}`);
  }

  return {
    viewBox: `0 0 ${LADDER_WIDTH} ${height}`,
    width: LADDER_WIDTH,
    height,
    plotLeft: LADDER_LEFT,
    plotRight: LADDER_RIGHT,
    axisY,
    ticks: ladderTicks(low, high).map((value) => ({ x: scaleX(value), label: formatEuro(value) })),
    dots,
    own: ownPrice !== null ? { x: scaleX(ownPrice), label: ownLabel } : null,
    median: median !== null ? { x: scaleX(median), label: medianLabel } : null,
    ariaLabel: `Κλίμακα τιμών: ${parts.join(", ")}.`,
  };
}

export function ladderTicks(low: number, high: number): number[] {
  for (const step of TICK_STEPS) {
    const ticks: number[] = [];
    for (let value = Math.ceil(low / step) * step; value <= high; value += step) {
      ticks.push(value);
    }
    if (ticks.length >= 3 && ticks.length <= 6) {
      return ticks;
    }
  }
  return [Math.round(low), Math.round(high)];
}

export function quantile(sorted: readonly number[], q: number): number {
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, sorted.length - 1);
  return sorted[lower] + (position - lower) * (sorted[upper] - sorted[lower]);
}

function mean(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}
