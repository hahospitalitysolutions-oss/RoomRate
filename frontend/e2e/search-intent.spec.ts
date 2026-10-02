import { expect, test } from "@playwright/test";

import { ApiClientError } from "../src/app/services/api-client-error";
import {
  buildAuthoritativeSearchIntent,
  classifySearchPostFailure,
} from "../src/app/onboarding/search-intent";
import { CurrentUser } from "../src/app/types/market";

const OWNED_PROPERTY_ID = "11111111-1111-1111-1111-111111111111";
const USER: CurrentUser = {
  account_id: "00000000-0000-0000-0000-000000000001",
  onboarding_complete: true,
  owned_property_id: OWNED_PROPERTY_ID,
  property_name: "Authoritative Hotel",
  destination: "Rhodes",
  raw_destination: "Rhodes, Greece",
  selected_room_type_category: "double",
};

test("authoritative intent is frozen and contains no workflow-derived extras", () => {
  const intent = buildAuthoritativeSearchIntent(
    USER,
    OWNED_PROPERTY_ID,
    { checkIn: "2026-09-02", checkOut: "2026-09-06" },
  );
  expect(intent).toEqual({
    owned_property_id: OWNED_PROPERTY_ID,
    room_type_category: "double",
    destination: "Rhodes",
    raw_destination: "Rhodes, Greece",
    check_in: "2026-09-02",
    check_out: "2026-09-06",
    adults: 2,
    children: 0,
    rooms: 1,
    filters_payload: { limit: 8 },
  });
  expect(Object.isFrozen(intent)).toBe(true);
  expect(Object.isFrozen(intent.filters_payload)).toBe(true);
});

for (const [name, error] of [
  ["transport", new ApiClientError("network", "transport")],
  ["timeout", new ApiClientError("timeout", "timeout")],
  ["HTTP 500", new ApiClientError("server", "http", 500)],
  ["HTTP 503", new ApiClientError("server", "http", 503)],
] as const) {
  test(`${name} POST failure is commit-ambiguous`, () => {
    expect(classifySearchPostFailure(error)).toBe("ambiguous");
  });
}

for (const [status, expected] of [[422, "validation"], [429, "quota"], [400, "deterministic"]] as const) {
  test(`HTTP ${status} POST failure is ${expected}`, () => {
    expect(classifySearchPostFailure(new ApiClientError("detail", "http", status))).toBe(expected);
  });
}

test("an untyped failure is never assumed to have committed", () => {
  expect(classifySearchPostFailure(new Error("unknown"))).toBe("deterministic");
});
