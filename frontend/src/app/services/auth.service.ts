import { Injectable } from "@angular/core";
import {
  AuthChangeEvent,
  createClient,
  Session,
  Subscription,
  SupabaseClient,
} from "@supabase/supabase-js";

import { environment } from "../../environments/environment";

export type SignUpResult = {
  needsEmailConfirmation: boolean;
};

type SupabasePasswordGrantResponse = {
  access_token?: string;
  refresh_token?: string;
  error?: string;
  error_description?: string;
  msg?: string;
  message?: string;
};

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly supabase: SupabaseClient | null;

  constructor() {
    this.supabase = environment.supabaseUrl && environment.supabaseAnonKey
      ? createClient(environment.supabaseUrl, environment.supabaseAnonKey)
      : null;
  }

  isConfigured(): boolean {
    return Boolean(this.supabase);
  }

  async getSession(): Promise<Session | null> {
    if (!this.supabase) {
      return null;
    }
    const { data } = await this.supabase.auth.getSession();
    return data.session;
  }

  async getAccessToken(): Promise<string> {
    const session = await this.getSession();
    if (!session?.access_token) {
      throw new Error("Πρέπει πρώτα να συνδεθείτε.");
    }
    return session.access_token;
  }

  async signIn(email: string, password: string): Promise<void> {
    if (!this.supabase) {
      throw new Error("Λείπουν οι ρυθμίσεις περιβάλλοντος του Supabase.");
    }
    const payload = await this.passwordGrant(email, password);
    if (!payload.access_token || !payload.refresh_token) {
      const message = payload.error_description || payload.msg || payload.message || payload.error || "Το Supabase δεν επέστρεψε συνεδρία.";
      throw new Error(this.toFriendlyAuthError(message));
    }
    const { error } = await this.supabase.auth.setSession({
      access_token: payload.access_token,
      refresh_token: payload.refresh_token,
    });
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
  }

  async signUp(email: string, password: string): Promise<SignUpResult> {
    if (!this.supabase) {
      throw new Error("Λείπουν οι ρυθμίσεις περιβάλλοντος του Supabase.");
    }
    const { data, error } = await this.withTimeout(
      this.supabase.auth.signUp({
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
    if (!this.supabase) {
      throw new Error("Λείπουν οι ρυθμίσεις περιβάλλοντος του Supabase.");
    }
    const { error } = await this.withTimeout(
      this.supabase.auth.resend({
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
    if (!this.supabase) {
      return;
    }
    await this.supabase.auth.signOut();
  }

  onAuthStateChange(
    callback: (event: AuthChangeEvent, session: Session | null) => void,
  ): Subscription | null {
    if (!this.supabase) {
      return null;
    }
    return this.supabase.auth.onAuthStateChange(callback).data.subscription;
  }

  async requestPasswordReset(email: string): Promise<void> {
    if (!this.supabase) {
      throw new Error("Λείπουν οι ρυθμίσεις περιβάλλοντος του Supabase.");
    }
    const { error } = await this.withTimeout(
      this.supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth?recovery=1`,
      }),
      "Το αίτημα επαναφοράς κωδικού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.",
    );
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
  }

  async updatePassword(password: string): Promise<void> {
    if (!this.supabase) {
      throw new Error("Λείπουν οι ρυθμίσεις περιβάλλοντος του Supabase.");
    }
    const { error } = await this.withTimeout(
      this.supabase.auth.updateUser({ password }),
      "Η ενημέρωση του κωδικού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.",
    );
    if (error) {
      throw new Error(this.toFriendlyAuthError(error.message));
    }
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

  private async passwordGrant(email: string, password: string): Promise<SupabasePasswordGrantResponse> {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(`${environment.supabaseUrl.replace(/\/$/, "")}/auth/v1/token?grant_type=password`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "apikey": environment.supabaseAnonKey,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json().catch(() => ({})) as SupabasePasswordGrantResponse;
      if (!response.ok) {
        const message = payload.error_description || payload.msg || payload.message || payload.error || "Η σύνδεση μέσω Supabase απέτυχε.";
        throw new Error(this.toFriendlyAuthError(message));
      }
      return payload;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error("Η σύνδεση μέσω Supabase έληξε μετά από 20 δευτερόλεπτα. Ελέγξτε το δίκτυό σας και την κατάσταση του Supabase project, ή δοκιμάστε ξανά.");
      }
      if (error instanceof Error) {
        throw error;
      }
      throw new Error("Η σύνδεση μέσω Supabase απέτυχε πριν προλάβει το RoomRate να φορτώσει τον χώρο εργασίας σας.");
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  private toFriendlyAuthError(message: string): string {
    const normalized = message.toLowerCase();
    if (normalized.includes("email rate limit")) {
      return "Ζητήθηκαν πάρα πολλά email επιβεβαίωσης. Το Supabase περιόρισε προσωρινά αυτό το project. Περιμένετε λίγα λεπτά και ελέγξτε τα εισερχόμενα και τα ανεπιθύμητα, ή απενεργοποιήστε την επιβεβαίωση email για τοπική ανάπτυξη.";
    }
    if (normalized.includes("email not confirmed")) {
      return "Το email σας δεν έχει επιβεβαιωθεί ακόμη. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων και πατήστε τον σύνδεσμο επιβεβαίωσης.";
    }
    return message;
  }
}
