# Καθοδηγούμενο onboarding και επαγγελματικό UI

Ημερομηνία: 2026-07-27
Κατάσταση: εγκεκριμένο σχέδιο, έτοιμο για πλάνο υλοποίησης

## 1. Το πρόβλημα

Περνώντας από το app σαν χρήστης με πραγματικά δεδομένα εντοπίστηκε ότι ο νέος
χρήστης δεν έχει καμία καθοδήγηση:

- Μετά το sign-up το `auth-page` καλεί σιωπηλά `POST /onboarding/auto-setup`,
  το οποίο **διαλέγει μόνο του** ποιο κατάλυμα του Booking είναι ο χρήστης και
  δημιουργεί το owned property χωρίς καμία επιβεβαίωση. Λάθος αντιστοίχιση
  σημαίνει ότι κάθε επόμενη τιμή, σύγκριση και σύσταση χτίζεται πάνω σε δεδομένα
  άλλου ξενοδοχείου, χωρίς ο χρήστης να έχει τρόπο να το καταλάβει.
- Το discovery job βρίσκει μόνο τα δικά του δωμάτια, οπότε ο χρήστης
  προσγειώνεται σε χάρτη με «0 competitors» και μηδενικά KPI.
- Καμία οθόνη δεν λέει τι λείπει ή ποιο είναι το επόμενο βήμα. Η ροή
  (αναζήτηση → παρακολούθηση → σύσταση) είναι μοιρασμένη σε τρεις σελίδες
  χωρίς ένδειξη προόδου.

## 2. Στόχος και κριτήρια επιτυχίας

Ένας νέος χρήστης φτάνει στον χάρτη **με δεδομένα μέσα** και ξέρει ανά πάσα
στιγμή τι λείπει.

1. Ο χρήστης επιβεβαιώνει ρητά ποιο κατάλυμα είναι, με χάρτη και στοιχεία.
2. Καμία αναζήτηση που κοστίζει δεν ξεκινά χωρίς ρητό κλικ και δήλωση κόστους.
3. Καμία οθόνη δεν δείχνει μηδενικά αντί για εξήγηση.
4. Ο χρήστης βλέπει πρόοδο «X από 5» μέχρι την πρώτη σύσταση τιμής.
5. Όλο το UI στα ελληνικά, χωρίς emoji, με εικονίδια SVG γραμμής.

## 3. Αρχιτεκτονική

### 3.1 Νέο route `/setup`

```
/auth      →  δημιουργία λογαριασμού (χωρίς auto-setup πλέον)
/setup     →  ο οδηγός 3 βημάτων  (guard: μόνο όταν λείπει κάτι)
/map       →  ο χάρτης            (guard: ανακατεύθυνση σε /setup αν λείπει κάτι)
```

**`setupGuard` (νέο)** — τρέχει μετά τον `authGuard`. Καλεί `GET /api/v1/me`:

| Κατάσταση | Ενέργεια |
|---|---|
| `owned_property_id` κενό | → `/setup` βήμα 1 |
| έχει property, κενό `selected_room_type_category` | → `/setup` βήμα 2 |
| έχει και τα δύο | → κανονική πλοήγηση |

Ο ίδιος έλεγχος προστίθεται στο `/map` ώστε ένας μισοτελειωμένος χρήστης να
μην μπορεί να παρακάμψει τον οδηγό γράφοντας το URL.

### 3.2 Νέα components

