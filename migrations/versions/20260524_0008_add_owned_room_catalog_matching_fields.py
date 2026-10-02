"""Add owned-property room catalog matching fields.

Revision ID: 20260524_0008
Revises: 20260524_0007
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260524_0008"
down_revision: str | None = "20260524_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roomrate_owned_property_room_types", sa.Column("sample_meals", sa.Text(), nullable=True))
    op.add_column(
        "roomrate_owned_property_room_types",
        sa.Column("sample_free_cancellation", sa.String(length=100), nullable=True),
    )
    op.add_column("roomrate_owned_property_room_types", sa.Column("sample_facilities", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("roomrate_owned_property_room_types", "sample_facilities")
    op.drop_column("roomrate_owned_property_room_types", "sample_free_cancellation")
    op.drop_column("roomrate_owned_property_room_types", "sample_meals")
