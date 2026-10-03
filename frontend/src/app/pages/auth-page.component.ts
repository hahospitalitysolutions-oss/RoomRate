import { Component, signal } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";

import { AuthService } from "../services/auth.service";
import { OnboardingService } from "../services/onboarding.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { CurrentUser } from "../types/market";
import { defaultStayDates } from "../utils/date-defaults";

type AuthStep =
  | "idle"
  | "signing-in"
  | "creating-account"
  | "loading-account"
  | "matching-property"
  | "opening-map"
  | "resending-email"
  | "requesting-reset"
  | "updating-password";

@Component({
  selector: "app-auth-page",
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <main class="auth-page">
      <section class="auth-panel">
        <div class="auth-brand">
          <p class="eyebrow">RoomRate</p>
          <h1>{{ isRecovery() ? "Επιλέξτε νέο κωδικό πρόσβασης" : mode() === "sign-in" ? "Συνδεθείτε στον χώρο εργασίας σας" : "Δημιουργήστε τον λογαριασμό σας στο RoomRate" }}</h1>
          <p class="muted">
            {{ isRecovery()
              ? "Ορίστε νέο κωδικό πρόσβασης για τον λογαριασμό σας στο RoomRate."
              : mode() === "sign-in"
              ? "Συνεχίστε στο ταίριασμα δωματίων και στον χάρτη ανταγωνιστών."
              : "Συμπληρώστε το όνομα και την τοποθεσία του καταλύματός σας. Το RoomRate εντοπίζει αυτόματα τον κατάλογο δωματίων σας στο Booking." }}
          </p>
        </div>

        <div *ngIf="loading()" class="progress-panel">
          <strong>{{ loadingLabel() }}</strong>
          <span>{{ message() }}</span>
        </div>
        <div *ngIf="!authConfigured" class="alert alert-error">
          Λείπει η ρύθμιση σύνδεσης (neonAuthUrl). Συμπληρώστε τη στη ρύθμιση περιβάλλοντος του Angular πριν από τη σύνδεση.
        </div>
        <div *ngIf="mode() === 'sign-up' && !isRecovery()" class="alert">
          Μετά τη δημιουργία του λογαριασμού, ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων. Μπορεί να χρειαστεί επιβεβαίωση του email πριν από τη σύνδεση.
        </div>

        <form class="auth-form" (ngSubmit)="submit()">
          <label>
            <span>Όνομα καταλύματος</span>
            <input class="roomrate-input" name="propertyName" [(ngModel)]="propertyName" [required]="mode() === 'sign-up'" [disabled]="isRecovery()" autocomplete="organization" placeholder="π.χ. Rea Hotel">
          </label>
          <label>
            <span>Τοποθεσία</span>
            <input class="roomrate-input" name="location" [(ngModel)]="location" [required]="mode() === 'sign-up'" [disabled]="isRecovery()" placeholder="π.χ. Φαληράκι">
          </label>
          <label>
            <span>Email</span>
            <input class="roomrate-input" type="email" name="email" [(ngModel)]="email" [required]="!isRecovery()" [disabled]="isRecovery()" autocomplete="email">
          </label>
          <div>
            <label for="roomrate-password">
              <span>Κωδικός πρόσβασης</span>
            </label>
            <div class="password-field">
              <input
                id="roomrate-password"
                class="roomrate-input password-input"
                [type]="showPassword ? 'text' : 'password'"
                name="password"
                [(ngModel)]="password"
                required
                [autocomplete]="isRecovery() || mode() === 'sign-up' ? 'new-password' : 'current-password'"
              >
              <button
                class="password-toggle"
                type="button"
                [attr.aria-pressed]="showPassword"
                [attr.aria-label]="showPassword ? 'Απόκρυψη κωδικού πρόσβασης' : 'Εμφάνιση κωδικού πρόσβασης'"
                (mousedown)="$event.preventDefault()"
                (click)="togglePasswordVisibility()"
                (keydown.enter)="togglePasswordVisibilityFromKeyboard($event)"
                (keydown.space)="togglePasswordVisibilityFromKeyboard($event)"
              >
                {{ showPassword ? "Απόκρυψη" : "Εμφάνιση" }}
              </button>
            </div>
          </div>

          <div *ngIf="message() && !loading()" class="alert" [class.alert-error]="hasError()">{{ message() }}</div>

          <button class="primary-button" type="submit" [disabled]="loading() || !authConfigured">
            {{ loading() ? loadingLabel() : isRecovery() ? "Ενημέρωση κωδικού" : mode() === "sign-in" ? "Σύνδεση" : "Δημιουργία λογαριασμού" }}
          </button>
        </form>

        <div *ngIf="!isRecovery()" class="auth-actions">
          <button class="text-button" type="button" (click)="toggleMode()">
            {{ mode() === "sign-in" ? "Δημιουργία νέου λογαριασμού" : "Έχω ήδη λογαριασμό" }}
          </button>
          <button
            *ngIf="mode() === 'sign-in'"
            class="text-button"
            type="button"
            [disabled]="loading() || !email.trim()"
            (click)="resendConfirmation()"
          >
            Επαναποστολή email επιβεβαίωσης
          </button>
          <button
            *ngIf="mode() === 'sign-in'"
            class="text-button"
            type="button"
            [disabled]="loading() || !email.trim()"
            (click)="requestPasswordReset()"
          >
            Ξέχασα τον κωδικό μου
          </button>
        </div>

        <!--
          The same legal footer settings-page.component.ts carries, on the one
          page a prospective customer sees BEFORE handing over an e-mail
          address. /settings sits behind authGuard, so until now the policies
          were reachable only after the account existed — which is late for a
          document you are asked to accept at sign-up. Markup, classes and
          styles are copied verbatim from there so the two footers stay
          visually identical, and a future move of the settings-legal rules
          into styles.css catches both.
        -->
        <footer class="auth-actions settings-legal">
          <a routerLink="/privacy">Πολιτική Απορρήτου</a>
          <a routerLink="/terms">Όροι Χρήσης</a>
        </footer>
      </section>
    </main>
  `,
  styles: [`
    .settings-legal { gap: 1rem; }
    .settings-legal a { color: var(--muted); font-size: 0.85rem; }
  `],
})
export class AuthPageComponent {
  propertyName = "";
  location = "";
  email = "";
  password = "";
  showPassword = false;
  readonly mode = signal<"sign-in" | "sign-up">("sign-in");
  readonly message = signal("");
  readonly hasError = signal(false);
  readonly loading = signal(false);
  readonly authStep = signal<AuthStep>("idle");
  readonly authConfigured: boolean;
  /** The reset-link form; a signal because a successful reset turns it off after an await. */
  readonly isRecovery = signal(false);
  /** The reset link's token (Neon Auth appends ?token=… to the recovery redirect). */
  private readonly recoveryToken: string | null;

  constructor(
    private readonly authService: AuthService,
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
    route: ActivatedRoute,
  ) {
    this.authConfigured = this.authService.isConfigured();
    this.isRecovery.set(route.snapshot.queryParamMap.get("recovery") === "1");
    this.recoveryToken = route.snapshot.queryParamMap.get("token");
    if (this.isRecovery() && route.snapshot.queryParamMap.get("error")) {
      // Neon Auth sends an expired or reused reset link back with ?error=INVALID_TOKEN.
      this.hasError.set(true);
      this.message.set("Ο σύνδεσμος επαναφοράς δεν είναι έγκυρος ή έχει λήξει. Ζητήστε νέο email επαναφοράς.");
    } else if (route.snapshot.queryParamMap.get("updated") === "1") {
      this.message.set("Ο κωδικός πρόσβασης ενημερώθηκε. Συνδεθείτε με τον νέο σας κωδικό.");
    } else if (route.snapshot.queryParamMap.get("reason") === "session-expired") {
      // Byte-identical to ApiClientService's 401 message (inventory A-27 / SV-1):
      // the same expiry must read the same whether the router or the API says it.
      this.message.set("Η συνεδρία σας έληξε. Συνδεθείτε ξανά.");
    }
    this.propertyName = this.workflow.get("draftPropertyName");
    this.location = this.workflow.get("draftLocation");
  }

  toggleMode(): void {
    this.mode.update((mode) => mode === "sign-in" ? "sign-up" : "sign-in");
    this.message.set("");
    this.hasError.set(false);
  }

  loadingLabel(): string {
    const labels: Record<AuthStep, string> = {
      idle: this.isRecovery() ? "Ενημέρωση κωδικού" : this.mode() === "sign-in" ? "Σύνδεση" : "Δημιουργία λογαριασμού",
      "signing-in": "Γίνεται σύνδεση",
      "creating-account": "Δημιουργία λογαριασμού σε εξέλιξη",
      "loading-account": "Έλεγχος χώρου εργασίας",
      "matching-property": "Εντοπισμός του καταλύματός σας",
      "opening-map": "Άνοιγμα χάρτη",
      "resending-email": "Αποστολή email επιβεβαίωσης",
      "requesting-reset": "Αποστολή email επαναφοράς κωδικού",
      "updating-password": "Ενημέρωση κωδικού πρόσβασης",
    };
    return labels[this.authStep()];
  }

  async submit(): Promise<void> {
    if (this.isRecovery()) {
      await this.updatePassword();
      return;
    }
    if (this.mode() === "sign-up" && (!this.propertyName.trim() || !this.location.trim())) {
      this.hasError.set(true);
      this.message.set("Συμπληρώστε όνομα καταλύματος και τοποθεσία πριν δημιουργήσετε τον λογαριασμό.");
      return;
    }

    this.loading.set(true);
    this.setProgress(this.mode() === "sign-in" ? "signing-in" : "creating-account");
    this.hasError.set(false);
    try {
      if (this.mode() === "sign-in") {
        await this.withUiTimeout(
          this.authService.signIn(this.email.trim(), this.password),
          25_000,
          "Η σύνδεση δεν ολοκληρώθηκε. Ελέγξτε τη σύνδεσή σας στο διαδίκτυο και επιβεβαιώστε ότι το email και ο κωδικός είναι σωστά.",
        );
      } else {
        this.workflow.set("draftPropertyName", this.propertyName.trim());
        this.workflow.set("draftLocation", this.location.trim());
        const result = await this.withUiTimeout(
          this.authService.signUp(this.email.trim(), this.password),
          30_000,
          "Η δημιουργία λογαριασμού δεν ολοκληρώθηκε. Ελέγξτε τη σύνδεσή σας στο διαδίκτυο και δοκιμάστε ξανά.",
        );
        if (result.needsEmailConfirmation) {
          this.message.set("Ο λογαριασμός δημιουργήθηκε. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων, επιβεβαιώστε το email σας και μετά συνδεθείτε.");
          this.mode.set("sign-in");
          return;
        }
      }
      this.setProgress("loading-account");
      // Invalidate BEFORE the bootstrap read: after a sign-out -> different
      // sign-in without a reload, a stale cached /me here would bind and
      // store the PREVIOUS account's data. (continueAfterAuth invalidates
      // too, but that runs after this read.)
      this.onboarding.invalidateCurrentUser();
      const currentUser = await this.onboarding.currentUser();
      await this.continueAfterAuth(currentUser);
    } catch (error) {
      this.hasError.set(true);
      this.message.set(error instanceof Error ? error.message : "Η ταυτοποίηση απέτυχε.");
    } finally {
      this.loading.set(false);
      this.authStep.set("idle");
    }
  }

  async resendConfirmation(): Promise<void> {
    const email = this.email.trim();
    if (!email) {
      this.hasError.set(true);
      this.message.set("Συμπληρώστε πρώτα το email σας.");
      return;
    }
    this.loading.set(true);
    this.setProgress("resending-email");
    this.hasError.set(false);
    try {
      await this.withUiTimeout(
        this.authService.resendSignUpConfirmation(email),
        30_000,
        "Το αίτημα για email επιβεβαίωσης δεν ολοκληρώθηκε. Περιμένετε λίγα λεπτά πριν δοκιμάσετε ξανά.",
      );
      this.message.set("Ζητήθηκε email επιβεβαίωσης. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων.");
    } catch (error) {
      this.hasError.set(true);
      this.message.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η επαναποστολή του email επιβεβαίωσης.");
    } finally {
      this.loading.set(false);
    }
  }

  async requestPasswordReset(): Promise<void> {
    const email = this.email.trim();
    if (!email) {
      this.hasError.set(true);
      this.message.set("Συμπληρώστε πρώτα το email σας.");
      return;
    }
    this.loading.set(true);
    this.hasError.set(false);
    this.setProgress("requesting-reset");
    try {
      await this.authService.requestPasswordReset(email);
      this.message.set("Στάλθηκε email επαναφοράς κωδικού. Ανοίξτε τον σύνδεσμο σε αυτό το email για να ορίσετε νέο κωδικό.");
    } catch (error) {
      this.hasError.set(true);
      this.message.set(error instanceof Error ? error.message : "Δεν ήταν δυνατό το αίτημα επαναφοράς κωδικού.");
    } finally {
      this.loading.set(false);
      this.authStep.set("idle");
    }
  }

  async updatePassword(): Promise<void> {
    if (this.password.length < 8) {
      this.hasError.set(true);
      this.message.set("Χρησιμοποιήστε κωδικό με τουλάχιστον 8 χαρακτήρες.");
      return;
    }
    this.loading.set(true);
    this.hasError.set(false);
    this.setProgress("updating-password");
    try {
      await this.authService.updatePassword(this.password, this.recoveryToken);
      await this.authService.signOut();
      // Same route, so Angular keeps this component: switch it back to the
      // sign-in form here, and drop the used token from the address bar.
      this.isRecovery.set(false);
      this.mode.set("sign-in");
      this.message.set("Ο κωδικός πρόσβασης ενημερώθηκε. Συνδεθείτε με τον νέο σας κωδικό.");
      await this.router.navigate(["/auth"], {
        queryParams: { updated: "1" },
        replaceUrl: true,
      });
    } catch (error) {
      this.hasError.set(true);
      this.message.set(error instanceof Error ? error.message : "Δεν ήταν δυνατή η ενημέρωση του κωδικού πρόσβασης.");
    } finally {
      this.loading.set(false);
      this.authStep.set("idle");
    }
  }

  togglePasswordVisibility(): void {
    this.showPassword = !this.showPassword;
  }

  togglePasswordVisibilityFromKeyboard(event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    this.togglePasswordVisibility();
  }

  private async continueAfterAuth(currentUser: CurrentUser): Promise<void> {
    // Deliberately kept alongside submit()'s invalidation: it defends any
    // future caller of this method, at the cost of one extra /me after
    // sign-in. The user-switch e2e pins the overall behavior.
    this.onboarding.invalidateCurrentUser();
    this.workflow.bindToSubject(currentUser.auth_subject);
    if (currentUser.onboarding_complete && currentUser.owned_property_id) {
      this.storeCurrentUser(currentUser);
      this.setProgress("opening-map");
      await this.router.navigateByUrl("/map");
      return;
    }

    const propertyName = (this.propertyName.trim() || this.workflow.get("draftPropertyName")).trim();
    const location = (this.location.trim() || this.workflow.get("draftLocation")).trim();
    this.workflow.set("draftPropertyName", propertyName);
    this.workflow.set("draftLocation", location);

    // Two ways the wizard -- not this page -- owns what comes next:
    //   - a property already exists (half-finished onboarding): the wizard's
    //     own guard logic picks the right step; nothing to create here.
    //   - nothing to auto-setup FROM (blank sign-in form, no stored drafts):
    //     the wizard's step 1 opens in pick mode with its own search form and
    //     asks for name/location there. Demanding them on the sign-in form
    //     instead was a dead end -- the account is already authenticated, so
    //     there is nothing left for /auth to do. (Sign-UP still requires both
    //     up front. The auto-setup below still runs whenever name+location are
    //     available -- typed here or carried as sign-up drafts.)
    if (currentUser.owned_property_id || !propertyName || !location) {
      this.storeCurrentUser(currentUser);
      await this.router.navigateByUrl("/setup");
      return;
    }

    // Speed + control (spec §3.6): auto-setup starts the discovery job NOW so
    // it runs while the user reads the confirmation screen — but its pick is
    // a PROPOSAL. The wizard confirms or corrects it; nothing here polls.
    this.setProgress("matching-property");
    const stay = defaultStayDates();
    try {
      // Through OnboardingService, not a raw api.post: that class is the one
      // place onboarding URLs live (its own contract).
      const setup = await this.onboarding.autoSetup({
        property_name: propertyName,
        location,
        check_in: stay.checkIn,
        check_out: stay.checkOut,
        adults: 2,
        children: 0,
        rooms: 1,
        limit: 8,
      });
      this.workflow.set("ownedPropertyId", setup.owned_property_id);
      this.workflow.set("propertyName", setup.selected_candidate.display_name);
      this.workflow.set("destination", setup.selected_candidate.city || location);
      this.workflow.set("rawDestination", location);
      this.workflow.set("pendingCandidate", JSON.stringify(setup.selected_candidate));
      this.workflow.set("pendingDiscoveryJobId", setup.discovery_job.id);
      this.workflow.set("pendingSetupError", "");
    } catch (error) {
      // Swallows everything -- no candidate, Booking failure, rate limit or
      // quota 429, timeout, 5xx. NOT a dead end (spec §4 step 1): the
      // wizard's step 1 fallback lets the user fix name/location and search
      // again, and it surfaces this message verbatim (spec §8) so a quota
      // refusal is never reframed as "fix your input".
      this.workflow.set("pendingCandidate", "");
      this.workflow.set("pendingDiscoveryJobId", "");
      this.workflow.set(
        "pendingSetupError",
        error instanceof Error ? error.message : "",
      );
    }
    await this.router.navigateByUrl("/setup");
  }

  private setProgress(step: AuthStep): void {
    this.authStep.set(step);
    const messages: Record<AuthStep, string> = {
      idle: "",
      "signing-in": "Σύνδεση στον λογαριασμό σας.",
      "creating-account": "Δημιουργία του λογαριασμού σας. Αν είναι ενεργή η επιβεβαίωση, θα χρειαστεί να επιβεβαιώσετε το email σας πριν συνδεθείτε.",
      "loading-account": "Φόρτωση του λογαριασμού σας RoomRate από το FastAPI.",
      "matching-property": "Αναζήτηση στο Booking για το κατάλυμα και την τοποθεσία σας. Είναι ζωντανή αναζήτηση και μπορεί να διαρκέσει μερικά λεπτά.",
      "opening-map": "Άνοιγμα του χάρτη ανταγωνιστών σας.",
      "resending-email": "Ζητείται νέο email επιβεβαίωσης.",
      "requesting-reset": "Ζητείται ασφαλές email επαναφοράς κωδικού.",
      "updating-password": "Αποθήκευση του νέου σας κωδικού με ασφάλεια.",
    };
    this.message.set(messages[step]);
  }

  private async withUiTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
    let timeoutId: ReturnType<typeof window.setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
    });
    try {
      return await Promise.race([promise, timeout]);
    } finally {
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    }
  }

  private storeCurrentUser(currentUser: CurrentUser): void {
    this.workflow.bindToSubject(currentUser.auth_subject);
    this.workflow.set("ownedPropertyId", currentUser.owned_property_id || "");
    this.workflow.set("propertyName", currentUser.property_name || "");
    this.workflow.set("destination", currentUser.destination || "");
    this.workflow.set("rawDestination", currentUser.raw_destination || currentUser.destination || "");
    this.workflow.set("canonicalDestination", currentUser.canonical_destination || "");
    this.workflow.set("roomTypeCategory", currentUser.selected_room_type_category || "");
    if (!currentUser.owned_property_id) {
      // An account that owns nothing can own no pending onboarding either:
      // all three keys describe a property auto-setup created. A stale
      // `pendingCandidate` is the dangerous one -- setup-page reads it to pick
      // step 1's mode, so it reopens «Είστε εσείς;» about a property this
      // account does not have. The auto-setup path (success AND failure) never
      // routes through here, so a fresh pendingSetupError cannot be wiped by
      // this; mirrors settings-page's change-property reset.
      this.workflow.set("pendingCandidate", "");
      this.workflow.set("pendingDiscoveryJobId", "");
      this.workflow.set("pendingSetupError", "");
    }
  }
}
