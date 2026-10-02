import { CommonModule } from "@angular/common";
import { Component, ElementRef, HostListener, OnInit, signal } from "@angular/core";

import { IconComponent } from "./icon.component";
import { NotificationsService } from "../services/notifications.service";
import { AppNotification } from "../types/notifications";

type LoadingState = "idle" | "loading" | "ready" | "error";

// All async-written state lives in signals: the fetch continuations run
// outside the Angular zone (supabase-js navigator.locks escapes zone.js), and
// signal writes schedule change detection regardless of zone.
@Component({
  selector: "app-notification-bell",
  standalone: true,
  imports: [CommonModule, IconComponent],
  template: `
    <div class="notification-bell">
      <button
        type="button"
        class="bell-button"
        [attr.aria-label]="bellAriaLabel()"
        (click)="togglePanel()"
      >
        <app-icon name="bell" [size]="18"></app-icon>
        <span *ngIf="notifications.unreadCount()" class="bell-badge">{{ badgeLabel() }}</span>
      </button>

      <div *ngIf="panelOpen()" class="notification-panel">
        <div class="notification-panel-header">
          <strong>Ειδοποιήσεις</strong>
          <button
            class="text-button"
            type="button"
            [disabled]="!notifications.unreadCount() || busy()"
            (click)="markAllRead()"
          >
            Σήμανση όλων ως αναγνωσμένων
          </button>
        </div>
        <div *ngIf="loadState() === 'loading'" class="notification-empty">Φόρτωση ειδοποιήσεων...</div>
        <div *ngIf="loadState() === 'error'" class="notification-empty">{{ error() }}</div>
        <div *ngIf="loadState() === 'ready' && !items().length" class="notification-empty">
          Δεν υπάρχουν ακόμη ειδοποιήσεις. Εδώ θα εμφανίζονται οι ειδοποιήσεις τιμών από τους
          ανταγωνιστές που παρακολουθείτε.
        </div>
        <div class="notification-list">
          <article *ngFor="let item of items()" class="notification-item" [class.unread]="!item.is_read">
            <div class="notification-item-top">
              <strong>{{ item.title }}</strong>
              <small>{{ formatTime(item.created_at) }}</small>
            </div>
            <p>{{ item.message }}</p>
            <button *ngIf="!item.is_read" class="text-button" type="button" (click)="markRead(item)">
              Σήμανση ως αναγνωσμένης
            </button>
          </article>
        </div>
        <button
          *ngIf="hasMore()"
          class="text-button"
          type="button"
          [disabled]="busy()"
          (click)="loadMore()"
        >
          Φόρτωση περισσότερων
        </button>
      </div>
    </div>
  `,
})
export class NotificationBellComponent implements OnInit {
  readonly panelOpen = signal(false);
  readonly items = signal<AppNotification[]>([]);
  readonly loadState = signal<LoadingState>("idle");
  readonly error = signal("");
  readonly busy = signal(false);
  readonly hasMore = signal(false);
  private readonly pageSize = 30;

  constructor(
    readonly notifications: NotificationsService,
    private readonly host: ElementRef<HTMLElement>,
  ) {}

  ngOnInit(): void {
    // Starts the shared live socket once; safe to call from every page header.
    void this.notifications.start();
  }

  @HostListener("document:click", ["$event"])
  onDocumentClick(event: MouseEvent): void {
    if (this.panelOpen() && !this.host.nativeElement.contains(event.target as Node)) {
      this.panelOpen.set(false);
    }
  }

  /**
   * The bell's accessible name — the only thing a screen-reader user gets.
   *
   * `unreadCount` is typed as a number but is fed by
   * `/notifications/unread-count`: a response body without `count` writes
   * `undefined` into the signal, and the old inline concatenation read that
   * out as "Notifications, undefined unread". An unknown count is simply not
   * announced; the bell still names itself.
   *
   * Greek inflects the adjective with the count, so the sentence is built
   * here in TS rather than concatenated in the template: one unread is
   * «μη αναγνωσμένη», any other count «μη αναγνωσμένες».
   */
  bellAriaLabel(): string {
    const unread = this.notifications.unreadCount();
    if (!Number.isFinite(unread)) {
      return "Ειδοποιήσεις";
    }
    return unread === 1
      ? "Ειδοποιήσεις, 1 μη αναγνωσμένη"
      : `Ειδοποιήσεις, ${unread} μη αναγνωσμένες`;
  }

  badgeLabel(): string {
    return this.notifications.unreadCount() > 99 ? "99+" : String(this.notifications.unreadCount());
  }

  togglePanel(): void {
    this.panelOpen.update((open) => !open);
    if (this.panelOpen()) {
      void this.loadItems();
    }
  }

  async markRead(item: AppNotification): Promise<void> {
    if (item.is_read || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      await this.notifications.markRead(item.id);
      this.items.update((list) =>
        list.map((entry) => (entry.id === item.id ? { ...entry, is_read: true } : entry)),
      );
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η σήμανση της ειδοποίησης ως αναγνωσμένης.");
      this.loadState.set("error");
    } finally {
      this.busy.set(false);
    }
  }

  async markAllRead(): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      await this.notifications.markAllRead();
      this.items.update((list) => list.map((entry) => ({ ...entry, is_read: true })));
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η σήμανση όλων των ειδοποιήσεων ως αναγνωσμένων.");
      this.loadState.set("error");
    } finally {
      this.busy.set(false);
    }
  }

  async loadMore(): Promise<void> {
    if (this.busy() || !this.hasMore()) {
      return;
    }
    this.busy.set(true);
    try {
      const next = await this.notifications.list(false, this.pageSize, this.items().length);
      this.items.update((items) => [...items, ...next]);
      this.hasMore.set(next.length === this.pageSize);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση περισσότερων ειδοποιήσεων.");
    } finally {
      this.busy.set(false);
    }
  }

  formatTime(createdAt: string): string {
    const parsed = new Date(createdAt);
    if (Number.isNaN(parsed.getTime())) {
      return "";
    }
    // el-GR, not en-GB: one date format across a Greek UI.
    return parsed.toLocaleString("el-GR", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  private async loadItems(): Promise<void> {
    this.loadState.set("loading");
    this.error.set("");
    try {
      const items = await this.notifications.list(false, this.pageSize, 0);
      this.items.set(items);
      this.hasMore.set(items.length === this.pageSize);
      // Opening the panel is a natural moment to re-sync the badge.
      void this.notifications.refreshUnreadCount();
      this.loadState.set("ready");
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση των ειδοποιήσεων.");
      this.loadState.set("error");
    }
  }
}