| Component | Ευθύνη |
|---|---|
| `SetupPageComponent` | Container: κρατά το τρέχον βήμα, καλεί το service, δεν έχει δική του λογική API |
| `SetupPropertyStepComponent` | Βήμα 1: λίστα candidates + mini χάρτης, επιλογή |
| `SetupRoomStepComponent` | Βήμα 2: λίστα τύπων δωματίου, επιλογή |
| `SetupSearchStepComponent` | Βήμα 3: περίληψη κόστους, εκκίνηση, πρόοδος |
| `SetupChecklistComponent` | Η μπάρα προόδου στον χάρτη (μέρος C) |
| `EmptyStateComponent` | Επαναχρησιμοποιήσιμο: εικονίδιο, τίτλος, εξήγηση, προαιρετικό κουμπί (μέρος B) |
| `OnboardingService` | Το μόνο σημείο που μιλά στα onboarding endpoints |
| `SetupProgressService` | Υπολογίζει την κατάσταση του checklist από τα υπάρχοντα endpoints |

Κάθε component είναι standalone και signal-based, όπως τα υπόλοιπα του app
(κάθε async γραφή σε signal — δες `frontend/AGENTS.md`).

### 3.3 Ροή δεδομένων

Όλα τα endpoints υπάρχουν ήδη. Απαιτείται **μία** μικρή αλλαγή backend (§3.4).

```
sign-up POST /api/v1/onboarding/auto-setup            (πρόταση + discovery job)

Βήμα 1  GET  /api/v1/onboarding/property-candidates   (μόνο στο «δείξε άλλα»)
             ?property_name=&location=&check_in=&check_out=&limit=8
        PUT  /api/v1/onboarding/owned-property/{id}   (νέο· αλλαγή επιλογής)
             → 202 + discovery_job
        DELETE /api/v1/onboarding/owned-property/{id} (νέο· ξεκίνα από την αρχή)

Βήμα 2  GET  /api/v1/onboarding/owned-property/{id}/room-types
        PUT  /api/v1/onboarding/owned-property/{id}/selected-room-type

Βήμα 3  POST /api/v1/scrape-jobs/                     (job_type=competitor_search)
        GET  /api/v1/scrape-jobs/{id}                 (polling)
```

### 3.4 Το κατάλυμα γίνεται αναστρέψιμο (νέα endpoints)

Η αρχική εκδοχή του σχεδίου απέφευγε το `auto-setup` επειδή δημιουργεί
κατάλυμα πριν την επιβεβαίωση και **δεν υπήρχε τρόπος να το αλλάξεις ή να το
σβήσεις**. Αντί να αποφύγουμε το συμπτωματικό, διορθώνουμε την αιτία: το
κατάλυμα γίνεται αναστρέψιμο και έτσι μπορούμε να κρατήσουμε την ταχύτητα του
`auto-setup` ΚΑΙ τον έλεγχο του οδηγού.

**`PUT /api/v1/onboarding/owned-property/{owned_property_id}`** — «δεν είμαι
αυτό, είμαι εκείνο». Body ίδιο με το create (`OwnedPropertyOnboardingCreate`).
Ατομικά, σε μία συναλλαγή:

1. ενημερώνει τα στοιχεία καταλύματος (όνομα, URL, πόλη, διεύθυνση, συντεταγμένες,
   `canonical_destination`)
2. διαγράφει τους τύπους δωματίου του παλιού καταλύματος και μηδενίζει το
   `selected_room_type_category` — ανήκουν σε άλλο ξενοδοχείο
3. διαγράφει τους tracked competitors **μόνο όταν αλλάζει το
   `canonical_destination`** (αλλιώς η αγορά είναι η ίδια και διατηρούνται)
4. ξεκινά νέο discovery job

Επιστρέφει `OwnedPropertyOnboardingResponse` με 202, ακριβώς όπως το create.
Το `id` παραμένει ίδιο, οπότε κανένα reference δεν σπάει.

**`DELETE /api/v1/onboarding/owned-property/{owned_property_id}`** — «ξεκινώ από
την αρχή». Επιστρέφει 204. Το σχήμα καθαρίζει μόνο του:

| Πίνακας | Συμπεριφορά |
|---|---|
| `roomrate_owned_property_room_types` | CASCADE |
| `roomrate_tracked_competitors` | CASCADE |
| `roomrate_alert_rules` | CASCADE |
| `roomrate_price_recommendation_audits` | CASCADE |
| `roomrate_scrape_jobs` | SET NULL — τα jobs μένουν ως ιστορικό |

