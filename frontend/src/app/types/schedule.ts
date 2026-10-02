export type ScheduleConfig = {
  account_id: string;
  enabled: boolean;
  frequency_hours: number;
  /** The owner's hour on their own clock (`timezone`); absent from an API that predates it. */
  hour_local?: number;
  timezone?: string;
  /** Deprecated: `hour_local` as a UTC hour today. Only for an API without `hour_local`. */
  hour_utc: number;
  lead_days: number;
  nights: number;
  adults: number;
  children: number;
  rooms: number;
  consecutive_failures: number;
  last_run_at?: string | null;
};

/** Partial update body for PUT /api/v1/schedule (explicit nulls are rejected). */
export type ScheduleConfigUpdate = Partial<{
  enabled: boolean;
  frequency_hours: number;
  hour_local: number;
  /** Deprecated: read only by an API that predates `hour_local`; the current API ignores it. */
  hour_utc: number;
  lead_days: number;
  nights: number;
  adults: number;
  children: number;
  rooms: number;
}>;
