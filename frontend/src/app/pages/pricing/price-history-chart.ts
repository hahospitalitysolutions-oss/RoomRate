/**
 * The price-history chart's view model: one dot per search (the median of
 * every competitor's cheapest price in it), the P25-P75 band, the axis ticks
 * and the readout labels. Pure functions, moved out of pricing-page.component.ts
 * unchanged, so the card that draws the chart only renders what is built here.
 */
import type { PriceHistorySeries } from "../../types/pricing";
import { formatEuro, parseObserved } from "./pricing-format";

/** One scrape run as the chart draws it. */
export type HistoryRunPoint = {
  index: number;
  x: number;
  y: number;
  hitX: number;
  hitWidth: number;
  medianLabel: string;
  p25Label: string;
  p75Label: string;
  // "" when the run has too few competitors for a meaningful spread.
  rangeLabel: string;
  competitorCount: number;
  competitorLabel: string;
  dateLabel: string;
  timeLabel: string;
  stampLabel: string;
  ariaLabel: string;
  valueY: number;
  tooltipLeftPct: number;
  tooltipTopPct: number;
  tooltipAlign: "start" | "middle" | "end";
  tooltipBelow: boolean;
  showAxisLabel: boolean;
  isLabelled: boolean;
};

/** One scrape run after the per-competitor prices are collapsed. */
export type AggregatedRun = {
  runIndex: number;
  observedAt: string | null;
  median: number;
  // null when the run has too few competitors for a meaningful spread.
  p25: number | null;
  p75: number | null;
  competitorCount: number;
};

export type PriceHistoryChartView = {
  viewBox: string;
  plotLeft: number;
  plotRight: number;
  plotTop: number;
  plotBottom: number;
  plotHeight: number;
  captionY: number;
  dateLabelY: number;
  timeLabelY: number;
  yTicks: Array<{ y: number; label: string }>;
  bandSegments: string[];
  // "" in small-sample mode: a line through 1-2 dots reads as a trend that
  // the data cannot support.
  linePoints: string;
  points: HistoryRunPoint[];
  labelledPoints: HistoryRunPoint[];
  axisPoints: HistoryRunPoint[];
  smallSample: boolean;
  hasBand: boolean;
  summaryLabel: string;
  ariaLabel: string;
};

// Chart canvas in user units. The SVG keeps its aspect ratio while scaling to
// the container (no preserveAspectRatio="none": that stretch distorted every
// circle and every glyph), so these units are effectively device pixels on a
// desktop-width card.
const CHART_WIDTH = 560;
const CHART_HEIGHT = 240;
const PLOT_LEFT = 62;
const PLOT_RIGHT = 542;
const PLOT_TOP = 30;
const PLOT_BOTTOM = 194;
// Keeps the first/last dot's price label inside the canvas.
const DOT_INSET = 26;
// Beyond this the dots (and their hit targets) stop being separable; the
// summary line says how many runs were left out.
const MAX_PLOTTED_RUNS = 12;
// Up to this many runs every dot carries its price; past it only the first,
// last, cheapest and dearest are labelled and the rest live in the tooltip
// and the table view.
const MAX_LABELLED_RUNS = 4;
const MAX_X_TICKS = 6;
// 1-2 runs cannot show a trend, so the page shows the low-data panel instead
// of a chart.
const SMALL_SAMPLE_RUNS = 2;
// A P25-P75 band needs both a history to spread across and enough
// competitors per run for quartiles to mean anything.
const MIN_BAND_RUNS = 3;
const MIN_BAND_COMPETITORS = 3;
// The y domain is padded instead of stretched edge to edge, so a two-euro
// wobble no longer fills the canvas and reads as a collapse.
const DOMAIN_PAD_RATIO = 0.15;
// Sub-euro movement is not a story. Below this span the domain widens to an
// absolute window instead of a percentage of almost nothing.
const MIN_DOMAIN_SPAN = 2;
// Round tick values, searched finest-first until a step leaves 2-3 of them
// inside the domain.
const TICK_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
const MIN_LABEL_GAP_X = 64;
const MIN_LABEL_GAP_Y = 26;

