from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "pdf"
PDF_PATH = OUTPUT_DIR / "roomrate_handoff_el_2026-05-03.pdf"
MD_PATH = OUTPUT_DIR / "roomrate_handoff_el_2026-05-03.md"

FONT_REGULAR = "RoomRateRegular"
FONT_BOLD = "RoomRateBold"
FONT_ITALIC = "RoomRateItalic"

NAVY = colors.HexColor("#102033")
TEAL = colors.HexColor("#0F766E")
MINT = colors.HexColor("#E6F4F1")
AMBER = colors.HexColor("#F59E0B")
AMBER_LIGHT = colors.HexColor("#FFF4D6")
BLUE_LIGHT = colors.HexColor("#EAF2FF")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5B667A")
LINE = colors.HexColor("#D9E2EC")
SOFT = colors.HexColor("#F6F8FB")
GREEN_LIGHT = colors.HexColor("#EAF7EA")


def register_fonts() -> None:
    """Register Windows fonts that support Greek text."""
    font_dir = Path("C:/Windows/Fonts")
    regular = font_dir / "arial.ttf"
    bold = font_dir / "arialbd.ttf"
    italic = font_dir / "ariali.ttf"
    if not regular.exists() or not bold.exists():
        raise RuntimeError("Arial fonts were not found in C:/Windows/Fonts.")
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
    if italic.exists():
        pdfmetrics.registerFont(TTFont(FONT_ITALIC, str(italic)))
    else:
        pdfmetrics.registerFont(TTFont(FONT_ITALIC, str(regular)))


class ColorBand(Flowable):
    """Horizontal rounded color band used as a section visual."""

    def __init__(self, width: float, height: float, color: colors.Color):
        super().__init__()
        self.width = width
        self.height = height
        self.color = color

    def draw(self) -> None:
        self.canv.setFillColor(self.color)
        self.canv.roundRect(0, 0, self.width, self.height, 6, stroke=0, fill=1)


class StatusCard(Flowable):
    """Compact metric/status card."""

    def __init__(self, title: str, value: str, note: str, bg: colors.Color = BLUE_LIGHT):
        super().__init__()
        self.title = title
        self.value = value
        self.note = note
        self.bg = bg
        self.width = 5.1 * cm
        self.height = 2.45 * cm

    def draw(self) -> None:
        self.canv.setFillColor(self.bg)
        self.canv.roundRect(0, 0, self.width, self.height, 8, stroke=0, fill=1)
        self.canv.setFillColor(NAVY)
        self.canv.setFont(FONT_BOLD, 8)
        self.canv.drawString(0.35 * cm, 1.78 * cm, self.title)
        self.canv.setFont(FONT_BOLD, 15)
        self.canv.drawString(0.35 * cm, 1.05 * cm, self.value)
        self.canv.setFillColor(MUTED)
        self.canv.setFont(FONT_REGULAR, 7.5)
        self.canv.drawString(0.35 * cm, 0.45 * cm, self.note)


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RoomRateTitle",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=25,
            leading=31,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "cover_subtitle": ParagraphStyle(
            "RoomRateCoverSubtitle",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=11,
            leading=16,
            textColor=colors.HexColor("#D9F3EF"),
        ),
        "h1": ParagraphStyle(
            "RoomRateH1",
            parent=base["Heading1"],
            fontName=FONT_BOLD,
            fontSize=17,
            leading=22,
            textColor=NAVY,
            spaceBefore=8,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "RoomRateH2",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=12.5,
            leading=16,
            textColor=TEAL,
            spaceBefore=8,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "RoomRateBody",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9.3,
            leading=13.2,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "RoomRateSmall",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "callout": ParagraphStyle(
            "RoomRateCallout",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=9.3,
            leading=13.5,
            textColor=NAVY,
        ),
        "code": ParagraphStyle(
            "RoomRateCode",
            parent=base["Code"],
            fontName=FONT_REGULAR,
            fontSize=7.6,
            leading=10.2,
            textColor=colors.HexColor("#233044"),
            backColor=colors.HexColor("#F1F5F9"),
            borderPadding=6,
            spaceBefore=4,
            spaceAfter=6,
        ),
        "table_header": ParagraphStyle(
            "RoomRateTableHeader",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=8,
            leading=10,
            textColor=colors.white,
        ),
        "table_cell": ParagraphStyle(
            "RoomRateTableCell",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=7.6,
            leading=9.8,
            textColor=INK,
        ),
    }


