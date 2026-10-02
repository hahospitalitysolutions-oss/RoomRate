import { CommonModule } from "@angular/common";
import { AfterViewInit, Component, computed, ElementRef, inject, OnDestroy, OnInit, signal, ViewChild } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Router, RouterLink } from "@angular/router";
import type { Feature, Polygon } from "geojson";
import type mapboxgl from "mapbox-gl";

import { environment } from "../../environments/environment";
import { EmptyStateComponent } from "../components/empty-state.component";
import { NotificationBellComponent } from "../components/notification-bell.component";
import { NotificationToastsComponent } from "../components/notification-toasts.component";
import { SetupChecklistComponent } from "../onboarding/setup-checklist.component";
import { ApiClientService } from "../services/api-client.service";
import { AuthService } from "../services/auth.service";
import { NotificationsService } from "../services/notifications.service";
import { OnboardingService } from "../services/onboarding.service";
import { SetupProgressService } from "../services/setup-progress.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";
import {
  CategoryMatch,
  Competitor,
  CompetitorMapMarker,
  CompetitorPackage,
  CompetitorSort,
  MarketSummary,
  MatchSource,
  NearbyDestinationsResponse,
  OwnedPropertyRoomType,
  OwnPropertyMapInfo,
  RoomMatchRunRequest,
  RoomMatchRunResponse,
  ScrapeJobProgress,
  ScrapeJobResponse,
  ScrapeResultSummary,
  TrackedCompetitorListResponse,
} from "../types/market";
import { StayDates, defaultStayDates, ensureFutureStay, isPastLocalDate } from "../utils/date-defaults";
import { areComparableRoomCategories } from "../utils/room-category";

type LoadingState = "idle" | "loading" | "ready" | "error";
// What the badge over the match list can claim (spec Α.4): the two wire
// sources, plus the honest in-between when one read mixes them — some rows
// scored by the agent, the rest (older rows, missing field) statistically.
type MatchSourceState = MatchSource | "partial";
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
// `radiusKm`: the radius the job ran with (null before Round 6), for the
// sentence that explains a radius that kept no hotel.
type JobScope = StayDates & { roomTypeCategory: string; radiusKm: number | null };
// Why a finished search is showing nothing, in the order of the user's own
// ability to act on it. "amenities" carries no count on purpose: it is the one
// reason that is NOT read out of the scrape's result summary (see
// amenityFilterHidesEverything), so there is no honest number to put in it.
// "single_rooms" and "capacity" are the scraper's first two stages since Round
// 6 (spec §3.4), which replaced its room-category cut before storage.
// "comparable" is the other read filter and carries no count for the same
// reason as "amenities".
type EmptyResultReason =
  | { kind: "amenities" }
  | { kind: "comparable" }
  | { kind: "single_rooms"; count: number }
  | { kind: "capacity"; count: number }
  | { kind: "remaining-filters"; count: number };

// One ROOM of a matched hotel: its packages are that room's rate plans (spec
// §5). `rangeLabel` carries the collapsed «από … έως …» line and doubles as
// the switch between the two renderings: null (a single package, or old rows
// without rate-plan data) keeps today's plain package rows.
type RoomPlanGroup = {
  room_type: string;
  packages: CompetitorPackage[];
  rangeLabel: string | null;
};

type MapFilters = {
  destination: string;
  check_in: string;
  check_out: string;
  adults: string;
  children: string;
  rooms: string;
  limit: string;
  radius_km: string;
};

// Spec §4.1 bounds for the two Round 6 search fields. The server enforces its
// own (8 areas, 0.5-50 km); the form stays inside them so it never earns a 422.
const MAX_NEARBY_DESTINATIONS = 8;
const DEFAULT_RADIUS_KM = 10;
const MIN_RADIUS_KM = 1;
const MAX_RADIUS_KM = 30;

// A Round 6 scrape (scouts of several areas, up to 120 hotels) runs far longer
// than the old 10 minutes — a live Faliraki search with four nearby areas took
// 10-11 — and Apify can be slower on the day. So the page waits as long as the
// backend's own hard timeout (SCRAPE_JOB_TIMEOUT_SECONDS = 1800): 360 polls of
// 5 s = 30 minutes. It used to give up at 15 on a job that was still running.
const SCRAPE_POLL_ATTEMPTS = 360;
const SCRAPE_POLL_INTERVAL_MS = 5000;
// A scrape keeps running server-side whether or not one status read reaches
// the page, so a lone 502 or network blip must not end the wait. Only this
// many failed reads IN A ROW mean the server is really gone.
const SCRAPE_POLL_FAILURE_LIMIT = 5;
// Rows asked for by the marker and summary reads of a job: the whole job (the
// server caps a Round 6 scrape at 120 hotels), so the map, the header count and
// the summary box all describe the same set (spec §4.7).
const JOB_RESULT_READ_LIMIT = "1000";

// The map filter's name, defined once and rendered as the control's own label:
// every message that sends the user to that control has to call it exactly
// what the control is called, or the instruction points at nothing.
const ONLY_SELECTED_FILTER_NAME = "Μόνο τα επιλεγμένα στον χάρτη";
// The single way out of a map the filter has emptied, shared by both messages
// that leave the user looking at one.
const UNHIDE_INSTRUCTION = "Τσεκάρετε δωμάτια ή απενεργοποιήστε το φίλτρο.";

// Two of the four banners a search that DID return rooms can leave on screen
// (the others are SEARCH_AMENITY_FILTERED_MESSAGE and
// SEARCH_COMPARABLE_FILTERED_MESSAGE below, which need wording defined
// further down). They are module constants because
// refreshSearchResultMessage has to recognise this component's own banner to
// keep it true as the filters change under it; matching against the very
// constants the writer uses is what stops the two sides from drifting apart.
const SEARCH_PLOTTED_MESSAGE =
  "Όλα τα αποτελέσματα εμφανίζονται στον χάρτη. Τσεκάρετε όσα θέλετε να παρακολουθείτε.";
const SEARCH_FILTERED_MESSAGE =
  `Η αναζήτηση επέστρεψε αποτελέσματα, αλλά το φίλτρο «${ONLY_SELECTED_FILTER_NAME}» κρατά στον `
  + `χάρτη μόνο όσα δωμάτια έχετε τσεκάρει. ${UNHIDE_INSTRUCTION}`;
// The map overlay's version of the same state, reached from the other side:
// the user is looking at the map itself rather than at a search banner.
const FILTER_HIDES_ALL_MESSAGE =
  `Όλα τα αποτελέσματα είναι κρυμμένα από το φίλτρο «${ONLY_SELECTED_FILTER_NAME}». ${UNHIDE_INSTRUCTION}`;

// The OTHER user-caused empty screen, and a different one from the map filter
// above: the facility checkboxes are applied by the backend when the job's rows
// are READ, so the rows exist and every one of them was dropped for lacking a
// ticked facility. Said in three places at once — the sidebar's title, its
// explanation and the map overlay — so they are built from shared parts rather
// than written out three times and left to drift.
const AMENITY_FILTER_HIDES_ALL_TITLE = "Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα";
const AMENITY_UNHIDE_INSTRUCTION = "Αφαιρέστε κάποιο από αυτά για να δείτε ξανά τα δωμάτια.";
const AMENITY_FILTER_HIDES_ALL_EXPLANATION =
  "Η αναζήτηση επέστρεψε δωμάτια, αλλά κανένα δεν διαθέτει όλες τις παροχές που έχετε επιλέξει. "
  + AMENITY_UNHIDE_INSTRUCTION;
const AMENITY_FILTER_HIDES_ALL_MESSAGE = `${AMENITY_FILTER_HIDES_ALL_TITLE}. ${AMENITY_UNHIDE_INSTRUCTION}`;
// The third search-result banner (see SEARCH_PLOTTED_MESSAGE): the search
// returned rooms and the facility filter is keeping every one of them off the
// map. Its own sentence rather than SEARCH_FILTERED_MESSAGE's, because that
// one names the map filter — telling a user to untick cards or switch off a
// filter they never touched points them at the wrong control.
const SEARCH_AMENITY_FILTERED_MESSAGE =
  "Η αναζήτηση επέστρεψε αποτελέσματα, αλλά τα φίλτρα παροχών δεν αφήνουν κανένα στον χάρτη. "
  + AMENITY_UNHIDE_INSTRUCTION;
// The way out, offered where the message is read: the filters sidebar starts
// collapsed, so the checkboxes that caused this are not necessarily on screen.
const CLEAR_AMENITY_FILTERS_LABEL = "Καθαρισμός φίλτρων παροχών";
// A facility re-read that failed without an error message of its own.
const AMENITY_FILTER_FAILED_MESSAGE = "Δεν ήταν δυνατή η εφαρμογή των φίλτρων παροχών.";

// Spec Α.5: a failed agent run (error, skipped, quota) is never an error of
// the match list itself — the statistical scores on screen stay and this one
// sentence says why the AI ones did not arrive.
const AGENT_UNAVAILABLE_NOTICE = "Η εκτίμηση AI δεν είναι διαθέσιμη· εμφανίζεται η στατιστική.";

// Shown while the page runs the room-matching agent BY ITSELF (owner decision
// 2026-09-30): a completed job whose competitors carry no agent verdict for
// the selected room gets one automatic run per (job, room) per visit.
const AUTO_MATCH_PENDING_MESSAGE = "Εκτίμηση AI για το δωμάτιο σε εξέλιξη…";

// The same three places for the other read filter, «Μόνο συγκρίσιμα» (owner
// decision 2026-09-30: the comparison set is the AI agent's verdict, and the
// rooms it judged non-comparable are hidden by default), built from shared
// parts for the same reason as the facility texts above. Its name is
// rendered as the switch's own label.
//
// The title stays «Δεν βρέθηκαν συγκρίσιμα δωμάτια» on purpose: the filter is
// ON by default, so an empty comparable read is not something the user did —
// the sentence is simply true, while "the switch hides everything" would be a
// guess (rows without coordinates also leave a job's read empty).
const COMPARABLE_FILTER_NAME = "Μόνο συγκρίσιμα";
const COMPARABLE_HIDES_ALL_TITLE = "Δεν βρέθηκαν συγκρίσιμα δωμάτια";
const COMPARABLE_UNHIDE_INSTRUCTION = `Απενεργοποιήστε το «${COMPARABLE_FILTER_NAME}» για να δείτε όλα τα καταλύματα που βρέθηκαν.`;
const COMPARABLE_HIDES_ALL_EXPLANATION =
  "Η αναζήτηση επέστρεψε καταλύματα, αλλά κανένα δεν κρίθηκε συγκρίσιμο με το δωμάτιό σας. "
  + COMPARABLE_UNHIDE_INSTRUCTION;
const COMPARABLE_HIDES_ALL_MESSAGE = `Κανένα συγκρίσιμο δωμάτιο στον χάρτη. ${COMPARABLE_UNHIDE_INSTRUCTION}`;
const SEARCH_COMPARABLE_FILTERED_MESSAGE =
  "Η αναζήτηση επέστρεψε αποτελέσματα, αλλά κανένα δεν κρίθηκε συγκρίσιμο με το δωμάτιό σας. "
  + COMPARABLE_UNHIDE_INSTRUCTION;
const SHOW_ALL_ROOMS_LABEL = "Εμφάνιση όλων";

// A map that cannot draw says so where the map is, and says the one thing the
// user needs to know about the rest of the screen: the numbers still hold.
const MAP_LOAD_FAILED_MESSAGE =
  "Ο χάρτης δεν μπόρεσε να φορτώσει. Τα αποτελέσματα δεν επηρεάζονται.";
// The "nothing is running, here is the way out" banner. Written from three
// places (the initial signal, the restore's catch and the per-navigation
// reset), so it is a constant: three copies of one sentence is three chances
// for them to drift, and the e2e negatives hang off this exact wording.
// It names the button by its label, so the two must be translated together.
const START_SEARCH_MESSAGE =
  "Ρυθμίστε τα φίλτρα και πατήστε «Εύρεση ανταγωνιστών» για να ξεκινήσει ζωντανή αναζήτηση.";
// Shown when ensureSelectedRoom picked the room instead of the user. Rendered
// in the results sidebar, which is the only part of the page that is up
// unconditionally — the filters sidebar starts collapsed.
const AUTO_PICKED_ROOM_HINT =
  "Επιλέχθηκε αυτόματα το πρώτο δωμάτιο του καταλόγου — αλλάξτε το αν χρειάζεται.";

const DEFAULT_CENTER: [number, number] = [28.199, 36.3396];
const DEFAULT_ZOOM = 14;
// Below this zoom a dense market's price pills pile up (40 hotels inside
// 1.2 km of Faliraki centre): the markers drop their text and become dots.
const PRICE_LABEL_MIN_ZOOM = 13.5;
// How long a card stays highlighted after its marker was clicked.
const CARD_HIGHLIGHT_MS = 1500;
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

const DEFAULT_STAY = defaultStayDates();
// A facility filter is two different things at once, and 5.1 split them
// apart: `value` is the Booking facility name the backend matches rows on and
// the one thing `amenities=` may ever carry, `label` is what the hotelier
// reads. They used to be the same English string, so translating the checkbox
// text would have silently returned zero rooms instead of failing loudly.
// Never send a label; never render a value.
type AmenityOption = { value: string; label: string };

const COMMON_AMENITIES: AmenityOption[] = [
  { value: "Free WiFi", label: "Δωρεάν WiFi" },
  { value: "Free parking", label: "Δωρεάν στάθμευση" },
  { value: "Swimming pool", label: "Πισίνα" },
  { value: "Non-smoking rooms", label: "Δωμάτια για μη καπνίζοντες" },
  { value: "Restaurant", label: "Εστιατόριο" },
  { value: "Family rooms", label: "Οικογενειακά δωμάτια" },
  { value: "Tea/coffee maker", label: "Βραστήρας για τσάι και καφέ" },
  { value: "Bar", label: "Μπαρ" },
  { value: "Breakfast", label: "Πρωινό" },
  { value: "Balcony", label: "Μπαλκόνι" },
  { value: "Sea view", label: "Θέα στη θάλασσα" },
  { value: "Air conditioning", label: "Κλιματισμός" },
];
const AMENITY_BY_VALUE = new Map(COMMON_AMENITIES.map((option) => [option.value, option]));
// Keyed by the same `value`, so a matcher can never invent a facility the
// option list does not carry. `terms` are the (already bilingual) tokens the
// scraped facility text is searched for — that is the right layer for Greek.
const FACILITY_MATCHERS = [
  { value: "Free WiFi", terms: ["wifi", "wi-fi", "internet"] },
  { value: "Free parking", terms: ["parking", "στάθμευση", "πάρκινγκ"] },
  { value: "Swimming pool", terms: ["pool", "swimming", "πισίνα", "πισίνες"] },
  { value: "Non-smoking rooms", terms: ["non-smoking", "non smoking", "μη καπνιστών"] },
  { value: "Restaurant", terms: ["restaurant", "εστιατόριο"] },
  { value: "Family rooms", terms: ["family", "οικογενειακά"] },
  { value: "Tea/coffee maker", terms: ["tea", "coffee", "καφέ", "τσάι", "καφετιέρα"] },
  { value: "Bar", terms: ["bar", "μπαρ"] },
  { value: "Breakfast", terms: ["breakfast", "πρωινό"] },
  { value: "Balcony", terms: ["balcony", "μπαλκόνι"] },
  { value: "Sea view", terms: ["sea view", "θέα στη θάλασσα"] },
  { value: "Air conditioning", terms: ["air conditioning", "κλιματισμός"] },
];

