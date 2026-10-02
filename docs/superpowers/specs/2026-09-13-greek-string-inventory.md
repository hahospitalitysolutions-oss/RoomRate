# Greek string inventory — Round 5.1 (2026-09-13)

Shared translation CONTRACT for the four parallel translation agents (auth, map,
pricing, settings+shell). Apply the «Greek (exact, final)» column VERBATIM. Do
not improvise a synonym: if a term is in the glossary, that is the term.

---

## Rules (binding)

1. **Tone = the onboarding wizard** (`frontend/src/app/onboarding/*.ts`):
   professional, formal plural («σας», «Συμπληρώστε», «Δοκιμάστε»), zero emoji,
   no slang, no exclamation marks. Verbs to the user are 2nd-person plural
   imperative; verbs about the system are 1st-person plural («Δεν μπορέσαμε
   να…») or impersonal.
2. **The §7 glossary of `docs/superpowers/specs/2026-07-27-onboarding-ui-design.md`
   (~l.254-262) is mandatory** and is extended below. Hotelier language, not
   statistician language — but numbers, units and percentages stay exact.
3. **One concept = one Greek term everywhere.** The Greek already in the app
   (Rounds 2-4 map copy, checklist labels, wizard labels) WINS over anything
   new. The glossary below already encodes those wins.
4. **Product/brand words stay English**: RoomRate, Booking, Mapbox, Supabase,
   Apify, FastAPI, Angular, UTC, P25/P75 (as chart axis shorthand only),
   AI agent. Currency formatting (`Intl.NumberFormat("el-GR", EUR)`) is
   untouched. Interpolations keep their exact expressions
   (`{{ count }}`, `${response.saved_count}`, `{{ rule.threshold_pct }}`…).
5. **Accessibility**: every `aria-label` becomes a full Greek sentence/phrase,
   never a bare noun. **The map marker aria-label is a REQUIRED ADDITION, not
   just a translation** (the 4.1 a11y gap) — see §"Per file → map, row M-52".
6. **Interpolated counts**: wherever the English concatenates a number with a
   noun, the Greek MUST be built as an explicit singular/plural branch in TS,
   because Greek inflects both noun and adjective. Rows flagged
   **[CONCAT→RESTRUCTURE]** must become a method/computed returning the whole
   sentence, exactly like the existing
   `SetupProgressService.trackingLabel()` and
   `PricingPageComponent.smallSampleNotice()` do today.
7. Skip: console/`console.warn`, comments, `data-testid`, CSS classes, enum and
   API values (`"competitor_search"`, `"any"`, `"drop"`, `"rise"`, `"double"`),
   API paths, amenity/facility MATCHER labels (see Notes §N4).

---

## Term glossary (concept → Greek, alphabetical) — canonical

| Concept (EN) | Greek (canonical) | Source |
|---|---|---|
| Accommodation / property | κατάλυμα | wizard, settings |
| Accommodation name | Όνομα καταλύματος | setup-wizard.spec.ts:978 |
| Adults | Ενήλικες | new, wizard-consistent |
| Alert rule | κανόνας ειδοποιήσεων | settings (delete copy) |
| Average | Μέση τιμή | new |
| Cached | Από προσωρινή μνήμη | new |
| Check-in | Άφιξη | search-step «Άφιξη» |
| Check-out | Αναχώρηση | search-step «Αναχώρηση» |
| Children | Παιδιά | new |
| Competitor(s) | ανταγωνιστής / ανταγωνιστές | checklist, pricing |
| Competitor sample size | Πλήθος ανταγωνιστών | new |
| Confidence | βεβαιότητα | new |
| Destination / Location | Περιοχή (label) / τοποθεσία (prose) | search-step «Περιοχή», wizard «Τοποθεσία» |
| Direction (alert) | Κατεύθυνση | new |
| Facilities / amenities | Παροχές | map empty-state copy |
| Filters (sidebar/button) | Φίλτρα | new |
| Find Competitors | Εύρεση ανταγωνιστών | new |
| Highest | Υψηλότερη τιμή | new |
| Hotels | Καταλύματα | wizard «καταλυμάτων» |
| **Lead time** | **Ημέρες μέχρι την άφιξη** | **§7 MANDATORY** |
| Loading | Φόρτωση / Γίνεται φόρτωση | settings «Φόρτωση καταλύματος» |
| Lowest | Χαμηλότερη τιμή | new |
| Map | Χάρτης | map copy |
| **Market median** | **Διάμεση τιμή αγοράς** | §7 pattern extension |
| **Market P25** | **Χαμηλό εύρος αγοράς** | **§7 MANDATORY** |
| **Market P75** | **Υψηλό εύρος αγοράς** | **§7 MANDATORY** |
| Median (chart/table) | Διάμεσος | pricing chart (existing Greek) |
| Nightly price / per night | ανά βράδυ | chart «€/βράδυ» |
| Notifications | Ειδοποιήσεις | notification-bell.spec.ts:27 |
| Password | Κωδικός πρόσβασης | new |
| Price alerts | Ειδοποιήσεις τιμών | checklist `trackingWhy` |
| Pricing (nav) | Τιμολόγηση | new |
| Recommendation | σύσταση τιμής | checklist step 5 |
| Review score / Review avg | Βαθμολογία / Μέση βαθμολογία | property-step «βαθμολογιών» |
| Room | δωμάτιο | wizard, checklist |
| Room catalog | κατάλογος δωματίων | room-step |
| Room match / matching | Ταίριασμα δωματίου | search-step «Ταίριασμα με το δικό σας δωμάτιο» |
| Room type / category | Τύπος δωματίου / Κατηγορία δωματίου | search-step |
| Rooms (occupancy) | Δωμάτια | new |
| Rooms left | Διαθέσιμα δωμάτια | new |
| **Sample runs** | **Αναζητήσεις που συγκρίθηκαν** | **§7 MANDATORY** |
| Save / Saving | Αποθήκευση / Γίνεται αποθήκευση... | property-step, room-step |
| Schedule / scheduled scraping | Προγραμματισμένες αναζητήσεις | new |
| Search (noun/verb) | αναζήτηση / Αναζήτηση | wizard everywhere |
| Searching (busy) | Γίνεται αναζήτηση | search-step pattern |
| Session expired | Η συνεδρία σας έληξε | new |
| Settings (nav) | Ρυθμίσεις | new |
| Sign in / Sign out | Σύνδεση / Αποσύνδεση | new |
| Stay | διαμονή | wizard |
| **Statistical baseline** | **Στατιστική βάση αναφοράς** | §7 pattern extension |
| Threshold | Όριο μεταβολής | new |
| Track / tracked | παρακολούθηση / παρακολουθούμενοι | checklist, settings |
| Trend (7/30 days) | Τάση (7 ημερών) / Τάση (30 ημερών) | §7 pattern |
| Try again | Δοκιμάστε ξανά | wizard error constants |
| **Your market position** | **Θέση σας στην αγορά** | §7 pattern extension |
| **Your reference price** | **Η τιμή αναφοράς σας** | §7 pattern extension |

---

## Per file

### frontend/src/app/pages/auth-page.component.ts — 47 strings

