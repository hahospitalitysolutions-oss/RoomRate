# Round 6 — Stream A (scraper + job API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One map search with the defaults (40 results, 4 neighbouring areas, 10 km) scouts several Booking destinations in parallel, keeps every non-single room that fits the party, deep-crawls up to 120 hotels with live progress visible in `result_summary.progress`, and the read API returns each competitor with `distance_km`, `category_match`, `booking_url`, an `include_similar` toggle, `sort=distance`, a single consistent market summary, and a `GET /maps/own-property` payload for the «Εσείς» marker. Spec: `docs/superpowers/specs/2026-09-14-round6-demo-competitors-map-pricing-design.md` §3, §6 (first two bullets), §7 (Stream A rows).

**Architecture:** The API stays the contract owner. `ScrapeJobCreate` gains `nearby_destinations`/`radius_km` (persisted on `roomrate_scrape_jobs`, migration `20260915_0024`). `ScrapeJobService.run_job` resolves the radius centre server-side from `OnboardingRepository.get_owned_property` and passes it to the pure `BookingScrapeJobRunner._build_args`, which emits the new CLI flags plus `--deep-crawl-max-hotels` (the `hotel_cap`). The scraper gets `fetch_hotel_lists` (parallel scouts merged by normalized URL, haversine radius, distance sort, cap), two new pre-persist filters (`single_rooms`, `capacity`) replacing the category pool, and a `ProgressReporter` that merges `progress` into `result_summary` while the job is running. Warnings flow in one direction: the scraper emits `warnings: []` in its sentinel summary (nearby scout failures land there), the service appends `radius_skipped_no_coordinates` before `complete_job`. On the read side, `RoomRatesRepository` learns `include_similar`, drops the city filter on the job-scoped path, and gains `fetch_own_property_rates`; `MarketService` computes `category_match`/`distance_km` from `market_helpers` (single haversine implementation shared with the scraper) and picks per-hotel "cheapest same-category else cheapest similar" for markers and the summary.

**Tech Stack:** Python 3.12, FastAPI + pydantic v2, SQLAlchemy `text()` repositories, Alembic, pandas (scraper), pytest with the repo's fake-connection/echo-connection idioms (`api/tests/_fakes.py`). No new dependencies.

---

## Conventions used by every task

- **Worktree:** `C:\vscode_code\wt-round6-a\room_project2` on branch `feature/round6-a`. The git root is the PARENT directory: always `cd` into the worktree's `room_project2` and `git add` explicit paths. Never `git add -A`.
- **Pytest (PowerShell), replace `<path>`:**
  ```powershell
  cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest <path> -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
  ```
- **Commits:** imperative sentence subject, blank line, body, `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. PowerShell here-string form (closing `'@` at column 0):
  ```powershell
  cd C:\vscode_code\wt-round6-a\room_project2
  git add <paths>
  git commit -m @'
  <Subject>

  <Body>

  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  '@
  ```
- New user-facing strings (logs/messages) are Greek, zero emoji. Never remove the room-name cascade in `scraper/persistence.py`. Never change `source_property_key` in `api/services/room_rates_normalizer.py`.
- Field names are the §7 contract and must not drift: `nearby_destinations`, `radius_km`, `result_summary.progress`, `result_summary.warnings`, `include_similar`, `distance_km`, `category_match`, `booking_url`, `sort=distance`, `MarketSummary.total_records/same_category_hotels/similar_hotels`, `GET /api/v1/maps/own-property`, `GET /api/v1/onboarding/nearby-destinations`.

## Spec ambiguities resolved in this plan

