# RoomRate — Next Work Queue (handoff, 2026-08-12)

> **Για το νέο session:** αυτό είναι το πλήρες, διατεταγμένο backlog μετά την
> ολοκλήρωση του onboarding wizard plan (11/11) και του live-fix round.
> Εκτέλεση με superpowers:subagent-driven-development (implementer → spec
> review με mutation kill table → quality review· τα ευρήματα διπλώνουν πίσω
> στο εκάστοτε plan αρχείο). Κάθε στοιχείο εδώ είναι αυτοτελές — δεν
> χρειάζεται το ιστορικό της συζήτησης που το γέννησε.

## Κατάσταση αφετηρίας

- ~~Branch: `feature/onboarding-setup-wizard`, head `d3293b1`, pushed·
  PR #9 ανοιχτό προς main~~ **PR #9 ΕΓΙΝΕ merge (2026-08-12, `6641663`)·
  Round 2 = PR #10, ΕΓΙΝΕ merge (2026-08-22, `e77a9ad`)**· το Round 3
  δουλεύτηκε σε νέο branch `feature/flow-correctness-round3` από εκεί
  (βλ. Ευρήματα εκτέλεσης στα §3.1/§3.2/§3.3).
- Baselines μετά το Round 3: frontend e2e **106 passed + 1 skipped**
  (`npx playwright test` από `frontend/`· το skipped είναι το
  live-walkthrough που αυτο-παρακάμπτεται χωρίς ζωντανό backend· ιστορικό:
  93+1 στην αφετηρία, 104+1 μετά το Round 2), backend **601 passed**
  (ιστορικό: 570 → 586 → 601)
  (`python -m pytest api/tests -q --basetemp=<session scratchpad>/pytest-tmp`
  — το default %TEMP% είναι μπλοκαρισμένο στο sandbox), production build
  καθαρό.
- Servers για live δουλειά: `.claude/launch.json` → `api` (uvicorn :8000),
  `roomrate-frontend` (:4200). Ο λογαριασμός δοκιμών για live flows
  δημιουργείται φρέσκος (`backup.planahead+smokeMMDDx@gmail.com`, το
  Confirm-email είναι OFF στο dev Supabase project `nycfqostjdjaynstaloo`)·
  οι παλιοί 6 smoke auth χρήστες περιμένουν διαγραφή από τον ιδιοκτήτη στο
  dashboard (τα app δεδομένα τους έχουν ήδη καθαριστεί).
- Standing rules που ΔΕΝ αλλάζουν (κληρονομιά των δύο plans, παραβίαση =
  review finding): Playwright wildcard mocks ΠΡΩΤΑ (LIFO), `**` όπου
  υπάρχουν query strings, recording mocks για ό,τι κρίνει ένα test,
  non-default τιμές στα fixtures, signals για κάθε state γραμμένο μετά από
  await (frontend/AGENTS.md), κανένα emoji / ελληνικά για νέο UI κείμενο,
  τα seeds του `seedBrowserState` είναι νεκρά μετά το bind — subject-scoped
  κλειδιά (`roomrate_…:e2e-user`) μέσω addInitScript.

---

## ROUND 2 — Η εμπειρία αναζήτησης (πρώτο, πελατο-ορατό)

### 2.1 Ο διαχωρισμός twin/double αδειάζει τα αποτελέσματα — chip task_da739cac

**Απόδειξη (live, 2026-08-12):** job `6510897e-068f-4c97-b011-4e35580a9b36`
(λογαριασμός Rea Hotel, Φαληράκι 11→15/9, επιλεγμένο δωμάτιο «Δίκλινο
Δωμάτιο με 2 Μονά Κρεβάτια, Ντους και Μπαλκόνι»): το scrape γύρισε 8
πραγματικές γραμμές (Aurora Living 147-185€, Esperos Mare 478-614€ — δες
`output/scrapes/scrape_job_6510897e….csv`), το UI έδειξε «Δεν βρέθηκαν
συγκρίσιμα δωμάτια».

**Μηχανισμός:** `api/services/room_rates_normalizer.py`
`normalize_room_type_category` ελέγχει την ομάδα **twin** (γραμμή ~123:
"2 μονά", "μονά κρεβάτια"…) ΠΡΙΝ το **double** (γραμμή ~124: "δίκλινο",
"διπλό")· τα πανταχού παρόντα ελληνικά labels «Δίκλινο Δωμάτιο με 1 Διπλό ή
2 Μονά Κρεβάτια» — ένα φυσικό δωμάτιο που πουλιέται ως double-ή-twin —
κατατάσσονται όλα twin, ενώ το φίλτρο κατηγορίας της αναζήτησης περίμενε
άλλο bucket → όλα τα συγκρίσιμα κόπηκαν.

**Δουλειά:**
1. Εντόπισε από πού παίρνει το φίλτρο την κατηγορία η ροή competitor-search
   (room_type_category του job — από το onboarding-selected category — vs η
   κανονικοποίηση του δωματίου που διάλεξε ο χρήστης στο dropdown του χάρτη)
   και ευθυγράμμισέ τα.
2. Αντιμετώπισε double+twin ως ΜΙΑ συγκρίσιμη δεξαμενή στο matching (το
   `api/services/room_matching.py` ήδη έχει same_category bonus και
   capacity λογική για διαφοροποίηση ΜΕΣΑ στη δεξαμενή — δες το docstring
   του με τη φόρμουλα σκοραρίσματος).
3. Όταν αναζήτηση ολοκληρώνεται με γραμμές που ΟΛΕΣ κόπηκαν από κατηγορία,
   το UI να λέει την αλήθεια: «Βρέθηκαν N δωμάτια άλλης κατηγορίας…» αντί
   για σκέτο «δεν βρέθηκαν» (η ροή γράφει resultHint/κενές καταστάσεις στο
   map-page — οι truthful-per-status κανόνες του Task 9 ισχύουν).
4. Tests: backend pins για τον normalizer/matching (api/tests, house style:
   fakes που καθρεφτίζουν υπογραφές και καταγράφουν κλήσεις), και mocked
   e2e για το νέο μήνυμα. Αν η πηγή-του-φίλτρου αποδειχθεί σχεδιαστική
   απόφαση κάπου, διόρθωσε ΚΑΙ το έγγραφο (standing κανόνας: τα reviews
   βρίσκουν και plan bugs).

**Ευρήματα εκτέλεσης (2026-08-16, branch `feature/search-experience-round2`,
implementer + spec review με kill table — και τα τρία plan bugs
επιβεβαιώθηκαν ανεξάρτητα από τον reviewer πάνω στα artifacts):**

- **Η «Απόδειξη» παραπάνω ήταν λάθος απόδοση.** Το CSV του job `6510897e`
  έχει 8 γραμμές ΟΛΕΣ `room_type_category = twin` — το ίδιο bucket με το
  job (`scraper.log:352-355`: `Room type filter 'twin': 34 -> 10`, name
  filter `10 -> 8`, 8 packages γράφτηκαν). Τίποτα δεν κόπηκε από κατηγορία
  εκεί· η κενή οθόνη εκείνου του job οφείλεται στο stale `restoredStay`
  (δες 2.2). Ο μηχανισμός twin/double είναι ΠΑΝΤΩΣ αληθινός — αποδείξεις
  από άλλα jobs: `scraper.log:308` (513ded50, `'double': 62 -> 4`), `:329`
  (b3a1c5e8, `'double': 29 -> 4`), `:150` (2026-05-21, `'double': 4 -> 0`,
  άδειασε τελείως).
- **Βήμα 1 — η πηγή του φίλτρου ΗΤΑΝ ήδη ευθυγραμμισμένη.** Το dropdown του
  χάρτη διαβάζει `roomrate_owned_property_room_types.room_type_category`, που
  αντιγράφεται από `normalize_room_type_category(room_type)`
  (`api/repositories/scrape_jobs_repository.py:469-487`,
  `room_rates_normalizer.py`)· το job στέλνει `selectedRoomType()`, ο
  scraper φιλτράρει με το ίδιο, το read το στέλνει πίσω. Ο διαχωρισμός ήταν
  ΜΕΣΑ στον normalizer (twin πριν double), όχι ανάμεσα σε πηγές. Καμία
  αλλαγή πηγής.
- **Βήμα 2 — τι σημαίνει «μία δεξαμενή» (μην το «διορθώσει» κανείς
  αργότερα):** `comparable_room_type_categories()` στον normalizer, pool
  ΜΟΝΟ για σύγκριση (αποθηκευμένες κατηγορίες, tracking keys, history
  buckets ανέγγιχτα). Το pool εφαρμόζεται στα ΔΥΟ σημεία κοπής —
  `scraper/persistence.py` (`.isin(pool)`) και `api/repositories/
  room_rates_repository.py` (SQL `IN` και στους τρεις query builders:
  latest-snapshot, job-scoped, amenities). Το `same_category` του matching
  μένει ΑΚΡΙΒΕΣ επίτηδες: το pool αποφασίζει ποιος ΜΠΑΙΝΕΙ, το +20 bonus
  κατατάσσει ΜΕΣΑ στο pool (pin: `test_market_service.py::
  test_same_category_bonus_still_ranks_the_exact_bucket_first` — αν γίνει
  pool-based, το bonus παίρνει και τα δύο buckets και χάνεται ο
  διαφοροποιητής). Καταγεγραμμένη απόφαση: το tracked-competitor predicate
  του ίδιου query διευρύνθηκε (`tc.room_type_category IN (pool)` όταν
  ζητείται κατηγορία) — αφορά μόνο `selected_competitors_only=True`,
  δηλαδή τον smart advisor (`api/routers/agents.py`)· χωρίς αυτό ένα pooled
  read θα έκρυβε ακριβώς τους cross-bucket tracked ανταγωνιστές.
- **Βήμα 3 — το `resultHint` είναι νεκρό state** (5 writes στο map-page,
  0 reads στο template ή οπουδήποτε) — το μήνυμα μπήκε στο ΠΡΑΓΜΑΤΙΚΑ
  αποδιδόμενο empty state. Ο αριθμός N είναι `total_records`, δηλαδή
  γραμμές/προσφορές (το CSV του 6510897e: 8 γραμμές για 4 διακριτά
  δωμάτια), οπότε η λέξη είναι «προσφορές», όχι «δωμάτια»: «Βρέθηκαν N
  προσφορές άλλης κατηγορίας…» (ενικός «Βρέθηκε 1 προσφορά…»). Ο αριθμός
  βγαίνει από δύο πραγματικά reads του ίδιου job που διαφέρουν μόνο στο
  φίλτρο κατηγορίας, διεκδικείται μόνο όταν το εντός-κατηγορίας read είναι
  γνήσια κενό (οι markers ρίχνουν ξενοδοχεία χωρίς συντεταγμένες, άρα κενός
  χάρτης δεν αποδεικνύει κόψιμο από φίλτρο), και επιλύεται ΠΡΙΝ το
  `status.set("ready")`.
- **Ορθογώνιος μηχανισμός, ΟΧΙ ελάττωμα του pool:** το room-name filter του
  scraper (`scraper/matching.py` `_filter_by_room_name`: exact → partial →
  fuzzy ≥0.45, αλλιώς όλα) τρέχει ΜΕΤΑ το pool και είναι bucket-blind — μια
  twin γραμμή που δεν μοιάζει ονομαστικά κόβεται όπως θα κοβόταν και μια
  double. Στα live double jobs (`4 -> 2` στο name filter) το pool μπορεί να
  μην αλλάξει τίποτα στην αποθήκευση για ονόματα τύπου «Deluxe Δίκλινο με
  θέα θάλασσα». Pinned η αλληλεπίδραση (pool πριν το name filter,
  bucket-blind). Το exact tier του cascade ρίχνει ΟΛΑ τα άλλα ξενοδοχεία
  όταν υπάρξει ένα πανομοιότυπο όνομα οπουδήποτε — προϋπάρχον σχεδιαστικό
  ερώτημα recall-vs-precision, βλ. Round 4.