| # | English (exact) | Where (line / kind) | Greek (exact, final) | e2e assertions |
|---|---|---|---|---|
| A-1 | `Choose a new password` | 32 / template h1 | `Επιλέξτε νέο κωδικό πρόσβασης` | — |
| A-2 | `Sign in to your market workspace` | 32 / template h1 | `Συνδεθείτε στον χώρο εργασίας σας` | — |
| A-3 | `Create your RoomRate account` | 32 / template h1 | `Δημιουργήστε τον λογαριασμό σας στο RoomRate` | — |
| A-4 | `Enter a new password for your RoomRate account.` | 35 / template p | `Ορίστε νέο κωδικό πρόσβασης για τον λογαριασμό σας στο RoomRate.` | — |
| A-5 | `Continue to your live room matching and competitor map.` | 37 / template p | `Συνεχίστε στο ταίριασμα δωματίων και στον χάρτη ανταγωνιστών.` | — |
| A-6 | `Add your accommodation name and location. RoomRate will discover your Booking room catalog automatically.` | 38 / template p | `Συμπληρώστε το όνομα και την τοποθεσία του καταλύματός σας. Το RoomRate εντοπίζει αυτόματα τον κατάλογο δωματίων σας στο Booking.` | — |
| A-7 | `Supabase environment values are missing. Add them in Angular environment configuration before using login.` | 47 / template alert | `Λείπουν οι τιμές περιβάλλοντος του Supabase. Συμπληρώστε τις στη ρύθμιση περιβάλλοντος του Angular πριν από τη σύνδεση.` | — |
| A-8 | `After creating your account, check your inbox and spam folder. Supabase may require email confirmation before sign-in.` | 50 / template alert | `Μετά τη δημιουργία του λογαριασμού, ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων. Το Supabase ενδέχεται να ζητήσει επιβεβαίωση email πριν από τη σύνδεση.` | — |
| A-9 | `Accommodation name` | 55 / label | `Όνομα καταλύματος` | **setup-wizard.spec.ts:124, :964 `getByLabel(/accommodation name/i)` — POSITIVE, MUST CHANGE to `/όνομα καταλύματος/i`** |
| A-10 | `e.g. Rea Hotel` | 56 / placeholder | `π.χ. Rea Hotel` | — |
| A-11 | `Location` | 59 / label | `Τοποθεσία` | **setup-wizard.spec.ts:125 `getByLabel(/location/i)`, :965 `getByLabel(/^location$/i)` — POSITIVE, MUST CHANGE to `/^τοποθεσία$/i`** |
| A-12 | `e.g. Faliraki` | 60 / placeholder | `π.χ. Φαληράκι` | — |
| A-13 | `Email` | 63 / label | `Email` (stays — universal, and the wizard spec keeps it) | setup-wizard.spec.ts:126, :929, :967, :1035 `getByLabel(/email/i)` — unchanged, still matches |
| A-14 | `Password` | 68 / label | `Κωδικός πρόσβασης` | — |
| A-15 | `Hide password` | 84 / aria-label | `Απόκρυψη κωδικού πρόσβασης` | — |
| A-16 | `Show password` | 84 / aria-label | `Εμφάνιση κωδικού πρόσβασης` | — |
| A-17 | `Hide` | 90 / button | `Απόκρυψη` | — |
| A-18 | `Show` | 90 / button | `Εμφάνιση` | — |
| A-19 | `Update password` | 98,169 / button + idle label | `Ενημέρωση κωδικού` | — |
| A-20 | `Sign in` | 98,169 / button + idle label | `Σύνδεση` | — |
| A-21 | `Create account` | 98,169 / button + idle label | `Δημιουργία λογαριασμού` | — |
| A-22 | `Create a new account` | 104 / text-button | `Δημιουργία νέου λογαριασμού` | — |
| A-23 | `I already have an account` | 104 / text-button | `Έχω ήδη λογαριασμό` | — |
| A-24 | `Resend confirmation email` | 114 / text-button | `Επαναποστολή email επιβεβαίωσης` | — |
| A-25 | `Forgot password` | 122 / text-button | `Ξέχασα τον κωδικό μου` | — |
| A-26 | `Password updated. Sign in with your new password.` | 153 / message.set | `Ο κωδικός πρόσβασης ενημερώθηκε. Συνδεθείτε με τον νέο σας κωδικό.` | — |
| A-27 | `Your session expired. Sign in again.` | 155 / message.set | `Η συνεδρία σας έληξε. Συνδεθείτε ξανά.` | — (see S-svc-1: same sentence in api-client.service.ts — keep IDENTICAL) |
| A-28 | `Signing in` | 170 / loadingLabel | `Γίνεται σύνδεση` | — |
| A-29 | `Creating account` | 171 / loadingLabel | `Δημιουργία λογαριασμού σε εξέλιξη` | — |
| A-30 | `Checking workspace` | 172 / loadingLabel | `Έλεγχος χώρου εργασίας` | — |
| A-31 | `Finding your property` | 173 / loadingLabel | `Εντοπισμός του καταλύματός σας` | — |
| A-32 | `Opening map` | 174 / loadingLabel | `Άνοιγμα χάρτη` | — |
| A-33 | `Sending confirmation email` | 175 / loadingLabel | `Αποστολή email επιβεβαίωσης` | — |
| A-34 | `Sending password reset email` | 176 / loadingLabel | `Αποστολή email επαναφοράς κωδικού` | — |
| A-35 | `Updating password` | 177 / loadingLabel | `Ενημέρωση κωδικού πρόσβασης` | — |
| A-36 | `Sign in did not finish. Check your internet connection and confirm the email/password are correct.` | 201 / withUiTimeout | `Η σύνδεση δεν ολοκληρώθηκε. Ελέγξτε τη σύνδεσή σας στο διαδίκτυο και επιβεβαιώστε ότι το email και ο κωδικός είναι σωστά.` | — |
| A-37 | `Account creation did not finish. Check your internet connection and try again.` | 209 / withUiTimeout | `Η δημιουργία λογαριασμού δεν ολοκληρώθηκε. Ελέγξτε τη σύνδεσή σας στο διαδίκτυο και δοκιμάστε ξανά.` | — |
| A-38 | `Account created. Check your inbox and spam folder, confirm your email, then sign in.` | 212 / message.set | `Ο λογαριασμός δημιουργήθηκε. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων, επιβεβαιώστε το email σας και μετά συνδεθείτε.` | — |
| A-39 | `Authentication failed.` | 227 / message.set fallback | `Η ταυτοποίηση απέτυχε.` | — |
| A-40 | `Add your email first.` | 238, 263 / message.set | `Συμπληρώστε πρώτα το email σας.` | — |
| A-41 | `Confirmation email request did not finish. Wait a few minutes before retrying.` | 248 / withUiTimeout | `Το αίτημα για email επιβεβαίωσης δεν ολοκληρώθηκε. Περιμένετε λίγα λεπτά πριν δοκιμάσετε ξανά.` | — |
| A-42 | `Confirmation email requested. Check your inbox and spam folder.` | 250 / message.set | `Ζητήθηκε email επιβεβαίωσης. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων.` | — |
| A-43 | `Could not resend confirmation email.` | 253 / fallback | `Δεν ήταν δυνατή η επαναποστολή του email επιβεβαίωσης.` | — |
| A-44 | `Password reset email sent. Open the link in that email to choose a new password.` | 271 / message.set | `Στάλθηκε email επαναφοράς κωδικού. Ανοίξτε τον σύνδεσμο σε αυτό το email για να ορίσετε νέο κωδικό.` | — |
| A-45 | `Could not request a password reset.` | 274 / fallback | `Δεν ήταν δυνατό το αίτημα επαναφοράς κωδικού.` | — |
| A-46 | `Use a password with at least 8 characters.` | 284 / message.set | `Χρησιμοποιήστε κωδικό με τουλάχιστον 8 χαρακτήρες.` | — |
| A-47 | `Could not update the password.` | 298 / fallback | `Δεν ήταν δυνατή η ενημέρωση του κωδικού πρόσβασης.` | — |
| A-48 | `Connecting to Supabase Auth.` | 394 / setProgress | `Σύνδεση με το Supabase Auth.` | — |
| A-49 | `Creating your Supabase account. If confirmation is enabled, you will need to confirm your email before signing in.` | 395 / setProgress | `Δημιουργία του λογαριασμού σας στο Supabase. Αν είναι ενεργή η επιβεβαίωση, θα χρειαστεί να επιβεβαιώσετε το email σας πριν συνδεθείτε.` | — |
| A-50 | `Loading your RoomRate account from FastAPI.` | 396 / setProgress | `Φόρτωση του λογαριασμού σας RoomRate από το FastAPI.` | — |
| A-51 | `Searching Booking for your accommodation and location. This is a live lookup and can take up to a couple of minutes.` | 397 / setProgress | `Αναζήτηση στο Booking για το κατάλυμα και την τοποθεσία σας. Είναι ζωντανή αναζήτηση και μπορεί να διαρκέσει μερικά λεπτά.` | — |
| A-52 | `Opening your competitor map.` | 398 / setProgress | `Άνοιγμα του χάρτη ανταγωνιστών σας.` | — |
| A-53 | `Requesting a new Supabase confirmation email.` | 399 / setProgress | `Ζητείται νέο email επιβεβαίωσης από το Supabase.` | — |
| A-54 | `Requesting a secure password reset email.` | 400 / setProgress | `Ζητείται ασφαλές email επαναφοράς κωδικού.` | — |
| A-55 | `Saving your new password securely.` | 401 / setProgress | `Αποθήκευση του νέου σας κωδικού με ασφάλεια.` | — |

Already Greek (do not touch): l.189 «Συμπληρώστε όνομα καταλύματος και τοποθεσία…».

---

### frontend/src/app/pages/map-page.component.ts — 46 strings

