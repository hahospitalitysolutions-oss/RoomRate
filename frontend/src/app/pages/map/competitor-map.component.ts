/**
 * The Mapbox map of the competitor results: one marker per hotel, «Εσείς» and
 * its radius circle, the camera fit and the map's own notices.
 *
 * Moved out of map-page.component.ts with its logic unchanged. The markers are
 * imperative (Mapbox owns their DOM), so the page still decides WHEN they are
 * redrawn — renderMarkers() after a load, a tick or a filter change, never on
 * an intermediate state such as the empty list a re-read starts from, which
 * would close the popup the user is reading. What they show is read at that
 * moment through `source` (CompetitorMapSource), so a render can never see
 * inputs that change detection has not delivered yet.
 *
 * The host is the page's own `.map-canvas` element (attribute selector), so
 * the DOM and the stylesheet's sibling selectors stay exactly as they were;
 * the page projects its «Φίλτρα» button in first.
 */
import { CommonModule } from "@angular/common";
import { AfterViewInit, Component, ElementRef, OnDestroy, ViewChild, input, output, signal } from "@angular/core";
import type { Feature, Polygon } from "geojson";
import type mapboxgl from "mapbox-gl";

import { environment } from "../../../environments/environment";
import type { CompetitorMapMarker, OwnPropertyMapInfo } from "../../types/market";
import { categoryLabel, formatDistance, formatEuro, formatRadius, hotelKey, markerKey } from "./competitor-format";

/** The page state the map draws, read at render time. */
export interface CompetitorMapSource {
  /** The competitor rows to plot: coordinate-checked, after the page's map filter. */
  visibleCompetitors(): CompetitorMapMarker[];
  /** markerKey()s of the rows ticked for tracking. */
  selectedKeys(): ReadonlySet<string>;
  /** «Εσείς» as the API returned it, or null. */
  ownProperty(): OwnPropertyMapInfo | null;
  /** Its validated position, or null when it cannot be placed. */
  ownLocation(): { lng: number; lat: number } | null;
  /** The match score a hotel's popup shows, or null (matching off, or no score). */
  popupMatchScore(hotelName: string): number | null;
}

type MapboxModule = typeof mapboxgl;
// One competitor marker on the map, kept across repaints (see renderMarkers).
// `popupHtml` and the coordinates are what the entry last rendered, so a repaint
// only touches what really changed: setHTML on an open popup rebuilds its
// content and moves focus into it.
type MarkerEntry = {
  marker: mapboxgl.Marker;
  element: HTMLButtonElement;
  // The price text inside the marker; hidden by the stylesheet when zoomed out.
  pricePill: HTMLSpanElement;
  popup: mapboxgl.Popup;
  popupHtml: string;
  lng: number;
  lat: number;
};

// A map that cannot draw says so where the map is, and says the one thing the
// user needs to know about the rest of the screen: the numbers still hold.
const MAP_LOAD_FAILED_MESSAGE =
  "Ο χάρτης δεν μπόρεσε να φορτώσει. Τα αποτελέσματα δεν επηρεάζονται.";

const DEFAULT_CENTER: [number, number] = [28.199, 36.3396];
const DEFAULT_ZOOM = 14;
// Below this zoom a dense market's price pills pile up (40 hotels inside
// 1.2 km of Faliraki centre): the markers drop their text and become dots.
const PRICE_LABEL_MIN_ZOOM = 13.5;
const PRICE_BAND_CLASSES = ["roomrate-marker-dot-low", "roomrate-marker-dot-mid", "roomrate-marker-dot-high"];

type PriceTertiles = { low: number; high: number };

/**
 * The two cut points that split the prices on the map into thirds (spec §4.4).
 *
 * Relative to the markers actually drawn, not fixed euros: the old 75/130 €
 * thresholds painted a whole 200-260 € market «high». Interpolated between the
 * sorted prices (numpy's default), so three markers still get three colours.
 * Null when there is no spread to split — a single price, or one price for
 * all — which reads as «Μεσαία» rather than as a cheap or a dear market.
 */
