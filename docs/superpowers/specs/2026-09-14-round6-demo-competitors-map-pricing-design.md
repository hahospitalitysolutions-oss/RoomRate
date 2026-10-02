# Round 6 — Περισσότεροι ανταγωνιστές, χάρτης, τιμολόγηση, σταθερότητα (demo Πέμπτης)

Ημερομηνία: 2026-09-14. Κατάσταση: εγκεκριμένο σχέδιο (ενότητες 1-4 εγκρίθηκαν από τον ιδιοκτήτη).

## 1. Πλαίσιο και στόχος

Την Πέμπτη 2026-09-17 το πρωί ο ιδιοκτήτης παρουσιάζει την πρόοδο της εφαρμογής με **live scrape** για το
Rea Hotel (Φαληράκι, δίκλινο twin). Σήμερα η αναζήτηση ανταγωνιστών επιστρέφει 2 καταλύματα, ο χάρτης
δεν δείχνει το δικό του ξενοδοχείο, όσο τρέχει το scrape δεν υπάρχει ένδειξη προόδου, και η σελίδα
τιμολόγησης δεν βρίσκει τα δεδομένα του χάρτη επειδή προτείνει άλλες ημερομηνίες.

Στόχος: από τον χάρτη, μία αναζήτηση με τις προεπιλογές να φέρνει ~40-50 καταλύματα σε 6-9 λεπτά με ορατή
πρόοδο, να τα δείχνει με απόσταση και ετικέτα κατηγορίας γύρω από το δικό του ξενοδοχείο, και με ένα κλικ
να ανοίγει σύσταση τιμής που βασίζεται στην ίδια αναζήτηση, σε σωστά ελληνικά, χωρίς παράλογα νούμερα.

Εκτός πεδίου: billing, Sentry/hosting, αναβάθμιση numpy, καθάρισμα smoke λογαριασμών, το LLM σκέλος
(δεν υπάρχει ακόμη `ANTHROPIC_API_KEY` — η σύσταση στο demo είναι η στατιστική).

## 2. Ευρήματα που καθορίζουν το σχέδιο

Διάγνωση της χοάνης (πραγματικά logs + διαγνωστικά scouts 2026-09-14):

| Στάδιο | Σήμερα | Πηγή |
|---|---|---|
| Το Booking έχει για «Φαληράκι» (destination id 900039186, CITY) | **180 καταλύματα** | log actor, scout 40 |
| Η σελίδα χάρτη ζητά | `limit` 8 (προεπιλογή), μέγιστο 50 | `map-page.component.ts:500,1100` |
| Scout 40 «Φαληράκι» | 40/40 με συντεταγμένες, όλα εντός 1,2 km | scout διαγνωστικό |
| Scout 40 «Ρόδος» | 13 εντός 10 km (Zoes, Ladiko Inn, Esperides, Kresten Royal/Καλλιθέα 6 km, Διόνυσος/Ιξιά 9 km) | scout διαγνωστικό |
| Φίλτρο κατηγορίας πριν την αποθήκευση (μόνο double/twin) | 25 γραμμές → 12, **10 καταλύματα → 2** (τα aparthotel/studios κόβονται ολόκληρα) | `scraper/persistence.py:46-48`, job 2023c76e |
| Cache scout | κλειδί χωρίς `limit`: μια παλιά λίστα 10 απαντά σε αίτημα για 40 | `scraper/scout.py:33-60` |
| Ο actor (`voyager/booking-scraper`) | δεν έχει είσοδο ακτίνας/συντεταγμένων· έχει `search, maxItems, propertyType, sortBy, minScore, starsCountFilter, minMaxPrice, flexWindow`· χρέωση 0,005 $/κατάλυμα | input schema build 0.0.187 |
| Έξοδος scraper προς API | διαβάζεται μόνο στο τέλος της διεργασίας (`communicate`) — δεν υπάρχει κανάλι προόδου | `scrape_job_service.py:263-280` |
| Σελίδα τιμολόγησης | προεπιλογή ημερομηνιών σήμερα+30/4 νύχτες, αγνοεί την τελευταία αναζήτηση | `pricing-page.component.ts`, `date-defaults.ts` |
| Στατιστικά αγοράς | η γραμμή του ίδιου του ξενοδοχείου μετρά μέσα στον διάμεσο· το όριο ±20% «στραγγίζει» το εύρος σε μία τιμή («160 € – 160 €»)· σημειώσεις στα αγγλικά | `price_history_repository.py:17-55`, `price_recommendation_agent.py:291-328` |
| Ειδοποιήσεις τιμής | συγκρίνουν με το αμέσως προηγούμενο scrape, έστω 3 λεπτά πριν («+113%») | `price_alert_service.py:163-168` |
| Χάρτης | δείκτες χωρίς κείμενο, σταθερά κατώφλια 75/130 €, το δικό σου ξενοδοχείο ποτέ, popup κλείνει σε κάθε repaint, δύο κουτιά σύνοψης που διαφωνούν | `map-page.component.ts:1866-1996` |

