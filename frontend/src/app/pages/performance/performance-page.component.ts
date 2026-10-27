/**
 * «Απόδοση»: how the property did, from the owner's own bookings (Excel or
 * the Booking.com export), next to the market prices RoomRate collects.
 *
 * The page owns `/status` and the view in the URL (performance-filter-state.ts):
 * tabs, filter bar, the empty state before any upload and the notice while
 * room counts are missing. Each tab's analyses (A4–A8), the upload flow (A3)
 * and the rooms form (A9) open from here.
 * Plan, task split and file ownership: docs/performance/PLAN.md.
 */
import { CommonModule } from "@angular/common";
import { Component, computed, effect, ElementRef, inject, OnInit, signal, viewChild } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";
import { ActivatedRoute, Router } from "@angular/router";

import { AppHeaderComponent } from "../../components/app-header.component";
import { OnboardingService } from "../../services/onboarding.service";
import { PerformanceApiService } from "../../services/performance-api.service";
import type { PerformanceStatus } from "../../types/performance";
import { formatGreekDate } from "../pricing/pricing-format";
import {
  PERFORMANCE_TABS,
  PerformanceTab,
  PerformanceView,
  readView,
  sameFilters,
  toApiFilters,
  toQueryParams,
} from "./performance-filter-state";
import { PerformanceFiltersComponent } from "./performance-filters.component";
import { PerformanceForecastComponent } from "./performance-forecast.component";
import { PerformanceOccupancyComponent } from "./performance-occupancy.component";
import { PerformanceOverviewComponent } from "./performance-overview.component";
import { PerformanceRoomsFormComponent } from "./performance-rooms-form.component";
import { PerformanceRoomsComponent } from "./performance-rooms.component";
import { PerformanceStaysComponent } from "./performance-stays.component";
import { PerformanceUploadComponent } from "./performance-upload.component";

/** The side tasks the page opens over the tabs. */
type PerformancePanel = "upload" | "rooms";

const TEMPLATE_FILENAME = "RoomRate_kratiseis.xlsx";