- **Kill table:** 15 mutations, 14 σκοτωμένες. Επιζών F4 («ο αριθμός
  επιλύεται ΠΡΙΝ το ready» — καμία δοκιμή δεν το πιάνει) → ονομαστική
  υποχρέωση του 2.2 (gated-route condition-held pin, το εργαλείο του 2.2).
- **ΙΣΤΟΡΙΚΟ ΕΥΡΗΜΑ — ΕΚΛΕΙΣΕ ΣΤΟ ROUND 4.5.3 (2026-08-28): το μήνυμα του
  βήματος 3 ήταν αδρανές στο live σενάριο.** Το
  βήμα 3 γράφτηκε με την υπόθεση ότι το κόψιμο κατηγορίας γίνεται στο
  read· γίνεται όμως στον scraper ΠΡΙΝ την αποθήκευση
  (`scraper/persistence.py` row_filters, πρώτο φίλτρο) — για
  competitor_search jobs με κατηγορία, το αποθηκευμένο σύνολο S ⊆
  pool(job.category). Και το restore path (`applyRestoredJobFilters` βάζει
  `selectedRoomType` = κατηγορία του job) και η φρέσκια αναζήτηση διαβάζουν
  με την κατηγορία του job → in-category read = any-category read = |S| →
  `inCategory === 0` συνεπάγεται `anyCategory === 0` → ο αριθμός είναι
  πάντα 0 και εμφανίζεται το γενικό κείμενο. Το μόνο προσβάσιμο σενάριο:
  αλλαγή του dropdown δωματίου ΟΣΟ τρέχει το polling. Live επιβεβαίωση:
  `scraper.log:288` `'double': 38 -> 10` — οι 28 κομμένες προσφορές
  υπάρχουν μόνο στο log· το `'double': 4 -> 0` (`:150`) δεν αποθηκεύει
  τίποτα. Ο κώδικας είναι fail-safe (γενικό κείμενο), δεν μπλοκάρει το
  merge του pool. **Επιλογές (απόφαση ιδιοκτήτη, Εκκρεμότητα 7):**
  (α) ΠΡΟΤΕΙΝΟΜΕΝΗ — μεταφορά των μετρητών του scraper στο job: ο scraper
  τυπώνει μία γραμμή JSON summary στο stdout (`rows_seen`, before/after
  ανά row filter, `rows_written`)· ο runner (`_execute` στο
  `scrape_job_service.py`, σήμερα πετάει το stdout) την αναλύει· το
  service την αποθηκεύει σε νέα nullable JSONB στήλη
  `roomrate_scrape_jobs.result_summary` (υλοποιήθηκε ως Alembic migration 0023 — το
  Alembic ΥΠΑΡΧΕΙ: `alembic.ini`, `migrations/versions/` 21 revisions,
  `docs/DOCUMENTATION.md:95,199-205`)· εκτίθεται στο `ScrapeJobResponse`·
  το frontend το χρησιμοποιεί ΑΝΤΙ για τα δύο probe reads όταν το
  job-scoped in-category read είναι 0: αν το φίλτρο κατηγορίας άδειασε τη
  ροή → «Βρέθηκαν N προσφορές άλλης κατηγορίας» (N = before του φίλτρου
  κατηγορίας)· αν την άδειασε επόμενο φίλτρο (όνομα δωματίου/γεύματα/
  ακύρωση/παροχές — το ΣΥΧΝΟΤΕΡΟ live: `4 -> 2` στο name filter) →
  «Βρέθηκαν K προσφορές της κατηγορίας σας, καμία όμως δεν πέρασε τα
  υπόλοιπα φίλτρα»· αλλιώς το γενικό. Τότε το F4 μεταφέρεται στο job
  payload (τετριμμένο) και τα probe reads φεύγουν. (β) αποθήκευση ΟΛΩΝ
  των γραμμών για competitor_search και φίλτρο μόνο στο read — μεγαλύτερο
  (όγκος, σημασιολογία history/alerts). (γ) descope: κράτα το probe μόνο
  για το dropdown-switch σενάριο και ξαναγράψε το βήμα 3 + την πρόταση του
  2.2 για το «θα εμφανιστεί live».
  **Προδιαγραφή της (α) για τον implementer (από το quality re-review,
  επαληθευμένα σημεία):** (i) το logging του scraper πάει στο **stdout**
  (`scraper/logging_config.py:23`), άρα η γραμμή JSON χρειάζεται sentinel
  prefix (π.χ. `ROOMRATE_RESULT_SUMMARY {json}`), parse της ΤΕΛΕΥΤΑΙΑΣ
  τέτοιας γραμμής, μικρό versioned payload, malformed/απούσα → warning +
  NULL (NULL ⇒ γενικό κείμενο). (ii) ΟΛΑ τα exit-0 μονοπάτια πρέπει να την
  εκπέμπουν: `scraper/cli.py:191-193` (`if not hotels: return`), `:201-203`
  (`if not raw_data: return`), το emptied-filter early return
  (`persistence.py:91-93`) και το `df.empty` return· failure
  (`sys.exit(2)`), retry, timeout/kill δεν φτάνουν στο parse — να δηλωθεί.
  (iii) `_execute` ήδη κάνει PIPE το stdout και το πετάει
  (`scrape_job_service.py:256`, `_, stderr_output = communicate(...)`· το
  retry-on-TimeoutExpired loop διατηρεί το output)· `ScrapeJobRunnerProtocol
  .run` / `BookingScrapeJobRunner.run` επιστρέφουν `None` σήμερα
  (`:126-128, :158-163`) → επιστρέφουν το summary, οι fake runners των
  tests αλλάζουν· αποθήκευση μέσα στο `complete_job(...)` (ίδια συναλλαγή
  με τη μετάβαση σε completed, `:487-491`) → summary = η επιτυχημένη
  προσπάθεια (προηγούμενες αποτυχημένες προσπάθειες μπορεί να έχουν ήδη
  γράψει runs/packages που τα job-scoped reads καλύπτουν). (iv) Νέα
  nullable JSONB κληρονομεί τα row policies του `roomrate_scrape_jobs`
  (τα jobs διαβάζονται από τον πίνακα, όχι view) — καμία αλλαγή RLS· αλλά
  `JOB_COLUMNS` (`scrape_jobs_repository.py:17-26`) είναι ρητή λίστα →
  προσθήκη, + `ScrapeJobResponse` (`api/schemas/scrape_jobs.py:67`,
  `result_summary: dict | None = None`) + το frontend interface.
  (v) **Κενό που πρέπει να κλείσει μαζί:** run rows δημιουργούνται μόνο
  μέσα στο `write_normalized_rates`, που καλείται μόνο `if
  normalized_rates` (`persistence.py:132`)· ένα αδειασμένο job (`'double':
  4 -> 0`) ΔΕΝ έχει run row, άρα το `canRestoreJob` (`map-page.component
  .ts`, απαιτεί `scrape_runs_count > 0`) το απορρίπτει και το ειλικρινές
  μήνυμα θα φαινόταν μόνο στη φρέσκια ροή, ποτέ μετά από reload/restore
  → είτε χαλάρωση του `canRestoreJob` για jobs με `result_summary`, είτε
  εγγραφή completed run με 0 packages. (vi) Η συνθήκη στο frontend να
  βγαίνει από το payload (`rows_written === 0` + ποιο φίλτρο άδειασε τη
  ροή), ΟΧΙ από «το job-scoped in-category read είναι 0» — αυτό το read
  κληρονομεί το `restoredStay` (bug του 2.2) και περιττεύει· ένα summary
  read μένει μόνο για το market strip.
- **Ιστορικά ανοιχτά — ΕΚΛΕΙΣΑΝ ΣΤΑ 4.5.1-4.5.2:** price history +
  alerts (`api/repositories/price_history_repository.py:48`,
  `price_alert_service`) φιλτράρουν την κατηγορία ΤΗΣ ΓΡΑΜΜΗΣ με ακριβή
  ισότητα — συνέπεια: ανταγωνιστής που εντοπίστηκε σε double αναζήτηση
  αλλά η μόνη του pooled γραμμή είναι twin παράγει 0 σημεία ιστορικού/
  alerts. **Το preselection ΔΕΝ πάσχει εντός ίδιας κατηγορίας** (διόρθωση
  του final review, 2026-08-17): το tracked row αποθηκεύεται με την
  κατηγορία ΤΗΣ ΑΝΑΖΗΤΗΣΗΣ (`saveTrackedCompetitors` στέλνει το
  `selectedRoomType()`), το `/tracked/competitors` φιλτράρει στο tracked
  κλειδί, και το `applyTrackedCompetitors` ταιριάζει markers με
  property/package id — άρα ο ίδιος ανταγωνιστής ΠΡΟ-τσεκάρεται σε
  επόμενες double αναζητήσεις. Ο ακριβής περιορισμός του preselection
  είναι ΔΙΑ-κατηγορικός: tracked κάτω από double → αναζήτηση κάτω από
  twin δεν τον προ-τσεκάρει. Επιπλέον exact-category σημείο: το
  `canRestoreJob` (map-page) απαιτεί `job.room_type_category ===
  selectedRoomType()` — ένα ολοκληρωμένο twin job δεν γίνεται restore με
  double dropdown παρότι οι γραμμές του διαβάζονται από το pool
  (συντηρητικό, μάλλον σωστό· στη λίστα της απόφασης 5 για πληρότητα).
  Το αγγλικό header alert «No competitor rooms matched…» δίπλα στο
  ελληνικό empty state → Round 5.1.

### 2.2 Πάτημα «Find Competitors» εν μέσω φόρτωσης δείχνει «ολοκληρώθηκε/κενό» — chip task_4d711463

**Απόδειξη (live, 2026-08-11 ~23:48, job b3a1c5e8):** κλικ στο Find ~1s μετά
το άνοιγμα του /map — ενώ η αλυσίδα επαναφοράς του προηγούμενου job έτρεχε
ακόμα — άφησε το UI σε ready-empty («Η αναζήτηση ολοκληρώθηκε αλλά δεν
επέστρεψε…») για τα 2+ λεπτά που το νέο job έτρεχε στο backend.

**Μηχανισμός:** το `findCompetitors` καθάρισε τη λίστα και έβαλε
status='loading', αλλά η ουρά της ΠΑΛΙΑΣ αλυσίδας (`loadMarkers` γραμμή
~862 `status.set("ready")`) πάτησε το loading με άδεια λίστα. Είναι η
status-εκδοχή του «two concurrent chains» residual που κατέγραψε το Fix-B
review.

**Δουλειά:** generation token/chain id — το `loadMarkers` (και το poll της
αναζήτησης) αιχμαλωτίζει generation στην είσοδο· κάθε late write (status,
competitors, και η συνεργασία με το υπάρχον `userTouchedSelection` guard)
εφαρμόζεται μόνο αν το generation είναι ακόμα τρέχον· το `findCompetitors`
το προχωράει. Pin με το καθιερωμένο condition-held μοτίβο του
`no-second-click.spec.ts` (τα δύο υπάρχοντα gated-route σενάρια είναι το
πρότυπο): κράτα με πύλη το maps/competitors της ΠΡΩΤΗΣ αλυσίδας, πάτα Find
στη διάρκεια, ελευθέρωσε, βεβαίωσε ότι ΔΕΝ εμφανίζεται το ready-empty όσο
το νέο job (mock ως running) τρέχει.

**Προστέθηκαν από τα ευρήματα του 2.1 (2026-08-16) — ΜΕΡΟΣ του scope του
2.2, όχι προαιρετικά:**