Απόφαση ιδιοκτήτη (ερώτηση 4): και οι 4 χαλαρώσεις (γειτονικές περιοχές + ακτίνα, άλλες κατηγορίες ως
«Παρόμοιο», χωρητικότητα ≥ ατόμων, μεγαλύτερο scout). Προσέγγιση Β.

## 3. Ροή Α — Scraper και API εργασίας/ανάγνωσης

### 3.1 Αίτημα εργασίας (`api/schemas/scrape_jobs.py` `ScrapeJobCreate`)

Νέα πεδία:

- `nearby_destinations: list[str] = []` — έως 8 στοιχεία, 1-100 χαρακτήρες το καθένα, trimmed, χωρίς
  διπλότυπα (case-insensitive), χωρίς τον κύριο προορισμό.
- `radius_km: float | None = None` — 0.5 έως 50. `None` = χωρίς φίλτρο απόστασης.

`filters_payload.limit`: προεπιλογή **40** (ήταν 25), όρια 1..80. Νέο server-side `nearby_limit =
min(20, limit)`. Ο server υπολογίζει `hotel_cap = min(120, limit + nearby_limit × len(nearby_destinations))`
(μέγιστα καταλύματα προς deep crawl, περνά ως `--deep-crawl-max-hotels`). Το `deep_crawl_max_items`, αν
λείπει από το payload, γίνεται `min(320, max(2 × hotel_cap, 40))`. Η σελίδα χάρτη παύει να στέλνει
`deep_crawl_max_items`.

Κέντρο ακτίνας: **από τον server**, ποτέ από τον browser. `OnboardingRepository.get_owned_property` επιστρέφει
επιπλέον `latitude, longitude, booking_url`. Αν `radius_km` δόθηκε αλλά το κατάλυμα δεν έχει συντεταγμένες,
η εργασία τρέχει χωρίς ακτίνα και το `result_summary.warnings` περιέχει `"radius_skipped_no_coordinates"`.

Η εργασία αποθηκεύει `nearby_destinations` και `radius_km` (νέες στήλες στο `roomrate_scrape_jobs`, Alembic
migration `20260915_0024`, nullable, χωρίς backfill) ώστε η επαναφορά της τελευταίας αναζήτησης να δείχνει
ό,τι στάλθηκε. Η ίδια migration προσθέτει `booking_url TEXT NULL` στο `roomrate_properties`· ο
`normalized_market_writer` το γεμίζει από τη στήλη CSV `hotel_url` στο upsert (`DO UPDATE SET booking_url`),
ώστε παλιά καταλύματα να το αποκτούν στο επόμενο scrape.

Προεπιλογές γειτονικών περιοχών ανά canonical προορισμό: στατικός πίνακας στο
`api/services/nearby_destinations.py` (`faliraki → ["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"]`, οι
υπόλοιποι κενοί), εκτεθειμένος από `GET /api/v1/onboarding/nearby-destinations?destination=<str>` →
`{"destination": str, "canonical": str, "nearby": [str]}`.

### 3.2 CLI του scraper (`api/services/scrape_job_service.py:_build_args`, `scraper/cli.py`)

Νέα ορίσματα: `--nearby-destination <str>` (επαναλαμβανόμενο), `--nearby-max-items <int>`,
`--origin-lat <float> --origin-lng <float>`, `--radius-km <float>`, `--deep-crawl-max-hotels <int>`.
Προεπιλογές: `--deep-crawl-batch-size 8` (ήταν 4), workers 3 (αμετάβλητο — όριο ταυτόχρονων runs Apify).

### 3.3 Scout πολλών προορισμών (`scraper/scout.py`)

Νέα συνάρτηση `fetch_hotel_lists(client, config, engine) -> list[dict]`:

1. Λίστα προορισμών = `[config.destination] + config.nearby_destinations`. Για κάθε έναν, αντίγραφο του
   `ScraperConfig` με `scout_max_items = config.scout_max_items` (κύριος) ή `config.nearby_max_items`.
