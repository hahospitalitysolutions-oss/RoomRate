import { CommonModule } from "@angular/common";
import { Component, OnDestroy, OnInit, signal } from "@angular/core";

import { NotificationsService } from "../services/notifications.service";
import { AppNotification } from "../types/notifications";

// How long a live toast stays on screen before auto-dismissing.
const TOAST_LIFETIME_MS = 6_000;
// Keep the stack shallow so a burst of alerts never covers the page.
const MAX_VISIBLE_TOASTS = 4;

type Toast = {
  key: string;
  title: string;
  message: string;
};

@Component({
  selector: "app-notification-toasts",
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="toast-stack" aria-live="polite">
      <div *ngFor="let toast of toasts()" class="toast">
        <strong>{{ toast.title }}</strong>
        <p>{{ toast.message }}</p>
        <button class="toast-close" type="button" aria-label="Κλείσιμο ειδοποίησης" (click)="dismiss(toast.key)">
          &times;
        </button>
      </div>
    </div>
  `,
})
export class NotificationToastsComponent implements OnInit, OnDestroy {
  // Signal, not a plain array: show() fires from a WebSocket message handler
  // that runs outside the Angular zone, and the auto-dismiss timer was
  // scheduled there too. Signal writes trigger change detection anyway.
  readonly toasts = signal<Toast[]>([]);

  private unsubscribe: (() => void) | null = null;
  private readonly timers = new Map<string, ReturnType<typeof window.setTimeout>>();

  constructor(private readonly notifications: NotificationsService) {}

  ngOnInit(): void {
    this.unsubscribe = this.notifications.onLive((notification) => this.show(notification));
  }

  ngOnDestroy(): void {
    this.unsubscribe?.();
    for (const timer of this.timers.values()) {
      window.clearTimeout(timer);
    }
    this.timers.clear();
  }

  dismiss(key: string): void {
    this.toasts.update((toasts) => toasts.filter((toast) => toast.key !== key));
    const timer = this.timers.get(key);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      this.timers.delete(key);
    }
  }

  private show(notification: AppNotification): void {
    // The id is unique per notification; suffix guards against replays.
    const key = `${notification.id}-${Date.now()}`;
    this.toasts.update((toasts) => [
      ...toasts,
      { key, title: notification.title, message: notification.message },
    ]);
    while (this.toasts().length > MAX_VISIBLE_TOASTS) {
      this.dismiss(this.toasts()[0].key);
    }
    this.timers.set(key, window.setTimeout(() => this.dismiss(key), TOAST_LIFETIME_MS));
  }
}
