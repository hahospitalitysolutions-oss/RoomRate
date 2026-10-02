"""Optimize scrape-job execution and hot read paths.

Adds atomic-claim columns (``claimed_by``, ``heartbeat_at``) to scrape jobs,
a write-time amenity cache on properties, a partial unique index preventing
duplicate active jobs per market, and covering indexes for price history and
package-category reads. Also indexes the scraper-owned ``scout_cache`` table
when it exists.

Note: ``ix_roomrate_scrape_jobs_account_status (account_id, status)`` already
exists from revision 20260503_0002, so it is intentionally NOT recreated here.

Revision ID: 20260612_0009
Revises: 20260524_0008
Create Date: 2026-06-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260612_0009"
down_revision: str | None = "20260524_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Atomic claim + liveness tracking for scrape jobs.
    op.add_column("roomrate_scrape_jobs", sa.Column("claimed_by", sa.String(length=255), nullable=True))
    op.add_column("roomrate_scrape_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    # 2) Write-time amenity cache used by normalized read paths.
    op.add_column("roomrate_properties", sa.Column("amenities_cached", sa.Text(), nullable=True))

    # 3) Data cleanup BEFORE the partial unique index: keep only the newest
    #    active job per market scope, mark older duplicates failed.
    op.execute(
        """
        UPDATE roomrate_scrape_jobs
        SET
            status = 'failed',
            error_message = 'superseded duplicate (migration 0009)',
            finished_at = now(),
            updated_at = now()
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY
                            account_id,
                            canonical_destination,
                            check_in,
                            check_out,
                            adults,
                            children,
                            rooms
                        ORDER BY requested_at DESC
                    ) AS rn
                FROM roomrate_scrape_jobs
                WHERE status IN ('queued', 'running')
            ) t
            WHERE t.rn > 1
        )
        """
    )

    # 4) At most one active (queued/running) job per account + market scope.
    op.create_index(
        "uq_roomrate_scrape_jobs_active_market",
        "roomrate_scrape_jobs",
        ["account_id", "canonical_destination", "check_in", "check_out", "adults", "children", "rooms"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    # 5) Category + price scans (cheapest-by-category queries).
    op.create_index(
        "ix_roomrate_room_packages_category_price",
        "roomrate_room_packages",
        ["room_type_category", "price_per_night_eur"],
    )

    # 6) Price-history run lookups, newest finished runs first.
    op.create_index(
        "ix_roomrate_scrape_runs_history",
        "roomrate_scrape_runs",
        ["account_id", "canonical_destination", "check_in", "check_out", "status", sa.text("finished_at DESC")],
    )

    # 7) ix_roomrate_scrape_jobs_account_status: skipped — already created in
    #    revision 20260503_0002 with the identical (account_id, status) shape.

    # 8) scout_cache is created by the scraper itself (not Alembic), so guard
    #    the index for fresh databases where the table does not exist yet.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                CREATE INDEX IF NOT EXISTS ix_scout_cache_lookup
                    ON scout_cache (destination, check_in, check_out, cached_at);
            END IF;
        END
        $$;
        """
    )

    # Backfill the amenity cache from the bridge table for existing rows.
    op.execute(
        """
        UPDATE roomrate_properties p
        SET amenities_cached = sub.agg
        FROM (
            SELECT pa.property_id, string_agg(a.name, '|' ORDER BY a.name) AS agg
            FROM roomrate_property_amenities pa
            JOIN roomrate_amenities a ON a.id = pa.amenity_id
            GROUP BY pa.property_id
        ) sub
        WHERE sub.property_id = p.id
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scout_cache_lookup")
    op.drop_index("ix_roomrate_scrape_runs_history", table_name="roomrate_scrape_runs")
    op.drop_index("ix_roomrate_room_packages_category_price", table_name="roomrate_room_packages")
    op.drop_index("uq_roomrate_scrape_jobs_active_market", table_name="roomrate_scrape_jobs")
    op.drop_column("roomrate_properties", "amenities_cached")
    op.drop_column("roomrate_scrape_jobs", "heartbeat_at")
    op.drop_column("roomrate_scrape_jobs", "claimed_by")
