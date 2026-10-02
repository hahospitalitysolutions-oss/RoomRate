"""Add durable retry state to scrape jobs.

Revision ID: 20260722_0016
Revises: 20260711_0015
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0016"
down_revision: str | None = "20260711_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist attempt counters and the earliest safe retry time."""
    # Παλιές εγκαταστάσεις που μετονόμασαν τον πίνακα κράτησαν ενίοτε το
    # legacy όνομα του index. Επαναφέρουμε το production invariant χωρίς να
    # δημιουργούμε διπλό index σε fresh databases.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('ix_roomrate_rate_observations_property_observed') IS NULL THEN
                IF to_regclass('ix_roompulse_rate_observations_property_observed') IS NOT NULL THEN
                    ALTER INDEX ix_roompulse_rate_observations_property_observed
                        RENAME TO ix_roomrate_rate_observations_property_observed;
                ELSE
                    CREATE INDEX ix_roomrate_rate_observations_property_observed
                        ON roomrate_rate_observations (property_id, observed_at);
                END IF;
            END IF;
        END $$;
        """
    )
    op.add_column(
        "roomrate_scrape_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "roomrate_scrape_jobs",
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column(
        "roomrate_scrape_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_roomrate_scrape_jobs_attempt_count_non_negative",
        "roomrate_scrape_jobs",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_roomrate_scrape_jobs_max_attempts_range",
        "roomrate_scrape_jobs",
        "max_attempts BETWEEN 1 AND 10",
    )
    op.create_index(
        "ix_roomrate_scrape_jobs_queued_ready",
        "roomrate_scrape_jobs",
        ["next_attempt_at", "requested_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    """Remove durable retry state."""
    op.drop_index("ix_roomrate_scrape_jobs_queued_ready", table_name="roomrate_scrape_jobs")
    op.drop_constraint(
        "ck_roomrate_scrape_jobs_max_attempts_range",
        "roomrate_scrape_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_roomrate_scrape_jobs_attempt_count_non_negative",
        "roomrate_scrape_jobs",
        type_="check",
    )
    op.drop_column("roomrate_scrape_jobs", "next_attempt_at")
    op.drop_column("roomrate_scrape_jobs", "max_attempts")
    op.drop_column("roomrate_scrape_jobs", "attempt_count")
