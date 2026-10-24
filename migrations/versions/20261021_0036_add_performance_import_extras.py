"""«Απόδοση»: keep a template's «Δωμάτια» and «Κλειστές περίοδοι» with the upload until commit.

``roomrate_performance_imports.extras``: ``{"rooms": [...], "closures": [...]}``
as read from the template, applied on commit once their room labels are
mapped. Additive only (docs/DEPLOYMENT.md §4); migration 0035 is already
applied on shared databases, so it is extended here rather than edited.

Revision ID: 20261021_0036
Revises: 20261020_0035
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261021_0036"
down_revision: str | None = "20261020_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "roomrate_performance_imports",
        sa.Column("extras", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("roomrate_performance_imports", "extras")
