"""Persist the scraper's versioned result summary on completed jobs.

Revision ID: 20260828_0023
Revises: 20260823_0022
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260828_0023"
down_revision: str | None = "20260823_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable summary; existing and malformed-output jobs remain NULL."""
    op.add_column(
        "roomrate_scrape_jobs",
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Remove the result summary column."""
    op.drop_column("roomrate_scrape_jobs", "result_summary")