2. Τρέχουν σε `ThreadPoolExecutor(max_workers=config.deep_crawl_workers)` μέσω της υπάρχουσας
   `fetch_hotel_list` (cache ανά προορισμό, retries όπως σήμερα). Αποτυχία **γειτονικού** scout → warning και
   συνέχεια· αποτυχία του **κύριου** → `ActorRunError` όπως σήμερα.
3. Ένωση με κλειδί το normalized URL· ο πρώτος (κύριος προορισμός πρώτος) κερδίζει.
4. Αν υπάρχει `origin`: `distance_km = haversine(origin, hotel)` με 1 δεκαδικό. Κατάλυμα χωρίς συντεταγμένες
   ενώ η ακτίνα είναι ενεργή → εξαιρείται (καταγράφεται). Κρατούνται όσα έχουν `distance_km ≤ radius_km`.
   Αν η ένωση είχε καταλύματα αλλά κανένα δεν μένει μέσα στην ακτίνα → warning `radius_excluded_all`
   (αλλιώς η εργασία φαίνεται σαν «άδεια αγορά»· διόρθωση από review 2026-09-15).
5. Ταξινόμηση κατά απόσταση (αύξουσα, `None` στο τέλος)· περικοπή στα `deep_crawl_max_hotels`.
6. Επιστρέφει τη λίστα για το deep crawl. Η απόσταση **δεν** αποθηκεύεται (υπολογίζεται στην ανάγνωση από
   τις συντεταγμένες· καμία αλλαγή στο CSV ή στις κανονικοποιημένες στήλες).

Cache scout: `_load_scout_cache` θεωρεί HIT μόνο αν `len(rows) >= config.scout_max_items` (αλλιώς MISS με
log «cache μικρότερη από το αίτημα: 10 < 40»). Χωρίς αλλαγή σχήματος.

### 3.4 Φίλτρα πριν την αποθήκευση (`scraper/persistence.py`)

Για `competitor_search` το φίλτρο «κατηγορία εντός pool double/twin» **καταργείται**. Στη θέση του:

- `single_rooms`: γραμμές με `room_type_category == "single"` αφαιρούνται, **μόνο όταν `ceil(adults / rooms) ≥ 2`**.
- `capacity`: γραμμές με γνωστό `max_persons > 0` και `max_persons < ceil(adults / rooms)` αφαιρούνται
  (το `max_persons` είναι άτομα ανά δωμάτιο της τιμής· η αρχική διατύπωση «`< adults`» έκοβε όλες τις
  τιμές 2 ατόμων σε αναζήτηση 2 δωματίων/4 ενηλίκων — διόρθωση από review 2026-09-15).

Το `filter_counts` αποκτά τα δύο νέα στάδια και χάνει το `room_type_category`. Η στήλη
`room_type_category` διατηρείται σε κάθε γραμμή (`None` για άγνωστα ονόματα). Ο καταρράκτης ονόματος
δωματίου, τα φίλτρα γεύματος/ακύρωσης/παροχών: αμετάβλητα.

### 3.5 Πρόοδος σε πραγματικό χρόνο (`scraper/progress.py`)

`ProgressReporter(engine, scrape_job_id)` με `update(stage, done=None, total=None, **counts)`. Γράφει

```sql
UPDATE roomrate_scrape_jobs
SET result_summary = COALESCE(result_summary, '{}'::jsonb) || jsonb_build_object('progress', :progress::jsonb)
WHERE id = :job_id AND status = 'running'
```

No-op όταν `engine is None` (dry run) ή δεν υπάρχει `scrape_job_id`· κάθε σφάλμα καταγράφεται και
καταπίνεται (η πρόοδος δεν ρίχνει ποτέ scrape). Σημεία εκπομπής: έναρξη scout (`destinations_total`),
τέλος κάθε scout (`hotels_found` σωρευτικά), μετά την ένωση/ακτίνα (`hotels_in_radius`), μετά από κάθε
batch deep crawl (`done/total` καταλύματα), έναρξη αποθήκευσης.

Σχήμα `result_summary.progress`:

```json
{"stage": "scout" | "deep_crawl" | "persist", "done": 12, "total": 48,
 "destinations_total": 5, "hotels_found": 61, "hotels_in_radius": 48,
 "updated_at": "2026-09-16T10:02:11Z"}
```

Η ολοκλήρωση (`complete_job`) αντικαθιστά ολόκληρο το `result_summary` με την τελική σύνοψη (χωρίς
`progress`), όπως σήμερα. Το `GET /api/v1/scrape-jobs/{id}` ήδη επιστρέφει `result_summary`.

