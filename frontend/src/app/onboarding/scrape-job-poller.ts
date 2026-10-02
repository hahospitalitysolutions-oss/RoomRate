import { ScrapeJobResponse } from "../types/market";
import { remainingDeadlineMs, settleBeforeDeadline } from "./poll-deadline";

export type ScrapeJobPollOutcome =
  | "completed"
  | "cancelled"
  | "expired"
  | "rejected"
  | "terminal"
  | "unexpected";

type PollOptions = {
  getJob: () => Promise<ScrapeJobResponse>;
  timeoutMs: number;
  intervalMs: number;
  cancellation: Promise<void>;
  milestones: readonly string[];
  pollsPerMilestone: number;
  onMilestone: (milestone: string) => void;
};

/** Poll one durable scrape job within one real monotonic time budget. */
export async function pollScrapeJob(options: PollOptions): Promise<ScrapeJobPollOutcome> {
  const deadline = performance.now() + options.timeoutMs;
  let pollIndex = 0;
  while (true) {
    const outcome = await settleBeforeDeadline(
      options.getJob,
      deadline,
      options.cancellation,
    );
    if (outcome.kind !== "resolved") {
      return outcome.kind === "expired" ? "expired" : outcome.kind;
    }
    if (outcome.value.status === "completed") {
      return "completed";
    }
    if (outcome.value.status === "failed" || outcome.value.status === "cancelled") {
      return "terminal";
    }
    if (outcome.value.status !== "queued" && outcome.value.status !== "running") {
      return "unexpected";
    }
    options.onMilestone(
      options.milestones[Math.min(
        Math.floor(pollIndex / options.pollsPerMilestone),
        options.milestones.length - 1,
      )],
    );
    pollIndex += 1;
    const remaining = remainingDeadlineMs(deadline);
    if (remaining <= 0) {
      return "expired";
    }
    const delayOutcome = await cancellableDelay(
      Math.min(options.intervalMs, remaining),
      options.cancellation,
    );
    if (delayOutcome === "cancelled") {
      return "cancelled";
    }
    if (remainingDeadlineMs(deadline) <= 0) {
      return "expired";
    }
  }
}

function cancellableDelay(
  milliseconds: number,
  cancellation: Promise<void>,
): Promise<"elapsed" | "cancelled"> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (outcome: "elapsed" | "cancelled"): void => {
      if (settled) return;
      settled = true;
      globalThis.clearTimeout(timer);
      resolve(outcome);
    };
    const timer = globalThis.setTimeout(() => finish("elapsed"), milliseconds);
    void cancellation.then(() => finish("cancelled"));
  });
}
