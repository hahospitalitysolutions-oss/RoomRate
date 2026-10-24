import { Injectable, isDevMode } from "@angular/core";

import { ApiClientError, ApiClientService } from "./api-client.service";
import type {
  ForecastTarget,
  ImportCommitRequest,
  ImportCommitResult,
  ImportFormat,
  ImportMappingUpdate,
  ImportPreview,
  ImportSummary,
  PerformanceChannels,
  PerformanceCountries,
  PerformanceFilters,
  PerformanceForecast,
  PerformanceMonthly,
  PerformancePace,
  PerformanceRoomTypes,
  PerformanceStatus,
  PerformanceStayPatterns,
  PerformanceSummary,
  PropertyClosures,
  RevenueSettings,
  RoomInventory,
} from "../types/performance";

const BASE = "/api/v1/performance";
/** localStorage switch for working on the page with the sample answers only: "1" = on. */
export const PERFORMANCE_MOCK_KEY = "roomrate_performance_mock";

/**
 * The «Απόδοση» calls (contract: types/performance.ts, docs/performance/API.md).
 *
 * In development (`ng serve`) a call whose endpoint the API does not have yet
 * (HTTP 404 «Not Found») answers from `public/mock/performance/<name>.json`,
 * so the page and the backend are built in parallel: as each endpoint lands,
 * its real answer replaces the sample. `localStorage.roomrate_performance_mock
 * = "1"` (or `?mock=1` in the URL) uses the samples for every call. A
 * production build never falls back: a missing endpoint is an error there.
 */
@Injectable({ providedIn: "root" })
export class PerformanceApiService {
  constructor(private readonly api: ApiClientService) {}

  status(ownedPropertyId: string): Promise<PerformanceStatus> {
    return this.read("status", "/status", this.params(ownedPropertyId));
  }

  summary(ownedPropertyId: string, filters: PerformanceFilters): Promise<PerformanceSummary> {
    return this.read("summary", "/summary", this.params(ownedPropertyId, filters));
  }

  monthly(ownedPropertyId: string, filters: PerformanceFilters): Promise<PerformanceMonthly> {
    return this.read("monthly", "/monthly", this.params(ownedPropertyId, filters));
  }

  channels(ownedPropertyId: string, filters: PerformanceFilters): Promise<PerformanceChannels> {
    return this.read("channels", "/channels", this.params(ownedPropertyId, filters));
  }

  countries(ownedPropertyId: string, filters: PerformanceFilters): Promise<PerformanceCountries> {
    return this.read("countries", "/countries", this.params(ownedPropertyId, filters));
  }

  roomTypes(ownedPropertyId: string, filters: PerformanceFilters): Promise<PerformanceRoomTypes> {
    return this.read("room-types", "/room-types", this.params(ownedPropertyId, filters));
  }

  stayPatterns(ownedPropertyId: string, filters: PerformanceFilters): Promise<PerformanceStayPatterns> {
    return this.read("stay-patterns", "/stay-patterns", this.params(ownedPropertyId, filters));
  }

  pace(ownedPropertyId: string, season: number): Promise<PerformancePace> {
    const params = this.params(ownedPropertyId);
    params.set("season", String(season));
    return this.read("pace", "/pace", params);
  }

  forecast(ownedPropertyId: string, target: ForecastTarget, horizonMonths = 12): Promise<PerformanceForecast> {
    const params = this.params(ownedPropertyId);
    params.set("target", target);
    params.set("horizon", String(horizonMonths));
    return this.read("forecast", "/forecast", params);
  }

  /** The RoomRate Excel template, to save as RoomRate_kratiseis.xlsx. */
  template(ownedPropertyId: string): Promise<Blob> {
    return this.api.getBlob(`${BASE}/template.xlsx?${this.params(ownedPropertyId)}`);
  }

  /** Step 1: the file is read and checked; nothing is stored yet. */
  uploadImport(ownedPropertyId: string, file: File, format: ImportFormat): Promise<ImportPreview> {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    return this.write("import-preview", () =>
      this.api.postForm<ImportPreview>(`${BASE}/imports?${this.params(ownedPropertyId)}`, form));
  }

