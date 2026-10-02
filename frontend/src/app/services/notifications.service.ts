import { Injectable, signal } from "@angular/core";

import { environment } from "../../environments/environment";
import {
  AlertSocketFrame,
  AppNotification,
  MarkAllReadResponse,
  UnreadCountResponse,
  WsTicketResponse,
} from "../types/notifications";
import { ApiClientService } from "./api-client.service";
import { AuthService } from "./auth.service";

// Exponential backoff for the alert socket: 1s, 2s, 4s ... capped at 30s so a
// backend restart reconnects quickly without hammering a dead server.
const RECONNECT_BASE_DELAY_MS = 1_000;
const RECONNECT_MAX_DELAY_MS = 30_000;
const HEARTBEAT_INTERVAL_MS = 25_000;
const MAX_RECENT_NOTIFICATION_IDS = 500;

type LiveListener = (notification: AppNotification) => void;

@Injectable({ providedIn: "root" })
export class NotificationsService {
  /**
   * Last-known unread total; kept fresh by REST refreshes and live pushes.
   *
   * A signal (not a plain field) on purpose: every write here happens after an
   * await or inside a WebSocket callback, both of which run OUTSIDE the
   * Angular zone (supabase-js getSession acquires a navigator.locks lock that
   * zone.js cannot patch). Signal writes schedule change detection regardless
   * of zone, so the badge updates without a second click.
   */
  readonly unreadCount = signal(0);

  private started = false;
  // Bumped by every write that supersedes an in-flight REST count. A refresh
  // whose generation is stale by the time it resolves is dropped: without
  // this, the refresh fired from socket.onopen raced live alerts and its
  // older count silently erased their increments, leaving the badge low
  // until some later refresh happened to run.
  private countGeneration = 0;
  private socket: WebSocket | null = null;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof window.setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof window.setInterval> | null = null;
  private readonly liveListeners = new Set<LiveListener>();
  private readonly recentNotificationIds = new Set<string>();
  private readonly handleOnline = (): void => this.resumeConnection();
  private readonly handleVisibility = (): void => {
    if (document.visibilityState === "visible") {
      this.resumeConnection();
    }
  };

  constructor(
    private readonly api: ApiClientService,
    private readonly auth: AuthService,
  ) {}

  /** Idempotent: refreshes the unread badge and opens the live socket once. */
  async start(): Promise<void> {
    if (this.started) {
      return;
    }
    this.started = true;
    window.addEventListener("online", this.handleOnline);
    document.addEventListener("visibilitychange", this.handleVisibility);
    await this.refreshUnreadCount();
    void this.openSocket();
  }

