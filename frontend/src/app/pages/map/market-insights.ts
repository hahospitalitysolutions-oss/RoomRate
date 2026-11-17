/**
 * The detailed reads of «Αγορά vs εμείς», below the headline cards: what the
 * market charges by guest rating and by distance, how its median has moved
 * from search to search, and how little is left to book. Pure functions over
 * the rows the page already holds (the hotels of the search and the price
 * history of the same market), so they are tested through the dashboard.
 */
import type { PriceHistorySeries } from "../../types/pricing";
import { aggregateRuns } from "../pricing/price-history-chart";
import { formatEuro } from "./competitor-format";
import { compareLabel, formatRating, HotelComparisonRow, ladderTicks, quantile } from "./market-comparison";

// ---------------------------------------------------------------------------
// Price by band (distance, rating)
// ---------------------------------------------------------------------------

export type PriceBand = {
  id: string;
  label: string;
  count: number;
  median: number;
  medianLabel: string;
  /** «Εσείς: 12% φθηνότερα»; "" without an own price. */
  gap: string;
  /** Bar length against the largest figure on the chart (own price included). */
  widthPct: number;
};

type BandDef = { id: string; label: string; test: (value: number) => boolean };

const DISTANCE_BANDS: BandDef[] = [
  { id: "near", label: "Έως 1 km", test: (km) => km <= 1 },
  { id: "mid", label: "1 έως 3 km", test: (km) => km > 1 && km <= 3 },
  { id: "far", label: "Πάνω από 3 km", test: (km) => km > 3 },
];

const RATING_BANDS: BandDef[] = [
  { id: "top", label: "9 και πάνω", test: (score) => score >= 9 },
  { id: "good", label: "8 έως 8,9", test: (score) => score >= 8 && score < 9 },
  { id: "low", label: "Κάτω από 8", test: (score) => score < 8 },
];

function bands(
  hotels: readonly HotelComparisonRow[],
  pick: (hotel: HotelComparisonRow) => number | null,
  defs: BandDef[],
  ownPrice: number | null,
): PriceBand[] {
  const filled = defs
    .map((def) => {
      const prices = hotels
        .filter((hotel) => {
          const value = pick(hotel);
          return value !== null && def.test(value);
        })
        .map((hotel) => hotel.price)
        .sort((a, b) => a - b);
      return { def, prices };
    })
    .filter(({ prices }) => prices.length > 0);
  const top = Math.max(ownPrice ?? 0, ...filled.map(({ prices }) => quantile(prices, 0.5)));
  return filled.map(({ def, prices }) => {
    const median = quantile(prices, 0.5);
    const gap = compareLabel(ownPrice, median);
    return {
      id: def.id,
      label: def.label,
      count: prices.length,
      median,
      medianLabel: formatEuro(median),
      gap: gap ? `Εσείς: ${gap}` : "",
      widthPct: top > 0 ? Math.round((median / top) * 1000) / 10 : 0,
    };
  });
}

/** Median price within 1 km, 1-3 km and beyond, of the hotels whose distance is known. */
export function distanceBands(hotels: readonly HotelComparisonRow[], ownPrice: number | null): PriceBand[] {
  return bands(hotels, (hotel) => hotel.distanceKm, DISTANCE_BANDS, ownPrice);
}

/** Median price by guest rating (9+, 8-8.9, under 8), of the hotels that have one. */
export function ratingBands(hotels: readonly HotelComparisonRow[], ownPrice: number | null): PriceBand[] {
  return bands(hotels, (hotel) => hotel.reviewScore, RATING_BANDS, ownPrice);
}

// ---------------------------------------------------------------------------
// Price against rating (scatter)
// ---------------------------------------------------------------------------

export type ValueDot = {
  key: string;
  hotelName: string;
  x: number;
  y: number;
  inScope: boolean;
  priceLabel: string;
  ratingLabel: string;
};

export type ValueScatterView = {
  viewBox: string;
  width: number;
  height: number;
  plotLeft: number;
  plotRight: number;
  plotTop: number;
  plotBottom: number;
  xTicks: Array<{ x: number; label: string }>;
  yTicks: Array<{ y: number; label: string }>;
  dots: ValueDot[];
  own: { y: number; label: string } | null;
  median: { y: number; label: string } | null;
  /** Hotels left out because Booking shows no rating for them. */
  unrated: number;
  ariaLabel: string;
};