  updateMapping(ownedPropertyId: string, importId: string, body: ImportMappingUpdate): Promise<ImportPreview> {
    return this.write("import-preview", () =>
      this.api.put<ImportPreview>(`${BASE}/imports/${importId}/mapping?${this.params(ownedPropertyId)}`, body));
  }

  /** Step 2: the checked bookings are stored (a booking_ref seen before is replaced). */
  commitImport(ownedPropertyId: string, importId: string, body: ImportCommitRequest): Promise<ImportCommitResult> {
    return this.write("import-commit", () =>
      this.api.post<ImportCommitResult>(`${BASE}/imports/${importId}/commit?${this.params(ownedPropertyId)}`, body));
  }

  imports(ownedPropertyId: string): Promise<ImportSummary[]> {
    return this.read("imports", "/imports", this.params(ownedPropertyId));
  }

  /** Every booking of the property, gone; the settings stay. */
  deleteAllBookings(ownedPropertyId: string): Promise<void> {
    return this.api.delete(`${BASE}/bookings?${this.params(ownedPropertyId)}`);
  }

  inventory(ownedPropertyId: string): Promise<RoomInventory> {
    return this.read("inventory", "/inventory", this.params(ownedPropertyId));
  }

  saveInventory(ownedPropertyId: string, body: RoomInventory): Promise<RoomInventory> {
    return this.write("inventory", () => this.api.put<RoomInventory>(`${BASE}/inventory?${this.params(ownedPropertyId)}`, body));
  }

  closures(ownedPropertyId: string): Promise<PropertyClosures> {
    return this.read("closures", "/closures", this.params(ownedPropertyId));
  }

  saveClosures(ownedPropertyId: string, body: PropertyClosures): Promise<PropertyClosures> {
    return this.write("closures", () => this.api.put<PropertyClosures>(`${BASE}/closures?${this.params(ownedPropertyId)}`, body));
  }

  revenueSettings(ownedPropertyId: string): Promise<RevenueSettings> {
    return this.read("revenue-settings", "/revenue-settings", this.params(ownedPropertyId));
  }

  saveRevenueSettings(ownedPropertyId: string, body: RevenueSettings): Promise<RevenueSettings> {
    return this.write("revenue-settings", () =>
      this.api.put<RevenueSettings>(`${BASE}/revenue-settings?${this.params(ownedPropertyId)}`, body));
  }

  /** True when every call answers from the samples (development only). */
  mockMode(): boolean {
    if (!isDevMode()) return false;
    try {
      return window.localStorage.getItem(PERFORMANCE_MOCK_KEY) === "1"
        || new URLSearchParams(window.location.search).get("mock") === "1";
    } catch {
      return false;
    }
  }

  private params(ownedPropertyId: string, filters?: PerformanceFilters): URLSearchParams {
    const params = new URLSearchParams({ owned_property_id: ownedPropertyId });
    if (filters) {
      params.set("date_from", filters.date_from);
      params.set("date_to", filters.date_to);
      params.set("amounts", filters.amounts);
      params.set("compare", filters.compare);
      if (filters.room_type_id) params.set("room_type_id", filters.room_type_id);
    }
    return params;
  }

  private async read<T>(mock: string, path: string, params: URLSearchParams): Promise<T> {
    return this.write(mock, () => this.api.get<T>(`${BASE}${path}`, params));
  }

  private async write<T>(mock: string, call: () => Promise<T>): Promise<T> {
    if (this.mockMode()) return this.sample<T>(mock);
    try {
      return await call();
    } catch (error) {
      // An endpoint the backend has not built yet: FastAPI's own 404.
      if (isDevMode() && error instanceof ApiClientError && error.status === 404 && error.message === "Not Found") {
        console.warn(`[performance] ${mock}: no endpoint yet, using public/mock/performance/${mock}.json`);
        return this.sample<T>(mock);
      }
      throw error;
    }
  }

  private async sample<T>(name: string): Promise<T> {
    const response = await fetch(`/mock/performance/${name}.json`);
    if (!response.ok) throw new Error(`Missing sample public/mock/performance/${name}.json`);
    return (await response.json()) as T;
  }
}