function priceTertiles(prices: number[]): PriceTertiles | null {
  const sorted = prices.filter((price) => Number.isFinite(price)).sort((a, b) => a - b);
  if (sorted.length < 2 || sorted[0] === sorted[sorted.length - 1]) {
    return null;
  }
  const quantile = (fraction: number): number => {
    const position = (sorted.length - 1) * fraction;
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, sorted.length - 1);
    return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
  };
  return { low: quantile(1 / 3), high: quantile(2 / 3) };
}

// The owner's radius circle on the map (spec §4.3): one GeoJSON source drawn
// by a fill and an outline layer.
const RADIUS_SOURCE_ID = "roomrate-own-radius";
const RADIUS_FILL_LAYER_ID = "roomrate-own-radius-fill";
const RADIUS_LINE_LAYER_ID = "roomrate-own-radius-line";
const EARTH_RADIUS_KM = 6371;

/**
 * A circle of `km` around a point, as a closed GeoJSON polygon of `steps`
 * points (spec §4.3: 64).
 *
 * Spherical destination-point formula rather than a flat offset in degrees: a
 * degree of longitude at Rhodes (36° N) is 20% shorter than a degree of
 * latitude, so a "circle" drawn in degrees would be an ellipse that puts
 * in-radius hotels outside it.
 */
export function circlePolygon(lng: number, lat: number, km: number, steps = 64): Feature<Polygon> {
  const angularDistance = km / EARTH_RADIUS_KM;
  const latRad = (lat * Math.PI) / 180;
  const lngRad = (lng * Math.PI) / 180;
  const ring: number[][] = [];
  for (let step = 0; step < steps; step += 1) {
    const bearing = (2 * Math.PI * step) / steps;
    const pointLat = Math.asin(
      Math.sin(latRad) * Math.cos(angularDistance) + Math.cos(latRad) * Math.sin(angularDistance) * Math.cos(bearing),
    );
    const pointLng = lngRad + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latRad),
      Math.cos(angularDistance) - Math.sin(latRad) * Math.sin(pointLat),
    );
    ring.push([(pointLng * 180) / Math.PI, (pointLat * 180) / Math.PI]);
  }
  // GeoJSON rings are closed: the last position repeats the first.
  ring.push([...ring[0]]);
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

@Component({
  selector: "div[appCompetitorMap]",
  standalone: true,
  imports: [CommonModule],
  template: `
    <ng-content></ng-content>
    <!--
      data-radius-km: the radius circle the map was given (the circle itself is WebGL).
      is-zoomed-out / data-zoomed-out: below zoom 13.5 the competitor markers are dots.
      data-fit-count: how many times the camera was fitted (a re-read must not add one).
    -->
    <div
      #mapContainer
      class="roomrate-map"
      [class.is-zoomed-out]="mapZoomedOut()"
      [attr.data-zoomed-out]="mapZoomedOut()"
      [attr.data-radius-km]="radiusCircleKm()"
      [attr.data-fit-count]="mapFitCount()"
    ></div>
    <div *ngIf="mapError()" class="map-notice map-error-notice" data-testid="map-error-notice">
      {{ mapError() }}
    </div>
    <div *ngIf="!mapboxToken" class="map-empty-state">Λείπει το token του Mapbox.</div>
    <div *ngIf="mapboxToken && emptyMessage() !== null" class="map-empty-state">
      {{ emptyMessage() }}
    </div>
    <!--
      The lower-left corner, above the Mapbox logo: why «Εσείς» is
      missing (spec §4.3) stacked over the marker key (spec §4.4), so
      the two never overlap.
    -->
    <div *ngIf="ownCoordinatesMissing() || showLegend()" class="map-corner">
      <p *ngIf="ownCoordinatesMissing()" class="map-notice map-own-notice" data-testid="own-location-notice">
        Δεν βρέθηκαν συντεταγμένες για το κατάλυμά σας
      </p>
      <div *ngIf="showLegend()" class="map-legend" data-testid="map-legend" role="note" aria-label="Υπόμνημα χάρτη">
        <div class="map-legend-row">
          <span class="map-legend-item"><span class="map-legend-swatch map-legend-low" aria-hidden="true"></span>Χαμηλή τιμή</span>
          <span class="map-legend-item"><span class="map-legend-swatch map-legend-mid" aria-hidden="true"></span>Μεσαία τιμή</span>
          <span class="map-legend-item"><span class="map-legend-swatch map-legend-high" aria-hidden="true"></span>Υψηλή τιμή</span>
        </div>
        <div class="map-legend-row">
          <span class="map-legend-item"><span class="map-legend-swatch map-legend-same" aria-hidden="true"></span>Ίδια κατηγορία</span>
          <span class="map-legend-item"><span class="map-legend-swatch map-legend-similar" aria-hidden="true"></span>Παρόμοιο</span>
          <span class="map-legend-item"><span class="map-legend-swatch map-legend-own" aria-hidden="true"></span>Εσείς</span>
        </div>
      </div>
    </div>
  `,
})
export class CompetitorMapComponent implements AfterViewInit, OnDestroy {
  @ViewChild("mapContainer") mapContainer?: ElementRef<HTMLDivElement>;

