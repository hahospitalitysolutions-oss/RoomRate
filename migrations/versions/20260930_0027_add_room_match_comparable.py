"""Let the matching agent decide which competitor rooms are comparable.

The owner decided the comparison set is chosen by the agent alone: every
scored room carries the agent's yes/no «is this a real substitute for the
reference room». Nullable: rows written before this column existed read as
comparable when their score is at least 50 (the read side's COALESCE).

Revision ID: 20260930_0027
Revises: 20260930_0026
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260930_0027"
down_revision: str | None = "20260930_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roomrate_room_matches", sa.Column("comparable", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("roomrate_room_matches", "comparable")