/**
 * Turn the raw per-(run, competitor) series into the chart's view model.
 *
 * One dot per scrape run = the median of every competitor's cheapest
 * nightly price in that run; the band behind it is the P25-P75 spread of
 * those same per-competitor prices.
 */
export function buildHistoryChart(series: PriceHistorySeries): PriceHistoryChartView | null {
  const allRuns = aggregateRuns(series);
  if (!allRuns.length) {
    return null;
  }
  const runs = allRuns.slice(-MAX_PLOTTED_RUNS);
  const smallSample = runs.length <= SMALL_SAMPLE_RUNS;
  const bandSpans = runs.length < MIN_BAND_RUNS ? [] : groupBandSpans(runs);
  const bandedRuns = new Set<number>(bandSpans.flat());

  // The y domain covers only what is actually drawn, padded on both sides so
  // the extremes never sit pinned to the plot edges.
  const drawnValues = runs.map((run) => run.median);
  for (const index of bandedRuns) {
    drawnValues.push(runs[index].p25 as number, runs[index].p75 as number);
  }
  const [domainMin, domainMax] = paddedDomain(drawnValues);
  const plotHeight = PLOT_BOTTOM - PLOT_TOP;
  const scaleY = (value: number): number =>
    round1(PLOT_BOTTOM - ((value - domainMin) / (domainMax - domainMin)) * plotHeight);

  const firstX = PLOT_LEFT + DOT_INSET;
  const lastX = PLOT_RIGHT - DOT_INSET;
  const xs = runs.map((_, index) =>
    runs.length === 1
      ? round1((firstX + lastX) / 2)
      : round1(firstX + (index * (lastX - firstX)) / (runs.length - 1)),
  );
  const ys = runs.map((run) => scaleY(run.median));

  const medians = runs.map((run) => run.median);
  const lowest = Math.min(...medians);
  const highest = Math.max(...medians);
  const labelled = pickValueLabels(medians, xs, ys);
  const axisStep = Math.ceil(runs.length / MAX_X_TICKS);

  const points: HistoryRunPoint[] = runs.map((run, index) => {
    const x = xs[index];
    const y = ys[index];
    const hasSpread = run.p25 != null && run.p75 != null;
    // The hit band runs midpoint to midpoint, so the whole column answers
    // the pointer instead of the 8px dot.
    const hitLeft = index === 0 ? PLOT_LEFT : round1((xs[index - 1] + x) / 2);
    const hitRight = index === runs.length - 1 ? PLOT_RIGHT : round1((x + xs[index + 1]) / 2);
    const above = y - 12 >= PLOT_TOP + 4;
    const rangeLabel = hasSpread
      ? `${formatEuro(run.p25)} – ${formatEuro(run.p75)}`
      : "";
    const competitorLabel = run.competitorCount === 1
      ? "1 ανταγωνιστής"
      : `${run.competitorCount} ανταγωνιστές`;
    const stampLabel = formatRunStamp(run.observedAt);
    const medianLabel = formatEuro(run.median);
    return {
      index,
      x,
      y,
      hitX: hitLeft,
      hitWidth: round1(hitRight - hitLeft),
      medianLabel,
      p25Label: hasSpread ? formatEuro(run.p25) : "—",
      p75Label: hasSpread ? formatEuro(run.p75) : "—",
      rangeLabel,
      competitorCount: run.competitorCount,
      competitorLabel,
      dateLabel: formatRunDate(run.observedAt),
      timeLabel: formatRunTime(run.observedAt),
      stampLabel,
      ariaLabel: [
        stampLabel,
        `διάμεσος ${medianLabel}`,
        ...(rangeLabel ? [`P25–P75 ${rangeLabel}`] : []),
        competitorLabel,
      ].join(" · "),
      valueY: above ? round1(y - 12) : round1(y + 20),
      tooltipLeftPct: round1((x / CHART_WIDTH) * 100),
      tooltipTopPct: round1((y / CHART_HEIGHT) * 100),
      tooltipAlign: x < CHART_WIDTH * 0.2 ? "start" : x > CHART_WIDTH * 0.8 ? "end" : "middle",
      tooltipBelow: y < PLOT_TOP + plotHeight * 0.3,
      // Stepping back from the newest run keeps the latest date labelled
      // and the labels evenly spaced whatever the run count.
      showAxisLabel: (runs.length - 1 - index) % axisStep === 0,
      isLabelled: labelled.has(index),
    };
  });

  const runWord = runs.length === 1 ? "αναζήτηση" : "αναζητήσεις";
  const countLabel = allRuns.length > runs.length
    ? `Τελευταίες ${runs.length} από ${allRuns.length} αναζητήσεις`
    : `${runs.length} ${runWord}`;
  const spreadLabel = lowest === highest
    ? `διάμεσος ${formatEuro(lowest)}`
    : `εύρος ${formatEuro(lowest)} – ${formatEuro(highest)}`;

  return {
    viewBox: `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`,
    plotLeft: PLOT_LEFT,
    plotRight: PLOT_RIGHT,
    plotTop: PLOT_TOP,
    plotBottom: PLOT_BOTTOM,
    plotHeight,
    captionY: PLOT_TOP - 12,
    dateLabelY: PLOT_BOTTOM + 22,
    timeLabelY: PLOT_BOTTOM + 36,
    yTicks: buildYTicks(domainMin, domainMax).map((value) => ({
      y: scaleY(value),
      label: formatEuro(value),
    })),
    bandSegments: bandSpans.map((span) => bandPolygon(span, runs, xs, scaleY)),
    // 1-2 runs get dots only: a line between them would read as a trend.
    linePoints: smallSample ? "" : points.map((point) => `${point.x},${point.y}`).join(" "),
    points,
    labelledPoints: points.filter((point) => point.isLabelled),
    axisPoints: points.filter((point) => point.showAxisLabel),
    smallSample,
    hasBand: bandSpans.length > 0,
    summaryLabel: `${countLabel} · ${spreadLabel}`,
    ariaLabel: `Ιστορικό τιμών αγοράς: ${countLabel.toLowerCase()}, ${spreadLabel}.`,
  };
}

