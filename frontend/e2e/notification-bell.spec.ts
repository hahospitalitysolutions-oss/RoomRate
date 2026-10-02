/**
 * The notification bell's accessible name. Its own file because the bell's
 * other scenarios (badge, one-click panel) live in no-second-click.spec.ts,
 * which pins click behavior rather than labels.
 */
import { expect, Page, test } from "@playwright/test";

import { API, CURRENT_USER, mockMapbox, seedBrowserState } from "./helpers";

/**
 * Mounts the bell (it lives in the map header) with `/unread-count` answering
 * exactly `body` — the only input `bellAriaLabel()` reads.
 */
async function mountBellWithUnreadCount(page: Page, body: Record<string, unknown>): Promise<void> {
  await seedBrowserState(page);
  await mockMapbox(page);
  // LIFO order: the wildcard goes FIRST so every specific mock after it wins.
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/notifications/unread-count`, (route) => route.fulfill({ json: body }));

  await page.goto("/map");
}

test("the bell keeps a real accessible name when the unread count is missing", async ({ page }) => {
  // The odd payload under test: an /unread-count body with no `count`.
  // NotificationsService writes `response.count` straight into the badge
  // signal, so `undefined` reaches the template and the label is the only
  // place left to defend -- it used to read "Notifications, undefined unread"
  // to a screen reader.
  await mountBellWithUnreadCount(page, {});

  const bell = page.locator(".bell-button");
  await expect(bell).toBeVisible();
  await expect(bell).toHaveAttribute("aria-label", "Ειδοποιήσεις");
});

test("the bell announces several unread notifications in the plural", async ({ page }) => {
  // Greek inflects the adjective with the count, so the label is built in TS
  // instead of being concatenated in the template the way English allowed.
  // This pins the plural branch of bellAriaLabel().
  await mountBellWithUnreadCount(page, { count: 3 });

  await expect(page.locator(".bell-button")).toHaveAttribute(
    "aria-label",
    "Ειδοποιήσεις, 3 μη αναγνωσμένες",
  );
});

test("the bell announces a single unread notification in the singular", async ({ page }) => {
  // The branch a mechanical `${n} μη αναγνωσμένες` gets wrong: one unread is
  // «μη αναγνωσμένη».
  await mountBellWithUnreadCount(page, { count: 1 });

  await expect(page.locator(".bell-button")).toHaveAttribute(
    "aria-label",
    "Ειδοποιήσεις, 1 μη αναγνωσμένη",
  );
});
