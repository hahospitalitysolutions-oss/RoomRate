import { Routes } from "@angular/router";

import { authGuard } from "./guards/auth.guard";
import { setupGuard } from "./guards/setup.guard";
import { AuthPageComponent } from "./pages/auth-page.component";
import { PrivacyPageComponent } from "./pages/legal/privacy-page.component";
import { TermsPageComponent } from "./pages/legal/terms-page.component";
import { MapPageComponent } from "./pages/map-page.component";
import { PricingPageComponent } from "./pages/pricing-page.component";
import { SettingsPageComponent } from "./pages/settings-page.component";
import { SetupPageComponent } from "./pages/setup-page.component";

export const routes: Routes = [
  { path: "auth", component: AuthPageComponent },
  // Public, like /auth: no guards. A privacy policy or terms page that bounces
  // a signed-out visitor to the sign-in form is useless as a footer link, and
  // GDPR expects the policy to be readable BEFORE an account exists.
  // e2e/legal-pages.spec.ts pins the guard-free behaviour.
  { path: "privacy", component: PrivacyPageComponent },
  { path: "terms", component: TermsPageComponent },
  { path: "setup", component: SetupPageComponent, canActivate: [authGuard, setupGuard] },
  { path: "map", component: MapPageComponent, canActivate: [authGuard, setupGuard] },
  { path: "pricing", component: PricingPageComponent, canActivate: [authGuard, setupGuard] },
  { path: "settings", component: SettingsPageComponent, canActivate: [authGuard] },
  { path: "", pathMatch: "full", redirectTo: "map" },
  { path: "**", redirectTo: "map" },
];
