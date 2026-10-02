import { CompetitorSearchRequest } from "../services/onboarding.service";
import { ScrapeJobResponse } from "../types/market";

/** Return the new job whose durable request is exactly the user's intent. */
export function findNewMatchingSearchJob(
  jobs: ScrapeJobResponse[],
  baselineJobIds: ReadonlySet<string>,
  intended: CompetitorSearchRequest,
): ScrapeJobResponse | null {
  return jobs.find((job) =>
    !baselineJobIds.has(job.id) && searchJobMatches(job, intended)
  ) ?? null;
}

export function searchJobMatches(
  job: ScrapeJobResponse,
  intended: CompetitorSearchRequest,
): boolean {
  return job.job_type === "competitor_search"
    && job.owned_property_id === intended.owned_property_id
    && job.room_type_category === intended.room_type_category
    && job.destination === intended.destination
    && (job.raw_destination ?? null) === (intended.raw_destination ?? null)
    && job.check_in === intended.check_in
    && job.check_out === intended.check_out
    && job.adults === intended.adults
    && job.children === intended.children
    && job.rooms === intended.rooms
    && stableJson(job.filters_payload) === stableJson(intended.filters_payload);
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) =>
      `${JSON.stringify(key)}:${stableJson(record[key])}`
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}