  readonly mapboxToken = environment.mapboxToken;
  readonly source = input.required<CompetitorMapSource>();
  /** Why nothing is plotted, or null while there is something to plot. */
  readonly emptyMessage = input<string | null>(null);
  readonly ownCoordinatesMissing = input(false);
  readonly showLegend = input(false);
  /** A competitor marker was clicked (beside Mapbox opening its popup): its hotelKey. */
  readonly markerClick = output<string>();

  // A map that cannot draw is a map failure, never a results one (setMapError).
  // Written from Mapbox callbacks, so a signal.
  readonly mapError = signal("");
  // The radius (km) of the circle the map was really given, or null. Written
  // from the map's "load" callback too, so a signal; rendered as
  // data-radius-km on the map container, since the circle itself is WebGL.
  readonly radiusCircleKm = signal<number | null>(null);
  // True below PRICE_LABEL_MIN_ZOOM. Written from Mapbox's zoomend, outside the
  // zone, so a signal; the template puts it on the map container, where the
  // stylesheet turns the competitor pills into dots.
  readonly mapZoomedOut = signal(false);
  // How many times the camera has been fitted to the content. Written after
  // awaits (render chains), so a signal; rendered as data-fit-count on the map
  // container purely so tests can assert a re-read did NOT move the camera —
  // the fit itself is WebGL and leaves nothing else observable in the DOM.
  readonly mapFitCount = signal(0);

  private mapboxModule: MapboxModule | null = null;
  private map: mapboxgl.Map | null = null;
  // Keyed by hotel (hotelKey). Plain, not a signal: never rendered, and only
  // the imperative marker code reads it.
  private readonly markerEntries = new Map<string, MarkerEntry>();
  // The marker raised while its card is hovered or focused (hotelKey).
  private readonly highlightedMarkerKey = signal<string | null>(null);
  // The «Εσείς» marker and its circle, imperative like the competitor markers:
  // plain fields, never rendered (what the template shows reads the radiusCircleKm
  // signal). `ownMapSignature` is where the last fit saw
  // them (position and radius), so only a real move refits the map.
  // `styleReady` flips in the "load" handler: a source can only be added to a
  // loaded style.
  private ownMarker: mapboxgl.Marker | null = null;
  private ownMarkerPopup: mapboxgl.Popup | null = null;
  private ownPopupHtml = "";
  private ownMapSignature = "";
  private radiusPolygon: Feature<Polygon> | null = null;
  private styleReady = false;
  private resizeObserver: ResizeObserver | null = null;
  private didFitMarkers = false;

  async ngAfterViewInit(): Promise<void> {
    this.clearMarkers();
    await this.initializeMap();
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.clearMarkers();
    this.removeOwnMarker();
    this.map?.remove();
    this.map = null;
  }

  /** A job new to the screen: the next render fits the camera to it again. */
  resetFit(): void {
    this.didFitMarkers = false;
  }

  /** The map's box changed outside a window resize (the filters sidebar opened or closed). */
  resize(): void {
    this.map?.resize();
  }

  /** A card under the pointer or keyboard focus: its hotel's marker is raised and outlined. */
  highlightMarker(key: string): void {
    this.highlightedMarkerKey.set(key);
    this.applyMarkerHighlight();
  }

