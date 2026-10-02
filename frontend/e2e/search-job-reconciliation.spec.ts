import { expect, test } from "@playwright/test";

import { CompetitorSearchRequest } from "../src/app/services/onboarding.service";
import { ScrapeJobResponse } from "../src/app/types/market";
import { searchJobMatches } from "../src/app/onboarding/search-job-reconciliation";

const INTENT: CompetitorSearchRequest = {
  owned_property_id: "11111111-1111-1111-1111-111111111111",
  room_type_category: "suite",
  destination: "Rhodes",
  raw_destination: "Rhodes, Greece",
  check_in: "2026-09-02",
  check_out: "2026-09-06",
  adults: 2,
  children: 1,
  rooms: 1,
  filters_payload: { limit: 8, nested: { exact: true } },
};

const MATCHING_JOB: ScrapeJobResponse = {
  id: "55555555-5555-5555-5555-555555555555",
  account_id: "00000000-0000-0000-0000-000000000001",
  job_type: "competitor_search",
  status: "queued",
  requested_at: "2026-08-03T12:00:00Z",
  attempt_count: 0,
  max_attempts: 3,
  scrape_runs_count: 0,
  ...INTENT,
};

const mismatches: Array<[string, Partial<ScrapeJobResponse>]> = [
  ["job_type", { job_type: "owned_property_room_discovery" }],
  ["owned_property_id", { owned_property_id: "99999999-9999-9999-9999-999999999999" }],
  ["room_type_category", { room_type_category: "double" }],
  ["destination", { destination: "Lindos" }],
  ["raw_destination", { raw_destination: "Rhodes" }],
  ["check_in", { check_in: "2026-09-03" }],
  ["check_out", { check_out: "2026-09-07" }],
  ["adults", { adults: 3 }],
  ["children", { children: 0 }],
  ["rooms", { rooms: 2 }],
  ["filters_payload", { filters_payload: { limit: 8, nested: { exact: false } } }],
];

test("an exact durable job matches the immutable search intent", () => {
  expect(searchJobMatches(MATCHING_JOB, INTENT)).toBe(true);
});

for (const [field, mutation] of mismatches) {
  test(`a near-match differing only in ${field} is rejected`, () => {
    expect(searchJobMatches({ ...MATCHING_JOB, ...mutation }, INTENT)).toBe(false);
  });
}
