from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "erd"
OUTPUT_PATH = OUTPUT_DIR / "roomrate_erd_detailed_el.png"

FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = FONT_DIR / "arial.ttf"
FONT_BOLD = FONT_DIR / "arialbd.ttf"

W, H = 3600, 2500
BG = "#F5F7FB"
NAVY = "#102033"
TEXT = "#172033"
MUTED = "#5B667A"
LINE = "#CAD5E3"
WHITE = "#FFFFFF"

TEAL = "#0F766E"
TEAL_DARK = "#0B5F59"
BLUE = "#2563EB"
BLUE_DARK = "#1D4ED8"
AMBER = "#D97706"
PURPLE = "#6D28D9"
GREEN = "#15803D"

SOFT_TEAL = "#E6F4F1"
SOFT_BLUE = "#EAF2FF"
SOFT_AMBER = "#FFF4D6"
SOFT_PURPLE = "#F1EAFF"
SOFT_GREEN = "#EAF7EA"


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


F_TITLE = font(FONT_BOLD, 64)
F_SUBTITLE = font(FONT_REGULAR, 30)
F_SECTION = font(FONT_BOLD, 32)
F_TABLE = font(FONT_BOLD, 28)
F_PURPOSE_TITLE = font(FONT_BOLD, 18)
F_PURPOSE = font(FONT_REGULAR, 18)
F_FIELD = font(FONT_REGULAR, 20)
F_FIELD_BOLD = font(FONT_BOLD, 20)
F_SMALL = font(FONT_REGULAR, 18)
F_SMALL_BOLD = font(FONT_BOLD, 18)


@dataclass(frozen=True)
class TableSpec:
    key: str
    title: str
    purpose: str
    fields: list[tuple[str, str]]
    xy: tuple[int, int]
    color: str
    soft: str
    width: int = 520


