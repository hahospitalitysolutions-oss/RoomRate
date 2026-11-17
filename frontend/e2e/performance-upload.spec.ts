/**
 * «Απόδοση» A3 and A9: uploading bookings (preview, errors, room mapping,
 * «Αγνοείται», skip errors, one commit however many clicks), the API's
 * errors as they come, deleting every booking, and the rooms / closures forms.
 */
import { readFileSync } from "node:fs";

import { expect, Page, Route, test } from "@playwright/test";

import { API, CURRENT_USER, RecordedCall, recordCall, seedBrowserState } from "./helpers";

const UNMAPPED = "Double or Twin Room";
const STANDARD = "11111111-1111-4111-8111-000000000002";

function sample(name: string): any {
  return JSON.parse(readFileSync(`public/mock/performance/${name}.json`, "utf-8"));
}

type Handler = (route: Route) => Promise<void> | void;

/** Samples for every call; `handlers` take over an endpoint ("imports", "commit", …) by its last path segment + method. */
async function openUpload(page: Page, handlers: Record<string, Handler> = {}): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];
  await seedBrowserState(page);
  await page.routeWebSocket(/\/ws\/alerts/, (ws) => ws.onMessage(() => undefined));
  await page.route(`${API}/api/v1/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${API}/api/v1/me`, (route) => route.fulfill({ json: CURRENT_USER }));
  await page.route(`${API}/api/v1/performance/**`, async (route) => {
    recordCall(calls, route);
    const request = route.request();
    const path = new URL(request.url()).pathname.split("/performance/")[1];
    const last = path.split("/").pop()!;
    const key = `${request.method()} ${last}`;
    if (handlers[key]) return handlers[key](route);
    if (key === "POST imports" || key === "PUT mapping") return route.fulfill({ json: sample("import-preview") });
    if (key === "POST commit") return route.fulfill({ json: sample("import-commit") });
    if (key === "DELETE bookings") return route.fulfill({ status: 204, body: "" });
    return route.fulfill({ json: sample(path) });
  });
  await page.goto("/performance");
  await page.getByTestId("performance-upload-open").click();
  await expect(page.getByTestId("performance-upload-panel")).toBeVisible();
  return calls;
}

async function chooseFile(page: Page, name = "bookings_export_2026.xlsx", size = 1024): Promise<void> {
  await page.getByTestId("performance-upload-file").setInputFiles({
    name,
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.alloc(size, 1),
  });
}

test("upload: preview, map the last label, skip the errors, commit once", async ({ page }) => {
  const readyPreview = { ...sample("import-preview"), ready: true };
  readyPreview.mapping = readyPreview.mapping.map((row: { label: string }) =>
    (row.label === UNMAPPED ? { ...row, room_type_id: STANDARD } : row));
  let commits = 0;
  const calls = await openUpload(page, {
    "PUT mapping": (route) => route.fulfill({ json: readyPreview }),
    // A slow commit: a second click while it runs must not send a second one.
    "POST commit": async (route) => {
      commits += 1;
      await new Promise((resolve) => setTimeout(resolve, 400));
      await route.fulfill({ json: sample("import-commit") });
    },
  });

  await expect(page.getByTestId("performance-revenue-settings")).toBeVisible();
  await expect(page.getByTestId("performance-settings-vat")).toHaveValue("13");
  await chooseFile(page);
  await page.getByTestId("performance-upload-check").click();

  const preview = page.getByTestId("performance-upload-preview");
  await expect(page.getByTestId("performance-preview-new")).toHaveText("371");
  await expect(page.getByTestId("performance-preview-errors")).toContainText("Γραμμή 17");
  await expect(page.getByTestId("performance-preview-errors")).toContainText("Λείπει η ημερομηνία αναχώρησης.");
  await expect(page.getByTestId(`performance-mapping-${UNMAPPED}`)).toContainText("Χωρίς αντιστοίχιση");
  await expect(page.getByTestId("performance-upload-not-ready")).toBeVisible();
  await expect(page.getByTestId("performance-upload-commit")).toBeDisabled();

  await page.getByTestId(`performance-mapping-select-${UNMAPPED}`).selectOption(STANDARD);
  await expect(page.getByTestId("performance-upload-not-ready")).toHaveCount(0);
  const mappingCall = calls.find((call) => call.method === "PUT" && call.path.endsWith("/mapping"))!;
  expect((mappingCall.body as { mapping: { label: string; room_type_id: string | null }[] }).mapping)
    .toContainEqual({ label: UNMAPPED, room_type_id: STANDARD, ignore: false });

  // Errors present: committing needs «Παράλειψη γραμμών με λάθη».
  await expect(page.getByTestId("performance-upload-commit")).toBeDisabled();
  await page.getByTestId("performance-upload-skip-errors").check();
  await page.getByTestId("performance-upload-commit").dblclick();

  await expect(page.getByTestId("performance-upload-result")).toContainText("Αποθηκεύτηκαν 371 νέες κρατήσεις");
  await expect(page.getByTestId("performance-upload-result")).toContainText("παραλείφθηκαν 3");
  await expect(preview).toHaveCount(0);
  expect(commits).toBe(1);
  expect(calls.find((call) => call.method === "POST" && call.path.endsWith("/commit"))!.body).toEqual({ skip_errors: true });
});

