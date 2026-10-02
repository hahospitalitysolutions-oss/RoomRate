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

  async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
    }, 180_000);
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
          "Content-Type": "application/json",
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
      throw new ApiClientError(message || "Το αίτημα προς το RoomRate API απέτυχε.", "http", response.status);
    }
    return payload as T;
  }
}
