export type StayDates = {
  checkIn: string;
  checkOut: string;
};

/**
 * Return a future stay window using local calendar dates.
 *
 * Using local date parts avoids UTC rollover around midnight in Europe/Athens.
 */
const DEFAULT_LEAD_DAYS = 30;
const DEFAULT_NIGHTS = 4;

export function defaultStayDates(
  reference: Date = new Date(),
  leadDays = DEFAULT_LEAD_DAYS,
  nights = DEFAULT_NIGHTS,
): StayDates {
  const checkIn = atLocalMidnight(reference);
  checkIn.setDate(checkIn.getDate() + leadDays);
  const checkOut = new Date(checkIn);
  checkOut.setDate(checkOut.getDate() + nights);
  return {
    checkIn: formatLocalDate(checkIn),
    checkOut: formatLocalDate(checkOut),
  };
}

export function isPastLocalDate(value: string, reference: Date = new Date()): boolean {
  if (!value) {
    return false;
  }
  return value < formatLocalDate(atLocalMidnight(reference));
}

/**
 * Return a bookable stay window, rolling a past one forward.
 *
 * Restoring a completed job replays the dates it was scraped for, which go
 * stale as soon as that check-in passes. The backend rejects a past check_in,
 * so replaying them verbatim left every action (new search, price
 * recommendation) refusing with a validation error until the user hand-edited
 * both fields. The stay LENGTH is preserved so the restored market stays
 * comparable; a window already in the future is returned untouched.
 */
export function ensureFutureStay(
  checkIn: string,
  checkOut: string,
  reference: Date = new Date(),
): StayDates {
  if (!checkIn || !checkOut) {
    return defaultStayDates(reference);
  }
  if (!isPastLocalDate(checkIn, reference)) {
    return { checkIn, checkOut };
  }
  const nights = Math.round(
    (Date.parse(`${checkOut}T00:00:00`) - Date.parse(`${checkIn}T00:00:00`)) / 86_400_000,
  );
  return defaultStayDates(reference, DEFAULT_LEAD_DAYS, nights > 0 ? nights : DEFAULT_NIGHTS);
}

function atLocalMidnight(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function formatLocalDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