/** Collapse the per-(run, competitor) points into one chronological run list. */
function aggregateRuns(series: PriceHistorySeries): AggregatedRun[] {
  const byRun = new Map<number, { prices: number[]; observedAt: string | null }>();
  for (const point of series.points || []) {
    if (point.min_price_eur == null || !Number.isFinite(point.min_price_eur)) {
      continue;
    }
    const entry = byRun.get(point.run_index) || { prices: [], observedAt: null };
    entry.prices.push(point.min_price_eur);
    if (point.observed_at && (!entry.observedAt || point.observed_at < entry.observedAt)) {
      entry.observedAt = point.observed_at;
    }
    byRun.set(point.run_index, entry);
  }
  return Array.from(byRun.entries())
    .map(([runIndex, entry]) => {
      const sorted = [...entry.prices].sort((a, b) => a - b);
      // Quartiles of one or two prices are not a spread — they would draw a
      // confident-looking band around what is really a single observation.
      const hasSpread = sorted.length >= MIN_BAND_COMPETITORS;
      return {
        runIndex,
        observedAt: entry.observedAt,
        median: quantile(sorted, 0.5),
        p25: hasSpread ? quantile(sorted, 0.25) : null,
        p75: hasSpread ? quantile(sorted, 0.75) : null,
        competitorCount: sorted.length,
      };
    })
    .sort((a, b) => {
      if (a.observedAt && b.observedAt) {
        return a.observedAt.localeCompare(b.observedAt);
      }
      // run_index 1 is the newest run, so descending index = chronological.
      return b.runIndex - a.runIndex;
    });
}

/**
 * Group the runs into the stretches the band may span.
 *
 * A run without enough competitors breaks the band instead of being
 * interpolated across: the gap is the honest statement that nothing is
 * known about the spread there. A lone qualifying run has no width to draw,
 * so its quartiles stay in the readout and the table only.
 */
function groupBandSpans(runs: AggregatedRun[]): number[][] {
  const spans: number[][] = [];
  let current: number[] = [];
  runs.forEach((run, index) => {
    if (run.p25 != null && run.p75 != null) {
      current.push(index);
      return;
    }
    if (current.length >= 2) {
      spans.push(current);
    }
    current = [];
  });
  if (current.length >= 2) {
    spans.push(current);
  }
  return spans;
}