  /** Only the card that raised the marker lowers it (a late mouseleave must not undo a newer focus). */
  unhighlightMarker(key: string): void {
    if (this.highlightedMarkerKey() === key) {
      this.highlightedMarkerKey.set(null);
      this.applyMarkerHighlight();
    }
  }

  /**
   * A card click eases the map to its hotel's marker, at the current zoom (the
   * user chose it; nothing zooms out from under them). The card is a label, so
   * the browser replays the click on its checkbox and that copy bubbles back
   * here: it is skipped, as is a click on the checkbox itself — ticking stays
   * what it always was.
   */
  easeToMarker(event: Event, key: string): void {
    if (event.target instanceof HTMLInputElement) {
      return;
    }
    const entry = this.markerEntries.get(key);
    if (!this.map || !entry) {
      return;
    }
    this.map.easeTo({ center: [entry.lng, entry.lat], zoom: this.map.getZoom(), duration: 600 });
  }

  /** The markers are imperative: the highlighted one is written onto them here and after every repaint. */
  private applyMarkerHighlight(): void {
    const key = this.highlightedMarkerKey();
    for (const [entryKey, entry] of this.markerEntries) {
      entry.element.classList.toggle("is-highlighted", entryKey === key);
    }
  }

  private async initializeMap(): Promise<void> {
    if (!this.mapboxToken || !this.mapContainer?.nativeElement || this.map) {
      return;
    }
    try {
      const mapboxModule = (await import("mapbox-gl")).default;
      this.mapboxModule = mapboxModule;
      mapboxModule.accessToken = this.mapboxToken;
      this.map = new mapboxModule.Map({
        container: this.mapContainer.nativeElement,
        style: "mapbox://styles/mapbox/navigation-day-v1",
        center: DEFAULT_CENTER,
        zoom: DEFAULT_ZOOM,
        pitch: 0,
        bearing: 0,
      });
      this.map.addControl(new mapboxModule.NavigationControl({ showCompass: true }), "top-right");
      this.map.addControl(new mapboxModule.ScaleControl({ unit: "metric" }), "bottom-right");
      this.map.on("error", (event) => {
        const message = event.error?.message;
        if (message && !this.map?.loaded()) {
          this.setMapError(message);
        }
      });
      this.map.once("load", () => {
        // It loaded after all (a retried style, a late tile server): the
        // notice would be describing a map the user is looking at.
        this.mapError.set("");
        this.map?.resize();
        this.styleReady = true;
        // Reconciled, not rebuilt: markers drawn before the style arrived stay.
        this.renderMarkers();
        // The radius circle is a style source, which only now can be added.
        this.renderOwnProperty();
      });
      this.resizeObserver = new ResizeObserver(() => this.map?.resize());
      this.resizeObserver.observe(this.mapContainer.nativeElement);
      // Prices only where they can be read: every finished zoom (a fit, the
      // controls, the wheel) says whether the pills fit or become dots.
      const syncZoomedOut = () => this.mapZoomedOut.set((this.map?.getZoom() ?? DEFAULT_ZOOM) < PRICE_LABEL_MIN_ZOOM);
      this.map.on("zoomend", syncZoomedOut);
      syncZoomedOut();
      // Results that landed while Mapbox was still being imported: markers need
      // no style, so they go on now rather than wait for "load".
      this.renderMarkers();
      this.renderOwnProperty();
    } catch (error) {
      this.setMapError(error instanceof Error ? error.message : "Δεν ήταν δυνατή η αρχικοποίηση του Mapbox.");
    }
  }

  /**
   * A map that cannot draw is a MAP failure, not a results failure (chip
   * task_8fecd58c).
   *
   * Both call sites used to route through setError, which flips the RESULTS
   * status to "error" and blanks message() — so one aborted style request wiped
   * a perfectly good competitor list, its market snapshot and its empty states,
   * for a purely presentational problem. Results state is never touched here;
   * genuine results errors still go through setError. The raw Mapbox reason
   * stays in the console for support rather than in a Greek sentence for the
   * hotelier, and the notice says the one thing that matters to them.
   */
  private setMapError(reason: string): void {
    console.warn("RoomRate: the Mapbox map failed to load —", reason);
    this.mapError.set(MAP_LOAD_FAILED_MESSAGE);
  }

