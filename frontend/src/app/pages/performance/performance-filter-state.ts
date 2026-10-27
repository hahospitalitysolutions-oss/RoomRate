/**
 * The «Απόδοση» view as it lives in the URL: `?tab=&from=&to=&room=&amounts=&compare=`,
 * so a shared link, a reload and back/forward open the same picture.
 *
 * Pure functions only: the page reads the query params, resolves them against
 * `/status` (seasons, room types) into a `PerformanceView`, and writes a change
 * back as query params. Anything missing or invalid in the URL falls back to
 * the default instead of reaching the API.
 */
import type { AmountBasis, PerformanceFilters, PerformanceStatus } from "../../types/performance";

export type PerformanceTab = "overview" | "occupancy" | "rooms" | "stays" | "forecast";

export const PERFORMANCE_TABS: { id: PerformanceTab; label: string }[] = [
  { id: "overview", label: "Επισκόπηση" },
  { id: "occupancy", label: "Πληρότητα" },
  { id: "rooms", label: "Δωμάτια" },
  { id: "stays", label: "Διαμονή & κρατήσεις" },
  { id: "forecast", label: "Προβλέψεις" },
];

export type PerformanceView = {
  tab: PerformanceTab;
  /** "YYYY-MM-DD", inclusive. */
  from: string;
  to: string;
  /** An owned room type id, or "" for the whole property. */
  room: string;
  amounts: AmountBasis;
  compare: boolean;
};

/** The query params of the view; `null` removes one from the URL. */
export type PerformanceQueryParams = {
  tab: string | null;
  from: string | null;
  to: string | null;
  room: string | null;
  amounts: string | null;
  compare: string | null;
};

type QueryReader = { get(name: string): string | null };

const DEFAULT_TAB: PerformanceTab = "overview";
const DEFAULT_AMOUNTS: AmountBasis = "gross";
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A real calendar date in "YYYY-MM-DD" form (rejects 2026-02-30). */
export function isIsoDate(value: string | null | undefined): value is string {
  const match = value ? DATE_PATTERN.exec(value) : null;
  if (!match) return false;
  const [, year, month, day] = match.map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export function seasonRange(season: number): { from: string; to: string } {
  return { from: `${season}-01-01`, to: `${season}-12-31` };
}

/** The season a range covers exactly, or null for a custom range. */
export function seasonOf(from: string, to: string): number | null {
  const season = Number(from.slice(0, 4));
  const range = seasonRange(season);
  return range.from === from && range.to === to ? season : null;
}

/**
 * The period the page opens on: the newest season that has started (the next
 * one is mostly bookings on the books), else the newest there is, else this year.
 */
export function defaultSeason(status: PerformanceStatus | null, today = new Date()): number {
  const seasons = status?.seasons ?? [];
  const year = today.getFullYear();
  return seasons.find((season) => season <= year) ?? seasons[0] ?? year;
}

/** The view a set of query params asks for, with every invalid or missing value at its default. */
export function readView(query: QueryReader, status: PerformanceStatus | null, today = new Date()): PerformanceView {
  const tab = PERFORMANCE_TABS.some((item) => item.id === query.get("tab"))
    ? (query.get("tab") as PerformanceTab)
    : DEFAULT_TAB;

  const from = query.get("from");
  const to = query.get("to");
  const period = isIsoDate(from) && isIsoDate(to) && from <= to
    ? { from, to }
    : seasonRange(defaultSeason(status, today));

  // A room id is kept only once /status confirms it is one of the property's types.
  const room = query.get("room") ?? "";
  const knownRoom = status?.room_types.some((type) => type.id === room) ?? false;

  return {
    tab,
    ...period,
    room: knownRoom ? room : "",
    amounts: query.get("amounts") === "net" ? "net" : DEFAULT_AMOUNTS,
    compare: query.get("compare") !== "none",
  };
}

/** The query params of a view; defaults are left out so the plain URL stays plain. */
export function toQueryParams(view: PerformanceView): PerformanceQueryParams {
  return {
    tab: view.tab === DEFAULT_TAB ? null : view.tab,
    from: view.from,
    to: view.to,
    room: view.room || null,
    amounts: view.amounts === DEFAULT_AMOUNTS ? null : view.amounts,
    compare: view.compare ? null : "none",
  };
}

/** The query every analysis call takes (`/summary`, `/monthly`, …). */
export function toApiFilters(view: PerformanceView): PerformanceFilters {
  return {
    date_from: view.from,
    date_to: view.to,
    room_type_id: view.room || null,
    amounts: view.amounts,
    compare: view.compare ? "last_year" : "none",
  };
}

/** Two queries that ask the API the same thing (a tab switch must not reload the data). */
export function sameFilters(a: PerformanceFilters, b: PerformanceFilters): boolean {
  return a.date_from === b.date_from
    && a.date_to === b.date_to
    && (a.room_type_id ?? null) === (b.room_type_id ?? null)
    && a.amounts === b.amounts
    && a.compare === b.compare;
}