Τα `roomrate_scrape_runs` / `rate_observations` δεν δείχνουν στο κατάλυμα:
είναι δεδομένα **αγοράς** και διατηρούνται σκόπιμα, ώστε το ιστορικό τιμών
της περιοχής να επιβιώνει.

Και τα δύο endpoints είναι account-scoped: κατάλυμα άλλου λογαριασμού
επιστρέφει 404, ποτέ 403 (δεν αποκαλύπτουμε την ύπαρξή του).

### 3.5 Η μία αλλαγή backend: ταξινόμηση candidates

Το `OnboardingService.search_property_candidates` επιστρέφει τα candidates
**όπως τα δίνει ο provider, χωρίς ταξινόμηση**. Το scoring υπάρχει
(`_candidate_match_score`) αλλά εφαρμόζεται μόνο μέσα στο `automatic_setup`,
που τώρα παύει να χρησιμοποιείται από το UI.

Χωρίς αυτό, το βήμα 1 είτε δεν μπορεί να προεπιλέξει το πιθανότερο κατάλυμα,
είτε το frontend θα διπλασίωνε τη λογική scoring σε TypeScript — δύο υλοποιήσεις
που θα αποκλίνουν σιωπηλά.

**Αλλαγή:** το `search_property_candidates` ταξινομεί φθίνουσα κατά
`_candidate_match_score(property_name, candidate)`, ώστε το πρώτο στοιχείο να
είναι το best match. Το `automatic_setup` απλοποιείται ώστε να παίρνει το
πρώτο, αντί να ξανατρέχει `max(...)`. Test-first, με candidates σε τυχαία σειρά.

### 3.6 Η ροή «mix»: ταχύτητα auto-setup + έλεγχος οδηγού

Το `auth-page` **κρατά** την κλήση `auto-setup` — αλλά σταματά να θεωρεί ότι
τελείωσε. Η επιλογή γίνεται πρόταση προς επιβεβαίωση, όχι τετελεσμένο:

```
sign-up
  └─ POST /onboarding/auto-setup      ξεκινά ΑΜΕΣΩΣ το discovery job
       └─ redirect /setup
            βήμα 1  «Βρήκαμε αυτό. Είσαι εσύ;»  (selected_candidate + χάρτης)
              ├─ Ναι  → βήμα 2· τα δωμάτια είναι ήδη έτοιμα ή σχεδόν
              └─ Όχι  → GET candidates → επιλογή → PUT owned-property/{id}
                        → νέο discovery job → βήμα 2
```

Το κέρδος είναι πραγματικό: το discovery job τρέχει όσο ο χρήστης διαβάζει την
οθόνη επιβεβαίωσης, οπότε στη συνηθισμένη περίπτωση (σωστό auto-match) το
βήμα 2 δεν περιμένει καθόλου. Και επειδή το `PUT` υπάρχει, η λάθος περίπτωση
κοστίζει ένα κλικ αντί να είναι μη αναστρέψιμη.

Το `draftPropertyName` / `draftLocation` του `WorkflowStorageService` παραμένουν
ως fallback όταν το `auto-setup` αποτύχει (π.χ. κανένα candidate) — τότε το
βήμα 1 ζητά διόρθωση ονόματος/τοποθεσίας και καλεί απευθείας `candidates`.

**Διαχείριση εκτός onboarding:** στο `/settings` προστίθεται ενότητα «Το
κατάλυμά μου» με τα στοιχεία, «Αλλαγή καταλύματος» (ίδια οθόνη με το βήμα 1)
και «Διαγραφή». Η διαγραφή ζητά επιβεβαίωση με πληκτρολόγηση του ονόματος και
δηλώνει ρητά τι χάνεται (δωμάτια, παρακολουθούμενοι ανταγωνιστές, κανόνες
ειδοποιήσεων) και τι διατηρείται (ιστορικό τιμών αγοράς).