1. **Where the radius centre is resolved:** in `ScrapeJobService.run_job` through a new optional `owned_property_lookup` collaborator (the `OnboardingRepository`), so `_build_args` stays pure and unit-testable (`ScrapeJobCommand` carries `origin_lat`/`origin_lng`). A lookup failure degrades to "no radius + warning" rather than failing the job.
2. **`result_summary.warnings`:** always present (`[]`) in the scraper's sentinel summary from now on; a nearby scout failure adds `nearby_scout_failed:<destination>`; the service appends `radius_skipped_no_coordinates`. A job whose scraper output had no parsable summary still records the service warning (`{"warnings": [...]}`), otherwise `None` passes through unchanged.
3. **`nearby_destinations` normalization** is lenient where the UI chips can repeat themselves (trim, drop blanks, case-insensitive dedupe, drop the main destination silently) and strict where the spec gives numbers (an entry over 100 characters or more than 8 entries after normalization is a 422).
4. **`ProgressReporter` SQL** spells the cast as `CAST(:progress AS jsonb)` because SQLAlchemy's `text()` parses `:progress::jsonb` as a bind named `progres`. Same semantics as spec §3.5.
5. **`category_match` without a baseline category** (no `room_type_category` in the read filters): every package is `"same"` — there is nothing to compare against and the «Μόνο ίδια κατηγορία» toggle is meaningless. With a baseline: `"same"` iff the row category is in `comparable_room_type_categories(baseline)`, else (including `None`) `"similar"`.
6. **`MarketSummary` prices** are computed over ONE price per hotel — the marker's price (cheapest same-category package, else cheapest similar) — so the single summary box agrees with the map. `total_records` stays the raw row count; `rooms_left_total` stays the row sum. The existing per-row summary test is updated accordingly.
7. **`GET /maps/own-property`:** `scrape_job_id` is optional (the «Εσείς» marker exists before the first search); the price/room come from the owner's cheapest package in the comparable pool of the job's category (fallback: the property's `selected_room_type_category`), else the cheapest package of any category — the same fallback Stream C uses for the reference price.
8. **`sort=distance`** requires `owned_property_id` (400 otherwise, like `sort=match` requires `match_room`); a property without coordinates yields `distance_km: null` everywhere and the list falls back to price order.
9. **haversine lives in `api/services/market_helpers.py`** and is imported by the scraper (the scraper already imports `api.services.*`); it lands in Task 9 because the scout needs it first, Task 15 adds the read-side helpers around it.

## File structure

**Created**
- `migrations/versions/20260915_0024_add_nearby_search_and_booking_url.py` — nullable `nearby_destinations` (JSONB) + `radius_km` (NUMERIC(5,1)) on `roomrate_scrape_jobs`; `booking_url` (TEXT) on `roomrate_properties`.
- `api/services/nearby_destinations.py` — static default neighbouring areas per canonical destination.
- `scraper/progress.py` — `ProgressReporter`: best-effort `result_summary.progress` writer.
- `api/tests/test_migrations_head.py` — single Alembic head + ORM columns.
- `api/tests/test_scrape_job_schemas.py` — `ScrapeJobCreate`/`ScrapeJobResponse` new fields.
- `api/tests/test_nearby_destinations.py` — defaults + endpoint.
- `api/tests/test_scraper_progress.py` — `ProgressReporter`.
- `api/tests/test_scout_nearby.py` — `fetch_hotel_lists`.
- `api/tests/test_market_helpers.py` — haversine, `category_match_for`, `distance_km_from`.
- `api/tests/test_round6_routes.py` — `include_similar`, `sort=distance`, `GET /maps/own-property`.

**Modified**
- `api/config.py` — `env_file_encoding="utf-8-sig"`.
- `api/models/market.py` — ORM columns for the migration.
- `api/schemas/scrape_jobs.py` — `nearby_destinations`, `radius_km` (+ validators, response fields).
- `api/repositories/scrape_jobs_repository.py` — persist/read the two new job columns.
- `api/schemas/onboarding.py` — `NearbyDestinationsResponse`.
- `api/routers/onboarding.py` — `GET /nearby-destinations`.
- `api/repositories/onboarding_repository.py` — `get_owned_property` also returns `latitude`, `longitude`, `booking_url`.
- `api/services/scrape_job_service.py` — command fields, `_build_args` (limits, new flags), origin resolution, warning merge.
- `api/dependencies.py` — wire `OnboardingRepository` into `ScrapeJobService`.
- `scraper/config.py`, `scraper/cli.py` — new config fields/flags, batch size 8, progress + warnings wiring.
- `scraper/scout.py` — `fetch_hotel_lists`, cache size check.
- `scraper/deep_crawl.py` — per-batch progress.
- `scraper/persistence.py` — `single_rooms` + `capacity` filters replace the category pool.
- `scraper/models.py` — `warnings: []` in `empty_result_summary`.
- `scraper/provider.py`, `scraper/__init__.py` — export `fetch_hotel_lists`.
- `api/services/room_rates_normalizer.py` — `NormalizedRoomRate.booking_url` from CSV `hotel_url` (NOT `source_property_key`).
- `api/repositories/normalized_market_writer.py` — `booking_url` in the property upsert.
- `api/services/market_helpers.py` — `haversine_km`, `category_match_for`, `distance_km_from`.
- `api/repositories/room_rates_repository.py` — `include_similar`, job-scoped city filter removal, `booking_url` column, `fetch_own_property_rates`.
- `api/services/market_service.py` — `include_similar` filter field, `category_match`, `distance_km`, marker choice, summary counts, `sort_by_distance`, `get_own_property_cheapest_rate`.
- `api/schemas/market.py` — additive fields + `OwnPropertyMapInfo`.
- `api/routers/_filters.py`, `api/routers/maps.py`, `api/routers/competitors.py` — `include_similar`, `build_origin`, `sort=distance`, own-property endpoint.
- `api/services/onboarding_service.py` — one stale comment about the scout cache.
- Tests touched: `api/tests/test_config_validation.py`, `api/tests/test_scrape_jobs_repository.py`, `api/tests/test_onboarding_repository.py`, `api/tests/test_scrape_job_service.py`, `api/tests/test_booking_scraper_pipeline.py`, `api/tests/test_normalized_market_writer.py`, `api/tests/test_room_rates_repository.py`, `api/tests/test_market_service.py`, `api/tests/test_api_routes.py`, `api/tests/test_competitor_matching_routes.py`.

---

### Task 1: Read the `.env` with a BOM-tolerant encoding

**Files:**
- Modify: `api/config.py:23`
- Test: `api/tests/test_config_validation.py` (append)

- [ ] **Step 1: Write the failing test** — append to `api/tests/test_config_validation.py`:

```python
# ----------------------------------------------------------------------------
# .env encoding (Round 6 §6): the owner's .env is saved with a UTF-8 BOM
# ----------------------------------------------------------------------------


def test_settings_read_a_dotenv_saved_with_a_utf8_bom(tmp_path, monkeypatch):
    """With plain utf-8 the first key reads as '\\ufeffROOMRATE_STALE_JOB_MINUTES'
    and silently never applies; the scraper already loads .env with utf-8-sig.
    """
    monkeypatch.delenv("ROOMRATE_STALE_JOB_MINUTES", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"\xef\xbb\xbfROOMRATE_STALE_JOB_MINUTES=42\n")

    loaded = Settings(_env_file=str(env_file))

    assert loaded.stale_job_minutes == 42


def test_settings_declare_the_bom_tolerant_env_file_encoding():
    assert Settings.model_config["env_file_encoding"] == "utf-8-sig"
```

- [ ] **Step 2: Run the test and watch it fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_config_validation.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: 2 failures — `assert 5 == 42` and `assert 'utf-8' == 'utf-8-sig'`.

- [ ] **Step 3: Implement** — in `api/config.py` replace line 23:

```python
    # utf-8-sig: the owner's .env is written by a Windows editor with a UTF-8
    # BOM; plain utf-8 keeps the BOM glued to the first key so that variable
    # never loads. The scraper (scraper/clients.py) already tolerates it.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")
```

- [ ] **Step 4: Run the test file again** (same command). Expected: all tests in the file PASS.

- [ ] **Step 5: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add api/config.py api/tests/test_config_validation.py
git commit -m @'
Read the API .env with a BOM-tolerant encoding

The owner's .env is saved with a UTF-8 BOM. With plain utf-8 the byte
order mark stays glued to the first key, so that variable silently never
applies. The scraper already loads the same file with utf-8-sig; the API
now does too.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 2: Migration `20260915_0024` + ORM columns + single-head test

**Files:**
- Create: `migrations/versions/20260915_0024_add_nearby_search_and_booking_url.py`
- Modify: `api/models/market.py` (`RoomRateScrapeJob` after line 262 `result_summary`; `RoomRateProperty` after line 407 `stars`)
- Test: `api/tests/test_migrations_head.py` (new)

- [ ] **Step 1: Write the failing tests** — create `api/tests/test_migrations_head.py`:

```python
"""Alembic head + ORM column checks for the Round 6 migration.

ScriptDirectory reads the version files only (env.py never runs), so this
needs no DATABASE_URL. CI enforces a single head; this pins the same rule
locally so a parallel stream's migration cannot fork the history unnoticed.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from api.models.market import RoomRateProperty, RoomRateScrapeJob

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUND6_REVISION = "20260915_0024"


def _script_directory() -> ScriptDirectory:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_round6_migration_is_the_single_alembic_head():
    assert _script_directory().get_heads() == [ROUND6_REVISION]


def test_round6_migration_revises_the_result_summary_migration():
    revision = _script_directory().get_revision(ROUND6_REVISION)

    assert revision.down_revision == "20260828_0023"


def test_orm_models_declare_the_round6_columns():
    job_columns = RoomRateScrapeJob.__table__.columns
    property_columns = RoomRateProperty.__table__.columns

    assert job_columns["nearby_destinations"].nullable is True
    assert job_columns["radius_km"].nullable is True
    assert (job_columns["radius_km"].type.precision, job_columns["radius_km"].type.scale) == (5, 1)
    assert property_columns["booking_url"].nullable is True
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_migrations_head.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: 3 failures (`['20260828_0023'] == ['20260915_0024']`, `Can't locate revision`, `KeyError: 'nearby_destinations'`).

- [ ] **Step 3: Create the migration** — `migrations/versions/20260915_0024_add_nearby_search_and_booking_url.py`:

```python
"""Persist nearby-destination searches on jobs and Booking URLs on properties.

Revision ID: 20260915_0024
Revises: 20260828_0023
Create Date: 2026-09-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260915_0024"
down_revision: str | None = "20260828_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """All nullable, no backfill: older jobs mean "no nearby areas, no radius"."""
    op.add_column(
        "roomrate_scrape_jobs",
        sa.Column("nearby_destinations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("roomrate_scrape_jobs", sa.Column("radius_km", sa.Numeric(5, 1), nullable=True))
    # Filled by normalized_market_writer from the CSV hotel_url on every
    # upsert, so pre-existing properties gain it on their next scrape.
    op.add_column("roomrate_properties", sa.Column("booking_url", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the three Round 6 columns."""
    op.drop_column("roomrate_properties", "booking_url")
    op.drop_column("roomrate_scrape_jobs", "radius_km")
    op.drop_column("roomrate_scrape_jobs", "nearby_destinations")
```

- [ ] **Step 4: Add the ORM columns** — in `api/models/market.py`, inside `RoomRateScrapeJob` directly after `result_summary: Mapped[dict | None] = mapped_column(JSONB)` (line 262):

```python
    # Round 6: what the map form sent, echoed back so «Επαναφορά τελευταίας
    # αναζήτησης» can refill the nearby-area chips and the radius. NULL for
    # jobs created before migration 20260915_0024.
    nearby_destinations: Mapped[list | None] = mapped_column(JSONB)
    radius_km: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
```

and inside `RoomRateProperty` directly after `stars: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))` (line 407):

```python
    # Round 6: the Booking listing URL (scraper CSV hotel_url), refreshed on
    # every upsert so the map popup can link «Άνοιγμα στο Booking».
    booking_url: Mapped[str | None] = mapped_column(Text)
```

(`Decimal`, `Numeric`, `Text`, `JSONB` are already imported in that module.)

- [ ] **Step 5: Run the tests again** (same command). Expected: 3 PASS.

- [ ] **Step 6: Confirm one head from the CLI**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; python -m alembic heads
```

Expected output contains exactly one line: `20260915_0024 (head)`.

- [ ] **Step 7: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add migrations/versions/20260915_0024_add_nearby_search_and_booking_url.py api/models/market.py api/tests/test_migrations_head.py
git commit -m @'
Store nearby-area searches on jobs and Booking URLs on properties

Migration 20260915_0024 adds nullable nearby_destinations (JSONB) and
radius_km (NUMERIC(5,1)) to roomrate_scrape_jobs so the map can restore
the last search form, and booking_url to roomrate_properties for the
popup link. No backfill; the head test pins the single Alembic head.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 3: `ScrapeJobCreate`/`ScrapeJobResponse` gain `nearby_destinations` + `radius_km`; the repository persists them

**Files:**
- Modify: `api/schemas/scrape_jobs.py`
- Modify: `api/repositories/scrape_jobs_repository.py` (`JOB_COLUMNS`, `_insert_job`)
- Test: `api/tests/test_scrape_job_schemas.py` (new), `api/tests/test_scrape_jobs_repository.py` (append)

- [ ] **Step 1: Write the failing tests** — create `api/tests/test_scrape_job_schemas.py`:

```python
"""ScrapeJobCreate / ScrapeJobResponse Round 6 fields (spec §3.1, §7)."""

from datetime import date

import pytest
from pydantic import ValidationError

from api.schemas.scrape_jobs import ScrapeJobCreate, ScrapeJobResponse


def _create(**overrides) -> ScrapeJobCreate:
    payload = {"destination": "Φαληράκι", "check_in": date(2030, 7, 1), "check_out": date(2030, 7, 5)}
    payload.update(overrides)
    return ScrapeJobCreate(**payload)


def _response(**overrides) -> ScrapeJobResponse:
    payload = {
        "id": "00000000-0000-0000-0000-000000000777",
        "account_id": "00000000-0000-0000-0000-000000000001",
        "destination": "Φαληράκι",
        "check_in": date(2030, 7, 1),
        "check_out": date(2030, 7, 5),
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "status": "queued",
        "requested_at": "2026-05-10T12:00:00Z",
    }
    payload.update(overrides)
    return ScrapeJobResponse(**payload)


def test_create_defaults_to_no_nearby_areas_and_no_radius():
    request = _create()

    assert request.nearby_destinations == []
    assert request.radius_km is None


def test_create_normalizes_nearby_destinations_leniently():
    """Trim, drop blanks, case-insensitive dedupe, drop the main destination —
    alias-aware, so «faliraki» IS the main destination «Φαληράκι»."""
    request = _create(nearby_destinations=[" Ιξιά ", "", "ιξιά", "Αφάντου", "faliraki", "Faliraki", "  "])

    assert request.nearby_destinations == ["Ιξιά", "Αφάντου"]


@pytest.mark.parametrize(
    "nearby_destinations",
    [
        [f"Περιοχή {index}" for index in range(9)],  # 9 distinct entries after normalization
        ["Κ" * 101],  # one entry over 100 characters
    ],
)
def test_create_rejects_too_many_or_too_long_nearby_destinations(nearby_destinations):
    with pytest.raises(ValidationError):
        _create(nearby_destinations=nearby_destinations)


@pytest.mark.parametrize("radius_km", [0.4, 50.1, -1])
def test_create_rejects_radius_outside_half_to_fifty_km(radius_km):
    with pytest.raises(ValidationError):
        _create(radius_km=radius_km)


def test_create_accepts_the_radius_bounds():
    assert _create(radius_km=0.5).radius_km == 0.5
    assert _create(radius_km=50).radius_km == 50.0


def test_response_reads_null_columns_of_pre_round6_jobs_as_defaults():
    response = _response(nearby_destinations=None, radius_km=None)

    assert response.nearby_destinations == []
    assert response.radius_km is None


def test_response_echoes_the_persisted_search_form():
    # NUMERIC(5,1) arrives as Decimal; JSONB as a list.
    response = _response(nearby_destinations=["Ιξιά", "Αφάντου"], radius_km="10.0")

    assert response.nearby_destinations == ["Ιξιά", "Αφάντου"]
    assert response.radius_km == 10.0
```

and append to `api/tests/test_scrape_jobs_repository.py`:

```python
def test_create_job_persists_nearby_destinations_and_radius(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"active_jobs": 0, "daily_jobs": 0}]),
            FakeResult(rows=[{**_inserted_job_row(), "nearby_destinations": ["Ιξιά"], "radius_km": 10.0}]),
        ],
    )
    request = ScrapeJobCreate(
        destination="Φαληράκι",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        nearby_destinations=["Ιξιά"],
        radius_km=10,
    )

    row = ScrapeJobRepository().create_job(ACCOUNT_ID, request)

    insert_sql, params = engine.connection.calls[2]
    assert "nearby_destinations" in insert_sql and "radius_km" in insert_sql
    assert params["nearby_destinations"] == ["Ιξιά"]
    assert params["radius_km"] == 10.0
    assert row["nearby_destinations"] == ["Ιξιά"]