@Component({
  selector: "app-performance-page",
  standalone: true,
  imports: [
    CommonModule,
    AppHeaderComponent,
    PerformanceFiltersComponent,
    PerformanceOverviewComponent,
    PerformanceOccupancyComponent,
    PerformanceRoomsComponent,
    PerformanceStaysComponent,
    PerformanceForecastComponent,
    PerformanceUploadComponent,
    PerformanceRoomsFormComponent,
  ],
  template: `
    <main class="page-shell">
      <app-header>
        <p class="eyebrow">Απόδοση</p>
        <h1>Πώς πάει το κατάλυμα</h1>
        <p class="app-band-subtitle">Πληρότητα, μέση τιμή και έσοδα από τις κρατήσεις σας, δίπλα στις τιμές της αγοράς.</p>
      </app-header>

      <section class="page-body performance-body">
        <div *ngIf="status() === null && !error()" class="panel" data-testid="performance-loading">
          <p class="muted">Φόρτωση…</p>
        </div>
        <p *ngIf="error()" class="alert alert-error" role="alert" data-testid="performance-error">{{ error() }}</p>

        <ng-container *ngIf="status() as current">
          <p *ngIf="templateError()" class="alert alert-error" role="alert" data-testid="performance-template-error">
            {{ templateError() }}
          </p>

          <section *ngIf="!current.has_data" class="panel performance-empty" data-testid="performance-empty">
            <h2>Δεν υπάρχουν ακόμη κρατήσεις</h2>
            <p class="muted">
              Ανεβάστε τις κρατήσεις σας, από το πρότυπο του RoomRate ή όπως τις κατεβάζετε από το Booking.com,
              για να δείτε πληρότητα, μέση τιμή και έσοδα ανά μήνα.
            </p>
            <div class="performance-actions">
              <button class="secondary-button" type="button" data-testid="performance-empty-upload" (click)="openPanel('upload')">
                Ανέβασμα κρατήσεων
              </button>
              <button class="text-button" type="button" data-testid="performance-template" [disabled]="templateBusy()" (click)="downloadTemplate()">
                Κατέβασε το πρότυπο
              </button>
            </div>
          </section>

          <ng-container *ngIf="current.has_data">
            <div
              *ngIf="!current.inventory_complete"
              class="alert performance-notice"
              role="status"
              data-testid="performance-inventory-notice"
            >
              <div>
                <strong>Ορίστε τα δωμάτια ανά τύπο</strong>
                <p>
                  Χωρίς τον αριθμό δωματίων δεν υπολογίζονται πληρότητα και RevPAR.
                  Λείπει για: {{ missingRoomTypes().join(", ") }}.
                </p>
              </div>
              <button class="text-button" type="button" data-testid="performance-inventory-link" (click)="openPanel('rooms')">
                Ορισμός δωματίων
              </button>
            </div>

            <div class="panel performance-toolbar">
              <p class="muted" data-testid="performance-bookings">
                {{ formatCount(current.bookings) }} κρατήσεις
                <ng-container *ngIf="current.last_import as lastImport">
                  · τελευταίο ανέβασμα {{ formatDate(lastImport.committed_at) }} ({{ lastImport.filename }})
                </ng-container>
              </p>
              <div class="performance-actions">
                <button class="text-button" type="button" data-testid="performance-template" [disabled]="templateBusy()" (click)="downloadTemplate()">
                  Κατέβασε το πρότυπο
                </button>
                <button class="secondary-button" type="button" data-testid="performance-upload-open" (click)="openPanel('upload')">
                  Ανέβασμα κρατήσεων
                </button>
              </div>
            </div>

            <app-performance-filters [status]="current" [view]="view()" (changed)="updateView($event)"></app-performance-filters>
          </ng-container>

          <section
            *ngIf="panel() as openedPanel"
            class="panel performance-side-panel"
            [attr.data-testid]="'performance-' + openedPanel + '-panel'"
            [attr.aria-label]="openedPanel === 'upload' ? 'Ανέβασμα κρατήσεων' : 'Δωμάτια ανά τύπο'"
          >
            <div class="performance-side-panel-head">
              <h2>{{ openedPanel === "upload" ? "Ανέβασμα κρατήσεων" : "Δωμάτια ανά τύπο" }}</h2>
              <button class="text-button" type="button" data-testid="performance-panel-close" (click)="panel.set(null)">Κλείσιμο</button>
            </div>
            <app-performance-upload
              *ngIf="openedPanel === 'upload'"
              [ownedPropertyId]="ownedPropertyId()"
              [status]="current"
              (statusChanged)="statusChanged($event)"
              (templateRequested)="downloadTemplate()"
            ></app-performance-upload>
            <app-performance-rooms-form
              *ngIf="openedPanel === 'rooms'"
              [ownedPropertyId]="ownedPropertyId()"
              [status]="current"
              (statusChanged)="statusChanged($event)"
            ></app-performance-rooms-form>
          </section>

          <ng-container *ngIf="current.has_data">
            <div #tablist role="tablist" aria-label="Ενότητες απόδοσης" class="performance-tabs">
              <button
                *ngFor="let tab of tabs; index as i"
                type="button"
                role="tab"
                class="performance-tab"
                [id]="'performance-tab-' + tab.id"
                [attr.aria-selected]="tab.id === view().tab"
                [attr.aria-controls]="'performance-tabpanel'"
                [attr.tabindex]="tab.id === view().tab ? 0 : -1"
                [attr.data-testid]="'performance-tab-' + tab.id"
                (click)="selectTab(tab.id)"
                (keydown)="onTabKey($event, i)"
              >{{ tab.label }}</button>
            </div>

            <div
              id="performance-tabpanel"
              class="performance-tabpanel"
              role="tabpanel"
              [attr.aria-labelledby]="'performance-tab-' + view().tab"
              [attr.data-testid]="'performance-tabpanel-' + view().tab"
              [ngSwitch]="view().tab"
            >
              <app-performance-overview
                *ngSwitchCase="'overview'"
                [ownedPropertyId]="ownedPropertyId()"
                [filters]="apiFilters()"
                [version]="dataVersion()"
                (openRooms)="openPanel('rooms')"
              ></app-performance-overview>
              <app-performance-occupancy
                *ngSwitchCase="'occupancy'"
                [ownedPropertyId]="ownedPropertyId()"
                [filters]="apiFilters()"
                [status]="current"
                [version]="dataVersion()"
                (openRooms)="openPanel('rooms')"
              ></app-performance-occupancy>
              <app-performance-rooms
                *ngSwitchCase="'rooms'"
                [ownedPropertyId]="ownedPropertyId()"
                [filters]="apiFilters()"
                [selectedRoom]="view().room"
                [version]="dataVersion()"
                (roomSelected)="updateView({ room: $event })"
                (openRooms)="openPanel('rooms')"
              ></app-performance-rooms>
              <app-performance-stays
                *ngSwitchCase="'stays'"
                [ownedPropertyId]="ownedPropertyId()"
                [filters]="apiFilters()"
                [version]="dataVersion()"
              ></app-performance-stays>
              <app-performance-forecast
                *ngSwitchCase="'forecast'"
                [ownedPropertyId]="ownedPropertyId()"
                [version]="dataVersion()"
              ></app-performance-forecast>
            </div>
          </ng-container>
        </ng-container>
      </section>
    </main>
  `,
})
export class PerformancePageComponent implements OnInit {
  readonly tabs = PERFORMANCE_TABS;
  readonly status = signal<PerformanceStatus | null>(null);
  readonly error = signal("");
  readonly panel = signal<PerformancePanel | null>(null);
  readonly templateBusy = signal(false);
  readonly templateError = signal("");

  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly queryParams = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  readonly ownedPropertyId = signal("");
  /** Bumped when the bookings or rooms change (upload, delete, rooms form): every tab reads its data again. */
  readonly dataVersion = signal(0);

