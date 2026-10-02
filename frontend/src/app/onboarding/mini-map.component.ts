import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  OnChanges,
  OnDestroy,
  ViewChild,
  signal,
} from "@angular/core";
import type mapboxgl from "mapbox-gl";

import { environment } from "../../environments/environment";
import { PropertyCandidate } from "../types/market";

/**
 * Lifecycle owner for the setup wizard's Mapbox preview.
 *
 * The component creates one map for its own DOM node, removes it with that
 * node, and rebuilds candidate markers whenever either the candidates or the
 * selected key changes. A missing token or coordinates degrades to the
 * neutral map placeholder without blocking the onboarding flow.
 */
@Component({
  selector: "app-mini-map",
  standalone: true,
  template: `<div #mapContainer class="map-container"></div>`,
  styles: [`
    :host { display: block; min-height: 260px; border-radius: 0.5rem; overflow: hidden; background: #e2e8f0; }
    .map-container { width: 100%; height: 100%; min-height: inherit; }
  `],
})
export class MiniMapComponent implements AfterViewInit, OnChanges, OnDestroy {
  @Input() candidates: readonly PropertyCandidate[] = [];
  @Input() selectedKey = "";

  @ViewChild("mapContainer", { static: true })
  private mapContainer!: ElementRef<HTMLDivElement>;

  private readonly mapboxModule = signal<typeof mapboxgl | null>(null);
  private readonly map = signal<mapboxgl.Map | null>(null);
  private readonly markers = signal<mapboxgl.Marker[]>([]);
  private readonly mapInitialization = signal<Promise<void> | null>(null);
  private viewReady = false;
  private destroyed = false;

  ngAfterViewInit(): void {
    this.viewReady = true;
    void this.renderMarkers();
  }

  ngOnChanges(): void {
    if (this.viewReady) {
      void this.renderMarkers();
    }
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.resetMap();
  }

  private async renderMarkers(): Promise<void> {
    try {
      await this.renderMarkersSafely();
    } catch {
      // Dynamic-import, WebGL and Mapbox construction errors are optional UI
      // failures: reset to the neutral placeholder and keep onboarding usable.
      this.resetMap();
    }
  }

  private async renderMarkersSafely(): Promise<void> {
    if (this.destroyed) {
      return;
    }
    const placeable = this.placeableCandidates();
    if (!environment.mapboxToken || !this.viewReady || !placeable.length) {
      this.removeMarkers();
      return;
    }

    await this.ensureMap(placeable[0]);
    // The dynamic chunk may finish after an *ngIf removed this component.
    const currentMap = this.map();
    const mapboxModule = this.mapboxModule();
    if (this.destroyed || !currentMap || !mapboxModule) {
      return;
    }

    const currentCandidates = this.placeableCandidates();
    this.removeMarkers();
    this.markers.set(currentCandidates.map((candidate) =>
      new mapboxModule.Marker({
        color: candidate.candidate_key === this.selectedKey ? "#0f766e" : "#64748b",
      })
        .setLngLat([candidate.longitude!, candidate.latitude!])
        .addTo(currentMap),
    ));

    if (currentCandidates.length > 1) {
      const bounds = new mapboxModule.LngLatBounds();
      currentCandidates.forEach((candidate) => bounds.extend([candidate.longitude!, candidate.latitude!]));
      currentMap.fitBounds(bounds, { padding: 40, maxZoom: 14 });
    }
  }

  private async ensureMap(firstCandidate: PropertyCandidate): Promise<void> {
    if (this.map()) {
      return;
    }
    if (!this.mapInitialization()) {
      this.mapInitialization.set(this.createMap(firstCandidate).finally(() => {
        this.mapInitialization.set(null);
      }));
    }
    await this.mapInitialization();
  }

  private async createMap(firstCandidate: PropertyCandidate): Promise<void> {
    const mapboxModule = (await import("mapbox-gl")).default;
    // Mandatory post-import guard: destroy can happen while the chunk loads.
    if (this.destroyed || !this.viewReady) {
      return;
    }

    mapboxModule.accessToken = environment.mapboxToken;
    this.mapboxModule.set(mapboxModule);
    this.map.set(new mapboxModule.Map({
      container: this.mapContainer.nativeElement,
      style: "mapbox://styles/mapbox/streets-v12",
      center: [firstCandidate.longitude!, firstCandidate.latitude!],
      zoom: 13,
    }));
  }

  private placeableCandidates(): PropertyCandidate[] {
    return this.candidates.filter(
      (candidate): candidate is PropertyCandidate => candidate.latitude != null && candidate.longitude != null,
    );
  }

  private removeMarkers(): void {
    this.markers().forEach((marker) => {
      try {
        marker.remove();
      } catch {
        // Cleanup is best-effort; resetMap also removes the owning map.
      }
    });
    this.markers.set([]);
  }

  private resetMap(): void {
    this.removeMarkers();
    try {
      this.map()?.remove();
    } catch {
      // A partially constructed WebGL map may also reject during teardown.
    }
    this.map.set(null);
    this.mapboxModule.set(null);
  }
}