def test_job_reads_select_the_round6_columns(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[])])

    assert ScrapeJobRepository().get_job(ACCOUNT_ID, JOB_ID) is None

    sql, _ = engine.connection.calls[0]
    assert "j.nearby_destinations" in sql and "j.radius_km" in sql
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_scrape_job_schemas.py api/tests/test_scrape_jobs_repository.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: the new tests fail (`AttributeError: nearby_destinations`, `ValidationError` not raised, `"nearby_destinations" in insert_sql` False).

- [ ] **Step 3: Extend the schemas** — in `api/schemas/scrape_jobs.py` add `field_validator` to the pydantic import line and, in `ScrapeJobCreate`, after `filters_payload`:

```python
    # Round 6 (§3.1): neighbouring Booking destinations scouted in parallel
    # and the radius (km) around the OWNER's property. Normalized in the
    # validator below; the radius centre is resolved server-side (Task 6).
    nearby_destinations: list[str] = Field(default_factory=list)
    radius_km: float | None = Field(default=None, ge=0.5, le=50)
```

Extend `validate_date_range` — insert directly before `return self`:

```python
        self.nearby_destinations = self._normalize_nearby_destinations(self.nearby_destinations)
```

and add the helper to `ScrapeJobCreate` (after `validate_date_range`):

```python
    MAX_NEARBY_DESTINATIONS = 8
    MAX_NEARBY_DESTINATION_LENGTH = 100

    def _normalize_nearby_destinations(self, values: list[str]) -> list[str]:
        """Lenient where the UI chips repeat themselves, strict on the spec numbers.

        Trim, drop blanks, dedupe and drop the main destination by canonical key
        (case-, accent- and alias-insensitive: «faliraki» is «Φαληράκι»). An
        entry over 100 characters or more than 8 entries AFTER normalization is
        a validation error (422 at the boundary).
        """
        main_key = canonicalize_destination(self.destination)
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            entry = str(value or "").strip()
            if not entry:
                continue
            if len(entry) > self.MAX_NEARBY_DESTINATION_LENGTH:
                raise ValueError("nearby_destinations entries must be at most 100 characters")
            key = canonicalize_destination(entry)
            if key == main_key or key in seen:
                continue
            seen.add(key)
            normalized.append(entry)
        if len(normalized) > self.MAX_NEARBY_DESTINATIONS:
            raise ValueError("nearby_destinations allows at most 8 areas")
        return normalized
```

In `ScrapeJobResponse` add after `result_summary`:

```python
    # Round 6: echoed back for «Επαναφορά τελευταίας αναζήτησης». NULL columns
    # (jobs older than migration 20260915_0024) read as the defaults.
    nearby_destinations: list[str] = Field(default_factory=list)
    radius_km: float | None = None

    @field_validator("nearby_destinations", mode="before")
    @classmethod
    def _null_nearby_is_empty(cls, value: object) -> object:
        return [] if value is None else value
```

- [ ] **Step 4: Persist and read the columns** — in `api/repositories/scrape_jobs_repository.py`:

`JOB_COLUMNS`: replace the line `j.result_summary,` with

```python
                        j.result_summary, j.nearby_destinations, j.radius_km,
```

`_insert_job`: add `nearby_destinations, radius_km` to the INSERT column list (after `scheduled, status, max_attempts`), `:nearby_destinations, :radius_km` to VALUES, `nearby_destinations, radius_km` to RETURNING (after `next_attempt_at`), bind both JSONB params:

```python
            ).bindparams(
                bindparam("filters_payload", type_=JSONB),
                bindparam("nearby_destinations", type_=JSONB),
            ),
```

and add to the parameter dict:

```python
                "nearby_destinations": request.nearby_destinations,
                "radius_km": request.radius_km,
```

- [ ] **Step 5: Run the two test files again** (same command). Expected: all PASS. Then run the callers of the schema to catch shape drift:

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_scrape_job_routes.py api/tests/test_scrape_job_service.py api/tests/test_onboarding_routes.py api/tests/test_onboarding_service.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: all PASS (the new fields default, so existing fixtures need no change).

- [ ] **Step 6: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add api/schemas/scrape_jobs.py api/repositories/scrape_jobs_repository.py api/tests/test_scrape_job_schemas.py api/tests/test_scrape_jobs_repository.py
git commit -m @'
Accept nearby areas and a radius on scrape-job requests

ScrapeJobCreate gains nearby_destinations (trimmed, deduped and freed of
the main destination by canonical key; at most 8 entries of 100 chars)
and radius_km (0.5-50). Both are stored on the job and echoed by
ScrapeJobResponse so the map can restore the last search form.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 4: Default neighbouring areas + `GET /api/v1/onboarding/nearby-destinations`

**Files:**
- Create: `api/services/nearby_destinations.py`
- Modify: `api/schemas/onboarding.py` (append `NearbyDestinationsResponse`)
- Modify: `api/routers/onboarding.py` (new route + imports)
- Test: `api/tests/test_nearby_destinations.py` (new)

- [ ] **Step 1: Write the failing tests** — create `api/tests/test_nearby_destinations.py`:

```python
"""Static neighbouring-area defaults and their onboarding endpoint (spec §3.1)."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.dependencies import AccountContext, get_account_context
from api.main import app
from api.services.nearby_destinations import default_nearby_destinations

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
FALIRAKI_NEARBY = ["Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"]


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("destination", ["Φαληράκι", "faliraki", " Faliraki "])
def test_faliraki_defaults_resolve_through_every_alias(destination):
    assert default_nearby_destinations(destination) == FALIRAKI_NEARBY


@pytest.mark.parametrize("destination", ["Rhodes", "Ρόδος", "Nowhere", "", None])
def test_other_destinations_have_no_defaults(destination):
    assert default_nearby_destinations(destination) == []


def test_nearby_destinations_endpoint_returns_the_contract_shape():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    client = TestClient(app)

    response = client.get("/api/v1/onboarding/nearby-destinations", params={"destination": "Φαληράκι"})

    assert response.status_code == 200
    assert response.json() == {"destination": "Φαληράκι", "canonical": "faliraki", "nearby": FALIRAKI_NEARBY}


def test_nearby_destinations_endpoint_rejects_a_blank_destination():
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    client = TestClient(app)

    assert client.get("/api/v1/onboarding/nearby-destinations", params={"destination": "   "}).status_code == 422
    assert client.get("/api/v1/onboarding/nearby-destinations").status_code == 422
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_nearby_destinations.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: collection error `ModuleNotFoundError: api.services.nearby_destinations`.

- [ ] **Step 3: Create the service module** — `api/services/nearby_destinations.py`:

```python
"""Static default neighbouring areas per canonical destination (Round 6 §3.1).

The Booking actor has no radius input, so the map form pre-fills the areas
around the owner's market and the scraper scouts each one. Only Faliraki has
defaults for the demo; every other destination starts with an empty list and
the owner types chips by hand.
"""

from __future__ import annotations

from api.services.destination_aliases import canonical_destination

DEFAULT_NEARBY_DESTINATIONS: dict[str, tuple[str, ...]] = {
    "faliraki": ("Καλλιθέα Ρόδου", "Ιξιά", "Αφάντου", "Κολύμπια"),
}


def default_nearby_destinations(destination: str | None) -> list[str]:
    """Return the default neighbouring areas for a destination (any alias), else []."""
    canonical = canonical_destination(destination)
    return list(DEFAULT_NEARBY_DESTINATIONS.get(canonical, ()))
```

- [ ] **Step 4: Schema + route** — append to `api/schemas/onboarding.py`:

```python
class NearbyDestinationsResponse(BaseModel):
    """Default neighbouring areas for a destination (Round 6 map form)."""

    destination: str
    canonical: str
    nearby: list[str] = Field(default_factory=list)
```

In `api/routers/onboarding.py` add `NearbyDestinationsResponse` to the `api.schemas.onboarding` import block, add the imports

```python
from api.services.destination_aliases import canonical_destination as canonicalize_destination
from api.services.nearby_destinations import default_nearby_destinations
```

and add the route (before `/property-candidates` is fine; paths do not overlap):

```python
@router.get("/nearby-destinations", response_model=NearbyDestinationsResponse)
async def nearby_destinations(
    destination: str = Query(..., min_length=1, max_length=255),
    account: AccountContext = Depends(get_account_context),
) -> NearbyDestinationsResponse:
    """Return the default neighbouring areas the map form pre-fills for a destination."""
    stripped = destination.strip()
    if not stripped:
        raise HTTPException(status_code=422, detail="destination is required")
    return NearbyDestinationsResponse(
        destination=stripped,
        canonical=canonicalize_destination(stripped),
        nearby=default_nearby_destinations(stripped),
    )