### 3.6 Ανάγνωση (`api/repositories/room_rates_repository.py`, `api/services/market_service.py`)

- Νέα παράμετρος ανάγνωσης `include_similar: bool = True` σε `/api/v1/maps/competitors`,
  `/api/v1/competitors/`, `/api/v1/market/summary`. `True` → `room_type_category IS DISTINCT FROM 'single'`·
  `False` → `room_type_category IN (comparable pool της βασικής κατηγορίας)` (η σημερινή συμπεριφορά).
- Στη διαδρομή ανάγνωσης **ανά εργασία** (`scrape_job_id`) το φίλτρο `city IN (aliases)` **αφαιρείται** — η
  εργασία ορίζει το σύνολο. Η διαδρομή «τελευταία τιμή ανά προορισμό» (view) μένει ως έχει.
- Η εξαίρεση του δικού καταλύματος (ισότητα `display_name`) μένει. Νέα μέθοδος
  `fetch_own_property_rates(account_id, scrape_job_id, display_name)` επιστρέφει τις γραμμές που εξαιρούνται.
- `category_match`: `"same"` αν `room_type_category ∈ comparable_pool(baseline_category)`, αλλιώς `"similar"`
  (και για `None`).
- `distance_km`: haversine μεταξύ συντεταγμένων καταλύματος ιδιοκτήτη και ανταγωνιστή, 1 δεκαδικό, `null` αν
  λείπουν συντεταγμένες. Υλοποίηση στο `api/services/market_helpers.py`.

Επεκτάσεις σχημάτων (`api/schemas/market.py`), όλες προσθετικές:

- `CompetitorPackage` + `room_type_category: str | null`, `category_match: "same" | "similar"`.
- `Competitor` + `distance_km: float | null`, `category_match` (= `"same"` αν οποιοδήποτε πακέτο είναι
  `same`), `booking_url: str | null` (από τη νέα στήλη `roomrate_properties.booking_url`).
- `CompetitorMapMarker` + `distance_km`, `category_match`, `booking_url`. Ο δείκτης χρησιμοποιεί το
  φθηνότερο πακέτο **ίδιας κατηγορίας** αν υπάρχει, αλλιώς το φθηνότερο παρόμοιο.
- `MarketSummary` + `total_records`, `same_category_hotels`, `similar_hotels`. Υπολογισμός: φθηνότερο
  συγκρίσιμο πακέτο ανά κατάλυμα (ένα κουτί σύνοψης στη σελίδα).
- `/api/v1/competitors/` `sort` δέχεται και `distance`.

Νέο endpoint `GET /api/v1/maps/own-property?owned_property_id=&scrape_job_id=` →

```json
{"display_name": "Rea Hotel", "latitude": 36.34, "longitude": 28.20, "radius_km": 10.0,
 "price_per_night_eur": 92.0, "room_type": "Δίκλινο Δωμάτιο ...", "booking_url": "https://..."}
```

`radius_km` από την εργασία, `price_per_night_eur`/`room_type` από το φθηνότερο πακέτο ίδιας κατηγορίας της
δικής γραμμής στην εργασία (`null` αν το Booking δεν επέστρεψε το κατάλυμα). Συντεταγμένες `null` → το
frontend δεν σχεδιάζει δείκτη «Εσείς» και το λέει.

### 3.7 Tests ροής Α (pytest)

Ένωση/προτεραιότητα URL, ακτίνα (μέσα/έξω/χωρίς συντεταγμένες/χωρίς origin), περικοπή και ταξινόμηση,
αποτυχία γειτονικού vs κύριου scout, cache «μικρότερη από το αίτημα», φίλτρα `single_rooms`/`capacity`
και `filter_counts`, `ProgressReporter` (no-op σε dry run, σφάλμα καταπίνεται, JSON merge), `_build_args`
για τα νέα ορίσματα και το `hotel_cap`, schema validation (`nearby_destinations`, `radius_km`), ανάγνωση
`include_similar`/city ανά εργασία/`category_match`/`distance_km`, `own-property` endpoint, migration head.

## 4. Ροή Β — Σελίδα χάρτη (`frontend/src/app/pages/map-page.component.ts`)

1. **Φόρμα:** «Πλήθος ανταγωνιστών» προεπιλογή 40, `max` 80 (`readBoundedNumber(limit, 40, 1, 80)`)· νέα
   πεδία «Γειτονικές περιοχές» (chips: προσυμπλήρωση από `nearby-destinations`, προσθήκη/αφαίρεση, έως 8) και
   «Ακτίνα (km)» (αριθμός 1-30, προεπιλογή 10). Η φόρμα ανοίγει αυτόματα όταν δεν υπάρχει προηγούμενη
   αναζήτηση. Η επαναφορά τελευταίας αναζήτησης γεμίζει και τα νέα πεδία από την εργασία.