test("upload: an ignored label reads «Αγνοείται» and is sent as ignored", async ({ page }) => {
  const ignored = { ...sample("import-preview"), ready: true };
  ignored.mapping = ignored.mapping.map((row: { label: string }) =>
    (row.label === UNMAPPED ? { ...row, room_type_id: null, ignored: true } : row));
  const calls = await openUpload(page, { "PUT mapping": (route) => route.fulfill({ json: ignored }) });

  await chooseFile(page);
  await page.getByTestId("performance-upload-check").click();
  await page.getByTestId(`performance-mapping-select-${UNMAPPED}`).selectOption("__ignore__");

  await expect(page.getByTestId(`performance-mapping-${UNMAPPED}`).getByTestId("performance-mapping-ignored")).toHaveText("Αγνοείται");
  const body = calls.find((call) => call.method === "PUT")!.body as { mapping: unknown[] };
  expect(body.mapping).toContainEqual({ label: UNMAPPED, room_type_id: null, ignore: true });
});

test("upload: the format the backend recognised is shown when it differs from the choice", async ({ page }) => {
  const calls = await openUpload(page);

  await page.getByTestId("performance-upload-format-template").check();
  await chooseFile(page);
  await page.getByTestId("performance-upload-check").click();

  await expect(page.getByTestId("performance-upload-format-detected")).toContainText("Export του Booking.com");
  expect(calls.some((call) => call.method === "POST" && call.path.endsWith("/imports"))).toBe(true);
});

test("upload: a file over 5 MB is refused before sending, and the API's errors show as they come", async ({ page }) => {
  await openUpload(page, {
    "POST imports": (route) => route.fulfill({ status: 400, json: { detail: "Λείπει η στήλη «Check-in»." } }),
    "POST commit": (route) => route.fulfill({ status: 404, json: { detail: "Η προεπισκόπηση έληξε. Ανεβάστε ξανά το αρχείο." } }),
  });

  await chooseFile(page, "huge.xlsx", 5 * 1024 * 1024 + 1);
  await expect(page.getByTestId("performance-upload-error")).toHaveText("Το αρχείο είναι πάνω από 5 MB.");
  await expect(page.getByTestId("performance-upload-check")).toBeDisabled();

  await chooseFile(page);
  await page.getByTestId("performance-upload-check").click();
  await expect(page.getByTestId("performance-upload-error")).toHaveText("Λείπει η στήλη «Check-in».");
});

test("upload: the history, and deleting every booking only after confirming", async ({ page }) => {
  let deleted = false;
  const calls = await openUpload(page, {
    "GET status": (route) => route.fulfill({ json: sample(deleted ? "status-empty" : "status") }),
    "DELETE bookings": (route) => {
      deleted = true;
      return route.fulfill({ status: 204, body: "" });
    },
  });

  await expect(page.getByTestId("performance-imports")).toContainText("bookings_export_2026.xlsx");
  await page.getByTestId("performance-delete-all").click();
  await expect(page.getByRole("alertdialog")).toContainText("766 κρατήσεις");
  expect(calls.some((call) => call.method === "DELETE")).toBe(false);

  await page.getByTestId("performance-delete-confirm").click();
  await expect(page.getByTestId("performance-empty")).toBeVisible();
  expect(calls.filter((call) => call.method === "DELETE" && call.path.endsWith("/bookings")).length).toBe(1);
});

test("rooms form: room counts and a closed period are saved", async ({ page }) => {
  const calls = await openUpload(page);
  await page.getByTestId("performance-panel-close").click();

  const status = sample("status");
  await page.route(`${API}/api/v1/performance/status**`, (route) =>
    route.fulfill({ json: { ...status, inventory_complete: false, room_types: status.room_types.map((type: object, i: number) => (i === 3 ? { ...type, rooms: null } : type)) } }));
  await page.reload();
  await page.getByTestId("performance-inventory-link").click();

  const inventory = page.getByTestId("performance-inventory-form");
  await expect(inventory).toContainText("Σουίτα");
  await page.getByTestId("performance-inventory-rooms-3").fill("2");
  await page.getByTestId("performance-inventory-save").click();
  await expect(inventory).toContainText("Αποθηκεύτηκαν.");
  const saved = calls.find((call) => call.method === "PUT" && call.path.endsWith("/inventory"))!.body as { room_types: { rooms: number }[] };
  expect(saved.room_types[3].rooms).toBe(2);

  const closures = page.getByTestId("performance-closures-form");
  await expect(closures).toContainText("Κλειστές περίοδοι");
  await page.getByTestId("performance-closure-add").click();
  await page.getByTestId("performance-closures-save").click();
  await expect(closures).toContainText("Κάθε περίοδος θέλει «Από» και «Έως»");

  const added = page.getByTestId("performance-closure-2");
  await added.locator('input[type="date"]').nth(0).fill("2027-01-10");
  await added.locator('input[type="date"]').nth(1).fill("2027-01-20");
  await page.getByTestId("performance-closures-save").click();
  await expect(closures).toContainText("Αποθηκεύτηκαν.");
  const body = calls.find((call) => call.method === "PUT" && call.path.endsWith("/closures"))!.body as { closures: unknown[] };
  expect(body.closures).toHaveLength(3);
  expect(body.closures[2]).toMatchObject({ date_from: "2027-01-10", date_to: "2027-01-20", room_type_id: null });
});