  /**
   * Bring the competitor markers in line with what the map should show.
   *
   * Reconciled per hotel (hotelKey), never rebuilt (spec §4.5): a marker still
   * wanted keeps its Mapbox Marker and Popup and only has its classes, label
   * and popup content brought up to date, a new hotel gets a marker and a hotel
   * no longer shown loses its own. Every repaint used to drop and recreate every
   * marker, so ticking a card, flipping the map filter or re-reading the job
   * closed the popup the user was reading. clearMarkers() is for teardown and a
   * fresh start only.
   */
  renderMarkers(): void {
    const map = this.map;
    const mapboxModule = this.mapboxModule;
    if (!map || !mapboxModule) {
      return;
    }
    map.resize();
    // One marker per hotel: two packages of one hotel sit on the same spot,
    // where only the top marker could ever be clicked. The first row (the
    // API's order) speaks for the hotel, which counts as selected when any of
    // its rows is ticked. Already coordinate-filtered by the computed itself.
    const selectedKeys = this.source().selectedKeys();
    const wanted = new Map<string, { competitor: CompetitorMapMarker; isSelected: boolean }>();
    for (const competitor of this.source().visibleCompetitors()) {
      const key = hotelKey(competitor);
      const isSelected = selectedKeys.has(markerKey(competitor));
      const existing = wanted.get(key);
      if (existing) {
        existing.isSelected = existing.isSelected || isSelected;
      } else {
        wanted.set(key, { competitor, isSelected });
      }
    }

    for (const [key, entry] of this.markerEntries) {
      if (!wanted.has(key)) {
        entry.marker.remove();
        this.markerEntries.delete(key);
      }
    }
    // The colour bands of THIS set of markers: the map filter or a re-read
    // moves them with what is drawn.
    const tertiles = priceTertiles(Array.from(wanted.values(), ({ competitor }) => competitor.price_per_night_eur));
    for (const [key, { competitor, isSelected }] of wanted) {
      let entry = this.markerEntries.get(key);
      if (!entry) {
        entry = this.createMarkerEntry(map, mapboxModule, competitor);
        this.markerEntries.set(key, entry);
      }
      this.updateMarkerEntry(entry, competitor, isSelected, tertiles);
    }
    // A marker drawn while its card is hovered comes up already raised.
    this.applyMarkerHighlight();
    this.fitMapToContent();
  }

  private createMarkerEntry(map: mapboxgl.Map, mapboxModule: MapboxModule, competitor: CompetitorMapMarker): MarkerEntry {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "roomrate-marker-dot";
    const pricePill = document.createElement("span");
    pricePill.className = "price-pill";
    element.appendChild(pricePill);
    const popup = new mapboxModule.Popup({
      closeButton: true,
      closeOnClick: true,
      maxWidth: "280px",
      // Clears the price pill, which is taller than the old 16 px dot.
      offset: 18,
    });
    const marker = new mapboxModule.Marker({ element, anchor: "center" })
      .setLngLat([competitor.longitude, competitor.latitude])
      .setPopup(popup)
      .addTo(map);
    // Beside Mapbox's own click (which opens the popup): point the list at the
    // hotel. The key is fixed for the entry's life, the map key it lives under.
    const key = hotelKey(competitor);
    element.addEventListener("click", () => this.markerClick.emit(key));
    return { marker, element, pricePill, popup, popupHtml: "", lng: competitor.longitude, lat: competitor.latitude };
  }