2. **Πρόοδος:** όσο η εργασία είναι `queued/running`, κάτω από το banner εμφανίζεται
   «Στάδιο 1/2 — Αναζήτηση καταλυμάτων σε {destinations_total} περιοχές… βρέθηκαν {hotels_found}» /
   «Στάδιο 2/2 — Τιμές δωματίων: {done}/{total} καταλύματα» / «Αποθήκευση αποτελεσμάτων…» και χρονόμετρο
   `m:ss` από το `started_at` (ή `requested_at`). Χωρίς `progress` ακόμη: «Εκκίνηση…». Το polling των 5"
   και το όριο των 120 προσπαθειών (10') μένουν· το όριο ανεβαίνει σε 180 (15') γιατί τα scrapes μεγάλωσαν.
3. **Δείκτης «Εσείς»:** από `maps/own-property`· διακριτό στυλ (σκούρος, ετικέτα «Εσείς»), popup με όνομα,
   «Η τιμή σας στο Booking: 92 €» όταν υπάρχει, «Ακτίνα 10 km». Κύκλος ακτίνας ως GeoJSON πολύγωνο
   (64 σημεία) σε fill+line layer· `fitBounds` περιλαμβάνει δείκτες, «Εσείς» και κύκλο.
4. **Δείκτες ανταγωνιστών:** ετικέτα τιμής «85 €» πάνω στον δείκτη· γεμάτος για `same`, με περίγραμμα για
   `similar`· χρώμα τιμής από τριτημόρια των τιμών των ορατών δεικτών (όχι σταθερά 75/130). Υπόμνημα στη
   γωνία (χαμηλή/μεσαία/υψηλή, ίδια κατηγορία/παρόμοιο, εσείς).
5. **Popup:** + «Απόσταση 0,4 km», ετικέτα «Ίδια κατηγορία»/«Παρόμοιο», σύνδεσμος «Άνοιγμα στο Booking»
   (νέα καρτέλα, `rel="noopener"`). Δεν κλείνει στα repaints: το `renderMarkers` συμφιλιώνει ανά κλειδί
   `property_id ?? hotel_name` (ενημερώνει κλάσεις/aria/popup των υπαρχόντων, προσθέτει νέους, αφαιρεί
   όσους λείπουν) αντί για `clearMarkers()`.
6. **Λίστα:** κάθε κάρτα δείχνει «0,4 km» και chip κατηγορίας· διακόπτης «Μόνο ίδια κατηγορία» (στέλνει
   `include_similar=false` σε δείκτες, ανταγωνιστές, σύνοψη)· «Ταξινόμηση κατά» + «Απόσταση».
7. **Μία σύνοψη:** το πάνω «Σύνοψη αγοράς» και το κάτω «Η αγορά της περιοχής» ενώνονται σε ένα κουτί από
   `/market/summary` («Καταλύματα N · Καταγραφές M · Ίδια κατηγορία K · Παρόμοια L», ελάχιστη/διάμεση/
   μέση/μέγιστη, μέση βαθμολογία). Ο τίτλος «N ανταγωνιστές» μετρά καταλύματα.
8. **Σύνδεση με τιμολόγηση:** μετά από ολοκληρωμένη αναζήτηση, κουμπί «Σύσταση τιμής για αυτή την
   αναζήτηση» → `/pricing?job=<id>`.
9. **Μη σχεδιάσιμα:** αποτελέσματα χωρίς συντεταγμένες μένουν στη λίστα (όπως σήμερα)· το κείμενο της
   προειδοποίησης `radius_skipped_no_coordinates` είναι «Η ακτίνα δεν εφαρμόστηκε: το κατάλυμά σας δεν έχει
   συντεταγμένες».

Tests (Playwright, mocks με `**`): φόρμα με προεπιλογές και νέα πεδία στο POST· πρόοδος από
`result_summary.progress` και χρονόμετρο· δείκτης «Εσείς» και κύκλος (ή απουσία τους με μήνυμα)· ετικέτες
τιμής/κατηγορίας· popup ανοιχτό μετά από τικ κάρτας και αλλαγή φίλτρου· διακόπτης «Μόνο ίδια κατηγορία»
στέλνει `include_similar=false`· ταξινόμηση κατά απόσταση· ένα κουτί σύνοψης· κουμπί προς
`/pricing?job=`. Τα υπάρχοντα specs (`map-auto-plot`, `map-page-polish`) ενημερώνονται για τα νέα πεδία.

