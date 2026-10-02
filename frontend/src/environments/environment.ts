/**
 * Production values are injected through public/runtime-config.js.
 *
 * These are browser-safe identifiers, not backend secrets. Keeping them in a
 * runtime file lets one immutable Angular bundle move through environments
 * without rebuilding it.
 */
const runtimeConfig = window.__ROOMRATE_CONFIG__ ?? {};

export const environment = {
  apiBaseUrl: runtimeConfig.apiBaseUrl?.replace(/\/+$/, "") ?? "",
  supabaseUrl: runtimeConfig.supabaseUrl?.replace(/\/+$/, "") ?? "",
  supabaseAnonKey: runtimeConfig.supabaseAnonKey ?? "",
  mapboxToken: runtimeConfig.mapboxToken ?? "",
};
