import { defineConfig } from "@playwright/test";

// Browser regression tests for the Angular app. The FastAPI backend and the
// Supabase project are fully mocked inside the specs (page.route +
// page.routeWebSocket), so these tests only need the Angular dev server.
// ROOMRATE_E2E_PORT lets a second checkout (git worktree) run its own dev
// server and suite next to the default one on 4200.
const port = process.env.ROOMRATE_E2E_PORT ?? "4200";
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // The specs mock per-page network state; they are independent and can run
  // in parallel against one shared dev server.
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npx ng serve --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