@dataclass
class Box:
    spec: TableSpec
    height: int

    @property
    def x(self) -> int:
        return self.spec.xy[0]

    @property
    def y(self) -> int:
        return self.spec.xy[1]

    @property
    def width(self) -> int:
        return self.spec.width

    @property
    def left(self) -> tuple[int, int]:
        return self.x, self.y + self.height // 2

    @property
    def right(self) -> tuple[int, int]:
        return self.x + self.width, self.y + self.height // 2

    @property
    def top(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y

    @property
    def bottom(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height


def wrap_text(d: ImageDraw.ImageDraw, text: str, font_obj: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if d.textlength(candidate, font=font_obj) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def measure_box(d: ImageDraw.ImageDraw, spec: TableSpec) -> int:
    purpose_lines = wrap_text(d, spec.purpose, F_PURPOSE, spec.width - 44)
    return 66 + 24 + len(purpose_lines) * 24 + 18 + len(spec.fields) * 28 + 22


def draw_table(d: ImageDraw.ImageDraw, box: Box) -> None:
    spec = box.spec
    x, y = spec.xy
    d.rounded_rectangle([x, y, x + spec.width, y + box.height], radius=18, fill=WHITE, outline=LINE, width=2)
    d.rounded_rectangle([x, y, x + spec.width, y + 58], radius=18, fill=spec.color)
    d.rectangle([x, y + 38, x + spec.width, y + 58], fill=spec.color)
    d.text((x + 22, y + 16), spec.title, font=F_TABLE, fill=WHITE)

    yy = y + 78
    d.text((x + 22, yy), "Γιατί υπάρχει:", font=F_PURPOSE_TITLE, fill=spec.color)
    yy += 25
    for line in wrap_text(d, spec.purpose, F_PURPOSE, spec.width - 44):
        d.text((x + 22, yy), line, font=F_PURPOSE, fill=TEXT)
        yy += 24

    yy += 10
    d.line([(x + 22, yy), (x + spec.width - 22, yy)], fill=LINE, width=1)
    yy += 16
    for name, kind in spec.fields:
        field_font = F_FIELD_BOLD if kind else F_FIELD
        d.text((x + 22, yy), name, font=field_font, fill=NAVY if kind else TEXT)
        if kind:
            tw = d.textlength(name, font=field_font)
            d.text((x + 30 + tw, yy), kind, font=F_SMALL, fill=spec.color)
        yy += 28


def ortho_points(start: tuple[int, int], end: tuple[int, int], via_x: int | None = None) -> list[tuple[int, int]]:
    sx, sy = start
    ex, ey = end
    mx = via_x if via_x is not None else (sx + ex) // 2
    return [(sx, sy), (mx, sy), (mx, ey), (ex, ey)]


def draw_line(d: ImageDraw.ImageDraw, points: list[tuple[int, int]], color: str, width: int = 4, dashed: bool = False) -> None:
    if not dashed:
        d.line(points, fill=color, width=width, joint="curve")
        return

    dash_len = 24
    gap_len = 14
    for start, end in zip(points, points[1:]):
        sx, sy = start
        ex, ey = end
        dx, dy = ex - sx, ey - sy
        length = (dx * dx + dy * dy) ** 0.5
        if not length:
            continue
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            segment_end = min(pos + dash_len, length)
            p1 = (sx + ux * pos, sy + uy * pos)
            p2 = (sx + ux * segment_end, sy + uy * segment_end)
            d.line([p1, p2], fill=color, width=width)
            pos += dash_len + gap_len


def draw_one_arrow(d: ImageDraw.ImageDraw, point: tuple[int, int], next_point: tuple[int, int], color: str, optional: bool = False) -> None:
    x, y = point
    nx, ny = next_point
    dx, dy = nx - x, ny - y
    length = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / length, dy / length
    px, py = -uy, ux

    tip = (x, y)
    base = (x + ux * 28, y + uy * 28)
    left = (base[0] + px * 13, base[1] + py * 13)
    right = (base[0] - px * 13, base[1] - py * 13)
    fill = WHITE if optional else color
    d.polygon([tip, left, right], fill=fill, outline=color)
    d.line([tip, left], fill=color, width=3)
    d.line([tip, right], fill=color, width=3)


def draw_relationship(
    d: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str,
    label: str,
    label_xy: tuple[int, int],
    optional_one: bool = False,
    via_x: int | None = None,
) -> None:
    points = ortho_points(start, end, via_x)
    draw_line(d, points, color, width=4, dashed=optional_one)
    draw_one_arrow(d, start, points[1], color, optional=optional_one)
    draw_label(d, label, label_xy, color)


def draw_label(d: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], fill: str) -> None:
    x, y = xy
    width = int(d.textlength(text, font=F_SMALL_BOLD)) + 26
    d.rounded_rectangle([x - 8, y - 6, x + width, y + 28], radius=12, fill=WHITE, outline=fill, width=2)
    d.text((x + 5, y), text, font=F_SMALL_BOLD, fill=fill)


def draw_badge(d: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], fill: str) -> None:
    x, y = xy
    tw = int(d.textlength(text, font=F_SMALL_BOLD))
    d.rounded_rectangle([x, y, x + tw + 30, y + 38], radius=19, fill=fill)
    d.text((x + 15, y + 9), text, font=F_SMALL_BOLD, fill=NAVY)


def draw_legend(d: ImageDraw.ImageDraw) -> None:
    x, y = 2460, 76
    d.rounded_rectangle([x, y, x + 1020, y + 172], radius=22, fill="#16283D", outline="#2A3F57", width=2)
    d.text((x + 28, y + 22), "Legend σχέσεων", font=F_TABLE, fill=WHITE)

    y1 = y + 78
    d.line([(x + 35, y1), (x + 155, y1)], fill=WHITE, width=4)
    draw_one_arrow(d, (x + 35, y1), (x + 155, y1), WHITE)
    d.text((x + 190, y1 - 13), "Βελάκι = πλευρά του 1 / parent record", font=F_SMALL, fill="#E8EEF7")
    d.text((x + 560, y1 - 13), "Σκέτη άκρη = πολλά child records", font=F_SMALL, fill="#E8EEF7")

    y2 = y + 122
    draw_line(d, [(x + 35, y2), (x + 155, y2)], WHITE, dashed=True)
    draw_one_arrow(d, (x + 35, y2), (x + 155, y2), WHITE, optional=True)
    d.text((x + 190, y2 - 13), "Διακεκομμένη = optional FK", font=F_SMALL, fill="#E8EEF7")
    d.text((x + 560, y2 - 13), "PK/FK/UK = primary/foreign/unique key", font=F_SMALL, fill="#E8EEF7")


def specs() -> list[TableSpec]:
    return [
        TableSpec(
            "accounts",
            "roomrate_accounts",
            "Tenant account: ο πελάτης/operator. Όλα τα jobs, properties και dashboard reads φιλτράρονται με account_id.",
            [("id", "PK"), ("slug", "UK"), ("display_name", ""), ("status", "")],
            (80, 360),
            TEAL,
            SOFT_TEAL,
        ),
        TableSpec(
            "users",
            "roomrate_user_identities",
            "Μελλοντική ταυτότητα login από Supabase/Clerk/Auth0. Κρατά auth provider και subject, όχι business data.",
            [("id", "PK"), ("auth_provider + auth_subject", "UK"), ("email", ""), ("display_name", ""), ("is_active", "")],
            (80, 725),
            TEAL,
            SOFT_TEAL,
        ),
        TableSpec(
            "memberships",
            "roomrate_memberships",
            "Συνδέει users με accounts και ρόλους. Επιτρέπει πολλά accounts ανά user και πολλούς users ανά account.",
            [("id", "PK"), ("account_id", "FK"), ("user_id", "FK"), ("role", "")],
            (700, 540),
            TEAL,
            SOFT_TEAL,
        ),
        TableSpec(
            "owned",
            "roomrate_owned_properties",
            "Τα καταλύματα του χρήστη. Εδώ μπαίνουν Booking URL, πόλη και lat/lng για onboarding και scrape scope.",
            [("id", "PK"), ("account_id", "FK"), ("matched_property_id", "FK?"), ("display_name", ""), ("booking_url", ""), ("city / country", ""), ("latitude / longitude", "")],
            (80, 1110),
            TEAL,
            SOFT_TEAL,
        ),
        TableSpec(
            "jobs",
            "roomrate_scrape_jobs",
            "Αίτημα scrape από frontend. Κρατά τι ζήτησε ο χρήστης, status και timestamps για polling.",
            [("id", "PK"), ("account_id", "FK"), ("owned_property_id", "FK?"), ("destination", ""), ("check_in / check_out", ""), ("adults / children / rooms", ""), ("status", "")],
            (700, 1110),
            AMBER,
            SOFT_AMBER,
        ),
        TableSpec(
            "runs",
            "roomrate_scrape_runs",
            "Πραγματική εκτέλεση scraper. Είναι account-scoped, συνδέεται με job και κρατά occupancy για σωστές συγκρίσεις.",
            [("id", "PK"), ("account_id", "FK"), ("scrape_job_id", "FK?"), ("provider + source_run_key", "UK"), ("destination", ""), ("check_in / check_out", ""), ("adults / children / rooms", ""), ("status", ""), ("raw_metadata", "")],
            (1350, 540),
            BLUE,
            SOFT_BLUE,
            width=570,
        ),
        TableSpec(
            "properties",
            "roomrate_properties",
            "Global canonical competitor properties από Booking.com. Δεν ανήκουν σε έναν πελάτη, επαναχρησιμοποιούνται σε πολλά runs.",
            [("id", "PK"), ("provider + source_property_key", "UK"), ("canonical_name", ""), ("display_name", ""), ("city / country", ""), ("address", ""), ("latitude / longitude", ""), ("stars", "")],
            (1350, 1110),
            BLUE,
            SOFT_BLUE,
            width=570,
        ),
        TableSpec(
            "observations",
            "roomrate_rate_observations",
            "Η κατάσταση ενός property μέσα σε ένα scrape run: reviews, min/max price και rooms-left signal.",
            [("id", "PK"), ("scrape_run_id", "FK"), ("property_id", "FK"), ("observed_at", ""), ("review_score / count", ""), ("rooms_left_min", ""), ("price_min / max", "")],
            (2070, 620),
            BLUE,
            SOFT_BLUE,
            width=560,
        ),
        TableSpec(
            "packages",
            "roomrate_room_packages",
            "Room/package offers κάτω από observation. Εδώ κρατάμε δωμάτιο, meal plan, cancellation, τιμές και rooms_left.",
            [("id", "PK"), ("rate_observation_id", "FK"), ("source_record_id", "UK"), ("room_type", ""), ("meals / cancellation", ""), ("price_per_night_eur", ""), ("price_total_eur", ""), ("rooms_left", "")],
            (2070, 1120),
            BLUE,
            SOFT_BLUE,
            width=560,
        ),
        TableSpec(
            "amenities",
            "roomrate_amenities",
            "Dictionary καθαρών amenities. Deduplication για WiFi/wifi/παρόμοιες τιμές μέσω normalized_name.",
            [("id", "PK"), ("name", ""), ("normalized_name", "UK"), ("created_at / updated_at", "")],
            (2820, 440),
            PURPLE,
            SOFT_PURPLE,
            width=560,
        ),
        TableSpec(
            "bridge",
            "roomrate_property_amenities",
            "Many-to-many bridge ανάμεσα σε properties και amenities. Ένα property έχει πολλά amenities και αντίστροφα.",
            [("property_id", "PK/FK"), ("amenity_id", "PK/FK"), ("created_at / updated_at", "")],
            (2820, 780),
            PURPLE,
            SOFT_PURPLE,
            width=560,
        ),
        TableSpec(
            "raw",
            "roomrate_raw_ingestion_events",
            "Raw payload audit/replay. Κρατά hash για idempotency και βοηθά debugging όταν αλλάξει ο scraper.",
            [("id", "PK"), ("scrape_run_id", "FK?"), ("source / source_run_id", ""), ("payload_hash", "UK"), ("captured_at", ""), ("payload", "JSONB")],
            (2820, 1120),
            PURPLE,
            SOFT_PURPLE,
            width=560,
        ),
    ]


def draw_views(d: ImageDraw.ImageDraw) -> None:
    d.rounded_rectangle([80, 1860, 3480, 2298], radius=26, fill=WHITE, outline=LINE, width=2)
    d.text((120, 1900), "API Views και καθαρός οδηγός σχέσεων", font=F_SECTION, fill=NAVY)

    view_specs = [
        (
            "roomrate_latest_room_rates",
            "Κύριο read view για dashboard, competitors και agents. Δείχνει latest completed run ανά account/destination/dates/occupancy.",
            (125, 1975),
            GREEN,
            SOFT_GREEN,
        ),
        (
            "roomrate_competitor_markers",
            "Έτοιμο view για Mapbox marker candidates ανά account/property/date. Κρατά ένα marker ανά property.",
            (1240, 1975),
            GREEN,
            SOFT_GREEN,
        ),
        (
            "Legacy fallback",
            "room_rates και scout_cache μένουν προσωρινά για rollback/σύγκριση. Δεν είναι το προτεινόμενο SaaS read model.",
            (2355, 1975),
            AMBER,
            SOFT_AMBER,
        ),
    ]
    for title, purpose, (x, y), color, soft in view_specs:
        d.rounded_rectangle([x, y, x + 1010, y + 145], radius=18, fill=soft, outline=color, width=2)
        d.text((x + 28, y + 22), title, font=F_TABLE, fill=color)
        yy = y + 65
        for line_text in wrap_text(d, purpose, F_FIELD, 940):
            d.text((x + 28, yy), line_text, font=F_FIELD, fill=TEXT)
            yy += 28

    d.rounded_rectangle([125, 2170, 3440, 2268], radius=18, fill="#EEF2F7", outline=LINE, width=1)
    d.text((155, 2188), "Οδηγός R σχέσεων: το βελάκι δείχνει την πλευρά του 1, η άλλη άκρη είναι τα πολλά.", font=F_FIELD_BOLD, fill=NAVY)
    relationships = [
        "R1 account -> memberships",
        "R2 user -> memberships",
        "R3 account -> owned_properties",
        "R4 owned_property -> scrape_jobs",
        "R5 scrape_job -> scrape_runs",
        "R6 account -> scrape_runs",
        "R7 scrape_run -> observations",
        "R8 property -> observations",
        "R9 observation -> room_packages",
        "R10 property -> property_amenities",
        "R11 amenity -> property_amenities",
        "R12 scrape_run -> raw_events",
    ]
    for index, text in enumerate(relationships):
        col = index % 4
        row = index // 4
        d.text((155 + col * 810, 2222 + row * 28), text, font=F_SMALL, fill=TEXT)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([70, 55, W - 70, 255], radius=34, fill=NAVY)
    d.text((110, 92), "RoomRate ERD - Αναλυτική Βάση Δεδομένων", font=F_TITLE, fill=WHITE)
    d.text(
        (114, 166),
        "Normalized SaaS schema + market intelligence | Alembic head: 20260510_0003 | Generated: 2026-05-16",
        font=F_SUBTITLE,
        fill="#D9F3EF",
    )
    draw_badge(d, "Tenant / Users", (1260, 105), SOFT_TEAL)
    draw_badge(d, "Scrape flow", (1495, 105), SOFT_AMBER)
    draw_badge(d, "Market data", (1698, 105), SOFT_BLUE)
    draw_badge(d, "Amenities / Raw", (1920, 105), SOFT_PURPLE)
    draw_legend(d)

    d.text((80, 300), "1. Multi-tenant / SaaS layer", font=F_SECTION, fill=TEAL_DARK)
    d.text((700, 300), "2. Scrape job flow", font=F_SECTION, fill=AMBER)
    d.text((1350, 300), "3. Market intelligence layer", font=F_SECTION, fill=BLUE_DARK)
    d.text((2820, 300), "4. Amenities & raw payloads", font=F_SECTION, fill=PURPLE)

    table_specs = specs()
    boxes = {spec.key: Box(spec, measure_box(d, spec)) for spec in table_specs}

    # Draw relationships first so table cards stay readable above lines.
    draw_relationship(
        d,
        boxes["accounts"].right,
        boxes["memberships"].left,
        TEAL_DARK,
        "R1",
        (520, 475),
    )
    draw_relationship(
        d,
        boxes["users"].right,
        (boxes["memberships"].left[0], boxes["memberships"].left[1] + 70),
        TEAL_DARK,
        "R2",
        (520, 760),
    )
    draw_relationship(
        d,
        boxes["accounts"].bottom,
        boxes["owned"].top,
        TEAL_DARK,
        "R3",
        (250, 940),
    )
    draw_relationship(
        d,
        boxes["owned"].right,
        boxes["jobs"].left,
        AMBER,
        "R4",
        (510, 1290),
        optional_one=True,
    )
    draw_relationship(
        d,
        boxes["jobs"].right,
        (boxes["runs"].left[0], boxes["runs"].left[1] + 110),
        AMBER,
        "R5",
        (1080, 940),
        optional_one=True,
    )
    draw_relationship(
        d,
        boxes["accounts"].right,
        boxes["runs"].left,
        BLUE,
        "R6",
        (1010, 465),
    )
    draw_relationship(
        d,
        boxes["runs"].right,
        boxes["observations"].left,
        BLUE,
        "R7",
        (1875, 565),
    )
    draw_relationship(
        d,
        boxes["properties"].right,
        (boxes["observations"].left[0], boxes["observations"].left[1] + 95),
        BLUE,
        "R8",
        (1875, 1050),
    )
    draw_relationship(
        d,
        boxes["observations"].bottom,
        boxes["packages"].top,
        BLUE,
        "R9",
        (2310, 1010),
    )
    draw_relationship(
        d,
        boxes["properties"].right,
        boxes["bridge"].left,
        PURPLE,
        "R10",
        (2575, 1035),
    )
    draw_relationship(
        d,
        boxes["amenities"].bottom,
        boxes["bridge"].top,
        PURPLE,
        "R11",
        (3030, 700),
    )
    draw_relationship(
        d,
        boxes["runs"].right,
        boxes["raw"].left,
        PURPLE,
        "R12",
        (2450, 500),
        optional_one=True,
    )

    for box in boxes.values():
        draw_table(d, box)

    draw_views(d)

    img.save(OUTPUT_PATH, quality=95)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
