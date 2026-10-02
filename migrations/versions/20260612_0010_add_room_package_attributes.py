"""Add extracted room attributes to room packages and expose them in the view.

Adds a nullable JSONB ``room_attributes`` column to ``roomrate_room_packages``
(capacity/view/balcony/size/bed facts extracted at write time by
``api.services.room_matching``) and recreates ``roomrate_latest_room_rates``
from the shared V2 definition so normalized reads can use the attributes for
match scoring. ``roomrate_competitor_markers`` selects from base tables only,
so it is intentionally left untouched.

View SQL lives in ``api/db_views.py`` (single source of truth).

Revision ID: 20260612_0010
Revises: 20260612_0009
Create Date: 2026-06-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from api.db_views import LATEST_ROOM_RATES_V1, LATEST_ROOM_RATES_V2, LATEST_ROOM_RATES_VIEW_NAME


revision: str = "20260612_0010"
down_revision: str | None = "20260612_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roomrate_room_packages", sa.Column("room_attributes", JSONB(), nullable=True))

    op.execute(f"DROP VIEW IF EXISTS {LATEST_ROOM_RATES_VIEW_NAME}")
    op.execute(LATEST_ROOM_RATES_V2)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {LATEST_ROOM_RATES_VIEW_NAME}")
    op.execute(LATEST_ROOM_RATES_V1)

    op.drop_column("roomrate_room_packages", "room_attributes")