// All async-written state is signal-based. Every API call in this component
// awaits getAccessToken() first, and supabase-js getSession() acquires a
// navigator.locks lock that zone.js cannot patch — so the continuation after
// EVERY await runs outside the Angular zone. Plain-field mutations there were
// invisible to change detection (data only appeared after an unrelated
// click); signal writes schedule change detection regardless of zone. State
// written only by synchronous user handlers (matchRoomEnabled, minMatchScore,
// matchSort) stays plain — the triggering click already runs in the zone.
@Component({
  selector: "app-map-page",
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, EmptyStateComponent, NotificationBellComponent, NotificationToastsComponent, SetupChecklistComponent],
  template: `
    <main class="map-page">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate — Χάρτης αγοράς</p>
          <h1>{{ propertyName() || filters().destination || "Στοχευμένη ανάλυση δωματίων ανταγωνιστών" }}</h1>
        </div>
        <!--
          Status first, then navigation: the two chips only report (spans with
          a pill look, nothing to press), the links and «Αποσύνδεση» read as
          navigation. The row wraps instead of pushing the page sideways on a
          narrow window.
        -->
        <div class="header-actions map-header-actions">
          <span class="status-chip" data-testid="header-count-chip">{{ status() === "loading" ? "Φόρτωση" : competitorCountLabel() }}</span>
          <span class="status-chip status-chip-live" data-testid="header-live-chip">Ζωντανά δεδομένα</span>
          <nav class="header-nav" aria-label="Πλοήγηση">
            <a class="header-nav-link" routerLink="/pricing">Τιμολόγηση</a>
            <a class="header-nav-link" routerLink="/settings">Ρυθμίσεις</a>
          </nav>
          <app-notification-bell />
          <button class="header-nav-link header-signout" type="button" (click)="signOut()">Αποσύνδεση</button>
        </div>
      </header>

      <section class="map-grid" [class.filters-collapsed]="filtersCollapsed()">
        <aside *ngIf="!filtersCollapsed()" class="filters-sidebar">
          <div class="filters-title-row">
            <h2>Φίλτρα αναζήτησης</h2>
            <button class="panel-toggle-button" type="button" (click)="toggleFiltersSidebar()">Απόκρυψη</button>
          </div>
          <!-- The room itself is chosen at the top of the results column («Σύγκριση για»); a new search runs for it. -->
          <div *ngIf="selectedOwnedRoom()" class="baseline-room">
            <strong>{{ selectedOwnedRoom()?.room_type }}</strong>
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
              <input class="roomrate-input" type="date" [ngModel]="filters().check_in" (ngModelChange)="setFilter('check_in', $event)">
            </label>
            <label>
              <span>Αναχώρηση</span>
              <input class="roomrate-input" type="date" [ngModel]="filters().check_out" (ngModelChange)="setFilter('check_out', $event)">
            </label>
          </div>
          <label>
            <span>Πλήθος ανταγωνιστών</span>
            <input class="roomrate-input" min="1" max="80" type="number" data-testid="limit-input" [ngModel]="filters().limit" (ngModelChange)="setFilter('limit', $event)">
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
                <button type="button" [attr.aria-label]="'Αφαίρεση περιοχής ' + area" (click)="removeNearbyDestination(area)">×</button>
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
                (ngModelChange)="nearbyDraft.set($event)"
                (keydown.enter)="addNearbyDestination()"
              >
              <button class="panel-toggle-button" type="button" [disabled]="!canAddNearbyDestination()" (click)="addNearbyDestination()">Προσθήκη</button>
            </div>
            <small class="nearby-hint">Έως {{ maxNearbyDestinations }} περιοχές εκτός από την κύρια.</small>
          </div>
          <label>
            <span>Ακτίνα (km)</span>
            <input class="roomrate-input" min="1" max="30" step="1" type="number" data-testid="radius-input" [ngModel]="filters().radius_km" (ngModelChange)="setFilter('radius_km', $event)">
          </label>

          <div class="amenity-list">
            <div class="amenity-list-header">
              <span>Δημοφιλέστερες παροχές</span>
              <button type="button" [disabled]="!selectedAmenities().size" (click)="clearAmenities()">Καθαρισμός</button>
            </div>
            <label *ngFor="let amenity of amenities()" class="checkbox-row">
              <input type="checkbox" [checked]="selectedAmenities().has(amenity.value)" (change)="toggleAmenity(amenity.value)">
              <span>{{ amenity.label }}</span>
            </label>
          </div>

          <button class="primary-button" type="button" [disabled]="actionStatus() === 'loading'" (click)="findCompetitors()">
            {{ actionStatus() === "loading" ? "Γίνεται αναζήτηση" : "Εύρεση ανταγωνιστών" }}
          </button>
          <div *ngIf="message()" class="alert">{{ message() }}</div>
          <!-- What the scrape itself reported about this result (spec §3.1/§3.3), under the banner that describes it. -->
          <p *ngFor="let warning of searchWarnings()" class="search-warning" data-testid="search-warning">{{ warning }}</p>
          <!-- Live progress of the search this page started (spec §4.2), under the banner. -->
          <div #scrapeProgressBlock *ngIf="scrapeInFlight()" class="scrape-progress" data-testid="scrape-progress">
            <span role="status" data-testid="scrape-progress-text">{{ scrapeProgressLabel() }}</span>
            <span *ngIf="scrapeElapsedLabel()" class="scrape-progress-clock" data-testid="scrape-elapsed">{{ scrapeElapsedLabel() }}</span>
          </div>
          <div *ngIf="error()" class="alert alert-error">{{ error() }}</div>
        </aside>

        <section class="map-shell">
          <app-setup-checklist></app-setup-checklist>
          <div class="map-canvas">
            <button *ngIf="filtersCollapsed()" class="show-filters-button" type="button" (click)="toggleFiltersSidebar()">
              Φίλτρα
            </button>
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
            <div *ngIf="mapboxToken && !visibleCompetitors().length" class="map-empty-state">
              {{ mapEmptyMessage() }}
            </div>
            <!--
              The lower-left corner, above the Mapbox logo: why «Εσείς» is
              missing (spec §4.3) stacked over the marker key (spec §4.4), so
              the two never overlap.
            -->
            <div *ngIf="ownCoordinatesMissing() || showMapLegend()" class="map-corner">
              <p *ngIf="ownCoordinatesMissing()" class="map-notice map-own-notice" data-testid="own-location-notice">
                Δεν βρέθηκαν συντεταγμένες για το κατάλυμά σας
              </p>
              <div *ngIf="showMapLegend()" class="map-legend" data-testid="map-legend" role="note" aria-label="Υπόμνημα χάρτη">
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
          </div>
        </section>

        <aside class="results-sidebar">
          <!--
            The room the whole column is read for, as the column's first
            control (owner feedback 2026-09-30): the owner compares per room
            type, so the choice cannot live in the «Φίλτρα» form, which starts
            collapsed. Same signal and handler as ever: a switch re-reads the
            markers, the summary and the matches with the room's id, and runs
            the matching agent by itself when the job has no verdict for it.
          -->
          <label class="compare-room-field">
            <span>Σύγκριση για</span>
            <select class="roomrate-input" data-testid="room-select" [ngModel]="selectedRoomId()" (ngModelChange)="onRoomTypeChange($event)">
              <option value="">Επιλέξτε το δωμάτιό σας</option>
              <option *ngFor="let option of roomOptions()" [value]="option.value">{{ option.label }}</option>
            </select>
          </label>
          <!--
            The auto-pick notice sits right under the dropdown it describes, in
            the panel that is always on screen: the filters sidebar starts
            collapsed and the skip path that causes the auto-pick never opens
            it, so a notice in there was seen by nobody.
          -->
          <p *ngIf="autoPickedRoom()" class="map-notice" data-testid="auto-picked-room-hint">
            {{ autoPickedRoomHint }}
          </p>
          <!--
            ONE summary (spec §4.7). There used to be two: this title over stats
            computed from the map sample, and «Η αγορά της περιοχής» from
            /market/summary further down — and the two disagreed on screen. Both
            numbers and prices now come from /market/summary alone.
          -->
          <h2>Σύνοψη αγοράς</h2>
          <div *ngIf="marketSummaryWithData() as summary" class="market-summary-block" data-testid="market-summary">
            <p class="market-summary-counts" data-testid="market-summary-counts">{{ marketCountsLine(summary) }}</p>
            <div class="kpi-strip">
              <div><span>Ελάχιστη</span><strong>{{ formatEuro(summary.price_min_eur) }}</strong></div>
              <div><span>Διάμεση</span><strong>{{ formatEuro(summary.price_median_eur) }}</strong></div>
              <div><span>Μέση</span><strong>{{ formatEuro(summary.price_avg_eur) }}</strong></div>
              <div><span>Μέγιστη</span><strong>{{ formatEuro(summary.price_max_eur) }}</strong></div>
              <div><span>Μέση βαθμολογία</span><strong>{{ (summary.avg_review_score || 0).toFixed(1) }}</strong></div>
            </div>
          </div>
          <ng-container *ngIf="!marketSummaryWithData()">
            <div *ngIf="status() === 'loading'" class="results-empty">
              <strong>Αναζήτηση σε εξέλιξη</strong>
              <span>Τα στατιστικά αγοράς θα εμφανιστούν μόλις ολοκληρωθεί η αναζήτηση.</span>
            </div>
            <!--
              Not while the summary read is still out (the markers land a moment
              earlier), and not over results on the map: «no market data» there
              would be false, whatever happened to the summary read.
            -->
            <app-empty-state
              *ngIf="status() !== 'loading' && marketSummaryStatus() !== 'loading' && !competitors().length"
              data-testid="market-snapshot-empty"
              icon="chart"
              title="Δεν υπάρχουν ακόμη δεδομένα αγοράς"
              explanation="Τα στατιστικά εμφανίζονται μόλις ολοκληρωθεί η πρώτη αναζήτηση ανταγωνιστών για την περιοχή σας."
            ></app-empty-state>
          </ng-container>

          <!-- Spec §4.8: a price recommendation built on the search on screen. -->
          <a
            *ngIf="activeJobId() as jobId"
            class="pricing-link"
            data-testid="pricing-for-search"
            routerLink="/pricing"
            [queryParams]="{ job: jobId }"
          >Σύσταση τιμής για αυτή την αναζήτηση</a>

          <button class="secondary-button" type="button" [disabled]="!selectedKeys().size || actionStatus() === 'loading'" (click)="saveTrackedCompetitors()">
            Προσθήκη επιλεγμένων στην παρακολούθηση
          </button>
          <div *ngIf="saveMessage()" class="save-feedback" [class.save-feedback-error]="saveHasError()">
            {{ saveMessage() }}
          </div>

          <!--
            Rendered only with results on screen: with nothing to filter the
            control would be a dead switch that says the map hides things.
          -->
          <label *ngIf="competitors().length" class="checkbox-row map-filter-toggle">
            <input
              type="checkbox"
              data-testid="only-selected-toggle"
              [checked]="showOnlySelected()"
              (change)="toggleShowOnlySelected()"
            >
            <span>{{ onlySelectedFilterName }}</span>
          </label>
          <!--
            «Μόνο συγκρίσιμα» (owner decision 2026-09-30). Rendered for as long
            as a job is on screen, not only while it has rows: on (the default)
            over a market with nothing comparable, it must stay there to be
            switched off again.
          -->
          <label *ngIf="activeJobId()" class="checkbox-row map-filter-toggle">
            <input
              type="checkbox"
              data-testid="comparable-toggle"
              [checked]="comparableOnly()"
              (change)="toggleComparableOnly()"
            >
            <span>{{ comparableFilterName }}</span>
          </label>
          <!-- The automatic agent run for the room on screen: its pending line, then (on failure) the fallback notice. -->
          <div *ngIf="autoMatchPending()" class="results-empty" data-testid="auto-match-pending">
            <span>{{ autoMatchPendingMessage }}</span>
          </div>
          <div *ngIf="autoMatchNotice()" class="alert alert-error" data-testid="auto-match-notice">{{ autoMatchNotice() }}</div>

          <div *ngIf="status() === 'loading'" class="results-empty">
            <strong>Γίνεται αναζήτηση</strong>
            <span>Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.</span>
          </div>
          <div *ngIf="status() === 'ready' && !competitors().length">
            <app-empty-state
              icon="search"
              [title]="emptyResultTitle()"
              [explanation]="emptyResultExplanation()"
              [actionLabel]="emptyResultActionLabel()"
              (action)="onEmptyResultAction()"
            ></app-empty-state>
          </div>
          <div *ngIf="status() === 'idle' && !competitors().length">
            <app-empty-state
              icon="search"
              title="Δεν έχετε τρέξει ακόμη αναζήτηση"
              explanation="Ρυθμίστε τα φίλτρα και ξεκινήστε μια αναζήτηση ανταγωνιστών για να δείτε συγκρίσιμα δωμάτια."
            ></app-empty-state>
          </div>

          <!--
            Map ↔ list linking: hovering or focusing a card raises its hotel's
            marker, clicking it eases the map there; a marker click flashes the
            hotel's cards (is-highlighted) and scrolls the first into view.
            data-hotel-key is the marker's own key (hotelKey).
          -->
          <div class="competitor-list">
            <label
              *ngFor="let competitor of competitors()"
              class="competitor-card"
              [class.is-highlighted]="highlightedCardKey() === hotelKey(competitor)"
              [attr.data-hotel-key]="hotelKey(competitor)"
              (mouseenter)="highlightMarker(hotelKey(competitor))"
              (mouseleave)="unhighlightMarker(hotelKey(competitor))"
              (focusin)="highlightMarker(hotelKey(competitor))"
              (focusout)="unhighlightMarker(hotelKey(competitor))"
              (click)="easeToMarker($event, hotelKey(competitor))"
            >
              <input
                type="checkbox"
                [checked]="selectedKeys().has(markerKey(competitor))"
                (change)="toggleCompetitor(markerKey(competitor))"
              >
              <span class="competitor-body">
                <span class="card-row">
                  <strong>{{ competitor.hotel_name }}</strong>
                  <b>{{ formatEuro(competitor.price_per_night_eur) }}</b>
                </span>
                <small>{{ competitor.room_type || competitor.room_type_category || competitor.property_type }}</small>
                <!-- Spec §4.6: the category chip and the distance, each only when the API sent it. -->
                <span
                  *ngIf="categoryLabel(competitor.category_match) || formatDistance(competitor.distance_km) || competitor.comparable === false"
                  class="card-row compact card-tags"
                >
                  <span
                    *ngIf="categoryLabel(competitor.category_match) as category"
                    class="category-chip"
                    [class.is-similar]="competitor.category_match === 'similar'"
                    data-testid="card-category"
                  >{{ category }}</span>
                  <!-- «Μόνο συγκρίσιμα» off: the agent's "not comparable" verdict, muted. -->
                  <span
                    *ngIf="competitor.comparable === false"
                    class="category-chip is-not-comparable"
                    data-testid="not-comparable-chip"
                  >Μη συγκρίσιμο</span>
                  <small *ngIf="formatDistance(competitor.distance_km) as distance" data-testid="card-distance">{{ distance }}</small>
                </span>
                <span class="card-row compact">
                  <small>Βαθμολογία {{ competitor.review_score.toFixed(1) }} ({{ competitor.review_count }})</small>
                  <small>{{ roomsLeftLabel(competitor.rooms_left) }}</small>
                </span>
                <span *ngIf="matchRoomEnabled && matchScoreFor(competitor.hotel_name) !== null" class="card-row compact">
                  <small>Ταίριασμα</small>
                  <span class="match-chip" [ngClass]="matchChipClass(matchScoreFor(competitor.hotel_name))">
                    {{ formatScore(matchScoreFor(competitor.hotel_name)) }}
                  </span>
                </span>
              </span>
            </label>
          </div>
          <!-- Once under the results, not per card (spec §5): where every shown price comes from. -->
          <p *ngIf="competitors().length" class="price-source-note" data-testid="price-source-note">
            Τιμές επίσημης ιστοσελίδας (desktop, χωρίς σύνδεση)
          </p>

          <div class="match-controls">
            <div class="filter-section-label">Ταίριασμα δωματίου</div>
            <label class="checkbox-row">
              <input type="checkbox" [ngModel]="matchRoomEnabled" (ngModelChange)="onMatchToggle($event)">
              <span>Ταίριασμα με το δικό μου δωμάτιο</span>
            </label>
            <ng-container *ngIf="matchRoomEnabled">
              <!-- Agents (spec Α.4): ONE source badge for everything the match list
                   shows. «Εκτίμηση AI» is only ever earned by an agent read — a
                   false source here would be worse than no badge at all. -->
              <div class="match-source-row">
                <span class="source-chip" data-testid="match-source-badge">{{ matchSourceLabel() }}</span>
                <!-- Manual agent re-run (spec Α.1): needs the completed job on
                     screen and a reference room to send it. -->
                <button
                  type="button"
                  class="panel-toggle-button"
                  data-testid="reassess-button"
                  [disabled]="reassessInFlight() || !canReassess()"
                  (click)="reassessMatches()"
                >{{ reassessInFlight() ? "Εκτίμηση σε εξέλιξη…" : "Επανεκτίμηση ταιριάσματος" }}</button>
              </div>
              <div *ngIf="reassessNotice()" class="alert alert-error" data-testid="reassess-notice">{{ reassessNotice() }}</div>
              <label>
                <span>Ελάχιστο σκορ ταιριάσματος: {{ minMatchScore }}</span>
                <input type="range" min="0" max="100" step="5" [ngModel]="minMatchScore" (ngModelChange)="onMinScoreChange($event)">
              </label>
              <label>
                <span>Ταξινόμηση κατά</span>
                <select class="roomrate-input" [ngModel]="matchSort" (ngModelChange)="onMatchSortChange($event)">
                  <option value="match">Καλύτερο ταίριασμα</option>
                  <option value="price">Τιμή</option>
                  <option value="distance">Απόσταση</option>
                </select>
              </label>
              <div *ngIf="matchError()" class="alert alert-error">{{ matchError() }}</div>
              <div *ngIf="matchStatus() === 'loading'" class="results-empty">
                <strong>Βαθμολόγηση δωματίων</strong>
                <span>Σύγκριση των πακέτων των ανταγωνιστών με το δικό σας δωμάτιο.</span>
              </div>
              <div *ngIf="matchStatus() === 'ready' && !matchedCompetitors().length" class="results-empty">
                <strong>Κανένα ταίριασμα σε αυτό το όριο</strong>
                <span>Μειώστε το ελάχιστο σκορ ταιριάσματος ή τρέξτε πρώτα νέα αναζήτηση ανταγωνιστών.</span>
              </div>
              <div *ngIf="matchedCompetitors().length" class="matched-list">
                <article *ngFor="let hotel of matchedCompetitors(); index as hotelIndex" class="matched-card">
                  <span class="card-row">
                    <strong>{{ hotel.hotel_name }}</strong>
                    <span class="match-chip" [ngClass]="matchChipClass(hotel.best_match_score)">
                      {{ formatScore(hotel.best_match_score) }}
                    </span>
                  </span>
                  <small>
                    {{ formatExactEuro(hotel.price_min_eur) }}&ndash;{{ formatExactEuro(hotel.price_max_eur) }}
                    &middot; Βαθμολογία {{ hotel.review_score.toFixed(1) }} ({{ hotel.review_count }})
                    <ng-container *ngIf="formatDistance(hotel.distance_km) as distance">&middot; {{ distance }}</ng-container>
                  </small>
                  <span class="matched-packages">
                    <ng-container *ngFor="let group of roomGroupsFor(hotel)">
                      <ng-container *ngIf="!group.rangeLabel">
                        <ng-container *ngFor="let pkg of group.packages">
                          <!-- No rate-plan data (old rows): today's plain row. -->
                          <ng-container *ngIf="!pkg.rate_plan">
                            <span class="card-row compact">
                              <small>{{ pkg.room_type }}</small>
                              <span class="matched-package-meta">
                                <span *ngIf="pkg.comparable === false" class="rate-plan-chip is-not-comparable" data-testid="not-comparable-chip">Μη συγκρίσιμο</span>
                                <small>{{ formatExactEuro(pkg.price_per_night_eur) }}</small>
                                <span class="match-chip" [ngClass]="matchChipClass(pkg.match_score)">
                                  {{ formatScore(pkg.match_score) }}
                                </span>
                              </span>
                            </span>
                            <!-- Agents (spec Α.4): the agent's own line under the chip; statistical rows carry null and get nothing. -->
                            <small *ngIf="pkg.match_reasoning" class="match-reasoning" data-testid="match-reasoning">{{ pkg.match_reasoning }}</small>
                          </ng-container>
                          <!-- A room's ONLY plan (spec §5): the guest-visible price and its
                               chips inline — nothing to collapse, so no range or button. -->
                          <span *ngIf="pkg.rate_plan" class="card-row compact">
                            <small>{{ pkg.room_type }}</small>
                            <span class="rate-plan-row single-plan-row" data-testid="single-plan-row">
                              <ng-container *ngTemplateOutlet="planStrip; context: { $implicit: pkg }"></ng-container>
                            </span>
                          </span>
                        </ng-container>
                      </ng-container>
                      <!-- Spec §5: several rate plans of one room collapse into a range and open on demand. -->
                      <ng-container *ngIf="group.rangeLabel">
                        <span class="card-row compact">
                          <small>{{ group.room_type }}</small>
                          <span class="matched-package-meta">
                            <small data-testid="room-price-range">{{ group.rangeLabel }}</small>
                            <button
                              type="button"
                              class="text-button plans-toggle"
                              [attr.aria-expanded]="plansExpanded(hotelIndex, group)"
                              (click)="togglePlans(hotelIndex, group)"
                            >Πλάνα τιμών</button>
                          </span>
                        </span>
                        <span *ngIf="plansExpanded(hotelIndex, group)" class="rate-plan-list" data-testid="rate-plan-list">
                          <span *ngFor="let pkg of group.packages" class="rate-plan-row" data-testid="rate-plan-row">
                            <ng-container *ngTemplateOutlet="planStrip; context: { $implicit: pkg }"></ng-container>
                          </span>
                        </span>
                      </ng-container>
                    </ng-container>
                  </span>
                </article>
              </div>
              <!-- One plan's price + chips, rendered identically wherever a plan shows
                   (the opened list and a room's only plan) so the two cannot drift. -->
              <ng-template #planStrip let-pkg>
                <b class="rate-plan-price">{{ formatExactEuro(effectivePlanPrice(pkg)) }}</b>
                <span *ngIf="pkg.rate_plan?.has_genius_discount" class="rate-plan-chip">Genius</span>
                <span *ngIf="pkg.rate_plan?.discount_label" class="rate-plan-chip">{{ pkg.rate_plan?.discount_label }}</span>
                <span *ngIf="cancellationChipLabel(pkg.rate_plan?.cancellation_type) as cancellation" class="rate-plan-chip">
                  {{ cancellation }}
                </span>
                <span *ngIf="pkg.rate_plan?.payment_label" class="rate-plan-chip">{{ pkg.rate_plan?.payment_label }}</span>
                <span *ngIf="pkg.comparable === false" class="rate-plan-chip is-not-comparable" data-testid="not-comparable-chip">Μη συγκρίσιμο</span>
                <small *ngIf="pkg.meals" class="rate-plan-meals">{{ pkg.meals }}</small>
                <span class="match-chip" [ngClass]="matchChipClass(pkg.match_score)">
                  {{ formatScore(pkg.match_score) }}
                </span>
                <!-- Agents (spec Α.4): full-basis, so it wraps onto its own line under the chip. -->
                <small *ngIf="pkg.match_reasoning" class="match-reasoning" data-testid="match-reasoning">{{ pkg.match_reasoning }}</small>
              </ng-template>
            </ng-container>
          </div>
        </aside>
      </section>

      <app-notification-toasts />
    </main>
  `,
})
export class MapPageComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild("mapContainer") mapContainer?: ElementRef<HTMLDivElement>;
  @ViewChild("scrapeProgressBlock") scrapeProgressBlock?: ElementRef<HTMLDivElement>;

  mapboxToken = environment.mapboxToken;
  // Bound to the filter checkbox's label, so the control and the two messages
  // that name it cannot drift apart.
  readonly onlySelectedFilterName = ONLY_SELECTED_FILTER_NAME;
  readonly comparableFilterName = COMPARABLE_FILTER_NAME;
  readonly autoPickedRoomHint = AUTO_PICKED_ROOM_HINT;
  readonly amenities = signal<AmenityOption[]>(COMMON_AMENITIES);
  readonly competitors = signal<CompetitorMapMarker[]>([]);
  readonly selectedKeys = signal<Set<string>>(new Set());
  // Optional "hide everything I did not tick" filter over the map. A finished
  // search plots ALL of its results (owner-observed 2026-08-22: nobody, the
  // owner included, discovered that the map only ever showed ticked cards), so
  // this is the way back to the narrow view — off by default, and deliberately
  // NOT reset by a new search or by resetCompetitorSearchState: it is the
  // user's own view preference, and silently re-showing everything they chose
  // to hide would undo a deliberate click. A signal, not a plain field: the
  // template and visibleCompetitors read it. It is only ever written by a
  // synchronous click handler, so it needs no chain-generation guard — the
  // guards exist for writes queued behind an await.
  readonly showOnlySelected = signal(false);
  // «Μόνο συγκρίσιμα» (owner decision 2026-09-30): ON by default, so every
  // read shows only the rooms the matching agent judged comparable with the
  // selected room; off, everything comes back with a `comparable` verdict per
  // package. Every competitors/markers/summary/amenities read sends it as
  // `comparable_only`. Written only by its own click handler.
  readonly comparableOnly = signal(true);
  // (job, room) pairs the automatic agent run has already been decided for in
  // this visit — probed, posted or abandoned-for-good — so each pair is run
  // at most ONCE per page session and a failed run is never retried by
  // itself. A signal: it is written around awaits (AGENTS.md).
  private readonly autoMatchDecided = signal<ReadonlySet<string>>(new Set());
  // The automatic run's pending line and its fallback notice: both written
  // after awaits, so signals.
  readonly autoMatchPending = signal(false);
  readonly autoMatchNotice = signal("");
  readonly autoMatchPendingMessage = AUTO_MATCH_PENDING_MESSAGE;
  // True once the user has touched competitor selection during this
  // navigation (toggleCompetitor). The load/restore chain's own selectedKeys
  // clears and its stored-selection/tracked-competitors apply below all
  // defer to it, because a click landing while that chain is still settling
  // was silently wiped by the chain's own writes (live-proven 2026-08-11:
  // the same click stuck once the chain had fully settled). Reset wherever
  // selection legitimately starts over: a fresh search and the initial
  // property/room context reset.
  private readonly userTouchedSelection = signal(false);
  readonly selectedAmenities = signal<Set<string>>(new Set());
  readonly roomTypes = signal<OwnedPropertyRoomType[]>([]);
  readonly propertyName = signal("");
  readonly selectedRoomId = signal("");
  readonly selectedRoomType = signal("");
  readonly filtersCollapsed = signal(true);
  readonly status = signal<LoadingState>("idle");
  readonly actionStatus = signal<LoadingState>("idle");
  readonly message = signal(START_SEARCH_MESSAGE);
  // Machine-readable truth emitted by the scraper and persisted atomically
  // with job completion. A signal because it is written after async polling.
  private readonly activeResultSummary = signal<ScrapeResultSummary | null>(null);
  readonly saveMessage = signal("");
  readonly saveHasError = signal(false);
  readonly error = signal("");
  // True while the room in the dropdown was chosen by ensureSelectedRoom
  // rather than by the user or the wizard (see there). Starts false with every
  // navigation, so the notice cannot outlive the visit it describes.
  readonly autoPickedRoom = signal(false);
  // The map's OWN failure, kept strictly apart from `error`/`status`, which
  // belong to the competitor results (chip task_8fecd58c).
  readonly mapError = signal("");
  readonly filters = signal<MapFilters>({
    destination: "",
    check_in: DEFAULT_STAY.checkIn,
    check_out: DEFAULT_STAY.checkOut,
    adults: "2",
    children: "0",
    rooms: "1",
    // Spec §3.1: the server default moved from 25 to 40; the page used to ask for 8.
    limit: "40",
    radius_km: String(DEFAULT_RADIUS_KM),
  });
  // «Γειτονικές περιοχές» (spec §4.1): prefilled from the server's defaults for
  // the destination, replaced by a restored job's own list. Signals: both
  // writers run after an await.
  readonly nearbyDestinations = signal<string[]>([]);
  readonly nearbyDraft = signal("");
  readonly maxNearbyDestinations = MAX_NEARBY_DESTINATIONS;
  readonly canAddNearbyDestination = computed(() => this.nearbyDestinations().length < MAX_NEARBY_DESTINATIONS);
  // True once the user added or removed an area, so late server defaults never
  // overwrite a list the user already shaped.
  private readonly nearbyEdited = signal(false);
  // Progress of the search this page started (spec §4.2): true from the Find
  // click until its job completes or fails. The progress comes from each poll
  // and the clock from a 1 s interval — both outside the zone, so signals.
  readonly scrapeInFlight = signal(false);
  readonly scrapeProgress = signal<ScrapeJobProgress | null>(null);
  private readonly scrapeClockStartedAt = signal<number | null>(null);
  private readonly scrapeClockNow = signal(Date.now());
  private scrapeClockTimer: ReturnType<typeof window.setInterval> | null = null;
  readonly scrapeProgressLabel = computed(() => this.describeScrapeProgress(this.scrapeProgress()));
  readonly scrapeElapsedLabel = computed(() => {
    const startedAt = this.scrapeClockStartedAt();
    if (startedAt === null) {
      return "";
    }
    const seconds = Math.max(0, Math.floor((this.scrapeClockNow() - startedAt) / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  });
  readonly marketSummary = signal<MarketSummary | null>(null);
  matchRoomEnabled = false;
  minMatchScore = 0;
  matchSort: CompetitorSort = "match";
  readonly matchedCompetitors = signal<Competitor[]>([]);
  readonly matchStatus = signal<LoadingState>("idle");
  readonly matchError = signal("");
  // Where the shown match scores come from (spec Α.4). Written after the
  // /competitors/ await — outside the zone, so a signal like its siblings.
  // "statistical" until a read proves otherwise: that is the honest default
  // (spec Α.5), and «Εκτίμηση AI» is only ever earned by agent rows.
  readonly matchSource = signal<MatchSourceState>("statistical");
  readonly matchSourceLabel = computed(() => {
    const source = this.matchSource();
    if (source === "agent") {
      return "Εκτίμηση AI";
    }
    return source === "partial" ? "Εκτίμηση AI (μερική)" : "Στατιστική εκτίμηση";
  });
  // The manual agent re-run (spec Α.1): in-flight flag and fallback notice
  // are both written around awaits, so both are signals.
  readonly reassessInFlight = signal(false);
  readonly reassessNotice = signal("");
  // The POST needs a completed job (activeJobId is only ever set for one) and
  // the reference room it should score against.
  readonly canReassess = computed(() => Boolean(this.activeJobId() && this.selectedRoomId()));
  // The matched hotels' packages grouped into rooms (spec §5), computed once
  // per match load: the template reads the groups on every change-detection
  // pass, and rebuilding them there would hand *ngFor fresh identities each
  // time — recreating the rows (and their expand buttons) under the user.
  private readonly matchedRoomGroups = computed(() => {
    const groups = new Map<Competitor, RoomPlanGroup[]>();
    for (const hotel of this.matchedCompetitors()) {
      groups.set(hotel, this.buildRoomGroups(hotel.packages ?? []));
    }
    return groups;
  });
  // Which «Πλάνα τιμών» lists are open, keyed cardIndex|room so two
  // same-named hotels in one list cannot toggle together. A signal: the
  // template reads it, the click handler and the match-load continuation
  // (after an await) write it. Cleared whenever a match load LANDS — rows the
  // server just re-sent are new rows, and an old key must not leave a list of
  // a later search pre-opened.
  private readonly expandedPlanKeys = signal<Set<string>>(new Set());

  readonly roomOptions = computed(() =>
    this.roomTypes().map((item) => ({
      value: item.id,
      label: `${item.room_type} (${item.room_type_category})`,
    })),
  );
  readonly selectedOwnedRoom = computed(() =>
    this.roomTypes().find((item) => item.id === this.selectedRoomId())
      || this.roomTypes().find((item) => item.room_type_category === this.selectedRoomType()),
  );
  /**
   * True when the hotelier's own facility filter is the only thing that can
   * explain an empty screen.
   *
   * The facility checkboxes are a READ filter — `amenities=` on the job-scoped
   * competitor and summary reads — so unlike everything `result_summary`
   * describes, they hide rows the scrape really did write. That makes them
   * invisible to the summary-based reasoning below, which is why this is asked
   * first and answered from different facts: ticks on screen, a job-scoped
   * result with nothing in it, and a job whose own summary says it wrote rows.
   *
   * A job carrying NO summary counts too (older jobs, and the ones this page
   * restores from a list): the read is scoped to that job, the job is what is
   * on screen, and the read-time facility filter is the only thing the backend
   * drops job-scoped rows for. `rows_written === 0` is the one case that falls
   * through — the scrape itself wrote nothing, so the filter cannot be what is
   * hiding it and the summary below has the truthful answer.
   *
   * "ready" is load-bearing and not a formality: a FAILED read leaves exactly
   * this shape behind — setError empties the results, the job id stays, the
   * ticks stay — and the map (which reads this before its own ready check)
   * answered a 500 by blaming the facility filter, sending the user to untick
   * boxes that would not bring the read back. Nothing is known about what the
   * filter hides until a read has succeeded.
   */
  private readonly amenityFilterHidesEverything = computed(() => {
    if (
      this.status() !== "ready"
      || !this.selectedAmenities().size
      || this.competitors().length
      || !this.activeScrapeJobId()
    ) {
      return false;
    }
    const summary = this.activeResultSummary();
    return !summary || summary.version !== 1 || summary.rows_written > 0;
  });

  /**
   * The same question for «Μόνο συγκρίσιμα», answered from the same facts:
   * `comparable_only=true` is also a READ filter, so a job whose rows exist
   * can come back empty because of the switch alone. Asked after the facility
   * filter, which keeps its answer when both are on.
   */
  private readonly comparableFilterHidesEverything = computed(() => {
    if (
      this.status() !== "ready"
      || !this.comparableOnly()
      || this.competitors().length
      || !this.activeScrapeJobId()
    ) {
      return false;
    }
    const summary = this.activeResultSummary();
    return !summary || summary.version !== 1 || summary.rows_written > 0;
  });

  private readonly emptyResultReason = computed<EmptyResultReason | null>(() => {
    // Asked first, though for a v1 summary the two cannot both be true: this
    // branch requires `rows_written > 0` and every branch below requires
    // `rows_written === 0`. What makes the order load-bearing is the early
    // `return null` two lines down — reversed, a job whose rows were written
    // and then filtered out on the read would fall out of here with NO reason
    // at all, and the generic «Δεν βρέθηκαν συγκρίσιμα δωμάτια» would blame
    // the market for the user's own tick. It also answers the cases the
    // summary cannot: a missing or non-v1 summary reaches nothing below.
    if (this.amenityFilterHidesEverything()) {
      return { kind: "amenities" };
    }
    if (this.comparableFilterHidesEverything()) {
      return { kind: "comparable" };
    }
    const summary = this.activeResultSummary();
    if (!summary || summary.version !== 1 || summary.rows_written !== 0) {
      return null;
    }
    const counts = summary.filter_counts ?? {};
    // The two stages before storage, in the scraper's own order: the first one
    // that left nothing is the answer, with how many offers it was handed.
    for (const kind of ["single_rooms", "capacity"] as const) {
      const stage = counts[kind];
      if (stage && stage.before > 0 && stage.after === 0) {
        return { kind, count: stage.before };
      }
    }
    const remainingFilters = ["room_name", "meal", "free_cancellation", "amenities"];
    const emptiedLater = remainingFilters.some((name) => {
      const stage = counts[name];
      return Boolean(stage && stage.before > 0 && stage.after === 0);
    });
    // What reached those later filters: the offers the first two stages kept.
    const keptCount = counts["capacity"]?.after ?? counts["single_rooms"]?.after ?? summary.rows_seen;
    return emptiedLater && keptCount > 0
      ? { kind: "remaining-filters" as const, count: keptCount }
      : null;
  });

  readonly emptyResultTitle = computed(() => {
    const reason = this.emptyResultReason();
    if (!reason) {
      return "Δεν βρέθηκαν συγκρίσιμα δωμάτια";
    }
    if (reason.kind === "amenities") {
      return AMENITY_FILTER_HIDES_ALL_TITLE;
    }
    if (reason.kind === "comparable") {
      return COMPARABLE_HIDES_ALL_TITLE;
    }
    const count = reason.count;
    if (reason.kind === "remaining-filters") {
      // «Συγκρίσιμες», no longer «της κατηγορίας σας»: since Round 6 the scraper
      // keeps similar categories too, so these offers are not all the owner's.
      return count === 1
        ? "Βρέθηκε 1 συγκρίσιμη προσφορά"
        : `Βρέθηκαν ${count} συγκρίσιμες προσφορές`;
    }
    if (reason.kind === "single_rooms") {
      return count === 1
        ? "Βρέθηκε 1 προσφορά για μονόκλινο δωμάτιο"
        : `Βρέθηκαν ${count} προσφορές για μονόκλινα δωμάτια`;
    }
    return count === 1
      ? "Βρέθηκε 1 τιμή για λιγότερα άτομα από όσα ζητήσατε"
      : `Βρέθηκαν ${count} τιμές για λιγότερα άτομα από όσα ζητήσατε`;
  });
  // The only two of these empty states the user can undo from where they are
  // standing — the two read filters — so the only two that offer a button
  // (onEmptyResultAction). Empty elsewhere, and the empty-state component
  // renders no button for an empty label.
  readonly emptyResultActionLabel = computed(() => {
    const kind = this.emptyResultReason()?.kind;
    if (kind === "amenities") {
      return CLEAR_AMENITY_FILTERS_LABEL;
    }
    return kind === "comparable" ? SHOW_ALL_ROOMS_LABEL : "";
  });
  readonly emptyResultExplanation = computed(() => {
    const reason = this.emptyResultReason();
    if (reason?.kind === "amenities") {
      return AMENITY_FILTER_HIDES_ALL_EXPLANATION;
    }
    if (reason?.kind === "comparable") {
      return COMPARABLE_HIDES_ALL_EXPLANATION;
    }
    // Both pre-storage stages answer in the same sentence, naming what the
    // offers were; the way forward is a search that reaches more of the market.
    if (reason?.kind === "single_rooms" || reason?.kind === "capacity") {
      const offers = reason.kind === "single_rooms" ? "μονόκλινα δωμάτια" : "λιγότερα άτομα από όσα ζητήσατε";
      return `Η αναζήτηση ολοκληρώθηκε και επέστρεψε τιμές, όλες όμως για ${offers}. `
        + "Δοκιμάστε άλλες ημερομηνίες ή περισσότερες γειτονικές περιοχές και τρέξτε ξανά την αναζήτηση.";
    }
    if (reason?.kind === "remaining-filters") {
      return "Καμία από αυτές δεν πέρασε τα υπόλοιπα φίλτρα ονόματος, γευμάτων, ακύρωσης ή παροχών.";
    }
    return "Η αναζήτηση ολοκληρώθηκε αλλά δεν επέστρεψε δωμάτια συγκρίσιμα με το δικό σας για αυτές τις ημερομηνίες.";
  });
  // What the map actually plots. Every result with usable coordinates goes on
  // the map as soon as a search finishes; ticking a card selects it for
  // TRACKING, not for display. `showOnlySelected` is the one way selection
  // narrows the map, and it is off unless the user turned it on.
  //
  // Coordinates are filtered here rather than in renderMarkers so the map's
  // empty-state overlay is driven by what can really be drawn: results that
  // all lack coordinates leave a genuinely empty map, and the overlay has to
  // say so instead of staying hidden behind a non-zero count.
  readonly plottableCompetitors = computed(() =>
    this.competitors().filter((competitor) => this.isValidCoordinate(competitor)),
  );
  readonly visibleCompetitors = computed(() => {
    const plottable = this.plottableCompetitors();
    if (!this.showOnlySelected()) {
      return plottable;
    }
    return plottable.filter((competitor) => this.selectedKeys().has(this.markerKey(competitor)));
  });
  // «Εσείς» (spec §4.3): the owner's own hotel from /maps/own-property, for the
  // job on screen or for no job. Written after an await, so a signal; null
  // until it is read, and after a failed read (said nowhere: best effort).
  readonly ownProperty = signal<OwnPropertyMapInfo | null>(null);
  readonly ownLocation = computed(() => {
    const own = this.ownProperty();
    if (!own || typeof own.latitude !== "number" || typeof own.longitude !== "number") {
      return null;
    }
    const valid = Number.isFinite(own.latitude) && Number.isFinite(own.longitude)
      && Math.abs(own.latitude) <= 90 && Math.abs(own.longitude) <= 180;
    return valid ? { lng: own.longitude, lat: own.latitude } : null;
  });
  // The API knows the hotel and cannot place it: the map says so instead of
  // leaving the owner to wonder where «Εσείς» went.
  readonly ownCoordinatesMissing = computed(() => Boolean(this.ownProperty()) && !this.ownLocation());
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
  readonly showMapLegend = computed(() =>
    Boolean(this.mapboxToken) && (this.visibleCompetitors().length > 0 || this.ownLocation() !== null));
  // The summary box renders only a summary that describes something: a read
  // that came back empty (or not as an object at all) leaves the empty states.
  readonly marketSummaryWithData = computed(() => {
    const summary = this.marketSummary();
    return summary && (summary.total_hotels > 0 || summary.total_records > 0) ? summary : null;
  });
  // Whether the summary read for the result on screen is still out. Written
  // around an await, so a signal; the empty state waits for it (see template).
  readonly marketSummaryStatus = signal<LoadingState>("idle");
  // The scrape's own warnings about the job on screen, as sentences (spec §7).
  // A code this page does not know yet is left out rather than shown raw.
  readonly searchWarnings = computed(() => {
    const warnings = this.activeResultSummary()?.warnings;
    return (Array.isArray(warnings) ? warnings : [])
      .map((code) => this.describeSearchWarning(code))
      .filter((sentence) => Boolean(sentence));
  });

  private ownedPropertyId = "";
  private rawDestination = "";
  private hotelMatchScores = new Map<string, number>();
  private mapboxModule: MapboxModule | null = null;
  private map: mapboxgl.Map | null = null;
  // Keyed by hotel (hotelKey). Plain, not a signal: never rendered, and only
  // the imperative marker code reads it.
  private readonly markerEntries = new Map<string, MarkerEntry>();
  // Map ↔ list linking, both keyed by hotelKey: the marker raised while its
  // card is hovered or focused, and the card flashed after a marker click.
  // Signals: the flash is written from a Mapbox DOM listener and cleared by a
  // timer (AGENTS.md), and the template reads the card one.
  readonly highlightedMarkerKey = signal<string | null>(null);
  readonly highlightedCardKey = signal<string | null>(null);
  private cardHighlightTimer: ReturnType<typeof window.setTimeout> | null = null;
  private readonly hostElement: ElementRef<HTMLElement> = inject(ElementRef);
  // The «Εσείς» marker and its circle, imperative like the competitor markers:
  // plain fields, never rendered (what the template shows reads the ownProperty
  // and radiusCircleKm signals). `ownMapSignature` is where the last fit saw
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
  // Signals, not plain fields (chip task_a934b51e): both are written after an
  // await inside the load/restore chains, i.e. outside the Angular zone, so
  // AGENTS.md puts them on the signal side of the line even though nothing
  // renders them directly today. The conversion is behaviour-neutral — it only
  // removes the standing invitation to read them from a template later and get
  // a stale value.
  private readonly activeScrapeJobId = signal("");
  // The job on screen, for the template: «Μόνο συγκρίσιμα» and the link to
  // its price recommendation exist only while there is one. Only ever set for
  // a completed job (loadMarkers runs for nothing else).
  readonly activeJobId = this.activeScrapeJobId.asReadonly();
  // Id of the load chain that owns the screen. findCompetitors bumps it FIRST,
  // so every write still queued behind an await in an older chain (the restore
  // chain on load, the tail of a previous search) is dropped instead of
  // landing on the new search. Live-proven 2026-08-11 (job b3a1c5e8): a Find
  // click ~1s after /map opened left "the search finished and returned
  // nothing" on screen for the 2+ minutes the new job really ran, because the
  // OLD chain's loadMarkers tail (competitors + status "ready") overwrote the
  // fresh "loading". Deliberately a plain counter, not a signal: it is never
  // rendered and never read from the template, so the AGENTS.md rule (signals
  // so out-of-zone writes still trigger change detection) does not apply — the
  // writes it gates are themselves signals.
  private chainGeneration = 0;
  // True when the restored job's stay had already passed and the date filters
  // were rolled forward, so the banner can say the shown prices are historic.
  private restoredStayIsStale = false;
  // The stay the job behind the CURRENT markers was scraped for, always set
  // from that job itself (see loadMarkers). Job-scoped marker, summary and
  // match reads must use it, because the date inputs routinely hold something
  // else: a restored past window rolled forward, or the dates the user typed
  // for the next search. It used to be written once, on restore, and never
  // cleared — so a new search's rows were read with the RESTORED job's dates
  // and a scrape that wrote 8 rows rendered as an empty map (live, job
  // 6510897e). Null whenever no job-scoped result is on screen. A signal for
  // the same reason as its sibling activeScrapeJobId above.
  private readonly activeJobScope = signal<JobScope | null>(null);
  private saveMessageTimeout: ReturnType<typeof window.setTimeout> | null = null;
  private matchRefreshTimer: ReturnType<typeof window.setTimeout> | null = null;
  // Set by ngOnDestroy so long-running poll loops stop with the component.
  private destroyed = false;

  constructor(
    private readonly api: ApiClientService,
    private readonly auth: AuthService,
    private readonly onboarding: OnboardingService,
    private readonly workflow: WorkflowStorageService,
    private readonly router: Router,
    private readonly notifications: NotificationsService,
    readonly setupProgress: SetupProgressService,
  ) {}

  async ngOnInit(): Promise<void> {
    // Startup is a chain like any other: everything below runs after two
    // awaits, and the Find button is already live once loadCurrentUser
    // resolved (property, rooms and dates are all in place). A click landing
    // in that window owns the screen from then on — otherwise the reset below
    // undoes the search that click started, re-enables the button (inviting a
    // second, Apify-costing job for a search already running) and lets the
    // restore repaint the previous job over the live one. Guarded by
    // generation rather than by actionStatus, so it is the same "who owns the
    // screen" question the rest of this component answers; actionStatus is a
    // rendering of that answer, not the answer itself.
    const generation = this.chainGeneration;
    await this.loadCurrentUser();
    // The nearby defaults land BEFORE the restore starts, so a restored job's
    // own areas always overwrite them rather than racing them.
    await Promise.all([this.loadAmenities(), this.loadNearbyDefaults(generation)]);
    if (!this.isCurrentChain(generation)) {
      return;
    }
    this.resetCompetitorSearchState();
    // Reuse the last completed scrape instead of forcing a fresh (Apify-costing)
    // live search on every navigation back to the map.
    await this.restoreLatestCompetitorSearch(generation);
    // Nothing restored (no job, or its read failed): the owner's hotel still
    // goes on the map (spec §4.3). A restored job read it in loadMarkers.
    if (this.isCurrentChain(generation) && !this.activeScrapeJobId()) {
      void this.loadOwnProperty("", generation);
    }
  }

  async ngAfterViewInit(): Promise<void> {
    this.clearMarkers();
    await this.initializeMap();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    if (this.saveMessageTimeout) {
      window.clearTimeout(this.saveMessageTimeout);
    }
    if (this.matchRefreshTimer) {
      window.clearTimeout(this.matchRefreshTimer);
    }
    if (this.cardHighlightTimer) {
      window.clearTimeout(this.cardHighlightTimer);
    }
    this.stopScrapeProgress();
    this.resizeObserver?.disconnect();
    this.clearMarkers();
    this.removeOwnMarker();
    this.map?.remove();
    this.map = null;
  }

  /**
   * The one way the result set changes.
   *
   * The checklist's tracking step now asks for min(3, available), so the
   * available count has to move with this list or the step would demand a
   * number this market cannot supply (Faliraki/double really returns 2-3
   * rooms). Only rows carrying a property_id count: saveTrackedCompetitors
   * drops the others, so they are not available to track.
   */
  private setCompetitors(rows: CompetitorMapMarker[]): void {
    this.competitors.set(rows);
    this.setupProgress.setAvailableCompetitors(rows.filter((row) => row.property_id).length);
  }

  markerKey(marker: CompetitorMapMarker): string {
    return marker.room_package_id || marker.property_id || `${marker.hotel_name}-${marker.latitude}-${marker.longitude}`;
  }

  /**
   * The map marker's identity: one marker per hotel, whatever package a row
   * carries (spec §4.5). The cards carry it too, so a card and its marker can
   * find each other.
   */
  hotelKey(marker: CompetitorMapMarker): string {
    return marker.property_id || marker.hotel_name;
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

  /**
   * A marker click: its hotel's cards flash for CARD_HIGHLIGHT_MS and the first
   * one scrolls into view, so the list says which result the map just opened.
   * Runs in a Mapbox DOM listener, hence the signal and a timer that ngOnDestroy
   * clears; a newer click restarts the flash for its own hotel.
   */
  private flashCard(key: string): void {
    this.highlightedCardKey.set(key);
    if (this.cardHighlightTimer) {
      window.clearTimeout(this.cardHighlightTimer);
    }
    this.cardHighlightTimer = window.setTimeout(() => {
      this.cardHighlightTimer = null;
      this.highlightedCardKey.set(null);
    }, CARD_HIGHLIGHT_MS);
    const card = this.hostElement.nativeElement.querySelector<HTMLElement>(
      `.competitor-card[data-hotel-key="${CSS.escape(key)}"]`,
    );
    card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  setFilter(key: keyof MapFilters, value: string): void {
    this.filters.update((filters) => ({ ...filters, [key]: value }));
  }

  /**
   * Add the typed area as a chip. Refused (the text stays in the input) when
   * it repeats a chip in any letter case or names the main destination itself —
   * the server would drop both silently, so the chip would be a lie.
   */
  addNearbyDestination(): void {
    const area = this.nearbyDraft().trim();
    if (!area || !this.canAddNearbyDestination()) {
      return;
    }
    const key = this.destinationKey(area);
    if (this.isMainDestination(area) || this.nearbyDestinations().some((item) => this.destinationKey(item) === key)) {
      return;
    }
    this.nearbyDestinations.update((areas) => [...areas, area]);
    this.nearbyDraft.set("");
    this.nearbyEdited.set(true);
  }

  removeNearbyDestination(area: string): void {
    this.nearbyDestinations.update((areas) => areas.filter((item) => item !== area));
    this.nearbyEdited.set(true);
  }

  private destinationKey(value: string): string {
    return value.trim().toLocaleLowerCase("el-GR");
  }

  private isMainDestination(area: string): boolean {
    const key = this.destinationKey(area);
    return [this.filters().destination, this.rawDestination]
      .some((destination) => Boolean(destination) && this.destinationKey(destination) === key);
  }

  /** Trimmed, blank-free, case-insensitively unique, never the main destination, at most 8. */
  private normalizeNearbyDestinations(areas: unknown[]): string[] {
    const seen = new Set<string>();
    const normalized: string[] = [];
    for (const item of areas) {
      const area = typeof item === "string" ? item.trim() : "";
      const key = this.destinationKey(area);
      if (!area || seen.has(key) || this.isMainDestination(area)) {
        continue;
      }
      seen.add(key);
      normalized.push(area);
    }
    return normalized.slice(0, MAX_NEARBY_DESTINATIONS);
  }

  /**
   * Prefill the chips with the server's default neighbouring areas.
   *
   * Best effort: an older API (404) or a failed read leaves the list empty and
   * the search runs on the main destination alone. Dropped when a Find click
   * took the screen or the user already edited the list.
   */
  private async loadNearbyDefaults(generation: number): Promise<void> {
    const destination = this.filters().destination.trim();
    if (!destination) {
      return;
    }
    try {
      const response = await this.api.get<NearbyDestinationsResponse>(
        "/api/v1/onboarding/nearby-destinations",
        new URLSearchParams({ destination }),
      );
      if (!this.isCurrentChain(generation) || this.nearbyEdited()) {
        return;
      }
      this.nearbyDestinations.set(this.normalizeNearbyDestinations(Array.isArray(response?.nearby) ? response.nearby : []));
    } catch {
      // Nothing to prefill; the chips stay as they are.
    }
  }

  /**
   * Why the map is empty — one honest answer per reason it can be.
   *
   * Results are plotted automatically now, so "tick a card to see them" is no
   * longer true of any of these states. The remaining reasons are distinct and
   * must not be collapsed into each other: the user's own filter hiding
   * everything is not the same as a search finding nothing, and neither is the
   * same as results that carry no coordinates.
   */
  mapEmptyMessage(): string {
    if (this.status() === "loading") {
      return "Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών...";
    }
    if (this.competitors().length) {
      // Checked BEFORE the filter: a result set nothing can draw stays that
      // whatever the filter is set to, and blaming the filter there would send
      // the user to a switch that would change nothing.
      if (!this.plottableCompetitors().length) {
        return "Τα αποτελέσματα δεν έχουν συντεταγμένες, οπότε δεν μπορούν να μπουν στον χάρτη.";
      }
      // Drawable rooms exist and none of them is on screen, so the user's own
      // filter is the only thing left that can be hiding them.
      return FILTER_HIDES_ALL_MESSAGE;
    }
    // Nothing came back at all, and the facility filter is what dropped it:
    // the rows exist in the job behind this screen. Said on the map too, not
    // only in the sidebar, because this is a state the user caused and can undo
    // — the generic "see the right-hand column" below is for the reasons they
    // cannot.
    if (this.amenityFilterHidesEverything()) {
      return AMENITY_FILTER_HIDES_ALL_MESSAGE;
    }
    if (this.comparableFilterHidesEverything()) {
      return COMPARABLE_HIDES_ALL_MESSAGE;
    }
    if (this.status() === "ready") {
      // Deliberately not "the search found nothing": a search CAN finish with
      // offers that the scraper's own stages kept off the map (see
      // emptyResultTitle). The sidebar carries that distinction; here we only
      // state what is true of the map itself.
      return "Κανένα δωμάτιο δεν μπορεί να μπει στον χάρτη. Δείτε τη δεξιά στήλη για λεπτομέρειες.";
    }
    return "Τρέξτε μια αναζήτηση ανταγωνιστών για να δείτε όλα τα αποτελέσματα στον χάρτη.";
  }

  /** Narrow the map to the ticked rooms, or open it back up to all results. */
  toggleShowOnlySelected(): void {
    this.showOnlySelected.update((only) => !only);
    this.refreshSearchResultMessage();
    // Markers are created imperatively, so the computed change alone repaints
    // nothing on the map itself.
    this.renderMarkers();
  }

  /** The banner for a search that returned rooms, true for the filters as they are. */
  private searchResultMessage(): string {
    // The two read filters are asked first because they are the banners that
    // can be true with nothing on the map: the other two describe what IS
    // plotted, and «Όλα τα αποτελέσματα εμφανίζονται στον χάρτη» over an empty
    // map is a lie the user can see through.
    if (this.amenityFilterHidesEverything()) {
      return SEARCH_AMENITY_FILTERED_MESSAGE;
    }
    if (this.comparableFilterHidesEverything()) {
      return SEARCH_COMPARABLE_FILTERED_MESSAGE;
    }
    return this.showOnlySelected() ? SEARCH_FILTERED_MESSAGE : SEARCH_PLOTTED_MESSAGE;
  }

  /**
   * Keep a search-result banner true when the filters change under it.
   *
   * Rewrites the banner ONLY when it is one of this component's own four
   * search-result texts. Every other banner — the restored-search one, the
   * "run a search" prompt — describes something the filters have no bearing
   * on, and overwriting those would lose what they say.
   *
   * Called from both filters that can empty a screen full of results: the map
   * filter (synchronously, from its own click) and the facility checkboxes
   * (after their re-read, which is the only moment their effect is known).
   */
  private refreshSearchResultMessage(): void {
    const current = this.message();
    if (
      current === SEARCH_PLOTTED_MESSAGE
      || current === SEARCH_FILTERED_MESSAGE
      || current === SEARCH_AMENITY_FILTERED_MESSAGE
      || current === SEARCH_COMPARABLE_FILTERED_MESSAGE
    ) {
      this.message.set(this.searchResultMessage());
    }
  }

  formatEuro(value: number): string {
    return new Intl.NumberFormat("el-GR", {
      style: "currency",
      currency: "EUR",
      maximumFractionDigits: 0,
    }).format(value || 0);
  }

  /**
   * The three counted phrases the page renders, each built whole in TS.
   *
   * Greek inflects the noun with the number, so "N " + noun cannot be
   * concatenated in the template the way English allows — «1 ανταγωνιστές»
   * is wrong in a way «1 competitors» is not. Same shape as
   * SetupProgressService.trackingLabel(): one method, one sentence out.
   * Zero takes the plural, which is what Greek does.
   */
  competitorCountLabel(): string {
    // Hotels, not rows (spec §4.7): the same hotel under two packages is one
    // competitor, as it is one «Καταλύματα» in the summary box.
    const count = new Set(this.competitors().map((competitor) => competitor.property_id || competitor.hotel_name)).size;
    return count === 1 ? "1 ανταγωνιστής" : `${count} ανταγωνιστές`;
  }

  /**
   * «Καταλύματα N · Καταγραφές M · Ίδια κατηγορία K · Παρόμοια L» (spec §4.7).
   * Labels before numbers, so no noun has to agree with a count. The category
   * split is left out when the API predates it rather than shown as zeros.
   */
  marketCountsLine(summary: MarketSummary): string {
    const parts = [`Καταλύματα ${summary.total_hotels}`, `Καταγραφές ${summary.total_records}`];
    if (typeof summary.same_category_hotels === "number") {
      parts.push(`Ίδια κατηγορία ${summary.same_category_hotels}`);
    }
    if (typeof summary.similar_hotels === "number") {
      parts.push(`Παρόμοια ${summary.similar_hotels}`);
    }
    return parts.join(" · ");
  }

  roomsLeftLabel(roomsLeft: number): string {
    return roomsLeft === 1 ? "1 διαθέσιμο δωμάτιο" : `${roomsLeft} διαθέσιμα δωμάτια`;
  }

  toggleAmenity(amenity: string): void {
    this.selectedAmenities.update((selected) => {
      const next = new Set(selected);
      if (next.has(amenity)) {
        next.delete(amenity);
      } else {
        next.add(amenity);
      }
      return next;
    });
    void this.reloadActiveJob(AMENITY_FILTER_FAILED_MESSAGE);
  }

  clearAmenities(): void {
    this.selectedAmenities.set(new Set());
    void this.reloadActiveJob(AMENITY_FILTER_FAILED_MESSAGE);
  }

  /**
   * «Μόνο συγκρίσιμα» on or off (owner decision 2026-09-30).
   *
   * Like the facility checkboxes, a filter the server applies while READING
   * (`comparable_only`), so the job on screen is read again. The facility
   * list is read with the same scope first: it can lose the facility only
   * non-comparable rooms had, and pruning it after the job's re-read went out
   * would leave the boxes and the rows disagreeing (the same order
   * onRoomTypeChange keeps).
   */
  async toggleComparableOnly(): Promise<void> {
    this.comparableOnly.update((only) => !only);
    await this.loadAmenities();
    await this.reloadActiveJob("Δεν ήταν δυνατή η εφαρμογή του φίλτρου συγκρίσιμων.");
  }

  /**
   * The empty state's one button: undo the read filter that emptied the
   * screen — clear the facilities, or switch «Μόνο συγκρίσιμα» off. Both
   * re-read the job on screen.
   */
  onEmptyResultAction(): void {
    const kind = this.emptyResultReason()?.kind;
    if (kind === "amenities") {
      this.clearAmenities();
    } else if (kind === "comparable" && this.comparableOnly()) {
      void this.toggleComparableOnly();
    }
  }

  /**
   * Re-read the job on screen with the read filters as they now stand: the
   * facility checkboxes and «Μόνο συγκρίσιμα».
   *
   * Both are filters the BACKEND applies while reading the job's rows, so
   * until those rows are fetched again nothing on screen changes: ticking a
   * facility moved a signal and left the map, the market strip and the cards
   * all showing the unfiltered job. Routed through loadMarkers rather than a
   * bare marker read so the three stay one result set — it re-runs the summary
   * and (when it is on) the match read with the same params.
   *
   * The generation is read HERE, the way refreshRoomMatches defaults it: the
   * click starts no chain of its own, it joins whichever chain owns the screen,
   * so a Find pressed while this read is in flight still drops the whole tail.
   * Bumping the generation instead would cancel a restore that has not yet
   * published its job id — and this would then find nothing to re-read.
   *
   * One read per toggle, deliberately: the facility list is a dozen checkboxes
   * clicked one at a time, and the only debounce this component has
   * (scheduleMatchRefresh) exists for a slider that fires continuously while it
   * is dragged. Nothing here fires on its own.
   */
  private async reloadActiveJob(failureMessage: string): Promise<void> {
    const scrapeJobId = this.activeScrapeJobId();
    const jobScope = this.activeJobScope();
    // Nothing job-scoped on screen. The next read to run — a Find, the restore
    // chain, the unscoped match read — builds its own params and picks the new
    // selection up by itself.
    if (!scrapeJobId || !jobScope) {
      return;
    }
    const generation = this.chainGeneration;
    try {
      // The job's own machine-readable truth is handed back in: it belongs to
      // the job, not to the read, and loadMarkers would otherwise blank it and
      // take the empty-state explanation down with it.
      await this.loadMarkers(scrapeJobId, generation, jobScope, this.activeResultSummary());
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // The banner is rewritten HERE and nowhere earlier: whether the new
      // selection empties the screen is only known once its rows are back.
      // A signal write after an await, like every other write in this chain.
      this.refreshSearchResultMessage();
    } catch (error) {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // loadMarkers flipped the status to "loading" before its read. Left
      // there, the sidebar waits forever for a read that already failed; sent
      // to "ready" it would show an empty result list, and the empty state
      // would blame the filter for a failure that is not the filter's.
      this.setError(error instanceof Error ? error.message : failureMessage);
    }
  }

  async onRoomTypeChange(roomTypeId: string): Promise<void> {
    // The user just made the choice their own, so the auto-pick notice has
    // nothing left to report (it is also gone on the next navigation, since
    // the signal starts false with the component).
    this.autoPickedRoom.set(false);
    this.selectedRoomId.set(roomTypeId);
    const room = this.selectedOwnedRoom();
    this.selectedRoomType.set(room?.room_type_category || "");
    this.workflow.set("roomTypeId", roomTypeId);
    this.workflow.set("roomTypeCategory", this.selectedRoomType());
    this.persistSelectedRoomType();
    // The facility ticks used to be wiped here, and nothing re-read the rows
    // they filter: the boxes went blank while the screen kept showing the
    // FILTERED (empty) result set, so a job that really did write rows
    // answered «Δεν βρέθηκαν συγκρίσιμα δωμάτια» — the exact lie the amenity
    // empty state exists to prevent, walked back into through the room
    // dropdown. The clear is gone rather than paired with a re-read: a ticked
    // facility is the user's own read filter and nothing about a different
    // room makes it wrong, so wiping it was both a surprise and a wasted read.
    //
    // loadAmenities still prunes the selection to the options it hands back,
    // and the rows are re-read right after it, so the boxes and the rows can
    // never disagree.
    //
    // The re-read is unconditional now (owner decision 2026-09-30): every
    // read carries `owned_room_type_id`, and the comparable set is the
    // agent's verdict for ONE room — another room is another set over the
    // same job, so the markers, the summary and the match list all change.
    // loadMarkers then runs the agent by itself for this room when the job
    // has no verdict for it yet (autoRunAgentMatching).
    await this.loadAmenities();
    await this.reloadActiveJob("Δεν ήταν δυνατή η φόρτωση των συγκρίσιμων για αυτό το δωμάτιο.");
  }

  /**
   * Sync the room selection to the backend.
   *
   * "Match my room" requires the persisted `selected_room_type_category` (GET
   * /competitors match_room=true 404s without it). The old comment here also
   * claimed nothing else writes it "since the onboarding page was unrouted" —
   * stale since Round 3: the wizard's room step (setup-room-step.component)
   * writes it on every ordinary onboarding, through the same service call
   * below.
   *
   * Routed through OnboardingService.selectRoomType rather than a raw PUT
   * (Εκκρεμότητα 8, owner decision 2026-08-23): the service invalidates the
   * shared /me cache that every page and the setup guard read. A raw PUT left
   * the database holding the new room while the next /me reader was served a
   * cache still naming the old one. Best effort — matching simply stays
   * unavailable if it fails.
   */
  private persistSelectedRoomType(): void {
    if (!this.ownedPropertyId || !this.selectedRoomType()) {
      return;
    }
    void this.onboarding
      .selectRoomType(this.ownedPropertyId, this.selectedRoomType())
      .catch(() => undefined);
  }

  toggleCompetitor(key: string): void {
    this.selectedKeys.update((selected) => {
      const next = new Set(selected);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
    // User-initiated: arm the guard so the load/restore chain's own writes
    // (still possibly in flight) stop clobbering this selection.
    this.userTouchedSelection.set(true);
    this.persistCompetitorSelection();
    this.renderMarkers();
  }

  async findCompetitors(): Promise<void> {
    if (!this.ownedPropertyId) {
      this.setError("Ολοκληρώστε τη ρύθμιση του καταλύματος πριν αναζητήσετε ανταγωνιστές.");
      return;
    }
    const baselineRoom = this.selectedOwnedRoom();
    if (!baselineRoom || !this.selectedRoomType()) {
      this.setError("Επιλέξτε το δωμάτιό σας πριν αναζητήσετε ανταγωνιστές.");
      return;
    }
    if (!this.filters().destination.trim()) {
      this.setError("Λείπει η τοποθεσία του καταλύματος. Συνδεθείτε ξανά δηλώνοντας όνομα καταλύματος και τοποθεσία.");
      return;
    }
    if (isPastLocalDate(this.filters().check_in)) {
      this.setError("Η άφιξη δεν μπορεί να είναι στο παρελθόν.");
      return;
    }
    if (this.filters().check_out <= this.filters().check_in) {
      this.setError("Η αναχώρηση πρέπει να είναι μετά την άφιξη.");
      return;
    }

    // Supersede every chain still in flight BEFORE clearing state, so none of
    // their queued writes can land on this search. Nothing else bumps the
    // generation, and the button is [disabled] while actionStatus is
    // "loading", so this run cannot be superseded in turn — its own late
    // writes (message/actionStatus below) need no guard.
    this.chainGeneration += 1;
    const generation = this.chainGeneration;
    this.actionStatus.set("loading");
    this.status.set("loading");
    this.message.set("Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών. Τα αποτελέσματα θα εμφανιστούν μόλις ολοκληρωθεί.");
    this.saveMessage.set("");
    this.saveHasError.set(false);
    this.error.set("");
    this.setCompetitors([]);
    this.activeResultSummary.set(null);
    this.selectedKeys.set(new Set());
    // A fresh search legitimately starts selection over -- disarm the guard
    // so this run's own load chain (loadMarkers below) can restore/apply
    // normally instead of finding itself blocked by a stale prior touch.
    this.userTouchedSelection.set(false);
    this.activeScrapeJobId.set("");
    // No job-scoped result on screen any more, so no job stay either: reads
    // between here and the new job's own load must fall back to the filters.
    this.activeJobScope.set(null);
    this.didFitMarkers = false;
    this.marketSummary.set(null);
    this.marketSummaryStatus.set("idle");
    this.matchedCompetitors.set([]);
    this.hotelMatchScores.clear();
    this.matchError.set("");
    this.matchStatus.set("idle");
    this.clearMarkers();
    // No deep_crawl_max_items any more: the server derives it from its own
    // hotel cap, which also counts the nearby areas (spec §3.1).
    const resultLimit = this.readBoundedNumber(this.filters().limit, 40, 1, 80);
    // «Εκκίνηση…» from the click itself: the POST alone can take a moment.
    this.scrapeProgress.set(null);
    this.scrapeInFlight.set(true);
    // The block renders under the Find button, which sits at the foot of a
    // long, scrolling form: bring it into view once it is on the page. Centred,
    // because it grows by a line once the stage-2 counts arrive.
    window.setTimeout(() => this.scrapeProgressBlock?.nativeElement.scrollIntoView({ block: "center" }), 0);

    try {
      const job = await this.api.post<ScrapeJobResponse>("/api/v1/scrape-jobs/", {
        owned_property_id: this.ownedPropertyId,
        job_type: "competitor_search",
        room_type_category: this.selectedRoomType(),
        destination: this.filters().destination,
        raw_destination: this.rawDestination || this.filters().destination,
        check_in: this.filters().check_in,
        check_out: this.filters().check_out,
        adults: Number(this.filters().adults),
        children: Number(this.filters().children),
        rooms: Number(this.filters().rooms),
        // Spec §3.1: the radius centre is resolved by the server, never sent from here.
        nearby_destinations: this.nearbyDestinations(),
        radius_km: this.readBoundedNumber(this.filters().radius_km, DEFAULT_RADIUS_KM, MIN_RADIUS_KM, MAX_RADIUS_KM),
        filters_payload: {
          limit: resultLimit,
          match_mode: "catalog_room_name",
          room_type: baselineRoom.room_type,
        },
      });

      this.trackScrapeProgress(job);
      const completedJob = await this.pollScrapeJob(job.id);
      if (this.destroyed) {
        return;
      }
      this.workflow.set("lastCompetitorJobId", completedJob.id);
      await this.loadAmenities();
      // The stay comes from the job the backend actually ran, not from the
      // date inputs: those stay editable while a search runs, and they still
      // held a restored job's window in the live failure this fixes.
      await this.loadMarkers(completedJob.id, generation, {
        checkIn: completedJob.check_in,
        checkOut: completedJob.check_out,
        roomTypeCategory: completedJob.room_type_category || this.selectedRoomType(),
        radiusKm: completedJob.radius_km ?? null,
      }, completedJob.result_summary);
      // Filter-aware, for both filters that survive a search. This run cleared
      // the ticked cards, so with the map filter on the map is empty — saying
      // the results are all on it would be a lie the user can see through. The
      // facility checkboxes survive too and empty the screen from the backend
      // side, which is why they are asked about before the sentence below:
      // that one blames the own-property exclusion, and a filtered-out result
      // set was not excluded, it was filtered.
      this.message.set(this.competitors().length || this.amenityFilterHidesEverything() || this.comparableFilterHidesEverything()
        ? this.searchResultMessage()
        : "Δεν βρέθηκαν δωμάτια ανταγωνιστών αφού εξαιρέθηκε το δικό σας κατάλυμα.");
      this.actionStatus.set("ready");
    } catch (error) {
      // The POST itself may be what failed, before any poll could stop it.
      this.stopScrapeProgress();
      if (this.destroyed) {
        return;
      }
      this.setError(error instanceof Error ? error.message : "Δεν ήταν δυνατή η εκτέλεση της αναζήτησης ανταγωνιστών.");
      this.actionStatus.set("error");
    }
  }

  async saveTrackedCompetitors(): Promise<void> {
    this.saveMessage.set("");
    this.saveHasError.set(false);
    if (!this.ownedPropertyId || !this.selectedRoomType()) {
      this.showSaveMessage("Επιλέξτε κατάλυμα και τύπο δωματίου πριν αποθηκεύσετε ανταγωνιστές προς παρακολούθηση.", true);
      return;
    }
    if (!this.selectedKeys().size) {
      this.showSaveMessage("Επιλέξτε τουλάχιστον έναν ανταγωνιστή πριν την αποθήκευση.", true);
      return;
    }
    const competitors = this.competitors()
      .filter((competitor) => this.selectedKeys().has(this.markerKey(competitor)) && competitor.property_id)
      .map((competitor) => ({
        property_id: competitor.property_id,
        room_package_id: competitor.room_package_id || null,
      }));
    if (!competitors.length) {
      this.showSaveMessage("Στις επιλεγμένες καταχωρίσεις λείπει το αναγνωριστικό καταλύματος, οπότε δεν μπορούν ακόμη να αποθηκευτούν.", true);
      return;
    }

    try {
      const response = await this.api.post<{ saved_count: number }>("/api/v1/tracked/competitors", {
        owned_property_id: this.ownedPropertyId,
        room_type_category: this.selectedRoomType(),
        competitors,
      });
      this.showSaveMessage(this.trackedSavedMessage(response.saved_count), false);
      // Re-feed the checklist from the same source the load path uses: the
      // tracking step used to stay untickable until the next map load, because
      // nothing after a save called setTracked. Re-READ rather than count what
      // was just sent — the save keeps existing tracked rooms, so saved_count
      // is not the list length.
      await this.fetchTrackedCompetitors();
    } catch (error) {
      this.showSaveMessage(error instanceof Error ? error.message : "Δεν ήταν δυνατή η αποθήκευση των ανταγωνιστών προς παρακολούθηση.", true);
    }
  }

  /**
   * The save confirmation, built whole for the same reason as the count
   * labels above: Greek inflects the verb AND the noun with the number, so
   * the English `room${s}` trick has no equivalent to splice in.
   */
  private trackedSavedMessage(savedCount: number): string {
    const kept = "Τα ήδη παρακολουθούμενα δωμάτια διατηρήθηκαν.";
    return savedCount === 1
      ? `Προστέθηκε 1 δωμάτιο ανταγωνιστή στην παρακολούθηση. ${kept}`
      : `Προστέθηκαν ${savedCount} δωμάτια ανταγωνιστών στην παρακολούθηση. ${kept}`;
  }

  async signOut(): Promise<void> {
    this.notifications.stop();
    await this.auth.signOut();
    this.workflow.clear();
    await this.router.navigateByUrl("/auth");
  }

  toggleFiltersSidebar(): void {
    this.filtersCollapsed.update((collapsed) => !collapsed);
    window.setTimeout(() => this.map?.resize(), 0);
  }

  private openFiltersSidebar(): void {
    if (this.filtersCollapsed()) {
      this.toggleFiltersSidebar();
    }
  }

  onMatchToggle(enabled: boolean): void {
    this.matchRoomEnabled = enabled;
    if (!enabled) {
      this.matchedCompetitors.set([]);
      this.hotelMatchScores.clear();
      this.matchError.set("");
      this.matchStatus.set("idle");
      this.matchSource.set("statistical");
      this.reassessNotice.set("");
      // Rebuild popups without the match row.
      this.renderMarkers();
      return;
    }
    void this.refreshRoomMatches();
  }

  onMinScoreChange(value: number): void {
    this.minMatchScore = this.readBoundedNumber(String(value), 0, 0, 100);
    // The range input fires continuously while dragging; debounce the refetch.
    this.scheduleMatchRefresh();
  }

  onMatchSortChange(value: CompetitorSort): void {
    this.matchSort = value;
    void this.refreshRoomMatches();
  }

  matchScoreFor(hotelName: string): number | null {
    const score = this.hotelMatchScores.get(hotelName);
    return typeof score === "number" ? score : null;
  }

  matchChipClass(score: number | null | undefined): string {
    if (score == null) {
      return "match-chip-low";
    }
    if (score >= 75) {
      return "match-chip-high";
    }
    if (score >= 45) {
      return "match-chip-mid";
    }
    return "match-chip-low";
  }

  formatScore(score: number | null | undefined): string {
    return score == null ? "—" : `${Math.round(score)}%`;
  }

  roomGroupsFor(hotel: Competitor): RoomPlanGroup[] {
    return this.matchedRoomGroups().get(hotel) ?? [];
  }

  /**
   * The price the guest actually sees for a package (spec §5): the plan's
   * discounted per-night price, falling back to the main column when the plan
   * carries no discounted one (or predates the rate-plan columns).
   */
  effectivePlanPrice(pkg: CompetitorPackage): number {
    return pkg.rate_plan?.discounted_price_per_night_eur ?? pkg.price_per_night_eur;
  }

  /**
   * The ONE format for every price on a matched card — the header min/max,
   * the room ranges and the plan rows: whole euros drop the cents (110 €),
   * anything fractional keeps exactly two (87,50 €). One card must never mix
   * a rounded 88 € with the 87,50 € it stands for, and two plans a few cents
   * apart must never LOOK identical.
   */
  formatExactEuro(value: number): string {
    const amount = value || 0;
    const decimals = Number.isInteger(amount) ? 0 : 2;
    return new Intl.NumberFormat("el-GR", {
      style: "currency",
      currency: "EUR",
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(amount);
  }

  /** The two cancellation classes worth a chip; anything unknown gets none rather than a guess. */
  cancellationChipLabel(type: string | null | undefined): string {
    if (type === "free_cancellation") {
      return "Δωρεάν ακύρωση";
    }
    if (type === "non_refundable") {
      return "Μη επιστρέψιμη";
    }
    return "";
  }

  plansExpanded(hotelIndex: number, group: RoomPlanGroup): boolean {
    return this.expandedPlanKeys().has(this.planKey(hotelIndex, group));
  }

  togglePlans(hotelIndex: number, group: RoomPlanGroup): void {
    const key = this.planKey(hotelIndex, group);
    const next = new Set(this.expandedPlanKeys());
    if (!next.delete(key)) {
      next.add(key);
    }
    this.expandedPlanKeys.set(next);
  }

  // The card's index, not the hotel's name: Competitor rows carry no property
  // id, and one name can legitimately appear twice in a list.
  private planKey(hotelIndex: number, group: RoomPlanGroup): string {
    return `${hotelIndex}|${group.room_type}`;
  }

  /** One group per room_type, rooms in order of first appearance, packages in server order. */
  private buildRoomGroups(packages: CompetitorPackage[]): RoomPlanGroup[] {
    const byRoom = new Map<string, CompetitorPackage[]>();
    for (const pkg of packages) {
      const room = byRoom.get(pkg.room_type);
      if (room) {
        room.push(pkg);
      } else {
        byRoom.set(pkg.room_type, [pkg]);
      }
    }
    return [...byRoom.entries()].map(([room_type, roomPackages]) => ({
      room_type,
      packages: roomPackages,
      rangeLabel: this.roomRangeLabel(roomPackages),
    }));
  }

  /**
   * «από {min} € έως {max} €» over the room's effective prices (spec §5) —
   * only for a room with MORE THAN ONE package and at least some rate-plan
   * data. Old rows (rate_plan null everywhere) keep today's rendering.
   */
  private roomRangeLabel(packages: CompetitorPackage[]): string | null {
    if (packages.length < 2 || !packages.some((pkg) => pkg.rate_plan)) {
      return null;
    }
    const prices = packages.map((pkg) => this.effectivePlanPrice(pkg));
    return `από ${this.formatExactEuro(Math.min(...prices))} έως ${this.formatExactEuro(Math.max(...prices))}`;
  }

  /**
   * Score competitor rooms against the selected room.
   *
   * `generation` defaults to the current chain because the user handlers
   * (toggle, sort, debounced slider) start one right where they are called;
   * loadMarkers passes its own so a superseded load cannot refill the match
   * list that findCompetitors just cleared.
   */
  async refreshRoomMatches(generation: number = this.chainGeneration): Promise<void> {
    if (!this.matchRoomEnabled) {
      return;
    }
    if (!this.ownedPropertyId || !this.selectedRoomType() || !this.filters().destination.trim()) {
      this.matchError.set("Επιλέξτε κατάλυμα και δωμάτιο πριν ενεργοποιήσετε το ταίριασμα δωματίου.");
      this.matchStatus.set("error");
      return;
    }
    this.matchStatus.set("loading");
    this.matchError.set("");
    try {
      const params = this.buildMarkerParams(this.activeScrapeJobId() || undefined);
      // The match list keeps its own row cap (the map and summary read the whole job).
      params.set("limit", "500");
      params.set("match_room", "true");
      params.set("min_match_score", String(this.minMatchScore));
      // `sort=distance` is a 400 without owned_property_id; buildMarkerParams
      // always carries it here, since this method returns above without one.
      params.set("sort", this.matchSort);
      const hotels = await this.api.get<Competitor[]>("/api/v1/competitors/", params);
      if (!this.isCurrentChain(generation)) {
        return;
      }
      this.matchedCompetitors.set(Array.isArray(hotels) ? hotels : []);
      // Fresh rows, fresh cards: an open plans list of the PREVIOUS load must
      // not carry over to a card that merely reuses its position and name.
      this.expandedPlanKeys.set(new Set());
      this.hotelMatchScores = new Map(
        this.matchedCompetitors()
          .filter((hotel) => typeof hotel.best_match_score === "number")
          .map((hotel) => [hotel.hotel_name, hotel.best_match_score as number]),
      );
      this.matchSource.set(this.deriveMatchSource(this.matchedCompetitors()));
      this.matchStatus.set("ready");
      // Refresh popups so they include the newly loaded match scores.
      this.renderMarkers();
    } catch (error) {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      const message = error instanceof Error ? error.message : "Δεν ήταν δυνατή η βαθμολόγηση των δωματίων των ανταγωνιστών.";
      // The probe stays English on purpose: it matches the BACKEND's own
      // error text, which Round 5.1 does not translate (inventory N7).
      this.matchError.set(message.includes("No selected room type")
        ? "Δεν έχει επιλεγεί ακόμη δωμάτιο αναφοράς για το κατάλυμά σας. Ολοκληρώστε την επιλογή δωματίου στον οδηγό ρύθμισης για να ενεργοποιηθεί το ταίριασμα."
        : message);
      this.matchedCompetitors.set([]);
      this.expandedPlanKeys.set(new Set());
      this.hotelMatchScores.clear();
      this.matchSource.set("statistical");
      this.matchStatus.set("error");
    }
  }

  /**
   * Manual agent re-run (spec Α.1): POST once for the (completed job,
   * reference room) pair on screen, then re-read the match list so the
   * scores and the badge reflect whatever the run wrote. Every failure —
   * status "error" or "skipped", the 429 quota, a transport error — only
   * raises the fallback notice (spec Α.5): the list on screen is untouched
   * and keeps working with the scores it already shows.
   */
  async reassessMatches(): Promise<void> {
    const scrapeJobId = this.activeJobId();
    const ownedRoomTypeId = this.selectedRoomId();
    if (!scrapeJobId || !ownedRoomTypeId || this.reassessInFlight()) {
      return;
    }
    this.reassessInFlight.set(true);
    this.reassessNotice.set("");
    try {
      const body: RoomMatchRunRequest = {
        scrape_job_id: scrapeJobId,
        owned_room_type_id: ownedRoomTypeId,
      };
      const run = await this.api.post<RoomMatchRunResponse>("/api/v1/agents/room-matches", body);
      if (run?.status === "completed") {
        await this.refreshRoomMatches();
      } else {
        this.reassessNotice.set(AGENT_UNAVAILABLE_NOTICE);
      }
    } catch {
      this.reassessNotice.set(AGENT_UNAVAILABLE_NOTICE);
    } finally {
      this.reassessInFlight.set(false);
    }
  }

  /**
   * The badge over the match list (spec Α.4). «Εκτίμηση AI» must never be a
   * false claim, so only a non-empty read whose EVERY row says agent earns
   * it. A read that mixes agent rows with statistical ones (or with rows an
   * older API sent without the field) says so — «Εκτίμηση AI (μερική)» —
   * and a read with no agent row at all, the empty list included, is what
   * the scores then are: statistical.
   */
  private deriveMatchSource(hotels: Competitor[]): MatchSourceState {
    const agentRows = hotels.filter((hotel) => hotel.match_source === "agent").length;
    if (!agentRows) {
      return "statistical";
    }
    return agentRows === hotels.length ? "agent" : "partial";
  }

  /**
   * The automatic agent run (owner decision 2026-09-30): the comparison set
   * is the matching agent's verdict, so a completed job shown for a room the
   * agent has not judged yet gets the run by itself — at most ONCE per
   * (job, room) per visit, and a failure (error, skipped, 429, transport) is
   * only the fallback notice, never an automatic retry.
   *
   * "Not judged yet" is read from `/competitors/` with `match_room=true`:
   * `match_source` is only ever "agent" on that read. It asks with
   * `comparable_only=false` so a verdict of "nothing is comparable" still
   * shows up as agent rows instead of an empty list, and without a score
   * floor so the slider cannot hide them. An empty job has nothing to judge.
   *
   * The pair is marked decided BEFORE the await, so the re-read this very run
   * triggers (and any concurrent reload) cannot probe or post it again; it is
   * unmarked only when the probe is abandoned because the screen moved on
   * (another room or another chain), so coming back to it still runs.
   */
  private async autoRunAgentMatching(scrapeJobId: string, generation: number): Promise<void> {
    const ownedRoomTypeId = this.selectedRoomId();
    if (!scrapeJobId || !ownedRoomTypeId || !this.ownedPropertyId) {
      return;
    }
    const pairKey = `${scrapeJobId}|${ownedRoomTypeId}`;
    if (this.autoMatchDecided().has(pairKey)) {
      return;
    }
    this.autoMatchDecided.update((decided) => new Set(decided).add(pairKey));
    const screenMovedOn = () =>
      !this.isCurrentChain(generation)
      || this.selectedRoomId() !== ownedRoomTypeId
      || this.activeScrapeJobId() !== scrapeJobId;
    const forget = () => this.autoMatchDecided.update((decided) => {
      const next = new Set(decided);
      next.delete(pairKey);
      return next;
    });

    let hotels: Competitor[];
    try {
      const params = this.buildMarkerParams(scrapeJobId);
      params.set("comparable_only", "false");
      params.set("match_room", "true");
      params.set("limit", "500");
      const answer = await this.api.get<Competitor[]>("/api/v1/competitors/", params);
      hotels = Array.isArray(answer) ? answer : [];
    } catch {
      // Nothing is known about the verdict; the manual button stays the way in.
      return;
    }
    if (screenMovedOn()) {
      forget();
      return;
    }
    if (!hotels.length || hotels.some((hotel) => hotel.match_source === "agent")) {
      return;
    }

    this.autoMatchPending.set(true);
    this.autoMatchNotice.set("");
    let completed = false;
    try {
      const body: RoomMatchRunRequest = { scrape_job_id: scrapeJobId, owned_room_type_id: ownedRoomTypeId };
      const run = await this.api.post<RoomMatchRunResponse>("/api/v1/agents/room-matches", body);
      completed = run?.status === "completed";
    } catch {
      completed = false;
    } finally {
      this.autoMatchPending.set(false);
    }
    if (!completed) {
      this.autoMatchNotice.set(AGENT_UNAVAILABLE_NOTICE);
      return;
    }
    // The run rewrote the verdicts, so every read of this job is stale: the
    // comparable markers, the summary, the facilities and the match list.
    if (screenMovedOn()) {
      return;
    }
    await this.loadAmenities();
    await this.reloadActiveJob("Δεν ήταν δυνατή η φόρτωση των συγκρίσιμων για αυτό το δωμάτιο.");
  }

  private async loadCurrentUser(): Promise<void> {
    try {
      const currentUser = await this.onboarding.currentUser();
      this.setupProgress.setUser(currentUser);
      this.workflow.bindToSubject(currentUser.auth_subject);
      this.ownedPropertyId = this.workflow.get("ownedPropertyId") || currentUser.owned_property_id || "";
      this.propertyName.set(this.workflow.get("propertyName") || currentUser.property_name || "");
      this.rawDestination = this.workflow.get("rawDestination") || currentUser.raw_destination || "";
      this.selectedRoomType.set(this.workflow.get("roomTypeCategory") || currentUser.selected_room_type_category || "");
      this.selectedRoomId.set(this.workflow.get("roomTypeId"));
      this.setFilter("destination", this.workflow.get("destination") || currentUser.destination || "");

      if (this.ownedPropertyId) {
        this.roomTypes.set(await this.api.get<OwnedPropertyRoomType[]>(
          `/api/v1/onboarding/owned-property/${this.ownedPropertyId}/room-types`,
        ));
        this.ensureSelectedRoom();
      }
    } catch (error) {
      this.setError(error instanceof Error ? error.message : "Δεν ήταν δυνατή η φόρτωση των στοιχείων του λογαριασμού.");
    }
  }

  private ensureSelectedRoom(): void {
    if (!this.roomTypes().length) {
      this.selectedRoomId.set("");
      this.selectedRoomType.set("");
      this.autoPickedRoom.set(false);
      return;
    }
    const storedRoom = this.roomTypes().find((room) => room.id === this.selectedRoomId());
    const categoryRoom = this.roomTypes().find((room) => room.room_type_category === this.selectedRoomType());
    const selected = storedRoom || categoryRoom || this.roomTypes()[0];
    // Neither the stored room nor the stored category matched anything in the
    // catalog, so nobody chose what is about to be selected and persisted —
    // the page picked the first room by itself (the room-step "Αργότερα" skip
    // is the usual way in). Announced rather than silent: the whole market
    // comparison is scoped to this room.
    this.autoPickedRoom.set(!storedRoom && !categoryRoom);
    this.selectedRoomId.set(selected.id);
    this.selectedRoomType.set(selected.room_type_category);
    this.workflow.set("roomTypeId", selected.id);
    this.workflow.set("roomTypeCategory", selected.room_type_category);
    this.persistSelectedRoomType();
  }

  private splitFacilities(value: string): string[] {
    return value
      .split("|")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  private async pollScrapeJob(jobId: string): Promise<ScrapeJobResponse> {
    let consecutiveFailures = 0;
    try {
      for (let attempt = 0; attempt < SCRAPE_POLL_ATTEMPTS; attempt += 1) {
        // Stop polling when the user navigates away — otherwise this loop keeps
        // firing requests (and later mutating destroyed-component state) for up
        // to 30 minutes.
        if (this.destroyed) {
          throw new Error("Η αναζήτηση ανταγωνιστών ακυρώθηκε λόγω πλοήγησης.");
        }
        let job: ScrapeJobResponse;
        try {
          job = await this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${jobId}`);
        } catch (error) {
          // One failed read mid-scrape used to abort the whole wait, show the
          // error banner and re-enable «Εύρεση» while the job kept running.
          // Keep the last shown progress, wait the normal interval and ask
          // again; give up only once the failure run says the server is gone.
          consecutiveFailures += 1;
          if (consecutiveFailures >= SCRAPE_POLL_FAILURE_LIMIT) {
            throw error;
          }
          await new Promise((resolve) => window.setTimeout(resolve, SCRAPE_POLL_INTERVAL_MS));
          continue;
        }
        consecutiveFailures = 0;
        if (job.status === "completed") {
          return job;
        }
        if (job.status === "failed") {
          throw new Error(job.error_message || "Η αναζήτηση ανταγωνιστών απέτυχε.");
        }
        this.trackScrapeProgress(job);
        await new Promise((resolve) => window.setTimeout(resolve, SCRAPE_POLL_INTERVAL_MS));
      }
      throw new Error("Η αναζήτηση ανταγωνιστών εκτελείται ακόμη. Αφήστε τον διακομιστή ανοιχτό και περιμένετε να ολοκληρωθεί.");
    } finally {
      // Completed, failed, timed out or abandoned: the progress and its clock
      // describe a job that is no longer running either way.
      this.stopScrapeProgress();
    }
  }

  /**
   * Take the progress and the clock's start from one job read (spec §4.2).
   *
   * The progress is replaced, not merged: a job retried after a failure starts
   * over, and «Εκκίνηση…» is then the truth. The clock counts from
   * `started_at`, or from `requested_at` while the job is still queued.
   */
  private trackScrapeProgress(job: ScrapeJobResponse): void {
    // A poll answer that lands after ngOnDestroy must not restart the clock
    // interval below: stopScrapeProgress has already run, and nothing would
    // ever clear a timer started now.
    if (this.destroyed) {
      return;
    }
    this.scrapeProgress.set(job.result_summary?.progress ?? null);
    const startedAt = Date.parse(job.started_at || job.requested_at || "");
    if (!Number.isFinite(startedAt)) {
      return;
    }
    this.scrapeClockStartedAt.set(startedAt);
    this.scrapeClockNow.set(Date.now());
    if (!this.scrapeClockTimer) {
      this.scrapeClockTimer = window.setInterval(() => this.scrapeClockNow.set(Date.now()), 1000);
    }
  }

  private stopScrapeProgress(): void {
    if (this.scrapeClockTimer) {
      window.clearInterval(this.scrapeClockTimer);
      this.scrapeClockTimer = null;
    }
    this.scrapeInFlight.set(false);
    this.scrapeProgress.set(null);
    this.scrapeClockStartedAt.set(null);
  }

  /**
   * The progress sentence, built whole because Greek inflects the noun with
   * the number (see competitorCountLabel): «σε 1 περιοχή», «βρέθηκε 1».
   */
  private describeScrapeProgress(progress: ScrapeJobProgress | null): string {
    const count = (value: number | null | undefined) => (typeof value === "number" && Number.isFinite(value) ? value : 0);
    switch (progress?.stage) {
      case "scout": {
        const areas = count(progress.destinations_total);
        const found = count(progress.hotels_found);
        return `Στάδιο 1/2 — Αναζήτηση καταλυμάτων σε ${areas} ${areas === 1 ? "περιοχή" : "περιοχές"}… `
          + (found === 1 ? "βρέθηκε 1" : `βρέθηκαν ${found}`);
      }
      case "deep_crawl": {
        const total = count(progress.total);
        return `Στάδιο 2/2 — Τιμές δωματίων: ${count(progress.done)}/${total} ${total === 1 ? "κατάλυμα" : "καταλύματα"}`;
      }
      case "persist":
        return "Αποθήκευση αποτελεσμάτων…";
      default:
        return "Εκκίνηση…";
    }
  }

  /**
   * Load the map/sidebar for one chain.
   *
   * `generation` is the caller's chain id: every write here belongs to that
   * chain and is skipped once a newer one (a Find click) has taken the screen.
   * A superseded chain is dropped at the entry check below, so this method
   * never starts a read — nor touches the entry state — for one. Callers check
   * too, which saves the call entirely; the entry check is what makes that a
   * property of this method rather than of its two current call sites.
   *
   * `jobStay` is the stay `scrapeJobId` was scraped for, read from the job
   * itself. Both are required: a job-scoped read carrying anything other than
   * its own job's dates matches no stored rows, and typing that as "required"
   * is what stops the next caller from reintroducing it.
   */
  private async loadMarkers(
    scrapeJobId: string,
    generation: number,
    jobScope: JobScope,
    resultSummary?: ScrapeResultSummary | null,
  ): Promise<void> {
    if (!this.isCurrentChain(generation)) {
      return;
    }
    // A re-read of the job already on screen (a facility tick) keeps its
    // markers, and an open popup with them: renderMarkers reconciles them with
    // the new rows below. Any other job starts from an empty map.
    const rereadsJobOnScreen = scrapeJobId === this.activeScrapeJobId();
    this.activeJobScope.set(jobScope);
    this.activeResultSummary.set(resultSummary || null);
    const params = this.buildMarkerParams(scrapeJobId);
    this.status.set("loading");
    this.setCompetitors([]);
    // Chain-initiated: skip if the user has already touched selection this
    // navigation, so a click does not get wiped by the chain's own reset.
    if (!this.userTouchedSelection()) {
      this.selectedKeys.set(new Set());
    }
    if (!rereadsJobOnScreen) {
      this.clearMarkers();
    }
    const nextCompetitors = await this.api.get<CompetitorMapMarker[]>("/api/v1/maps/competitors", params);
    // Find was pressed while this read was in flight: everything below
    // describes a job the user has already moved on from, so drop the whole
    // tail rather than repaint the new search with it.
    if (!this.isCurrentChain(generation)) {
      return;
    }
    this.setCompetitors(Array.isArray(nextCompetitors) ? nextCompetitors : []);
    this.activeScrapeJobId.set(scrapeJobId);
    // The owner's hotel for THIS job (its radius and own Booking price belong
    // to the job). A re-read of the same job changes neither, so it reads nothing.
    if (!rereadsJobOnScreen) {
      void this.loadOwnProperty(scrapeJobId, generation);
    }
    // Chain-initiated: same guard as above.
    if (!this.userTouchedSelection()) {
      this.selectedKeys.set(new Set());
    }
    // Unconditional: the checklist's tracked-competitors step needs an
    // accurate count on every load, regardless of whether a stored selection
    // is about to be restored below or whether any competitors came back.
    const tracked = await this.fetchTrackedCompetitors();
    if (!this.isCurrentChain(generation)) {
      return;
    }
    const restoredSelection = this.restoreStoredCompetitorSelection(scrapeJobId);
    if (!restoredSelection) {
      this.applyTrackedCompetitors(tracked);
    }
    // Only a job NEW to the screen earns a fresh camera fit. A re-read of the
    // job already shown (a facility tick, «Μόνο συγκρίσιμα») changes the
    // rows, not the ground they stand on — resetting here yanked the map away
    // from wherever the user had panned or zoomed it.
    if (!rereadsJobOnScreen) {
      this.didFitMarkers = false;
    }
    this.status.set("ready");
    this.renderMarkers();
    // The strip and match scores describe the same result set as the markers.
    void this.loadMarketSummary(scrapeJobId, generation);
    if (this.matchRoomEnabled) {
      void this.refreshRoomMatches(generation);
    }
    // First load and every room switch alike: a room the agent has not judged
    // for this job gets its one automatic run.
    void this.autoRunAgentMatching(scrapeJobId, generation);
  }

  /** True while `generation` is still the chain that owns the screen. */
  private isCurrentChain(generation: number): boolean {
    return generation === this.chainGeneration;
  }

  /** `generation` is the startup chain this restore belongs to (see ngOnInit). */
  private async restoreLatestCompetitorSearch(generation: number): Promise<void> {
    if (!this.ownedPropertyId || !this.selectedRoomType() || !this.filters().destination) {
      return;
    }

    try {
      const job = await this.findRestorableCompetitorJob();
      // Find pressed while the job lookup was in flight: this restore lost the
      // screen. Its filter/date rewrite below would otherwise drag the running
      // search back to the old job's stay, and its message would claim a
      // restored search over a live one.
      if (!this.isCurrentChain(generation)) {
        return;
      }
      if (!job) {
        // No previous search to show (spec §4.1): the form is the only way
        // forward, so it opens by itself instead of hiding behind «Φίλτρα».
        this.openFiltersSidebar();
        return;
      }

      this.applyRestoredJobFilters(job);
      this.workflow.set("lastCompetitorJobId", job.id);
      this.message.set(
        this.restoredStayIsStale
          ? `Εμφανίζεται η τελευταία ολοκληρωμένη αναζήτησή σας για ${job.check_in} έως ${job.check_out}. `
            + "Οι ημερομηνίες αυτές έχουν παρέλθει, οπότε τα φίλτρα μετακινήθηκαν στο επόμενο διαθέσιμο "
            + "διάστημα — πατήστε «Εύρεση ανταγωνιστών» για ζωντανές τιμές."
          : "Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών. Χρησιμοποιήστε την «Εύρεση ανταγωνιστών» μόνο όταν θέλετε φρέσκα ζωντανά δεδομένα.",
      );
      await this.loadMarkers(job.id, generation, {
        checkIn: job.check_in,
        checkOut: job.check_out,
        roomTypeCategory: job.room_type_category || this.selectedRoomType(),
        radiusKm: job.radius_km ?? null,
      }, job.result_summary);
    } catch {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // This restore still owns the screen and just failed. loadMarkers had
      // already flipped status to "loading" before its read, and leaving it
      // there left the sidebar promising a scrape that had failed and the map
      // saying "Searching..." forever. Idle is what is true: nothing is
      // running, nothing was restored, and the message below is the way out.
      this.status.set("idle");
      this.setCompetitors([]);
      this.activeResultSummary.set(null);
      // No job-scoped result on screen, so no job id or job stay either: a
      // later read must not carry the failed job's identity.
      this.activeScrapeJobId.set("");
      this.activeJobScope.set(null);
      this.message.set(START_SEARCH_MESSAGE);
    }
  }

  private async findRestorableCompetitorJob(): Promise<ScrapeJobResponse | null> {
    const storedJobId = this.workflow.get("lastCompetitorJobId");
    if (storedJobId) {
      try {
        const storedJob = await this.api.get<ScrapeJobResponse>(`/api/v1/scrape-jobs/${storedJobId}`);
        if (this.canRestoreJob(storedJob)) {
          this.setupProgress.setJobs([storedJob]);
          return storedJob;
        }
      } catch {
        this.workflow.set("lastCompetitorJobId", null);
      }
    }

    try {
      const jobs = await this.api.get<ScrapeJobResponse[]>("/api/v1/scrape-jobs/", new URLSearchParams({ limit: "50" }));
      this.setupProgress.setJobs(jobs);
      return jobs.find((job) => this.canRestoreJob(job)) || null;
    } catch {
      return null;
    }
  }

  private canRestoreJob(job: ScrapeJobResponse): boolean {
    return job.status === "completed"
      && job.job_type === "competitor_search"
      && (job.scrape_runs_count > 0 || Boolean(job.result_summary))
      && Boolean(job.owned_property_id)
      && job.owned_property_id === this.ownedPropertyId
      && Boolean(job.room_type_category)
      && areComparableRoomCategories(job.room_type_category, this.selectedRoomType());
  }

  private applyRestoredJobFilters(job: ScrapeJobResponse): void {
    // The job's own dates may already have passed, and the backend refuses a
    // past check-in — replaying them verbatim would leave every action
    // failing validation. Roll the window forward, keeping its length.
    const stay = ensureFutureStay(job.check_in, job.check_out);
    this.restoredStayIsStale = stay.checkIn !== job.check_in;
    this.filters.update((filters) => ({
      ...filters,
      destination: job.destination || filters.destination,
      check_in: stay.checkIn,
      check_out: stay.checkOut,
      adults: String(job.adults),
      children: String(job.children),
      rooms: String(job.rooms),
    }));
    this.rawDestination = job.raw_destination || this.rawDestination;

    // The search-size settings travel together (spec §4.1): only a job that
    // actually ran with nearby areas restores its limit, areas and radius. A
    // job answering null (pre-Round 6, no backfill) or an empty list (a search
    // inherited from such a restore) never chose those values — replaying its
    // limit would silently shrink every later search — so the Round-6 defaults
    // already on the form stay: limit 40, 10 km, and the areas loadNearbyDefaults
    // prefilled before this restore started (ngOnInit orders it that way).
    if (Array.isArray(job.nearby_destinations) && job.nearby_destinations.length > 0) {
      const limit = this.readPositiveFilterValue(job.filters_payload["limit"]);
      if (limit) {
        this.setFilter("limit", limit);
      }
      this.nearbyDestinations.set(this.normalizeNearbyDestinations(job.nearby_destinations));
      const radius = this.readPositiveFilterValue(job.radius_km);
      if (radius) {
        this.setFilter("radius_km", radius);
      }
    }

    // A pooled double<->twin restore is valid for reading, but the job's
    // historical exact bucket must not silently replace the room the user has
    // selected today. Exact restores keep the existing synchronization.
    if (job.room_type_category && job.room_type_category === this.selectedRoomType()) {
      this.selectedRoomType.set(job.room_type_category);
      const matchingRoom = this.roomTypes().find((room) => room.room_type_category === job.room_type_category);
      if (matchingRoom) {
        this.selectedRoomId.set(matchingRoom.id);
        this.workflow.set("roomTypeId", matchingRoom.id);
      }
      this.workflow.set("roomTypeCategory", job.room_type_category);
    }
  }

  private readPositiveFilterValue(value: unknown): string {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? String(parsed) : "";
  }

  private readBoundedNumber(value: string, fallback: number, min: number, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(min, Math.min(Math.round(parsed), max));
  }

  private persistCompetitorSelection(): void {
    if (!this.activeScrapeJobId()) {
      return;
    }
    this.workflow.set("lastCompetitorSelection", JSON.stringify({
      jobId: this.activeScrapeJobId(),
      keys: Array.from(this.selectedKeys()),
    }));
  }

  private restoreStoredCompetitorSelection(scrapeJobId: string): boolean {
    const rawSelection = this.workflow.get("lastCompetitorSelection");
    if (!rawSelection) {
      return false;
    }

    try {
      const parsed = JSON.parse(rawSelection) as { jobId?: unknown; keys?: unknown };
      if (parsed.jobId !== scrapeJobId || !Array.isArray(parsed.keys)) {
        return false;
      }
      const availableKeys = new Set(this.competitors().map((competitor) => this.markerKey(competitor)));
      // Chain-initiated: skip if the user has already touched selection this
      // navigation, so this restore does not overwrite their click.
      if (!this.userTouchedSelection()) {
        this.selectedKeys.set(new Set(
          parsed.keys
            .filter((key): key is string => typeof key === "string")
            .filter((key) => availableKeys.has(key)),
        ));
      }
      return true;
    } catch {
      this.workflow.set("lastCompetitorSelection", null);
      return false;
    }
  }

  private scheduleMatchRefresh(): void {
    if (this.matchRefreshTimer) {
      window.clearTimeout(this.matchRefreshTimer);
    }
    this.matchRefreshTimer = window.setTimeout(() => {
      this.matchRefreshTimer = null;
      void this.refreshRoomMatches();
    }, 400);
  }

  private async loadMarketSummary(scrapeJobId: string, generation: number): Promise<void> {
    this.marketSummaryStatus.set("loading");
    try {
      // Same params, row limit included, as the marker read (see
      // JOB_RESULT_READ_LIMIT): the box and the header describe one set.
      const params = this.buildMarkerParams(scrapeJobId);
      const summary = await this.api.get<MarketSummary>("/api/v1/market/summary", params);
      // A superseded chain's strip would describe the previous job while the
      // new search runs — findCompetitors cleared it on purpose.
      if (!this.isCurrentChain(generation)) {
        return;
      }
      this.marketSummary.set(summary);
      this.marketSummaryStatus.set("ready");
    } catch {
      if (!this.isCurrentChain(generation)) {
        return;
      }
      // The strip is supplementary; a failed summary never blocks the map.
      this.marketSummary.set(null);
      this.marketSummaryStatus.set("error");
    }
  }

  private async loadAmenities(): Promise<void> {
    if (!this.filters().destination || !this.selectedRoomType()) {
      this.amenities.set(COMMON_AMENITIES);
      return;
    }
    const params = new URLSearchParams({
      destination: this.filters().destination,
      room_type_category: this.selectedRoomType(),
      check_in: this.filters().check_in,
      check_out: this.filters().check_out,
      adults: this.filters().adults,
      children: this.filters().children,
      rooms: this.filters().rooms,
      limit: "200",
    });
    // The facilities offered are those of the rooms the reads will show.
    this.appendComparableScope(params);
    try {
      const options = await this.api.get<string[]>("/api/v1/market/amenities", params);
      const normalizedOptions = this.normalizeAmenityOptions(options);
      this.amenities.set(normalizedOptions.length ? normalizedOptions : COMMON_AMENITIES);
      // Selection is held as backend VALUES, so it is pruned against values.
      const available = new Set(this.amenities().map((option) => option.value));
      this.selectedAmenities.update((selected) =>
        new Set(Array.from(selected).filter((amenity) => available.has(amenity))),
      );
    } catch {
      this.amenities.set(COMMON_AMENITIES);
    }
  }

  private normalizeAmenityOptions(options: string[]): AmenityOption[] {
    const rawText = options
      .flatMap((option) => this.splitFacilities(option))
      .join("|")
      .toLocaleLowerCase("el-GR");
    // Matching happens on VALUES throughout: the scraped facility text is the
    // backend's vocabulary, and the Greek label plays no part in it.
    const matched = Array.from(new Set(FACILITY_MATCHERS
      .filter((facility) => facility.terms.some((term) => rawText.includes(term.toLocaleLowerCase("el-GR"))))
      .map((facility) => facility.value)));
    const ordered = [
      ...matched
        .map((value) => AMENITY_BY_VALUE.get(value))
        .filter((option): option is AmenityOption => Boolean(option)),
      ...COMMON_AMENITIES.filter((option) => !matched.includes(option.value)),
    ];
    return ordered.slice(0, 12);
  }

  /**
   * Fetches tracked competitors and feeds SetupProgressService. Runs on
   * EVERY map load, independent of applyTrackedCompetitors below: the
   * checklist's tracked-competitors step must reflect reality even when a
   * stored selection means the fetched list is never applied to the cards.
   */
  private async fetchTrackedCompetitors(): Promise<TrackedCompetitorListResponse | null> {
    if (!this.ownedPropertyId || !this.selectedRoomType()) {
      return null;
    }
    try {
      const params = new URLSearchParams({
        owned_property_id: this.ownedPropertyId,
        room_type_category: this.selectedRoomType(),
      });
      const tracked = await this.api.get<TrackedCompetitorListResponse>("/api/v1/tracked/competitors", params);
      this.setupProgress.setTracked(tracked);
      return tracked;
    } catch {
      return null;
    }
  }

  private applyTrackedCompetitors(tracked: TrackedCompetitorListResponse | null): void {
    // Chain-initiated: both writes below skip if the user has already
    // touched selection this navigation, so this apply does not overwrite
    // their click.
    if (!tracked || !this.competitors().length) {
      if (!this.userTouchedSelection()) {
        this.selectedKeys.set(new Set());
      }
      return;
    }
    const trackedPropertyIds = new Set(tracked.competitors.map((competitor) => competitor.property_id));
    const trackedPackageIds = new Set(
      tracked.competitors
        .map((competitor) => competitor.room_package_id)
        .filter((id): id is string => Boolean(id)),
    );
    if (!this.userTouchedSelection()) {
      this.selectedKeys.set(new Set(
        this.competitors()
          .filter((competitor) =>
            Boolean(competitor.room_package_id && trackedPackageIds.has(competitor.room_package_id))
            || Boolean(competitor.property_id && trackedPropertyIds.has(competitor.property_id)),
          )
          .map((competitor) => this.markerKey(competitor)),
      ));
    }
  }

  private buildMarkerParams(scrapeJobId?: string): URLSearchParams {
    const filters = this.filters();
    // Reads about a specific job must use THAT job's stay, not the filters:
    // the filters hold the window for the NEXT search (a restored past stay
    // rolled forward, or dates the user has since typed), which matches none
    // of the job's stored rows.
    const jobScope = scrapeJobId ? this.activeJobScope() : null;
    const stay = jobScope || {
      checkIn: filters.check_in,
      checkOut: filters.check_out,
    };
    const params = new URLSearchParams({
      destination: filters.destination,
      check_in: stay.checkIn,
      check_out: stay.checkOut,
      adults: filters.adults,
      children: filters.children,
      rooms: filters.rooms,
      room_type_category: jobScope?.roomTypeCategory || this.selectedRoomType(),
      // A ROW limit on the API side, not a hotel count: «Πλήθος ανταγωνιστών»
      // (the form's limit) sized the scrape, and reading it back as rows showed
      // a handful of a Round 6 job's hotels (each has several packages).
      limit: JOB_RESULT_READ_LIMIT,
    });
    if (this.ownedPropertyId) {
      params.set("owned_property_id", this.ownedPropertyId);
    }
    if (scrapeJobId) {
      params.set("scrape_job_id", scrapeJobId);
    }
    // Appended to EVERY read, job-scoped or not. It used to be appended only
    // when there was no `scrape_job_id`, which made the facility checkboxes a
    // dead control in the normal flow: every read after a search is job-scoped
    // (markers, market summary, room matching), so the filter reached the
    // backend nowhere the hotelier could see. The backend has always honoured
    // `amenities` on the job-scoped read too — its builder applies the same
    // amenities filter as the snapshot one.
    for (const amenity of this.selectedAmenities()) {
      params.append("amenities", amenity);
    }
    this.appendComparableScope(params);
    return params;
  }

  /**
   * «Μόνο συγκρίσιμα» and the room it is judged against, on every read of the
   * market (competitors, markers, summary, facilities). Always sent, both
   * values: the comparison set belongs to ONE of the owner's rooms, so a
   * switch of room is a different set even over the same job. The legacy
   * `include_similar` is not sent any more.
   */
  private appendComparableScope(params: URLSearchParams): void {
    params.set("comparable_only", String(this.comparableOnly()));
    const ownedRoomTypeId = this.selectedRoomId();
    if (ownedRoomTypeId) {
      params.set("owned_room_type_id", ownedRoomTypeId);
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
  private renderMarkers(): void {
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
    const selectedKeys = this.selectedKeys();
    const wanted = new Map<string, { competitor: CompetitorMapMarker; isSelected: boolean }>();
    for (const competitor of this.visibleCompetitors()) {
      const key = this.hotelKey(competitor);
      const isSelected = selectedKeys.has(this.markerKey(competitor));
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
    const key = this.hotelKey(competitor);
    element.addEventListener("click", () => this.flashCard(key));
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
    const price = this.formatEuro(competitor.price_per_night_eur);
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
    const category = this.categoryLabel(competitor.category_match);
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
   * Read the owner's own hotel for the map (spec §4.3): «Εσείς», its radius
   * circle and its own Booking price, for `scrapeJobId` ("" = no job).
   *
   * Best effort: a failed read (an older API, a hiccup) leaves the map without
   * «Εσείς» and says nothing — the competitors are what the page is for.
   * Coordinates that come back null are a different answer, and the map says
   * so (ownCoordinatesMissing).
   */
  private async loadOwnProperty(scrapeJobId: string, generation: number): Promise<void> {
    if (!this.ownedPropertyId) {
      return;
    }
    const params = new URLSearchParams({ owned_property_id: this.ownedPropertyId });
    if (scrapeJobId) {
      params.set("scrape_job_id", scrapeJobId);
    }
    let own: OwnPropertyMapInfo | null = null;
    try {
      own = this.readOwnProperty(await this.api.get<unknown>("/api/v1/maps/own-property", params));
    } catch {
      own = null;
    }
    // A Find click took the screen while this read was out: its answer
    // describes the job the user has moved on from.
    if (!this.isCurrentChain(generation)) {
      return;
    }
    this.ownProperty.set(own);
    this.renderOwnProperty();
  }

  /** The response as OwnPropertyMapInfo, or null for anything else (a mock's `[]`, an HTML error page). */
  private readOwnProperty(response: unknown): OwnPropertyMapInfo | null {
    if (!response || typeof response !== "object" || Array.isArray(response)) {
      return null;
    }
    const value = response as Record<string, unknown>;
    if (typeof value["display_name"] !== "string") {
      return null;
    }
    const numberOrNull = (field: string) =>
      typeof value[field] === "number" && Number.isFinite(value[field]) ? (value[field] as number) : null;
    const stringOrNull = (field: string) => (typeof value[field] === "string" ? (value[field] as string) : null);
    return {
      display_name: value["display_name"] as string,
      latitude: numberOrNull("latitude"),
      longitude: numberOrNull("longitude"),
      radius_km: numberOrNull("radius_km"),
      price_per_night_eur: numberOrNull("price_per_night_eur"),
      room_type: stringOrNull("room_type"),
      booking_url: stringOrNull("booking_url"),
    };
  }

  /**
   * Put «Εσείς» and its radius circle on the map as ownProperty() stands, or
   * take them off. Imperative like renderMarkers, and reconciled the same way:
   * an existing marker is moved and updated, so its open popup survives.
   */
  private renderOwnProperty(): void {
    const own = this.ownProperty();
    const location = this.ownLocation();
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
        <p class="roomrate-popup__own-line">Η τιμή σας στο Booking: <strong>${this.formatEuro(own.price_per_night_eur)}</strong></p>`
      : "";
    const room = own.room_type ? `
        <p class="roomrate-popup__room">${this.escapeHtml(own.room_type)}</p>` : "";
    const radius = typeof own.radius_km === "number" && own.radius_km > 0
      ? `
        <p class="roomrate-popup__own-line">Ακτίνα ${this.formatRadius(own.radius_km)}</p>`
      : "";
    return `
      <div class="roomrate-popup roomrate-popup--own">
        <div class="roomrate-popup__eyebrow">Εσείς</div>
        <h3>${this.escapeHtml(own.display_name)}</h3>${price}${room}${radius}
      </div>
    `;
  }

  /** «10 km», «7,5 km»: at most one decimal, with the Greek comma. */
  private formatRadius(km: number): string {
    return `${new Intl.NumberFormat("el-GR", { maximumFractionDigits: 1 }).format(km)} km`;
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

  private clearMarkers(): void {
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

  private isValidCoordinate(marker: CompetitorMapMarker): boolean {
    return Number.isFinite(marker.latitude)
      && Number.isFinite(marker.longitude)
      && marker.latitude >= -90
      && marker.latitude <= 90
      && marker.longitude >= -180
      && marker.longitude <= 180;
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

  /** «Ίδια κατηγορία» / «Παρόμοιο» (spec §3.6); empty when an older API sends no match. */
  categoryLabel(match: CategoryMatch | null | undefined): string {
    if (match === "same") {
      return "Ίδια κατηγορία";
    }
    return match === "similar" ? "Παρόμοιο" : "";
  }

  private buildPopupHtml(marker: CompetitorMapMarker): string {
    const propertyType = this.formatPropertyType(marker.property_type || "Κατάλυμα");
    const roomType = marker.room_type || marker.room_type_category || "Δωμάτιο";
    const reviewLabel = marker.review_count === 1 ? "κριτική" : "κριτικές";
    const roomsLeftLabel = marker.rooms_left === 1 ? "δωμάτιο" : "δωμάτια";
    const matchScore = this.matchRoomEnabled ? this.matchScoreFor(marker.hotel_name) : null;
    const matchRow = matchScore === null ? "" : `
          <div>
            <dt>Ταίριασμα δωματίου</dt>
            <dd>${Math.round(matchScore)}%</dd>
          </div>`;
    // Round 6 (spec §4.5): each piece only when the API sent it — an older API
    // or an owner without coordinates leaves the line out, never shows «— km».
    const category = this.categoryLabel(marker.category_match);
    const distance = this.formatDistance(marker.distance_km);
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
        <div class="roomrate-popup__price">${this.formatEuro(marker.price_per_night_eur)} <span>ανά βράδυ</span></div>
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

  private describeSearchWarning(code: unknown): string {
    if (typeof code !== "string") {
      return "";
    }
    if (code === "radius_skipped_no_coordinates") {
      return "Η ακτίνα δεν εφαρμόστηκε: το κατάλυμά σας δεν έχει συντεταγμένες.";
    }
    if (code === "radius_excluded_all") {
      // The radius the job ran with; the form's value for a job that predates it.
      const radiusKm = this.activeJobScope()?.radiusKm
        ?? this.readBoundedNumber(this.filters().radius_km, DEFAULT_RADIUS_KM, MIN_RADIUS_KM, MAX_RADIUS_KM);
      return `Κανένα κατάλυμα δεν βρέθηκε μέσα στην ακτίνα των ${this.formatRadius(radiusKm)}. `
        + "Ελέγξτε τη θέση του καταλύματός σας ή μεγαλώστε την ακτίνα.";
    }
    const nearbyPrefix = "nearby_scout_failed:";
    if (code.startsWith(nearbyPrefix) && code.slice(nearbyPrefix.length).trim()) {
      return `Η αναζήτηση στην περιοχή ${code.slice(nearbyPrefix.length).trim()} δεν ολοκληρώθηκε· `
        + "τα υπόλοιπα αποτελέσματα εμφανίζονται κανονικά.";
    }
    return "";
  }

  /** «0,4 km»: Greek decimal comma, one decimal (spec §4.5); empty when the distance is unknown. */
  formatDistance(km: number | null | undefined): string {
    if (typeof km !== "number" || !Number.isFinite(km)) {
      return "";
    }
    return `${new Intl.NumberFormat("el-GR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(km)} km`;
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

  private resetCompetitorSearchState(): void {
    this.status.set("idle");
    this.actionStatus.set("idle");
    this.setCompetitors([]);
    this.activeResultSummary.set(null);
    this.selectedKeys.set(new Set());
    // Establishing property/room context for this navigation legitimately
    // starts selection over -- disarm the guard so the restore chain that
    // follows (restoreLatestCompetitorSearch) can apply normally.
    this.userTouchedSelection.set(false);
    this.activeScrapeJobId.set("");
    this.activeJobScope.set(null);
    this.didFitMarkers = false;
    this.marketSummary.set(null);
    this.marketSummaryStatus.set("idle");
    this.matchedCompetitors.set([]);
    this.hotelMatchScores.clear();
    this.matchError.set("");
    this.matchStatus.set("idle");
    // Deliberately keep lastCompetitorJobId/lastCompetitorSelection: they feed
    // restoreLatestCompetitorSearch (stale entries clean themselves up there).
    this.message.set(START_SEARCH_MESSAGE);
    this.clearMarkers();
    // «Εσείς» and its circle start over with the page's context; the restore
    // (or ngOnInit, with no job) reads them again.
    this.ownProperty.set(null);
    this.renderOwnProperty();
  }

  private showSaveMessage(message: string, hasError: boolean): void {
    if (this.saveMessageTimeout) {
      window.clearTimeout(this.saveMessageTimeout);
      this.saveMessageTimeout = null;
    }
    this.saveHasError.set(hasError);
    this.saveMessage.set(message);
    if (!hasError) {
      this.saveMessageTimeout = window.setTimeout(() => {
        this.saveMessage.set("");
        this.saveMessageTimeout = null;
      }, 4200);
    }
  }

  private setError(message: string): void {
    this.error.set(message);
    this.status.set("error");
    this.message.set("");
    // A failed re-read of the job on screen kept its markers (see loadMarkers)
    // while the rows went: bring the map back in line with the list.
    this.renderMarkers();
  }
}
