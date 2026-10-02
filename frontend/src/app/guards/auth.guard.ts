import { inject } from "@angular/core";
import { CanActivateFn, Router } from "@angular/router";

import { AuthService } from "../services/auth.service";

export const authGuard: CanActivateFn = async () => {
  const authService = inject(AuthService);
  const router = inject(Router);
  try {
    const session = await authService.getSession();
    return session ? true : router.createUrlTree(["/auth"]);
  } catch {
    return router.createUrlTree(["/auth"]);
  }
};
