import { expect, test } from "@playwright/test";

import { pollScrapeJob } from "../src/app/onboarding/scrape-job-poller";
import { ScrapeJobResponse } from "../src/app/types/market";

const NEVER_CANCELLED = new Promise<void>(() => undefined);

function job(status: string): ScrapeJobResponse {
  return {
    id: "55555555-5555-5555-5555-555555555555",
    account_id: "00000000-0000-0000-0000-000000000001",
    owned_property_id: "11111111-1111-1111-1111-111111111111",
    job_type: "competitor_search",
    room_type_category: "suite",
    destination: "Rhodes",
    raw_destination: "Rhodes, Greece",
    check_in: "2026-09-02",
    check_out: "2026-09-06",
    adults: 2,
    children: 0,
    rooms: 1,
    filters_payload: { limit: 8 },
    status,
    requested_at: "2026-08-03T12:00:00Z",
    attempt_count: 0,
    max_attempts: 3,
    scrape_runs_count: 0,
  };
}

async function poll(getJob: () => Promise<ScrapeJobResponse>) {
  return pollScrapeJob({
    getJob,
    timeoutMs: 1_000,
    intervalMs: 0,
    cancellation: NEVER_CANCELLED,
    milestones: ["ένα", "δύο"],
    pollsPerMilestone: 1,
    onMilestone: () => undefined,
  });
}

test("poller follows queued to running to completed", async () => {
  const statuses = ["queued", "running", "completed"];
  let reads = 0;
  const outcome = await poll(() => Promise.resolve(job(statuses[reads++] ?? "completed")));
  expect(outcome).toBe("completed");
  expect(reads).toBe(3);
});

for (const status of ["failed", "cancelled"] as const) {
  test(`poller maps ${status} to terminal`, async () => {
    expect(await poll(() => Promise.resolve(job(status)))).toBe("terminal");
  });
}

test("poller rejects an unexpected durable status", async () => {
  expect(await poll(() => Promise.resolve(job("mystery")))).toBe("unexpected");
});

test("poller maps a rejected job GET without leaking the error", async () => {
  expect(await poll(() => Promise.reject(new Error("Raw provider GET failure")))).toBe("rejected");
});
