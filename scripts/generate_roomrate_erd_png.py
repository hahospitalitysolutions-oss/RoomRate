from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "erd"
OUTPUT_PATH = OUTPUT_DIR / "roomrate_erd_el.png"
OUTPUT_CLEAN_PATH = OUTPUT_DIR / "roomrate_erd_clean_el.png"

FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = FONT_DIR / "arial.ttf"
FONT_BOLD = FONT_DIR / "arialbd.ttf"

W, H = 2600, 1700
BG = "#F6F8FB"
NAVY = "#102033"
TEAL = "#0F766E"
TEAL_DARK = "#0B5F59"
BLUE = "#2563EB"
AMBER = "#D97706"
PURPLE = "#6D28D9"
GREEN = "#15803D"
TEXT = "#172033"
MUTED = "#5B667A"
LINE = "#CBD5E1"
WHITE = "#FFFFFF"
SOFT_TEAL = "#E6F4F1"
SOFT_BLUE = "#EAF2FF"
SOFT_AMBER = "#FFF4D6"
SOFT_PURPLE = "#F1EAFF"
SOFT_GREEN = "#EAF7EA"


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


F_TITLE = font(FONT_BOLD, 54)
F_SUBTITLE = font(FONT_REGULAR, 26)
F_SECTION = font(FONT_BOLD, 26)
F_TABLE = font(FONT_BOLD, 24)
F_FIELD = font(FONT_REGULAR, 19)
F_FIELD_BOLD = font(FONT_BOLD, 19)
F_SMALL = font(FONT_REGULAR, 17)


class TableBox:
    def __init__(
        self,
        key: str,
        title: str,
        fields: list[tuple[str, str]],
        xy: tuple[int, int],
        color: str,
        fill: str,
        width: int = 455,
    ):
        self.key = key
        self.title = title
        self.fields = fields
        self.x, self.y = xy
        self.width = width
        self.color = color
        self.fill = fill
        self.row_h = 29
        self.header_h = 52
        self.pad = 18
        self.height = self.header_h + self.pad + len(fields) * self.row_h + self.pad

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

    def draw(self, d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle(
            [self.x, self.y, self.x + self.width, self.y + self.height],
            radius=18,
            fill=WHITE,
            outline=LINE,
            width=2,
        )
        d.rounded_rectangle(
            [self.x, self.y, self.x + self.width, self.y + self.header_h],
            radius=18,
            fill=self.color,
        )
        d.rectangle(
            [self.x, self.y + self.header_h - 18, self.x + self.width, self.y + self.header_h],
            fill=self.color,
        )
        d.text((self.x + self.pad, self.y + 14), self.title, font=F_TABLE, fill=WHITE)
        yy = self.y + self.header_h + self.pad
        for name, kind in self.fields:
            marker = ""
            field_font = F_FIELD
            field_fill = TEXT
            if kind:
                marker = f" {kind}"
                field_font = F_FIELD_BOLD
                field_fill = NAVY
            d.text((self.x + self.pad, yy), name, font=field_font, fill=field_fill)
            if marker:
                tw = d.textlength(name, font=field_font)
                d.text((self.x + self.pad + tw + 6, yy), marker, font=F_SMALL, fill=self.color)
            yy += self.row_h


def line(d: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str = NAVY) -> None:
    sx, sy = start
    ex, ey = end
    mid_x = (sx + ex) // 2
    d.line([(sx, sy), (mid_x, sy), (mid_x, ey), (ex, ey)], fill=color, width=4)
    r = 6
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=color)


def label(d: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], fill: str = MUTED) -> None:
    x, y = xy
    d.rounded_rectangle([x - 8, y - 5, x + int(d.textlength(text, font=F_SMALL)) + 8, y + 23], radius=8, fill=BG)
    d.text((x, y), text, font=F_SMALL, fill=fill)


