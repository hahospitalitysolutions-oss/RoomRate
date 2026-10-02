/**
 * Pure formatting helpers of the pricing page, shared by the page and its
 * cards. Moved out of pricing-page.component.ts unchanged.
 */

export type LoadingState = "idle" | "loading" | "ready" | "error";

export function formatEuro(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(value);
}

/**
 * Range bounds rounded OUTWARD — floor the low bound, ceil the high bound
 * — instead of each rounding independently to the nearest euro. Nearest-
 * euro rounding can NARROW a range (305.5-306.5 collapsed to "306 € –
 * 307 €", silently dropping the true 305.5-305.99 slice); floor/ceil
 * guarantees the shown range always CONTAINS the true range. Chosen over
 * showing one decimal so the range keeps the same 0-decimal styling as
 * every other price on this card.
 */
export function formatEuroFloor(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return "—";
  }
  return formatEuro(Math.floor(value));
}

export function formatEuroCeil(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return "—";
  }
  return formatEuro(Math.ceil(value));
}

/**
 * The first sentence of `text`: up to the first «.», «!», «?» or Greek «;»
 * that is followed by whitespace or the end, so a decimal («110.5 €») never
 * cuts it short. The whole (trimmed) text when it has no such stop.
 */
export function firstSentence(text: string): string {
  const trimmed = text.trim();
  // The reasoning is always Greek (the prompt asks for it), where «;» is the
  // question mark: usually the ASCII semicolon (U+037E normalizes to it), so
  // both end a sentence, like «.», «!» and «?».
  const match = /^[\s\S]*?[.!?;;](?=\s|$)/.exec(trimmed);
  return match ? match[0] : trimmed;
}

export function parseObserved(observedAt: string | null): Date | null {
  if (!observedAt) {
    return null;
  }
  const parsed = new Date(observedAt);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** An API timestamp as «dd/MM/yyyy» in the viewer's calendar; "" when missing or unreadable. */
export function formatGreekDate(stamp: string | null | undefined): string {
  const parsed = parseObserved(stamp ?? null);
  return parsed
    ? parsed.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit", year: "numeric" })
    : "";
}
