import { Component, Input } from "@angular/core";
import { NgIf } from "@angular/common";
import { Router, RouterLink, RouterLinkActive } from "@angular/router";
import { AuthService } from "../services/auth.service";
import { NotificationsService } from "../services/notifications.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import { NotificationBellComponent } from "./notification-bell.component";

/**
 * The Aegean band every signed-in page opens with: the brand, the one
 * navigation, the bell and «Αποσύνδεση» on top, the page's own heading under
 * them (projected content).
 *
 * Projection slots:
 * - `[headerStatus]`: status chips that only report (the map's result count).
 *   They render inside `.header-actions`, before the navigation.
 * - default: the page heading block (eyebrow, h1, subtitle).
 *
 * `.map-header` and `.header-actions` stay on the markup: the e2e suite reads
 * the map's result count through `.header-actions`.
 */
@Component({
  selector: "app-header",
  standalone: true,
  imports: [NgIf, RouterLink, RouterLinkActive, NotificationBellComponent],
  template: `
    <header class="map-header app-band" [class.app-band-compact]="compact">
      <div class="app-topbar">
        <a class="app-brand" routerLink="/map" aria-label="RoomRate, αρχική">
          <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
            <circle cx="16" cy="12" r="6" fill="#f2a65a"></circle>
            <path d="M3 21c3.2-2.4 6.4-2.4 9.6 0s6.4 2.4 9.6 0 6.4-2.4 9.6 0" stroke="#ffffff" stroke-width="2.4" fill="none" stroke-linecap="round"></path>
            <path d="M3 26.5c3.2-2.4 6.4-2.4 9.6 0s6.4 2.4 9.6 0 6.4-2.4 9.6 0" stroke="#ffffff" stroke-width="2.4" fill="none" stroke-linecap="round" opacity="0.5"></path>
          </svg>
          <span>RoomRate</span>
        </a>
        <div class="header-actions">
          <ng-content select="[headerStatus]"></ng-content>
          <nav *ngIf="showNav" class="header-nav" aria-label="Πλοήγηση">
            <a class="header-nav-link" routerLink="/map" routerLinkActive="is-active" ariaCurrentWhenActive="page">Χάρτης</a>
            <a class="header-nav-link" routerLink="/pricing" routerLinkActive="is-active" ariaCurrentWhenActive="page">Τιμολόγηση</a>
            <a class="header-nav-link" routerLink="/settings" routerLinkActive="is-active" ariaCurrentWhenActive="page">Ρυθμίσεις</a>
          </nav>
          <app-notification-bell *ngIf="showNav" />
          <button class="header-nav-link header-signout" type="button" (click)="signOut()">Αποσύνδεση</button>
        </div>
      </div>
      <div class="app-band-heading">
        <ng-content></ng-content>
      </div>
    </header>
  `,
})
export class AppHeaderComponent {
  /** Map page: one short band, so the map keeps the height. */
  @Input() compact = false;
  /** The setup wizard has nowhere to go yet: no navigation, no bell. */
  @Input() showNav = true;

  constructor(
    private readonly auth: AuthService,
    private readonly notifications: NotificationsService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
  ) {}

  async signOut(): Promise<void> {
    this.notifications.stop();
    await this.auth.signOut();
    this.workflow.clear();
    await this.router.navigateByUrl("/auth");
  }
}