## 5. Ροή Γ — Τιμολόγηση

### 5.1 Σελίδα (`frontend/src/app/pages/pricing-page.component.ts`)

- Προσυμπλήρωση: `?job=<id>` → αλλιώς `workflow.lastCompetitorJobId` → `GET /api/v1/scrape-jobs/{id}`. Αν η
  εργασία είναι `completed` και `competitor_search`: ημερομηνίες, ενήλικες/παιδιά/δωμάτια, κατηγορία από την
  εργασία και σημείωση «Σύμφωνα με την αναζήτηση της {ημερομηνία}». Αλλιώς οι σημερινές προεπιλογές.
- Θέση στην αγορά: «Φθηνότερα από εσάς: {k} από {n} καταλύματα» (από `position`)· «Τάση (7 ημερών)»
  όταν λείπει: «— (χρειάζονται αναζητήσεις με 7+ ημέρες διαφορά)».
- Πηγή τιμής αναφοράς: «Η τιμή σας στο Booking για αυτές τις ημερομηνίες» (`booking_live`) ή «Τιμή
  αναφοράς από την εγγραφή» (`onboarding_sample`).
- Βάση στατιστικών: «Βάση: {same} ίδιας κατηγορίας» ή «Βάση: {same} ίδιας κατηγορίας + {similar} παρόμοια»
  (από `stats_scope`).
- Οι σημειώσεις/παράγοντες/αιτιολόγηση εμφανίζονται όπως έρχονται (πλέον ελληνικά) — χωρίς διπλή
  εμφάνιση (η λίστα σημειώσεων παύει να επαναλαμβάνει την αιτιολόγηση).

### 5.2 Backend (`api/routers/agents.py`, `price_statistics_service.py`, `price_recommendation_agent.py`, `price_history_repository.py`)

- **Εξαίρεση δικής γραμμής:** μέσα στο `price_history_repository` (και όχι στους routers): το CTE εξαιρεί
  κάθε κατάλυμα του λογαριασμού με `NOT EXISTS (SELECT 1 FROM roomrate_owned_properties op WHERE
  op.account_id = :account_id AND lower(trim(op.display_name)) = lower(trim(p.canonical_name)))`. Ισχύει
  αυτόματα για στατιστικά, ιστορικό (`/market/price-history`) και ειδοποιήσεις, χωρίς αλλαγή στο
  `api/routers/market.py` (που ανήκει στη ροή Α).
- **Τιμή αναφοράς:** (1) η δική γραμμή στο τελευταίο ολοκληρωμένο run του κλειδιού αγοράς — φθηνότερο
  πακέτο στο comparable pool, αλλιώς φθηνότερο οποιοδήποτε → `own_price_source="booking_live"`· (2) αλλιώς
  `sample_price_per_night_eur` → `"onboarding_sample"`· (3) αλλιώς `null` (όπως σήμερα).
- **Κανόνας βάσης:** το repository επιστρέφει ανά (run, κατάλυμα) `min_price_same` και `min_price_similar`
  (comparable pool της βασικής κατηγορίας vs υπόλοιπα εκτός `single`). Η υπηρεσία υπολογίζει διάμεσο/p25/p75
  στα `same`· αν τα καταλύματα `same` του τελευταίου run είναι < 5 και υπάρχουν `similar`, χρησιμοποιεί
  `same + similar` (μία τιμή ανά κατάλυμα: η φθηνότερη ίδιας κατηγορίας αν υπάρχει, αλλιώς η φθηνότερη
  παρόμοια — όπως ο δείκτης του χάρτη· διόρθωση από review 2026-09-15). «Τελευταίο run» = όλα τα runs με το
  πιο πρόσφατο `finished_at` (μία εργασία γράφει πλέον ένα run, αλλά τα παλιά δεδομένα μπορεί να είναι
  σπασμένα ανά πόλη). Απάντηση: `stats_scope: {"same_category": n, "similar": m,
  "used": "same" | "same_plus_similar"}`.
- **Θέση:** `position: {"cheaper_than_you": k, "total": n}` δίπλα στο υπάρχον `own_position_percentile`.
- **Βεβαιότητα:** μετρά διαφορετικές ημερολογιακές ημέρες runs: high ≥ 5 ημέρες και διαθέσιμη τάση 7d,
  medium ≥ 2 ημέρες, αλλιώς low.
