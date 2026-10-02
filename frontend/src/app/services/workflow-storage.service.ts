import { Injectable } from "@angular/core";

const STORAGE_KEYS = {
  ownedPropertyId: "roomrate_owned_property_id",
  propertyName: "roomrate_property_name",
  destination: "roomrate_property_city",
  rawDestination: "roomrate_property_raw_destination",
  canonicalDestination: "roomrate_property_canonical_destination",
  roomTypeId: "roomrate_selected_room_type_id",
  roomTypeCategory: "roomrate_selected_room_type_category",
  draftPropertyName: "roomrate_draft_property_name",
  draftLocation: "roomrate_draft_location",
  lastCompetitorJobId: "roomrate_last_competitor_job_id",
  lastCompetitorSelection: "roomrate_last_competitor_selection",
  pendingCandidate: "roomrate_pending_candidate",
  pendingDiscoveryJobId: "roomrate_pending_discovery_job_id",
  pendingSetupError: "roomrate_pending_setup_error",
  checklistHidden: "roomrate_checklist_hidden",
};

type StorageKey = keyof typeof STORAGE_KEYS;

const GLOBAL_DRAFT_KEYS = new Set<StorageKey>([
  "draftPropertyName",
  "draftLocation",
]);

@Injectable({ providedIn: "root" })
export class WorkflowStorageService {
  private authSubject = "";

  /**
   * Bind persisted workflow state to the authenticated Supabase subject.
   *
   * Account-specific values are never read before this binding, which prevents
   * one user from inheriting the previous user's selected property or job.
   */
  bindToSubject(authSubject: string | null | undefined): void {
    this.authSubject = authSubject?.trim() || "";
  }

  get(key: StorageKey): string {
    const storageKey = this.resolveKey(key);
    return storageKey ? window.localStorage.getItem(storageKey) || "" : "";
  }

  set(key: StorageKey, value: string | null | undefined): void {
    const storageKey = this.resolveKey(key);
    if (!storageKey) {
      return;
    }
    if (!value) {
      window.localStorage.removeItem(storageKey);
      return;
    }
    window.localStorage.setItem(storageKey, value);
  }

  clear(): void {
    for (const key of Object.keys(STORAGE_KEYS) as StorageKey[]) {
      const storageKey = this.resolveKey(key);
      if (storageKey) {
        window.localStorage.removeItem(storageKey);
      }
    }
    this.authSubject = "";
  }

  private resolveKey(key: StorageKey): string | null {
    const baseKey = STORAGE_KEYS[key];
    if (GLOBAL_DRAFT_KEYS.has(key)) {
      return baseKey;
    }
    return this.authSubject ? `${baseKey}:${this.authSubject}` : null;
  }
}