def draw_badge(d: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], fill: str) -> None:
    x, y = xy
    tw = int(d.textlength(text, font=F_SMALL))
    d.rounded_rectangle([x, y, x + tw + 26, y + 34], radius=17, fill=fill)
    d.text((x + 13, y + 8), text, font=F_SMALL, fill=NAVY)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([70, 55, W - 70, 190], radius=28, fill=NAVY)
    d.text((105, 82), "RoomRate ERD - Βάση Δεδομένων", font=F_TITLE, fill=WHITE)
    d.text(
        (108, 146),
        "Normalized schema + multi-tenant layer | Alembic head: 20260510_0003",
        font=F_SUBTITLE,
        fill="#D9F3EF",
    )
    draw_badge(d, "Tenant/SaaS", (1130, 113), SOFT_TEAL)
    draw_badge(d, "Market Intelligence", (1290, 113), SOFT_BLUE)
    draw_badge(d, "Legacy fallback", (1538, 113), SOFT_AMBER)

    d.text((90, 235), "1. Multi-tenant / SaaS layer", font=F_SECTION, fill=TEAL_DARK)
    d.text((925, 235), "2. Market intelligence layer", font=F_SECTION, fill=BLUE)
    d.text((1980, 235), "3. Amenities, raw data & views", font=F_SECTION, fill=PURPLE)

    tables = {
        "accounts": TableBox(
            "accounts",
            "roomrate_accounts",
            [
                ("id", "PK"),
                ("slug", "UK"),
                ("display_name", ""),
                ("status", ""),
                ("created_at / updated_at", ""),
            ],
            (90, 285),
            TEAL,
            SOFT_TEAL,
        ),
        "users": TableBox(
            "users",
            "roomrate_user_identities",
            [
                ("id", "PK"),
                ("auth_provider + subject", "UK"),
                ("email", ""),
                ("display_name", ""),
                ("is_active", ""),
            ],
            (90, 555),
            TEAL,
            SOFT_TEAL,
        ),
        "memberships": TableBox(
            "memberships",
            "roomrate_memberships",
            [
                ("id", "PK"),
                ("account_id", "FK"),
                ("user_id", "FK"),
                ("role", ""),
                ("created_at / updated_at", ""),
            ],
            (590, 420),
            TEAL,
            SOFT_TEAL,
        ),
        "owned": TableBox(
            "owned",
            "roomrate_owned_properties",
            [
                ("id", "PK"),
                ("account_id", "FK"),
                ("matched_property_id", "FK"),
                ("display_name", ""),
                ("booking_url", ""),
                ("city / country", ""),
                ("latitude / longitude", ""),
                ("location_source", ""),
                ("location_confidence", ""),
            ],
            (90, 860),
            TEAL,
            SOFT_TEAL,
        ),
        "jobs": TableBox(
            "jobs",
            "roomrate_scrape_jobs",
            [
                ("id", "PK"),
                ("account_id", "FK"),
                ("owned_property_id", "FK"),
                ("destination", ""),
                ("check_in / check_out", ""),
                ("adults / children / rooms", ""),
                ("status", ""),
                ("requested/started/finished", ""),
            ],
            (590, 860),
            AMBER,
            SOFT_AMBER,
        ),
        "runs": TableBox(
            "runs",
            "roomrate_scrape_runs",
            [
                ("id", "PK"),
                ("account_id", "FK"),
                ("scrape_job_id", "FK"),
                ("provider + source_run_key", "UK"),
                ("destination", ""),
                ("check_in / check_out", ""),
                ("nights / guests", ""),
                ("adults / children / rooms", ""),
                ("status", ""),
                ("raw_metadata", ""),
            ],
            (1100, 410),
            BLUE,
            SOFT_BLUE,
        ),
        "properties": TableBox(
            "properties",
            "roomrate_properties",
            [
                ("id", "PK"),
                ("provider + source_key", "UK"),
                ("canonical_name", ""),
                ("display_name", ""),
                ("city / country", ""),
                ("address", ""),
                ("latitude / longitude", ""),
                ("stars", ""),
            ],
            (1100, 860),
            BLUE,
            SOFT_BLUE,
        ),
        "observations": TableBox(
            "observations",
            "roomrate_rate_observations",
            [
                ("id", "PK"),
                ("scrape_run_id", "FK"),
                ("property_id", "FK"),
                ("observed_at", ""),
                ("review_score / count", ""),
                ("rooms_left_min", ""),
                ("price_min / max", ""),
            ],
            (1605, 600),
            BLUE,
            SOFT_BLUE,
        ),
        "packages": TableBox(
            "packages",
            "roomrate_room_packages",
            [
                ("id", "PK"),
                ("rate_observation_id", "FK"),
                ("source_record_id", "UK"),
                ("room_type", ""),
                ("meals / cancellation", ""),
                ("price_per_night", ""),
                ("price_total", ""),
                ("rooms_left", ""),
            ],
            (1605, 1035),
            BLUE,
            SOFT_BLUE,
        ),
        "amenities": TableBox(
            "amenities",
            "roomrate_amenities",
            [
                ("id", "PK"),
                ("name", ""),
                ("normalized_name", "UK"),
                ("created_at / updated_at", ""),
            ],
            (2110, 410),
            PURPLE,
            SOFT_PURPLE,
        ),
        "bridge": TableBox(
            "bridge",
            "property_amenities",
            [
                ("property_id", "PK/FK"),
                ("amenity_id", "PK/FK"),
                ("created_at / updated_at", ""),
            ],
            (2110, 690),
            PURPLE,
            SOFT_PURPLE,
        ),
        "raw": TableBox(
            "raw",
            "raw_ingestion_events",
            [
                ("id", "PK"),
                ("scrape_run_id", "FK"),
                ("source_run_id", ""),
                ("payload_hash", "UK"),
                ("captured_at", ""),
                ("payload jsonb", ""),
            ],
            (2110, 940),
            PURPLE,
            SOFT_PURPLE,
        ),
    }

    for box in tables.values():
        box.draw(d)

    # Tenant relationships
    line(d, tables["accounts"].right, tables["memberships"].left, TEAL_DARK)
    label(d, "1 account -> many memberships", (380, 380), TEAL_DARK)
    line(d, tables["users"].right, (tables["memberships"].left[0], tables["memberships"].left[1] + 65), TEAL_DARK)
    label(d, "1 user -> many memberships", (382, 655), TEAL_DARK)
    line(d, tables["accounts"].bottom, tables["owned"].top, TEAL_DARK)
    label(d, "account owns properties", (160, 750), TEAL_DARK)
    line(d, tables["accounts"].right, (tables["jobs"].left[0], tables["jobs"].left[1] - 60), AMBER)
    label(d, "account requests scrape jobs", (460, 830), AMBER)
    line(d, tables["owned"].right, tables["jobs"].left, AMBER)
    label(d, "owned property configures job", (372, 1070), AMBER)
    line(d, tables["jobs"].right, (tables["runs"].left[0], tables["runs"].left[1] + 95), AMBER)
    label(d, "worker creates account-scoped run", (880, 720), AMBER)

    # Market relationships
    line(d, tables["accounts"].right, tables["runs"].left, BLUE)
    label(d, "account scopes runs", (790, 360), BLUE)
    line(d, tables["runs"].right, tables["observations"].left, BLUE)
    label(d, "run contains observations", (1390, 560), BLUE)
    line(d, tables["properties"].right, (tables["observations"].left[0], tables["observations"].left[1] + 70), BLUE)
    label(d, "global property observed", (1395, 915), BLUE)
    line(d, tables["observations"].bottom, tables["packages"].top, BLUE)
    label(d, "observation has packages", (1698, 938), BLUE)

    # Amenity/raw relationships
    line(d, tables["properties"].right, tables["bridge"].left, PURPLE)
    label(d, "property has amenities", (1870, 820), PURPLE)
    line(d, tables["amenities"].bottom, tables["bridge"].top, PURPLE)
    label(d, "amenity bridge", (2195, 625), PURPLE)
    line(d, tables["runs"].right, tables["raw"].left, PURPLE)
    label(d, "raw payloads per run", (1840, 510), PURPLE)

    # Views
    d.rounded_rectangle([80, 1370, 2520, 1595], radius=22, fill=WHITE, outline=LINE, width=2)
    d.text((112, 1402), "Views που χρησιμοποιεί το API", font=F_SECTION, fill=NAVY)
    d.rounded_rectangle([112, 1460, 825, 1548], radius=16, fill=SOFT_GREEN, outline="#B7E0B7", width=2)
    d.text((145, 1482), "roomrate_latest_room_rates", font=F_TABLE, fill=GREEN)
    d.text((145, 1520), "Compatibility view με account_id για market / competitors / agents.", font=F_SMALL, fill=TEXT)
    d.rounded_rectangle([945, 1460, 1658, 1548], radius=16, fill=SOFT_GREEN, outline="#B7E0B7", width=2)
    d.text((978, 1482), "roomrate_competitor_markers", font=F_TABLE, fill=GREEN)
    d.text((978, 1520), "Mapbox marker candidates ανά account/property/date.", font=F_SMALL, fill=TEXT)
    d.rounded_rectangle([1778, 1460, 2490, 1548], radius=16, fill=SOFT_AMBER, outline="#F4D27A", width=2)
    d.text((1811, 1482), "Legacy fallback", font=F_TABLE, fill=AMBER)
    d.text((1811, 1520), "room_rates και scout_cache μένουν προσωρινά για rollback.", font=F_SMALL, fill=TEXT)

    d.text(
        (90, 1630),
        "Σημείωση: τα roomrate_properties είναι global competitor entities. Τα scrape runs/jobs/results είναι scoped ανά account.",
        font=F_SMALL,
        fill=MUTED,
    )

    img.save(OUTPUT_PATH, quality=95)
    print(OUTPUT_PATH)
    build_clean_png()