## 4. Ο οδηγός, βήμα προς βήμα

### Βήμα 1 — «Είσαι εσύ;»

Δείχνει το `selected_candidate` που επέστρεψε το `auto-setup` ως **mini Mapbox
χάρτης με pin** (έχουμε `latitude`/`longitude` σε κάθε `PropertyCandidate`)
δίπλα σε κάρτα με: `display_name`, `property_type`, `stars`, `address`,
`review_score`, `review_count` και σύνδεσμο `booking_url` για έλεγχο.

«Ναι, αυτό είναι» → βήμα 2 χωρίς καμία κλήση (το κατάλυμα υπάρχει ήδη).

«Δείξε άλλα» → καλεί `property-candidates` με το όνομα/τοποθεσία του sign-up
και ημερομηνίες από `defaultStayDates()` — το endpoint απορρίπτει `check_in`
στο παρελθόν, οπότε δεν επιτρέπεται να περάσουν σταθερές ημερομηνίες. Όσο
τρέχει (blocking Booking scout, μπορεί να πάρει λεπτά) δείχνει πρόοδο με
ορόσημα, όχι spinner. Όλα τα candidates μπαίνουν στον ίδιο χάρτη ως επιλέξιμα
pins, με το τρέχον σημειωμένο· η νέα επιλογή καλεί `PUT /owned-property/{id}`.

Μετά την αλλαγή §3.5 το πρώτο candidate είναι το best match κατά το scoring
του backend, οπότε συμφωνεί πάντα με αυτό που ήδη επέλεξε το `auto-setup`.

**Κενή λίστα candidates ή αποτυχία `auto-setup`:** δεν είναι σφάλμα — ο χρήστης
βλέπει πεδία για να διορθώσει όνομα/τοποθεσία και να ξαναψάξει.

### Βήμα 2 — ποιο δωμάτιο τιμολογείς

Περιμένει το discovery job του βήματος 1 (ίδιο progress UI) και μετά δείχνει
τους τύπους δωματίων με `room_type`, δείγμα τιμής (`sample_price_per_night_eur`)
και χαρακτηριστικά (`sample_facilities`), ώστε να αναγνωρίζονται. Η επιλογή
γράφεται με `PUT selected-room-type`.

**Κανένα δωμάτιο:** εξηγεί ότι το Booking δεν επέστρεψε κατάλογο για αυτές τις
ημερομηνίες και προσφέρει επανάληψη ή παράλειψη προς τον χάρτη.

### Βήμα 3 — η πρώτη αναζήτηση

Δείχνει **πριν** το κλικ: περιοχή (με χάρτη και ακτίνα), ημερομηνίες
(`defaultStayDates()`, δηλαδή +30 ημέρες), πλήθος ανταγωνιστών, διάρκεια
2–4 λεπτά, και ρητή δήλωση ότι κάθε αναζήτηση τραβά ζωντανά δεδομένα και έχει
κόστος. Δύο κουμπιά: «Ξεκίνα την αναζήτηση» και «Αργότερα» (→ χάρτης, με το
checklist να δείχνει το βήμα ανοιχτό).

Όσο τρέχει: μπάρα προόδου με ορόσημα και η πληροφορία ότι μπορεί να φύγει —
η ειδοποίηση έρχεται από το υπάρχον WebSocket. Στο τέλος → `/map` με δεδομένα.

## 5. Το checklist (μέρος C)

`SetupChecklistComponent` στην κορυφή του χάρτη, συμπτυσσόμενο, με πέντε βήματα:

