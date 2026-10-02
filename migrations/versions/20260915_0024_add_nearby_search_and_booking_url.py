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