  /**
   * Write one hotel's current state onto its marker, in place.
   *
   * classList, never className: Mapbox put its own classes on the element
   * (`mapboxgl-marker`, the anchor), and overwriting them unpositions it.
   */
  private updateMarkerEntry(
    entry: MarkerEntry,
    competitor: CompetitorMapMarker,
    isSelected: boolean,
    tertiles: PriceTertiles | null,
  ): void {
    const { element, pricePill } = entry;
    const price = formatEuro(competitor.price_per_night_eur);
    // The price pill (spec §4.4): the number is readable without opening anything.
    if (pricePill.textContent !== price) {
      pricePill.textContent = price;
    }
    element.classList.remove(...PRICE_BAND_CLASSES);
    element.classList.add(this.priceClass(competitor.price_per_night_eur, tertiles));
    // Outlined for a room of another category, filled for the owner's own.
    // Unknown (an older API) stays filled: nothing says it is not comparable.
    element.classList.toggle("is-similar", competitor.category_match === "similar");
    element.classList.toggle("is-selected", isSelected);
    // The ring drawn by `is-selected` is styling; this attribute is the state
    // itself, so tests (and the filter above) read it rather than a class.
    element.dataset["selected"] = String(isSelected);
    // A ring or an outline is invisible to a screen reader, so the label
    // carries the same facts in words (the 4.1 a11y gap, closed in 5.1 now the
    // page speaks Greek throughout). The hotel name stays FIRST:
    // map-auto-plot.spec.ts finds a marker by `[aria-label*="<hotel name>"]`,
    // and so does anyone scanning the labels by ear.
    const category = categoryLabel(competitor.category_match);
    element.setAttribute(
      "aria-label",
      `${competitor.hotel_name}, ${price} ανά βράδυ`
        + (category ? `, ${category.toLocaleLowerCase("el-GR")}` : "")
        + (isSelected ? ", επιλεγμένο για παρακολούθηση" : ""),
    );
    if (entry.lng !== competitor.longitude || entry.lat !== competitor.latitude) {
      entry.marker.setLngLat([competitor.longitude, competitor.latitude]);
      entry.lng = competitor.longitude;
      entry.lat = competitor.latitude;
    }
    const popupHtml = this.buildPopupHtml(competitor);
    if (popupHtml !== entry.popupHtml) {
      entry.popup.setHTML(popupHtml);
      entry.popupHtml = popupHtml;
    }
  }

  /**
   * Fit the map to what it shows, once per load (didFitMarkers): the
   * competitor markers, «Εσείς» and the whole radius circle (spec §4.3).
   */
  private fitMapToContent(): void {
    if (!this.map || !this.mapboxModule || this.didFitMarkers) {
      return;
    }
    const bounds = new this.mapboxModule.LngLatBounds();
    for (const entry of this.markerEntries.values()) {
      bounds.extend([entry.lng, entry.lat]);
    }
    if (this.ownMarker) {
      bounds.extend(this.ownMarker.getLngLat());
    }
    for (const [lng, lat] of this.radiusPolygon?.geometry.coordinates[0] ?? []) {
      bounds.extend([lng, lat]);
    }
    if (bounds.isEmpty()) {
      return;
    }
    this.didFitMarkers = true;
    this.mapFitCount.update((count) => count + 1);
    this.map.fitBounds(bounds, {
      padding: { top: 80, right: 80, bottom: 90, left: 80 },
      maxZoom: 14,
      pitch: 0,
      bearing: 0,
      duration: 700,
    });
  }

  /**
   * Put «Εσείς» and its radius circle on the map as ownProperty() stands, or
   * take them off. Imperative like renderMarkers, and reconciled the same way:
   * an existing marker is moved and updated, so its open popup survives.
   */
  renderOwnProperty(): void {
    const own = this.source().ownProperty();
    const location = this.source().ownLocation();
    if (!own || !location) {
      this.removeOwnMarker();
      this.setRadiusCircle(null);
      return;
    }
    const map = this.map;
    const mapboxModule = this.mapboxModule;
    if (!map || !mapboxModule) {
      return;
    }
    const lngLat: [number, number] = [location.lng, location.lat];
    if (!this.ownMarker) {
      const element = document.createElement("button");
      element.type = "button";
      element.className = "roomrate-marker-own";
      element.textContent = "Εσείς";
      this.ownMarkerPopup = new mapboxModule.Popup({ closeButton: true, closeOnClick: true, maxWidth: "280px", offset: 18 });
      this.ownPopupHtml = "";
      this.ownMarker = new mapboxModule.Marker({ element, anchor: "center" })
        .setLngLat(lngLat)
        .setPopup(this.ownMarkerPopup)
        .addTo(map);
    } else {
      const current = this.ownMarker.getLngLat();
      if (current.lng !== location.lng || current.lat !== location.lat) {
        this.ownMarker.setLngLat(lngLat);
      }
    }
    this.ownMarker.getElement().setAttribute("aria-label", `Εσείς: ${own.display_name}`);
    const popupHtml = this.buildOwnPopupHtml(own);
    if (popupHtml !== this.ownPopupHtml) {
      this.ownMarkerPopup?.setHTML(popupHtml);
      this.ownPopupHtml = popupHtml;
    }

    const radiusKm = typeof own.radius_km === "number" && own.radius_km > 0 ? own.radius_km : null;
    this.setRadiusCircle(radiusKm === null ? null : circlePolygon(location.lng, location.lat, radiusKm), radiusKm);
    // A hotel that moved or a radius that changed is new ground to show;
    // the same answer read again is not, and must not yank the map back.
    const signature = `${location.lng},${location.lat},${radiusKm ?? ""}`;
    if (signature !== this.ownMapSignature) {
      this.ownMapSignature = signature;
      this.didFitMarkers = false;
    }
    this.fitMapToContent();
  }

