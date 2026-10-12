import { Component } from "@angular/core";
import { RouterLink } from "@angular/router";

/**
 * Public privacy policy — reachable WITHOUT a session (see app.routes.ts).
 *
 * This is a DRAFT, and the page says so in a notice the reader cannot miss.
 * It is written against what RoomRate actually stores today, not against a
 * generic template: the account e-mail lives in Neon Auth, the owned
 * property (name, destination, Booking listing) and its selected room type in
 * `roomrate_owned_properties` / `roomrate_owned_property_room_types`, the
 * tracked competitors in `roomrate_tracked_competitors`, and the price
 * observations scraped from PUBLIC Booking listings in
 * `roomrate_rate_observations` / `roomrate_room_packages`. Notifications and
 * alert rules, plus the price-recommendation audit trail written when the
 * Anthropic leg is enabled, complete the picture.
 *
 * The processor list is the deployment's real one, Sentry included: api/main.py
 * calls init_sentry() whenever SENTRY_DSN is set, and although the SDK runs
 * with send_default_pii=False, an exception can still carry an account id in
 * its message or frame. Naming it is cheaper than explaining the omission.
 *
 * Deletion is described as the product performs it: the settings page deletes
 * the PROPERTY (DELETE /api/v1/onboarding/owned-property/{id}); there is no
 * account-deletion endpoint, so the ACCOUNT goes through the contact address.
 * The GDPR right to erasure below is unaffected -- it is a right to ask, and
 * asking is exactly the mechanism.
 *
 * Retention is described as a concept, not as a number: the actual windows are
 * deployment settings (`NOTIFICATION_RETENTION_DAYS`,
 * `SCRAPE_CSV_RETENTION_DAYS`, `PRICE_RECOMMENDATION_AUDIT_RETENTION_DAYS`), so
 * hard-coding days here would create a promise the operator can silently break.
 *
 * The cookie section is a verified factual claim: `document.cookie` appears
 * nowhere in `frontend/src`; the only cookie is Neon Auth's session cookie on
 * its own domain, and the setup progress lives in localStorage. e2e/legal-pages.spec.ts pins it, so if
 * an analytics cookie ever lands the spec is the place that argues back.
 *
 * No "@" character may appear in this template: Angular's block syntax claims
 * it, and an unknown block is a compile error. Contact details are bracketed
 * placeholders anyway until legal review.
 */