- **Όριο ±20%:** `recommended`, `low`, `high` περιορίζονται **ανεξάρτητα** στο `[own×0.8, own×1.2]`. Αν μετά
  `low == high`, `low = max(band_low, recommended×0.95)`, `high = min(band_high, recommended×1.05)`. Σημείωση
  όταν παρεμβαίνει: «Η αγορά είναι {±NN}% σε σχέση με την τιμή σας· η σύσταση περιορίζεται στο ±20% (όριο
  ασφαλείας)». Χωρίς τιμή αναφοράς δεν εφαρμόζεται (όπως σήμερα).
- **Ελληνικά:** όλα τα κείμενα `notes`, `key_factors`, `reasoning` της στατιστικής διαδρομής στα ελληνικά
  (π.χ. «Διάμεσος αγοράς 120 €», «Φθηνότερα από εσάς: 3 από 12», «Χρόνος έως την άφιξη 11 ημέρες»,
  «Βάση: 1 αναζήτηση», «Το ιστορικό δεν καλύπτει 7 ημέρες· η τάση 7 ημερών δεν είναι διαθέσιμη»). Το
  system prompt του LLM αποκτά «Απάντησε αποκλειστικά στα ελληνικά». Τα tests που καρφώνουν αγγλικά
  κείμενα ενημερώνονται.
- **Cache:** το κλειδί της 15λεπτης cache περιλαμβάνει το `id` του τελευταίου ολοκληρωμένου run του κλειδιού
  αγοράς· νέο scrape = νέα σύσταση.
- **Ειδοποιήσεις:** «προηγούμενο» run = το πιο πρόσφατο με `finished_at ≤ latest.finished_at − 12h`
  (`fetch_latest_vs_previous(min_gap_hours=12)`)· χωρίς τέτοιο run δεν παράγεται ειδοποίηση.

Tests (pytest): εξαίρεση δικής γραμμής, επιλογή τιμής αναφοράς (3 περιπτώσεις), κανόνας < 5, `position`,
βεβαιότητα ανά ημέρες, όριο χωρίς κατάρρευση + σημείωση, ελληνικά κείμενα, cache με run id, ειδοποίηση με
κενό 12h. Playwright: προσυμπλήρωση από `?job=` και από storage, κείμενα θέσης/βάσης/πηγής τιμής.

## 6. Σταθερότητα

- Βάση εκκίνησης: πλήρης εκτέλεση pytest (639), Playwright (162+1) και lint/build στο `main` πριν από κάθε
  αλλαγή· ίδια εκτέλεση μετά τη συγχώνευση.
- `api/config.py`: `env_file_encoding="utf-8-sig"` (το `.env` του ιδιοκτήτη έχει BOM· ο scraper ήδη το ανέχεται).
- Ερμητικά e2e: το `seedBrowserState` (ή ένα κοινό `mockNotificationBell`) κάνει mock by default το
  `**/api/v1/notifications/unread-count**` → `{ "unread": 0 }`. Σήμερα, αν τρέχει το πραγματικό API στο :8000,
  τρία tests του `setup-wizard.spec.ts` αποτυγχάνουν ψευδώς: το 401 της μη-mocked κλήσης ερμηνεύεται ως
  «η συνεδρία έληξε» (επιβεβαιώθηκε 2026-09-15 από το log του uvicorn). Βάση εκκίνησης στο `main`: pytest 639,
  lint καθαρό, Playwright 159 + 3 ψευδείς + 1 skipped.
- `source_property_key` (διπλότυπα καταλύματα όταν η πόλη γράφεται «Faliraki»): **αναβάλλεται**. Αλλαγή του
  τύπου του κλειδιού αλλάζει την ταυτότητα των 22 υπαρχόντων καταλυμάτων και θα έσπαγε το ιστορικό τιμών λίγο
  πριν το demo. Καταγράφεται ως εκκρεμότητα με migration δεδομένων.
- Χωρίς αλλαγή: `SCRAPE_JOB_TIMEOUT_SECONDS=1800` καλύπτει scrape 120 καταλυμάτων.

## 7. Συμβόλαιο API (για mocks των ροών Β και Γ)