```

- [ ] **Step 5: Run the test file again** (same command). Expected: all PASS.

- [ ] **Step 6: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add api/services/nearby_destinations.py api/schemas/onboarding.py api/routers/onboarding.py api/tests/test_nearby_destinations.py
git commit -m @'
Expose default neighbouring areas per destination

A static table maps the canonical destination to the areas the map form
pre-fills (Faliraki: Kallithea, Ixia, Afantou, Kolymbia); everything else
is empty. GET /api/v1/onboarding/nearby-destinations returns
{destination, canonical, nearby} for any alias of the destination.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 5: `get_owned_property` also returns `latitude`, `longitude`, `booking_url`

**Files:**
- Modify: `api/repositories/onboarding_repository.py` (`get_owned_property` SELECT, ~line 330)
- Test: `api/tests/test_onboarding_repository.py` (append)

- [ ] **Step 1: Write the failing test** — append to `api/tests/test_onboarding_repository.py`:

```python
def test_get_owned_property_returns_coordinates_and_booking_url(monkeypatch):
    """Round 6: the radius centre («Εσείς» marker) and the popup link come
    from here; the read stays a bare connection (no transaction)."""
    row = {
        "id": OWNED_PROPERTY_ID,
        "display_name": "Rea Hotel",
        "canonical_destination": "faliraki",
        "selected_room_type_category": "double",
        "booking_url": "https://www.booking.com/hotel/gr/rea.html",
        "latitude": 36.34,
        "longitude": 28.2,
    }
    connection = FakeConnection(results=[FakeResult(rows=[row])])
    engine = _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().get_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert result == row
    assert engine.opened == ["connect"]
    sql = _statement(connection, "FROM roomrate_owned_properties")
    for column in ("booking_url", "latitude", "longitude", "selected_room_type_category"):
        assert column in sql.split("FROM roomrate_owned_properties")[0]
    assert "is_active = true" in sql
```

- [ ] **Step 2: Run the test and watch it fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_onboarding_repository.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: 1 failure — `assert 'booking_url' in '... SELECT id, display_name, canonical_destination, selected_room_type_category '`.

- [ ] **Step 3: Extend the SELECT** — in `get_owned_property` replace the column list with:

```sql
                    SELECT
                        id,
                        display_name,
                        canonical_destination,
                        selected_room_type_category,
                        booking_url,
                        latitude,
                        longitude
                    FROM roomrate_owned_properties
