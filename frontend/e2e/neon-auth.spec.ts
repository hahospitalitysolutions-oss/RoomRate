/**
 * Login through Neon Auth: the session the SDK reads, the Bearer token the API
 * receives, sign-in through the form and the password-reset link. Neon Auth
 * is mocked (helpers.mockNeonAuth); nothing reaches the real auth server.
 */

import { expect, test } from "@playwright/test";

import {
  API,
  CURRENT_USER,
  E2E_AUTH_USER,
  fulfillNeonAuth,
  mockMapbox,
  mockNeonAuth,
  mockNotificationBell,
  NEON_AUTH_URL,
  seedBrowserState,
} from "./helpers";

function decodeJwtPayload(token: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
}

test("the API receives the Neon Auth JWT as the Bearer token", async ({ page }) => {
  const authorizations: string[] = [];
  await seedBrowserState(page);
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: {} }));
  await page.route(`${API}/api/v1/notifications/rules`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => {
    authorizations.push(route.request().headers()["authorization"] ?? "");
    return route.fulfill({ json: CURRENT_USER });
  });

  await page.goto("/settings");

  await expect.poll(() => authorizations.length).toBeGreaterThan(0);
  const [scheme, token] = authorizations[0].split(" ");
  expect(scheme).toBe("Bearer");
  // The JWT from the set-auth-jwt header, not Better Auth's opaque session token.
  expect(decodeJwtPayload(token)).toMatchObject({ sub: E2E_AUTH_USER.id, email: E2E_AUTH_USER.email });
});

test("signed out, a protected page sends the visitor to the login form", async ({ page }) => {
  await mockNeonAuth(page, { signedIn: false });
  await mockNotificationBell(page);

  await page.goto("/map");

  await expect(page).toHaveURL(/\/auth/);
  await expect(page.getByRole("button", { name: /^σύνδεση$/i })).toBeVisible();
});

test("signing in posts the credentials to Neon Auth and opens the workspace", async ({ page }) => {
  const auth = await mockNeonAuth(page, { signedIn: false });
  await mockNotificationBell(page);
  await mockMapbox(page);
  const signInBodies: unknown[] = [];
  await page.route(`${NEON_AUTH_URL}/sign-in/email**`, (route) => {
    if (route.request().method() === "POST") {
      signInBodies.push(route.request().postDataJSON());
      auth.signedIn = true;
    }
    return fulfillNeonAuth(route, { redirect: false, token: "e2e-session-token", user: E2E_AUTH_USER });
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));

  await page.goto("/auth");
  await page.getByLabel(/email/i).fill("e2e@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-1");
  await page.getByRole("button", { name: /^σύνδεση$/i }).click();

  await expect(page).toHaveURL(/\/map/);
  expect(signInBodies).toEqual([{ email: "e2e@roomrate.test", password: "e2e-password-1" }]);
});

test("an API without NEON_AUTH_URL says what to set, in Greek, after a good sign-in", async ({ page }) => {
  const auth = await mockNeonAuth(page, { signedIn: false });
  await mockNotificationBell(page);
  await page.route(`${NEON_AUTH_URL}/sign-in/email**`, (route) => {
    auth.signedIn = true;
    return fulfillNeonAuth(route, { redirect: false, token: "e2e-session-token", user: E2E_AUTH_USER });
  });
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({
    status: 500,
    json: { detail: "Login is not configured on this API: set NEON_AUTH_URL in .env and restart the API." },
  }));

  await page.goto("/auth");
  await page.getByLabel(/email/i).fill("e2e@roomrate.test");
  await page.locator("#roomrate-password").fill("e2e-password-1");
  await page.getByRole("button", { name: /^σύνδεση$/i }).click();

  await expect(page.getByText(/Το RoomRate API δεν έχει ρυθμιστεί για σύνδεση: προσθέστε το NEON_AUTH_URL/)).toBeVisible();
  await expect(page.getByText(/Supabase/)).toHaveCount(0);
});

test("a wrong password reads as a Greek message, not the provider's English one", async ({ page }) => {
  await mockNeonAuth(page, { signedIn: false });
  await mockNotificationBell(page);
  await page.route(`${NEON_AUTH_URL}/sign-in/email**`, (route) =>
    fulfillNeonAuth(route, { code: "INVALID_EMAIL_OR_PASSWORD", message: "Invalid email or password" }, {}, 401));

  await page.goto("/auth");
  await page.getByLabel(/email/i).fill("e2e@roomrate.test");
  await page.locator("#roomrate-password").fill("wrong-password");
  await page.getByRole("button", { name: /^σύνδεση$/i }).click();

  await expect(page.getByText("Λάθος email ή κωδικός πρόσβασης.")).toBeVisible();
  await expect(page).toHaveURL(/\/auth/);
});

test("the reset link's token sets the new password, then the form asks to sign in again", async ({ page }) => {
  await mockNeonAuth(page, { signedIn: false });
  await mockNotificationBell(page);
  const resetBodies: unknown[] = [];
  await page.route(`${NEON_AUTH_URL}/reset-password**`, (route) => {
    if (route.request().method() === "POST") {
      resetBodies.push(route.request().postDataJSON());
    }
    return fulfillNeonAuth(route, { status: true });
  });

  await page.goto("/auth?recovery=1&token=reset-token-123");
  await page.locator("#roomrate-password").fill("new-password-1");
  await page.getByRole("button", { name: /ενημέρωση κωδικού/i }).click();

  await expect(page).toHaveURL(/updated=1/);
  await expect(page.getByText("Ο κωδικός πρόσβασης ενημερώθηκε. Συνδεθείτε με τον νέο σας κωδικό.")).toBeVisible();
  expect(resetBodies).toEqual([{ newPassword: "new-password-1", token: "reset-token-123" }]);
});

test("an expired reset link says so instead of showing a form that cannot work", async ({ page }) => {
  await mockNeonAuth(page, { signedIn: false });
  await mockNotificationBell(page);

  await page.goto("/auth?recovery=1&error=INVALID_TOKEN");

  await expect(page.getByText("Ο σύνδεσμος επαναφοράς δεν είναι έγκυρος ή έχει λήξει. Ζητήστε νέο email επαναφοράς.")).toBeVisible();
});
