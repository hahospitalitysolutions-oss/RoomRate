import { Page, Request, Route } from "@playwright/test";

export const API = "http://127.0.0.1:8000";
// The dev build's Neon Auth base URL (src/environments/environment.development.ts).
export const NEON_AUTH_URL = "https://ep-holy-fog-b2lft4e6.neonauth.c-6.eu-central-1.aws.neon.tech/neondb/auth";

/** An unsigned JWT the auth SDK can read `exp` from; the mocked API never verifies it. */
export function fakeAccessToken(claims: Record<string, unknown> = {}): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const payload = {
    sub: "e2e-user",
    email: "e2e@roomrate.test",
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...claims,
  };
  return `${encode({ alg: "EdDSA", typ: "JWT" })}.${encode(payload)}.e2e-signature`;
}

export const E2E_AUTH_USER = {
  id: "e2e-user",
  email: "e2e@roomrate.test",
  emailVerified: true,
  name: "E2E Owner",
  image: null,
  createdAt: "2026-01-01T00:00:00.000Z",
  updatedAt: "2026-01-01T00:00:00.000Z",
};

/** Better Auth's GET /get-session body for a signed-in user. */
export function neonAuthSessionBody(user = E2E_AUTH_USER) {
  const now = Date.now();
  return {
    session: {
      id: "e2e-session",
      token: "e2e-session-token",
      userId: user.id,
      expiresAt: new Date(now + 7 * 24 * 3600 * 1000).toISOString(),
      createdAt: new Date(now).toISOString(),
      updatedAt: new Date(now).toISOString(),
    },
    user,
  };
}

/**
 * Answer a Neon Auth request the way its server does, CORS included: the app
 * runs on 127.0.0.1:4200 and calls the auth origin with credentials, and the
 * SDK reads the JWT from the exposed `set-auth-jwt` header.
 */
export function fulfillNeonAuth(route: Route, json: unknown, headers: Record<string, string> = {}, status = 200): Promise<void> {
  const request = route.request();
  const cors = {
    "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4200",
    "access-control-allow-credentials": "true",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": request.headers()["access-control-request-headers"] ?? "content-type",
    "access-control-expose-headers": "set-auth-jwt",
  };
  if (request.method() === "OPTIONS") {
    return route.fulfill({ status: 204, headers: cors });
  }
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: { ...cors, ...headers },
    body: JSON.stringify(json),
  });
}

/**
 * Hermetic Neon Auth: nothing reaches the real auth server. `signedIn` decides
 * what GET /get-session answers (a session plus its JWT, or null); a spec can
 * flip it later through the returned state, e.g. after a mocked sign-in.
 */
export async function mockNeonAuth(page: Page, options: { signedIn?: boolean } = {}): Promise<{ signedIn: boolean }> {
  const state = { signedIn: options.signedIn ?? true };
  // LIFO: the catch-all goes first so the specific routes registered after it win.
  await page.route(`${NEON_AUTH_URL}/**`, (route) => route.abort());
  await page.route(`${NEON_AUTH_URL}/get-session**`, (route) =>
    state.signedIn
      ? fulfillNeonAuth(route, neonAuthSessionBody(), { "set-auth-jwt": fakeAccessToken() })
      : fulfillNeonAuth(route, null));
  await page.route(`${NEON_AUTH_URL}/sign-out**`, (route) => {
    state.signedIn = false;
    return fulfillNeonAuth(route, { success: true });
  });
  return state;
}

export const ACCOUNT_ID = "00000000-0000-0000-0000-000000000001";
export const OWNED_PROPERTY_ID = "11111111-1111-1111-1111-111111111111";
export const JOB_ID = "22222222-2222-2222-2222-222222222222";
export const ROOM_TYPE_ID = "33333333-3333-3333-3333-333333333333";