- **Stale `restoredStay` (η ΠΡΑΓΜΑΤΙΚΗ αιτία της κενής οθόνης του job
  6510897e).** Στο `map-page.component.ts` το `restoredStay` γράφεται μόνο
  στο `applyRestoredJobFilters` (~:967) και δεν καθαρίζεται ποτέ· το
  `buildMarkerParams` (~:1199) το προτιμά για ΚΑΘΕ job-scoped read
  (`(scrapeJobId && this.restoredStay) || {checkIn: filters.check_in, …}`)
  ενώ το job-scoped SQL (`room_rates_repository.py`, ~:373) απαιτεί
  `rates.check_in = :check_in`. Ροή: restore του προηγούμενου job
  (513ded50/b3a1c5e8, 10→14/9) → αλλαγή δωματίου/ημερομηνιών → Find → το ΝΕΟ
  job (11→15/9) διαβάζεται με τις ΠΑΛΙΕΣ ημερομηνίες → 0 γραμμές για scrape
  που έγραψε 8. Διόρθωση: το stay ενός job-scoped read να προέρχεται από
  το ΙΔΙΟ το job (reset/overwrite του `restoredStay` από το `completedJob`
  πριν το `loadMarkers(completedJob.id)`, ή καθάρισμα όπου ξεκινά φρέσκια
  αναζήτηση)· pin με e2e που κάνει restore παλιού job με άλλες ημερομηνίες,
  τρέχει νέα αναζήτηση, και βεβαιώνει (recording mock) ότι το job-scoped
  read φέρει τις ημερομηνίες του ΝΕΟΥ job. (Σημείωση: ανεξάρτητα από αυτό,
  το μήνυμα του 2.1 με αριθμό μένει αδρανές live μέχρι την Εκκρεμότητα 7 —
  εδώ διορθώνεται το ότι ένα ΝΕΟ job διαβάζεται με ΞΕΝΕΣ ημερομηνίες και
  δείχνει κενό ενώ έχει 8 γραμμές.)
- **Υποχρέωση F4 (mutation survivor του 2.1):** pin ότι ο αριθμός
  «προσφορές άλλης κατηγορίας» επιλύεται ΠΡΙΝ το `status.set("ready")` —
  gated-route condition-held: κράτα με πύλη το categoryless probe read,
  βεβαίωσε ότι το ready-empty (χωρίς αριθμό) ΔΕΝ εμφανίζεται όσο η πύλη
  είναι κλειστή, ελευθέρωσε, βεβαίωσε το πλήρες μήνυμα. (Αν προηγηθεί η
  επιλογή (α) της Εκκρεμότητας 7, το probe φεύγει και το pin γίνεται
  «ο τίτλος του empty state υπολογίζεται από το job payload πριν το
  ready».)
- **Αφαίρεση του νεκρού `resultHint`** (signal + 5 writes, 0 reads) — το 2.2
  ξαναγράφει τα ίδια σημεία (findCompetitors/loadMarkers/reset)· να φύγει
  αντί να παραπλανά τον επόμενο αναγνώστη όπως παραπλάνησε αυτό το plan.

**Ευρήματα εκτέλεσης (2026-08-16/17, implementer + spec review με kill
table):**

- **Μηχανισμός επιβεβαιωμένος**, με δύο residuals που το plan δεν ονόμαζε:
  (1) το `marketSummary` (`loadMarketSummary`) ανήκει στο ίδιο residual —
  live-αναπαραγωγή: stale «42 records» strip δίπλα στο ready-empty· (2) το
  `applyRestoredJobFilters` είναι ΔΕΥΤΕΡΗ οδός προς το stale-dates bug —
  restore που προσγειώνεται ΜΕΤΑ από Find θα ξανάγραφε τα φίλτρα
  ημερομηνιών της τρέχουσας αναζήτησης (φρουρείται πλέον).
- **Σχεδιαστική διόρθωση στο σκίτσο του plan:** το generation ΔΕΝ
  συλλαμβάνεται «στην είσοδο του loadMarkers» — μια stale restore αλυσίδα
  που καλεί το `loadMarkers` αργά θα συλλάμβανε το ΝΕΟ generation και θα
  περνούσε κάθε φρουρό. Το generation είναι του ΚΑΛΟΥΝΤΟΣ και περνά ως
  ρητή παράμετρος (`loadMarkers`, `loadMarketSummary`,
  `refreshRoomMatches`)· το `findCompetitors` το προχωρά ΠΡΩΤΟ, πριν
  καθαρίσει state. (Ο reviewer σημειώνει: με τον φρουρό της restore
  αλυσίδας μετά το job lookup, η σύλληψη-στην-είσοδο θα ήταν σήμερα
  ισοδύναμη· η παράμετρος είναι defense-in-depth για μελλοντικό caller που
  κάνει await ανάμεσα στον φρουρό του και το loadMarkers.)
- **`restoredStay` → `activeJobStay`:** τίθεται ΜΟΝΟ στο `loadMarkers` από
  υποχρεωτική παράμετρο `jobStay` (η υπογραφή επιβάλλει «job-scoped read
  = stay του ίδιου job»)· `findCompetitors` περνά `completedJob.check_in/
  out` (οι ημερομηνίες που έτρεξε το backend, όχι τα inputs τη στιγμή του
  read)· καθαρίζεται στην έναρξη αναζήτησης και στο reset. Plain fields
  `chainGeneration` (γράφεται μόνο συγχρονισμένα στον click handler, ποτέ
  rendered — εκτός του κανόνα του AGENTS.md κατά γράμμα και ουσία) και
  `activeJobStay` (γράφεται μετά από await αλλά δεν διαβάζεται από
  template/computed/effect — αδελφό του `activeScrapeJobId`, chip
  task_a934b51e, Round 4.4: λύση και για τα δύο μαζί ή διευκρίνιση του
  AGENTS.md σε «state που παρατηρείται από το rendering»).
- **«Δεύτερο Find αδύνατο» — επαληθευμένο** (μοναδικός caller το κουμπί,
  `type="button"`, `actionStatus.set("loading")` συγχρονισμένα πριν το πρώτο
  await), με ΜΙΑ τρύπα που κλείνει σε αυτό το task: η ουρά του `ngOnInit`
  (`resetCompetitorSearchState()` μετά από `await loadAmenities()`
  ξανα-ενεργοποιεί το Find, μετά η restore αλυσίδα συλλαμβάνει
  ήδη-προχωρημένο generation) — πρακτικά απρόσιτο από άνθρωπο, φθηνή
  διόρθωση (σύλληψη generation στην αρχή του ngOnInit ή bail της restore
  όταν `actionStatus() === "loading"`).
- **Νέο chip task_8fecd58c (εκτός scope, ΔΕΝ διορθώθηκε):** ο error handler
  του `initializeMap` (Mapbox) καλεί `setError`, που βάζει το status των
  ΑΠΟΤΕΛΕΣΜΑΤΩΝ σε "error" και σβήνει το `message` — μια καθαρά
  παρουσιαστική αποτυχία χάρτη αδειάζει ολόκληρο το sidebar. Αυτό έκανε το
  race test να flake-άρει υπό φόρτο (το `environment.development.ts` έχει
  πραγματικό token, οπότε το abort του Mapbox στα mocks πυροδοτεί error σε
  ΚΑΘΕ test)· το test A σερβίρει ελάχιστο έγκυρο style. Round 4.4 υποψήφιο.
- **Kill table:** m1/m2/m4b/m5 σκοτωμένα· m3/m4c ισοδύναμοι mutants·
  m6/m7/m8 (guards `marketSummary`, restore-μετά-lookup, restore `catch`)
  επέζησαν → pinned σε αυτό το task (βλ. commits μετά το 4362d1b)· m9
  (`refreshRoomMatches` guard — κανένα e2e δεν ενεργοποιεί «Match my
  room») και m10 (πρώτος loadMarkers guard, pinned μόνο συνολικά από το
  test A) μένουν ΓΝΩΣΤΟΙ μη-pinned φρουροί. Επίσης μη-φρουρημένο,
  θεωρητικό, προϋπάρχον: `findRestorableCompetitorJob` →
  `workflow.set("lastCompetitorJobId", null)` σε catch μετά από await.
- **Quality review (approved, ready to merge)** — καταγεγραμμένες αποφάσεις
  και residuals: (1) **stay-από-το-job vs κατηγορία-από-το-dropdown**: το
  2.2 δένει το STAY ενός job-scoped read στο job, αλλά η ΚΑΤΗΓΟΡΙΑ
  εξακολουθεί να έρχεται από το ζωντανό `selectedRoomType()`
  (`buildMarkerParams`), και το `onRoomTypeChange` ούτε προχωρά το
  generation ούτε απενεργοποιείται όσο τρέχει αναζήτηση → αλλαγή δωματίου
  στα 2+ λεπτά του scrape διαβάζει το τελειωμένο job με κατηγορία για την
  οποία δεν έγινε scrape. ΣΥΝΕΙΔΗΤΗ κατάσταση προς το παρόν (είναι το
  μοναδικό προσβάσιμο μονοπάτι του μηνύματος «άλλης κατηγορίας» του 2.1
  και εκεί το μήνυμα είναι αληθές)· εναλλακτικές: πέρασμα
  `completedJob.room_type_category` μαζί με το stay (γενίκευση `jobStay`
  → job scope) ή απενεργοποίηση του room select όσο `actionStatus() ===
  "loading"`. Απόφαση μαζί με την Εκκρεμότητα 7. (2) **Προϋπάρχον, εκτός
  scope → 4.4:** restore αλυσίδα ΜΗ superseded της οποίας το marker read
  αποτυγχάνει αφήνει το `status` κολλημένο σε "loading" για πάντα (το
  restore `catch` επαναφέρει μόνο το `message`) — «Searching / Waiting…»
  δίπλα σε «Set filters and run Find Competitors…» με το Find ενεργό·
  διόρθωση `status.set("idle")` στο catch + ένα e2e (το m8 mock είναι το
  ίδιο μονοπάτι σε superseded εκδοχή). (3) **Round 5.1 churn:** οι
  θετικές αγγλικές βεβαιώσεις των νέων tests («Waiting for the live scrape
  to finish.», «Searching matching competitor rooms», κουμπιά Searching/
  Find Competitors/Filters, header «Loading») θα σπάσουν φωναχτά — καλό·
  οι ΑΡΝΗΤΙΚΕΣ (`not.toContainText("last completed")`, `("Set filters and
  run Find Competitors")`, `("42 records")`) θα περάσουν σιωπηλά μετά τη
  μετάφραση → το 5.1 τις ξανα-παράγει (ή βεβαιώνει παρουσία/απουσία του
  `.market-summary-block` με test id αντί για string). (4) Οι πέντε race
  tests κοστίζουν ~5s ο καθένας (`letSupersededChainSettle` = διάστημα
  poll της εφαρμογής, όχι sleep) και το own-dates ~10s — δεσμευμένος
  χρόνος εφαρμογής, αποδεκτό (suite 47s)· αυτό είναι το cost model για
  νέα σενάρια αυτού του σχήματος. (5) Sharpenings που έγιναν πριν το
  κλείσιμο: Mapbox abort + empty style και στο
  `mockCompletedSearchWithEmptyMap` (το F4 test άγγιζε πραγματικό
  api.mapbox.com σε παράθυρο `status === "loading"`)· entry guard στο
  `loadMarkers` (defense-in-depth, ισοδύναμος mutant σήμερα)· `requested`
  απόδειξη στο m6 test· `loadMarkers(scrapeJobId: string, …, jobStay:
  StayDates)` ώστε η υπογραφή να επιβάλλει πράγματι το «job-scoped read =
  stay του job».

---

## ROUND 3 — Ορθότητα ροής/υποδομής

### 3.1 Ενεργοποίηση της scout cache (engine threading) — chip task_02b7f086