| # | English (exact) | Where (line / kind) | Greek (exact, final) | e2e assertions |
|---|---|---|---|---|
| M-1 | `RoomRate Market Map` | 124 / eyebrow | `RoomRate — Χάρτης αγοράς` | — |
| M-2 | `Targeted competitor room intelligence` | 125 / h1 fallback | `Στοχευμένη ανάλυση δωματίων ανταγωνιστών` | — |
| M-3 | `Loading` | 128 / header status | `Φόρτωση` | **no-second-click.spec.ts:647 `toContainText("Loading")` — POSITIVE, MUST CHANGE** |
| M-4 | `<n> competitors` **[CONCAT→RESTRUCTURE]** | 128 / `competitors().length + " competitors"` | method `competitorCountLabel()`: `1 ανταγωνιστής` / `${n} ανταγωνιστές` (0 → `0 ανταγωνιστές`) | **map-auto-plot:236,327,348,364; map-page-polish:196,212,230,259,306,324; no-second-click:404,530,586 `getByText("N competitors")` — POSITIVE, MUST CHANGE to `"N ανταγωνιστές"`** |
| M-5 | `Live API` | 129 / header span | `Ζωντανά δεδομένα` | — |
| M-6 | `Pricing` | 130 / routerLink | `Τιμολόγηση` | **live-walkthrough:82 `getByRole("link",{name:"Pricing"})` — POSITIVE, MUST CHANGE** |
| M-7 | `Settings` | 131 / routerLink | `Ρυθμίσεις` | **live-walkthrough:94; map-page-polish:283; setup-room-step-actions:151 — POSITIVE, MUST CHANGE** |
| M-8 | `Sign out` | 133 / button | `Αποσύνδεση` | — |
| M-9 | `Search filters` | 140 / h2 | `Φίλτρα αναζήτησης` | **live-walkthrough:78 `getByText("Search filters")` — POSITIVE, MUST CHANGE** |
| M-10 | `Hide` | 141 / panel-toggle button | `Απόκρυψη` | — |
| M-11 | `Your room catalog` | 144 / label | `Ο κατάλογος δωματίων σας` | — |
| M-12 | `Choose your room` | 146 / option | `Επιλέξτε το δωμάτιό σας` | — |
| M-13 | `RoomRate matches competitor catalog rooms with similar room names and the same normalized category.` | 152 / small | `Το RoomRate ταιριάζει δωμάτια ανταγωνιστών με παρόμοια ονόματα και την ίδια κανονικοποιημένη κατηγορία.` | — |
| M-14 | `Location` | 155 / label | `Περιοχή` | — |
| M-15 | `Market dates` | 158 / section label | `Ημερομηνίες διαμονής` | — |
| M-16 | `From` | 161 / label | `Άφιξη` | — |
| M-17 | `To` | 165 / label | `Αναχώρηση` | — |
| M-18 | `Competitor sample size` | 170 / label | `Πλήθος ανταγωνιστών` | — |
| M-19 | `Most popular facilities` | 176 / span | `Δημοφιλέστερες παροχές` | — |
| M-20 | `Clear` | 177 / button | `Καθαρισμός` | — |
| M-21 | `Searching` | 186 / primary button busy | `Γίνεται αναζήτηση` | **no-second-click:885 `getByRole("button",{name:"Searching"})` — POSITIVE, MUST CHANGE** |
| M-22 | `Find Competitors` | 186 / primary button | `Εύρεση ανταγωνιστών` | **map-auto-plot:234,325; no-second-click:447,632,691,733,775,817,869,965 `getByRole("button",{name:"Find Competitors"})` — POSITIVE, MUST CHANGE** |
| M-23 | `Filters` | 196 / show-filters button | `Φίλτρα` | **live-walkthrough:77; map-auto-plot:233,324; map-page-polish:265,278,296,307; no-second-click:431,631,686,732,773,816,868,960; result-summary-map:88; setup-wizard:1171 — POSITIVE, MUST CHANGE** |
| M-24 | `Mapbox token is missing.` | 202 / map-empty-state | `Λείπει το token του Mapbox.` | — |
| M-25 | `Lowest` | 222 / stat label | `Χαμηλότερη τιμή` | — |
| M-26 | `Highest` | 223 / stat label | `Υψηλότερη τιμή` | — |
| M-27 | `Average` | 224 / stat label | `Μέση τιμή` | — |
| M-28 | `Review avg` | 225, 253 / stat label | `Μέση βαθμολογία` | — |
| M-29 | `Destination market` | 245 / h2 | `Η αγορά της περιοχής` | — |
| M-30 | `<n> records` **[CONCAT→RESTRUCTURE]** | 246 / `{{ summary.total_records }} records` | `1 καταγραφή` / `${n} καταγραφές` — build in TS (`recordsLabel()`) | **no-second-click:412 `getByText("42 records")` POSITIVE MUST CHANGE; :653, :827 `not.toContainText("42 records")` — NEGATIVE, MUST BE RE-DERIVED (see §Notes N1)** |
| M-31 | `Hotels` | 248 / kpi label | `Καταλύματα` | — |
| M-32 | `Min` | 249 / kpi label | `Ελάχιστη` | — |
| M-33 | `Median` | 250 / kpi label | `Διάμεση` | — |
| M-34 | `Average` | 251 / kpi label | `Μέση` | — |
| M-35 | `Max` | 252 / kpi label | `Μέγιστη` | — |
| M-36 | `Add selected to tracked` | 259 / button | `Προσθήκη επιλεγμένων στην παρακολούθηση` | **map-page-polish:236; no-second-click:536,595 `getByRole("button",{name:"Add selected to tracked"})` — POSITIVE, MUST CHANGE** |
| M-37 | `Searching` | 280 / results-empty strong | `Γίνεται αναζήτηση` | — |
| M-38 | `Waiting for the live scrape to finish.` | 281 / results-empty span | `Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.` | **no-second-click:646,706,742,828 POSITIVE MUST CHANGE; :769 and map-page-polish:327 `not.toContainText("Waiting for the live scrape")` — NEGATIVE, MUST BE RE-DERIVED** |
| M-39 | `Room matching` | 326 / section label | `Ταίριασμα δωματίου` | — |
| M-40 | `Match my room` | 330 / checkbox | `Ταίριασμα με το δικό μου δωμάτιο` | — |
| M-41 | `Min match score: {{ minMatchScore }}` | 333 / label | `Ελάχιστο σκορ ταιριάσματος: {{ minMatchScore }}` | — |
| M-42 | `Sort by` | 337 / label | `Ταξινόμηση κατά` | — |
| M-43 | `Best match` | 339 / option | `Καλύτερο ταίριασμα` | — |
| M-44 | `Price` | 340 / option | `Τιμή` | — |
| M-45 | `Scoring rooms` | 345 / strong | `Βαθμολόγηση δωματίων` | — |
| M-46 | `Comparing competitor packages against your room.` | 346 / span | `Σύγκριση των πακέτων των ανταγωνιστών με το δικό σας δωμάτιο.` | — |
| M-47 | `No matches at this threshold` | 349 / strong | `Κανένα ταίριασμα σε αυτό το όριο` | — |
| M-48 | `Lower the minimum match score or run a new competitor search first.` | 350 / span | `Μειώστε το ελάχιστο σκορ ταιριάσματος ή τρέξτε πρώτα νέα αναζήτηση ανταγωνιστών.` | — |
| M-49 | `Review {{ x }} ({{ n }})` | 312 / card-row small | `Βαθμολογία {{ competitor.review_score.toFixed(1) }} ({{ competitor.review_count }})` | — |
| M-50 | `{{ n }} rooms left` **[CONCAT→RESTRUCTURE]** | 313 / small | `1 διαθέσιμο δωμάτιο` / `${n} διαθέσιμα δωμάτια` — TS helper `roomsLeftLabel(n)` | — |
| M-51 | `Room match` | 316 / small label | `Ταίριασμα` | — |
| M-52 | `Review {{ x }} ({{ n }})` in matched-card | 362 / small | `Βαθμολογία {{ hotel.review_score.toFixed(1) }} ({{ hotel.review_count }})` | — |

**TS-side strings (map-page):**

| # | English (exact) | Where (line / kind) | Greek (exact, final) | e2e |
|---|---|---|---|---|
| M-53 | `Set filters and run Find Competitors to start a live scrape.` | 425, 1265, 1772 / message.set (3 sites — keep them ONE shared constant) | `Ρυθμίστε τα φίλτρα και πατήστε «Εύρεση ανταγωνιστών» για να ξεκινήσει ζωντανή αναζήτηση.` | **no-second-click:774 POSITIVE MUST CHANGE; :741 `not.toContainText("Set filters and run Find Competitors")` — NEGATIVE, MUST BE RE-DERIVED** |
| M-54 | `Searching matching competitor rooms...` | 676 / mapEmptyMessage | `Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών...` | **no-second-click:770 `not.toContainText("Searching matching competitor rooms")` — NEGATIVE, RE-DERIVE** |
| M-55 | `Complete property setup before searching competitors.` | 812 / setError | `Ολοκληρώστε τη ρύθμιση του καταλύματος πριν αναζητήσετε ανταγωνιστές.` | — |
| M-56 | `Choose your room before searching competitors.` | 818 / setError | `Επιλέξτε το δωμάτιό σας πριν αναζητήσετε ανταγωνιστές.` | — |
| M-57 | `The property location is missing. Sign in again with accommodation name and location.` | 821 / setError | `Λείπει η τοποθεσία του καταλύματος. Συνδεθείτε ξανά δηλώνοντας όνομα καταλύματος και τοποθεσία.` | — |
| M-58 | `Check-in cannot be in the past.` | 826 / setError | `Η άφιξη δεν μπορεί να είναι στο παρελθόν.` | **no-second-click:448 `getByText("Check-in cannot be in the past.").toHaveCount(0)` — NEGATIVE-STYLE (`toHaveCount(0)`), MUST BE RE-DERIVED against the Greek** |
| M-59 | `Check-out must be after check-in.` | 829 / setError | `Η αναχώρηση πρέπει να είναι μετά την άφιξη.` | — (same sentence as P-33 — keep IDENTICAL) |
| M-60 | `Searching matching competitor rooms. Results will appear after the scrape finishes.` | 842 / message.set | `Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών. Τα αποτελέσματα θα εμφανιστούν μόλις ολοκληρωθεί.` | **no-second-click:702,740,876 `toContainText("Searching matching competitor rooms")` — POSITIVE, MUST CHANGE** |
| M-61 | `No competitor rooms matched after excluding your own property.` | 906 / message.set | `Δεν βρέθηκαν δωμάτια ανταγωνιστών αφού εξαιρέθηκε το δικό σας κατάλυμα.` | — |
| M-62 | `Could not run competitor search.` | 912 / setError fallback | `Δεν ήταν δυνατή η εκτέλεση της αναζήτησης ανταγωνιστών.` | — |
| M-63 | `Choose your property and room type before saving tracked competitors.` | 921 / showSaveMessage | `Επιλέξτε κατάλυμα και τύπο δωματίου πριν αποθηκεύσετε ανταγωνιστές προς παρακολούθηση.` | — |
| M-64 | `Select at least one competitor card before saving.` | 925 / showSaveMessage | `Επιλέξτε τουλάχιστον έναν ανταγωνιστή πριν την αποθήκευση.` | — |
| M-65 | `Selected cards are missing property ids, so they cannot be saved yet.` | 935 / showSaveMessage | `Στις επιλεγμένες καταχωρίσεις λείπει το αναγνωριστικό καταλύματος, οπότε δεν μπορούν ακόμη να αποθηκευτούν.` | — |
| M-66 | `Tracked {n} selected competitor room{s}. Existing tracked rooms were kept.` **[CONCAT→RESTRUCTURE]** | 946 / template literal with inline `s` plural | `Προστέθηκε 1 δωμάτιο ανταγωνιστή στην παρακολούθηση. Τα ήδη παρακολουθούμενα δωμάτια διατηρήθηκαν.` / `Προστέθηκαν ${n} δωμάτια ανταγωνιστών στην παρακολούθηση. Τα ήδη παρακολουθούμενα δωμάτια διατηρήθηκαν.` | **map-page-polish:238 `getByText("Tracked 3 selected competitor rooms")` — POSITIVE, MUST CHANGE to `"Προστέθηκαν 3 δωμάτια ανταγωνιστών στην παρακολούθηση"`** |
| M-67 | `Could not save tracked competitors.` | 956 / fallback | `Δεν ήταν δυνατή η αποθήκευση των ανταγωνιστών προς παρακολούθηση.` | — |
| M-68 | `Choose your property and room before enabling room matching.` | 1032 / matchError | `Επιλέξτε κατάλυμα και δωμάτιο πριν ενεργοποιήσετε το ταίριασμα δωματίου.` | — |
| M-69 | `Could not score competitor rooms.` | 1062 / fallback | `Δεν ήταν δυνατή η βαθμολόγηση των δωματίων των ανταγωνιστών.` | — |
| M-70 | `Your property has no baseline room selected yet. Finish room selection in onboarding to enable matching.` | 1064 / matchError | `Δεν έχει επιλεγεί ακόμη δωμάτιο αναφοράς για το κατάλυμά σας. Ολοκληρώστε την επιλογή δωματίου στον οδηγό ρύθμισης για να ενεργοποιηθεί το ταίριασμα.` | — |
| M-71 | `Could not load account context.` | 1091 / setError fallback | `Δεν ήταν δυνατή η φόρτωση των στοιχείων του λογαριασμού.` | — (same as P-31 — keep IDENTICAL) |
| M-72 | `Competitor search cancelled by navigation.` | 1131 / Error | `Η αναζήτηση ανταγωνιστών ακυρώθηκε λόγω πλοήγησης.` | — |
| M-73 | `Competitor scrape failed.` | 1138 / Error fallback | `Η αναζήτηση ανταγωνιστών απέτυχε.` | — |
| M-74 | `Competitor scrape is still running. Keep the backend open and wait for the job to complete.` | 1142 / Error | `Η αναζήτηση ανταγωνιστών εκτελείται ακόμη. Αφήστε τον διακομιστή ανοιχτό και περιμένετε να ολοκληρωθεί.` | «backend» → «διακομιστή» after Round 5.1 (untranslated term in user-facing copy); no spec asserts it |
| M-75 | `Showing your last completed search for {check_in} to {check_out}. Those dates have passed, so the filters moved to the next bookable window — run Find Competitors for live prices.` **[CONCAT→RESTRUCTURE]** | 1239-1241 / message.set | `Εμφανίζεται η τελευταία ολοκληρωμένη αναζήτησή σας για ${job.check_in} έως ${job.check_out}. Οι ημερομηνίες αυτές έχουν παρέλθει, οπότε τα φίλτρα μετακινήθηκαν στο επόμενο διαθέσιμο διάστημα — πατήστε «Εύρεση ανταγωνιστών» για ζωντανές τιμές.` | — |
| M-76 | `Loaded the last completed competitor search. Use Find Competitors only when you want fresh live data.` | 1242 / message.set | `Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών. Χρησιμοποιήστε την «Εύρεση ανταγωνιστών» μόνο όταν θέλετε φρέσκα ζωντανά δεδομένα.` | **map-page-polish:338 `toContainText("Loaded the last completed competitor search")` POSITIVE MUST CHANGE; no-second-click:705, :879 `not.toContainText("last completed")` — NEGATIVE, MUST BE RE-DERIVED (the substring "last completed" disappears entirely)** |
| M-77 | `Could not initialize Mapbox.` | 1584 / setMapError arg | `Δεν ήταν δυνατή η αρχικοποίηση του Mapbox.` (console-only path — see Notes N5) | — |
| **M-78** | `{hotel}, {price} per night` | **1631 / marker `aria-label` — REQUIRED ADDITION (4.1 a11y gap)** | **`${competitor.hotel_name}, ${this.formatEuro(...)} ανά βράδυ`** and, when `isSelected` is true, append **`, επιλεγμένο για παρακολούθηση`** → full selected form: `«{hotel}, {price} ανά βράδυ, επιλεγμένο για παρακολούθηση»`. Remove the stale comment at l.1626-1629 that defers this to Round 5.1. | **map-auto-plot:124 selector `.roomrate-marker-dot[aria-label*="${hotel}"]` — still matches (hotel name stays first). Do NOT move the hotel name.** |
| M-79 | `Property` | 1697, 1734 / popup fallback | `Κατάλυμα` | — |
| M-80 | `Room` | 1698 / popup fallback | `Δωμάτιο` | — |
| M-81 | `review` / `reviews` | 1699 / popup plural | `κριτική` / `κριτικές` | — |
| M-82 | `room` / `rooms` | 1700 / popup plural | `δωμάτιο` / `δωμάτια` | — |
| M-83 | `Room match` | 1704 / popup dt | `Ταίριασμα δωματίου` | — |
| M-84 | `per night` | 1712 / popup price span | `ανά βράδυ` | — |
| M-85 | `Room type` | 1714 / popup label | `Τύπος δωματίου` | — |
| M-86 | `Review score` | 1719 / popup dt | `Βαθμολογία` | — |
| M-87 | `Rooms left` | 1723 / popup dt | `Διαθέσιμα δωμάτια` | — |

