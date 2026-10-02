/**
 * The collapsible «Φίλτρα αναζήτησης» form of the map page: stay dates, the
 * hotel count, nearby areas, radius, facilities and the «Εύρεση
 * ανταγωνιστών» button with the search's own banners under it.
 *
 * Presentational only: every value comes from the page and every change goes
 * back to it as an output, so the page stays the one owner of the search
 * state (and of the signals its load chains write after awaits). The host is
 * the page's `<aside class="filters-sidebar">` itself (attribute selector),
 * so the grid layout and the stylesheet see the same element as before.
 */
import { CommonModule } from "@angular/common";
import { Component, input, output } from "@angular/core";
import { FormsModule } from "@angular/forms";

import type { OwnedPropertyRoomType } from "../../types/market";
import { AmenityOption, MAX_NEARBY_DESTINATIONS, MapFilters } from "./map-page.constants";

export type MapFilterChange = { key: keyof MapFilters; value: string };

@Component({
  selector: "aside[appMapFiltersSidebar]",
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="filters-title-row">
      <h2>Φίλτρα αναζήτησης</h2>
      <button class="panel-toggle-button" type="button" (click)="collapseRequest.emit()">Απόκρυψη</button>
    </div>
    <!-- The room itself is chosen at the top of the results column («Σύγκριση για»); a new search runs for it. -->
    <div *ngIf="baselineRoom()" class="baseline-room">
      <strong>{{ baselineRoom()?.room_type }}</strong>
      <small>Το RoomRate ταιριάζει δωμάτια ανταγωνιστών με παρόμοια ονόματα και την ίδια κανονικοποιημένη κατηγορία.</small>
    </div>
    <label>
      <span>Περιοχή</span>
      <input class="roomrate-input muted-input" [value]="filters().destination" readonly>
    </label>
    <div class="filter-section-label">Ημερομηνίες διαμονής</div>
    <div class="two-cols">
      <label>
        <span>Άφιξη</span>
        <input class="roomrate-input" type="date" [ngModel]="filters().check_in" (ngModelChange)="change('check_in', $event)">
      </label>
      <label>
        <span>Αναχώρηση</span>
        <input class="roomrate-input" type="date" [ngModel]="filters().check_out" (ngModelChange)="change('check_out', $event)">
      </label>
    </div>
    <label>
      <span>Πλήθος ανταγωνιστών</span>
      <input class="roomrate-input" min="1" max="80" type="number" data-testid="limit-input" [ngModel]="filters().limit" (ngModelChange)="change('limit', $event)">
    </label>
    <!--
      A div, not a label: it holds the chips' own remove buttons. Enter in
      the input and the button both add; the list is what the POST sends.
    -->
    <div class="nearby-field">
      <span class="nearby-field-label">Γειτονικές περιοχές</span>
      <div *ngIf="nearbyDestinations().length" class="nearby-chips">
        <span *ngFor="let area of nearbyDestinations()" class="nearby-chip">
          <span data-testid="nearby-chip">{{ area }}</span>
          <button type="button" [attr.aria-label]="'Αφαίρεση περιοχής ' + area" (click)="nearbyRemove.emit(area)">×</button>
        </span>
      </div>
      <div class="nearby-add-row">
        <input
          class="roomrate-input"
          data-testid="nearby-input"
          aria-label="Νέα γειτονική περιοχή"
          placeholder="π.χ. Ιξιά"
          maxlength="100"
          [disabled]="!canAddNearbyDestination()"
          [ngModel]="nearbyDraft()"
          (ngModelChange)="nearbyDraftChange.emit($event)"
          (keydown.enter)="nearbyAdd.emit()"
        >
        <button class="panel-toggle-button" type="button" [disabled]="!canAddNearbyDestination()" (click)="nearbyAdd.emit()">Προσθήκη</button>
      </div>
      <small class="nearby-hint">Έως {{ maxNearbyDestinations }} περιοχές εκτός από την κύρια.</small>
    </div>
    <label>
      <span>Ακτίνα (km)</span>
      <input class="roomrate-input" min="1" max="30" step="1" type="number" data-testid="radius-input" [ngModel]="filters().radius_km" (ngModelChange)="change('radius_km', $event)">
    </label>

    <div class="amenity-list">
      <div class="amenity-list-header">
        <span>Δημοφιλέστερες παροχές</span>
        <button type="button" [disabled]="!selectedAmenities().size" (click)="amenitiesClear.emit()">Καθαρισμός</button>
      </div>
      <label *ngFor="let amenity of amenities()" class="checkbox-row">
        <input type="checkbox" [checked]="selectedAmenities().has(amenity.value)" (change)="amenityToggle.emit(amenity.value)">
        <span>{{ amenity.label }}</span>
      </label>
    </div>

    <button class="primary-button" type="button" [disabled]="searching()" (click)="findRequest.emit()">
      {{ searching() ? "Γίνεται αναζήτηση" : "Εύρεση ανταγωνιστών" }}
    </button>
    <div *ngIf="message()" class="alert">{{ message() }}</div>
    <!-- What the scrape itself reported about this result (spec §3.1/§3.3), under the banner that describes it. -->
    <p *ngFor="let warning of searchWarnings()" class="search-warning" data-testid="search-warning">{{ warning }}</p>
    <!-- Live progress of the search this page started (spec §4.2), under the banner. -->
    <div *ngIf="scrapeInFlight()" class="scrape-progress" data-testid="scrape-progress">
      <span role="status" data-testid="scrape-progress-text">{{ scrapeProgressLabel() }}</span>
      <span *ngIf="scrapeElapsedLabel()" class="scrape-progress-clock" data-testid="scrape-elapsed">{{ scrapeElapsedLabel() }}</span>
    </div>
    <div *ngIf="error()" class="alert alert-error">{{ error() }}</div>
  `,
})
export class MapFiltersSidebarComponent {
  readonly maxNearbyDestinations = MAX_NEARBY_DESTINATIONS;

  /** The owner's room a new search runs for, or null before one is known. */
  readonly baselineRoom = input<OwnedPropertyRoomType | null>(null);
  readonly filters = input.required<MapFilters>();
  readonly nearbyDestinations = input.required<string[]>();
  readonly nearbyDraft = input.required<string>();
  readonly canAddNearbyDestination = input.required<boolean>();
  readonly amenities = input.required<AmenityOption[]>();
  readonly selectedAmenities = input.required<ReadonlySet<string>>();
  /** A search is being started or polled: the button waits for it. */
  readonly searching = input.required<boolean>();
  readonly message = input.required<string>();
  readonly searchWarnings = input.required<string[]>();
  readonly scrapeInFlight = input.required<boolean>();
  readonly scrapeProgressLabel = input.required<string>();
  readonly scrapeElapsedLabel = input.required<string>();
  readonly error = input.required<string>();

  readonly collapseRequest = output<void>();
  readonly filterChange = output<MapFilterChange>();
  readonly nearbyDraftChange = output<string>();
  readonly nearbyAdd = output<void>();
  readonly nearbyRemove = output<string>();
  readonly amenityToggle = output<string>();
  readonly amenitiesClear = output<void>();
  readonly findRequest = output<void>();

  change(key: keyof MapFilters, value: string): void {
    this.filterChange.emit({ key, value });
  }
}