| # | Βήμα | Πηγή αλήθειας |
|---|---|---|
| 1 | Κατάλυμα συνδέθηκε | `me.owned_property_id` |
| 2 | Δωμάτιο επιλέχθηκε | `me.selected_room_type_category` |
| 3 | Πρώτη αναζήτηση | υπάρχει completed `competitor_search` με `scrape_runs_count > 0` |
| 4 | Παρακολούθηση 3+ ανταγωνιστών | `GET /tracked/competitors` |
| 5 | Πρώτη σύσταση τιμής | `price-history` με ≥2 runs |

Κάθε ανολοκλήρωτο βήμα εξηγεί **γιατί** χρειάζεται (π.χ. «η σύσταση χρειάζεται
δεύτερη αναζήτηση για να συγκρίνει»). Στο 5/5 κρύβεται μόνιμα· η κατάσταση
απόκρυψης αποθηκεύεται ανά χρήστη στο `WorkflowStorageService`.

Το `SetupProgressService` υπολογίζει την κατάσταση από κλήσεις που ο χάρτης
**ήδη κάνει** — δεν προστίθενται νέα requests στο κρίσιμο μονοπάτι.

## 6. Κενές οθόνες (μέρος B)

`EmptyStateComponent` με είσοδο: εικονίδιο, τίτλος, εξήγηση, προαιρετική
ενέργεια. Αντικαθιστά:

- **Market snapshot / Destination market** χωρίς δεδομένα → σήμερα δείχνουν
  «0 €» τέσσερις φορές
- **Λίστα ανταγωνιστών** χωρίς αποτελέσματα
- **Ιστορικό τιμών** χωρίς σημεία
- **Ειδοποιήσεις** χωρίς εγγραφές (υπάρχει ήδη κείμενο, ευθυγραμμίζεται)

Κανένα «0» δεν εμφανίζεται ως δήθεν μέτρηση όταν απλώς λείπουν δεδομένα.

## 7. Οπτική γλώσσα

- **Ελληνικά παντού** στο UI (labels, μηνύματα, κουμπιά). Οι τεχνικοί όροι
  αποδίδονται σε γλώσσα ξενοδόχου: «Market P25» → «Χαμηλό εύρος αγοράς»,
  «Lead time» → «Ημέρες μέχρι την άφιξη», «Sample runs» → «Αναζητήσεις που
  συγκρίθηκαν».
- **Καθόλου emoji.** Εικονίδια SVG γραμμής, ενιαίο `stroke-width`, σε ένα
  αρχείο `icons.ts` ως inline templates. Το υπάρχον emoji καμπανάκι
  (`&#128276;` στο `notification-bell.component.ts`) αντικαθίσταται.
- Ιεραρχία μέσω τυπογραφίας και χώρου, όχι χρωματιστών συμβόλων. Η υπάρχουσα
  παλέτα (teal `#0f766e`, dark navy) διατηρείται.
- Πρόοδος ως λεπτές μπάρες, όχι checkbox glyphs.

## 8. Χειρισμός σφαλμάτων

| Σενάριο | Συμπεριφορά |
|---|---|
| `property-candidates` αποτυγχάνει | Μήνυμα + «Δοκίμασε ξανά», με δυνατότητα διόρθωσης ονόματος/τοποθεσίας |
| Κενή λίστα candidates | Καθοδήγηση για διόρθωση, όχι σφάλμα |
| Discovery job `failed` | Εξήγηση + επανάληψη· ο χρήστης μπορεί να προχωρήσει στον χάρτη |
| Scrape job `failed` στο βήμα 3 | Το μήνυμα του backend + επανάληψη· δεν μπλοκάρει την είσοδο στον χάρτη |
| 429 (quota) | Το μήνυμα του backend αυτούσιο, με πρόταση να δοκιμάσει αργότερα |
| Ο χρήστης φεύγει μεσοδρομής | Η κατάσταση ζει στο backend· ο `setupGuard` τον επιστρέφει στο σωστό βήμα |

## 9. Testing

**pytest**:

- §3.5 `search_property_candidates` επιστρέφει το best match πρώτο όταν ο
  provider δίνει τα candidates σε τυχαία σειρά· `automatic_setup` συνεχίζει να
  διαλέγει το ίδιο κατάλυμα όπως πριν
