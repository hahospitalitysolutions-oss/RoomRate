export type ScheduleConfig = {
  account_id: string;
  enabled: boolean;
  frequency_hours: number;
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
  hour_utc: number;
  lead_days: number;
  nights: number;
  adults: number;
  children: number;
  rooms: number;
}>;
