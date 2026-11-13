/**
 * The map page's constants, message texts and shared types.
 *
 * Moved out of map-page.component.ts as they were (the comments explain why
 * each text is a constant): the page, its filters sidebar and its cards all
 * read them, and one module keeps the sentences that name the same control
 * from drifting apart.
 */
import type { CompetitorPackage, MatchSource, RoomMatchRunResponse } from "../../types/market";
import { StayDates, defaultStayDates } from "../../utils/date-defaults";

export type LoadingState = "idle" | "loading" | "ready" | "error";
// What the match source badge can claim (spec Α.4): the two wire
// sources, plus the honest in-between when one read mixes them — some rows
// scored by the agent, the rest (older rows, missing field) statistically.
export type MatchSourceState = MatchSource | "partial";

// `radiusKm`: the radius the job ran with (null before Round 6), for the
// sentence that explains a radius that kept no hotel.
export type JobScope = StayDates & { roomTypeCategory: string; radiusKm: number | null };
// Why a finished search is showing nothing, in the order of the user's own
// ability to act on it. "comparable" carries no count on purpose: it is the
// one reason that is NOT read out of the scrape's result summary (see
// comparableFilterHidesEverything), so there is no honest number to put in it.
// "single_rooms" and "capacity" are the scraper's first two stages since Round
// 6 (spec §3.4), which replaced its room-category cut before storage.
export type EmptyResultReason =
  | { kind: "comparable" }
  | { kind: "single_rooms"; count: number }
  | { kind: "capacity"; count: number }
  | { kind: "remaining-filters"; count: number };

