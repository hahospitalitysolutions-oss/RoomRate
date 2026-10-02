# Rate plans — οι διαφορετικές τιμές του ίδιου δωματίου

Ημερομηνία: 2026-09-29. Κατάσταση: εγκεκριμένο από τον ιδιοκτήτη (τα 4 βήματα της συζήτησης).
Βάση: `main 68612a8`. Branches: `feature/rate-plans-api` (backend, worktree `wt-round6-a`),
`feature/rate-plans-ui` (frontend, κύριο checkout). Στόχος: το ίδιο δωμάτιο να δείχνει όλα τα
πλάνα τιμής του με ετικέτες, και η σύγκριση να γίνεται σε όμοια πλάνα.

## 1. Τι επιστρέφει ο actor (επιβεβαιωμένο 2026-09-29 από datasets παλιών runs, δωρεάν)

Ανά `rooms[].options[]`: `price` (βασική τιμή διαμονής), `displayedPrice` (ό,τι βλέπει ο
επισκέπτης — μετά την έκπτωση, χωρίς μη συμπεριλαμβανόμενους φόρους), `excludedTaxesPrice`,
`discount {discountedPrice, text: "- 8%", description}` ή `null`, `hasGeniusDiscount` (bool),
`geniusDiscountText`, `id` (block id), `persons`, `currency`, `cancellationType`
(`non_refundable` | `free_cancellation` | άλλα), `freeCancellation` (bool), `yourChoices`
(ελληνικές ετικέτες, π.χ. «Μη επιστρέψιμη τιμή», «Πληρωμή online»). Ανά room επιπλέον:
`bedTypes`, `facilities`, `url`, `isPartnerOfferRoom`. ΣΗΜΕΡΑ κρατάμε μόνο price/persons/
freeCancellation/yourChoices → χάνουμε Genius, εκπτώσεις και τη displayed τιμή.

Γνωστά όρια (μένουν εκτός): τιμές μόνο-για-κινητά και τιμές συνδεδεμένων μελών δεν είναι
ορατές στον actor (desktop, ανώνυμος)· το UI το δηλώνει.

## 2. Scraper (`transform.py`) — νέες στήλες CSV ανά option

- `discounted_price_per_night_eur`: `discount.discountedPrice/nights` αν υπάρχει, αλλιώς ίση
  με `price_per_night_eur`. Η ΚΥΡΙΑ στήλη `price_per_night_eur` μένει ως έχει (συνέχεια
  ιστορικού)· η σύγκριση/εμφάνιση χρησιμοποιεί τη νέα.
- `discount_pct`: από `discount.text` («- 8%» → 8.0), αλλιώς υπολογισμένο
  `round((1 - discounted/price)*100, 1)`, αλλιώς NULL. `discount_label`: το `discount.text`
  καθαρισμένο («-8%») ή NULL.
- `has_genius_discount`: bool από `hasGeniusDiscount`.
- `cancellation_type`: το ωμό `cancellationType` (ήδη υπάρχει το Yes/No `free_cancellation`).
- `payment_label`: «Πληρωμή online» / «Πληρωμή στο κατάλυμα» αν υπάρχει στο `yourChoices`,
  αλλιώς NULL.
- `rate_block_id`: το option `id` (ή το ήδη παραγόμενο fallback) — ταυτότητα πλάνου, μπαίνει
  ΗΔΗ στο record_id (κανένα dedupe πρόβλημα), απλώς αποθηκεύεται και ως στήλη.

## 3. Βάση — migration `20260929_0025` (προσθετική, nullable, χωρίς backfill)

`roomrate_room_packages` + στήλες: `discounted_price_per_night_eur NUMERIC(10,2)`,
`discount_pct NUMERIC(5,1)`, `discount_label VARCHAR(40)`, `has_genius_discount BOOLEAN`,
`cancellation_type VARCHAR(40)`, `payment_label VARCHAR(60)`, `rate_block_id VARCHAR(80)`.
Normalizer: αντίστοιχα πεδία στο `NormalizedRoomRate` — όταν οι στήλες λείπουν από παλιά
CSV, defaults ΟΛΑ NULL (και το `has_genius_discount` NULL, όχι False, ώστε η επανεισαγωγή
παλιών δεδομένων να κρατά `rate_plan: null` — review 2026-09-29). Writer: στα INSERT/UPSERT
των packages. Παλιές γραμμές: NULL παντού → το UI τα αντιμετωπίζει ως «χωρίς στοιχεία πλάνου».