// Drawn for a half-width card (about 300-360 px): the labels stay readable without scaling down.
const SCATTER_WIDTH = 360;
const SCATTER_HEIGHT = 236;
const SCATTER_LEFT = 44;
const SCATTER_RIGHT = 350;
const SCATTER_TOP = 12;
const SCATTER_BOTTOM = 198;

export function buildValueScatter(
  hotels: readonly HotelComparisonRow[],
  inScope: (hotel: HotelComparisonRow) => boolean,
  ownPrice: number | null,
  median: number | null,
): ValueScatterView | null {
  const rated = hotels.filter((hotel) => hotel.reviewScore !== null);
  if (rated.length < 2) {
    return null;
  }
  const scores = rated.map((hotel) => hotel.reviewScore as number);
  const xLow = Math.max(0, Math.floor(Math.min(...scores) * 2) / 2 - 0.5);
  const xHigh = Math.min(10, Math.ceil(Math.max(...scores) * 2) / 2 + 0.5);
  const prices = rated.map((hotel) => hotel.price);
  if (ownPrice !== null) prices.push(ownPrice);
  if (median !== null) prices.push(median);
  let yLow = Math.min(...prices);
  let yHigh = Math.max(...prices);
  if (yHigh - yLow < 10) {
    yLow -= 5;
    yHigh += 5;
  }
  const pad = (yHigh - yLow) * 0.08;
  yLow = Math.max(0, yLow - pad);
  yHigh += pad;

  const scaleX = (value: number) => round1(SCATTER_LEFT + ((value - xLow) / (xHigh - xLow || 1)) * (SCATTER_RIGHT - SCATTER_LEFT));
  const scaleY = (value: number) => round1(SCATTER_BOTTOM - ((value - yLow) / (yHigh - yLow)) * (SCATTER_BOTTOM - SCATTER_TOP));

  const xStep = xHigh - xLow > 4 ? 1 : 0.5;
  const xTicks: Array<{ x: number; label: string }> = [];
  for (let value = Math.ceil(xLow / xStep) * xStep; value <= xHigh + 1e-9; value += xStep) {
    xTicks.push({ x: scaleX(value), label: formatRating(value) });
  }

  return {
    viewBox: `0 0 ${SCATTER_WIDTH} ${SCATTER_HEIGHT}`,
    width: SCATTER_WIDTH,
    height: SCATTER_HEIGHT,
    plotLeft: SCATTER_LEFT,
    plotRight: SCATTER_RIGHT,
    plotTop: SCATTER_TOP,
    plotBottom: SCATTER_BOTTOM,
    xTicks,
    yTicks: ladderTicks(yLow, yHigh).map((value) => ({ y: scaleY(value), label: formatEuro(value) })),
    dots: rated.map((hotel) => ({
      key: hotel.key,
      hotelName: hotel.hotelName,
      x: scaleX(hotel.reviewScore as number),
      y: scaleY(hotel.price),
      inScope: inScope(hotel),
      priceLabel: formatEuro(hotel.price),
      ratingLabel: formatRating(hotel.reviewScore),
    })),
    own: ownPrice !== null ? { y: scaleY(ownPrice), label: `Εσείς ${formatEuro(ownPrice)}` } : null,
    median: median !== null ? { y: scaleY(median), label: `Διάμεση ${formatEuro(median)}` } : null,
    unrated: hotels.length - rated.length,
    ariaLabel: `Τιμή και βαθμολογία ${rated.length} καταλυμάτων`
      + (ownPrice !== null ? `, η τιμή σας ${formatEuro(ownPrice)}` : ""),
  };
}

/**
 * The one sentence the scatter is for: how many hotels rated at least as
 * well as the market's median rating charge less than the owner.
 */
export function valueSentence(hotels: readonly HotelComparisonRow[], ownPrice: number | null): string {
  const scores = hotels.map((hotel) => hotel.reviewScore).filter((score): score is number => score !== null).sort((a, b) => a - b);
  if (ownPrice === null || scores.length < 2) {
    return "";
  }
  const medianScore = quantile(scores, 0.5);
  const strong = hotels.filter((hotel) => hotel.reviewScore !== null && hotel.reviewScore >= medianScore);
  const cheaper = strong.filter((hotel) => hotel.price < ownPrice).length;
  return `${cheaper} από τα ${strong.length} καταλύματα με βαθμολογία ${formatRating(medianScore)} και πάνω χρεώνουν λιγότερο από εσάς.`;
}