export type MapFilters = {
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
export const MAX_NEARBY_DESTINATIONS = 8;
export const DEFAULT_RADIUS_KM = 10;
export const MIN_RADIUS_KM = 1;
export const MAX_RADIUS_KM = 30;

// A Round 6 scrape (scouts of several areas, up to 120 hotels) runs far longer
// than the old 10 minutes — a live Faliraki search with four nearby areas took
// 10-11 — and Apify can be slower on the day. So the page waits as long as the
// backend's own hard timeout (SCRAPE_JOB_TIMEOUT_SECONDS = 1800): 360 polls of
// 5 s = 30 minutes. It used to give up at 15 on a job that was still running.
export const SCRAPE_POLL_ATTEMPTS = 360;
export const SCRAPE_POLL_INTERVAL_MS = 5000;
// A scrape keeps running server-side whether or not one status read reaches
// the page, so a lone 502 or network blip must not end the wait. Only this
// many failed reads IN A ROW mean the server is really gone.
export const SCRAPE_POLL_FAILURE_LIMIT = 5;
// Rows asked for by the marker and summary reads of a job: the whole job (the
// server caps a Round 6 scrape at 120 hotels), so the map, the header count and
// the summary box all describe the same set (spec §4.7).
export const JOB_RESULT_READ_LIMIT = "1000";

// The map filter's name, defined once and rendered as the control's own label:
// every message that sends the user to that control has to call it exactly
// what the control is called, or the instruction points at nothing.
export const ONLY_SELECTED_FILTER_NAME = "Μόνο τα επιλεγμένα στον χάρτη";
// The single way out of a map the filter has emptied, shared by both messages
// that leave the user looking at one.
export const UNHIDE_INSTRUCTION = "Τσεκάρετε δωμάτια ή απενεργοποιήστε το φίλτρο.";

// Two of the three banners a search that DID return rooms can leave on screen
// (the third is SEARCH_COMPARABLE_FILTERED_MESSAGE below, which needs wording
// defined further down). They are module constants because
// refreshSearchResultMessage has to recognise this component's own banner to
// keep it true as the filters change under it; matching against the very
// constants the writer uses is what stops the two sides from drifting apart.
export const SEARCH_PLOTTED_MESSAGE =
  "Όλα τα αποτελέσματα εμφανίζονται στον χάρτη. Τσεκάρετε όσα θέλετε να παρακολουθείτε.";
export const SEARCH_FILTERED_MESSAGE =
  `Η αναζήτηση επέστρεψε αποτελέσματα, αλλά το φίλτρο «${ONLY_SELECTED_FILTER_NAME}» κρατά στον `
  + `χάρτη μόνο όσα δωμάτια έχετε τσεκάρει. ${UNHIDE_INSTRUCTION}`;
// The map overlay's version of the same state, reached from the other side:
// the user is looking at the map itself rather than at a search banner.
export const FILTER_HIDES_ALL_MESSAGE =
  `Όλα τα αποτελέσματα είναι κρυμμένα από το φίλτρο «${ONLY_SELECTED_FILTER_NAME}». ${UNHIDE_INSTRUCTION}`;

// Spec Α.5: a failed agent run (error, skipped, quota) is never an error of
// the scores on screen — the statistical scores stay and this one
// sentence says why the AI ones did not arrive.
export const AGENT_UNAVAILABLE_NOTICE = "Η εκτίμηση AI δεν είναι διαθέσιμη· εμφανίζεται η στατιστική.";
// Another run (usually the one that starts by itself after the search) is
// still scoring this job: not a failure, so it is said in the quiet style.
export const AGENT_IN_PROGRESS_NOTICE =
  "Το ταίριασμα AI είναι ακόμη σε εξέλιξη· ανανεώστε τη σελίδα σε λίγο για να το δείτε.";
// The automatic run after a search asks again this many times while the
// server answers "in_progress" (each answer comes after a server-side wait of
// up to 150 s), so a large job's verdicts still land without a reload.
export const AGENT_STILL_RUNNING_RETRIES = 2;

/** The notice for an agent run that did not complete: «still running» is not «unavailable». */
export function agentRunNotice(run: RoomMatchRunResponse | null | undefined): string {
  return run?.status === "skipped" && run.skip_reason === "in_progress"
    ? AGENT_IN_PROGRESS_NOTICE
    : AGENT_UNAVAILABLE_NOTICE;
}

// Shown while the matching runs BY ITSELF after a search (owner decision
// 2026-11-13): one pass per job, for every room type of the property.
export const AUTO_MATCH_PENDING_MESSAGE = "Ταίριασμα AI για όλους τους τύπους δωματίων σας σε εξέλιξη…";

// The same three places for the other read filter, «Μόνο συγκρίσιμα» (owner
// decision 2026-09-30: the comparison set is the AI agent's verdict, and the
// rooms it judged non-comparable are hidden by default), built from shared
// parts so they cannot drift. Its name is rendered as the switch's own label.
//
// The title stays «Δεν βρέθηκαν συγκρίσιμα δωμάτια» on purpose: the filter is
// ON by default, so an empty comparable read is not something the user did —
// the sentence is simply true, while "the switch hides everything" would be a
// guess (rows without coordinates also leave a job's read empty).
export const COMPARABLE_FILTER_NAME = "Μόνο συγκρίσιμα";
export const COMPARABLE_HIDES_ALL_TITLE = "Δεν βρέθηκαν συγκρίσιμα δωμάτια";
export const COMPARABLE_UNHIDE_INSTRUCTION = `Απενεργοποιήστε το «${COMPARABLE_FILTER_NAME}» για να δείτε όλα τα καταλύματα που βρέθηκαν.`;
export const COMPARABLE_HIDES_ALL_EXPLANATION =
  "Η αναζήτηση επέστρεψε καταλύματα, αλλά κανένα δεν κρίθηκε συγκρίσιμο με το δωμάτιό σας. "
  + COMPARABLE_UNHIDE_INSTRUCTION;
export const COMPARABLE_HIDES_ALL_MESSAGE = `Κανένα συγκρίσιμο δωμάτιο στον χάρτη. ${COMPARABLE_UNHIDE_INSTRUCTION}`;
export const SEARCH_COMPARABLE_FILTERED_MESSAGE =
  "Η αναζήτηση επέστρεψε αποτελέσματα, αλλά κανένα δεν κρίθηκε συγκρίσιμο με το δωμάτιό σας. "
  + COMPARABLE_UNHIDE_INSTRUCTION;
export const SHOW_ALL_ROOMS_LABEL = "Εμφάνιση όλων";

// The "nothing is running, here is the way out" banner. Written from three
// places (the initial signal, the restore's catch and the per-navigation
// reset), so it is a constant: three copies of one sentence is three chances
// for them to drift, and the e2e negatives hang off this exact wording.
// It names the button by its label, so the two must be translated together.
export const START_SEARCH_MESSAGE =
  "Ρυθμίστε τα φίλτρα και πατήστε «Εύρεση ανταγωνιστών» για να ξεκινήσει ζωντανή αναζήτηση.";
// Shown when ensureSelectedRoom picked the room instead of the user. Rendered
// in the results sidebar, right under the room dropdown it describes.
export const AUTO_PICKED_ROOM_HINT =
  "Επιλέχθηκε αυτόματα το πρώτο δωμάτιο του καταλόγου — αλλάξτε το αν χρειάζεται.";


export const DEFAULT_STAY = defaultStayDates();
