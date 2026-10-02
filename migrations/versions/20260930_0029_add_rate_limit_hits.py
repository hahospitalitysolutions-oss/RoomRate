"""Count rate-limited POSTs in PostgreSQL, shared by every API process.

The limiter used to count per process, so behind N uvicorn workers or API
replicas a caller got up to N x RATE_LIMIT_PER_MINUTE expensive POSTs (scrape
jobs, LLM calls). One row per allowed hit, keyed by a SHA-256 of the caller
key (never a raw IP, token or account id); a check prunes its key's hits
older than the window, counts the rest and inserts only when they fit, under
a per-key advisory lock. ``hit_at`` alone is indexed too, for the sweep that
drops every expired row. Deny-by-default RLS like every RoomRate table
(migration 0019): only FastAPI touches it.

Revision ID: 20260930_0029
Revises: 20260930_0028
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260930_0029"
down_revision: str | None = "20260930_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the shared rate-limit hit log."""
    op.create_table(
        "roomrate_rate_limit_hits",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column(
            "hit_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_roomrate_rate_limit_hits_key_hit",
        "roomrate_rate_limit_hits",
        ["key_hash", "hit_at"],
    )
    op.create_index(
        "ix_roomrate_rate_limit_hits_hit_at",
        "roomrate_rate_limit_hits",
        ["hit_at"],
    )
    op.execute('ALTER TABLE "roomrate_rate_limit_hits" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop the shared rate-limit hit log."""
    op.drop_index("ix_roomrate_rate_limit_hits_hit_at", table_name="roomrate_rate_limit_hits")
    op.drop_index("ix_roomrate_rate_limit_hits_key_hit", table_name="roomrate_rate_limit_hits")
    op.drop_table("roomrate_rate_limit_hits")
