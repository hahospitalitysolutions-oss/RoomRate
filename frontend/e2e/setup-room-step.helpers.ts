import { expect, Page } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  JOB_ID,
  OWNED_PROPERTY_ID,
  RecordedCall,
  recordCall,
  seedSessionOnly,
} from "./helpers";

export const LOAD_ERROR = "Δεν μπορέσαμε να φορτώσουμε τα δωμάτια. Δοκιμάστε ξανά.";
export const SAVE_ERROR = "Δεν μπορέσαμε να αποθηκεύσουμε το δωμάτιο. Δοκιμάστε ξανά.";
export const TIMEOUT_ERROR = "Η ανακάλυψη δωματίων διαρκεί ασυνήθιστα πολύ. Δοκιμάστε ξανά σε λίγο.";

export const ROOM_CATALOG = [
  {
    id: "33333333-3333-3333-3333-333333333333",
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Double Room with Sea View",
    room_type_category: "double",
    sample_price_per_night_eur: 0,
    sample_facilities: "Balcony, Sea view",
    is_active: true,
  },
  {
    id: "33333333-3333-3333-3333-333333333334",
    owned_property_id: OWNED_PROPERTY_ID,
    room_type: "Family Suite",
    room_type_category: "suite",
    sample_price_per_night_eur: 180,
    sample_facilities: "Two bedrooms",
    is_active: true,
  },
];

const PROPOSAL = {
  candidate_key: "room-step-hotel",
  display_name: "Room Step Hotel",
  booking_url: "https://www.booking.com/hotel/gr/room-step.html",
  city: "Faliraki",
  address: "Leoforos Kallitheas 20",
  country: "Greece",
  property_type: "hotel",
  latitude: 36.34,
  longitude: 28.2,
  stars: 3,
  review_score: 8.8,
  review_count: 121,
};

export async function prepareRoomStep(
  page: Page,
  calls: RecordedCall[],
  options: { discoveryJobId?: string | null; withProposal?: boolean } = {},
): Promise<{ withProposal: boolean }> {
  const discoveryJobId = options.discoveryJobId === undefined ? JOB_ID : options.discoveryJobId;
  const withProposal = options.withProposal ?? true;
  await seedSessionOnly(page);
  await page.addInitScript(
    ({ candidate, jobId, ownedPropertyId, proposal }) => {
      window.localStorage.setItem("roomrate_owned_property_id:e2e-user", ownedPropertyId);
      if (proposal) {
        window.localStorage.setItem("roomrate_pending_candidate:e2e-user", JSON.stringify(candidate));
      }
      if (jobId) {
        window.localStorage.setItem("roomrate_pending_discovery_job_id:e2e-user", jobId);
      }
    },
    { candidate: PROPOSAL, jobId: discoveryJobId, ownedPropertyId: OWNED_PROPERTY_ID, proposal: withProposal },
  );

  // LIFO: all endpoint-specific recording fakes are registered after this.
  await page.route(`${API}/api/v1/**`, (route) => {
    recordCall(calls, route);
    return route.fulfill({ json: [] });
  });
  await page.route(`${API}/api/v1/me`, (route) => {
    recordCall(calls, route);
    return route.fulfill({
      json: {
        ...CURRENT_USER,
        onboarding_complete: false,
        property_name: PROPOSAL.display_name,
        selected_room_type_category: null,
      },
    });
  });
  return { withProposal };
}

export async function openRoomStep(page: Page, withProposal: boolean): Promise<void> {
  await page.goto("/setup");
  if (withProposal) {
    await page.getByRole("button", { name: "Ναι, αυτό είναι" }).click();
  }
  await expect(page.getByTestId("setup-step-title")).toContainText("Βήμα 2");
}

export function pathCalls(calls: RecordedCall[], path: string, method = "GET"): RecordedCall[] {
  return calls.filter((call) => call.path === path && call.method === method);
}