```

and update the docstring's first line to: `"""Return the owned property's market-key fields plus coordinates and Booking URL.` (Round 6: `ScrapeJobService` resolves the radius centre and `GET /maps/own-property` the «Εσείς» marker from this row).

- [ ] **Step 4: Run the test file again** (same command), plus the two consumers:

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_onboarding_repository.py api/tests/test_price_recommendation_routes.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: all PASS (`agents.py` reads the dict by key; extra keys are harmless).

- [ ] **Step 5: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add api/repositories/onboarding_repository.py api/tests/test_onboarding_repository.py
git commit -m @'
Return coordinates and the Booking URL with the owned property

get_owned_property now selects booking_url, latitude and longitude so
the scrape-job service can resolve the radius centre server-side and the
map can draw the owner's marker without a second query.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 6: `_build_args` sizing + new flags, server-side origin, `radius_skipped_no_coordinates`

**Files:**
- Modify: `api/services/scrape_job_service.py` (`ScrapeJobCommand`, `_build_args`, `ScrapeJobService.__init__`/`run_job`, new protocol + helpers)
- Modify: `api/dependencies.py` (`get_scrape_job_service`)
- Test: `api/tests/test_scrape_job_service.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `api/tests/test_scrape_job_service.py` (add `from decimal import Decimal` to the imports; `pytest` is already imported):

```python
# ----------------------------------------------------------------------------
# Round 6: nearby areas, radius origin, hotel cap (spec §3.1/§3.2)
# ----------------------------------------------------------------------------

OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


class FakeOwnedPropertyLookup:
    def __init__(self, row: dict | None = None, error: Exception | None = None):
        self.row = row
        self.error = error
        self.calls: list[tuple[UUID, UUID]] = []

    def get_owned_property(self, account_id, owned_property_id):
        self.calls.append((account_id, owned_property_id))
        if self.error:
            raise self.error
        return self.row


def _flag_values(args: list[str], flag: str) -> list[str]:
    return [args[index + 1] for index, value in enumerate(args) if value == flag]


def test_booking_runner_args_default_to_the_round6_limits():
    args = BookingScrapeJobRunner(timeout_seconds=10)._build_args(_command())

    assert args[args.index("--scout-max-items") + 1] == "40"
    assert args[args.index("--deep-crawl-max-hotels") + 1] == "40"
    assert args[args.index("--deep-crawl-max-items") + 1] == "80"  # min(320, max(2*40, 40))
    assert args[args.index("--deep-crawl-batch-size") + 1] == "8"
    assert "--nearby-destination" not in args
    assert "--radius-km" not in args


def test_booking_runner_args_pass_nearby_areas_and_the_hotel_cap():
    args = BookingScrapeJobRunner(timeout_seconds=10)._build_args(
        _command(nearby_destinations=("Ιξιά", "Αφάντου", "Καλλιθέα Ρόδου", "Κολύμπια"))
    )

    assert _flag_values(args, "--nearby-destination") == ["Ιξιά", "Αφάντου", "Καλλιθέα Ρόδου", "Κολύμπια"]
    assert args[args.index("--nearby-max-items") + 1] == "20"  # min(20, 40)
    # hotel_cap = min(120, 40 + 20 * 4) = 120; package budget = min(320, 2 * 120)
    assert args[args.index("--deep-crawl-max-hotels") + 1] == "120"
    assert args[args.index("--deep-crawl-max-items") + 1] == "240"


def test_booking_runner_args_nearby_limit_follows_a_small_result_limit():
    args = BookingScrapeJobRunner(timeout_seconds=10)._build_args(
        _command(nearby_destinations=("Ιξιά",), filters_payload={"limit": 8})
    )

    assert args[args.index("--nearby-max-items") + 1] == "8"
    assert args[args.index("--deep-crawl-max-hotels") + 1] == "16"
    assert args[args.index("--deep-crawl-max-items") + 1] == "40"  # max(2 * 16, 40)


def test_booking_runner_args_emit_the_radius_only_with_a_resolved_origin():
    runner = BookingScrapeJobRunner(timeout_seconds=10)

    with_origin = runner._build_args(_command(radius_km=10.0, origin_lat=36.34, origin_lng=28.2))
    without_origin = runner._build_args(_command(radius_km=10.0))

    assert with_origin[with_origin.index("--origin-lat") + 1] == "36.34"
    assert with_origin[with_origin.index("--origin-lng") + 1] == "28.2"
    assert with_origin[with_origin.index("--radius-km") + 1] == "10.0"
    assert "--radius-km" not in without_origin
    assert "--origin-lat" not in without_origin


def test_run_job_resolves_the_radius_origin_from_the_owned_property():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {
        **_job(),
        "owned_property_id": OWNED_PROPERTY_ID,
        "radius_km": 10.0,
        "nearby_destinations": ["Ιξιά"],
    }
    runner = FakeRunner(result_summary={"version": 1, "rows_seen": 1, "filter_counts": {}, "rows_written": 1, "warnings": []})
    lookup = FakeOwnedPropertyLookup(row={"latitude": Decimal("36.340000"), "longitude": Decimal("28.200000")})
    service = ScrapeJobService(repository=repository, runner=runner, owned_property_lookup=lookup)

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    command = runner.commands[0]
    assert lookup.calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID)]
    assert (command.origin_lat, command.origin_lng, command.radius_km) == (36.34, 28.2, 10.0)
    assert command.nearby_destinations == ("Ιξιά",)
    # Nothing to warn about: the scraper's own warnings pass through untouched.
    assert repository.completions[0][3]["warnings"] == []


@pytest.mark.parametrize(
    "lookup",
    [
        FakeOwnedPropertyLookup(row={"latitude": None, "longitude": None}),
        FakeOwnedPropertyLookup(row=None),
        FakeOwnedPropertyLookup(error=RuntimeError("db down")),
        None,
    ],
)
def test_run_job_runs_without_radius_and_warns_when_the_origin_is_unknown(lookup):
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {**_job(), "owned_property_id": OWNED_PROPERTY_ID, "radius_km": 10.0}
    scraper_summary = {"version": 1, "rows_seen": 1, "filter_counts": {}, "rows_written": 1, "warnings": ["nearby_scout_failed:Ιξιά"]}
    runner = FakeRunner(result_summary=scraper_summary)
    service = ScrapeJobService(repository=repository, runner=runner, owned_property_lookup=lookup)

    assert service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID) is True

    command = runner.commands[0]
    assert (command.origin_lat, command.origin_lng) == (None, None)
    assert command.radius_km == 10.0
    assert repository.transitions[-1][0] == "completed"
    assert repository.completions[0][3]["warnings"] == ["nearby_scout_failed:Ιξιά", "radius_skipped_no_coordinates"]


def test_run_job_records_the_radius_warning_even_without_a_scraper_summary():
    repository = FakeScrapeJobRepository()
    repository.jobs[JOB_ID] = {**_job(), "radius_km": 5.0}  # no owned property at all
    service = ScrapeJobService(repository=repository, runner=FakeRunner(result_summary=None))

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert repository.completions[0][3] == {"warnings": ["radius_skipped_no_coordinates"]}


def test_run_job_without_a_radius_never_touches_the_summary_or_the_lookup():
    repository = FakeScrapeJobRepository()
    lookup = FakeOwnedPropertyLookup(row={"latitude": 36.34, "longitude": 28.2})
    service = ScrapeJobService(repository=repository, runner=FakeRunner(result_summary=None), owned_property_lookup=lookup)

    service.run_job(account_id=ACCOUNT_ID, job_id=JOB_ID)

    assert lookup.calls == []
    assert repository.completions[0][3] is None
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_scrape_job_service.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: the new tests fail (`TypeError: unexpected keyword argument 'nearby_destinations'` / `'owned_property_lookup'`, `'25' == '40'`).

- [ ] **Step 3: Implement** — in `api/services/scrape_job_service.py`:

Add the import `from api.services.market_helpers import as_optional_float` and, after `RESULT_SUMMARY_SENTINEL`, the Round 6 constants:

```python
# Round 6 sizing (§3.1): default result limit, per-nearby-area scout size,
# deep-crawl hotel cap, and the batch size the runner pins on every job.
DEFAULT_RESULT_LIMIT = 40
MAX_NEARBY_LIMIT = 20
MAX_DEEP_CRAWL_HOTELS = 120
DEEP_CRAWL_BATCH_SIZE = 8
RADIUS_SKIPPED_WARNING = "radius_skipped_no_coordinates"
```

Add to `ScrapeJobCommand` after `scheduled`:

```python
    # Round 6: neighbouring areas + radius; the origin is the OWNER's
    # coordinates resolved in ScrapeJobService.run_job, never sent by the
    # browser. radius_km without an origin means "no radius" (flags omitted).
    nearby_destinations: tuple[str, ...] = ()
    radius_km: float | None = None
    origin_lat: float | None = None
    origin_lng: float | None = None
```

Add the lookup protocol after `AlertEvaluatorProtocol`:

```python
class OwnedPropertyLookupProtocol(Protocol):
    def get_owned_property(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict | None:
        """Return the owned property row (with latitude/longitude), or None."""
```

Add the module-level helper after `_bounded_int`:

```python
def _merge_warnings(result_summary: dict | None, warnings: list[str]) -> dict | None:
    """Append service-side warnings to the scraper's summary.

    Warnings flow one way: the scraper emits ``warnings: []`` in its sentinel
    summary (nearby scout failures land there) and the service appends its
    own. A run with no parsable summary still records the service warning as
    ``{"warnings": [...]}``; with nothing to add the summary passes through.
    """
    if not warnings:
        return result_summary
    existing = list((result_summary or {}).get("warnings") or [])
    return {**(result_summary or {}), "warnings": [*existing, *warnings]}
```

Replace the sizing block at the top of `_build_args` (everything from `result_limit = ...` through `deep_crawl_max_items = _bounded_int(...)`) with:

```python
        # Round 6 sizing (§3.1): 40 results by default, up to 20 per nearby
        # area, at most 120 hotels deep-crawled; the package budget follows
        # the hotel cap unless the payload pins deep_crawl_max_items.
        result_limit = _bounded_int(filters_payload.get("limit"), default=DEFAULT_RESULT_LIMIT, minimum=1, maximum=80)
        nearby_destinations = [str(area).strip() for area in command.nearby_destinations if str(area).strip()]
        nearby_limit = min(MAX_NEARBY_LIMIT, result_limit)
        hotel_cap = min(MAX_DEEP_CRAWL_HOTELS, result_limit + nearby_limit * len(nearby_destinations))
        deep_crawl_default = min(320, max(2 * hotel_cap, 40))
        deep_crawl_max_items = _bounded_int(
            filters_payload.get("deep_crawl_max_items"),
            default=deep_crawl_default,
            minimum=result_limit,
            maximum=320,
        )
```

In the `args` list insert, directly before `"--output-csv"`:

```python
            "--deep-crawl-max-hotels",
            str(hotel_cap),
            "--deep-crawl-batch-size",
            str(DEEP_CRAWL_BATCH_SIZE),
```

and after the `target_urls` loop (before `return args`):

```python
        for area in nearby_destinations:
            args.extend(["--nearby-destination", area])
        if nearby_destinations:
            args.extend(["--nearby-max-items", str(nearby_limit)])
        if command.radius_km is not None and command.origin_lat is not None and command.origin_lng is not None:
            args.extend(
                [
                    "--origin-lat", str(float(command.origin_lat)),
                    "--origin-lng", str(float(command.origin_lng)),
                    "--radius-km", str(float(command.radius_km)),
                ]
            )
```

`ScrapeJobService.__init__`: add the parameter `owned_property_lookup: OwnedPropertyLookupProtocol | None = None` (after `alert_evaluator`) and store it:

```python
        # Round 6: resolves the radius centre from the owner's property. None
        # (tests, minimal wiring) means every radius request degrades to
        # "no radius + warning".
        self.owned_property_lookup = owned_property_lookup
```

`run_job`: directly before `try:` (after the `_heartbeat` definition) add

```python
        origin, service_warnings = self._resolve_radius_origin(account_id, job)
```

extend the `ScrapeJobCommand(...)` call with

```python
                    nearby_destinations=tuple(job.nearby_destinations),
                    radius_km=job.radius_km,
                    origin_lat=origin[0] if origin else None,
                    origin_lng=origin[1] if origin else None,
```

and directly before the `completed, discovered_count = self.repository.complete_job(` call add

```python
        result_summary = _merge_warnings(result_summary, service_warnings)
```

Add the method to `ScrapeJobService` (before `_evaluate_alerts`):

```python
    def _resolve_radius_origin(
        self, account_id: uuid.UUID, job: ScrapeJobResponse
    ) -> tuple[tuple[float, float] | None, list[str]]:
        """Resolve the radius centre from the OWNER's property, never the browser.

        Every miss — no owned property on the job, no lookup wired, a lookup
        failure, missing/zero coordinates — degrades to "no radius" plus the
        ``radius_skipped_no_coordinates`` warning rather than failing the job.
        """
        if job.radius_km is None:
            return None, []
        owned: dict = {}
        if self.owned_property_lookup is not None and job.owned_property_id:
            try:
                owned = self.owned_property_lookup.get_owned_property(account_id, job.owned_property_id) or {}
            except Exception:
                logger.warning(
                    "Owned property lookup failed; running without radius: account_id=%s job_id=%s",
                    account_id,
                    job.id,
                    exc_info=True,
                )
        latitude = as_optional_float(owned.get("latitude"))
        longitude = as_optional_float(owned.get("longitude"))
        if latitude is None or longitude is None or (latitude, longitude) == (0.0, 0.0):
            logger.warning(
                "Radius %.1f km requested but the owned property has no coordinates; running without radius: "
                "account_id=%s job_id=%s",
                job.radius_km,
                account_id,
                job.id,
            )
            return None, [RADIUS_SKIPPED_WARNING]
        return (latitude, longitude), []
```

In `api/dependencies.py` `get_scrape_job_service`, add after `alert_evaluator=get_price_alert_service(),`:

```python
        # Round 6: radius centre lookup (owner's coordinates), API + scheduler paths.
        owned_property_lookup=OnboardingRepository(),
```

- [ ] **Step 4: Run the service tests again** (same command). Expected: all PASS — the pre-existing `_build_args` tests pin explicit limits (`25`, `5000`) so they are unaffected by the new default.

- [ ] **Step 5: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add api/services/scrape_job_service.py api/dependencies.py api/tests/test_scrape_job_service.py
git commit -m @'
Size scrapes from the map defaults and resolve the radius centre server-side

_build_args defaults to 40 results, scouts up to min(20, limit) hotels per
nearby area and caps the deep crawl at min(120, limit + nearby * n)
hotels (--deep-crawl-max-hotels) with a package budget of
min(320, max(2 * cap, 40)); batches are pinned at 8. run_job reads the
owner's coordinates through the onboarding repository and only then emits
--origin-lat/--origin-lng/--radius-km; a property without coordinates
runs without radius and appends radius_skipped_no_coordinates to
result_summary.warnings before completion.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 7: `ScraperConfig` fields + `scraper/cli.py` flags (batch size 8)

**Files:**
- Modify: `scraper/config.py`
- Modify: `scraper/cli.py` (`build_config_from_args`, `_build_interactive_config`)
- Test: `api/tests/test_booking_scraper_pipeline.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `api/tests/test_booking_scraper_pipeline.py`:

```python
# ----------------------------------------------------------------------------
# Round 6 §3.2: nearby areas, radius origin, hotel cap, batch size 8
# ----------------------------------------------------------------------------


def test_build_config_from_args_accepts_the_nearby_search_flags():
    config = build_config_from_args(
        [
            *_dry_run_cli_args(),
            "--nearby-destination", " Ιξιά ",
            "--nearby-destination", "ιξιά",
            "--nearby-destination", "faliraki",  # the main destination itself
            "--nearby-destination", "Αφάντου",
            "--nearby-max-items", "15",
            "--origin-lat", "36.34",
            "--origin-lng", "28.2",
            "--radius-km", "10",
            "--deep-crawl-max-hotels", "120",
        ]
    )

    assert config.nearby_destinations == ("Ιξιά", "Αφάντου")
    assert config.nearby_max_items == 15
    assert (config.origin_lat, config.origin_lng, config.radius_km) == (36.34, 28.2, 10.0)
    assert config.radius_active is True
    assert config.deep_crawl_max_hotels == 120


def test_build_config_from_args_round6_defaults():
    config = build_config_from_args(_dry_run_cli_args())

    assert config.nearby_destinations == ()
    assert config.nearby_max_items == 20
    assert config.radius_active is False
    assert config.deep_crawl_max_hotels is None
    assert config.deep_crawl_batch_size == 8


@pytest.mark.parametrize(
    "overrides",
    [{"origin_lat": 36.34}, {"radius_km": 0}, {"deep_crawl_max_hotels": 0}, {"nearby_max_items": 0}],
)
def test_scraper_config_rejects_half_an_origin_and_non_positive_round6_limits(overrides):
    with pytest.raises(ValueError):
        ScraperConfig(**overrides)
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_booking_scraper_pipeline.py -q -p no:cacheprovider -k "round6 or nearby_search_flags" --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: failures — `argparse` `SystemExit: 2` (unrecognized arguments), `AttributeError: nearby_destinations`, `TypeError: unexpected keyword argument 'origin_lat'`.

- [ ] **Step 3: Extend `ScraperConfig`** — in `scraper/config.py` add after `write_normalized`:

```python
    # --- Round 6: γειτονικές περιοχές, ακτίνα, όριο καταλυμάτων ---
    # Ο actor δεν δέχεται ακτίνα: κάθε γειτονική περιοχή γίνεται δικό της
    # scout (nearby_max_items το καθένα) και η ακτίνα εφαρμόζεται μετά,
    # με haversine από το κατάλυμα του ιδιοκτήτη (origin).
    nearby_destinations: tuple[str, ...] = ()
    nearby_max_items: int = 20
    origin_lat: float | None = None
    origin_lng: float | None = None
    radius_km: float | None = None          # None = χωρίς φίλτρο απόστασης
    deep_crawl_max_hotels: int | None = None  # None = χωρίς περικοπή
```

change the batch-size default (and its comment) to

```python
    # Πόσα ξενοδοχεία ανά batch. Round 6: 8 (ήταν 4) — με 120 ξενοδοχεία και
    # 3 workers, 15 batches αντί για 30 Apify runs.
    deep_crawl_batch_size: int = 8
```

add `"nearby_max_items": self.nearby_max_items,` to `positive_fields` and, at the end of `__post_init__`:

```python
        if (self.origin_lat is None) != (self.origin_lng is None):
            raise ValueError("origin_lat and origin_lng must be given together")
        if self.radius_km is not None and self.radius_km <= 0:
            raise ValueError("radius_km must be > 0")
        if self.deep_crawl_max_hotels is not None and self.deep_crawl_max_hotels < 1:
            raise ValueError("deep_crawl_max_hotels must be >= 1")

    @property
    def radius_active(self) -> bool:
        """True only with a radius AND a full origin; otherwise the scout keeps every hotel."""
        return self.radius_km is not None and self.origin_lat is not None and self.origin_lng is not None
```

- [ ] **Step 4: Parse the flags** — in `scraper/cli.py` add the arguments (after `--deep-crawl-workers`), change the batch-size default to `8`:

```python
    parser.add_argument("--deep-crawl-batch-size", type=int, default=8)
    parser.add_argument("--nearby-destination", action="append", default=[],
                        help="Γειτονική περιοχή για παράλληλο scout (επαναλαμβανόμενο).")
    parser.add_argument("--nearby-max-items", type=int, default=20)
    parser.add_argument("--origin-lat", type=float, default=None)
    parser.add_argument("--origin-lng", type=float, default=None)
    parser.add_argument("--radius-km", type=float, default=None)
    parser.add_argument("--deep-crawl-max-hotels", type=int, default=None)
```

add the helper above `build_config_from_args`:

```python
def _normalize_nearby_destinations(values: list[str], destination: str) -> tuple[str, ...]:
    """Trim, drop blanks, case-insensitive duplicates and the main destination."""
    seen = {destination.strip().casefold()}
    normalized: list[str] = []
    for value in values:
        entry = str(value or "").strip()
        key = entry.casefold()
        if not entry or key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return tuple(normalized)
```

and pass the new fields into the `ScraperConfig(...)` call (after `deep_crawl_batch_size=...`):

```python
        nearby_destinations=_normalize_nearby_destinations(args.nearby_destination, args.destination),
        nearby_max_items=args.nearby_max_items,
        origin_lat=args.origin_lat,
        origin_lng=args.origin_lng,
        radius_km=args.radius_km,
        deep_crawl_max_hotels=args.deep_crawl_max_hotels,
```

In `_build_interactive_config` change `deep_crawl_batch_size=4,` to `deep_crawl_batch_size=8,`.

- [ ] **Step 5: Run the whole pipeline test file** (drop the `-k`). Expected: all PASS (the deep-crawl batching tests pass `deep_crawl_batch_size` explicitly).

- [ ] **Step 6: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add scraper/config.py scraper/cli.py api/tests/test_booking_scraper_pipeline.py
git commit -m @'
Teach the scraper CLI the nearby-area, radius and hotel-cap flags

ScraperConfig gains nearby_destinations, nearby_max_items, origin_lat/lng,
radius_km and deep_crawl_max_hotels (validated together: half an origin
or a non-positive limit is rejected) and the CLI parses the matching
flags. Deep-crawl batches default to 8 hotels.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 8: `scraper/progress.py` — `ProgressReporter`

**Files:**
- Create: `scraper/progress.py`
- Test: `api/tests/test_scraper_progress.py` (new)

- [ ] **Step 1: Write the failing tests** — create `api/tests/test_scraper_progress.py`:

```python
"""ProgressReporter: best-effort result_summary.progress writer (spec §3.5)."""

import json
from uuid import UUID

import pytest

from scraper.progress import ProgressReporter

JOB_ID = UUID("00000000-0000-0000-0000-000000000777")


class _RecordingEngine:
    """engine.begin() context manager that records statements (or raises)."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.executed: list[tuple[str, dict]] = []

    def begin(self):
        return self

    def __enter__(self):
        if self.error:
            raise self.error
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), params or {}))
        return self