Already Greek (do not touch): l.47, 50, 57-75 (`ONLY_SELECTED_FILTER_NAME`,
`UNHIDE_INSTRUCTION`, `SEARCH_PLOTTED_MESSAGE`, `SEARCH_FILTERED_MESSAGE`,
`FILTER_HIDES_ALL_MESSAGE`, `MAP_LOAD_FAILED_MESSAGE`, `AUTO_PICKED_ROOM_HINT`),
229-237, 293-294, 489-511, 683, 694, 696.

**`formatPropertyType` (l.1731-1741) note**: it Title-Cases a raw Booking value
(`"hotel"`, `"guest_house"`). Leave the algorithm alone; only `"Property"` (M-79)
is a UI string.

### frontend/src/app/services/setup-progress.service.ts — 0 strings

All checklist copy is ALREADY Greek (l.66-70, 88-107). **Do not touch.** It is
the vocabulary source for the glossary above and is pinned by
map-page-polish.spec.ts:199, :202, :214, :216. Listed here only so the map agent
knows there is nothing to do.

---

### frontend/src/app/pages/pricing-page.component.ts — 40 strings

| # | English (exact) | Where (line / kind) | Greek (exact, final) | e2e assertions |
|---|---|---|---|---|
| P-1 | `RoomRate Pricing` | 135 / eyebrow | `RoomRate — Τιμολόγηση` | — |
| P-2 | `Price intelligence` | 136 / h1 fallback | `Ανάλυση τιμών` | — |
| P-3 | `Map` | 139 / routerLink | `Χάρτης` | **setup-room-step-actions:153 `getByRole("link",{name:"Map"})` — POSITIVE, MUST CHANGE** |
| P-4 | `Settings` | 140 / routerLink | `Ρυθμίσεις` | (see M-7 — same nav label, keep IDENTICAL) |
| P-5 | `Market context` | 151 / h2 | `Στοιχεία αγοράς` | — |
| P-6 | `Destination` | 154 / label | `Περιοχή` | — |
| P-7 | `Check-in` | 159 / label | `Άφιξη` | — |
| P-8 | `Check-out` | 163 / label | `Αναχώρηση` | — |
| P-9 | `Adults` | 169 / label | `Ενήλικες` | — |
| P-10 | `Children` | 173 / label | `Παιδιά` | — |
| P-11 | `Rooms` | 177 / label | `Δωμάτια` | — |
| P-12 | `Room category` | 182 / label | `Κατηγορία δωματίου` | — |
| P-13 | `Selected baseline room` | 188 / option | `Το επιλεγμένο δωμάτιο αναφοράς` | — |
| P-14 | `Analyzing market...` | 202 / button busy | `Ανάλυση αγοράς...` | — |
| P-15 | `Get recommendation` | 202 / button | `Λήψη σύστασης` | **live-walkthrough:86; no-second-click:1014; recommendation-card:141,159,179,193 `getByRole("button",{name:"Get recommendation"})` — POSITIVE, MUST CHANGE** |
| P-16 | `The hybrid agent reviews your price history and market context. This can take up to 30 seconds.` | 205 / hint | `Ο υβριδικός πράκτορας εξετάζει το ιστορικό τιμών σας και τα στοιχεία της αγοράς. Μπορεί να διαρκέσει έως 30 δευτερόλεπτα.` | — |
| P-17 | `No recommendation yet` | 212 / strong | `Δεν υπάρχει ακόμη σύσταση` | — |
| P-18 | `Pick your dates and run Get recommendation to see the suggested nightly price for your room.` | 213 / span | `Επιλέξτε ημερομηνίες και πατήστε «Λήψη σύστασης» για να δείτε την προτεινόμενη τιμή ανά βράδυ για το δωμάτιό σας.` | — |
| P-19 | `Not enough data for a recommendation` | 218 / h2 | `Δεν υπάρχουν αρκετά δεδομένα για σύσταση` | — |
| P-20 | `RoomRate refuses to invent a price when the market history cannot support one. Run competitor searches (or enable scheduled scraping) for these dates first.` | 221-222 / p | `Το RoomRate δεν εφευρίσκει τιμή όταν το ιστορικό της αγοράς δεν την στηρίζει. Τρέξτε πρώτα αναζητήσεις ανταγωνιστών (ή ενεργοποιήστε τις προγραμματισμένες αναζητήσεις) για αυτές τις ημερομηνίες.` | — |
| P-21 | `Recommended nightly price` | 231 / h2 | `Προτεινόμενη τιμή ανά βράδυ` | **no-second-click:1015; recommendation-card:161,181,195 `getByText("Recommended nightly price")` — POSITIVE, MUST CHANGE** |
| P-22 | `{{ rec.confidence }} confidence` **[CONCAT→RESTRUCTURE]** | 234 / chip | Map the enum, do NOT interpolate raw: `high` → `Υψηλή βεβαιότητα`, `medium` → `Μέτρια βεβαιότητα`, `low` → `Χαμηλή βεβαιότητα`. Add `confidenceLabel()` beside the existing `confidenceChipClass()`. | **no-second-click:1018 `getByText("medium confidence")` — POSITIVE, MUST CHANGE to `"Μέτρια βεβαιότητα"`** |
| P-23 | `AI agent` | 238 / source chip | `AI agent` (brand/technical — STAYS, see Notes N3) | — |
| P-24 | `Statistical` | 238 / source chip | `Στατιστική` | — |
| P-25 | `Cached` | 239 / source chip | `Από προσωρινή μνήμη` | — |
| P-26 | `Suggested range {low} – {high} per night` | 244-245 / p | `Προτεινόμενο εύρος {{ formatEuroFloor(...) }} – {{ formatEuroCeil(...) }} ανά βράδυ` (keep the `&ndash;` and both calls) | recommendation-card:149 `not.toContainText("306 €")` — **NEGATIVE but purely NUMERIC; no re-derivation needed (the number is unaffected by translation)** |
| P-27 | `Audited decision {{ audit_id }} · {{ model_version }}` | 255 / hint | `Ελεγμένη απόφαση {{ result()!.audit_id }} · {{ result()!.model_version }}` | — |
| P-28 | `Market statistics` | 261 / h2 | `Στατιστικά αγοράς` | — |
| P-29 | `Market price history` | 276 / h2 | `Ιστορικό τιμών αγοράς` | **live-walkthrough:83 `getByText("Market price history")` — POSITIVE, MUST CHANGE** |
| P-30 | `Refresh` | 278 / text-button | `Ανανέωση` | — |
| P-31 | `Loading price history...` | 284 / empty line | `Φόρτωση ιστορικού τιμών...` | — |
| P-32 | `Complete property setup before requesting price recommendations.` | 549 / contextError | `Ολοκληρώστε τη ρύθμιση του καταλύματος πριν ζητήσετε συστάσεις τιμών.` | — |
| P-33 | `Could not load account context.` | 559 / contextError fallback | `Δεν ήταν δυνατή η φόρτωση των στοιχείων του λογαριασμού.` | — (IDENTICAL to M-71) |
| P-34 | `Could not get a price recommendation.` | 590 / recError fallback | `Δεν ήταν δυνατή η λήψη σύστασης τιμής.` | — |
| P-35 | `Could not load price history.` | 665 / historyError fallback | `Δεν ήταν δυνατή η φόρτωση του ιστορικού τιμών.` | — |
| P-36 | `Pick both check-in and check-out dates.` | 719 / validateDates | `Επιλέξτε και ημερομηνία άφιξης και ημερομηνία αναχώρησης.` | — |
| P-37 | `Check-out must be after check-in.` | 722 / validateDates | `Η αναχώρηση πρέπει να είναι μετά την άφιξη.` | — (IDENTICAL to M-59) |
| P-38 | `Check-in cannot be in the past.` | 725 / validateDates | `Η άφιξη δεν μπορεί να είναι στο παρελθόν.` | — (IDENTICAL to M-58) |