  /** Close the live socket and reset state (call on sign-out). */
  stop(): void {
    this.started = false;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = 0;
    window.removeEventListener("online", this.handleOnline);
    document.removeEventListener("visibilitychange", this.handleVisibility);
    this.stopHeartbeat();
    const socket = this.socket;
    this.socket = null;
    // Detach handlers first so onclose does not schedule a reconnect.
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.close();
    }
    this.unreadCount.set(0);
  }

  /** Subscribe to live notifications (toasts); returns an unsubscribe fn. */
  onLive(listener: LiveListener): () => void {
    this.liveListeners.add(listener);
    return () => this.liveListeners.delete(listener);
  }

  async list(unreadOnly = false, limit = 30, offset = 0): Promise<AppNotification[]> {
    const params = new URLSearchParams({
      unread_only: String(unreadOnly),
      limit: String(limit),
      offset: String(offset),
    });
    const notifications = await this.api.get<AppNotification[]>("/api/v1/notifications", params);
    return Array.isArray(notifications) ? notifications : [];
  }

  async refreshUnreadCount(): Promise<void> {
    const generation = this.countGeneration;
    try {
      const response = await this.api.get<UnreadCountResponse>("/api/v1/notifications/unread-count");
      // A live alert (or a mark-read) landing mid-flight already produced a
      // newer count than this response describes; applying it would move the
      // badge backwards.
      if (generation !== this.countGeneration) {
        return;
      }
      this.unreadCount.set(response.count);
    } catch {
      // Keep the last-known badge; REST stays the source of truth on next load.
    }
  }

  async markRead(notificationId: string): Promise<void> {
    await this.api.post<{ updated: boolean }>(`/api/v1/notifications/${notificationId}/read`, {});
    this.countGeneration += 1;
    this.unreadCount.update((count) => Math.max(0, count - 1));
  }

  async markAllRead(): Promise<number> {
    const response = await this.api.post<MarkAllReadResponse>("/api/v1/notifications/read-all", {});
    this.countGeneration += 1;
    this.unreadCount.set(0);
    return response.updated;
  }

  private async openSocket(): Promise<void> {
    if (!this.started || this.socket) {
      return;
    }
    // The Supabase JWT must never appear in the WebSocket URL (URLs land in
    // server/proxy logs and browser history). Mint a one-time 60s ticket over
    // authenticated REST and hand THAT to the handshake instead.
    let ticket: string;
    try {
      const response = await this.api.post<WsTicketResponse>(
        "/api/v1/notifications/ws-ticket",
        {},
      );
      ticket = response.ticket;
    } catch {
      // No session yet or backend down; retry with backoff instead of failing.
      this.scheduleReconnect();
      return;
    }
    // stop() may have run while awaiting the ticket (sign-out mid-reconnect):
    // without this re-check we would open a socket with the PREVIOUS user's
    // ticket and block the next user's start() because this.socket is set.
    if (!this.started) {
      return;
    }
    // Derive ws(s):// from the REST base so one env value drives both.
    const wsBase = environment.apiBaseUrl.replace(/^http/, "ws").replace(/\/+$/, "");
    let socket: WebSocket;
    try {
      socket = new WebSocket(`${wsBase}/ws/alerts?ticket=${encodeURIComponent(ticket)}`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    if (!this.started) {
      socket.close();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.startHeartbeat(socket);
      void this.refreshUnreadCount();
    };
    socket.onmessage = (event: MessageEvent) => this.handleFrame(event.data);
    socket.onclose = () => {
      this.stopHeartbeat();
      this.socket = null;
      this.scheduleReconnect();
    };
    socket.onerror = () => {
      // onclose fires after onerror and owns the reconnect.
      socket.close();
    };
  }

  private handleFrame(raw: unknown): void {
    let frame: AlertSocketFrame;
    try {
      frame = JSON.parse(String(raw)) as AlertSocketFrame;
    } catch {
      return;
    }
    if (frame.type === "connected") {
      return;
    }
    // Frames arrive as {account_id, notification}; tolerate a flat
    // notification object too in case the envelope ever gets unwrapped.
    const candidate = frame.notification
      ?? ((frame as Partial<AppNotification>).title ? (frame as Partial<AppNotification>) : null);
    if (!candidate?.id) {
      return;
    }
    if (this.recentNotificationIds.has(candidate.id)) {
      return;
    }
    this.rememberNotificationId(candidate.id);
    if (!candidate.title) {
      // Oversize NOTIFY collapsed to {id}: the row is only available over REST.
      void this.refreshUnreadCount();
      return;
    }
    this.countGeneration += 1;
    this.unreadCount.update((count) => count + 1);
    const notification: AppNotification = {
      id: candidate.id,
      notification_type: candidate.notification_type || "alert",
      title: candidate.title,
      message: candidate.message || "",
      payload: candidate.payload ?? null,
      is_read: candidate.is_read ?? false,
      created_at: candidate.created_at || new Date().toISOString(),
    };
    for (const listener of this.liveListeners) {
      listener(notification);
    }
  }

  private scheduleReconnect(): void {
    if (!this.started || this.reconnectTimer !== null) {
      return;
    }
    const delay = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** this.reconnectAttempts,
      RECONNECT_MAX_DELAY_MS,
    );
    const jitteredDelay = Math.round(delay * (0.8 + Math.random() * 0.4));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      void this.openSocket();
    }, jitteredDelay);
  }

  private resumeConnection(): void {
    if (!this.started) {
      return;
    }
    void this.refreshUnreadCount();
    if (this.socket?.readyState === WebSocket.OPEN) {
      return;
    }
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    void this.openSocket();
  }

  private startHeartbeat(socket: WebSocket): void {
    this.stopHeartbeat();
    this.heartbeatTimer = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send("ping");
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private rememberNotificationId(notificationId: string): void {
    this.recentNotificationIds.add(notificationId);
    if (this.recentNotificationIds.size <= MAX_RECENT_NOTIFICATION_IDS) {
      return;
    }
    const oldestId = this.recentNotificationIds.values().next().value;
    if (oldestId) {
      this.recentNotificationIds.delete(oldestId);
    }
  }
}
