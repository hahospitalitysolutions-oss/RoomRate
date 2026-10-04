import { Injectable } from "@angular/core";
import { createAuthClient } from "@neondatabase/auth";
import { SupabaseAuthAdapter } from "@neondatabase/auth/vanilla/adapters";

import { environment } from "../../environments/environment";

/**
 * Login through Neon Auth (Better Auth managed by Neon).
 *
 * The Supabase-compatible adapter keeps the method names this app was built
 * on (signInWithPassword, getSession, onAuthStateChange...). The session's
 * access_token is the Neon Auth JWT (15 minutes, renewed by the SDK) that the
 * API verifies against the project JWKS.
 */
/** Neon Auth's password rule (Better Auth defaults): length only, 8 to 128. */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_REQUIREMENTS = `Ο κωδικός πρέπει να έχει από ${PASSWORD_MIN_LENGTH} έως ${PASSWORD_MAX_LENGTH} χαρακτήρες.`;

/** The Greek reason a password would be rejected, or null when it passes. */
export function passwordRequirementError(password: string): string | null {
  if (password.length < PASSWORD_MIN_LENGTH) {
    return `Ο κωδικός είναι πολύ μικρός (${password.length} χαρακτήρες). ${PASSWORD_REQUIREMENTS}`;
  }
  if (password.length > PASSWORD_MAX_LENGTH) {
    return `Ο κωδικός είναι πολύ μεγάλος (${password.length} χαρακτήρες). ${PASSWORD_REQUIREMENTS}`;
  }
  return null;
}

function createNeonAuthClient(url: string) {
  return createAuthClient(url, { adapter: SupabaseAuthAdapter() });
}

type NeonAuthClient = ReturnType<typeof createNeonAuthClient>;
export type AuthSession = NonNullable<Awaited<ReturnType<NeonAuthClient["getSession"]>>["data"]["session"]>;
type AuthStateCallback = Parameters<NeonAuthClient["onAuthStateChange"]>[0];
export type AuthChangeEvent = Parameters<AuthStateCallback>[0];
export type AuthSubscription = ReturnType<NeonAuthClient["onAuthStateChange"]>["data"]["subscription"];

export type SignUpResult = {
  needsEmailConfirmation: boolean;
};