  private buildOwnPopupHtml(own: OwnPropertyMapInfo): string {
    const price = typeof own.price_per_night_eur === "number"
      ? `
        <p class="roomrate-popup__own-line">Η τιμή σας στο Booking: <strong>${formatEuro(own.price_per_night_eur)}</strong></p>`
      : "";
    const room = own.room_type ? `
        <p class="roomrate-popup__room">${this.escapeHtml(own.room_type)}</p>` : "";
    const radius = typeof own.radius_km === "number" && own.radius_km > 0
      ? `
        <p class="roomrate-popup__own-line">Ακτίνα ${formatRadius(own.radius_km)}</p>`
      : "";
    return `
      <div class="roomrate-popup roomrate-popup--own">
        <div class="roomrate-popup__eyebrow">Εσείς</div>
        <h3>${this.escapeHtml(own.display_name)}</h3>${price}${room}${radius}
      </div>
    `;
  }

  private removeOwnMarker(): void {
    this.ownMarker?.remove();
    this.ownMarker = null;
    this.ownMarkerPopup = null;
    this.ownPopupHtml = "";
    this.ownMapSignature = "";
  }

  /**
   * Hand the radius circle to the map, or take it off (spec §4.3).
   *
   * Kept even before the style has loaded — the "load" handler adds it then —
   * so fitMapToContent can already take the whole circle into account.
   */
  private setRadiusCircle(polygon: Feature<Polygon> | null, radiusKm: number | null = null): void {
    this.radiusPolygon = polygon;
    const map = this.map;
    if (!map || !this.styleReady) {
      return;
    }
    const source = map.getSource(RADIUS_SOURCE_ID) as mapboxgl.GeoJSONSource | undefined;
    if (!polygon) {
      for (const layerId of [RADIUS_LINE_LAYER_ID, RADIUS_FILL_LAYER_ID]) {
        if (map.getLayer(layerId)) {
          map.removeLayer(layerId);
        }
      }
      if (source) {
        map.removeSource(RADIUS_SOURCE_ID);
      }
      this.radiusCircleKm.set(null);
      return;
    }
    if (source) {
      source.setData(polygon);
    } else {
      map.addSource(RADIUS_SOURCE_ID, { type: "geojson", data: polygon });
      map.addLayer({
        id: RADIUS_FILL_LAYER_ID,
        type: "fill",
        source: RADIUS_SOURCE_ID,
        paint: { "fill-color": "#0f172a", "fill-opacity": 0.05 },
      });
      map.addLayer({
        id: RADIUS_LINE_LAYER_ID,
        type: "line",
        source: RADIUS_SOURCE_ID,
        paint: { "line-color": "#0f172a", "line-opacity": 0.55, "line-width": 1.5, "line-dasharray": [2, 2] },
      });
    }
    this.radiusCircleKm.set(radiusKm);
  }

  clearMarkers(): void {
    for (const entry of this.markerEntries.values()) {
      entry.marker.remove();
    }
    this.markerEntries.clear();
    const container = this.map?.getContainer() || this.mapContainer?.nativeElement;
    if (!container) {
      return;
    }
    container.querySelectorAll(".roomrate-marker-dot").forEach((element) => {
      (element.closest(".mapboxgl-marker") || element).remove();
    });
    // Competitor popups only: «Εσείς» is not a search result and outlives a new search.
    container.querySelectorAll(".roomrate-popup:not(.roomrate-popup--own)").forEach((element) => {
      element.closest(".mapboxgl-popup")?.remove();
    });
  }

