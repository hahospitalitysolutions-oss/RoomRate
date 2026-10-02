import { expect, test } from "@playwright/test";

import { settleBeforeDeadline } from "../src/app/onboarding/poll-deadline";

test("an already-expired deadline never invokes its operation factory", async () => {
  let factoryCalls = 0;
  const outcome = await settleBeforeDeadline(
    () => {
      factoryCalls += 1;
      return Promise.reject(new Error("must not start"));
    },
    performance.now() - 1,
  );

  expect(outcome).toEqual({ kind: "expired" });
  expect(factoryCalls).toBe(0);
});

for (const failureMode of ["throw", "reject"] as const) {
  test(`deadline helper maps a factory ${failureMode} to rejected`, async () => {
    const outcome = await settleBeforeDeadline(
      () => {
        if (failureMode === "throw") {
          throw new Error("synchronous failure");
        }
        return Promise.reject(new Error("asynchronous failure"));
      },
      performance.now() + 1_000,
    );

    expect(outcome).toEqual({ kind: "rejected" });
  });
}