## 4. API (προσθετικά, αλλαγές μόνο σε schemas/service/repository ανάγνωσης)

- `CompetitorPackage` + `rate_plan: {discounted_price_per_night_eur, discount_pct,
  discount_label, has_genius_discount, cancellation_type, payment_label} | null` (null όταν
  όλα NULL). Το repository επιλέγει τις νέες στήλες.
- `Competitor` + ανά δωμάτιο εύρος: το frontend το υπολογίζει από τα packages (ΟΧΙ νέο πεδίο).
- Marker/summary: παραμένουν στη σημερινή κύρια τιμή (καμία αλλαγή συμπεριφοράς).
- Τιμολόγηση «όμοιο με όμοιο» (ελάχιστο, στο repository ιστορικού): το φθηνότερο ανά
  κατάλυμα υπολογίζεται ΠΡΩΤΑ μέσα στην ίδια κλάση ακύρωσης με το δικό πακέτο αναφοράς
  (`cancellation_type` ίδιο· άγνωστο/NULL συμμετέχει πάντα), αλλιώς πέφτει στο συνολικό
  ελάχιστο όπως σήμερα. Νέο πεδίο απάντησης `stats_scope.cancellation_class:
  "matched" | "all"` ώστε η σελίδα να γράφει «Σύγκριση σε τιμές ίδιας πολιτικής ακύρωσης».
  Διευκρίνιση (review 2026-09-29): το «matched» κρίνεται ΜΟΝΟ πάνω στις γραμμές της βάσης
  που πράγματι χρησιμοποιήθηκε — μόνο ίδιας κατηγορίας όταν `used == "same"`, το διευρυμένο
  σύνολο όταν `used == "same_plus_similar"`.

## 5. UI (`map-page` κάρτες + `pricing-page`)

- Κάθε ΔΩΜΑΤΙΟ ανταγωνιστή με >1 πακέτα: «από {min} € έως {max} €» (πάνω στις
  discounted τιμές όταν υπάρχουν, αλλιώς στις κύριες) + κουμπί ανάπτυξης που δείχνει τη
  λίστα πλάνων: τιμή, chips «Genius», «{discount_label}», «Δωρεάν ακύρωση»/«Μη επιστρέψιμη»,
  «{payment_label}», γεύμα (υπάρχον πεδίο). Ελληνικά, μηδέν emoji.
- Και σε δωμάτιο με ένα μόνο πακέτο, η εμφανιζόμενη τιμή είναι η discounted με τα chips της (review 2026-09-29).
- Υποσημείωση μία φορά στη λίστα: «Τιμές επίσημης ιστοσελίδας (desktop, χωρίς σύνδεση)».
- Pricing page: όταν `stats_scope.cancellation_class === "matched"`, γραμμή «Σύγκριση σε
  τιμές ίδιας πολιτικής ακύρωσης με το δωμάτιό σας.»

## 6. Tests

Backend: transform εξαγωγή (με/χωρίς discount, Genius true, «- 8%» parsing, παλιό CSV χωρίς
στήλες), migration head, writer upsert, repository επιλογή νέων στηλών, like-for-like
(ίδια κλάση, καμία ίδια → all). Frontend (νέο spec `rate-plans.spec.ts` + mocks με `**`):
εύρος από-έως, ανάπτυξη πλάνων με chips, null rate_plan (παλιά δεδομένα) χωρίς εύρος/chips,
γραμμή πολιτικής ακύρωσης στην τιμολόγηση. Υπάρχοντα specs που καρφώνουν πακέτα ενημερώνονται
χωρίς να χαθεί κάλυψη.