def p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def bullet_list(items: list[str], style: ParagraphStyle) -> ListFlowable:
    return ListFlowable(
        [ListItem(p(item, style), leftIndent=8) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=14,
        bulletFontName=FONT_BOLD,
        bulletColor=TEAL,
    )


def make_table(rows: list[list[str]], widths: list[float], style_map: dict[str, ParagraphStyle]) -> Table:
    table_rows = []
    for index, row in enumerate(rows):
        row_style = style_map["table_header"] if index == 0 else style_map["table_cell"]
        table_rows.append([p(cell, row_style) for cell in row])
    table = Table(table_rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def draw_header_footer(canvas, doc) -> None:
    canvas.saveState()
    page_width, page_height = A4
    if doc.page == 1:
        canvas.restoreState()
        return
    canvas.setFillColor(NAVY)
    canvas.rect(0, page_height - 1.2 * cm, page_width, 1.2 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_BOLD, 8.5)
    canvas.drawString(1.5 * cm, page_height - 0.74 * cm, "RoomRate - Project Handoff")
    canvas.setFont(FONT_REGULAR, 8)
    canvas.drawRightString(page_width - 1.5 * cm, page_height - 0.74 * cm, f"Σελίδα {doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(1.5 * cm, 1.25 * cm, page_width - 1.5 * cm, 1.25 * cm)
    canvas.setFillColor(MUTED)
    canvas.setFont(FONT_REGULAR, 7.5)
    canvas.drawString(1.5 * cm, 0.85 * cm, "Context report για παρουσίαση και συνέχιση νέου session")
    canvas.restoreState()


def cover(story: list, style_map: dict[str, ParagraphStyle], content_width: float) -> None:
    story.append(ColorBand(content_width, 4.7 * cm, NAVY))
    story.append(Spacer(1, -4.35 * cm))
    story.append(p("RoomRate based project", style_map["title"]))
    story.append(p("Παράδοση έργου & επόμενα βήματα", style_map["cover_subtitle"]))
    
    story.append(Spacer(1, 1.25 * cm))
    story.append(
        Table(
            [[
                StatusCard("Τρέχουσα βάση", "Tenant-ready DB", "normalized + scoped", MINT),
                StatusCard("Tests", "25 backend passed", "lint/build frontend passed", GREEN_LIGHT),
                StatusCard("Τελευταίο commit", "6defebe", "pushed στο origin/main", AMBER_LIGHT),
            ]],
            colWidths=[5.55 * cm, 5.55 * cm, 5.55 * cm],
        )
    )
    story.append(Spacer(1, 1.0 * cm))
    story.append(p("<b>Ημερομηνία:</b> 03/05/2026", style_map["body"]))
    story.append(p("<b>Project:</b> RoomRate personal SaaS MVP", style_map["body"]))
    story.append(p("<b>Στόχος:</b> revenue management / market intelligence για Booking.com competitor data.", style_map["body"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        make_table(
            [
                ["Τι να κρατήσουμε", "Σύντομη απάντηση"],
                ["Η βάση", "Έχει normalized schema και πλέον tenant/account scope."],
                ["Το API", "Διαβάζει normalized data με account filtering μέσω X-RoomRate-Account-ID."],
                ["Το επόμενο μεγάλο βήμα", "Login/onboarding + owned property setup + scrape job flow."],
            ],
            [4.1 * cm, 12.2 * cm],
            style_map,
        )
    )
    story.append(PageBreak())


def build_story(style_map: dict[str, ParagraphStyle]) -> list:
    content_width = A4[0] - 3 * cm
    story: list = []
    cover(story, style_map, content_width)

    story.append(p("1. Σύντομη σύνοψη", style_map["h1"]))
    story.append(
        p(
            "Το RoomRate έχει περάσει από απλό scraper/legacy table σε πιο παραγωγική αρχιτεκτονική: normalized PostgreSQL schema, API source switch, Mapbox frontend slice, agent-ready payloads και τώρα multi-tenant foundation. Αυτό σημαίνει ότι μπορούμε να υποστηρίξουμε διαφορετικούς πελάτες/accounts χωρίς να ανακατεύονται τα δεδομένα τους.",
            style_map["body"],
        )
    )
    story.append(
        p(
            "Δεν υπάρχει ακόμα πλήρες login UI ή onboarding flow. Υπάρχει όμως πλέον το κρίσιμο database/API υπόβαθρο για να χτιστεί σωστά: accounts, memberships, owned properties, scrape jobs και account-scoped reads.",
            style_map["body"],
        )
    )
    story.append(Spacer(1, 0.25 * cm))
    story.append(
        Table(
            [[
                StatusCard("DB rows", "57 latest rows", "στο default account", BLUE_LIGHT),
                StatusCard("Scrape runs", "8 scoped runs", "κανένα run χωρίς account", MINT),
                StatusCard("Tenant isolation", "verified", "άλλο account βλέπει 0 rows", GREEN_LIGHT),
            ]],
            colWidths=[5.55 * cm, 5.55 * cm, 5.55 * cm],
        )
    )

    story.append(p("2. Τι έχει ολοκληρωθεί", style_map["h1"]))
    story.append(
        bullet_list(
            [
                "<b>Normalized production schema:</b> οι νέοι πίνακες `roomrate_*` υπάρχουν και έχουν populated market data.",
                "<b>Legacy fallback:</b> το `room_rates` δεν διαγράφηκε, αλλά το API δουλεύει με `ROOMRATE_RATE_SOURCE=normalized`.",
                "<b>Scraper pipeline:</b> το πακέτο `scraper` γράφει στα normalized tables, έχει dry-run, account-id και date-aware record IDs.",
                "<b>Agent-ready payloads:</b> Smart Advisor και Amenities endpoints επιστρέφουν `data_quality`, `pricing_signals`, `amenity_signals`.",
                "<b>Frontend Mapbox slice:</b> υπάρχει Next.js frontend με proxy route ώστε το private API key να μένει server-side.",
                "<b>Multi-tenant base:</b> προστέθηκαν accounts, memberships, owned properties, scrape jobs και account_id στο scrape run.",
            ],
            style_map["body"],
        )
    )

    story.append(p("3. Αρχιτεκτονική βάσης δεδομένων", style_map["h1"]))
    story.append(
        p(
            "Η τωρινή κατεύθυνση είναι: τα competitor properties μένουν global/public market entities, ενώ τα scrape runs και τα αποτελέσματα που βλέπει ο χρήστης είναι scoped ανά account. Έτσι δύο χρήστες μπορούν να έχουν διαφορετικές περιοχές ή και ίδια περιοχή χωρίς να βλέπει ο ένας τα δεδομένα του άλλου.",
            style_map["body"],
        )
    )
    story.append(
        make_table(
            [
                ["Πίνακας / View", "Ρόλος", "Σημείωση"],
                ["roomrate_accounts", "Tenant account", "Ένας πελάτης/εταιρεία/operator."],
                ["roomrate_user_identities", "External auth user", "Έτοιμο για Supabase/Clerk/Auth0 subject."],
                ["roomrate_memberships", "User -> account role", "Υποστηρίζει πολλούς χρήστες ανά account."],
                ["roomrate_owned_properties", "Καταλύματα πελάτη", "Booking URL, city, lat/lng, confidence/source."],
                ["roomrate_scrape_jobs", "Αίτημα scrape από frontend", "Queued/running/completed/error state."],
                ["roomrate_scrape_runs", "Πραγματικό external scrape run", "Έχει πλέον account_id."],
                ["roomrate_latest_room_rates", "Read view για API", "Περιλαμβάνει account_id για filtering."],
            ],
            [4.2 * cm, 5.4 * cm, 6.7 * cm],
            style_map,
        )
    )

    story.append(p("4. Πώς δουλεύει πλέον το multi-user isolation", style_map["h1"]))
    story.append(
        p(
            "Στο MVP, το API εξακολουθεί να προστατεύεται από `X-API-Key`. Προστέθηκε όμως tenant context με το header `X-RoomRate-Account-ID`. Όταν λείπει, χρησιμοποιείται το default account `00000000-0000-0000-0000-000000000001` για backward compatibility.",
            style_map["body"],
        )
    )
    story.append(
        p(
            "Μελλοντικά, το header αυτό δεν πρέπει να είναι χειροκίνητο. Το frontend θα στέλνει JWT από Supabase/Clerk, το FastAPI θα το επαληθεύει, και μετά θα βρίσκει σε ποια accounts έχει membership ο χρήστης.",
            style_map["body"],
        )
    )
    story.append(
        p(
            "Ροή δεδομένων: User login -> AccountContext -> owned property -> scrape job -> scraper with account_id -> roomrate_scrape_runs.account_id -> API reads filtered by account_id.",
            style_map["code"],
        )
    )

    story.append(Spacer(1, 0.35 * cm))
    story.append(p("5. Scraping Flow για πελάτη", style_map["h1"]))
    story.append(
        p(
            "Ο χρήστης δεν πρέπει να πληκτρολογεί lat/lng χειροκίνητα. Το προτεινόμενο onboarding είναι να βάζει Booking.com URL ή όνομα + περιοχή. Το σύστημα βρίσκει metadata και του δείχνει confirmation με fallback σε map pin.",
            style_map["body"],
        )
    )
    story.append(
        make_table(
            [
                ["Βήμα", "Τι κάνει ο user", "Τι κάνει το σύστημα"],
                ["1", "Κάνει login", "Φτιάχνει/φορτώνει AccountContext."],
                ["2", "Προσθέτει κατάλυμα με Booking URL", "Εντοπίζει name, address, city, lat/lng, source confidence."],
                ["3", "Επιβεβαιώνει το κατάλυμα", "Γράφει `roomrate_owned_properties`."],
                ["4", "Επιλέγει dates/guests/rooms", "Δημιουργεί `roomrate_scrape_jobs`."],
                ["5", "Πατάει Run scrape", "Τρέχει `python -m scraper --account-id ...`."],
                ["6", "Βλέπει dashboard", "API επιστρέφει μόνο rows του account."],
            ],
            [1.2 * cm, 5.1 * cm, 10.0 * cm],
            style_map,
        )
    )

    story.append(p("6. Τρέχον API και commands", style_map["h1"]))
    story.append(
        p(
            "Βασικά endpoints που υπάρχουν τώρα:",
            style_map["body"],
        )
    )
    story.append(
        bullet_list(
            [
                "`GET /health` - public health check.",
                "`GET /api/v1/market/summary` - KPI summary.",
                "`GET /api/v1/competitors/` - grouped competitors.",
                "`GET /api/v1/maps/competitors` - Mapbox markers.",
                "`POST /api/v1/agents/price-recommendation` - hybrid price recommendation.",
            ],
            style_map["body"],
        )
    )
    story.append(
        p(
            "Run backend:<br/>cd C:\\vscode_code\\room_project2<br/>.\\.venv\\Scripts\\python.exe -m uvicorn api.main:app --reload --port 8000",
            style_map["code"],
        )
    )
    story.append(
        p(
            "Run tests:<br/>cd C:\\vscode_code\\room_project2<br/>.\\.venv\\Scripts\\python.exe -m pytest api\\tests -v",
            style_map["code"],
        )
    )
    story.append(
        p(
            "Run scraper for one account:<br/>.\\.venv\\Scripts\\python.exe booking_scraper_v3.py --account-id 00000000-0000-0000-0000-000000000001 --destination Faliraki --check-in 2026-06-15 --check-out 2026-06-20",
            style_map["code"],
        )
    )

    story.append(p("7. Verification που έγινε", style_map["h1"]))
    story.append(
        make_table(
            [
                ["Έλεγχος", "Αποτέλεσμα"],
                ["Backend tests", "25 passed"],
                ["Frontend lint", "npm.cmd run lint passed"],
                ["Frontend build", "npm.cmd run build passed"],
                ["Alembic current", "20260503_0002 (head)"],
                ["Default account rows", "57 rows στο roomrate_latest_room_rates"],
                ["Other account rows", "0 rows για test account 00000000-0000-0000-0000-000000000123"],
                ["Latest pushed commit", "6defebe Add tenant-scoped RoomRate data model"],
            ],
            [5.0 * cm, 11.3 * cm],
            style_map,
        )
    )

    story.append(p("8. Τι πρέπει να σκεφτούμε τώρα", style_map["h1"]))
    story.append(
        make_table(
            [
                ["Απόφαση", "Πρακτική πρόταση", "Γιατί έχει σημασία"],
                ["Auth provider", "Supabase Auth για MVP", "Δίνει hosted login, JWT, Postgres/RLS path."],
                ["Onboarding", "Booking URL first, map pin fallback", "Ο user δεν ψάχνει lat/lng μόνος του."],
                ["Scrape execution", "API creates scrape job, worker runs CLI", "Δεν μπλοκάρει το web request και κρατά ιστορικό."],
                ["Legacy writes", "Να γίνουν off by default μετά από 2-3 stable runs", "Μειώνει διπλή συντήρηση."],
                ["Frontend scope", "Πρώτα settings/onboarding, μετά dashboard polish", "Χωρίς account/property setup το dashboard δεν είναι πραγματικό SaaS."],
            ],
            [3.2 * cm, 6.1 * cm, 7.0 * cm],
            style_map,
        )
    )

    story.append(Spacer(1, 0.35 * cm))
    story.append(p("9. Προτεινόμενο πλάνο υλοποίησης", style_map["h1"]))
    story.append(
        bullet_list(
            [
                "<b>Step 1 - Auth choice:</b> κλείνουμε Supabase Auth ή Clerk. Recommendation: Supabase Auth για να συνδεθεί πιο φυσικά με Postgres/RLS αργότερα.",
                "<b>Step 2 - Account onboarding API:</b> endpoints για create account, membership και selected account context.",
                "<b>Step 3 - Owned property setup:</b> form όπου ο user βάζει Booking URL ή όνομα/περιοχή. Αποθήκευση σε `roomrate_owned_properties`.",
                "<b>Step 4 - Scrape job endpoint:</b> `POST /api/v1/scrape-jobs` που δημιουργεί job κάτω από account και γυρίζει job id.",
                "<b>Step 5 - Worker execution:</b> worker/CLI παίρνει job id και τρέχει scraper με `--account-id`.",
                "<b>Step 6 - Dashboard account switch:</b> frontend στέλνει το σωστό account id σε όλα τα proxy/API calls.",
                "<b>Step 7 - First advisor panel:</b> χρησιμοποιεί τα ήδη έτοιμα `pricing_signals` και `data_quality`.",
            ],
            style_map["body"],
        )
    )

    story.append(p("10. Γνωστοί περιορισμοί", style_map["h1"]))
    story.append(
        bullet_list(
            [
                "Δεν υπάρχει ακόμα πραγματικό user login/JWT verification.",
                "Το `X-RoomRate-Account-ID` είναι προσωρινό trusted header για internal MVP.",
                "Δεν υπάρχει ακόμα frontend form για owned property ή scrape job.",
                "Δεν υπάρχει ακόμα background worker/scheduler για scrape jobs.",
                "Το legacy `room_rates` παραμένει ως rollback fallback.",
                "Το `migration_preview.sql` είναι generated artifact και δεν πρέπει να γίνει commit.",
            ],
            style_map["body"],
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    story.append(
        p(
            "Προτεινόμενη άμεση προτεραιότητα: Supabase Auth decision + minimal onboarding screen που δημιουργεί account/property και μετά scrape job.",
            style_map["callout"],
        )
    )
    story.append(Spacer(1, 0.25 * cm))
    story.append(p("11. Agenda για την επόμενη συζήτηση", style_map["h1"]))
    story.append(
        make_table(
            [
                ["Θέμα", "Απόφαση που χρειάζεται"],
                ["Auth", "Supabase Auth ή Clerk για το πρώτο login flow."],
                ["Onboarding", "Booking URL first ή name+area first."],
                ["Scrape jobs", "Manual run button πρώτα ή scheduled jobs από την αρχή."],
                ["Dashboard", "Να προηγηθεί property setup πριν από επιπλέον KPI polish."],
            ],
            [5.0 * cm, 11.3 * cm],
            style_map,
        )
    )
    return story


def write_markdown() -> None:
    content = """# RoomRate - Παράδοση έργου & επόμενα βήματα

Ημερομηνία: 2026-05-03

## Τρέχουσα κατάσταση

- Το RoomRate έχει normalized PostgreSQL schema.
- Το API διαβάζει `ROOMRATE_RATE_SOURCE=normalized`.
- Υπάρχει multi-tenant foundation με `roomrate_accounts`, `roomrate_user_identities`, `roomrate_memberships`, `roomrate_owned_properties`, `roomrate_scrape_jobs`.
- Τα υπάρχοντα δεδομένα είναι scoped στο default account `00000000-0000-0000-0000-000000000001`.
- Το FastAPI δέχεται `X-RoomRate-Account-ID` και φιλτράρει normalized reads ανά account.
- Το scraper δέχεται `--account-id`.
- Το frontend proxy περνάει account id προς FastAPI.

## Verification

- `pytest api/tests -v` -> 25 passed.
- `npm.cmd run lint` -> passed.
- `npm.cmd run build` -> passed.
- Alembic current -> `20260503_0002`.
- Default account latest rows -> 57.
- Other account test rows -> 0.

## Επόμενα βήματα

1. Επιλογή auth provider: προτείνεται Supabase Auth.
2. Account onboarding API.
3. Owned property setup με Booking URL και map pin fallback.
4. Scrape job endpoint.
5. Background worker που τρέχει scraper με `--account-id`.
6. Dashboard account scope.
7. Smart Advisor panel.

## Context για νέο session

RoomRate project status as of 2026-05-03:
- Project is RoomRate personal SaaS MVP, not PlanAhead.
- Stack: FastAPI/Python/PostgreSQL/SQLAlchemy/Alembic, Next.js App Router/Tailwind/Mapbox.
- Database is normalized and tenant-ready. Current Alembic head: 20260503_0002.
- API reads normalized source by default: ROOMRATE_RATE_SOURCE=normalized.
- Tenant context exists through X-RoomRate-Account-ID with default account 00000000-0000-0000-0000-000000000001.
- Existing data: 57 latest rows under default account; other account test returns 0 rows.
- Scraper supports --account-id and writes account-scoped normalized runs.
- Frontend proxy passes X-RoomRate-Account-ID to FastAPI.
- Tests: pytest api/tests -v => 25 passed; frontend lint/build passed.
- Latest pushed commits: 929d99d normalized market schema/scraper; 6defebe tenant-scoped data model.
- Next goal: implement real login/onboarding, owned property setup, and scrape job flow.
"""
    MD_PATH.write_text(content, encoding="utf-8")


def build_pdf() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    register_fonts()
    style_map = styles()
    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.65 * cm,
        bottomMargin=1.55 * cm,
        title="RoomRate Project Handoff",
        author="RoomRate Engineering",
    )
    story = build_story(style_map)
    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    write_markdown()
    LOGGER.info("Wrote %s", PDF_PATH)
    LOGGER.info("Wrote %s", MD_PATH)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    build_pdf()


if __name__ == "__main__":
    main()