Το `PROPERTY_CANDIDATES_CACHE_HOURS` (default 24, commit 7228490, tests στο
`api/tests/test_onboarding_service.py`) είναι σωστά καλωδιωμένο αλλά
ΑΔΡΑΝΕΣ: και οι δύο κλήσεις `fetch_hotel_list` στο
`api/services/onboarding_service.py` (~:127/:147) περνούν `engine=None`,
και το `scraper/scout.py` πυροδοτεί cache read (:181-184) και write
(:233-234) μόνο με ζωντανό engine. Κόστος σήμερα: κάθε αναζήτηση υποψηφίων
= κρύο scrape ~72s (live μέτρηση) + Apify credits. Δουλειά: πέρασε το
υπάρχον engine/session infrastructure της εφαρμογής (ΟΧΙ δεύτερο pool) στα
scout calls· cache key = (destination, check_in, check_out, adults,
children, rooms, currency, language) — exact match, ΧΩΡΙΣ limit — άρα το
auto-setup ζεσταίνει γνήσια το «Δείξε άλλα» και το change-flow refine.
Tests στο provider seam. Acceptance: live change-flow refine ΑΠΟ CACHE σε
δευτερόλεπτα μετά από auto-setup ίδιας διαμονής. Μετά, αφαίρεσε το «No
effect yet» caveat από το `.env.example`.

**Ευρήματα εκτέλεσης (2026-08-22, branch `feature/flow-correctness-round3`,
implementer + spec review με kill table — όλα επιβεβαιωμένα στα artifacts):**

- **Υλοποίηση:** optional `engine` param στον `BookingPropertyCandidateProvider`,
  επιλύεται ΤΕΜΠΕΛΙΚΑ μέσα στο `search()` μέσω `get_engine(role="api")`
  (μία φορά ανά search, κοινό και για τα δύο scout calls — και το retry)·
  αποτυχία επίλυσης υποβαθμίζεται σε `engine=None` (uncached) με WARNING
  ανά search — το cache είναι optimization, ποτέ 500. Η τεμπελιά είναι
  συμπεριφορική ιδιότητα (pinned): transient αποτυχία ΘΕΡΑΠΕΥΕΤΑΙ στο
  επόμενο search και το warning ξανα-πυροδοτείται· eager θα πάγωνε την
  αποτυχία ανά instance.
- **Το scout.py δεν χρειάστηκε καμία αλλαγή** — το `cache_hours == 0`
  short-circuit υπήρχε ήδη (πριν από κάθε DB άγγιγμα) και το write ήταν
  ήδη gated σε `> 0`· έλειπε μόνο το engine.
- **Το migration 0015 ΔΕΝ είναι account scoping** — πρόσθεσε τις
  διαστάσεις διαθεσιμότητας (adults/children/rooms/currency/language) στο
  κλειδί· το `scout_cache` είναι ΣΚΟΠΙΜΑ global (δημόσια αποτελέσματα
  Booking, όχι tenant data). Το κλειδί του plan είναι σωστό — verified στο
  SQL (lookup WHERE + eviction DELETE), χωρίς limit και χωρίς account.
- **RLS:** το 0021 ενεργοποιεί RLS στο scout_cache με ΜΗΔΕΝ policies,
  αλλά το backend συνδέεται ως ΙΔΙΟΚΤΗΤΗΣ του πίνακα (χωρίς FORCE RLS) →
  εξαιρείται (DOCUMENTATION.md:838-843). Failure mode αν αλλάξει ο ρόλος:
  μηδενικές γραμμές ΧΩΡΙΣ σφάλμα στο read, denied INSERT με WARNING στο
  write.
- **Provenance πίνακα:** ΚΑΝΕΝΑ migration δεν δημιουργεί το scout_cache
  (όλα φρουρούνται με `to_regclass(...) IS NOT NULL`) — το φτιάχνει μόνο
  το legacy `_metadata.create_all` του scraper (`scraper/cli.py:169`,
  non-dry-run). Συνειδητή απόφαση να ΜΗΝ το δημιουργεί το API (create_all
  από το API θα παρήγαγε πίνακα ΧΩΡΙΣ το RLS που έστησε το 0021).
  Live προϋπόθεση: `SELECT to_regclass('scout_cache')` — αν NULL, τρέξε
  ένα non-dry-run scrape πρώτα.
- **Αλληλεπίδραση row-count (όχι bug, σχεδίαση του κλειδιού-χωρίς-limit):**
  όποιο search ζεστάνει πρώτο το cache καθορίζει πόσες γραμμές βλέπουν τα
  επόμενα ίδιας διαμονής. Στενότερο από ό,τι φαινόταν: τα ΧΕΙΡΟΚΙΝΗΤΑ
  competitor jobs τρέχουν με `scout_cache_hours=0` (ούτε διαβάζουν ούτε
  γράφουν)· μόνο τα SCHEDULED competitor scrapes ίδιας διαμονής
  (scout_max_items=10 vs onboarding 12-25) συμμετέχουν. Ασυμμετρία
  φρεσκάδας: onboarding διαβάζει έως 24h, scheduled jobs έως 12h.
- **Live acceptance checklist (για το live σκέλος):** (1) πίνακας υπάρχει·
  (2) `DATABASE_URL` = ρόλος-ιδιοκτήτης (`SELECT current_user,
  pg_get_userbyid(relowner) FROM pg_class WHERE relname='scout_cache'`)·
  (3) ΙΔΙΕΣ παράμετροι διαμονής (το destination είναι raw-text ισότητα —
  «Faliraki» ≠ «Φαληράκι» ≠ trailing space)· (4)
  `PROPERTY_CANDIDATES_CACHE_HOURS > 0` στο ζωντανό `.env` και εντός
  παραθύρου· (5) θετικό σήμα: γραμμή log `[ΣΤΑΔΙΟ 1] Cache HIT` (ο root
  logger του API είναι INFO και τα scraper logs προωθούνται εκεί) ή
  `SELECT count(*), max(cached_at) FROM scout_cache WHERE destination=…`
  μετά το auto-setup· αρνητικά σήματα ΔΥΟ: «Scout cache disabled for
  candidate search» (μη-επιλύσιμο engine — ρεαλιστικά μόνο dev/internal
  path, στην production το boot θα είχε σκάσει νωρίτερα) και «Αποτυχία
  αποθήκευσης scout cache» (πίνακας λείπει ή denied write — το read-side
  failure λογάρει μόνο DEBUG, αόρατο σε INFO root, οπότε ΑΥΤΟ είναι το
  ορατό σήμα για λείπον table/RLS).
- **Ταυτόχρονα cold searches ίδιας διαμονής (ανάλυση, όχι αλλαγή):** το
  unique constraint + `on_conflict_do_update` σημαίνει κανένα σφάλμα και
  κανένα duplicate· residuals: σε overlap χιλιοστών του δευτερολέπτου το
  cache μπορεί να μείνει με την ΕΝΩΣΗ των δύο συνόλων (όλα γνήσια
  ξενοδοχεία, αυτο-θεραπεύεται στο επόμενο write), και ένα θεωρητικό
  deadlock δύο multi-VALUES upserts λύνεται από την Postgres μετά το
  deadlock_timeout και καταλήγει στο benign WARNING «Αποτυχία
  αποθήκευσης» — το search δεν επηρεάζεται. Στο live triage: αυτό το
  WARNING μεμονωμένο = αβλαβές.
- **Final review Round 3 (2026-08-23) — cache-HIT candidates έχαναν τη
  διεύθυνση, ΔΙΟΡΘΩΘΗΚΕ (`08a3fe8`):** το `scout_cache` δεν είχε στήλη
  `address` ενώ ο scout την επιστρέφει και ο `_candidate_from_hotel` τη
  διαβάζει → στα warm paths («Δείξε άλλα», refine, same-day
  same-destination sign-up) οι κάρτες έβγαιναν χωρίς διεύθυνση και το
  κατάλυμα αποθηκευόταν με NULL address. Ευρύτερο από το onboarding: ο
  scrape-job worker (`scraper/transform.py` flatten) έχανε address σε ΚΑΘΕ
  cache hit από πριν το 3.1 (πάντα περνούσε engine) — ο ίδιος fix το
  θεραπεύει. Προστέθηκε στήλη (`scraper/tables.py` + **Alembic
  `20260823_0022`**, guarded `ADD COLUMN IF NOT EXISTS`), write + conflict
  update + read (NULL → ""), 4 mutations killed. Key-set diff: από τα 10
  κλειδιά του scout hotel dict έλειπε ΜΟΝΟ το address. **Η 0022
  εφαρμόστηκε στο dev DB (2026-08-23, `alembic upgrade head`)** — σε
  production ο deploy πρέπει να την τρέξει πριν ζεσταθεί το cache, αλλιώς
  κάθε write αποτυγχάνει («Αποτυχία αποθήκευσης scout cache») και το
  cache μένει κρύο.
- **Συγγενές, προϋπάρχον (→ 4.4):** `PropertyCandidate.country` διαβάζεται
  από το `automatic_setup` αλλά ο `_candidate_from_hotel` δεν το θέτει ποτέ
  και ο `_extract_meta` δεν το επιστρέφει — country = None σε κρύο ΚΑΙ
  ζεστό path. Είτε αφαίρεση του νεκρού read είτε εξαγωγή από το Apify item.
- **Live acceptance (2026-08-23):** ΜΠΛΟΚΑΡΙΣΜΕΝΟ στο Apify — το `.env` της
  εφαρμογής ΔΕΝ έχει `APIFY_TOKEN` (ούτε το shell env), οπότε πραγματικό
  cold scout δεν μπορεί να τρέξει από το περιβάλλον ανάπτυξης. Έγινε αντ'
  αυτού live-πλην-Apify επαλήθευση στο dev DB (πραγματικό engine, πραγματικό
  SQL write/read, RLS, `Cache HIT` log) με συνθετικό actor και καθαρισμό των
  συνθετικών γραμμών — βλ. την παράγραφο αποτελεσμάτων παρακάτω. Το
  πραγματικό cold→warm το αποδεικνύει το επόμενο sign-up σου με το token
  στο `.env` (checklist παραπάνω).
- **Αποτέλεσμα live-πλην-Apify (2026-08-23, dev DB, μετά το 0022):** PASS.
  Πραγματικός `get_engine(role="api")`, πραγματικό `scout_cache`, RLS
  owner-exempt. COLD: log `Cache MISS — τρέχω Apify actor`, ο (συνθετικός)
  actor κλήθηκε 1 φορά, 2 γραμμές γράφτηκαν ΜΕ address. WARM (ίδια
  διαμονή: destination/dates/3 ενήλικες/1 παιδί/2 δωμάτια, cache_hours
  24): log `Cache HIT — 2 ξενοδοχεία (< 24h παλιά). 0 Apify credits`, ο
  actor ΔΕΝ ξανακλήθηκε, ίδια candidates με address άθικτη, ~0.00s. Οι
  συνθετικές γραμμές διαγράφηκαν (2/2, 0 υπόλοιπο). Άρα από το checklist
  επαληθεύτηκαν live τα (1) πίνακας, (2) ρόλος-ιδιοκτήτης, (4) cache_hours
  > 0 (default 24 — το `.env` δεν ορίζει τη μεταβλητή), (5) τα θετικά
  σήματα· μένει ΜΟΝΟ το ίδιο το Apify call (token).

### 3.2 Αφαίρεση του backend room auto-select μετά το discovery — chip task_99488b2f

**Απόφαση ιδιοκτήτη (2026-08-11): ΑΦΑΙΡΕΣΗ.** Σήμερα, όταν ολοκληρώνεται
owned_property_room_discovery job, το backend γράφει μόνο του
selected_room_type_category → ο setupGuard βλέπει «πλήρη» λογαριασμό και
όποιος αργήσει στο βήμα 1 προσπερνά τα βήματα 2-3 του οδηγού
(live-παρατηρημένο: ο +smoke0811 βγήκε complete χωρίς να δει ποτέ το βήμα
2). Δουλειά: βρες το write στο discovery-completion path (ψάξε στο
api/services/ πού γράφεται selected_room_type στο job completion), αφαίρεσέ
το ώστε το δωμάτιο να γράφεται ΜΟΝΟ από το ρητό PUT
`/owned-property/{id}/selected-room-type`, pin «discovery completion does
not select a room», ενημέρωσε συνειδητά όσα backend tests υπέθεταν το
παλιό, τρέξε και το wizard e2e (η ροή guard→βήμα 2 τώρα ΘΑ συμβαίνει — αυτό
είναι το ζητούμενο). Χωρίς migration — υπάρχοντες λογαριασμοί κρατούν την
επιλογή τους.

