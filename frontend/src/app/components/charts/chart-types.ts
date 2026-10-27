/**
 * Shared inputs of the SVG charts (components/charts/). The charts know
 * nothing about the page that uses them: they take labels, numbers and a
 * value format, and draw. `null` is "no value" (a gap), never zero.
 */

/** How a value reads in the tooltip, the aria-label and the axis. */
export type ValueFormat =
  /** «1.234,50 €» (axis: «1.235 €»). */
  | "euro"
  /** «73,6%». */
  | "pct"
  /** «1.234». */
  | "count"
  /** «12,3 ημέρες». */
  | "days";

/**
 * Aigaio colours, by job: sea for the main series, terracotta for last year
 * or a comparison, sun for an accent; the rest extend a categorical palette.
 */
export type ChartColor = "sea" | "terracotta" | "sun" | "muted" | "sea-tint" | "terracotta-soft" | "ink";

/** The order categorical series take their colour in (channels, countries, …). */
export const CATEGORY_COLORS: ChartColor[] = ["sea", "terracotta", "sun", "muted", "sea-tint", "terracotta-soft", "ink"];

export type ChartSeries = {
  label: string;
  /** One value per category; null leaves a gap. */
  values: (number | null)[];
  color: ChartColor;
  format: ValueFormat;
  /** A dashed line (e.g. last year next to this year). */
  dashed?: boolean;
};

export type LegendItem = {
  label: string;
  color: ChartColor;
  mark: "bar" | "line" | "dashed" | "band" | "dot";
};

/** One line of a tooltip. */
export type TooltipRow = {
  label: string;
  value: string;
  color?: ChartColor;
};

export type ChartTooltip = {
  title: string;
  rows: TooltipRow[];
  /** Anchor in px inside the chart frame. */
  x: number;
  y: number;
};

export type ChartItem = {
  /** What a click reports back (e.g. a room type id). */
  id: string;
  label: string;
  value: number;
  /** A second line under the label, already formatted. */
  hint?: string;
  color?: ChartColor;
};

export type BubblePoint = {
  id: string;
  label: string;
  x: number | null;
  y: number | null;
  size: number;
  color?: ChartColor;
};
