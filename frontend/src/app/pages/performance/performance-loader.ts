/**
 * Loading for the «Απόδοση» tabs: each tab reads its calls again whenever its
 * query (filters, target, …) changes. The previous answer stays on screen
 * (dimmed) while the next one loads, and an answer that arrives after a newer
 * query was asked is dropped. Errors show the API's Greek `detail` as it comes.
 */
import { effect, signal, untracked } from "@angular/core";

import { formatMonthLong, formatMonthShort } from "../../components/charts/chart-utils";

const FALLBACK_ERROR = "Τα στοιχεία δεν φορτώθηκαν. Δοκιμάστε ξανά.";

export class PerformanceLoader<Q, T> {
  readonly data = signal<T | null>(null);
  readonly loading = signal(false);
  readonly error = signal("");
  private sequence = 0;
  private lastQuery: Q | null = null;

  /** Create in an injection context (a component field): it registers an effect. */
  constructor(
    query: () => Q | null,
    private readonly fetch: (query: Q) => Promise<T>,
  ) {
    effect(() => {
      const next = query();
      if (next === null) return;
      untracked(() => void this.load(next));
    });
  }

  reload(): void {
    if (this.lastQuery !== null) void this.load(this.lastQuery);
  }

  private async load(query: Q): Promise<void> {
    const sequence = ++this.sequence;
    this.lastQuery = query;
    this.loading.set(true);
    this.error.set("");
    try {
      const data = await this.fetch(query);
      if (sequence !== this.sequence) return;
      this.data.set(data);
    } catch (error) {
      if (sequence !== this.sequence) return;
      this.error.set(error instanceof Error && error.message ? error.message : FALLBACK_ERROR);
    } finally {
      if (sequence === this.sequence) this.loading.set(false);
    }
  }
}

/** Short month labels for a chart axis, with the year when the months span more than one. */
export function monthAxis(months: string[]): { categories: string[]; titles: string[] } {
  const years = new Set(months.map((month) => month.slice(0, 4)));
  return {
    categories: months.map((month) => formatMonthShort(month, years.size > 1)),
    titles: months.map(formatMonthLong),
  };
}

/** «Κλειστό» for a month the property was closed: no occupancy and closed days. Never shown as unsold. */
export function closedNote(month: { occupancy_pct: number | null; closed_days: number }): string | null {
  return month.occupancy_pct === null && month.closed_days > 0 ? "Κλειστό" : null;
}