- §3.4 `PUT owned-property`: ενημερώνει τα στοιχεία, σβήνει τα παλιά room types
  και το selected category, ξεκινά νέο discovery job, κρατά το ίδιο `id`
- §3.4 `PUT` διατηρεί τους tracked competitors όταν το `canonical_destination`
  δεν αλλάζει, και τους σβήνει όταν αλλάζει
- §3.4 `DELETE owned-property`: 204, το κατάλυμα φεύγει, τα scrape jobs
  επιβιώνουν με `owned_property_id = NULL`
- §3.4 και τα δύο επιστρέφουν 404 για κατάλυμα άλλου λογαριασμού

**Playwright** (`frontend/e2e/`), με mocked backend όπως το υπάρχον
`no-second-click.spec.ts`:

1. Νέος χρήστης χωρίς property → ανακατεύθυνση σε `/setup` βήμα 1
2. «Ναι, αυτό είναι» → βήμα 2 **χωρίς** καμία κλήση δημιουργίας
2β. «Δείξε άλλα» → επιλογή διαφορετικού → `PUT /owned-property/{id}` με το
   **επιλεγμένο** candidate (όχι το προτεινόμενο)
3. Επιλογή δωματίου → `PUT selected-room-type`
4. Βήμα 3: **καμία** κλήση `POST /scrape-jobs/` πριν το κλικ (φρουρά κόστους)
5. «Αργότερα» → χάρτης με το checklist στο 3/5
6. Ολοκληρωμένος χρήστης → `/setup` τον στέλνει στον χάρτη
7. Κενές οθόνες: με άδεια δεδομένα δεν υπάρχει «0 €» στο market snapshot
8. Καθόλου emoji: assertion ότι το DOM δεν περιέχει τους σχετικούς κωδικούς
9. Settings → «Διαγραφή καταλύματος» απαιτεί πληκτρολόγηση του ονόματος πριν
   ενεργοποιηθεί το κουμπί

Το υπάρχον `no-second-click.spec.ts` πρέπει να παραμείνει πράσινο (τα
seeded localStorage δεδομένα καλύπτουν ήδη ολοκληρωμένο χρήστη).

## 10. Καθάρισμα αχρησιμοποίητου backend κώδικα

Σάρωση κάθε συμβόλου του `api/`, `scraper/` και `scripts/` για αναφορές σε όλο
το repo (συμπεριλαμβανομένου του `frontend/src`, ελέγχοντας κάθε route μέσω του
URL path του).

### 10.1 Διαγράφονται

| # | Τι | Απόδειξη |
|---|---|---|
| 1 | `GET /api/v1/agents/amenities-context` + `MarketService.get_amenities_context` + `AmenitiesContext` schema | Μηδέν καλούντες σε frontend, scripts, tests. Το frontend χτυπά μόνο `price-recommendation` |
| 2 | `GET /api/v1/agents/smart-advisor-context` (**μόνο το route**) | Ίδια απόδειξη. Η service method **μένει** — την καλεί ο live pricing handler |
| 3 | Legacy header `X-RoomPulse-Account-ID` (`LEGACY_HEADER_BRAND` και οι δύο χρήσεις του) | Αδύνατο να σταλεί: το `allow_headers` του CORS δεν το περιλαμβάνει |
| 4 | `ROOMPULSE_*` env aliases (`LEGACY_ENV_PREFIX`) | Κανένα `.env`, doc ή test δεν τα ορίζει. Τα `ix_roompulse_*` **index names** στις migrations μένουν — είναι αντικείμενα βάσης |
| 5 | Legacy `room_rates` write path: το `if config.write_legacy:` block, ο πίνακας `_room_rates`, τα flags `--write-legacy` / `--no-legacy` | Ο runner δεν στέλνει ποτέ τα flags και το default είναι `False`, άρα το branch είναι πάντα νεκρό στο subprocess |
| 6 | Νεκρά re-export imports σε `scraper/provider.py`, `scraper/__init__.py`, `booking_scraper_v3.py` | Pass-through χωρίς καταναλωτή· τα tests κάνουν monkeypatch με string path στο module ορισμού, όχι import μέσω shim |
| 7 | Unused imports: `Decimal` (`price_alert_service.py`), `TA_CENTER`/`KeepTogether` (`generate_roomrate_handoff_pdf.py`), `pytest`/`date` σε τρία test αρχεία | pyflakes |
| 8 | `NG_APP_*` μεταβλητές στο `.env.example` | Τίποτα δεν τις διαβάζει· το frontend παίρνει config από `window.__ROOMRATE_CONFIG__`. Αντικαθίστανται από σχόλιο που εξηγεί το `public/runtime-config.js` |

