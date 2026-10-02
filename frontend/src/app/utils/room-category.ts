/** Whether two stored category keys belong to the same comparison pool. */
export function areComparableRoomCategories(left: string | null | undefined, right: string | null | undefined): boolean {
  const first = (left || "").trim().toLowerCase();
  const second = (right || "").trim().toLowerCase();
  if (!first || !second) {
    return false;
  }
  if (first === second) {
    return true;
  }
  return new Set([first, second]).size === 2
    && [first, second].every((category) => category === "double" || category === "twin");
}
