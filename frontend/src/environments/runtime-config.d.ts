type RoomRateRuntimeConfig = {
  apiBaseUrl?: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
  mapboxToken?: string;
};

interface Window {
  __ROOMRATE_CONFIG__?: RoomRateRuntimeConfig;
}