**Ευρήματα εκτέλεσης (2026-08-22, plan bug — το scope ήταν μισό):** η
αφαίρεση του write (`ScrapeJobRepository._refresh_owned_property_room_types`,
δεύτερο execute, commit `ac87965`) ΔΕΝ αρκούσε: το
`accounts_repository.get_account_overview` (~:198-217, το μόνο backing του
`GET /me`) ΚΑΤΑΣΚΕΥΑΖΕ την επιλογή στο read — με NULL στήλη γύριζε το πιο
πρόσφατα ενημερωμένο discovered δωμάτιο ως `selected_room_type_category`,
οπότε `onboarding_complete = true` και ο setupGuard συνέχιζε να προσπερνά
τα βήματα 2-3 (το σύμπτωμα του +smoke0811 θα αναπαραγόταν αυτούσιο). Το
fallback είχε ΜΗΔΕΝΙΚΗ κάλυψη tests (το test_me_route ταΐζει canned dict).
Αφαιρέθηκε ΚΑΙ αυτό ως συνέπεια της ίδιας απόφασης ιδιοκτήτη (το /me λέει
πλέον την αλήθεια: null όταν δεν έχει επιλεγεί ποτέ)· το μόνο εναπομείναν
live αποτέλεσμα του fallback ήταν η κατασκευή για never-selected
λογαριασμούς (το migration 0007 είχε ήδη κάνει backfill τα ιστορικά).
Παρενέργεια προς όφελος: το «not onboarded» branch του
`scripts/staging_e2e.py` μπορεί πλέον πραγματικά να πυροδοτηθεί (πριν το
έκρυβε το fallback). Scheduler: ανέγγιχτος — never-selected ιδιοκτησίες
απλώς δεν προγραμματίζονται (απαιτεί non-null στήλη + tracked competitor
στην κατηγορία), σωστό. Spec review: μηδέν mutation survivors (r1-r5
όλα killed). Σημείωση για τον ιδιοκτήτη (προϋπάρχον, ΟΧΙ ελάττωμα αυτής
της αλλαγής): το «Αργότερα» skip στο map-page (`ensureSelectedRoom` ~:876-890 →
`persistSelectedRoomType` ~:573-582) αυτο-διαλέγει το ΠΡΩΤΟ discovered δωμάτιο και το γράφει μέσω
του κανονικού, server-validated PUT — ακολουθεί ρητή ενέργεια χρήστη,
αλλά με αυστηρή ανάγνωση του «η επιλογή είναι απόφαση του βήματος 2» είναι
προϊοντικό ερώτημα (Εκκρεμότητα 8).

### 3.3 Το κενό δεύτερο sign-in σκαλώνει στο /auth — χωρίς chip, live-επιβεβαιωμένο

**Απόδειξη (2026-08-12):** sign-in του `+smoke0807e` (λογαριασμός χωρίς
κατάλυμα, με άδεια τα πεδία ονόματος/τοποθεσίας στη φόρμα) → μένει στο
/auth με «Συμπληρώστε όνομα καταλύματος και τοποθεσία για να συνεχίσετε.»
— παλιό καταγεγραμμένο κενό του `continueAfterAuth` (auth-page.component.ts,
το branch `if (!propertyName || !location)` μετά τον έλεγχο
owned_property_id). Σωστή συμπεριφορά: αφού ο /setup οδηγός έχει πλέον
πλήρες pick mode με δική του φόρμα αναζήτησης (βήμα 1), το sign-in χωρίς
drafts πρέπει να προχωρά στο `/setup` και να αφήνει τον οδηγό να τα
ζητήσει — όχι να απαιτεί τα πεδία στη φόρμα σύνδεσης. Μικρή αλλαγή στο
continueAfterAuth + προσαρμογή του σχετικού e2e (υπάρχει σχόλιο-σημάδι στο
user-switch σενάριο του setup-wizard.spec.ts).

**Ευρήματα εκτέλεσης (2026-08-23, commit `437f989` + pin follow-up):**

- Ο `continueAfterAuth` έχει ΕΝΑΝ caller (submit)· το error branch έγινε
  πλήρως νεκρό και ΑΦΑΙΡΕΘΗΚΕ (το «Συμπληρώστε όνομα καταλύματος…για να
  συνεχίσετε.» δεν υπάρχει πια στον πηγαίο) — blank case ενώθηκε με το
  half-finished branch (`owned_property_id || !propertyName || !location`
  → /setup). Sign-up validation και drafts→autoSetup ανέγγιχτα. Το wizard
  step 1 pick mode επιβεβαιώθηκε ότι δουλεύει για property-less χρήστη
  (guard, step επιλογή, createOwnedProperty path).
- **Νέο σενάριο 3.3** στο setup-wizard.spec.ts: blank sign-in property-less
  λογαριασμού → /setup, «Βήμα 1», pick inputs, ΚΑΙ μηδέν POST στο
  auto-setup (recording mock — το URL assertion μόνο του ΔΕΝ πιάνει το
  fall-through-to-autoSetup mutant, αποδεδειγμένο)· κοινός helper
  `mockSupabaseSignInAsUserB`. Kill table: b1 killed· b2 (navigate /map
  αντί /setup) ΙΣΟΔΥΝΑΜΟΣ mutant — τον μασκάρει ο setupGuard (bounce
  /map→/setup που ήδη pin-άρει το scenario 1), καμία ενέργεια· b3
  (`storeCurrentUser` του merged branch) pinned σε follow-up commit.
- **ΝΕΟ εύρημα (επιβεβαιωμένο στατικά από τον reviewer) — αναληθές κενό
  state στο /setup για property-less/draft-less άφιξη (προϋπάρχον, τώρα
  το ΠΡΟΕΠΙΛΕΓΜΕΝΟ σημείο άφιξης):** το `ngAfterViewInit` του
  setup-property-step κάνει `search(false)` με άδεια inputs, το validation
  bail-άρει ΠΡΙΝ από κάθε request, και το template δείχνει «Δεν βρέθηκαν
  καταλύματα — Το Booking δεν επέστρεψε αποτελέσματα…» + κόκκινο
  «Συμπληρώστε όνομα καταλύματος και τοποθεσία.» με ΜΗΔΕΝ requests.
  Χρειάζεται τρίτο state «δεν έχει γίνει αναζήτηση ακόμα» με νέο ελληνικό
  κείμενο (design decision) → **Round 4.4**. Το νέο test σκόπιμα δεν
  βεβαιώνει ούτε το empty state ούτε το error, ώστε το fix να μην το
  πολεμήσει.
- **Global draft keys — ΣΤΕΝΕΜΕΝΗ καταγραφή (ο reviewer ΑΝΤΕΚΡΟΥΣΕ την
  αρχική διατύπωση):** τα draftPropertyName/draftLocation είναι σκόπιμα
  global (το sign-up γράφει πριν γνωρίζει subject), αλλά ΚΑΘΕ κανονικό
  expiry path τα καθαρίζει (api-client κάνει `await signOut()` πριν το
  /auth?reason=session-expired· το auth-js 2.106.1 κάνει `_removeSession`
  ακόμα και σε 401/403/404 → SIGNED_OUT → workflow.clear). Επιζούν ΜΟΝΟ
  σε abnormal αποτυχία του signOut (network error στο /logout POST ή
  καταπιωμένο throw π.χ. lock timeout). Residual γωνία, όχι ροή — Round
  4.4 σημείωση, όχι bug του 3.3.

---

## ROUND 4 — Γυάλισμα προϊόντος