def _progress(engine: _RecordingEngine) -> dict:
    return json.loads(engine.executed[-1][1]["progress"])


def test_update_merges_progress_into_the_running_job_row():
    engine = _RecordingEngine()
    reporter = ProgressReporter(engine, JOB_ID)

    assert reporter.update("scout", done=0, total=5, destinations_total=5, hotels_found=0) is True

    sql, params = engine.executed[0]
    assert "COALESCE(result_summary, '{}'::jsonb) || jsonb_build_object('progress', CAST(:progress AS jsonb))" in sql
    assert "WHERE id = :job_id AND status = 'running'" in sql
    assert params["job_id"] == JOB_ID
    snapshot = _progress(engine)
    assert snapshot.pop("updated_at").endswith("Z")
    assert snapshot == {"stage": "scout", "done": 0, "total": 5, "destinations_total": 5, "hotels_found": 0}


def test_counts_accumulate_and_done_total_reset_when_the_stage_changes():
    engine = _RecordingEngine()
    reporter = ProgressReporter(engine, JOB_ID)
    reporter.update("scout", done=0, total=5, destinations_total=5, hotels_found=0)
    reporter.update("scout", done=5, total=5, hotels_found=61, hotels_in_radius=48)

    reporter.update("deep_crawl", total=48)

    snapshot = _progress(engine)
    assert snapshot["stage"] == "deep_crawl"
    assert "done" not in snapshot and snapshot["total"] == 48
    assert (snapshot["destinations_total"], snapshot["hotels_found"], snapshot["hotels_in_radius"]) == (5, 61, 48)

    reporter.update("deep_crawl", done=8, total=48)

    assert (_progress(engine)["done"], _progress(engine)["total"]) == (8, 48)


@pytest.mark.parametrize("engine, job_id", [(None, JOB_ID), (_RecordingEngine(), None)])
def test_update_is_a_noop_without_an_engine_or_a_job_id(engine, job_id):
    reporter = ProgressReporter(engine, job_id)

    assert reporter.enabled is False
    assert reporter.update("scout", done=0, total=1) is False
    if engine is not None:
        assert engine.executed == []


def test_update_swallows_and_logs_database_errors(caplog):
    reporter = ProgressReporter(_RecordingEngine(error=RuntimeError("connection refused")), JOB_ID)

    with caplog.at_level("WARNING", logger="roomrate.scraper"):
        assert reporter.update("persist") is False

    assert "connection refused" in caplog.text
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_scraper_progress.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: collection error `ModuleNotFoundError: scraper.progress`.

- [ ] **Step 3: Create the module** — `scraper/progress.py`:

```python
"""Best-effort live progress for scrape jobs (Round 6 §3.5).

The API reads the scraper's stdout only when the process exits, so the
scraper itself merges a ``progress`` object into ``result_summary`` while the
job is running. ``complete_job`` later replaces the whole summary, so the key
never outlives the run. Progress must never fail a scrape: every error is
logged and swallowed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from .logging_config import log

# CAST(:progress AS jsonb), not ``:progress::jsonb``: SQLAlchemy's text()
# would parse the latter as a bind named ``progres``.
PROGRESS_UPDATE_SQL = text(
    """
    UPDATE roomrate_scrape_jobs
    SET result_summary = COALESCE(result_summary, '{}'::jsonb)
                         || jsonb_build_object('progress', CAST(:progress AS jsonb)),
        updated_at = now()
    WHERE id = :job_id
      AND status = 'running'
    """
)


class ProgressReporter:
    """Writes ``result_summary.progress`` for one running job.

    Counts accumulate across calls (``destinations_total``, ``hotels_found``,
    ``hotels_in_radius``), so every snapshot carries the whole picture;
    ``done``/``total`` describe the current stage and reset when it changes
    (scouts during ``scout``, hotels during ``deep_crawl``).
    """

    def __init__(self, engine: Any, scrape_job_id: uuid.UUID | None):
        self.engine = engine
        self.scrape_job_id = scrape_job_id
        self._state: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        """False in dry runs (no engine) or interactive runs (no job id)."""
        return self.engine is not None and self.scrape_job_id is not None

    def update(
        self,
        stage: str,
        done: int | None = None,
        total: int | None = None,
        **counts: int | None,
    ) -> bool:
        """Merge one progress snapshot into the job row; True when written."""
        if stage != self._state.get("stage"):
            self._state.pop("done", None)
            self._state.pop("total", None)
        self._state["stage"] = stage
        for key, value in (("done", done), ("total", total), *counts.items()):
            if value is not None:
                self._state[key] = value
        self._state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.enabled:
            return False
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    PROGRESS_UPDATE_SQL,
                    {"progress": json.dumps(self._state, ensure_ascii=False), "job_id": self.scrape_job_id},
                )
            return True
        except Exception as exc:
            log.warning("Η ενημέρωση προόδου απέτυχε (η εργασία συνεχίζει): %s", exc)
            return False
```

- [ ] **Step 4: Run the test file again** (same command). Expected: 5 PASS.

- [ ] **Step 5: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add scraper/progress.py api/tests/test_scraper_progress.py
git commit -m @'
Add a best-effort progress reporter for running scrape jobs

ProgressReporter merges a progress snapshot (stage, done/total, cumulative
counts, updated_at) into result_summary with a JSONB update fenced on
status = running. It is a no-op without an engine or job id and swallows
every error, so progress can never fail a scrape.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 9: `fetch_hotel_lists` — parallel scouts, URL merge, radius, sort, cap (+ shared `haversine_km`)

**Files:**
- Modify: `api/services/market_helpers.py` (add `haversine_km`)
- Modify: `scraper/scout.py` (add `fetch_hotel_lists`), `scraper/provider.py`, `scraper/__init__.py` (export)
- Test: `api/tests/test_market_helpers.py` (new), `api/tests/test_scout_nearby.py` (new)

- [ ] **Step 1: Write the failing tests** — create `api/tests/test_market_helpers.py`:

