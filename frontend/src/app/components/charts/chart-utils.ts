/**
 * Formatting (el-GR) and scales shared by the SVG charts. Pure functions.
 */
import type { ValueFormat } from "./chart-types";

const EURO = new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const EURO_WHOLE = new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const ONE_DECIMAL = new Intl.NumberFormat("el-GR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const COUNT = new Intl.NumberFormat("el-GR", { maximumFractionDigits: 0 });
const COMPACT = new Intl.NumberFormat("el-GR", { notation: "compact", maximumFractionDigits: 1 });

const MONTHS_SHORT = ["Ιαν", "Φεβ", "Μαρ", "Απρ", "Μάι", "Ιούν", "Ιούλ", "Αύγ", "Σεπ", "Οκτ", "Νοέ", "Δεκ"];
const MONTHS_LONG = [
  "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος",
  "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος",
];

export const NO_VALUE = "—";

/** «1.234,50 €»: amounts always with cents. */
export function formatEuroCents(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? NO_VALUE : EURO.format(value);
}

/** «73,6%». */
export function formatPct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? NO_VALUE : `${ONE_DECIMAL.format(value)}%`;
}

/** Percentage points: «+4,9 μον.». */
export function formatPts(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return NO_VALUE;
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${ONE_DECIMAL.format(Math.abs(value))} μον.`;
}

/** A relative change: «+12,4%». */
export function formatChangePct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return NO_VALUE;
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${ONE_DECIMAL.format(Math.abs(value))}%`;
}

export function formatCount(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? NO_VALUE : COUNT.format(value);
}

export function formatDays(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return NO_VALUE;
  return `${ONE_DECIMAL.format(value)} ${value === 1 ? "ημέρα" : "ημέρες"}`;
}

/** The full reading of a value (tooltip, aria-label). */
export function formatValue(value: number | null | undefined, format: ValueFormat): string {
  switch (format) {
    case "euro": return formatEuroCents(value);
    case "pct": return formatPct(value);
    case "days": return formatDays(value);
    default: return formatCount(value);
  }
}

/** The short reading of an axis tick: no cents, thousands compacted. */
export function formatAxis(value: number, format: ValueFormat): string {
  switch (format) {
    case "euro": return Math.abs(value) >= 10_000 ? `${COMPACT.format(value)} €` : EURO_WHOLE.format(value);
    case "pct": return `${COUNT.format(value)}%`;
    case "days": return COUNT.format(value);
    default: return Math.abs(value) >= 10_000 ? COMPACT.format(value) : COUNT.format(value);
  }
}

/** "2026-05" → «Μάι» (or «Μάι 26» with the year). */
export function formatMonthShort(month: string, withYear = false): string {
  const [year, index] = month.split("-").map(Number);
  const name = MONTHS_SHORT[index - 1] ?? month;
  return withYear ? `${name} ${String(year).slice(2)}` : name;
}

/** "2026-05" → «Μάιος 2026». */
export function formatMonthLong(month: string): string {
  const [year, index] = month.split("-").map(Number);
  return MONTHS_LONG[index - 1] ? `${MONTHS_LONG[index - 1]} ${year}` : month;
}

/**
 * Round axis ticks from 0 (or below, for negatives) up past the largest value.
 * Returns ascending ticks; the last one is the domain's top.
 */
export function niceTicks(min: number, max: number, target = 4): number[] {
  const low = Math.min(0, min);
  const high = max > low ? max : low + 1;
  const rough = (high - low) / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= rough) ?? 10 * magnitude;
  const ticks: number[] = [];
  for (let value = Math.floor(low / step) * step; value < high + step * 0.999; value += step) {
    ticks.push(Math.round(value * 1e6) / 1e6);
  }
  return ticks;
}

/** A linear scale from [d0, d1] to [r0, r1]. */
export function linear(d0: number, d1: number, r0: number, r1: number): (value: number) => number {
  const span = d1 - d0 || 1;
  return (value) => round1(r0 + ((value - d0) / span) * (r1 - r0));
}

export function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/** Finite numbers of a list, nulls dropped. */
export function present(values: (number | null | undefined)[]): number[] {
  return values.filter((value): value is number => value != null && Number.isFinite(value));
}

/**
 * Polyline segments through the non-null points: a null breaks the line
 * instead of being drawn as zero.
 */
export function lineSegments(points: ({ x: number; y: number } | null)[]): string[] {
  const segments: string[] = [];
  let current: string[] = [];
  for (const point of points) {
    if (point) {
      current.push(`${point.x},${point.y}`);
      continue;
    }
    if (current.length) segments.push(current.join(" "));
    current = [];
  }
  if (current.length) segments.push(current.join(" "));
  return segments;
}
