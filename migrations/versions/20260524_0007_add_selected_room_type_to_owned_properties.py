"""Add selected baseline room type to owned properties.

Revision ID: 20260524_0007
Revises: 20260521_0006
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260524_0007"
down_revision: str | None = "20260521_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "roomrate_owned_properties",
        sa.Column("selected_room_type_category", sa.String(length=50), nullable=True),
    )
    op.execute(
        """
        UPDATE roomrate_owned_properties op
        SET selected_room_type_category = rt.room_type_category
        FROM (
            SELECT DISTINCT ON (account_id, owned_property_id)
                account_id,
                owned_property_id,
                room_type_category
            FROM roomrate_owned_property_room_types
            WHERE is_active = true
            ORDER BY account_id, owned_property_id, updated_at DESC, created_at DESC
        ) rt
        WHERE rt.account_id = op.account_id
          AND rt.owned_property_id = op.id
          AND op.selected_room_type_category IS NULL
        """
    )
    op.create_index(
        "ix_roomrate_owned_properties_selected_room_type",
        "roomrate_owned_properties",
        ["account_id", "selected_room_type_category"],
    )


def downgrade() -> None:
    op.drop_index("ix_roomrate_owned_properties_selected_room_type", table_name="roomrate_owned_properties")
    op.drop_column("roomrate_owned_properties", "selected_room_type_category")