```python
"""market_helpers: the single haversine shared by the scraper and the read API."""

import pytest

from api.services.market_helpers import haversine_km


def test_haversine_faliraki_to_rhodes_town_is_about_ten_and_a_half_km():
    assert haversine_km(36.34, 28.2, 36.4341, 28.2176) == pytest.approx(10.6, abs=0.1)


def test_haversine_is_zero_for_the_same_point_symmetric_and_one_decimal():
    assert haversine_km(36.34, 28.2, 36.34, 28.2) == 0.0
    assert haversine_km(36.34, 28.2, 36.41, 28.19) == haversine_km(36.41, 28.19, 36.34, 28.2)
    assert str(haversine_km(36.34, 28.2, 36.41, 28.19)).count(".") == 1
```

and create `api/tests/test_scout_nearby.py`:

```python
"""fetch_hotel_lists: parallel scouts merged by URL, radius, sort, cap (spec §3.3)."""

import pytest

from scraper import ActorRunError, fetch_hotel_lists
from scraper.config import ScraperConfig

FALIRAKI = (36.34, 28.2)


def _item(name: str, slug: str, lat: float | None = 36.34, lng: float | None = 28.2) -> dict:
    location = {} if lat is None else {"lat": str(lat), "lng": str(lng)}
    return {"name": name, "url": f"https://www.booking.com/hotel/gr/{slug}.el.html?aid=1", "location": location, "type": "hotel"}


# Distances from FALIRAKI: Aegean View 0.0, Afandou Inn ~6.2, Ixia Bay ~7.8, Far Away ~40 km.
SCOUTS = {
    "Φαληράκι": [_item("Aegean View", "aegean-view"), _item("Far Away", "far-away", 36.7, 28.2), _item("No Coords", "no-coords", None, None)],
    "Ιξιά": [_item("Aegean View (dup)", "aegean-view", 36.35, 28.21), _item("Ixia Bay", "ixia-bay", 36.41, 28.19)],
    "Αφάντου": [_item("Afandou Inn", "afandou-inn", 36.29, 28.17)],
}


def _install_fake_actor(monkeypatch, failing: frozenset[str] = frozenset(), calls: list | None = None) -> None:
    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        destination = actor_input["search"]
        if calls is not None:
            calls.append((destination, actor_input["maxItems"]))
        if destination in failing:
            raise ActorRunError(f"actor down for {destination}")
        return SCOUTS.get(destination, [])

    monkeypatch.setattr("scraper.scout._run_actor", fake_run_actor)


def _config(**overrides) -> ScraperConfig:
    base = dict(
        destination="Φαληράκι",
        nearby_destinations=("Ιξιά", "Αφάντου"),
        scout_max_items=40,
        nearby_max_items=20,
        scout_cache_hours=0,
        deep_crawl_workers=3,
    )
    base.update(overrides)
    return ScraperConfig(**base)


class _FakeProgress:
    def __init__(self):
        self.updates: list[dict] = []

    def update(self, stage, done=None, total=None, **counts):
        self.updates.append({"stage": stage, "done": done, "total": total, **counts})


def _names(hotels: list[dict]) -> list[str]:
    return [hotel["name"] for hotel in hotels]


def test_merges_scouts_by_normalized_url_with_the_main_destination_first(monkeypatch):
    calls: list = []
    _install_fake_actor(monkeypatch, calls=calls)

    hotels = fetch_hotel_lists(client=object(), config=_config(), engine=None)

    assert _names(hotels) == ["Aegean View", "Far Away", "No Coords", "Ixia Bay", "Afandou Inn"]
    assert all("?" not in hotel["url"] for hotel in hotels)
    assert sorted(calls) == [("Αφάντου", 20), ("Ιξιά", 20), ("Φαληράκι", 40)]


def test_radius_keeps_hotels_within_reach_sorted_by_distance(monkeypatch):
    _install_fake_actor(monkeypatch)

    hotels = fetch_hotel_lists(client=object(), config=_config(origin_lat=36.34, origin_lng=28.2, radius_km=10), engine=None)

    # Far Away (~40 km) and No Coords are out; the rest are nearest-first.
    assert _names(hotels) == ["Aegean View", "Afandou Inn", "Ixia Bay"]


def test_cap_trims_the_sorted_list(monkeypatch):
    _install_fake_actor(monkeypatch)

    within = fetch_hotel_lists(
        client=object(),
        config=_config(origin_lat=36.34, origin_lng=28.2, radius_km=10, deep_crawl_max_hotels=2),
        engine=None,
    )
    unranked = fetch_hotel_lists(client=object(), config=_config(deep_crawl_max_hotels=2), engine=None)

    assert _names(within) == ["Aegean View", "Afandou Inn"]
    assert _names(unranked) == ["Aegean View", "Far Away"]


def test_nearby_scout_failure_is_a_warning_and_the_main_failure_an_error(monkeypatch):
    _install_fake_actor(monkeypatch, failing=frozenset({"Ιξιά"}))
    warnings: list[str] = []

    hotels = fetch_hotel_lists(client=object(), config=_config(), engine=None, warnings=warnings)

    assert warnings == ["nearby_scout_failed:Ιξιά"]
    assert "Ixia Bay" not in _names(hotels)

    _install_fake_actor(monkeypatch, failing=frozenset({"Φαληράκι"}))
    with pytest.raises(ActorRunError):
        fetch_hotel_lists(client=object(), config=_config(), engine=None)


def test_progress_reports_each_scout_then_the_merge_and_radius(monkeypatch):
    _install_fake_actor(monkeypatch)
    progress = _FakeProgress()

    fetch_hotel_lists(
        client=object(),
        config=_config(origin_lat=36.34, origin_lng=28.2, radius_km=10),
        engine=None,
        progress=progress,
    )

    assert progress.updates[0] == {"stage": "scout", "done": 0, "total": 3, "destinations_total": 3, "hotels_found": 0}
    assert [update["done"] for update in progress.updates[1:4]] == [1, 2, 3]
    assert progress.updates[-1]["hotels_found"] == 5
    assert progress.updates[-1]["hotels_in_radius"] == 3
```

- [ ] **Step 2: Run the tests and watch them fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_market_helpers.py api/tests/test_scout_nearby.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: `ImportError: cannot import name 'haversine_km'` / `'fetch_hotel_lists'`.

- [ ] **Step 3: Add `haversine_km`** — append to `api/services/market_helpers.py` (add `import math` at the top):

```python
EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km, rounded to 1 decimal.

    The ONE implementation for the scraper's radius filter and the read
    API's ``distance_km``, so the scout and the map never disagree.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a)), 1)
```

- [ ] **Step 4: Add `fetch_hotel_lists`** — in `scraper/scout.py` extend the imports:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace

from api.services.market_helpers import haversine_km

from .actor import ActorRunError, _run_actor
from .utils import _extract_meta, _normalize_url, _safe_coord
```

and append:

```python
def _report(progress: Any | None, stage: str, **counts: int | None) -> None:
    if progress is not None:
        progress.update(stage, **counts)


def _distance_from_origin(hotel: dict, config: ScraperConfig) -> float | None:
    """km from the owner's property; None when the hotel has no coordinates (0/0)."""
    lat = _safe_coord(hotel.get("latitude"))
    lng = _safe_coord(hotel.get("longitude"))
    if lat == 0.0 or lng == 0.0:
        return None
    return haversine_km(config.origin_lat, config.origin_lng, lat, lng)


