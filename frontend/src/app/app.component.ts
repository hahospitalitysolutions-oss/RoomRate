import { Component, OnDestroy, OnInit } from "@angular/core";
import { RouterOutlet } from "@angular/router";
import { AuthService, AuthSubscription } from "./services/auth.service";
import { NotificationsService } from "./services/notifications.service";
import { SetupProgressService } from "./services/setup-progress.service";
import { WorkflowStorageService } from "./services/workflow-storage.service";

@Component({
  selector: "app-root",
  standalone: true,
  imports: [RouterOutlet],
  template: "<router-outlet />",
})
export class AppComponent implements OnInit, OnDestroy {
  private authSubscription: AuthSubscription | null = null;

  constructor(
    private readonly auth: AuthService,
    private readonly notifications: NotificationsService,
    private readonly workflow: WorkflowStorageService,
    private readonly setupProgress: SetupProgressService,
  ) {}

  async ngOnInit(): Promise<void> {
    const session = await this.auth.getSession().catch(() => null);
    if (session?.user.id) {
      this.workflow.bindToSubject(session.user.id);
      void this.notifications.start();
    }

    this.authSubscription = this.auth.onAuthStateChange((event, nextSession) => {
      if (event === "SIGNED_OUT" || !nextSession?.user.id) {
        this.notifications.stop();
        this.workflow.clear();
        this.setupProgress.reset();
        return;
      }
      this.workflow.bindToSubject(nextSession.user.id);
      // Return quickly from the auth callback and start REST synchronization
      // on the next task, so no auth call nests inside another.
      window.setTimeout(() => void this.notifications.start(), 0);
    });
  }

  ngOnDestroy(): void {
    this.authSubscription?.unsubscribe();
    this.notifications.stop();
  }
}