export const CURRENT_USER = {
  account_id: ACCOUNT_ID,
  auth_subject: "e2e-user",
  onboarding_complete: true,
  owned_property_id: OWNED_PROPERTY_ID,
  property_name: "E2E Test Hotel",
  destination: "Faliraki",
  raw_destination: "Faliraki, Rhodes",
  canonical_destination: "faliraki",
  selected_room_type_category: "double",
};

/** A signed-in Neon Auth session + RoomRate workflow storage. */
export async function seedBrowserState(page: Page): Promise<void> {
  await page.addInitScript(
    ({ ownedPropertyId, roomTypeId, jobId }) => {
      window.localStorage.setItem("roomrate_owned_property_id", ownedPropertyId);
      window.localStorage.setItem("roomrate_property_name", "E2E Test Hotel");
      window.localStorage.setItem("roomrate_property_city", "Faliraki");
      window.localStorage.setItem("roomrate_property_raw_destination", "Faliraki, Rhodes");
      window.localStorage.setItem("roomrate_property_canonical_destination", "faliraki");
      window.localStorage.setItem("roomrate_selected_room_type_id", roomTypeId);
      window.localStorage.setItem("roomrate_selected_room_type_category", "double");
      window.localStorage.setItem("roomrate_last_competitor_job_id", jobId);
    },
    {
      ownedPropertyId: OWNED_PROPERTY_ID,
      roomTypeId: ROOM_TYPE_ID,
      jobId: JOB_ID,
    },
  );
  await mockNeonAuth(page);
  await mockNotificationBell(page);
}

/**
 * Keep Mapbox off the network AND out of the assertions.
 *
 * Aborting every Mapbox call is not neutral: a failed style load now raises
 * the map's own mapError notice (Round 4 A6 — it no longer touches the
 * COMPETITOR RESULTS status/message, which historically it blanked via
 * setError). Specs that assert on the map canvas area, or that want a page
 * free of the Greek map-failure notice, should serve the style instead of
 * failing it; results-status assertions are no longer at risk either way.
 *
 * LIFO order matters: the abort goes FIRST so the style route registered after
 * it wins for styles, while tiles and events.mapbox.com telemetry stay aborted.
 */
export async function mockMapbox(page: Page): Promise<void> {
  await page.route(/https:\/\/(api|events)\.mapbox\.com\/.*/, (route) => route.abort());
  await page.route(/https:\/\/api\.mapbox\.com\/styles\/.*/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify({ version: 8, sources: {}, layers: [] }),
    }));
}

/** Only a signed-in Neon Auth session — a brand-new user with no workflow state. */
export async function seedSessionOnly(page: Page): Promise<void> {
  await mockNeonAuth(page);
  await mockNotificationBell(page);
}

/**
 * Hermetic e2e (spec §6): the bell in every page header reads
 * `/notifications/unread-count` on mount. Unmocked, the call reaches whatever
 * listens on :8000 — a live API answers 401 and the app reads that as
 * "session expired" (three setup-wizard tests failed that way on 2026-09-15).
 * The body is `{ count: 0 }`, the shape the bell really reads
 * (`UnreadCountResponse`), not the spec's `{ unread: 0 }`.
 * Registered from the seeding helpers, i.e. BEFORE any route a spec adds, so a
 * spec's own `unread-count` route (notification-bell.spec.ts) or its own API
 * wildcard (no-second-click.spec.ts) still wins: Playwright matches LIFO.
 */
export async function mockNotificationBell(page: Page): Promise<void> {
  await page.route(`${API}/api/v1/notifications/unread-count**`, (route) =>
    route.fulfill({ json: { count: 0 } }));
}

/** Record every mocked call so specs can assert what did (not) happen. */
export type RecordedCall = { method: string; path: string; body: unknown };

export function recordCall(calls: RecordedCall[], route: Route): void {
  const request = route.request();
  calls.push({
    method: request.method(),
    path: new URL(request.url()).pathname,
    body: safePostJson(request),
  });
}

function safePostJson(request: Request): unknown {
  try {
    return request.postDataJSON();
  } catch {
    return null;
  }
}
