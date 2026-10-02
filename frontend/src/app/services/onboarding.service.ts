import { Injectable } from "@angular/core";

import {
  AutomaticSetupResponse,
  CurrentUser,
  OwnedPropertyOnboardingResponse,
  OwnedPropertyRoomType,
  PropertyCandidate,
  ScrapeJobResponse,
  SelectedRoomTypeResponse,
} from "../types/market";
import { ApiClientService } from "./api-client.service";

export type OwnedPropertyOnboardingBody = {
  display_name: string;
  booking_url: string;
  city: string;
  raw_destination?: string | null;
  address?: string | null;
  country?: string | null;
  property_type?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  check_in: string;
  check_out: string;
};

export type CompetitorSearchRequest = {
  owned_property_id: string;
  room_type_category: string;
  destination: string;
  raw_destination?: string | null;
  check_in: string;
  check_out: string;
  adults: number;
  children: number;
  rooms: number;
  filters_payload: Record<string, unknown>;
};

/**
 * Typed surface for the onboarding, /me, and scrape-job endpoints.
 *
 * Wizard steps, the setup guard and every page's /me read reach
 * these endpoints only through this service — never via URL strings — so
 * those flows survive an endpoint change with a one-file edit. map-page and
 * pricing-page still hold their own URL strings for their OTHER endpoints
 * (competitors, market, scrape-jobs). Apart from the /me navigation cache
 * below, methods are thin typed delegations.
 */
@Injectable({ providedIn: "root" })
export class OnboardingService {
  constructor(private readonly api: ApiClientService) {}

  // At most one /me between mutations: the guard and every page share it,
  // across navigations. Never caches a failure, and every mutation below
  // invalidates it, so a completed wizard step is visible to the next read.
  private meCache: Promise<CurrentUser> | null = null;

  currentUser(): Promise<CurrentUser> {
    return (this.meCache ??= this.api.get<CurrentUser>("/api/v1/me").catch((err) => {
      this.meCache = null;
      throw err;
    }));
  }

  invalidateCurrentUser(): void {
    this.meCache = null;
  }

  async autoSetup(body: {
    property_name: string;
    location: string;
    check_in: string;
    check_out: string;
    adults: number;
    children: number;
    rooms: number;
    limit: number;
  }): Promise<AutomaticSetupResponse> {
    try {
      return await this.api.post<AutomaticSetupResponse>("/api/v1/onboarding/auto-setup", body);
    } finally {
      // finally, not after: the server may have committed before a 5xx, and
      // over-invalidating costs one fetch while a stale /me routes wrong.
      this.invalidateCurrentUser();
    }
  }

  /** Ordered best match first — `candidates[0]` is the backend's pick. */
  propertyCandidates(query: {
    property_name: string;
    location: string;
    check_in: string;
    check_out: string;
    limit?: number;
  }): Promise<PropertyCandidate[]> {
    const params = new URLSearchParams({
      property_name: query.property_name,
      location: query.location,
      check_in: query.check_in,
      check_out: query.check_out,
      limit: String(query.limit ?? 8),
    });
    // Live finding 2026-08-11: a cold live scout scrape takes 1-4 minutes,
    // but this GET aborted at the default 45s -- three user-style retries
    // over ~7 minutes all fell back. Matches the 180s budget POSTs get.
    return this.api.get<PropertyCandidate[]>(
      "/api/v1/onboarding/property-candidates",
      params,
      180_000,
    );
  }

  /** 202: creates the first owned property and queues room discovery. */
  async createOwnedProperty(
    body: OwnedPropertyOnboardingBody,
  ): Promise<OwnedPropertyOnboardingResponse> {
    try {
      return await this.api.post<OwnedPropertyOnboardingResponse>(
        "/api/v1/onboarding/owned-property",
        body,
      );
    } finally {
      this.invalidateCurrentUser();
    }
  }

  /** 202: swaps the property, keeps its id, queues a fresh discovery job. */
  async replaceOwnedProperty(
    ownedPropertyId: string,
    body: OwnedPropertyOnboardingBody,
  ): Promise<OwnedPropertyOnboardingResponse> {
    try {
      return await this.api.put<OwnedPropertyOnboardingResponse>(
        `/api/v1/onboarding/owned-property/${ownedPropertyId}`,
        body,
      );
    } finally {
      this.invalidateCurrentUser();
    }
  }

  /** 204; cascades room types/competitors/alerts, keeps market history. */
  async deleteOwnedProperty(ownedPropertyId: string): Promise<void> {
    try {
      await this.api.delete(`/api/v1/onboarding/owned-property/${ownedPropertyId}`);
    } finally {
      this.invalidateCurrentUser();
    }
  }

  roomTypes(ownedPropertyId: string): Promise<OwnedPropertyRoomType[]> {
    return this.api.get<OwnedPropertyRoomType[]>(
      `/api/v1/onboarding/owned-property/${ownedPropertyId}/room-types`,
    );
  }

  async selectRoomType(ownedPropertyId: string, roomTypeCategory: string): Promise<SelectedRoomTypeResponse> {
    try {
      return await this.api.put<SelectedRoomTypeResponse>(
        `/api/v1/onboarding/owned-property/${ownedPropertyId}/selected-room-type`,
        { room_type_category: roomTypeCategory },
      );
    } finally {
      this.invalidateCurrentUser();
    }
  }

  getScrapeJob(jobId: string): Promise<ScrapeJobResponse> {
    // The id can come from a URL (`/pricing?job=`): encoded, a value such as
    // "../../me" stays one path segment instead of resolving to another endpoint.
    return this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${encodeURIComponent(jobId)}`);
  }

  recentScrapeJobs(limit = 50): Promise<ScrapeJobResponse[]> {
    return this.api.get<ScrapeJobResponse[]>(
      "/api/v1/scrape-jobs/",
      new URLSearchParams({ limit: String(limit) }),
    );
  }

  startCompetitorSearch(body: CompetitorSearchRequest): Promise<ScrapeJobResponse> {
    return this.api.post<ScrapeJobResponse>("/api/v1/scrape-jobs/", {
      ...body,
      job_type: "competitor_search",
    });
  }
}