const NOT_CONFIGURED = "Λείπει η ρύθμιση σύνδεσης (neonAuthUrl) του RoomRate.";

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly client: NeonAuthClient | null;

  constructor() {
    this.client = environment.neonAuthUrl ? createNeonAuthClient(environment.neonAuthUrl) : null;
  }

  isConfigured(): boolean {
    return Boolean(this.client);
  }

  async getSession(): Promise<AuthSession | null> {
    if (!this.client) {
      return null;
    }
    const { data } = await this.client.getSession();
    return data.session ?? null;
  }

  async getAccessToken(): Promise<string> {
    const session = await this.getSession();
    if (!session?.access_token) {
      throw new Error("Πρέπει πρώτα να συνδεθείτε.");
    }
    return session.access_token;
  }

  async signIn(email: string, password: string): Promise<void> {
    const client = this.requireClient();
    const { data, error } = await this.withTimeout(
      client.signInWithPassword({ email, password }),
      "Η σύνδεση έληξε μετά από 20 δευτερόλεπτα. Ελέγξτε το δίκτυό σας και δοκιμάστε ξανά.",
      20_000,
    );
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
    if (!data.session) {
      throw new Error("Η σύνδεση δεν επέστρεψε συνεδρία. Δοκιμάστε ξανά.");
    }
  }

  async signUp(email: string, password: string): Promise<SignUpResult> {
    const client = this.requireClient();
    const { data, error } = await this.withTimeout(
      client.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth`,
        },
      }),
      "Η δημιουργία λογαριασμού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.",
    );
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
    return { needsEmailConfirmation: !data.session };
  }

  async resendSignUpConfirmation(email: string): Promise<void> {
    const client = this.requireClient();
    const { error } = await this.withTimeout(
      client.resend({
        type: "signup",
        email,
        options: {
          emailRedirectTo: `${window.location.origin}/auth`,
        },
      }),
      "Η επαναποστολή του email επιβεβαίωσης διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.",
    );
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
  }

  async signOut(): Promise<void> {
    if (!this.client) {
      return;
    }
    await this.client.signOut();
  }

  onAuthStateChange(
    callback: (event: AuthChangeEvent, session: AuthSession | null) => void,
  ): AuthSubscription | null {
    if (!this.client) {
      return null;
    }
    return this.client.onAuthStateChange(callback).data.subscription;
  }

  /** Emails a reset link; Neon Auth sends the browser back to /auth?recovery=1&token=…. */
  async requestPasswordReset(email: string): Promise<void> {
    const client = this.requireClient();
    const { error } = await this.withTimeout(
      client.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth?recovery=1`,
      }),
      "Το αίτημα επαναφοράς κωδικού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.",
    );
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
  }

  /**
   * Sets the new password with the token from the reset link. Better Auth
   * resets by token (POST /reset-password); there is no recovery session to
   * update the user through, as Supabase had.
   */
  async updatePassword(password: string, token: string | null): Promise<void> {
    this.requireClient();
    if (!token) {
      throw new Error("Ο σύνδεσμος επαναφοράς δεν είναι έγκυρος ή έχει λήξει. Ζητήστε νέο email επαναφοράς.");
    }
    const response = await this.withTimeout(
      fetch(`${environment.neonAuthUrl}/reset-password`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ newPassword: password, token }),
      }),
      "Η ενημέρωση του κωδικού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.",
    );
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { message?: string; code?: string };
      throw new Error(this.toFriendlyAuthError(body.message || body.code || "Δεν ήταν δυνατή η ενημέρωση του κωδικού πρόσβασης."));
    }
  }

  private requireClient(): NeonAuthClient {
    if (!this.client) {
      throw new Error(NOT_CONFIGURED);
    }
    return this.client;
  }

  private async withTimeout<T>(promise: Promise<T>, message: string, timeoutMs = 15000): Promise<T> {
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

  private toFriendlyAuthError(message: string): string {
    const normalized = message.toLowerCase();
    if (normalized.includes("invalid email or password") || normalized.includes("invalid login credentials")) {
      return "Λάθος email ή κωδικός πρόσβασης.";
    }
    if (normalized.includes("user already exists") || normalized.includes("already registered")) {
      return "Υπάρχει ήδη λογαριασμός με αυτό το email. Συνδεθείτε ή ζητήστε επαναφορά κωδικού.";
    }
    // The adapter turns PASSWORD_TOO_SHORT / PASSWORD_TOO_LONG into
    // "Password does not meet security requirements", which names no rule.
    if (
      normalized.includes("does not meet security requirements")
      || normalized.includes("password too short")
      || normalized.includes("password too long")
      || normalized.includes("password should be")
    ) {
      return `Ο κωδικός δεν πληροί τις απαιτήσεις. ${PASSWORD_REQUIREMENTS}`;
    }
    if (normalized.includes("too many requests") || normalized.includes("rate limit")) {
      return "Έγιναν πάρα πολλές προσπάθειες. Περιμένετε λίγα λεπτά και δοκιμάστε ξανά.";
    }
    if (normalized.includes("email not confirmed") || normalized.includes("email not verified")) {
      return "Το email σας δεν έχει επιβεβαιωθεί ακόμη. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων και πατήστε τον σύνδεσμο επιβεβαίωσης.";
    }
    if (normalized.includes("invalid token") || normalized.includes("invalid_token") || normalized.includes("token expired")) {
      return "Ο σύνδεσμος επαναφοράς δεν είναι έγκυρος ή έχει λήξει. Ζητήστε νέο email επαναφοράς.";
    }
    return message;
  }
}
