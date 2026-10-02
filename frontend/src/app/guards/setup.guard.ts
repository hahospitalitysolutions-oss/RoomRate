import { inject } from "@angular/core";
import { CanActivateFn, Router } from "@angular/router";

import { OnboardingService } from "../services/onboarding.service";

/**
 * Reads GET /me and routes by what is missing (spec §3.1):
 *
 *   no owned property            -> /setup (wizard opens at step 1)
 *   property but no room chosen  -> /setup (wizard opens at step 2), except
 *                                   the explicit one-navigation room-step skip
 *   both present                 -> normal navigation; /setup itself bounces
 *                                   to /map so the wizard is unreachable when
 *                                   there is nothing left to set up.
 *
 * Ordering vs authGuard: Angular evaluates route guards CONCURRENTLY
 * (prioritizedGuardValue); only their results are ranked, authGuard's first.
 * This guard still never fires a request for an unauthenticated user because
 * AuthService.getAccessToken throws before any fetch when there is no
 * session -- that early throw is the invariant this file relies on.
 *
 * State lives in the backend, so a user who leaves mid-wizard is returned to
 * the right step on the next visit (spec §8, last row). On /me failure the
 * guard lets navigation continue: a 401 has already been handled by
 * ApiClientService (sign-out + /auth redirect), so the catch covers only
 * blips and 5xx -- and the target pages own their error states.
 */
export const setupGuard: CanActivateFn = async (route) => {
  const onboarding = inject(OnboardingService);
  const router = inject(Router);
  const target = route.routeConfig?.path ?? "";
  const explicitRoomSkip = target === "map"
    && router.getCurrentNavigation()?.extras.state?.["allowIncompleteSetup"] === true;
  let complete: boolean;
  try {
    const me = await onboarding.currentUser();
    complete = Boolean(me.owned_property_id) && Boolean(me.selected_room_type_category);
  } catch {
    return true;
  }
  if (target === "setup") {
    // «Αλλαγή καταλύματος» από τις ρυθμίσεις: a complete user may re-enter
    // the wizard explicitly; accidental visits still bounce to the map.
    if (route.queryParamMap.get("change") === "1") {
      return true;
    }
    return complete ? router.createUrlTree(["/map"]) : true;
  }
  return complete || explicitRoomSkip ? true : router.createUrlTree(["/setup"]);
};
