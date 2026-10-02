import { Component } from "@angular/core";
import { RouterLink } from "@angular/router";

/**
 * Public terms of service — reachable WITHOUT a session (see app.routes.ts).
 *
 * A DRAFT, marked as such at the top. Three clauses here are deliberately
 * honest about Round 5 reality rather than aspirational:
 *
 *  - Booking data is described as INFORMATIONAL. The prices come from public
 *    listings through a third-party scraper on a schedule; they are a snapshot,
 *    not a live feed, and the service cannot warrant that a competitor's rate
 *    is still what it was when it was observed. Promising accuracy would be a
 *    promise the architecture cannot keep.
 *  - Billing says «θα οριστεί». There is no billing code in the product yet,
 *    so naming a price or a trial length here would be fiction.
 *  - Deletion separates the two things the product can actually do. The
 *    settings page deletes the PROPERTY (DELETE
 *    /api/v1/onboarding/owned-property/{id} — api/routers/onboarding.py); no
 *    account-deletion endpoint exists anywhere, so ACCOUNT deletion is a
 *    request to the contact address that a human carries out. The earlier
 *    wording offered both from the settings, which the app cannot honor.
 *
 * e2e/legal-pages.spec.ts pins both, so a future copy edit that quietly turns
 * either into a guarantee has to argue with a failing test first.
 *
 * No "@" character may appear in this template: Angular's block syntax claims
 * it, and an unknown block is a compile error.
 */