def simple_box(
    d: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    title: str,
    lines: list[str],
    color: str,
    width: int = 390,
    height: int = 184,
) -> dict[str, tuple[int, int]]:
    x, y = xy
    d.rounded_rectangle([x, y, x + width, y + height], radius=18, fill=WHITE, outline=LINE, width=2)
    d.rounded_rectangle([x, y, x + width, y + 48], radius=18, fill=color)
    d.rectangle([x, y + 30, x + width, y + 48], fill=color)
    d.text((x + 18, y + 12), title, font=F_TABLE, fill=WHITE)
    yy = y + 66
    for item in lines:
        d.text((x + 18, yy), item, font=F_FIELD, fill=TEXT)
        yy += 27
    return {
        "left": (x, y + height // 2),
        "right": (x + width, y + height // 2),
        "top": (x + width // 2, y),
        "bottom": (x + width // 2, y + height),
        "center": (x + width // 2, y + height // 2),
    }


def arrow(d: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str) -> None:
    sx, sy = start
    ex, ey = end
    mid_x = (sx + ex) // 2
    d.line([(sx, sy), (mid_x, sy), (mid_x, ey), (ex, ey)], fill=color, width=4)
    d.polygon([(ex, ey), (ex - 12, ey - 7), (ex - 12, ey + 7)], fill=color)


def build_clean_png() -> None:
    img = Image.new("RGB", (2200, 1480), BG)
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([60, 50, 2140, 180], radius=28, fill=NAVY)
    d.text((95, 78), "RoomRate ERD - καθαρή εικόνα για email", font=F_TITLE, fill=WHITE)
    d.text(
        (98, 140),
        "Multi-tenant SaaS schema + normalized market intelligence data",
        font=F_SUBTITLE,
        fill="#D9F3EF",
    )

    d.text((90, 230), "Tenant / χρήστες", font=F_SECTION, fill=TEAL_DARK)
    d.text((780, 230), "Scraping & market data", font=F_SECTION, fill=BLUE)
    d.text((1560, 230), "API views / fallback", font=F_SECTION, fill=GREEN)

    box_specs = [
        (
            "accounts",
            (90, 290),
            "roomrate_accounts",
            ["id PK", "slug UK", "display_name", "status"],
            TEAL,
            390,
        ),
        (
            "users",
            (90, 535),
            "user_identities",
            ["id PK", "auth provider + subject", "email", "is_active"],
            TEAL,
            390,
        ),
        (
            "memberships",
            (520, 413),
            "memberships",
            ["account_id FK", "user_id FK", "role", "created_at"],
            TEAL,
            390,
        ),
        (
            "owned",
            (90, 810),
            "owned_properties",
            ["account_id FK", "matched_property_id FK", "city", "lat/lng"],
            TEAL,
            390,
        ),
        (
            "jobs",
            (520, 810),
            "scrape_jobs",
            ["account_id FK", "owned_property_id FK", "dates / guests", "status"],
            AMBER,
            390,
        ),
        (
            "runs",
            (950, 410),
            "scrape_runs",
            ["account_id FK", "scrape_job_id FK", "dates + occupancy", "status"],
            BLUE,
            390,
        ),
        (
            "props",
            (950, 725),
            "properties",
            ["global competitor", "canonical_name", "city", "lat/lng"],
            BLUE,
            390,
        ),
        (
            "obs",
            (1380, 410),
            "rate_observations",
            ["scrape_run_id FK", "property_id FK", "reviews", "min/max price"],
            BLUE,
            390,
        ),
        (
            "packages",
            (1380, 725),
            "room_packages",
            ["observation_id FK", "room_type", "price", "rooms_left"],
            BLUE,
            390,
        ),
        (
            "amenities",
            (1380, 1010),
            "amenities bridge",
            ["properties", "amenities", "property_amenities", "deduped labels"],
            PURPLE,
            390,
        ),
        (
            "latest",
            (1770, 360),
            "latest_room_rates",
            ["API read view", "latest completed run", "account + occupancy", "dashboard/agents"],
            GREEN,
            360,
        ),
        (
            "markers",
            (1770, 640),
            "competitor_markers",
            ["Mapbox view", "latest completed run", "per account/date/occupancy", "future direct read"],
            GREEN,
            360,
        ),
        (
            "legacy",
            (1770, 920),
            "legacy fallback",
            ["room_rates", "scout_cache", "temporary rollback", "not preferred"],
            AMBER,
            360,
        ),
    ]
    boxes = {
        key: simple_box(d, xy, title, lines, color, width=width)
        for key, xy, title, lines, color, width in box_specs
    }

    accounts = boxes["accounts"]
    users = boxes["users"]
    memberships = boxes["memberships"]
    owned = boxes["owned"]
    jobs = boxes["jobs"]
    runs = boxes["runs"]
    props = boxes["props"]
    obs = boxes["obs"]
    packages = boxes["packages"]
    amenities = boxes["amenities"]
    latest = boxes["latest"]
    markers = boxes["markers"]

    # Draw arrows.
    arrow(d, accounts["right"], memberships["left"], TEAL_DARK)
    arrow(d, users["right"], memberships["left"], TEAL_DARK)
    arrow(d, accounts["bottom"], owned["top"], TEAL_DARK)
    arrow(d, owned["right"], jobs["left"], AMBER)
    arrow(d, jobs["right"], runs["left"], AMBER)
    arrow(d, accounts["right"], runs["left"], BLUE)
    arrow(d, runs["right"], obs["left"], BLUE)
    arrow(d, props["right"], obs["left"], BLUE)
    arrow(d, obs["bottom"], packages["top"], BLUE)
    arrow(d, props["bottom"], amenities["top"], PURPLE)
    arrow(d, packages["right"], latest["left"], GREEN)
    arrow(d, obs["right"], latest["left"], GREEN)
    arrow(d, props["right"], markers["left"], GREEN)

    # Repaint boxes over connector lines for a clean screenshot.
    for key, xy, title, lines, color, width in box_specs:
        simple_box(d, xy, title, lines, color, width=width)

    d.rounded_rectangle([90, 1270, 2130, 1370], radius=20, fill=WHITE, outline=LINE, width=2)
    d.text((120, 1292), "Κεντρική ιδέα", font=F_TABLE, fill=NAVY)
    d.text(
        (120, 1332),
        "Κάθε χρήστης/account έχει δικά του owned properties, scrape jobs και scrape runs. Τα competitor properties είναι global, αλλά τα API results φιλτράρονται με account_id.",
        font=F_FIELD,
        fill=TEXT,
    )

    d.text((90, 1418), "Generated from RoomRate schema - 2026-05-06", font=F_SMALL, fill=MUTED)
    img.save(OUTPUT_CLEAN_PATH, quality=95)
    print(OUTPUT_CLEAN_PATH)


if __name__ == "__main__":
    main()