| Endpoint | Αλλαγή |
|---|---|
| `POST /api/v1/scrape-jobs` | + `nearby_destinations: string[]`, `radius_km: number\|null`; `filters_payload.limit` προεπιλογή 40 |
| `GET /api/v1/scrape-jobs/{id}` | + `nearby_destinations`, `radius_km`; `result_summary.progress` όσο τρέχει· `result_summary.warnings: string[]` (τιμές: `radius_skipped_no_coordinates`, `radius_excluded_all`, `nearby_scout_failed:<περιοχή>`) |
| `GET /api/v1/onboarding/nearby-destinations?destination=` | νέο: `{destination, canonical, nearby: string[]}` |
| `GET /api/v1/maps/competitors` | + `include_similar` (default true)· δείκτης + `distance_km`, `category_match`, `booking_url` |
| `GET /api/v1/maps/own-property` | νέο (βλ. 3.6) |
| `GET /api/v1/competitors/` | + `include_similar`, `sort=distance`; ανταγωνιστής + `distance_km`, `category_match`, `booking_url`; πακέτο + `room_type_category`, `category_match` |
| `GET /api/v1/market/summary` | + `include_similar`; + `total_records`, `same_category_hotels`, `similar_hotels` |
| `POST /api/v1/agents/price-recommendation` (υπάρχον) | + `own_price_source`, `position`, `stats_scope`; ελληνικά κείμενα |
| `GET /api/v1/market/price-history` | εξαιρεί τη δική γραμμή |

## 8. Οργάνωση εργασίας

- Branch `feature/round6-demo` από `main` (`38ccace`). Τρεις ροές σε ξεχωριστά worktrees:
  - **Α** (`scraper/`, `api/schemas/scrape_jobs.py`, `api/services/scrape_job_service.py`,
    `api/services/nearby_destinations.py`, `api/repositories/room_rates_repository.py`,
    `api/repositories/onboarding_repository.py` (SELECT), `api/services/market_service.py`,
    `api/services/market_helpers.py`, `api/schemas/market.py`, `api/routers/maps.py`,
    `api/routers/competitors.py`, `api/routers/market.py` (summary), migration, tests).
  - **Β** (`frontend/src/app/pages/map-page.component.ts`, `frontend/src/app/types/market.ts`, e2e map specs,
    helpers).
  - **Γ** (`api/routers/agents.py`, `api/services/price_statistics_service.py`,
    `api/services/price_recommendation_agent.py`, `api/repositories/price_history_repository.py`,
    `api/services/price_alert_service.py`, `frontend/src/app/pages/pricing-page.component.ts`,
    `frontend/src/app/types/pricing.ts`, e2e pricing spec, tests).
  Το `frontend/src/app/types/market.ts` και το `api/routers/market.py` ανήκουν στη Β και στην Α αντίστοιχα·
  η Γ δεν τα αγγίζει. Η Γ διαβάζει την εργασία μέσω του υπάρχοντος `GET /api/v1/scrape-jobs/{id}`.
- Σειρά: Α ξεκινά πρώτη (συμβόλαιο)· Β και Γ παράλληλα με mocks. Κάθε ροή: υλοποίηση + tests → ανεξάρτητος
  reviewer → διορθώσεις. Συγχώνευση Α → Β → Γ, πλήρης βάση ελέγχων, PR, CI.
- Χρονοδιάγραμμα: Τρίτη 15/9 υλοποίηση + reviews· Τετάρτη 16/9 συγχώνευση, CI, πρόβα live μπροστά στον
  ιδιοκτήτη, διορθώσεις, scrape «προθέρμανσης» το βράδυ· Πέμπτη 17/9 πρωί demo.

## 9. Αποδοχή — πρόβα live (Τετάρτη)

1. Ο ιδιοκτήτης συνδέεται· ο χάρτης δείχνει «Εσείς», κύκλο 10 km και την τελευταία αναζήτηση.
2. «Εύρεση ανταγωνιστών» με τις προεπιλογές (40, 4 γειτονικές, 10 km): πρόοδος στάδιο 1 → 2 με μετρητές
   και χρονόμετρο· ολοκλήρωση σε ≤ 10 λεπτά.
3. Χάρτης: ≥ 30 δείκτες με τιμές, ετικέτες κατηγορίας, αποστάσεις, popup που μένει ανοιχτό μετά από τικ.
4. «Μόνο ίδια κατηγορία» → μόνο double/twin, σύνοψη και τίτλος ενημερώνονται.
5. «Σύσταση τιμής για αυτή την αναζήτηση» → σελίδα προσυμπληρωμένη, κάρτα με τιμή, εύρος (όχι μία τιμή),
   «Φθηνότερα από εσάς: k από n», βάση, πηγή τιμής, ελληνικά κείμενα· ιστορικό με το run.
6. Ειδοποιήσεις: κανένα «+113%» από scrape λίγων λεπτών.
7. pytest, Playwright, lint/build, CI πράσινα στο PR.
