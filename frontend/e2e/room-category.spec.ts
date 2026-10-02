import { expect, test } from "@playwright/test";

import { areComparableRoomCategories } from "../src/app/utils/room-category";


test("double and twin jobs can restore across the shared comparison pool", () => {
  expect(areComparableRoomCategories("double", "twin")).toBe(true);
  expect(areComparableRoomCategories("twin", "double")).toBe(true);
});

test("suite remains isolated from the double-twin comparison pool", () => {
  expect(areComparableRoomCategories("suite", "double")).toBe(false);
  expect(areComparableRoomCategories("twin", "suite")).toBe(false);
});