> **Αποφάσεις 2026-08-23 (ο ιδιοκτήτης ζήτησε γνώμη και ανέθεσε την
> εκτέλεση· branch `feature/product-polish-round4` από main `2f99478` =
> merge του PR #11):** 4.1 → auto-plot ΟΛΩΝ των αποτελεσμάτων, checkboxes =
> φίλτρο απόκρυψης + επιλογή tracking (η σημασιολογία «selected → tracked»
> μένει). Κατώφλι «Παρακολούθηση 3+» → `min(3, διαθέσιμοι)` με προσαρμοσμένο
> κείμενο (4.4). Εκκρεμότητα 8 → το skip-path auto-pick ΜΕΝΕΙ αλλά περνά
> από `OnboardingService.selectRoomType` (cache/DB συμφωνούν) και δηλώνεται
> ρητά στον χάρτη ότι επιλέχθηκε αυτόματα (4.4). Οι Εκκρεμότητες 5, 6, 7
> (αλλαγές σημασιολογίας / μεσαίου μεγέθους) → **Round 4.5** με σύσταση
> «ναι», εκτελούνται αμέσως μετά το Round 4 εκτός αν ο ιδιοκτήτης πει όχι.
> Σειρά εκτέλεσης: 4.1 → 4.2 → 4.3 → 4.4.
> **Επιτάχυνση 2026-08-24 (εντολή ιδιοκτήτη, στόχος ~1 ώρα):** μετά το 4.2,
> τα 4.3+4.4 τρέχουν ως ΤΡΕΙΣ παράλληλες δέσμες με αυστηρό διαχωρισμό
> αρχείων (Α: map-page items A1-A6, Β: 4.3 κάρτα σύστασης, Γ: wizard/auth/
> backend-nit/docs C1-C5), χωρίς per-δέσμη spec/quality reviews — τα
> αντικαθιστά ΕΝΑ τελικό whole-branch review (fable) πριν το PR. Οι
> implementers κάνουν TDD με watched-fail αλλά χωρίς mutation ceremony·
> το τελικό review κληρονομεί και το quality σκέλος του 4.2.
> **Αποτελέσματα δεσμών (2026-08-24, commits 74b2fe7 A / b1567f4 B /
> a42765e C):** όλα τα A1-A6, B1-B2, C1-C5 υλοποιημένα με watched-fail
> pins. Integration baselines με την πρώτη: backend 605, e2e 136+1,
> lint/build καθαρά. Flags: (1) το setMapError κάνει console.warn — το
> ΜΟΝΟ console.* στο src/app, σκόπιμο, αλλιώς χάνεται ο λόγος του Mapbox
> failure — απόφαση σύμβασης. (2) Το persistSelectedRoomType τρέχει σε
> ΚΑΘΕ φόρτωση χάρτη (προϋπάρχον) και πλέον ακυρώνει το /me cache — ένα
> έξτρα /me στο επόμενο navigation, υποψήφιο guard «μόνο όταν άλλαξε».
> (3) Το κουμπί του βήματος 1 λέει «Αναζήτηση ξανά» και στο νέο
> not-searched-yet state (δεμένο με empty-states-and-settings.spec.ts:393)
> — στο 5.1 μαζί με το rename. (4) Το B2 μετρά συγκρίσιμους από το
> market/summary.total_hotels — τίμιο ΠΛΗΣΙΕΣΤΕΡΟ σήμα, όχι byte-ίδιο με
> το εσωτερικό count του percentile. Σωστό fix: πεδίο
> comparable_competitors στο PriceStatistics (Round 4.5/5 backend nit).
> (5) Το mockMapbox docstring του helpers.ts ενημερώθηκε από τον
> controller (το A6 άλλαξε ό,τι περιέγραφε). (6) Τελικό review:
> μηδέν survivors σε 6 mutation spot-checks· 3 Important + 4 Minor
> κλείστηκαν στο `2ca3e6f` (ορατό auto-pick hint στο results panel,
> ελληνικός ενικός στο μικρό δείγμα, min-width floor στο γράφημα —
> το μερικό premise του ευρήματος δεν ίσχυε: το media rule υπήρχε και
> το scroll δούλευε, το fix έγινε robustness floor· empty-state με gate
> στο error· διορθώσεις λεκτικών). Τελικά: e2e **138 passed + 1
> skipped**, backend **605**, lint/build καθαρά.

### 4.1 Auto-plot αποτελεσμάτων στον χάρτη (ΑΠΟΦΑΣΙΣΜΕΝΟ 2026-08-23: auto-plot όλων)

Μετά από αναζήτηση ο χάρτης μένει άδειος μέχρι ο χρήστης να τικάρει κάρτες
— ούτε ο ίδιος ο ιδιοκτήτης δεν το κατάλαβε στα δοκιμαστικά του (το hint
overlay υπάρχει αλλά δεν αρκεί). Πρόταση: μετά από ολοκληρωμένη αναζήτηση,
ΟΛΑ τα αποτελέσματα σχεδιάζονται αυτόματα στον χάρτη· τα checkboxes
γίνονται φίλτρο απόκρυψης και επιλογή για tracking (η υπάρχουσα σημασιολογία
«selected → tracked» διατηρείται). Προσοχή στη συνύπαρξη με το
`userTouchedSelection` guard (Fix B) και τα υπάρχοντα pins του
no-second-click. Εναλλακτικά, αν ο ιδιοκτήτης προτιμά τη σημερινή λογική:
δυνατότερο overlay + auto-tick των top-N.

### 4.2 Redesign γραφήματος ιστορικού (η πρόταση είναι αποφασισμένη από 2026-08-11)

Το τωρινό sparkline (pricing-page.component.ts, `buildSparkline`) πάσχει:
min-max κλιμάκωση σε όλο το ύψος (κάθε μεταβολή δείχνει ακραία), κανένας
άξονας/τιμές/ώρες, ευθεία-παραπλάνηση με 1-2 σημεία, αγγλικό κείμενο.
Νέα μορφή: τελείες ανά run ΜΕ τιμή, άξονας y με 2-3 ticks και padded
domain (όχι full-stretch), ημερομηνία+ώρα ανά run στον x, ζώνη P25-P75 όταν
≥3 runs, hover tooltip, ελληνικές ετικέτες, και ειλικρινής small-n
λειτουργία (≤2 runs: τελείες+τιμές+σημείωση «Χρειάζονται περισσότερες
αναζητήσεις για αξιόπιστη τάση»). Παραμένει inline SVG (χωρίς βιβλιοθήκη,
στο ύφος του project). ΠΡΙΝ γραφτεί κώδικας γραφήματος: φόρτωσε το dataviz
skill (κανόνας triggering του).

**Ευρήματα εκτέλεσης (2026-08-24, commit `48afdc2` — το dataviz skill
φορτώθηκε και οδήγησε τις επιλογές):**

- Χρώμα σειράς: το brand `--teal #0f766e` ΑΠΕΤΥΧΕ στον palette validator
  (OKLCH chroma 0.086 < 0.10 floor) → νέα μεταβλητή `--chart-series
  #0d9488` (όλοι οι έλεγχοι PASS, 3.74:1)· το `--teal` μένει για UI chrome.
  Dark mode ΔΕΝ υπάρχει — όταν έρθει, θέλει δικό του validated βήμα.
- «Τελείες ΜΕ τιμή» κυριολεκτικά = anti-pattern πάνω από ~4 runs → ladder:
  ετικέτες σε όλα έως 4 runs, μετά newest/oldest/min/max με collision
  filter· ΟΛΕΣ οι τιμές προσβάσιμες στο `<details>` table view (το
  WCAG-καθαρό δίδυμο του γραφήματος) και στο readout.
- Το plan δεν όριζε άνω όριο runs ενώ το API δίνει έως 60 → cap στα 12
  νεότερα + «Τελευταίες 12 από N αναζητήσεις».
- Ζώνη P25-P75: quartiles ΜΟΝΟ σε runs με ≥3 ανταγωνιστές· ζώνη μόνο σε
  συνεχόμενα τμήματα ≥2 τέτοιων runs (thin run = κενό, όχι παρεμβολή).
- Tooltip χωρίς gate: hover ΚΑΙ keyboard focus (hit bands πλήρους ύψους,
  tabindex, ελληνικά aria-labels)· «refetch κρατά το frame» (dim αντί για
  κατάρρευση σε loading). Standing κανόνας για μελλοντικά γραφήματα:
  tooltip πάντα με accessible δίδυμο.
- Χωρίς `preserveAspectRatio="none"` (παραμόρφωνε κύκλους/κείμενο)·
  καμβάς 560×240, κάτω από 640px οριζόντιο scroll της κάρτας.
- Και ο x άξονας αραιώνει (όχι μόνο οι τιμές): κάθε k-οστό run από το
  νεότερο, max 6 ετικέτες, το νεότερο πάντα με ετικέτα — στα 12 runs το
  παλαιότερο μπορεί να μείνει χωρίς x label (οι τιμές του πάντα στο
  table view).
- Παραμένει αγγλικός ο τίτλος κάρτας/Refresh/Loading (chrome → 5.1)· τα
  x labels στη ζώνη ώρας του χρήστη (τα specs καρφώνουν Europe/Athens).

### 4.3 Γυάλισμα agent σύστασης τιμής

- Η κάρτα δείχνει το εύρος 305.5-306.5 ως «306 € – 307 €» (στρογγυλοποίηση
  του formatEuro παραμορφώνει τα όρια — δείξε δεκαδικά ή floor/ceil σωστά).
- Ένδειξη «μικρό δείγμα» όταν οι συγκρίσιμοι ανταγωνιστές είναι 1-2 (το
  «Own position 100.0 percentile» με 2 ξενοδοχεία παραπλανά).
- Operator action (όχι κώδικας): `ANTHROPIC_API_KEY` στο `.env` για να
  ανάψει το LLM σκέλος (μοντέλο ήδη ρυθμισμένο claude-sonnet-5· χωρίς
  κλειδί τρέχει το statistical fallback — by design). Ο μηχανισμός
  (validator, audit, plausibility band) είναι ήδη production-σοβαρός —
  δες docstring στο api/services/price_recommendation_agent.py.

### 4.4 Μικρά καταγεγραμμένα

- `activeScrapeJobId` → signal (chip task_a934b51e, μηχανικό, map-page ~:848)
  — μαζί με το αδελφό `activeJobStay` του 2.2 (ή διευκρίνιση του AGENTS.md
  σε «state που παρατηρείται από το rendering»).
- Chip task_8fecd58c (από το 2.2): το Mapbox error handler του
  `initializeMap` καλεί `setError` και αδειάζει το results sidebar
  (status "error", message "") για καθαρά παρουσιαστική αποτυχία χάρτη —
  ξεχωριστό map-error state, όχι το status των αποτελεσμάτων.
- Restore αλυσίδα (μη superseded) με αποτυχία στο marker read αφήνει το
  `status` κολλημένο σε "loading" (από το 2.2 quality review): `status.set
  ("idle")` στο restore `catch` + ένα e2e (μονοπάτι του m8 mock,
  non-superseded εκδοχή).
- Room select ενεργό όσο τρέχει αναζήτηση → κατηγορία-από-dropdown vs
  stay-από-job (βλ. 2.2 ευρήματα (1)) — απόφαση μαζί με Εκκρεμότητα 7.
- `PropertyCandidate.country` νεκρό/ποτέ-γεμάτο (από το 3.1 final review):
  `automatic_setup` το διαβάζει, ο mapper δεν το θέτει — μηδέν αξία σήμερα.
- Τρίτο state «δεν έχει γίνει αναζήτηση ακόμα» στο βήμα 1 του wizard (από
  το 3.3): άφιξη με ΚΕΝΑ ή ΜΕΡΙΚΑ drafts (και το ένα-πεδίο-μόνο σενάριο —
  ίδιο template state, quality review 3.3) δείχνει σήμερα ψευδές «Δεν
  βρέθηκαν καταλύματα» + κόκκινο error με μηδέν requests
  (setup-property-step ngAfterViewInit → search(false) → validation bail).
  Το fix κλειδώνει στο «έτρεξε πραγματικά αναζήτηση;», όχι στο αν υπάρχουν
  drafts. Νέο ελληνικό κείμενο, truthful-per-status.
- Draft keys σε abnormal signOut failure (από το 3.3, στενεμένο): τα
  global drafts επιζούν ΜΟΝΟ αν το signOut αποτύχει μη-φυσιολογικά
  (network error στο /logout, καταπιωμένο throw) — τότε user B σε browser
  του A μπορεί να auto-δημιουργήσει κατάλυμα από τα drafts του A.
  Υποψήφιο: clear των draft keys και στο INITIAL_SESSION-with-user path ή
  πριν το autoSetup όταν το subject άλλαξε. Συγγενές (final review Round
  3): το `storeCurrentUser` του property-less /setup branch σβήνει τα έξι
  property keys (pinned 3.3b) αλλά ΟΧΙ τα `pendingCandidate`/
  `pendingDiscoveryJobId`/`pendingSetupError` — ένα stale
  `pendingCandidate:<subject>` θα άνοιγε το βήμα 1 σε confirm mode για
  property-less χρήστη. Abnormal path μόνο (server-side καθαρισμός όπως το
  smoke-account wipe)· μονογραμμική θωράκιση.
- Checklist βήμα 4: τικάρει μόνο στο επόμενο load μετά το tracking (το
  save δεν ξανα-ταΐζει το `setTracked`) — μικρό re-feed μετά το
  saveTrackedCompetitors.
- Κατώφλι «Παρακολούθηση 3+»: σε μικρές αγορές (Φαληράκι double: 2-3
  ανταγωνιστές) είναι ανέφικτο — είτε min(3, διαθέσιμοι) είτε αλλαγή
  κειμένου. Απόφαση προϊόντος.
- Κουδούνι: aria-label «undefined» σε περίεργα payloads (προϋπάρχον,
  ασήμαντο, φαίνεται στα mocked runs).
- Popup κλείνει σε κάθε repaint (από το 4.1 quality review, προϋπάρχον για
  τα ticks, τώρα και στο filter toggle): το renderMarkers ξεκινά με
  clearMarkers, οπότε ανοιχτό popup χάνεται όταν ο χρήστης τικάρει άλλη
  κάρτα ή αλλάζει το φίλτρο. Αμελητέο κόστος στα ≤50 markers· diff-based
  reconciliation ΜΟΝΟ αν τα popups γίνουν κεντρικά στη ροή.
- Chip task_cb5ec1e7 (από το 4.1): το DOCUMENTATION.md (~:2000, ~:2011)
  ισχυρίζεται ακόμα ότι το Angular «δεν κάνει auto-restore παλιών
  competitor cards» και καθαρίζει το lastCompetitorJobId — ψευδές από τα
  Rounds 2/3 (το resetCompetitorSearchState τα ΚΡΑΤΑ για το
  restoreLatestCompetitorSearch). Docs sweep.
- ~~Name-filter cascade του scraper~~ (`scraper/matching.py`
  `_filter_by_room_name`, από το 2.1): πριν το Round 4.5 το exact tier κρατούσε ΜΟΝΟ τις
  πανομοιότυπες γραμμές παγκόσμια — αν ένας ανταγωνιστής ονομάζει το δωμάτιό
  του ακριβώς όπως εσύ, ΟΛΑ τα άλλα ξενοδοχεία πέφτουν (live: `4 -> 2`).
  **ΕΚΛΕΙΣΕ 2026-08-28:** exact → partial → fuzzy → fallback ανά property,
  με URL-first identity και fallback χωρίς συγχώνευση ανώνυμων listings.

---

## ROUND 4.5 — Σημασιολογικές εκκρεμότητες 5-7 (σύσταση Claude «ναι», 2026-08-23)

**Execution status — ΟΛΟΚΛΗΡΩΘΗΚΕ 2026-08-28 στο
`feature/product-polish-round4` (working tree, πριν από commit).** Υλοποιήθηκαν
παράλληλα τα 4.5.1–4.5.3 και έγινε ανεξάρτητο cross-review/synthesis. Τελικό
baseline: backend **638 passed**, Playwright **145 passed + 1 skipped**,
TypeScript lint/build, `compileall`, `pip check` και Alembic single head
`20260828_0023` καθαρά.

Mutation evidence: exact-only price history/tracking reads, relabelled alert
payload, global αντί per-hotel name tier, αγνόηση του provider URL ως property
identity και χρήση της τρέχουσας dropdown category αντί της job category στο
restore προκάλεσαν αποτυχία στα αντίστοιχα pins. Μετά την επαναφορά, τα
στοχευμένα suites πέρασαν.

**Live evidence 2026-08-28 (development PostgreSQL + πραγματικό Apify):** η
0023 εφαρμόστηκε και το πλήρες `scripts/staging_e2e.py` πέρασε δύο scrapes,
tracking, pooled history, deterministic alert nudge, WebSocket/REST alert και
pricing recommendation. Το πρώτο live attempt αποκάλυψε Windows-only
`stdout` decoding ως CP1252, που έχανε το sentinel μετά από ελληνικά logs.
Προστέθηκε explicit UTF-8 contract + integration pin και η επανάληψη πέρασε:
summary #1 `rows_seen=20`, category `20 -> 9`, `rows_written=9`; summary #2
`rows_seen=17`, category `17 -> 7`, `rows_written=7`, και τα δύο ορατά από το
job API με `version=1` και `scrape_runs_count=1`.

### 4.5.1 Pool double+twin πέρα από τη σύγκριση (Εκκρεμότητα 5)
Εφάρμοσε το `comparable_room_type_categories()` και στα price history reads
(`price_history_repository.py:48`), στην αξιολόγηση alerts
(`price_alert_service`), στο tracked preselection (`tracking_repository`,
φίλτρο στο tracked κλειδί) και στο `canRestoreJob` (map-page). Συνέπεια:
τα στατιστικά αγοράς των double jobs περιλαμβάνουν πλέον twin γραμμές —
σκόπιμα (ίδια φυσική δεξαμενή). Pins σε κάθε layer· κανένα rename
αποθηκευμένου λεξιλογίου.

**Έγινε:** history/alerts/tracking/restore χρησιμοποιούν το pool με bound SQL
parameters. Τα writes μένουν exact. Το pooled tracking κάνει newest-row dedupe
ανά competitor property ώστε διπλή αποθήκευση σε `double` και `twin` να μη
φουσκώνει το checklist. Το market route κανονικοποιεί whitespace/case πριν
περάσει την κατηγορία στο service.

### 4.5.2 Name-filter cascade ανά ξενοδοχείο (Εκκρεμότητα 6)
`scraper/matching.py::_filter_by_room_name`: τα tiers exact → partial →
fuzzy να εφαρμόζονται ΑΝΑ ξενοδοχείο (κράτα το καλύτερο tier κάθε
ξενοδοχείου), όχι παγκόσμια — ώστε ένα πανομοιότυπο όνομα σε ΕΝΑΝ
ανταγωνιστή να μην ρίχνει όλα τα άλλα ξενοδοχεία. Pins με τα live labels
του scraper.log (4 -> 2 περίπτωση). Απόφαση ιδιοκτήτη αν προτιμά την
τωρινή ακρίβεια.

**Έγινε:** το καλύτερο tier επιλέγεται ανά σταθερή property identity. Προτιμάται
normalized `hotel_url`, μετά normalized display name· αν λείπουν και τα δύο ή
υπάρχει fallback «Άγνωστο Κατάλυμα», οι γραμμές μένουν ανεξάρτητες. Διατηρούνται
input order, duplicate package offers και duplicate DataFrame indices. Το
`record_id` χρησιμοποιεί επίσης URL όταν υπάρχει, για να μη συγκρούονται δύο
listings με ίδιο display/fallback name.

### 4.5.3 Ειλικρινές κενό αποτέλεσμα αναζήτησης (Εκκρεμότητα 7, επιλογή α)
Προδιαγραφή στο §2.1 «ΑΝΟΙΧΤΟ» (i)-(vi): scraper stdout sentinel
`ROOMRATE_RESULT_SUMMARY {json}` → runner → νέα nullable JSONB
`roomrate_scrape_jobs.result_summary` (Alembic 0023) → `ScrapeJobResponse`
→ map-page empty state από το payload («Βρέθηκαν N προσφορές άλλης
κατηγορίας» / «Βρέθηκαν K προσφορές της κατηγορίας σας, καμία δεν πέρασε τα
φίλτρα ονόματος/γευμάτων/ακύρωσης/παροχών» / γενικό)· τα δύο probe reads
φεύγουν· F4 pin μεταφέρεται στο payload· `canRestoreJob` χαλαρώνει για jobs
με result_summary. Μαζί: το job-scoped read να κουβαλά και την ΚΑΤΗΓΟΡΙΑ
του job (γενίκευση `jobStay` → job scope) ώστε η αλλαγή dropdown κατά το
polling να μην διαβάζει το job με ξένη κατηγορία (λύνει και το 4.4 «room
select ενεργό όσο τρέχει αναζήτηση»).

**Έγινε:** το scraper εκπέμπει version-1 summary σε κάθε exit-0 path, ο runner
διαβάζει αυστηρά το τελευταίο sentinel, το repository το αποθηκεύει atomically
με completion και API/UI το μεταφέρουν χωρίς probe reads. Missing/malformed
summary γίνεται `NULL` με generic fallback, όχι ψευδής εξήγηση. Τα jobs πριν
την 0023 παραμένουν `NULL`. Σε pooled restore τα reads χρησιμοποιούν exact
category/dates του job, αλλά η σημερινή επιλογή δωματίου του χρήστη δεν αλλάζει.

---

## ROUND 5 — Τα μεγάλα κύματα (υπάρχουν ήδη ως αποφάσεις)

### 5.1 Ελληνική μετάφραση των προϋπαρχουσών σελίδων (string inventory)

Ό,τι έχτισε το wizard plan είναι ελληνικό· το παλιό chrome (auth, map:
«Search filters/Market snapshot/Find Competitors/Searching», pricing,
settings headers) είναι αγγλικό — το πιο ορατό «ημιτελές» για πώληση σε
Έλληνες ξενοδόχους. Ο §7 πίνακας όρων ζει στο spec
`docs/superpowers/specs/2026-07-27-onboarding-ui-design.md` («Market P25»
→ «Χαμηλό εύρος αγοράς», «Lead time» → «Ημέρες μέχρι την άφιξη», «Sample
runs» → «Αναζητήσεις που συγκρίθηκαν»). Θέλει δικό του plan (inventory →
μαζική αλλαγή → e2e strings sweep).
Στο inventory να μπει και το a11y κενό του 4.1: το aria-label των markers
μένει αγγλικό και η κατάσταση «επιλεγμένο για παρακολούθηση» ΔΕΝ
εκφωνείται (μόνο `data-selected`/halo) — όταν μεταφραστεί το label, να
προστεθεί «, επιλεγμένο για παρακολούθηση» όταν ισχύει. Επίσης οι
αρνητικές αγγλικές βεβαιώσεις των Round 2/3 e2e (καταγεγραμμένες στα
§2.2/§3.3 ευρήματα) ξανα-παράγονται εδώ.

**Ευρήματα εκτέλεσης (2026-09-13, branch `feature/round5-greek-and-infra`,
εντολή ιδιοκτήτη «πολλά sessions, τελείωσε ό,τι έμεινε»):** ΕΓΙΝΕ ως
γλωσσάρι-συμβόλαιο + τέσσερις παράλληλοι μεταφραστές με ξένα αρχεία.
- Συμβόλαιο: `docs/superpowers/specs/2026-09-13-greek-string-inventory.md`
  (277 strings, glossary όρων, δείκτης e2e assertions με τα 11 αρνητικά
  προς επανεξαγωγή). Απόφαση: «records» → «καταγραφές». Κενά του inventory
  που βρήκαν οι μεταφραστές (να συμπληρωθούν στο doc): «Market snapshot» →
  «Σύνοψη αγοράς» (map), 58ο string στο settings («Could not load alert
  rules.»), 4 μη-δεικτοδοτημένα sites στο setup-wizard.spec.ts (create
  account / sign in / sign out), αντίφαση A-11 (χρησιμοποιήθηκε το
  αυστηρό `/^τοποθεσία$/i`), header «40 strings» της pricing ενώ είναι 49.
- Auth (55 + services 16): έγινε· 3 getByLabel regexes + 4 extra sites στο
  setup-wizard.spec.ts. Pricing (49, με τους 10 §7 όρους): έγινε·
  `confidenceLabel()` με fallback «Χαμηλή βεβαιότητα» (ποτέ πιο σίγουρο
  από την πραγματικότητα). Settings/bell/toasts/index (70): έγινε· `el-GR`
  στα δύο date formatters· legal footer links στο settings. Map (87): έγινε·
  **M-78 a11y κλεισμένο** («{hotel}, {price} ανά βράδυ» + «, επιλεγμένο για
  παρακολούθηση»)· **amenities** έγιναν `{value, label}` — το query param
  μένει αγγλικό (pinned με recording mock), τα 12 ελληνικά labels είναι
  κείμενο του agent, ΟΧΙ του συμβολαίου → **θέλει έγκριση ιδιοκτήτη**.
- Αρνητικά assertions: 7 επανεξαγμένα στο map (κάθε νέο substring
  βεβαιώνεται θετικά αλλού στο ίδιο run), 0 χρειάστηκαν στα υπόλοιπα
  (επαληθευμένο, όχι υποθετικό). Νέος τρόπος αποτυχίας: **ελληνικές
  ομοηχίες** (header «N ανταγωνιστές» vs checklist «…N ανταγωνιστές», P-18
  empty state περιέχει το P-21 heading) → scoped locators
  (`.header-actions`, `.recommendation-card`).
- ~~Product bug που βρέθηκε (ΟΧΙ διορθώθηκε)~~ **ΔΙΟΡΘΩΘΗΚΕ 2026-09-13
  (branch `feature/amenities-filter-fix`, commit `4a90a11`, εντολή
  ιδιοκτήτη):** το `buildMarkerParams` έστελνε `amenities` ΜΟΝΟ χωρίς
  `scrape_job_id`, ενώ όλα τα reads μετά από αναζήτηση είναι job-scoped
  → τα facility checkboxes δεν έφταναν ποτέ στο backend (το backend τα
  εφάρμοζε ήδη και στα job-scoped reads — καθαρά frontend bug). Τώρα:
  παροχές σε ΚΑΘΕ read, κάθε αλλαγή checkbox ξαναδιαβάζει το ενεργό job
  (τρέχον generation, ίδιο job scope/summary), και όταν τα φίλτρα
  αδειάζουν τα αποτελέσματα ενώ το job έγραψε γραμμές, το empty state
  λέει «Τα φίλτρα παροχών κρύβουν όλα τα αποτελέσματα» με κουμπί
  «Καθαρισμός φίλτρων παροχών» — όχι «Δεν βρέθηκαν». Δύο pins, τρία
  mutations κόκκινα. Review (fable, 3 mutations + 3 Important) → κλείστηκαν
  στο `580d78d`: η αλλαγή δωματίου δεν σβήνει πια τα ticks (ο χρήστης
  κρατά το φίλτρο του· prune μόνο από το `loadAmenities`, με re-read αν
  στένεψε)· `amenityFilterHidesEverything` απαιτεί `status === "ready"`
  (αποτυχημένο re-read = σφάλμα, όχι «φταίει το φίλτρο»)· pinned ότι
  `rows_written === 0` κρατά την εξήγηση του summary (άλλη κατηγορία) και
  ΟΧΙ του φίλτρου· νέο banner σταθερά «…τα φίλτρα παροχών δεν αφήνουν
  κανένα στον χάρτη» μέσω του υπάρχοντος constant-identity refresh·
  pinned ότι tick κατά τη διάρκεια αναζήτησης δεν την υπερκαλύπτει ούτε
  ξεκινά δικό του read. Σημείωση: το `normalizeAmenityOptions` γυρνά
  πάντα και τις 12 επιλογές, άρα το prune του `loadAmenities` δεν στενεύει
  ποτέ σήμερα — ο guard είναι σωστός αλλά αναξιοποίητος. Στο ίδιο branch:
  `SentrySettings` διπλώθηκε στο `Settings` (`4fd0d86`). Μένει σκόπιμο follow-up: αναβάθμιση σε numpy 2
  (πύλη: το deprecation test του `_trend_pct`).
- **Flake του wizard scenario 5b ΛΥΘΗΚΕ ΡΙΖΙΚΑ (2026-09-14, `f604d07`):** τρίτη
  εμφάνιση (Round 3 review, Round 4 δέσμη, CI push run του PR #14 — το PR
  run του ίδιου commit πέρασε). Δεν ήταν φόρτος: το test ξανακαταχωρούσε
  γενικό `/api/v1/**` → `[]` wildcard ΜΕΤΑ το `reachStepThree`, ενώ το
  βήμα 3 διαβάζει `/me` μερικά ticks μετά την επικεφαλίδα (πίσω από το
  navigator.locks του Supabase getSession) → `[]` →
  `buildAuthoritativeSearchIntent` πετούσε → error state χωρίς «Αργότερα».
  Fix μόνο στο test (μόνο specific routes, όπως το 5c· `test.slow()`
  αφαιρέθηκε), 8/8 σε `--repeat-each 8 --workers 4`. Κανόνας: ΠΟΤΕ
  δεύτερο γενικό wildcard μετά από helper που άφησε τη σελίδα με εκκρεμή
  reads.
- Παραμένουν αγγλικά ΣΚΟΠΙΜΑ: RoomRate/Booking/Mapbox/Supabase, `Email`
  label, `AI agent`, `P25`/`P75`, backend payload κείμενο (reasoning,
  key_factors, notes) — αυτά μεταφράζονται στο backend αν ποτέ χρειαστεί.

### 5.2 Production/λειτουργική υποδομή (τίποτα δεν υπάρχει στο repo σήμερα)

Από την αξιολόγηση 2026-08-07: κανένα Dockerfile/CI/hosting config· το
`.env.example` περιγράφει την production τοπολογία (api service + χωριστό
`python -m api.worker`) και τα production hardening flags που ΥΠΑΡΧΟΥΝ ήδη
στον κώδικα (boot validation, rate limiting, HSTS, JSON logs με
X-Request-ID, quotas, retention). ~~Ανοιχτό ερώτημα: πώς γίνεται το DB
schema (δεν βρέθηκε alembic — πιθανόν Supabase SQL;)~~ **Απαντήθηκε
2026-08-16 (plan bug):** το schema γίνεται με Alembic — `alembic.ini`,
`migrations/env.py` (target_metadata = Base.metadata), 21 revisions στο
`migrations/versions/` (τελευταία `20260727_0021_close_rls_view_bypass`),
οδηγίες στο `docs/DOCUMENTATION.md:95,199-205` (`alembic upgrade head`,
`--sql` για preview). Ο `scraper/cli.py:169` κάνει επιπλέον
`create_all` για τα legacy tables του scraper. Το deploy plan χρειάζεται
μόνο «ποιος τρέχει `alembic upgrade head` και πότε» (CI step ή manual).
Λίστα κύματος: Docker/CI, migration step στο deploy, Sentry/monitoring,
backups, GDPR/όροι, billing (όταν οριστεί μοντέλο χρέωσης).

**Ευρήματα εκτέλεσης (2026-09-13, δύο παράλληλα infra sessions):**
- **Backend:** `Dockerfile.api` (python:3.12-slim, non-root, HEALTHCHECK
  /health, scraper μέσα για το worker subprocess, api/tests εκτός εικόνας),
  `.dockerignore`, `docker-compose.prod.yml` με one-shot **`migrate`**
  service (`alembic upgrade head`) που το `api`/`worker` περιμένουν με
  `service_completed_successfully` — αυτή είναι η απάντηση στο «ποιος
  τρέχει το upgrade και πότε»· `ROOMRATE_PROCESS_ROLE` ανά service.
  `.github/workflows/roomrate-ci.yml` (ΣΤΗ ΡΙΖΑ του repo `C:\vscode_code` — το GitHub αγνοεί workflows σε υποφακέλους· το πρώτο push το απέδειξε: μηδέν runs· κάθε job τρέχει με `working-directory: room_project2`): backend — **πρώτο πραγματικό run 2026-09-13:** frontend job πράσινο (lint/build/playwright στον runner)· backend κόκκινο σε ΕΝΑ test: το `numpy` ήταν unpinned, ο runner έλυσε numpy 2.x και το `test_trend_calculation_emits_no_datetime_deprecation_warning` έπιασε τη drift (pandas 2.2.2 + numpy 2 → generic-unit DeprecationWarning στο `_trend_pct`). Fix: `numpy==1.26.4` pin· αναβάθμιση σε numpy 2 = σκόπιμο follow-up με το test ως πύλη (pytest, έλεγχος ΕΝΟΣ Alembic head
  χωρίς DB, compileall) + frontend (lint/build/playwright chromium) —
  χωρίς secrets (επαληθεύτηκε ότι η backend suite περνά με κενό env).
  Sentry: `sentry-sdk[fastapi]==2.69.1`, init ΜΟΝΟ με `SENTRY_DSN`, pinned·
  ~~απόκλιση: `SentrySettings` μέσα στο `api/main.py`~~ (διπλώθηκε στο
  `Settings` στο `4fd0d86`, 2026-09-13). `scripts/
  backup_db.py` (pg_dump custom format, credentials μόνο μέσω PG* env).
  `docs/DEPLOYMENT.md` = runbook. ΔΕΝ χτίστηκε εικόνα (χωρίς Docker daemon)
  — Dockerfile επαληθεύτηκε με parsing/COPY-existence, compose με
  `docker compose config`, YAML με safe_load. **Το CI δεν έχει τρέξει ποτέ**
  — το πρώτο push του branch το τρέχει.
- **Frontend:** `frontend/Dockerfile` (Node 20 build → unprivileged nginx),
  `nginx.conf` (SPA fallback, cache ανά κλάση asset, CSP που επιτρέπει
  Mapbox/Supabase + `ROOMRATE_CSP_API_ORIGINS` — προσοχή: cross-origin API
  θέλει ΚΑΙ `wss://` λόγω του /ws/alerts), **runtime config**: το
  `/runtime-config.js` παράγεται από `ROOMRATE_*` env vars στο container
  (δεν υπάρχει `NG_APP_API_BASE_URL` — νεκρό όνομα, plan bug του 5.2
  κειμένου), άρα μία εικόνα για όλα τα περιβάλλοντα. `angular.json`:
  `inlineCritical: false` ώστε το production index.html να μην έχει inline
  `onload` που η CSP μπλοκάρει. Σελίδες **/privacy** και **/terms**
  (δημόσιες, ελληνικά ΠΡΟΣΧΕΔΙΑ με ορατή σήμανση «προς επιβεβαίωση από
  νομικό σύμβουλο», placeholders επωνυμίας/έδρας/email/ημερομηνίας, χωρίς
  tracking cookies — επαληθευμένο), e2e `legal-pages.spec.ts`.
- **Δεν έγινε (σχεδιαστικά):** billing (χωρίς μοντέλο χρέωσης), επιλογή
  hosting, νομική επιθεώρηση των προσχεδίων, Sentry DSN/ρύθμιση alerts —
  όλα στις Εκκρεμότητες ιδιοκτήτη.

---

## Εκκρεμότητες ιδιοκτήτη (όχι κώδικας)

1. ~~Merge ή όχι του PR #9~~ (έγινε, `6641663`)· ~~Round 2 PR #10~~
   (έγινε, `e77a9ad`)· ~~Round 3 PR #11~~ (έγινε, `2f99478`)· ~~Round 4 +
   4.5 PR #12~~ (έγινε, `01509dd`, 2026-09-13). Τώρα: merge ή όχι του
   Round 5 PR (`feature/round5-greek-and-infra`).
2. Διαγραφή 6 smoke auth χρηστών: dashboard
   https://supabase.com/dashboard/project/nycfqostjdjaynstaloo/auth/users →
   `backup.planahead+smoke0807b/c/d/e`, `+smoke0811`, `+smoke0811b`
   (τα app δεδομένα τους ήδη καθαρίστηκαν 2026-08-12· το market history
   Φαληρακίου μένει σκόπιμα).
3. `ANTHROPIC_API_KEY` στο `.env` αν θέλει το LLM σκέλος του agent.
4. ~~Απόφαση για 4.1 (auto-plot) και για το κατώφλι του 4.4.~~ ΑΠΟΦΑΣΙΣΜΕΝΑ
   2026-08-23 (βλ. Round 4 πλαίσιο): auto-plot όλων· min(3, διαθέσιμοι).
5. ~~Pool double+twin πέρα από τη σύγκριση.~~ **ΟΛΟΚΛΗΡΩΘΗΚΕ στο Round
   4.5.1 (2026-08-28):** history, alerts, pooled tracking preselection και
   restore είναι συνεπή, χωρίς rename των stored category keys.
6. ~~Name-filter cascade: recall-vs-precision του exact tier.~~
   **ΟΛΟΚΛΗΡΩΘΗΚΕ στο Round 4.5.2:** strongest tier ανά property identity,
   με URL-first grouping και regression pins για same-name listings.
7. ~~Live plumbing για ειλικρινές κενό αποτέλεσμα.~~ **ΟΛΟΚΛΗΡΩΘΗΚΕ στο
   Round 4.5.3 με επιλογή (α):** `result_summary` JSONB, Alembic 0023,
   scraper→runner→job→API→UI και generic fallback για legacy/invalid payloads.
8. **«Αργότερα» skip στον χάρτη αυτο-διαλέγει δωμάτιο (από το 3.2 review):**
   το `ensureSelectedRoom` του map-page γράφει το πρώτο discovered δωμάτιο
   μέσω του κανονικού PUT όταν ο χρήστης προσπεράσει το βήμα 2 με ρητό
   κλικ. Συνειδητό και server-validated — αλλά αν θες «καμία επιλογή χωρίς
   απόφαση χρήστη στο βήμα 2», αυτό το μονοπάτι θέλει είτε αφαίρεση (ο
   χάρτης δουλεύει χωρίς baseline; μάλλον όχι) είτε ρητό dialog. Απόφαση
   προϊόντος. Σημείωση σύνθεσης (final review Round 3): το
   `persistSelectedRoomType` κάνει raw `api.put` ΧΩΡΙΣ
   `onboarding.invalidateCurrentUser()`, οπότε μετά το skip η DB έχει
   επιλογή αλλά το in-session `/me` cache λέει null — το checklist δείχνει
   το βήμα 2 ανοιχτό και το επόμενο non-map link bounce-άρει στο /setup,
   ενώ ένα reload προσγειώνεται στον χάρτη ως complete. Όχι loop, όχι
   data-unsafe (pinned «this navigation only» στο
   setup-room-step-actions.spec). Αν το PUT επιβιώσει της απόφασης, να
   περάσει από `OnboardingService.selectRoomType` ώστε cache και DB να
   συμφωνούν.
9. **Round 5 (2026-09-13):** (α) έγκριση των 12 ελληνικών amenity labels
   (κείμενο agent, όχι συμβολαίου)· (β) νομική επιθεώρηση /privacy και
   /terms + συμπλήρωση placeholders (επωνυμία, έδρα, email, ημερομηνία)·
   (γ) hosting provider + `SENTRY_DSN`· (δ) μοντέλο χρέωσης → billing·
   (ε) το πρώτο CI run στο GitHub (ανοίγει με το push του branch) — αν
   κοκκινίσει σε κάτι περιβαλλοντικό (Node/Playwright στον runner), είναι
   δουλειά του επόμενου session, όχι regression.
