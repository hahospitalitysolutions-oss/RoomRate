import { Injectable } from "@angular/core";
import { Router } from "@angular/router";

import { environment } from "../../environments/environment";
import { ApiClientError } from "./api-client-error";
import { AuthService } from "./auth.service";

export { ApiClientError } from "./api-client-error";

@Injectable({ providedIn: "root" })
export class ApiClientService {
  constructor(
    private readonly authService: AuthService,
    private readonly router: Router,
  ) {}

  async get<T>(path: string, params?: URLSearchParams, timeoutMs = 45_000): Promise<T> {
    const suffix = params ? `?${params.toString()}` : "";
    return this.request<T>(path + suffix, { method: "GET" }, timeoutMs);
  }

  async post<T>(path: string, body: unknown, timeoutMs = 180_000): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
    }, timeoutMs);
  }

  async put<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PUT",
      body: JSON.stringify(body),
    }, 60_000);
  }

  async delete(path: string): Promise<void> {
    await this.request<unknown>(path, { method: "DELETE" }, 60_000);
  }

  /** multipart/form-data (a file upload): the browser sets the content type with its boundary. */
  async postForm<T>(path: string, form: FormData, timeoutMs = 120_000): Promise<T> {
    return this.request<T>(path, { method: "POST", body: form }, timeoutMs);
  }

  /** A file the API serves (e.g. the Excel template), fetched with the session's token. */
  async getBlob(path: string, timeoutMs = 60_000): Promise<Blob> {
    const token = await this.authService.getAccessToken();
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(new URL(path, environment.apiBaseUrl), {
        signal: controller.signal,
        headers: { "Authorization": `Bearer ${token}` },
      });
      if (!response.ok) {
        throw new ApiClientError("Το αρχείο δεν ήταν διαθέσιμο από το RoomRate API.", "http", response.status);
      }
      return await response.blob();
    } catch (error) {
      if (error instanceof ApiClientError) throw error;
      throw new ApiClientError(`Δεν ήταν δυνατή η επικοινωνία με το RoomRate API στο ${environment.apiBaseUrl}.`, "transport");
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  private async request<T>(path: string, init: RequestInit, timeoutMs: number): Promise<T> {
    const token = await this.authService.getAccessToken();
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    let response: Response;
    try {
      response = await fetch(new URL(path, environment.apiBaseUrl), {
        ...init,
        signal: controller.signal,
        headers: {
          ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
          "Authorization": `Bearer ${token}`,
          ...(init.headers || {}),
        },
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiClientError(
          `Λήξη χρόνου αναμονής του RoomRate API κατά την κλήση ${path}. Ελέγξτε ότι το FastAPI εκτελείται στο ${environment.apiBaseUrl}.`,
          "timeout",
        );
      }
      throw new ApiClientError(`Δεν ήταν δυνατή η επικοινωνία με το RoomRate API στο ${environment.apiBaseUrl}.`, "transport");
    } finally {
      window.clearTimeout(timeoutId);
    }
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) {
        await this.authService.signOut().catch(() => undefined);
        await this.router.navigate(["/auth"], {
          queryParams: { reason: "session-expired" },
        });
        // Byte-identical to the auth page's session-expired banner (A-27 / SV-1).
        throw new ApiClientError("Η συνεδρία σας έληξε. Συνδεθείτε ξανά.", "http", 401);
      }
      // FastAPI validation failures return detail as an ARRAY of error
      // objects; new Error(array) would render "[object Object]" in the UI.
      const detail = payload?.detail;
      const message = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: { msg?: string }) => item?.msg).filter(Boolean).join("; ")
          : "";
      if (message.includes("APIFY_TOKEN")) {
        // This API has no Apify token, so it cannot search Booking.
        throw new ApiClientError(
          "Η αναζήτηση στο Booking δεν είναι ρυθμισμένη στο RoomRate API: προσθέστε το APIFY_TOKEN στο .env του API και ξεκινήστε ξανά το API.",
          "http",
          response.status,
        );
      }
      if (response.status === 500 && message === "Internal Server Error") {
        throw new ApiClientError(
          "Σφάλμα στο RoomRate API (500). Οι λεπτομέρειες είναι στο terminal όπου τρέχει το API.",
          "http",
          500,
        );
      }
      if (response.status === 500 && message.includes("NEON_AUTH_URL")) {
        // The API cannot verify logins (its .env lacks NEON_AUTH_URL, or it
        // was started before it was added): name the fix, in Greek.
        throw new ApiClientError(
          "Το RoomRate API δεν έχει ρυθμιστεί για σύνδεση: προσθέστε το NEON_AUTH_URL στο .env του API και ξεκινήστε ξανά το API.",
          "http",
          500,
        );
      }
      throw new ApiClientError(message || "Το αίτημα προς το RoomRate API απέτυχε.", "http", response.status);
    }
    return payload as T;
  }
}
