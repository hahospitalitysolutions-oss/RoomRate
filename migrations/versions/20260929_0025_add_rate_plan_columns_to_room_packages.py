"""Store each package's rate-plan facts (discount, Genius, cancellation, payment).

Additive and nullable, no backfill (spec 2026-09-29 §3): rows written before
this migration stay NULL everywhere and the UI treats them as «χωρίς στοιχεία
πλάνου». The scraper fills the columns from the new transform CSV fields on
every later run.

Revision ID: 20260929_0025
Revises: 20260915_0024
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260929_0025"
down_revision: str | None = "20260915_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "roomrate_room_packages"


def upgrade() -> None:
    """Add the seven rate-plan columns to roomrate_room_packages."""
    op.add_column(_TABLE, sa.Column("discounted_price_per_night_eur", sa.Numeric(10, 2), nullable=True))
    op.add_column(_TABLE, sa.Column("discount_pct", sa.Numeric(5, 1), nullable=True))
    op.add_column(_TABLE, sa.Column("discount_label", sa.String(40), nullable=True))
    op.add_column(_TABLE, sa.Column("has_genius_discount", sa.Boolean(), nullable=True))
    op.add_column(_TABLE, sa.Column("cancellation_type", sa.String(40), nullable=True))
    op.add_column(_TABLE, sa.Column("payment_label", sa.String(60), nullable=True))
    op.add_column(_TABLE, sa.Column("rate_block_id", sa.String(80), nullable=True))


def downgrade() -> None:
    """Drop the seven rate-plan columns."""
    op.drop_column(_TABLE, "rate_block_id")
    op.drop_column(_TABLE, "payment_label")
    op.drop_column(_TABLE, "cancellation_type")
    op.drop_column(_TABLE, "has_genius_discount")
    op.drop_column(_TABLE, "discount_label")
    op.drop_column(_TABLE, "discount_pct")
    op.drop_column(_TABLE, "discounted_price_per_night_eur")