@Component({
  selector: "app-terms-page",
  standalone: true,
  imports: [RouterLink],
  template: `
    <main class="page-shell">
      <header class="map-header">
        <div>
          <p class="eyebrow">RoomRate</p>
          <h1>Όροι Χρήσης</h1>
        </div>
        <div class="header-actions">
          <a routerLink="/privacy">Πολιτική Απορρήτου</a>
        </div>
      </header>

      <section class="page-body">
        <p class="alert legal-draft" data-testid="legal-draft-notice">
          Προσχέδιο — προς επιβεβαίωση από νομικό σύμβουλο πριν την εμπορική διάθεση
        </p>

        <article class="panel legal-article">
          <p class="muted">Τελευταία ενημέρωση: [ημερομηνία έκδοσης]</p>
          <p>
            Οι παρόντες όροι διέπουν τη χρήση της υπηρεσίας RoomRate. Με τη δημιουργία λογαριασμού
            δηλώνετε ότι τους έχετε διαβάσει και ότι τους αποδέχεστε.
          </p>

          <h2>Αντικείμενο της υπηρεσίας</h2>
          <p>
            Η RoomRate είναι εργαλείο παρακολούθησης ανταγωνισμού και υποστήριξης τιμολόγησης για
            καταλύματα. Συγκεντρώνει δημόσια διαθέσιμες τιμές ανταγωνιστών, τις παρουσιάζει σε
            χάρτη και σε ιστορικά διαγράμματα και διατυπώνει προτάσεις τιμής. Οι προτάσεις αυτές
            είναι υποστηρικτικές: η απόφαση για την τιμή που θα ορίσετε παραμένει αποκλειστικά
            δική σας.
          </p>

          <h2>Λογαριασμός</h2>
          <ul>
            <li>
              Δηλώνετε ότι τα στοιχεία που καταχωρίζετε είναι ακριβή και ότι έχετε δικαίωμα να
              εκπροσωπείτε το κατάλυμα που καταχωρίζετε ως δικό σας.
            </li>
            <li>
              Είστε υπεύθυνοι για τη φύλαξη των διαπιστευτηρίων σας και για κάθε ενέργεια που
              γίνεται μέσω του λογαριασμού σας.
            </li>
            <li data-testid="legal-account-deletion">
              Μπορείτε να διαγράψετε το κατάλυμά σας οποτεδήποτε, μέσα από τις ρυθμίσεις της
              εφαρμογής, ή να ζητήσετε τη διαγραφή του λογαριασμού σας στη διεύθυνση επικοινωνίας
              της ενότητας «Επικοινωνία».
            </li>
          </ul>

          <h2>Αποδεκτή χρήση</h2>
          <p>Δεν επιτρέπεται να:</p>
          <ul>
            <li>
              αναπαράγετε, μεταπωλείτε ή διαθέτετε σε τρίτους τα δεδομένα και τις προτάσεις της
              υπηρεσίας, χωρίς προηγούμενη έγγραφη συμφωνία·
            </li>
            <li>
              επιχειρείτε αυτοματοποιημένη άντληση δεδομένων από την υπηρεσία, παράκαμψη των ορίων
              χρήσης ή πρόσβαση σε λογαριασμούς τρίτων·
            </li>
            <li>
              χρησιμοποιείτε την υπηρεσία κατά τρόπο που παραβιάζει την ισχύουσα νομοθεσία ή τους
              όρους των πηγών δεδομένων.
            </li>
          </ul>

          <h2>Δεδομένα τρίτων και πηγές τιμών</h2>
          <p>
            Οι τιμές και τα στοιχεία ανταγωνιστών προέρχονται από δημόσια προσβάσιμες καταχωρίσεις
            του Booking και αντλούνται μέσω τρίτου παρόχου. Παρέχονται αποκλειστικά για
            πληροφοριακούς σκοπούς και αποτυπώνουν τη στιγμή της άντλησης, όχι την τρέχουσα
            κατάσταση της αγοράς. Δεν εγγυόμαστε την ακρίβεια, την πληρότητα ή τη διαθεσιμότητά
            τους, ούτε ότι μια τιμή εξακολουθεί να ισχύει τη στιγμή που την βλέπετε. Σας
            συνιστούμε να επιβεβαιώνετε κρίσιμες πληροφορίες στην πηγή τους πριν λάβετε εμπορική
            απόφαση. Η RoomRate δεν συνδέεται με τη Booking.com και δεν ενεργεί για λογαριασμό της.
          </p>

          <h2>Χρέωση</h2>
          <p data-testid="legal-billing">
            Το μοντέλο χρέωσης της υπηρεσίας θα οριστεί και θα ανακοινωθεί πριν από την έναρξη
            οποιασδήποτε χρέωσης· μαζί του θα γνωστοποιηθούν οι τιμές των συνδρομητικών πακέτων
            και οι όροι πληρωμής. Έως τότε η πρόσβαση
            παρέχεται χωρίς οικονομική επιβάρυνση και χωρίς δέσμευση διάρκειας. Καμία χρέωση δεν
            πραγματοποιείται χωρίς τη ρητή προηγούμενη συγκατάθεσή σας.
          </p>

          <h2>Περιορισμός ευθύνης</h2>
          <p>
            Η υπηρεσία παρέχεται «ως έχει». Στον μέγιστο βαθμό που επιτρέπει η νομοθεσία, δεν
            φέρουμε ευθύνη για διαφυγόντα κέρδη, απώλεια εσόδων ή έμμεσες ζημίες που ενδέχεται να
            προκύψουν από τη χρήση της υπηρεσίας, από τυχόν ανακρίβεια των δεδομένων τρίτων ή από
            προσωρινή διακοπή της λειτουργίας της. Ουδεμία διάταξη των παρόντων όρων περιορίζει
            ευθύνη που δεν επιτρέπεται να περιοριστεί κατά το ελληνικό δίκαιο, ιδίως για δόλο ή
            βαριά αμέλεια.
          </p>

          <h2>Τροποποιήσεις</h2>
          <p>
            Μπορούμε να τροποποιήσουμε τους παρόντες όρους. Για ουσιώδεις μεταβολές θα σας
            ενημερώνουμε εγκαίρως μέσα από την εφαρμογή ή με μήνυμα ηλεκτρονικού ταχυδρομείου. Εάν
            δεν συμφωνείτε με τη νέα εκδοχή, μπορείτε να διακόψετε τη χρήση και να ζητήσετε τη
            διαγραφή του λογαριασμού σας.
          </p>

          <h2>Εφαρμοστέο δίκαιο και δωσιδικία</h2>
          <p>
            Οι παρόντες όροι διέπονται από το ελληνικό δίκαιο. Για κάθε διαφορά που τυχόν προκύψει
            αρμόδια ορίζονται τα δικαστήρια [πόλη έδρας], με την επιφύλαξη των δικαιωμάτων που
            αναγνωρίζει η νομοθεσία προστασίας καταναλωτή.
          </p>

          <h2>Επικοινωνία</h2>
          <p>
            Για ερωτήματα σχετικά με τους παρόντες όρους απευθυνθείτε στην [Επωνυμία εταιρείας],
            στη διεύθυνση [ηλεκτρονική διεύθυνση επικοινωνίας].
          </p>
        </article>
      </section>
    </main>
  `,
  styles: [
    `
      /* Mirrors privacy-page.component.ts: prose ergonomics, scoped here so
         styles.css stays the shared-layout file it is. */
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
export class TermsPageComponent {}