/** P75 edge left to right, then the P25 edge back — one closed area. */
function bandPolygon(
  span: number[],
  runs: AggregatedRun[],
  xs: number[],
  scaleY: (value: number) => number,
): string {
  const top = span.map((index) => `${xs[index]},${scaleY(runs[index].p75 as number)}`);
  const bottom = [...span].reverse().map((index) => `${xs[index]},${scaleY(runs[index].p25 as number)}`);
  return [...top, ...bottom].join(" ");
}

/**
 * Pad the value range so the chart never stretches its extremes to the edges.
 *
 * A span under MIN_DOMAIN_SPAN — a flat history, or one that moved by a few
 * cents — is widened to an absolute window around its midpoint first. A
 * ratio-only pad there would spend three quarters of the canvas on sub-euro
 * movement (the same lie the old full-stretch scaling told) and would leave
 * the tick search no round value to place. The floor is clamped at zero so a
 * wide spread never opens negative euro space below the cheapest run.
 */
function paddedDomain(values: number[]): [number, number] {
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (high - low < MIN_DOMAIN_SPAN) {
    const middle = (low + high) / 2;
    const half = Math.max(MIN_DOMAIN_SPAN / 2, Math.abs(middle) * 0.05);
    low = middle - half;
    high = middle + half;
  }
  const pad = (high - low) * DOMAIN_PAD_RATIO;
  return [Math.max(0, low - pad), high + pad];
}

/** The 2-3 round values that fit inside the padded domain. */
function buildYTicks(low: number, high: number): number[] {
  for (const step of TICK_STEPS) {
    const ticks: number[] = [];
    for (let value = Math.ceil(low / step) * step; value <= high && ticks.length <= 3; value += step) {
      ticks.push(round1(value));
    }
    if (ticks.length >= 2 && ticks.length <= 3) {
      return ticks;
    }
  }
  return [round1(low), round1(high)];
}

/**
 * Choose which dots carry a visible price.
 *
 * Up to MAX_LABELLED_RUNS runs every dot is labelled (the plan's "dots with
 * their price"); past that a number on every point is unreadable, so only
 * the newest, oldest, cheapest and dearest keep a label — and only where it
 * does not collide with one already placed. Everything else stays reachable
 * through the readout and the table view.
 */
function pickValueLabels(medians: number[], xs: number[], ys: number[]): Set<number> {
  if (medians.length <= MAX_LABELLED_RUNS) {
    return new Set(medians.map((_, index) => index));
  }
  const highest = medians.indexOf(Math.max(...medians));
  const lowest = medians.indexOf(Math.min(...medians));
  const picked = new Set<number>();
  const placed: Array<{ x: number; y: number }> = [];
  for (const candidate of [medians.length - 1, 0, highest, lowest]) {
    if (picked.has(candidate)) {
      continue;
    }
    const collides = placed.some(
      (mark) =>
        Math.abs(mark.x - xs[candidate]) < MIN_LABEL_GAP_X
        && Math.abs(mark.y - ys[candidate]) < MIN_LABEL_GAP_Y,
    );
    if (collides) {
      continue;
    }
    picked.add(candidate);
    placed.push({ x: xs[candidate], y: ys[candidate] });
  }
  return picked;
}

function formatRunDate(observedAt: string | null): string {
  const parsed = parseObserved(observedAt);
  return parsed ? parsed.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit" }) : "—";
}

function formatRunTime(observedAt: string | null): string {
  const parsed = parseObserved(observedAt);
  return parsed
    ? parsed.toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hour12: false })
    : "";
}

function formatRunStamp(observedAt: string | null): string {
  const parsed = parseObserved(observedAt);
  if (!parsed) {
    return "Άγνωστη ημερομηνία";
  }
  const date = parsed.toLocaleDateString("el-GR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  return `${date} ${formatRunTime(observedAt)}`;
}

/** Linear-interpolation quantile over an ASCENDING array (median at q=0.5). */
function quantile(sorted: number[], q: number): number {
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, sorted.length - 1);
  return sorted[lower] + (position - lower) * (sorted[upper] - sorted[lower]);
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}