  /** The view the URL asks for, resolved against /status: the single source for tabs, filters and calls. */
  readonly view = computed(() => readView(this.queryParams(), this.status()));
  /** The analysis calls' query; equal views give an equal (===) object so tabs do not reload for nothing. */
  readonly apiFilters = computed(() => toApiFilters(this.view()), { equal: sameFilters });
  readonly missingRoomTypes = computed(() =>
    (this.status()?.room_types ?? []).filter((type) => type.rooms === null).map((type) => type.label));

  private readonly tablist = viewChild<ElementRef<HTMLElement>>("tablist");

  constructor(
    private readonly onboarding: OnboardingService,
    private readonly performance: PerformanceApiService,
  ) {
    // On a phone the tab row scrolls sideways: keep the active tab (e.g. from a shared link) in sight.
    effect(() => {
      const list = this.tablist()?.nativeElement;
      const active = list?.querySelector<HTMLElement>(`#performance-tab-${this.view().tab}`);
      if (list && active) {
        list.scrollLeft = active.offsetLeft - list.offsetLeft - (list.clientWidth - active.offsetWidth) / 2;
      }
    });
  }

  async ngOnInit(): Promise<void> {
    try {
      const me = await this.onboarding.currentUser();
      this.ownedPropertyId.set(me.owned_property_id ?? "");
      this.status.set(await this.performance.status(this.ownedPropertyId()));
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : "Η απόδοση δεν είναι διαθέσιμη αυτή τη στιγμή.");
    }
  }

  /** Every change goes through the URL; `merge` keeps unrelated params such as `mock=1`. */
  updateView(change: Partial<PerformanceView>): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: toQueryParams({ ...this.view(), ...change }),
      queryParamsHandling: "merge",
    });
  }

  selectTab(tab: PerformanceTab): void {
    if (tab !== this.view().tab) this.updateView({ tab });
  }

  /** Arrow keys, Home and End move between tabs (WAI-ARIA tabs pattern). */
  onTabKey(event: KeyboardEvent, index: number): void {
    const last = this.tabs.length - 1;
    const target = {
      ArrowRight: index === last ? 0 : index + 1,
      ArrowLeft: index === 0 ? last : index - 1,
      Home: 0,
      End: last,
    }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    const tab = this.tabs[target];
    this.selectTab(tab.id);
    document.getElementById(`performance-tab-${tab.id}`)?.focus();
  }

  openPanel(panel: PerformancePanel): void {
    this.panel.set(panel);
  }

  statusChanged(status: PerformanceStatus): void {
    this.status.set(status);
    this.dataVersion.update((version) => version + 1);
  }

  async downloadTemplate(): Promise<void> {
    this.templateBusy.set(true);
    this.templateError.set("");
    try {
      const blob = await this.performance.template(this.ownedPropertyId());
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = TEMPLATE_FILENAME;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      this.templateError.set(error instanceof Error ? error.message : "Το πρότυπο δεν κατέβηκε. Δοκιμάστε ξανά.");
    } finally {
      this.templateBusy.set(false);
    }
  }

  formatDate(value: string): string {
    return formatGreekDate(value);
  }

  formatCount(value: number): string {
    return new Intl.NumberFormat("el-GR").format(value);
  }
}
