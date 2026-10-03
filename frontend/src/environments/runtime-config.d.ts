type RoomRateRuntimeConfig = {
  apiBaseUrl?: string;
  /** Neon Auth base URL (Neon -> Connect -> Auth), e.g. https://ep-....neonauth.<region>.aws.neon.tech/neondb/auth */
  neonAuthUrl?: string;
  mapboxToken?: string;
};

interface Window {
  __ROOMRATE_CONFIG__?: RoomRateRuntimeConfig;
}