> **Δεν αγγίζουμε το `scraper/utils.py:256-260`.** Ο pyflakes το επισημαίνει
> ως αχρησιμοποίητο re-export, αλλά το `scraper/persistence.py` εισάγει
> `_filter_by_room_name` και τα δύο `_matches_required_*` **από το `.utils`**.
> Η αφαίρεσή του σπάει τον scraper στο import.

### 10.2 Διορθώνεται η αντίφαση docs/κώδικα

Τα docs παρουσιάζουν το `python -m scraper` ως κύριο entry point και το
`booking_scraper_v3.py` ως shim συμβατότητας. Ο κώδικας κάνει το αντίθετο: ο
`BookingScrapeJobRunner` εκτελεί `booking_scraper_v3.py` ως subprocess.

**Απόφαση:** ο κώδικας ευθυγραμμίζεται με τα docs. Ο runner καλεί
`python -m scraper`, το `scraper/__main__.py` γίνεται ο πραγματικός entry point,
και τα tests εισάγουν από το `scraper` package. Το `booking_scraper_v3.py`
διαγράφεται. Test-first: το `_build_args` / `_execute` test επιβεβαιώνει τη νέα
εντολή πριν αλλάξει ο runner.

### 10.3 Διατηρούνται σκόπιμα (τεκμηριώνεται το γιατί)

- `api/models/market.py` — **δεν** είναι νεκρό: είναι ο στόχος autogenerate του
  Alembic (`migrations/env.py`). Το API κάνει queries με raw SQL, οπότε μοιάζει
  αχρησιμοποίητο· προστίθεται σχόλιο ώστε να μην το σβήσει κανείς κατά λάθος
- `api/db_views.py` — και οι πέντε εκδόσεις view αναφέρονται από migrations
  (V1/V2 σε downgrade paths). Η διαγραφή τους σπάει το downgrade
- `POST /onboarding/owned-property` και `GET /property-candidates` — σήμερα
  test-only, αλλά **τα χρησιμοποιεί ο οδηγός** αυτού του spec
- `RoomRatesRepository` legacy source — τεκμηριωμένος διακόπτης rollback
- `PUT /tracked/competitors` — κρατείται για μελλοντική οθόνη διαχείρισης
- `GET /health` — liveness probe χωρίς in-repo client

### 10.4 Testing του καθαρίσματος

Το υπάρχον suite (551 tests) είναι το δίχτυ. Επιπλέον: τα tests που καλύπτουν
διαγραφόμενο κώδικα αφαιρούνται μαζί του, και μετά από κάθε διαγραφή τρέχει
`python -m pyflakes api scraper scripts` και το πλήρες suite.

## 11. Εκτός scope

- Πολλαπλά καταλύματα ανά λογαριασμό
- Πολλαπλά χρονικά παράθυρα αγοράς (σεζόν)
- i18n υποδομή — τα ελληνικά μπαίνουν απευθείας
- Email/push ειδοποιήσεις
- Ανασχεδιασμός του `settings`