// ---------------------------------------------------------------------------
// Trend (price history) and availability
// ---------------------------------------------------------------------------

export type MarketTrendView = {
  runs: number;
  latestLabel: string;
  /** «+6% από την προηγούμενη αναζήτηση» */
  sinceLast: string;
  /** «+12% από την πρώτη (12/10)» when there are three or more searches. */
  sinceFirst: string;
  direction: "up" | "down" | "flat";
  /** Sparkline, viewBox 0 0 120 36. */
  sparkline: string;
};

const SPARK_WIDTH = 120;
const SPARK_HEIGHT = 36;

export function buildMarketTrend(series: PriceHistorySeries | null): MarketTrendView | null {
  const runs = series ? aggregateRuns(series) : [];
  if (runs.length < 2) {
    return null;
  }
  const medians = runs.map((run) => run.median);
  const latest = medians[medians.length - 1];
  const previous = medians[medians.length - 2];
  const first = medians[0];
  const low = Math.min(...medians);
  const high = Math.max(...medians);
  const span = high - low || 1;
  const scaleY = (value: number) => round1(SPARK_HEIGHT - 3 - ((value - low) / span) * (SPARK_HEIGHT - 6));
  const step = SPARK_WIDTH / (medians.length - 1);
  const change = pctChange(previous, latest);
  return {
    runs: runs.length,
    latestLabel: formatEuro(latest),
    sinceLast: `${formatPct(change)} από την προηγούμενη αναζήτηση`,
    sinceFirst: runs.length >= 3 ? `${formatPct(pctChange(first, latest))} από την πρώτη${firstDate(runs[0].observedAt)}` : "",
    direction: Math.abs(change) < 1 ? "flat" : change > 0 ? "up" : "down",
    sparkline: medians.map((value, index) => `${round1(index * step)},${scaleY(value)}`).join(" "),
  };
}

export type AvailabilityView = {
  total: number;
  /** Hotels with one or two rooms left. */
  scarce: number;
  scarcePct: number;
  /** The scarce hotels' median price against the rest: demand shows up as price. */
  scarceMedianLabel: string;
  restMedianLabel: string;
  sentence: string;
};

export function buildAvailability(hotels: readonly HotelComparisonRow[]): AvailabilityView | null {
  const known = hotels.filter((hotel) => hotel.roomsLeft > 0);
  if (!known.length) {
    return null;
  }
  const scarce = known.filter((hotel) => hotel.roomsLeft <= 2);
  const rest = known.filter((hotel) => hotel.roomsLeft > 2);
  const scarcePct = Math.round((scarce.length / known.length) * 100);
  const median = (rows: HotelComparisonRow[]) => (rows.length ? formatEuro(quantile(rows.map((h) => h.price).sort((a, b) => a - b), 0.5)) : "—");
  let sentence = "Υπάρχει ακόμη αρκετή διαθεσιμότητα στην αγορά.";
  if (scarcePct >= 50) {
    sentence = "Η αγορά γεμίζει: τα περισσότερα καταλύματα έχουν 1-2 δωμάτια ελεύθερα. Υπάρχει περιθώριο για υψηλότερη τιμή.";
  } else if (scarcePct >= 25) {
    sentence = "Αρκετά καταλύματα έχουν 1-2 δωμάτια ελεύθερα: η ζήτηση ανεβαίνει.";
  }
  return {
    total: known.length,
    scarce: scarce.length,
    scarcePct,
    scarceMedianLabel: median(scarce),
    restMedianLabel: median(rest),
    sentence,
  };
}

function pctChange(from: number, to: number): number {
  return from > 0 ? ((to - from) / from) * 100 : 0;
}

function formatPct(value: number): string {
  const rounded = Math.round(value);
  if (rounded === 0) return "Σταθερή";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)}%`;
}

function firstDate(observedAt: string | null): string {
  if (!observedAt) return "";
  const date = new Date(observedAt);
  return Number.isNaN(date.getTime()) ? "" : ` (${date.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit" })})`;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}