**§7 GLOSSARY BLOCK — `buildStatisticEntries` (l.737-753). These ten are the
mandatory ones; use exactly these:**

| # | English (exact) | Line | Greek (exact, final) | e2e |
|---|---|---|---|---|
| P-39 | `Market median` | 738 | **`Διάμεση τιμή αγοράς`** | **no-second-click:1019 `getByText("Market median",{exact:true})` — POSITIVE, MUST CHANGE (keep `exact: true`)** |
| P-40 | `Market P25` | 739 | **`Χαμηλό εύρος αγοράς`** | — |
| P-41 | `Market P75` | 740 | **`Υψηλό εύρος αγοράς`** | — |
| P-42 | `Your reference price` | 741 | **`Η τιμή αναφοράς σας`** | — |
| P-43 | `Your market position` | 743 | **`Θέση σας στην αγορά`** | — |
| P-44 | `Statistical baseline` | 748 | **`Στατιστική βάση αναφοράς`** | — |
| P-45 | `Trend (7 days)` | 749 | **`Τάση (7 ημερών)`** | — |
| P-46 | `Trend (30 days)` | 750 | **`Τάση (30 ημερών)`** | — |
| P-47 | `Lead time` | 751 | **`Ημέρες μέχρι την άφιξη`** | — |
| P-48 | `{{ lead_time_days }} days` **[CONCAT→RESTRUCTURE]** | 751 / value | The label already says «Ημέρες», so the VALUE becomes the bare number: `String(statistics.lead_time_days)`. Do NOT write «X ημέρες ημέρες». | — |
| P-49 | `Sample runs` | 752 | **`Αναζητήσεις που συγκρίθηκαν`** | — |

Already Greek (do not touch): l.290-291, 318 (`€/βράδυ`), 395-398, 404, 414, 420,
427, 431, 435-439, 511-514, 745, 835, 855-857, 871-877, 902, 1070.
**`formatPercentile` returns `P{n}` (l.767) — leave as is** (a compact numeric
token, and the small-sample suffix beside it is already Greek).

---

### frontend/src/app/pages/settings-page.component.ts — 45 strings

