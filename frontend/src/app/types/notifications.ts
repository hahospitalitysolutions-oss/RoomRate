export type AppNotification = {
  id: string;
  notification_type: string;
  title: string;
  message: string;
  payload?: Record<string, unknown> | null;
  is_read: boolean;
  created_at: string;
};

export type AlertRule = {
  id: string;
  owned_property_id: string | null;
  rule_type: string;
  threshold_pct: number;
  direction: "any" | "drop" | "rise";
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type AlertRuleCreate = {
  owned_property_id?: string | null;
  rule_type: "price_change";
  threshold_pct: number;
  direction: "any" | "drop" | "rise";
  is_active: boolean;
};

export type UnreadCountResponse = {
  count: number;
};

export type MarkAllReadResponse = {
  updated: number;
};

/** POST /api/v1/notifications/ws-ticket — one-time WebSocket auth ticket. */
export type WsTicketResponse = {
  ticket: string;
  expires_in_seconds: number;
};

/**
 * One frame from the /ws/alerts socket. The server sends either a handshake
 * ack ({type: "connected"}) or an account envelope whose "notification" is a
 * full notification row — or just {id} when the NOTIFY payload was oversize
 * and the client must refetch over REST.
 */
export type AlertSocketFrame = {
  type?: string;
  message?: string;
  account_id?: string;
  notification?: Partial<AppNotification>;
};