@Component({
  selector: "app-privacy-page",
  standalone: true,
  imports: [RouterLink],
  template: `
    <main class="page-shell">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate</p>
          <h1>Πολιτική Απορρήτου</h1>
        </div>
        <div class="header-actions">
          <a routerLink="/terms">Όροι Χρήσης</a>
        </div>
      </header>

      <section class="page-body">
        <p class="alert legal-draft" data-testid="legal-draft-notice">
          Προσχέδιο — προς επιβεβαίωση από νομικό σύμβουλο πριν την εμπορική διάθεση
        </p>

        <article class="panel legal-article">
          <p class="muted">Τελευταία ενημέρωση: [ημερομηνία έκδοσης]</p>
          <p>
            Η παρούσα πολιτική περιγράφει ποια δεδομένα συλλέγει η υπηρεσία RoomRate, για ποιον
            σκοπό τα επεξεργάζεται και ποια δικαιώματα έχετε επί αυτών. Σας παρακαλούμε να τη
            διαβάσετε πριν δημιουργήσετε λογαριασμό.
          </p>

          <h2>Υπεύθυνος επεξεργασίας</h2>
          <p>
            Υπεύθυνος επεξεργασίας των δεδομένων σας είναι η [Επωνυμία εταιρείας], με έδρα
            [διεύθυνση έδρας]. Για κάθε ζήτημα σχετικό με τα προσωπικά σας δεδομένα μπορείτε να
            επικοινωνείτε μαζί μας με τα στοιχεία της ενότητας «Επικοινωνία».
          </p>

          <h2>Δεδομένα που συλλέγουμε</h2>
          <ul>
            <li>
              <strong>Στοιχεία λογαριασμού.</strong> Τη διεύθυνση ηλεκτρονικού ταχυδρομείου με την
              οποία εγγράφεστε και το αναγνωριστικό χρήστη που αποδίδει ο πάροχος ταυτοποίησης.
              Τον κωδικό πρόσβασής σας δεν τον βλέπουμε ούτε τον αποθηκεύουμε εμείς.
            </li>
            <li>
              <strong>Στοιχεία του καταλύματός σας.</strong> Την επωνυμία, την τοποθεσία και τον
              σύνδεσμο της δημόσιας καταχώρισής σας στο Booking, όπως τα επιβεβαιώνετε εσείς κατά
              την αρχική ρύθμιση, καθώς και τον τύπο δωματίου που επιλέγετε ως σημείο αναφοράς.
            </li>
            <li>
              <strong>Ανταγωνιστές που παρακολουθείτε.</strong> Τη λίστα των καταλυμάτων που
              επιλέγετε να συγκρίνετε με το δικό σας.
            </li>
            <li>
              <strong>Παρατηρήσεις τιμών.</strong> Τιμές και διαθεσιμότητα που αντλούνται από
              δημόσια προσβάσιμες καταχωρίσεις του Booking για τα καταλύματα που παρακολουθείτε.
              Πρόκειται για επιχειρηματικά δεδομένα καταλυμάτων, όχι για δεδομένα επισκεπτών:
              η υπηρεσία δεν συλλέγει στοιχεία πελατών ή κρατήσεών σας.
            </li>
            <li>
              <strong>Ρυθμίσεις και ειδοποιήσεις.</strong> Τους κανόνες ειδοποίησης που ορίζετε,
              το πρόγραμμα των αυτόματων αναζητήσεων και το ιστορικό των ειδοποιήσεων που σας
              έχουν σταλεί.
            </li>
            <li>
              <strong>Τεχνικά αρχεία λειτουργίας.</strong> Αρχεία καταγραφής των αιτημάτων προς την
              υπηρεσία, για λόγους ασφάλειας, εντοπισμού σφαλμάτων και αποτροπής κατάχρησης.
            </li>
          </ul>

          <h2>Σκοπός και νομική βάση</h2>
          <ul>
            <li>
              <strong>Παροχή της υπηρεσίας</strong> (εκτέλεση σύμβασης): δημιουργία και συντήρηση
              του λογαριασμού σας, σύγκριση τιμών, εμφάνιση χάρτη και προτάσεων τιμολόγησης,
              αποστολή ειδοποιήσεων που έχετε ζητήσει.
            </li>
            <li>
              <strong>Ασφάλεια και ορθή λειτουργία</strong> (έννομο συμφέρον): προστασία από
              κατάχρηση, διάγνωση σφαλμάτων, τήρηση τεχνικών αρχείων.
            </li>
            <li>
              <strong>Επικοινωνία σχετική με την υπηρεσία</strong> (εκτέλεση σύμβασης): ενημερώσεις
              για αλλαγές, διακοπές ή ζητήματα του λογαριασμού σας.
            </li>
          </ul>
          <p>
            Δεν χρησιμοποιούμε τα δεδομένα σας για διαφημιστική στόχευση και δεν τα πωλούμε σε
            τρίτους.
          </p>

          <h2>Τρίτοι πάροχοι</h2>
          <p>
            Για να λειτουργήσει η υπηρεσία αξιοποιούμε τους παρακάτω παρόχους. Καθένας επεξεργάζεται
            μόνο όσα δεδομένα απαιτούνται για τον ρόλο του.
          </p>
          <ul data-testid="legal-processors">
            <li>
              <strong>Neon</strong> — φιλοξενία της βάσης δεδομένων, ταυτοποίηση χρηστών και
              διαχείριση συνεδρίας. Επεξεργάζεται τη διεύθυνση ηλεκτρονικού ταχυδρομείου, τα
              διαπιστευτήρια εισόδου σας και τα δεδομένα του λογαριασμού σας.
            </li>
            <li>
              <strong>Apify / Booking</strong> — άντληση δημόσια διαθέσιμων τιμών και στοιχείων
              καταχωρίσεων. Στέλνονται τα κριτήρια αναζήτησης (τοποθεσία, ημερομηνίες, τύπος
              δωματίου), όχι στοιχεία του λογαριασμού σας.
            </li>
            <li>
              <strong>Mapbox</strong> — απεικόνιση του χάρτη ανταγωνισμού. Ο πάροχος λαμβάνει τα
              αιτήματα φόρτωσης χαρτών από τον browser σας.
            </li>
            <li>
              <strong>Anthropic</strong> — μόνο όταν είναι ενεργό το σκέλος τεχνητής νοημοσύνης για
              τις προτάσεις τιμής. Αποστέλλονται συγκεντρωτικά στοιχεία αγοράς και τιμών, όχι η
              ταυτότητα ή τα στοιχεία επικοινωνίας σας. Όταν το σκέλος αυτό είναι
              απενεργοποιημένο, η υπηρεσία παράγει προτάσεις αποκλειστικά με στατιστικό υπολογισμό.
            </li>
            <li>
              <strong>Sentry</strong> — μόνο όταν είναι ενεργή η παρακολούθηση σφαλμάτων της
              υπηρεσίας. Λαμβάνει τεχνικά στοιχεία σφάλματος, όπως το μήνυμα, το σημείο του κώδικα
              και το περιβάλλον εκτέλεσης, τα οποία ενδέχεται να περιλαμβάνουν το αναγνωριστικό του
              λογαριασμού σας· δεν αποστέλλονται διαπιστευτήρια, ούτε το περιεχόμενο των τιμών και
              των καταχωρίσεών σας. Όταν η παρακολούθηση είναι απενεργοποιημένη, δεν αποστέλλεται
              κανένα δεδομένο.
            </li>
          </ul>

          <h2>Διατήρηση δεδομένων</h2>
          <p data-testid="legal-retention">
            Διατηρούμε τα δεδομένα του λογαριασμού και του καταλύματός σας για όσο διάστημα ο
            λογαριασμός σας παραμένει ενεργός. Οι ειδοποιήσεις, τα προσωρινά αρχεία των αναζητήσεων
            και το ιστορικό των προτάσεων τιμής διαγράφονται αυτόματα μετά από προκαθορισμένο
            διάστημα διατήρησης, το οποίο ορίζεται στις ρυθμίσεις της εγκατάστασης και
            γνωστοποιείται κατόπιν αιτήματός σας. Εάν διαγράψετε το κατάλυμά σας από τις ρυθμίσεις
            της εφαρμογής ή ζητήσετε τη διαγραφή του λογαριασμού σας στη διεύθυνση επικοινωνίας,
            διαγράφονται και τα συνδεδεμένα με αυτά δεδομένα, με εξαίρεση όσα οφείλουμε να
            τηρήσουμε για φορολογικούς ή νομικούς λόγους.
          </p>

          <h2>Τα δικαιώματά σας (GDPR)</h2>
          <p>
            Σύμφωνα με τον Γενικό Κανονισμό Προστασίας Δεδομένων (ΕΕ) 2016/679 έχετε τα εξής
            δικαιώματα:
          </p>
          <ul>
            <li><strong>Πρόσβαση</strong> — να ζητήσετε αντίγραφο των δεδομένων που σας αφορούν.</li>
            <li><strong>Διόρθωση</strong> — να ζητήσετε τη διόρθωση ανακριβών στοιχείων.</li>
            <li><strong>Διαγραφή</strong> — να ζητήσετε τη διαγραφή των δεδομένων σας.</li>
            <li>
              <strong>Φορητότητα</strong> — να λάβετε τα δεδομένα σας σε δομημένο, κοινώς
              χρησιμοποιούμενο και αναγνώσιμο από μηχανήματα μορφότυπο.
            </li>
            <li>
              <strong>Εναντίωση και περιορισμός</strong> — να αντιταχθείτε σε επεξεργασία που
              βασίζεται στο έννομο συμφέρον μας ή να ζητήσετε τον περιορισμό της.
            </li>
          </ul>
          <p>
            Απαντούμε σε κάθε αίτημα εντός ενός μηνός. Διατηρείτε επίσης το δικαίωμα υποβολής
            καταγγελίας στην Αρχή Προστασίας Δεδομένων Προσωπικού Χαρακτήρα.
          </p>

          <h2 id="cookies">Cookies και τοπική αποθήκευση</h2>
          <div data-testid="legal-cookies">
            <p>
              Η εφαρμογή δεν χρησιμοποιεί cookies παρακολούθησης, ούτε cookies τρίτων για
              διαφήμιση ή στατιστικά επισκεψιμότητας. Δεν εμφανίζουμε banner συγκατάθεσης επειδή
              δεν υπάρχει τέτοια συλλογή.
            </p>
            <p>
              Η συνεδρία εισόδου σας διατηρείται σε ένα απαραίτητο cookie σύνδεσης που ορίζει ο
              πάροχος ταυτοποίησης Neon. Επιπλέον χρησιμοποιούμε την τοπική αποθήκευση
              (localStorage) του browser σας για την πρόοδό σας στη ρύθμιση του καταλύματος, ώστε
              να μη χάνεται αν κλείσετε τη σελίδα. Τα δεδομένα αυτά παραμένουν στη συσκευή σας και
              διαγράφονται με την αποσύνδεσή σας ή με τον καθαρισμό των δεδομένων του browser.
            </p>
          </div>

          <h2>Επικοινωνία</h2>
          <p>
            Για την άσκηση των δικαιωμάτων σας ή για οποιοδήποτε ερώτημα σχετικό με την παρούσα
            πολιτική, απευθυνθείτε στην [Επωνυμία εταιρείας] στη διεύθυνση
            [ηλεκτρονική διεύθυνση επικοινωνίας].
          </p>
        </article>
      </section>
    </main>
  `,
  styles: [
    `
      /*
       * Component-scoped on purpose: styles.css is shared by every page and
       * these rules are long-form-prose ergonomics that no other page wants.
       */
      .legal-draft {
        font-weight: 700;
        margin: 0;
      }

      .legal-article {
        line-height: 1.65;
        max-width: 78ch;
      }

      .legal-article h2 {
        font-size: 17px;
        margin: 28px 0 8px;
      }

      .legal-article p {
        margin: 0 0 12px;
      }

      .legal-article ul {
        margin: 0 0 12px;
        padding-left: 20px;
      }

      .legal-article li {
        margin-bottom: 8px;
      }
    `,
  ],
})
export class PrivacyPageComponent {}
