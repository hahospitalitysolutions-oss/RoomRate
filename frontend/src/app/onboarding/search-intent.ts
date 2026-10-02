import { CurrentUser } from "../types/market";
import { ApiClientError } from "../services/api-client-error";
import { CompetitorSearchRequest } from "../services/onboarding.service";
import { StayDates } from "../utils/date-defaults";

export type SearchPostFailure = "ambiguous" | "validation" | "quota" | "deterministic";

/** Build the only paid-search request from authoritative account state. */
export function buildAuthoritativeSearchIntent(
  me: CurrentUser,
  ownedPropertyId: string,
  stay: StayDates,
): CompetitorSearchRequest {
  const category = me.selected_room_type_category?.trim();
  const destination = me.destination?.trim();
  const rawDestination = me.raw_destination?.trim();
  if (
    !ownedPropertyId
    || me.owned_property_id !== ownedPropertyId
    || !category
    || !destination
    || !rawDestination
  ) {
    throw new SearchIntentPreparationError();
  }
  return Object.freeze({
    owned_property_id: ownedPropertyId,
    room_type_category: category,
    destination,
    raw_destination: rawDestination,
    check_in: stay.checkIn,
    check_out: stay.checkOut,
    adults: 2,
    children: 0,
    rooms: 1,
    filters_payload: Object.freeze({ limit: 8 }),
  });
}

/** Only failures that may hide a committed POST enter reconciliation. */
export function classifySearchPostFailure(error: unknown): SearchPostFailure {
  if (!(error instanceof ApiClientError)) return "deterministic";
  if (error.kind === "transport" || error.kind === "timeout") return "ambiguous";
  if (error.status === 422) return "validation";
  if (error.status === 429) return "quota";
  return error.kind === "http" && error.status !== null && error.status >= 500
    ? "ambiguous"
    : "deterministic";
}

export class SearchIntentPreparationError extends Error {}