  private priceClass(price: number, tertiles: PriceTertiles | null): string {
    if (!tertiles) {
      return "roomrate-marker-dot-mid";
    }
    if (price <= tertiles.low) {
      return "roomrate-marker-dot-low";
    }
    if (price <= tertiles.high) {
      return "roomrate-marker-dot-mid";
    }
    return "roomrate-marker-dot-high";
  }

  private buildPopupHtml(marker: CompetitorMapMarker): string {
    const propertyType = this.formatPropertyType(marker.property_type || "Κατάλυμα");
    const roomType = marker.room_type || marker.room_type_category || "Δωμάτιο";
    const reviewLabel = marker.review_count === 1 ? "κριτική" : "κριτικές";
    const roomsLeftLabel = marker.rooms_left === 1 ? "δωμάτιο" : "δωμάτια";
    const matchScore = this.source().popupMatchScore(marker.hotel_name);
    const matchRow = matchScore === null ? "" : `
          <div>
            <dt>Ταίριασμα δωματίου</dt>
            <dd>${Math.round(matchScore)}%</dd>
          </div>`;
    // Round 6 (spec §4.5): each piece only when the API sent it — an older API
    // or an owner without coordinates leaves the line out, never shows «— km».
    const category = categoryLabel(marker.category_match);
    const distance = formatDistance(marker.distance_km);
    const categoryTag = category
      ? `<span class="roomrate-popup__category${marker.category_match === "similar" ? " is-similar" : ""}">${this.escapeHtml(category)}</span>`
      : "";
    const distanceTag = distance ? `<span class="roomrate-popup__distance">Απόσταση ${this.escapeHtml(distance)}</span>` : "";
    const tags = categoryTag || distanceTag ? `
        <div class="roomrate-popup__tags">${categoryTag}${distanceTag}</div>` : "";
    const bookingUrl = this.safeBookingUrl(marker.booking_url);
    const bookingLink = bookingUrl ? `
        <div class="roomrate-popup__footer">
          <a class="roomrate-popup__link" href="${this.escapeHtml(bookingUrl)}" target="_blank" rel="noopener">Άνοιγμα στο Booking</a>
        </div>` : "";

    return `
      <div class="roomrate-popup">
        <div class="roomrate-popup__eyebrow">${this.escapeHtml(propertyType)}</div>
        <h3>${this.escapeHtml(marker.hotel_name)}</h3>${tags}
        <div class="roomrate-popup__price">${formatEuro(marker.price_per_night_eur)} <span>ανά βράδυ</span></div>
        <div class="roomrate-popup__section">
          <span class="roomrate-popup__label">Τύπος δωματίου</span>
          <p class="roomrate-popup__room">${this.escapeHtml(roomType)}</p>
        </div>
        <dl class="roomrate-popup__metrics">
          <div>
            <dt>Βαθμολογία</dt>
            <dd>${marker.review_score.toFixed(1)} <span>(${marker.review_count} ${reviewLabel})</span></dd>
          </div>
          <div>
            <dt>Διαθέσιμα δωμάτια</dt>
            <dd>${marker.rooms_left} <span>${roomsLeftLabel}</span></dd>
          </div>${matchRow}
        </dl>${bookingLink}
      </div>
    `;
  }

  /**
   * The listing's Booking URL, or "" for anything that is not http(s).
   *
   * The API already refuses other schemes; checked again here because the
   * value lands in an href, where a `javascript:` URL would run on click.
   */
  private safeBookingUrl(url: string | null | undefined): string {
    return typeof url === "string" && /^https?:\/\//i.test(url.trim()) ? url.trim() : "";
  }

  private formatPropertyType(value: string): string {
    const normalized = value.replaceAll("_", " ").trim();
    if (!normalized) {
      return "Κατάλυμα";
    }
    return normalized
      .split(" ")
      .filter(Boolean)
      .map((word) => `${word.charAt(0).toUpperCase()}${word.slice(1).toLowerCase()}`)
      .join(" ");
  }

  private escapeHtml(value: string): string {
    return value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }
}