| # | English (exact) | Where (line / kind) | Greek (exact, final) | e2e |
|---|---|---|---|---|
| S-1 | `RoomRate Settings` | 57 / eyebrow | `RoomRate — Ρυθμίσεις` | — |
| S-2 | `Scheduled scraping` | 58 / h1 | `Προγραμματισμένες αναζητήσεις` | — |
| S-3 | `Map` | 61 / routerLink | `Χάρτης` | (IDENTICAL to P-3; setup-room-step-actions:153) |
| S-4 | `Pricing` | 62 / routerLink | `Τιμολόγηση` | (IDENTICAL to M-6) |
| S-5 | `Loading schedule` | 69 / strong | `Φόρτωση προγράμματος` | — |
| S-6 | `Fetching your recurring scrape configuration.` | 70 / span | `Ανάκτηση της ρύθμισης των επαναλαμβανόμενων αναζητήσεών σας.` | — |
| S-7 | `Recurring competitor scrapes` | 76 / h2 | `Επαναλαμβανόμενες αναζητήσεις ανταγωνιστών` | — |
| S-8 | `Enable scheduled scraping` | 81 / checkbox | `Ενεργοποίηση προγραμματισμένων αναζητήσεων` | — |
| S-9 | `Scheduled scrapes run automatically against Booking via Apify and consume Apify credits on every run. Keep the frequency conservative to control costs.` | 84-85 / alert | `Οι προγραμματισμένες αναζητήσεις εκτελούνται αυτόματα στο Booking μέσω Apify και καταναλώνουν μονάδες Apify σε κάθε εκτέλεση. Κρατήστε χαμηλή τη συχνότητα για να ελέγχετε το κόστος.` | — |
| S-10 | `Frequency (hours)` | 90 / label | `Συχνότητα (ώρες)` | — |
| S-11 | `Run hour (UTC, 0-23)` | 94 / label | `Ώρα εκτέλεσης (UTC, 0-23)` | — |
| S-12 | `Lead days before check-in` | 100 / label | `Ημέρες πριν από την άφιξη` | — |
| S-13 | `Nights per stay` | 104 / label | `Διανυκτερεύσεις ανά διαμονή` | — |
| S-14 | `Adults` | 110 / label | `Ενήλικες` | (IDENTICAL to P-9) |
| S-15 | `Children` | 114 / label | `Παιδιά` | (IDENTICAL to P-10) |
| S-16 | `Rooms` | 118 / label | `Δωμάτια` | (IDENTICAL to P-11) |
| S-17 | `Last run` | 125 / stat label | `Τελευταία εκτέλεση` | — |
| S-18 | `Consecutive failures` | 129 / stat label | `Συνεχόμενες αποτυχίες` | — |
| S-19 | `After repeated failures the scheduler pauses automatically. Fix the underlying issue, then re-enable scheduling here.` | 134-135 / p | `Μετά από επαναλαμβανόμενες αποτυχίες ο προγραμματισμός διακόπτεται αυτόματα. Διορθώστε την αιτία και ενεργοποιήστε τον ξανά από εδώ.` | — |
| S-20 | `Saving...` | 139 / button busy | `Γίνεται αποθήκευση...` | — (matches wizard's existing «Γίνεται αποθήκευση...») |
| S-21 | `Save schedule` | 139 / button | `Αποθήκευση προγράμματος` | — |
| S-22 | `Competitor price alerts` | 148 / h2 | `Ειδοποιήσεις τιμών ανταγωνιστών` | — |
| S-23 | `Create account-wide rules for competitor price rises, drops, or either direction.` | 150 / p.muted | `Δημιουργήστε κανόνες για όλο τον λογαριασμό, για αυξήσεις, μειώσεις ή και τις δύο κατευθύνσεις των τιμών των ανταγωνιστών.` | — |
| S-24 | `Change threshold (%)` | 153 / label | `Όριο μεταβολής (%)` | — |
| S-25 | `Direction` | 157 / label | `Κατεύθυνση` | — |
| S-26 | `Rise or drop` | 159 / option (value `any` STAYS) | `Άνοδος ή πτώση` | — |
| S-27 | `Drop only` | 160 / option (value `drop` STAYS) | `Μόνο πτώση` | — |
| S-28 | `Rise only` | 161 / option (value `rise` STAYS) | `Μόνο άνοδος` | — |
| S-29 | `Add alert rule` | 164 / button | `Προσθήκη κανόνα ειδοποίησης` | — |
| S-30 | `Loading alert rules...` | 166 / empty line | `Φόρτωση κανόνων ειδοποιήσεων...` | — |
| S-31 | `No custom rules. RoomRate uses the configured default threshold.` | 169 / empty line | `Δεν υπάρχουν προσαρμοσμένοι κανόνες. Το RoomRate χρησιμοποιεί το προεπιλεγμένο όριο.` | — |
| S-32 | `{{ directionLabel(...) }} at {{ threshold_pct }}%` **[CONCAT→RESTRUCTURE]** | 174 / strong | `{{ directionLabel(rule.direction) }} από {{ rule.threshold_pct }}%` — reads «Πτώση τιμής από 10%» | — |
| S-33 | `Active` | 175 / span | `Ενεργός` | — |
| S-34 | `Paused` | 175 / span | `Σε παύση` | — |
| S-35 | `Pause` | 179 / text-button | `Παύση` | — |
| S-36 | `Enable` | 179 / text-button | `Ενεργοποίηση` | — |
| S-37 | `Delete` | 182 / text-button | `Διαγραφή` | — |
| S-38 | `Could not load the schedule configuration.` | 295 / error fallback | `Δεν ήταν δυνατή η φόρτωση της ρύθμισης του προγράμματος.` | — |
| S-39 | `Schedule saved. Recurring scrapes are enabled and will consume Apify credits on every run.` | 330 / saveMessage | `Το πρόγραμμα αποθηκεύτηκε. Οι επαναλαμβανόμενες αναζητήσεις είναι ενεργές και καταναλώνουν μονάδες Apify σε κάθε εκτέλεση.` | — |
| S-40 | `Schedule saved. Recurring scrapes are disabled.` | 331 / saveMessage | `Το πρόγραμμα αποθηκεύτηκε. Οι επαναλαμβανόμενες αναζητήσεις είναι απενεργοποιημένες.` | — |
| S-41 | `Could not save the schedule.` | 334 / fallback | `Δεν ήταν δυνατή η αποθήκευση του προγράμματος.` | — |
| S-42 | `Alert threshold must be greater than 0 and no more than 100.` | 350 / rulesError | `Το όριο ειδοποίησης πρέπει να είναι μεγαλύτερο από 0 και το πολύ 100.` | — |
| S-43 | `Could not create the alert rule.` | 369 / fallback | `Δεν ήταν δυνατή η δημιουργία του κανόνα ειδοποίησης.` | — |
| S-44 | `Could not update the alert rule.` | 384 / fallback | `Δεν ήταν δυνατή η ενημέρωση του κανόνα ειδοποίησης.` | — |
| S-45 | `Delete the {{ threshold_pct }}% alert rule?` | 391 / **`window.confirm`** | `Διαγραφή του κανόνα ειδοποίησης ${rule.threshold_pct}%;` (Greek question mark «;», NOT «?») | — |
| S-46 | `Could not delete the alert rule.` | 400 / fallback | `Δεν ήταν δυνατή η διαγραφή του κανόνα ειδοποίησης.` | — |
| S-47 | `Price drop` | 461 / directionLabel | `Πτώση τιμής` | — |
| S-48 | `Price rise` | 461 / directionLabel | `Άνοδος τιμής` | — |
| S-49 | `Price change` | 461 / directionLabel | `Μεταβολή τιμής` | — |
| S-50 | `Never` | 467, 471 / lastRunLabel | `Ποτέ` | — |
| S-51 | `Frequency must be at least 1 hour.` | 539 / validate | `Η συχνότητα πρέπει να είναι τουλάχιστον 1 ώρα.` | — |
| S-52 | `Run hour must be between 0 and 23 (UTC).` | 542 / validate | `Η ώρα εκτέλεσης πρέπει να είναι από 0 έως 23 (UTC).` | — |
| S-53 | `Lead days cannot be negative.` | 545 / validate | `Οι ημέρες πριν από την άφιξη δεν μπορούν να είναι αρνητικές.` | Plural agreement corrected after Round 5.1 (was «δεν μπορεί»); no spec asserts it |
| S-54 | `Nights must be at least 1.` | 548 / validate | `Οι διανυκτερεύσεις πρέπει να είναι τουλάχιστον 1.` | — |
| S-55 | `Adults must be at least 1.` | 551 / validate | `Οι ενήλικες πρέπει να είναι τουλάχιστον 1.` | — |
| S-56 | `Children cannot be negative.` | 554 / validate | `Τα παιδιά δεν μπορούν να είναι αρνητικά.` | Plural agreement corrected after Round 5.1 (was «δεν μπορεί»); no spec asserts it |
| S-57 | `Rooms must be at least 1.` | 557 / validate | `Τα δωμάτια πρέπει να είναι τουλάχιστον 1.` | — |

**`lastRunLabel` / `formatTime` locale** — see Notes N6.

Already Greek (do not touch): l.190, 194, 197, 204, 206, 210-211, 214, 222, 225,
231, 451. Pinned by empty-states-and-settings.spec.ts:303, :305, :369, :411, :414.

---

### Shell & shared components

**`frontend/src/app/app.component.ts`** — **0 strings.** Template is
`"<router-outlet />"`. Nothing to translate. (The "shell/nav" the brief refers to
lives in each page's own `<header class="map-header">` — rows M-1..M-8, P-1..P-4,
S-1..S-4. Keep the three nav labels identical across all three pages.)

**`frontend/src/app/components/icon.component.ts`** — **0 strings.** All SVG
paths, `aria-hidden="true"`. `IconName` values are enum-like identifiers. Do not
touch.

**`frontend/src/app/components/empty-state.component.ts`** — **0 strings.** Pure
`@Input()` pass-through; every title/explanation lives at the call site and is
already inventoried there. Do not touch.

**`frontend/src/app/components/notification-bell.component.ts` — 10 strings**

| # | English (exact) | Where | Greek (exact, final) | e2e |
|---|---|---|---|---|
| B-1 | `Notifications` | 31 / panel header strong | `Ειδοποιήσεις` | — |
| B-2 | `Mark all read` | 38 / text-button | `Σήμανση όλων ως αναγνωσμένων` | — |
| B-3 | `Loading notifications...` | 41 / empty line | `Φόρτωση ειδοποιήσεων...` | — |
| B-4 | `No notifications yet. Price alerts from your tracked competitors will appear here.` | 44 / empty line | `Δεν υπάρχουν ακόμη ειδοποιήσεις. Εδώ θα εμφανίζονται οι ειδοποιήσεις τιμών από τους ανταγωνιστές που παρακολουθείτε.` | — |
| B-5 | `Mark read` | 54 / text-button | `Σήμανση ως αναγνωσμένης` | — |
| B-6 | `Load more` | 65 / text-button | `Φόρτωση περισσότερων` | — |
| B-7 | `Notifications, {n} unread` **[CONCAT→RESTRUCTURE]** | 108 / **aria-label** | `Ειδοποιήσεις, 1 μη αναγνωσμένη` / `Ειδοποιήσεις, ${unread} μη αναγνωσμένες`. **The `!Number.isFinite` fallback on the same line MUST stay the exact string `Ειδοποιήσεις`.** | **notification-bell.spec.ts:27 `toHaveAttribute("aria-label","Ειδοποιήσεις")` — POSITIVE on the FALLBACK branch; it must keep passing. Do not alter that branch.** |
| B-8 | `Could not mark the notification read.` | 133 / error fallback | `Δεν ήταν δυνατή η σήμανση της ειδοποίησης ως αναγνωσμένης.` | — |
| B-9 | `Could not mark all notifications read.` | 149 / fallback | `Δεν ήταν δυνατή η σήμανση όλων των ειδοποιήσεων ως αναγνωσμένων.` | — |
| B-10 | `Could not load more notifications.` | 166 / fallback | `Δεν ήταν δυνατή η φόρτωση περισσότερων ειδοποιήσεων.` | — |
| B-11 | `Could not load notifications.` | 196 / fallback | `Δεν ήταν δυνατή η φόρτωση των ειδοποιήσεων.` | — |

**`frontend/src/app/components/notification-toasts.component.ts` — 1 string**

| # | English (exact) | Where | Greek (exact, final) | e2e |
|---|---|---|---|---|
| T-1 | `Dismiss notification` | 27 / **aria-label** | `Κλείσιμο ειδοποίησης` | — |

Toast `title`/`message` come from the backend payload — **not UI strings, do not
translate** (no-second-click:1059, :1073-1074 assert backend-produced
«Price drop: Hotel Gamma 20.0%» — that is the BACKEND's copy, out of scope for
5.1; see Notes N7).

**`frontend/src/index.html` — 2 items**

| # | English (exact) | Where | Greek (exact, final) | e2e |
|---|---|---|---|---|
| H-1 | `<html lang="en">` | 2 / attribute | `<html lang="el">` — **required**: it drives screen-reader pronunciation and hyphenation for the whole Greek app | — |
| H-2 | `<title>RoomRate</title>` | 5 | `RoomRate` — **STAYS** (brand name, rule 4) | — |

**`frontend/src/app/services/*.ts`**

| # | English (exact) | File / line | Greek (exact, final) | e2e |
|---|---|---|---|---|
| SV-1 | `Your session expired. Sign in again.` | api-client.service.ts:74 | `Η συνεδρία σας έληξε. Συνδεθείτε ξανά.` — **byte-identical to A-27** | — |
| SV-2 | `RoomRate API request failed.` | api-client.service.ts:84 | `Το αίτημα προς το RoomRate API απέτυχε.` | — |
| SV-3 | `RoomRate API timed out while calling {path}. Check that FastAPI is running on {url}.` | api-client.service.ts:58-59 | `Λήξη χρόνου αναμονής του RoomRate API κατά την κλήση ${path}. Ελέγξτε ότι το FastAPI εκτελείται στο ${environment.apiBaseUrl}.` | — |
| SV-4 | `Could not reach RoomRate API at {url}.` | api-client.service.ts:64 | `Δεν ήταν δυνατή η επικοινωνία με το RoomRate API στο ${environment.apiBaseUrl}.` | — |
| SV-5 | `You need to sign in first.` | auth.service.ts:50 | `Πρέπει πρώτα να συνδεθείτε.` | — |
| SV-6 | `Supabase frontend env is missing.` | auth.service.ts:57, 75, 95, 130, 145 (5 sites) | `Λείπουν οι ρυθμίσεις περιβάλλοντος του Supabase.` | — |
| SV-7 | `Supabase did not return a session.` | auth.service.ts:61 | `Το Supabase δεν επέστρεψε συνεδρία.` | — |
| SV-8 | `Account creation is taking too long. Check your connection and try again.` | auth.service.ts:85 | `Η δημιουργία λογαριασμού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.` | — |
| SV-9 | `Confirmation email resend is taking too long. Check your connection and try again.` | auth.service.ts:105 | `Η επαναποστολή του email επιβεβαίωσης διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.` | — |
| SV-10 | `Password reset request is taking too long. Check your connection and try again.` | auth.service.ts:136 | `Το αίτημα επαναφοράς κωδικού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.` | — |
| SV-11 | `Password update is taking too long. Check your connection and try again.` | auth.service.ts:149 | `Η ενημέρωση του κωδικού διαρκεί υπερβολικά. Ελέγξτε τη σύνδεσή σας και δοκιμάστε ξανά.` | — |
| SV-12 | `Supabase sign-in failed.` | auth.service.ts:185 | `Η σύνδεση μέσω Supabase απέτυχε.` | — |
| SV-13 | `Supabase sign-in timed out after 20 seconds. Check your network, Supabase project status, or try again.` | auth.service.ts:191 | `Η σύνδεση μέσω Supabase έληξε μετά από 20 δευτερόλεπτα. Ελέγξτε το δίκτυό σας και την κατάσταση του Supabase project, ή δοκιμάστε ξανά.` | — |
| SV-14 | `Supabase sign-in failed before RoomRate could load your workspace.` | auth.service.ts:196 | `Η σύνδεση μέσω Supabase απέτυχε πριν προλάβει το RoomRate να φορτώσει τον χώρο εργασίας σας.` | — |
| SV-15 | `Too many confirmation emails were requested. Supabase has temporarily rate-limited this project. Wait a few minutes and check inbox/spam, or disable email confirmation for local development.` | auth.service.ts:205 | `Ζητήθηκαν πάρα πολλά email επιβεβαίωσης. Το Supabase περιόρισε προσωρινά αυτό το project. Περιμένετε λίγα λεπτά και ελέγξτε τα εισερχόμενα και τα ανεπιθύμητα, ή απενεργοποιήστε την επιβεβαίωση email για τοπική ανάπτυξη.` | — |
| SV-16 | `Your email is not confirmed yet. Check your inbox and spam folder, then click the confirmation link.` | auth.service.ts:208 | `Το email σας δεν έχει επιβεβαιωθεί ακόμη. Ελέγξτε τα εισερχόμενα και τον φάκελο ανεπιθύμητων και πατήστε τον σύνδεσμο επιβεβαίωσης.` | — |

`notifications.service.ts`, `onboarding.service.ts`, `workflow-storage.service.ts`,
`api-client-error.ts` — **0 user-facing strings.** Confirmed by grep.

---

## frontend/e2e — assertion index

**Owner rule:** a POSITIVE assertion breaks loudly when the string changes — good,
just swap the text. A **NEGATIVE** assertion (`not.toContainText`,
`toHaveCount(0)`) goes **silently green** after translation because the English
substring can no longer appear anywhere. Every NEGATIVE below must be
**RE-DERIVED against the new Greek** (or replaced by a `data-testid`
presence/absence assertion), never mechanically translated and never left alone.
This is the explicit Round 2/3 finding recorded in
`docs/superpowers/plans/2026-08-12-next-work-queue.md` §"Round 5.1 churn"
(l.360-367).

### live-walkthrough.spec.ts — all POSITIVE
| line | English | Greek replacement |
|---|---|---|
| 77 | `name: "Filters"` | `name: "Φίλτρα"` |
| 78 | `getByText("Search filters")` | `getByText("Φίλτρα αναζήτησης")` |
| 82 | `link name: "Pricing"` | `name: "Τιμολόγηση"` |
| 83 | `getByText("Market price history")` | `getByText("Ιστορικό τιμών αγοράς")` |
| 86 | `button name: "Get recommendation"` | `name: "Λήψη σύστασης"` |
| 94 | `link name: "Settings"` | `name: "Ρυθμίσεις"` |

### map-auto-plot.spec.ts
| line | English | Greek replacement | kind |
|---|---|---|---|
| 124 | `[aria-label*="${hotel}"]` | **unchanged** — M-78 keeps the hotel name first | POSITIVE (locator) |
| 233, 324 | `name: "Filters"` | `name: "Φίλτρα"` | POSITIVE |
| 234, 325 | `name: "Find Competitors"` | `name: "Εύρεση ανταγωνιστών"` | POSITIVE |
| 236, 327, 348, 364 | `getByText("3 competitors")` / `"4 competitors"` | `"3 ανταγωνιστές"` / `"4 ανταγωνιστές"` | POSITIVE |
| 245, 301, 302, 330, 377 | already-Greek negatives | **unchanged** (Greek source unchanged) | NEGATIVE, already fine |

### map-page-polish.spec.ts
| line | English | Greek replacement | kind |
|---|---|---|---|
| 196, 212, 230, 259, 306, 324 | `getByText("N competitors")` | `"N ανταγωνιστές"` | POSITIVE |
| 236 | `name: "Add selected to tracked"` | `name: "Προσθήκη επιλεγμένων στην παρακολούθηση"` | POSITIVE |
| 238 | `getByText("Tracked 3 selected competitor rooms")` | `getByText("Προστέθηκαν 3 δωμάτια ανταγωνιστών στην παρακολούθηση")` | POSITIVE |
| 265, 278, 296, 307, 336 | `name: "Filters"` | `name: "Φίλτρα"` | POSITIVE |
| 283 | `link name: "Settings"` | `name: "Ρυθμίσεις"` | POSITIVE |
| **327** | `not.toContainText("Waiting for the live scrape")` | **RE-DERIVE → `not.toContainText("Αναμονή για την ολοκλήρωση")`** | **NEGATIVE — FLAG** |
| 338 | `toContainText("Loaded the last completed competitor search")` | `toContainText("Φορτώθηκε η τελευταία ολοκληρωμένη αναζήτηση ανταγωνιστών")` | POSITIVE |
| 199-202, 214-216, 267, 331-332 | already Greek | unchanged | — |

### no-second-click.spec.ts (the heaviest file)
| line | English | Greek replacement | kind |
|---|---|---|---|
| 404, 530, 586 | `getByText("2 competitors")` | `"2 ανταγωνιστές"` | POSITIVE |
| 412 | `getByText("42 records")` | `getByText("42 καταγραφές")` | POSITIVE |
| 431, 631, 686, 732, 773, 816, 868, 960 | `name: "Filters"` | `name: "Φίλτρα"` | POSITIVE |
| 447, 632, 691, 733, 775, 817, 869, 965 | `name: "Find Competitors"` | `name: "Εύρεση ανταγωνιστών"` | POSITIVE |
| **448** | `getByText("Check-in cannot be in the past.").toHaveCount(0)` | **RE-DERIVE → `getByText("Η άφιξη δεν μπορεί να είναι στο παρελθόν.").toHaveCount(0)`** | **NEGATIVE-STYLE — FLAG** |
| 536, 595 | `name: "Add selected to tracked"` | `name: "Προσθήκη επιλεγμένων στην παρακολούθηση"` | POSITIVE |
| 646, 706, 742, 828 | `toContainText("Waiting for the live scrape to finish.")` | `toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.")` | POSITIVE |
| 647 | `toContainText("Loading")` (header) | `toContainText("Φόρτωση")` | POSITIVE |
| **653, 827** | `not.toContainText("42 records")` | **RE-DERIVE → `not.toContainText("42 καταγραφές")`, or better: assert `.market-summary-block` has count 0** | **NEGATIVE — FLAG** |
| 702, 740, 876 | `toContainText("Searching matching competitor rooms")` | `toContainText("Γίνεται αναζήτηση συγκρίσιμων δωματίων ανταγωνιστών")` | POSITIVE |
| **705, 879** | `not.toContainText("last completed")` | **RE-DERIVE → `not.toContainText("τελευταία ολοκληρωμένη αναζήτηση")` — the substring "last completed" vanishes entirely, so this is the most dangerous silent pass in the suite** | **NEGATIVE — FLAG** |
| **741** | `not.toContainText("Set filters and run Find Competitors")` | **RE-DERIVE → `not.toContainText("Ρυθμίστε τα φίλτρα")`** | **NEGATIVE — FLAG** |
| **769** | `not.toContainText("Waiting for the live scrape to finish.")` | **RE-DERIVE → `not.toContainText("Αναμονή για την ολοκλήρωση της ζωντανής αναζήτησης.")`** | **NEGATIVE — FLAG** |
| **770** | `not.toContainText("Searching matching competitor rooms")` | **RE-DERIVE → `not.toContainText("Γίνεται αναζήτηση συγκρίσιμων δωματίων")`** | **NEGATIVE — FLAG** |
| 774 | `toContainText("Set filters and run Find Competitors")` | `toContainText("Ρυθμίστε τα φίλτρα και πατήστε «Εύρεση ανταγωνιστών»")` | POSITIVE |
| 885 | `button name: "Searching"` | `name: "Γίνεται αναζήτηση"` | POSITIVE |
| 1014 | `name: "Get recommendation"` | `name: "Λήψη σύστασης"` | POSITIVE |
| 1015 | `getByText("Recommended nightly price")` | `getByText("Προτεινόμενη τιμή ανά βράδυ")` | POSITIVE |
| 1018 | `getByText("medium confidence")` | `getByText("Μέτρια βεβαιότητα")` | POSITIVE |
| 1019 | `getByText("Market median",{exact:true})` | `getByText("Διάμεση τιμή αγοράς",{exact:true})` | POSITIVE |
| 405-406, 429, 881, 958, 977, 1059, 1073-1074 | hotel names / backend toast copy | **unchanged — fixture + backend data** | — |
| 409-410 | `"80 €"`, `"120 €"` | **unchanged** — currency formatting is untouched (rule 4) | — |
| 644-645 | already-Greek negatives | unchanged | — |

### empty-states-and-settings.spec.ts — all already Greek, nothing to change
Lines 43-46, 174-175, 186, 196, 208-209, 217, 237-239, 254-255, 303, 305, 369-370,
379, 382-383, 388, 411, 414. **Except:** l.45-46 `not.toContainText("0 €")` /
`("€0")` are NUMERIC negatives — unaffected by translation, leave them. l.413
`getByText("E2E Test Hotel")` is fixture data.

### notification-bell.spec.ts
| line | assertion | action |
|---|---|---|
| 27 | `toHaveAttribute("aria-label","Ειδοποιήσεις")` | **Unchanged — but the bell agent MUST keep the `!Number.isFinite` fallback branch producing exactly `Ειδοποιήσεις` (B-7). Add NEW coverage for the `{n} μη αναγνωσμένες` branch.** |

### recommendation-card.spec.ts
| line | assertion | action |
|---|---|---|
| 141, 159, 179, 193 | `name: "Get recommendation"` | → `name: "Λήψη σύστασης"` — POSITIVE |
| **149** | `not.toContainText("306 €")` | **Numeric only — no re-derivation needed, but re-read the surrounding `range` locator after P-26 changes the prose around it.** |
| 161, 181, 195 | `getByText("Recommended nightly price")` | → `getByText("Προτεινόμενη τιμή ανά βράδυ")` — POSITIVE |

### price-history-chart.spec.ts
| line | assertion | action |
|---|---|---|
| 260 | `not.toHaveText("NaN")` | Numeric sentinel — **unchanged** |
| 295 | `toContainText("P25–P75")` | **unchanged** — P25/P75 stays as chart shorthand (rule 4), and the tooltip label at pricing l.397 is already Greek-adjacent |
| 302 | `aria-label` on the hit rect | The chart aria-label is **already Greek** (pricing l.853-858, 902) — unchanged |

### setup-wizard.spec.ts — the auth-label trap
| line | assertion | action |
|---|---|---|
| **124, 964** | `getByLabel(/accommodation name/i)` | **MUST CHANGE → `/όνομα καταλύματος/i`** (A-9). These are POSITIVE regex label lookups; they will throw "locator resolved to 0 elements" — loud, but the auth agent must fix them in the SAME commit or the suite is red. |
| **125** | `getByLabel(/location/i)` | **MUST CHANGE → `/τοποθεσία/i`** (A-11) |
| **965** | `getByLabel(/^location$/i)` | **MUST CHANGE → `/^τοποθεσία$/i`** (A-11) |
| 126, 929, 967, 1035 | `getByLabel(/email/i)` | **unchanged** — A-13 keeps the label `Email` |
| 1171 | `button name: "Filters"` | → `name: "Φίλτρα"` — POSITIVE |
| 978-979 | `getByLabel("Όνομα καταλύματος")`, `getByLabel("Τοποθεσία")` | **unchanged** — these are the WIZARD's already-Greek labels, and they are exactly the strings A-9/A-11 adopt. Good: one concept, one term. |
| 86, 157, 208, 226, 330, 369, 489-490, 493, 502, 517, 549, 562-565, 579, 605, 633, 723, 825, 1090, 1105, 1134-1137, 1275+ | fixture data / backend messages / already-Greek | **unchanged** |

### Other spec files — no changes needed
`map-page-polish` (partially above), `poll-deadline.spec.ts`,
`scrape-job-poller.spec.ts`, `search-intent.spec.ts` (l.15 fixture),
`search-job-reconciliation.spec.ts`, `room-category.spec.ts`,
`setup-room-step-polling.spec.ts` (l.66, 102 assert BACKEND messages are hidden —
fixture strings), `setup-room-step-actions.spec.ts` (l.55 fixture; **l.151, 153
nav links MUST change**: `"Settings"` → `"Ρυθμίσεις"`, `"Map"` → `"Χάρτης"`),
`setup-search-step.spec.ts` (l.176-177 `"Rhodes"`/`"double"` are DATA;
l.203, 286, 368, 399-400, 477 assert raw backend strings stay hidden — fixtures),
`result-summary-map.spec.ts` (**l.88 `name: "Filters"` → `"Φίλτρα"`**).

---

## Notes for the agents

**N1 — «records» is the one genuinely ambiguous string (M-30).** `total_records`
counts scraped price rows, not «εγγραφές» in the DB sense the hotelier would
misread. **Recommendation (adopt unless the owner objects): «καταγραφές»** —
«42 καταγραφές» reads as "42 recorded observations", which is what it is.
Rejected alternatives: «εγγραφές» (sounds like sign-ups), «σημεία» (overloaded
with the chart's dots). Flagged because both e2e negatives (no-second-click:653,
:827) hang off this exact substring.

**N2 — Fixture data is NOT UI.** `frontend/e2e/helpers.ts`,
`setup-room-step.helpers.ts`, and every `hotel_name` / `display_name` /
`property_name` / `destination` literal inside a spec's mock payload is **data
the backend would return**. Do not translate: "Hotel Alpha", "Hotel Beta",
"Hotel Gamma", "Hotel Delta", "Hotel Ambrosia", "Rea Hotel", "Rea Hotel Annex",
"Rea Hotel Garden", "E2E Test Hotel", "Villa Nea", "Room Step Hotel",
"Search Step Hotel", "Authoritative Hotel", "Rhodes", "Faliraki", "double".
Translating a fixture changes what the mock server returns, not what the UI says
— it would break the test for the wrong reason.

**N3 — Strings that stay English.** RoomRate, Booking, Mapbox, Supabase, Apify,
FastAPI, Angular, `AI agent` (P-23 — a product-mode name that sits beside
«Στατιστική»; translating one and not the other reads worse than keeping both
recognisable), `Email` (A-13, and it keeps four e2e regexes green), `UTC`
(S-11, S-52), `P25`/`P75` as chart-axis shorthand (pricing l.397, 438-439;
price-history-chart.spec.ts:295) — but **NOT** in the statistics table, where §7
mandates «Χαμηλό/Υψηλό εύρος αγοράς» (P-40, P-41). `<title>RoomRate</title>` (H-2).

**N4 — Amenity labels are a BOUNDARY, not UI copy.** `COMMON_AMENITIES`
(map l.79-92) and `FACILITY_MATCHERS[].label` (l.93-106) are sent to the backend
as `params.append("amenities", amenity)` (l.1543) **and** rendered as checkbox
text (l.181). `normalizeAmenityOptions` matches them by identity (l.1455-1458).
**Do not translate them in this round.** Translating the label breaks the
`amenities` query parameter and the `matched.includes(facility)` identity check.
If the owner wants Greek facility names, that is a separate change needing a
`{ value, label }` split — **flagged, not done here.** (The `terms` arrays
already carry Greek matching tokens, which is the correct layer.)

**N5 — `setMapError` (map l.1600-1603).** The raw Mapbox reason goes to
`console.warn` and the USER sees `MAP_LOAD_FAILED_MESSAGE`, which is already
Greek. M-77 (`"Could not initialize Mapbox."`) is therefore console-only in
practice. Translate it anyway for consistency; it costs nothing and the next
reader will not have to re-derive that reasoning.

**N6 — `toLocaleString("en-GB")` in two places.** notification-bell l.177 and
settings l.473 format dates with an English locale, inside an otherwise Greek
UI — they render "13 Sep 2026, 14:30". Pricing already uses `"el-GR"`
(l.1057, 1063, 1072). **Recommendation: switch both to `"el-GR"`** for one
date format across the app. No e2e asserts these strings, so it is safe.
Flagged rather than silently done because it is a behaviour change, not a
translation.

**N7 — Backend-authored copy is out of scope.** Notification `title`/`message`
(«Price drop: Hotel Gamma 20.0%»), `statistics.notes[]` (pricing l.225, 270),
`rec.reasoning` and `rec.key_factors[]` (l.250, 252), and every error message
surfaced verbatim from FastAPI/Supabase arrive from the server. Round 5.1
translates the frontend only. Leave the render sites alone; note the gap for a
backend round.

**N8 — Three shared sentences must be byte-identical across agents.** Coordinate
or you will ship three spellings of the same thing:
- A-27 ≡ SV-1: `Η συνεδρία σας έληξε. Συνδεθείτε ξανά.` (auth agent + shell agent)
- M-58 ≡ P-38: `Η άφιξη δεν μπορεί να είναι στο παρελθόν.` (map + pricing)
- M-59 ≡ P-37: `Η αναχώρηση πρέπει να είναι μετά την άφιξη.` (map + pricing)
- M-71 ≡ P-33: `Δεν ήταν δυνατή η φόρτωση των στοιχείων του λογαριασμού.` (map + pricing)
- Nav labels M-6/S-4 (`Τιμολόγηση`), M-7/P-4 (`Ρυθμίσεις`), P-3/S-3 (`Χάρτης`)

**N9 — M-53 is written at three call sites** (map l.425, 1265, 1772) with the
same English text today. Extract it to a module constant beside the other five
Greek constants at the top of the file rather than translating it three times.

**N10 — Greek punctuation.** Use «guillemets» for quoted UI names (the existing
map constants and the settings copy already do). Use the Greek question mark
`;` (U+003B), never `?` — this matters for S-45 (`window.confirm`). No final
period inside button labels or table headers. Keep the existing `&ndash;`,
`&middot;`, `&euro;` HTML entities exactly where they are.

---

## Counts

| File | Strings to translate | Already Greek (skip) |
|---|---|---|
| `pages/auth-page.component.ts` | **55** | 1 |
| `pages/map-page.component.ts` | **87** (incl. 1 required a11y ADDITION, M-78) | ~20 |
| `pages/pricing-page.component.ts` | **49** (10 are the §7 mandatory glossary block) | ~22 |
| `pages/settings-page.component.ts` | **57** | 11 |
| `components/notification-bell.component.ts` | **11** | 1 (the `Ειδοποιήσεις` fallback) |
| `components/notification-toasts.component.ts` | **1** | 0 |
| `services/api-client.service.ts` | **4** | 0 |
| `services/auth.service.ts` | **12** (SV-6 at 5 call sites) | 0 |
| `index.html` | **1** (`lang` attribute) | 1 (`<title>` stays) |
| `app.component.ts`, `components/icon.component.ts`, `components/empty-state.component.ts`, `services/setup-progress.service.ts`, `services/notifications.service.ts`, `services/onboarding.service.ts`, `services/workflow-storage.service.ts`, `services/api-client-error.ts` | **0** | — |
| **Total** | **277** | — |

**e2e churn:** 7 spec files touched. ~58 POSITIVE assertions to update,
**11 NEGATIVE assertions to RE-DERIVE** (map-page-polish:327;
no-second-click:448, 653, 705, 741, 769, 770, 827, 879 — plus the two numeric
ones at empty-states:45-46 and recommendation-card:149 that need no change but
should be re-read). 3 `getByLabel` regexes in setup-wizard.spec.ts (124, 125,
965) will resolve to 0 elements until the auth agent updates them.
