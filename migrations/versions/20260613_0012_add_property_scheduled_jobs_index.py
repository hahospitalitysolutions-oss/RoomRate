"""Index scheduled scrape jobs by owned property for rotation fairness.

The scheduler's ``list_schedulable_properties`` orders properties by each
property's most recent *scheduled* scrape job (least-recently-scheduled
first) via a correlated ``max(requested_at)`` subquery. Without an index on
``owned_property_id`` that subquery sequentially scans the whole jobs table
once per schedulable property per due account per tick. This partial index
covers exactly the rows the subquery touches (``filters_payload->>'scheduled'
= 'true'``) and stores ``requested_at DESC`` so the max resolves from the
index alone.

Revision ID: 20260613_0012
Revises: 20260613_0011
Create Date: 2026-06-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260613_0012"
down_revision: str | None = "20260613_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_roomrate_scrape_jobs_property_scheduled_requested",
        "roomrate_scrape_jobs",
        ["owned_property_id", sa.text("requested_at DESC")],
        postgresql_where=sa.text("(filters_payload->>'scheduled') = 'true'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_roomrate_scrape_jobs_property_scheduled_requested",
        table_name="roomrate_scrape_jobs",
    )
