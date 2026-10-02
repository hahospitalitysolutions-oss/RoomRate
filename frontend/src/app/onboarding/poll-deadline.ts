export type DeadlineOutcome<T> =
  | { kind: "resolved"; value: T }
  | { kind: "rejected" }
  | { kind: "cancelled" }
  | { kind: "expired" };

/** Remaining monotonic time, clamped for safe timer scheduling. */
export function remainingDeadlineMs(deadline: number): number {
  return Math.max(0, deadline - performance.now());
}

/**
 * Settles an operation no later than a monotonic deadline.
 *
 * The operation factory is never invoked after expiry. Once started, the
 * underlying promise cannot always be aborted (Angular HttpClient is hidden
 * behind the API service), but a late settlement is safely consumed.
 */
export async function settleBeforeDeadline<T>(
  operationFactory: () => Promise<T>,
  deadline: number,
  cancellation?: Promise<void>,
): Promise<DeadlineOutcome<T>> {
  const remaining = remainingDeadlineMs(deadline);
  if (remaining <= 0) {
    return { kind: "expired" };
  }

  let settled: Promise<DeadlineOutcome<T>>;
  try {
    settled = operationFactory().then<DeadlineOutcome<T>, DeadlineOutcome<T>>(
      (value) => ({ kind: "resolved", value }),
      () => ({ kind: "rejected" }),
    );
  } catch {
    return { kind: "rejected" };
  }

  let timer: ReturnType<typeof globalThis.setTimeout> | null = null;
  const timeout = new Promise<DeadlineOutcome<T>>((resolve) => {
    timer = globalThis.setTimeout(() => resolve({ kind: "expired" }), remaining);
  });
  const cancelled = cancellation?.then<DeadlineOutcome<T>>(() => ({ kind: "cancelled" }));
  try {
    return await Promise.race(cancelled ? [settled, timeout, cancelled] : [settled, timeout]);
  } finally {
    if (timer !== null) {
      globalThis.clearTimeout(timer);
    }
  }
}
