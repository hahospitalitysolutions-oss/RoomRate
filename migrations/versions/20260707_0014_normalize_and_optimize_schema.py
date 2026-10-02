"""Normalize the scrape schema and align every index with a real query.

Driven by a full audit of the live schema against the actual query surface
(api/repositories/*, api/db_views.py, api/scheduler.py):

* ``roomrate_scrape_jobs.scheduled`` becomes a real boolean column (1NF: the
  flag previously lived inside the ``filters_payload`` JSONB and was queried
  with ``filters_payload->>'scheduled'``). Existing rows are backfilled and
  the key is stripped from the JSONB so there is exactly one source of truth.
  The rotation-fairness partial index is rebuilt on the new column.
* ``roomrate_scrape_runs`` loses ``nights`` and ``guests`` (3NF: both are
  derivable — ``check_out - check_in`` and ``adults + children`` — and storing
  them allowed drift). The views expose computed values instead, so every
  reader keeps its column set.
* Both market views partition on ``canonical_destination`` directly instead of
  ``COALESCE(canonical_destination, destination)``. The column is NOT NULL
  since 20260521_0006, and the expression blocked predicate pushdown into the
  window subquery — every view query ranked ALL of an account's completed runs
  instead of only the requested market's.
* Index realignment. Dropped because no query can use them (verified against
  every SQL statement in the app; several were left-prefix duplicates):
  packages price/room_type/room_type_category, runs market_dates (destination
  variant) / status_started, properties market/coordinates, observations
  review, jobs account_status/requested/account_canonical_status/type_status.
  Added to match real queries: jobs (account_id, requested_at DESC) for the
  job list + daily quota, a partial queued-executor index, and two
  notification-feed indexes replacing the one that matched neither the read
  nor the unread query shape exactly.
* ``scout_cache`` (owned by the scraper, guarded with ``to_regclass``) gets
  real ``date``/``timestamptz`` column types. This also fixes a live bug: the
  scheduler's nightly cleanup compared the old TEXT ``cached_at`` against
  ``now() - make_interval(...)`` which raises ``operator does not exist:
  text < timestamp with time zone`` — the cleanup job has never deleted a row.

Revision ID: 20260707_0014
Revises: 20260614_0013
Create Date: 2026-07-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from api.db_views import (
    COMPETITOR_MARKERS_V1,
    COMPETITOR_MARKERS_V2,
    LATEST_ROOM_RATES_V2,
    LATEST_ROOM_RATES_V3,
)


revision: str = "20260707_0014"
down_revision: str | None = "20260614_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- scrape_jobs.scheduled: JSONB flag -> real column -------------------
    op.add_column(
        "roomrate_scrape_jobs",
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE roomrate_scrape_jobs SET scheduled = true "
        "WHERE filters_payload->>'scheduled' = 'true'"
    )
    op.execute(
        "UPDATE roomrate_scrape_jobs SET filters_payload = filters_payload - 'scheduled' "
        "WHERE filters_payload ? 'scheduled'"
    )
    op.execute("DROP INDEX IF EXISTS ix_roomrate_scrape_jobs_property_scheduled_requested")
    op.execute(
        "CREATE INDEX ix_roomrate_scrape_jobs_property_scheduled_requested "
        "ON roomrate_scrape_jobs (owned_property_id, requested_at DESC) WHERE scheduled"
    )

    # --- scrape_jobs index realignment --------------------------------------
    # (account_id, requested_at DESC) serves list_jobs and the daily-quota
    # count; the active-jobs count is covered by the partial unique index
    # uq_roomrate_scrape_jobs_active_market (leading account_id, predicate on
    # the active statuses). The queued partial serves the executor's global
    # oldest-first scan without indexing the ever-growing terminal rows.
    op.execute("DROP INDEX IF EXISTS ix_roompulse_scrape_jobs_account_status")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_scrape_jobs_requested")
    op.execute("DROP INDEX IF EXISTS ix_roomrate_scrape_jobs_account_canonical_status")
    op.execute("DROP INDEX IF EXISTS ix_roomrate_scrape_jobs_type_status")
    op.execute(
        "CREATE INDEX ix_roomrate_scrape_jobs_account_requested "
        "ON roomrate_scrape_jobs (account_id, requested_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_roomrate_scrape_jobs_queued_requested "
        "ON roomrate_scrape_jobs (requested_at) WHERE status = 'queued'"
    )

    # --- drop indexes no query can use --------------------------------------
    op.execute("DROP INDEX IF EXISTS ix_roompulse_room_packages_price")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_room_packages_room_type")
    # Left-prefix duplicate of ix_roomrate_room_packages_category_price.
    op.execute("DROP INDEX IF EXISTS ix_roomrate_room_packages_room_type_category")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_scrape_runs_market_dates")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_scrape_runs_status_started")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_properties_market")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_properties_coordinates")
    op.execute("DROP INDEX IF EXISTS ix_roompulse_rate_observations_review")

    # --- notification feed indexes ------------------------------------------
    # The old (account_id, is_read, created_at DESC) index could not return
    # the main feed (both read states) in sorted order. Replace with one index
    # per query shape: full feed, and a partial for unread feed + count.
    op.execute("DROP INDEX IF EXISTS ix_roomrate_notifications_account_unread")
    op.execute(
        "CREATE INDEX ix_roomrate_notifications_account_created "
        "ON roomrate_notifications (account_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_roomrate_notifications_account_unread_created "
        "ON roomrate_notifications (account_id, created_at DESC, id DESC) "
        "WHERE is_read = false"
    )

    # --- scrape_runs: drop derivable nights/guests, rebuild views ------------
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")
    op.drop_column("roomrate_scrape_runs", "nights")
    op.drop_column("roomrate_scrape_runs", "guests")
    op.execute(LATEST_ROOM_RATES_V3)
    op.execute(COMPETITOR_MARKERS_V2)

    # --- scout_cache type hardening (table owned by the scraper) ------------
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                ALTER TABLE scout_cache
                    ALTER COLUMN check_in TYPE date USING check_in::date,
                    ALTER COLUMN check_out TYPE date USING check_out::date,
                    ALTER COLUMN cached_at TYPE timestamptz USING cached_at::timestamptz;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # --- scout_cache back to text (ISO strings) ------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                ALTER TABLE scout_cache
                    ALTER COLUMN check_in TYPE text USING check_in::text,
                    ALTER COLUMN check_out TYPE text USING check_out::text,
                    ALTER COLUMN cached_at TYPE text
                        USING to_char(cached_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
            END IF;
        END $$;
        """
    )

    # --- restore stored nights/guests and the V2/V1 views --------------------
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")
    op.add_column("roomrate_scrape_runs", sa.Column("nights", sa.Integer(), nullable=True))
    op.add_column("roomrate_scrape_runs", sa.Column("guests", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE roomrate_scrape_runs "
        "SET nights = (check_out - check_in), guests = (adults + children)"
    )
    op.alter_column("roomrate_scrape_runs", "nights", nullable=False)
    op.alter_column("roomrate_scrape_runs", "guests", nullable=False)
    op.create_check_constraint(
        "ck_roompulse_scrape_runs_nights_positive", "roomrate_scrape_runs", "nights > 0"
    )
    op.create_check_constraint(
        "ck_roompulse_scrape_runs_guests_positive", "roomrate_scrape_runs", "guests > 0"
    )
    op.execute(LATEST_ROOM_RATES_V2)
    op.execute(COMPETITOR_MARKERS_V1)

    # --- notifications ---------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS ix_roomrate_notifications_account_created")
    op.execute("DROP INDEX IF EXISTS ix_roomrate_notifications_account_unread_created")
    op.execute(
        "CREATE INDEX ix_roomrate_notifications_account_unread "
        "ON roomrate_notifications (account_id, is_read, created_at DESC)"
    )

    # --- recreate the dropped legacy indexes -----------------------------------
    op.execute(
        "CREATE INDEX ix_roompulse_room_packages_price "
        "ON roomrate_room_packages (price_per_night_eur)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_room_packages_room_type "
        "ON roomrate_room_packages (room_type)"
    )
    op.execute(
        "CREATE INDEX ix_roomrate_room_packages_room_type_category "
        "ON roomrate_room_packages (room_type_category)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_scrape_runs_market_dates "
        "ON roomrate_scrape_runs (account_id, destination, check_in, check_out, adults, children, rooms)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_scrape_runs_status_started "
        "ON roomrate_scrape_runs (status, started_at)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_properties_market "
        "ON roomrate_properties (city, canonical_name)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_properties_coordinates "
        "ON roomrate_properties (latitude, longitude)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_rate_observations_review "
        "ON roomrate_rate_observations (review_score, review_count)"
    )

    # --- scrape_jobs indexes ----------------------------------------------------
    op.execute("DROP INDEX IF EXISTS ix_roomrate_scrape_jobs_account_requested")
    op.execute("DROP INDEX IF EXISTS ix_roomrate_scrape_jobs_queued_requested")
    op.execute(
        "CREATE INDEX ix_roompulse_scrape_jobs_account_status "
        "ON roomrate_scrape_jobs (account_id, status)"
    )
    op.execute(
        "CREATE INDEX ix_roompulse_scrape_jobs_requested "
        "ON roomrate_scrape_jobs (requested_at)"
    )
    op.execute(
        "CREATE INDEX ix_roomrate_scrape_jobs_account_canonical_status "
        "ON roomrate_scrape_jobs (account_id, canonical_destination, status)"
    )
    op.execute(
        "CREATE INDEX ix_roomrate_scrape_jobs_type_status "
        "ON roomrate_scrape_jobs (job_type, status)"
    )

    # --- scheduled column back into filters_payload ------------------------------
    op.execute("DROP INDEX IF EXISTS ix_roomrate_scrape_jobs_property_scheduled_requested")
    op.execute(
        "CREATE INDEX ix_roomrate_scrape_jobs_property_scheduled_requested "
        "ON roomrate_scrape_jobs (owned_property_id, requested_at DESC) "
        "WHERE (filters_payload->>'scheduled') = 'true'"
    )
    op.execute(
        "UPDATE roomrate_scrape_jobs "
        "SET filters_payload = COALESCE(filters_payload, '{}'::jsonb) || '{\"scheduled\": true}'::jsonb "
        "WHERE scheduled"
    )
    op.drop_column("roomrate_scrape_jobs", "scheduled")