def fetch_hotel_lists(
    client: Any,
    config: ScraperConfig,
    engine=None,
    progress: Any | None = None,
    warnings: list[str] | None = None,
) -> list[dict]:
    """Stage 1 for the main destination plus its nearby areas (Round 6 §3.3).

    Runs one ``fetch_hotel_list`` per destination in parallel (cache and
    retries as today), merges by normalized URL with the main destination
    winning, applies the radius from the owner's coordinates, sorts nearest
    first and trims to ``deep_crawl_max_hotels``. A failed nearby scout is a
    warning (``nearby_scout_failed:<destination>`` appended to ``warnings``);
    a failed main scout is an ``ActorRunError`` exactly as before. Distances
    are NOT stored — the read API recomputes them from the coordinates.
    """
    destinations = [config.destination, *config.nearby_destinations]
    scout_configs = [
        replace(
            config,
            destination=destination,
            scout_max_items=config.scout_max_items if index == 0 else config.nearby_max_items,
        )
        for index, destination in enumerate(destinations)
    ]
    _report(progress, "scout", done=0, total=len(destinations), destinations_total=len(destinations), hotels_found=0)

    results: dict[int, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=config.deep_crawl_workers) as pool:
        futures = {
            pool.submit(fetch_hotel_list, client, scout_config, engine): index
            for index, scout_config in enumerate(scout_configs)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                if index == 0:
                    # Ολική αποτυχία του κύριου scout = αποτυχημένο scrape (exit 2 στο cli).
                    if isinstance(exc, ActorRunError):
                        raise
                    raise ActorRunError(f"Το scout για τον προορισμό '{destinations[0]}' απέτυχε: {exc}") from exc
                log.warning(
                    "[ΣΤΑΔΙΟ 1] Το scout για τη γειτονική περιοχή '%s' απέτυχε (%s) — συνεχίζω χωρίς αυτήν.",
                    destinations[index], exc,
                )
                if warnings is not None:
                    warnings.append(f"nearby_scout_failed:{destinations[index]}")
                results[index] = []
            _report(
                progress, "scout",
                done=len(results), total=len(destinations),
                hotels_found=sum(len(hotels) for hotels in results.values()),
            )

    # Ένωση: κλειδί το normalized URL, ο κύριος προορισμός (index 0) κερδίζει.
    merged: dict[str, dict] = {}
    for index in range(len(destinations)):
        for hotel in results.get(index, []):
            key = _normalize_url(str(hotel.get("url") or ""))
            if key and key not in merged:
                merged[key] = {**hotel, "url": key}
    hotels = list(merged.values())
    log.info("[ΣΤΑΔΙΟ 1] Ένωση %d scouts → %d μοναδικά καταλύματα.", len(destinations), len(hotels))

    if config.origin_lat is not None and config.origin_lng is not None:
        ranked: list[tuple[float | None, dict]] = []
        dropped_outside = dropped_no_coords = 0
        for hotel in hotels:
            distance = _distance_from_origin(hotel, config)
            if config.radius_km is not None:
                if distance is None:
                    dropped_no_coords += 1
                    log.info("[ΣΤΑΔΙΟ 1] '%s' χωρίς συντεταγμένες — εκτός ακτίνας.", hotel.get("name"))
                    continue
                if distance > config.radius_km:
                    dropped_outside += 1
                    continue
            ranked.append((distance, hotel))
        # Πλησιέστερα πρώτα, None στο τέλος· stable sort κρατά τη σειρά scout στις ισοπαλίες.
        ranked.sort(key=lambda pair: (pair[0] is None, pair[0] or 0.0))
        hotels = [hotel for _, hotel in ranked]
        if config.radius_km is not None:
            log.info(
                "[ΣΤΑΔΙΟ 1] Ακτίνα %.1f km: %d εντός, %d εκτός, %d χωρίς συντεταγμένες.",
                config.radius_km, len(hotels), dropped_outside, dropped_no_coords,
            )
    hotels_in_radius = len(hotels)

    if config.deep_crawl_max_hotels is not None and len(hotels) > config.deep_crawl_max_hotels:
        log.info("[ΣΤΑΔΙΟ 1] Περικοπή %d → %d καταλύματα (deep_crawl_max_hotels).", len(hotels), config.deep_crawl_max_hotels)
        hotels = hotels[: config.deep_crawl_max_hotels]

    _report(
        progress, "scout",
        done=len(destinations), total=len(destinations),
        hotels_found=len(merged), hotels_in_radius=hotels_in_radius,
    )
    return hotels
```

Export it: in `scraper/provider.py` import `fetch_hotel_list, fetch_hotel_lists` from `.scout` and add `"fetch_hotel_lists"` to `__all__`; in `scraper/__init__.py` add `fetch_hotel_lists` to the `.provider` import and to `__all__`.

- [ ] **Step 5: Run the two new test files again** (same command). Expected: all PASS. Then the whole pipeline file (`api/tests/test_booking_scraper_pipeline.py`) — still PASS (`fetch_hotel_list` itself is untouched).

- [ ] **Step 6: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add api/services/market_helpers.py scraper/scout.py scraper/provider.py scraper/__init__.py api/tests/test_market_helpers.py api/tests/test_scout_nearby.py
git commit -m @'
Scout the main destination and its nearby areas in parallel

fetch_hotel_lists runs one cached scout per destination on the deep-crawl
worker pool, merges by normalized URL (main destination first), keeps the
hotels within the owner's radius by a shared haversine, sorts nearest
first and trims to the hotel cap. A failed nearby scout becomes a
nearby_scout_failed warning; the main scout still raises ActorRunError.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---

### Task 10: `persistence.py` — `single_rooms` + `capacity` filters replace the category pool

**Files:**
- Modify: `scraper/persistence.py` (`row_filters`, two helpers, drop the `comparable_room_type_categories` import)
- Test: `api/tests/test_booking_scraper_pipeline.py` (replace the three pool tests at ~613-669, rewrite the two `filter_counts` tests at ~671-718, give two writer tests a one-adult job, append one test)

- [ ] **Step 1: Rewrite/append the tests** — in `api/tests/test_booking_scraper_pipeline.py`:

(a) Replace `test_persist_results_keeps_the_whole_comparable_category_pool`, `test_persist_results_keeps_the_comparable_pool_from_the_twin_side_too` and `test_persist_results_still_drops_rows_outside_the_requested_pool` with:

```python
def test_persist_results_keeps_every_non_single_category_for_competitor_search(monkeypatch):
    """Round 6 §3.4: the double/twin pool filter is gone — the suite stays as «Παρόμοιο»."""
    df = process_and_flatten_data(_greek_bed_variant_property(), _hotel_meta(), ScraperConfig())
    config = ScraperConfig(dry_run=True, job_type="competitor_search", room_type_category="double")

    def fail_write(*args, **kwargs):
        raise AssertionError("dry-run should not write to the database")

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fail_write)

    result = persist_results(df, config)

    assert result.rows_seen == 3
    assert result.result_summary["filter_counts"] == {
        "single_rooms": {"before": 3, "after": 3},
        "capacity": {"before": 3, "after": 3},
    }


def test_persist_results_drops_single_rooms_and_rooms_too_small_for_the_party():
    config = ScraperConfig(dry_run=True, job_type="competitor_search", adults=2)
    df = pd.DataFrame(
        [
            {"room_type_category": "single", "room_type": "Μονόκλινο", "max_persons": 1},
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 1},  # known 1 < 2 adults
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 0},  # unknown capacity stays
            {"room_type_category": None, "room_type": "Mystery", "max_persons": 3},  # unknown category stays
            {"room_type_category": "apartment", "room_type": "Διαμέρισμα", "max_persons": 4},
        ]
    )

    result = persist_results(df, config)

    assert result.rows_seen == 3
    assert result.result_summary["filter_counts"] == {
        "single_rooms": {"before": 5, "after": 4},
        "capacity": {"before": 4, "after": 3},
    }


def test_persist_results_round6_filters_are_skipped_for_room_discovery():
    config = ScraperConfig(dry_run=True, job_type="owned_property_room_discovery", adults=2)
    df = pd.DataFrame([{"room_type_category": "single", "room_type": "Μονόκλινο", "max_persons": 1}])

    result = persist_results(df, config)

    assert result.rows_seen == 1
    assert result.result_summary["filter_counts"] == {}
```

(b) Rewrite the two `filter_counts` tests (assert on keys, not the whole dict — Task 12 adds `warnings` to the summary):

```python
def test_persist_results_records_the_filter_that_emptied_the_pipeline():
    config = ScraperConfig(room_type_category="double", room_name_query="Exact catalog name", dry_run=True)
    df = pd.DataFrame([{"room_type_category": "single", "room_type": "Single", "max_persons": 1}])

    result = persist_results(df, config)

    assert result.result_summary["rows_seen"] == 1
    assert result.result_summary["filter_counts"] == {"single_rooms": {"before": 1, "after": 0}}
    assert result.result_summary["rows_written"] == 0


def test_persist_results_reports_a_later_rate_filter_after_the_round6_filters_survive():
    config = ScraperConfig(room_type_category="double", required_meal="dinner included", dry_run=True)
    df = pd.DataFrame(
        [
            {"room_type_category": "double", "meals": "Breakfast included"},
            {"room_type_category": "twin", "meals": "No meals"},
        ]
    )

    result = persist_results(df, config)

    # No max_persons column: the capacity stage records counts and keeps every row.
    assert result.result_summary["filter_counts"] == {
        "single_rooms": {"before": 2, "after": 2},
        "capacity": {"before": 2, "after": 2},
        "meal": {"before": 2, "after": 0},
    }
```

(c) `_raw_property()` is a ONE-person package (`"persons": 1`) and the default job asks for 2 adults, so the capacity filter now empties it. In `test_persist_results_writes_normalized_rates` and `test_persist_results_passes_scrape_job_id_to_normalized_writer` add `adults=1,` to the `ScraperConfig(...)` passed to `persist_results`, with the comment `# one-adult job: the fixture's 1-person package must survive the capacity filter`. (The other `_raw_property` tests assert `rows_seen == 1` on an emptied pipeline and keep passing — `rows_seen` is the count before the emptying stage.)

- [ ] **Step 2: Run the file and watch the new tests fail**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_booking_scraper_pipeline.py -q -p no:cacheprovider -k "persist_results" --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-a"
```

Expected: 5 failures (`filter_counts` still `room_type_category`, `rows_seen 2 == 3`).

- [ ] **Step 3: Implement** — in `scraper/persistence.py` remove `comparable_room_type_categories` from the `api.services.room_rates_normalizer` import, add above `persist_results`:

```python
def _drop_single_rooms(frame: pd.DataFrame) -> pd.DataFrame:
    """Round 6 §3.4: a single room never competes with a 2+ person listing.

    Every other category (double/twin/suite/apartment/studio/None) stays and
    is labelled «Παρόμοιο» at read time instead of being cut before storage.
    """
    if "room_type_category" not in frame.columns:
        return frame
    return frame[frame["room_type_category"].fillna("") != "single"].copy()


def _drop_undersized_rooms(frame: pd.DataFrame, adults: int) -> pd.DataFrame:
    """Drop rows whose KNOWN capacity (max_persons > 0) is below the party size."""
    if "max_persons" not in frame.columns:
        return frame
    capacity = pd.to_numeric(frame["max_persons"], errors="coerce").fillna(0).astype(int)
    return frame[~((capacity > 0) & (capacity < adults))].copy()
```

and replace the whole `"room_type_category"` tuple in `row_filters` with these two:

```python
        (
            "single_rooms",
            is_competitor_search,
            "Φίλτρο μονόκλινων (%s): %d -> %d εγγραφές",
            "Καμία εγγραφή έμεινε μετά το φίλτρο μονόκλινων (%s)",
            "single",
            _drop_single_rooms,
        ),
        (
            "capacity",
            is_competitor_search,
            "Φίλτρο χωρητικότητας (>= %s άτομα): %d -> %d εγγραφές",
            "Καμία εγγραφή χωράει %s άτομα",
            str(config.adults),
            lambda frame: _drop_undersized_rooms(frame, config.adults),
        ),
```

(`room_type_category` stays on every row and in `ScraperConfig`; it is no longer a persistence filter.)

- [ ] **Step 4: Run the whole pipeline file** (drop `-k`). Expected: all PASS. Also run `api/tests/test_scrape_job_service.py` — unchanged (it treats `filter_counts` as opaque data).

- [ ] **Step 5: Commit**

```powershell
cd C:\vscode_code\wt-round6-a\room_project2
git add scraper/persistence.py api/tests/test_booking_scraper_pipeline.py
git commit -m @'
Keep every non-single room that fits the party instead of the category pool

competitor_search no longer drops rows outside the double/twin pool
(10 hotels became 2 when aparthotels and studios were cut whole).
Two stages replace it: single_rooms removes room_type_category single
and capacity removes rows whose known max_persons is below the job's
adults. filter_counts reports both; room_type_category stays on every
row for the read-side same/similar label.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
'@
```

---
