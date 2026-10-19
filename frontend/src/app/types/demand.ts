/** One daily demand collection run (ferries of the destination, or flights). */
export type DemandRun = {
  started_at: string;
  finished_at: string | null;
  // Observations saved: route-days for ferries, airport windows for flights.
  saved: number | null;
};

/** GET/PUT /api/v1/demand/settings: the owner's ferry and flight collection. */
export type DemandSettings = {
  owned_property_id: string;
  ferries_enabled: boolean;
  flights_enabled: boolean;
  // Greek hour (Europe/Athens) of the daily collection.
  hour_local: number;
  timezone: string;
  // null when the property is not on an island / an island with an airport.
  island: string | null;
  airport_iata: string | null;
  ferries_available: boolean;
  // false also when the installation has no AeroDataBox key.
  flights_available: boolean;
  collect_requested_at: string | null;
  // «Συλλογή τώρα» asked and not run yet.
  collection_pending: boolean;
  ferries_last_run: DemandRun | null;
  flights_last_run: DemandRun | null;
  // Greek time of the next daily collection; null when nothing is on.
  next_collection_at: string | null;
};

export type DemandSettingsUpdate = {
  ferries_enabled: boolean;
  flights_enabled: boolean;
  hour_local: number;
  collect_now?: boolean;
};

/** POST /api/v1/demand/collect. */
export type DemandCollectResponse = {
  queued: boolean;
  message: string;
  settings: DemandSettings;
};
