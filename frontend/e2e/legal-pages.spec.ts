/**
 * The two public legal pages: /privacy and /terms.
 *
 * The point of this spec is the ABSENCE of a session. Every other route in
 * app.routes.ts is behind authGuard (and most behind setupGuard too), so a
 * signed-out visit lands on /auth. A footer link to the privacy policy that
 * bounces a prospective customer into a sign-in form is worse than no link,
 * and GDPR expects the policy to be reachable BEFORE anyone hands over an
 * email address -- so these two routes must stay guard-free forever.
 *
 * Consequently: no seedBrowserState() / seedSessionOnly() anywhere below.
 */
import { expect, test } from "@playwright/test";

import { API, mockMapbox } from "./helpers";

const DRAFT_NOTICE = "Προσχέδιο — προς επιβεβαίωση από νομικό σύμβουλο πριν την εμπορική διάθεση";

/**
 * Neutralize the backend and Mapbox the same way every other spec does.
 *
 * Neither page calls the API, but the app shell boots on every route, and a
 * future shell addition (a header, a bell) must not turn this spec red for an
 * unrelated reason. LIFO order: the wildcard is registered FIRST so any
 * specific mock added later would win.
 */
async function mockEverything(page: import("@playwright/test").Page): Promise<void> {
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await mockMapbox(page);
}

test("the privacy policy renders for a signed-out visitor", async ({ page }) => {
  await mockEverything(page);

  await page.goto("/privacy");

  await expect(page.getByRole("heading", { name: "Πολιτική Απορρήτου", level: 1 })).toBeVisible();
  // The guard check: authGuard would have rewritten the URL to /auth.
  await expect(page).toHaveURL(/\/privacy$/);
  await expect(page.locator("[data-testid='legal-draft-notice']")).toContainText(DRAFT_NOTICE);
});

test("the terms of service render for a signed-out visitor", async ({ page }) => {
  await mockEverything(page);

  await page.goto("/terms");

  await expect(page.getByRole("heading", { name: "Όροι Χρήσης", level: 1 })).toBeVisible();
  await expect(page).toHaveURL(/\/terms$/);
  await expect(page.locator("[data-testid='legal-draft-notice']")).toContainText(DRAFT_NOTICE);
});

test("the privacy policy names the third parties and the GDPR rights", async ({ page }) => {
  await mockEverything(page);

  await page.goto("/privacy");

  // The sections a reviewer (and a DPA) looks for first. Asserting the
  // headings rather than the prose keeps the copy editable.
  await expect(page.getByRole("heading", { name: "Δεδομένα που συλλέγουμε" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Τρίτοι πάροχοι" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Τα δικαιώματά σας/ })).toBeVisible();

  // Every processor the deployment can actually talk to has to be named here.
  // Sentry is the one this branch adds (api/main.py calls init_sentry() when
  // SENTRY_DSN is set), and an exception report can carry an account id -- a
  // processor the policy omits is exactly the gap a DPA finds first.
  const processors = page.locator("[data-testid='legal-processors']");
  for (const processor of ["Neon", "Apify", "Mapbox", "Anthropic", "Sentry"]) {
    await expect(processors).toContainText(processor);
  }

  // The cookie claim is a factual one about this app: no tracking cookies,
  // only Neon Auth's session cookie and the setup progress in localStorage.
  // If that ever stops being true the copy must change, and this assertion is
  // the reminder.
  const cookieSection = page.locator("[data-testid='legal-cookies']");
  await expect(cookieSection).toContainText("cookie σύνδεσης");
  await expect(cookieSection).toContainText("Neon");
  await expect(cookieSection).toContainText("localStorage");
});

test("the terms keep the pricing model explicitly undecided", async ({ page }) => {
  await mockEverything(page);

  await page.goto("/terms");

  await expect(page.getByRole("heading", { name: "Χρέωση" })).toBeVisible();
  // Round 5 ships no billing. Saying so beats inventing a price.
  await expect(page.locator("[data-testid='legal-billing']")).toContainText("θα οριστεί");
});

test("both policies describe the deletion the product actually offers", async ({ page }) => {
  await mockEverything(page);

  // The settings page deletes the PROPERTY (DELETE
  // /api/v1/onboarding/owned-property/{id}). There is no account-deletion
  // endpoint at all, so "delete your account from the settings" was a promise
  // the app cannot keep -- and a false statement in a document a customer is
  // asked to accept. Both pages now route account deletion through the contact
  // address; these assertions are what a future copy edit has to argue with.
  await page.goto("/terms");
  const termsDeletion = page.locator("[data-testid='legal-account-deletion']");
  await expect(termsDeletion).toContainText("κατάλυμά σας");
  await expect(termsDeletion).toContainText("ρυθμίσεις της εφαρμογής");
  await expect(termsDeletion).toContainText("διαγραφή του λογαριασμού σας στη διεύθυνση επικοινωνίας");

  await page.goto("/privacy");
  const privacyRetention = page.locator("[data-testid='legal-retention']");
  await expect(privacyRetention).toContainText("διαγραφή του λογαριασμού σας στη διεύθυνση επικοινωνίας");
  // The GDPR right to erasure is a separate promise and must survive the edit.
  await expect(page.getByRole("listitem").filter({ hasText: "Διαγραφή" }).first()).toContainText(
    "να ζητήσετε τη διαγραφή των δεδομένων σας",
  );
});

test("the sign-in page reaches both policies without a session", async ({ page }) => {
  await mockEverything(page);

  await page.goto("/auth");

  // GDPR expects the policy to be readable BEFORE an e-mail address changes
  // hands. Until now the only footer carrying these links lived on /settings,
  // behind authGuard -- reachable exactly when it is too late to matter.
  // No navigation here: the hrefs are the contract.
  await expect(page.getByRole("link", { name: "Πολιτική Απορρήτου" })).toHaveAttribute(
    "href",
    "/privacy",
  );
  await expect(page.getByRole("link", { name: "Όροι Χρήσης" })).toHaveAttribute("href", "/terms");
});

test("each legal page links to the other one", async ({ page }) => {
  await mockEverything(page);

  await page.goto("/privacy");
  await page.getByRole("link", { name: "Όροι Χρήσης" }).click();
  await expect(page.getByRole("heading", { name: "Όροι Χρήσης", level: 1 })).toBeVisible();

  await page.getByRole("link", { name: "Πολιτική Απορρήτου" }).click();
  await expect(page.getByRole("heading", { name: "Πολιτική Απορρήτου", level: 1 })).toBeVisible();
});
