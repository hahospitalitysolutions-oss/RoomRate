"""Add durable audit/cache records for price recommendations.

Revision ID: 20260726_0020
Revises: 20260726_0019
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260726_0020"
down_revision: str | None = "20260726_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roomrate_price_recommendation_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owned_property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cache_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owned_property_id"],
            ["roomrate_owned_properties.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_roomrate_price_audits_account_created",
        "roomrate_price_recommendation_audits",
        ["account_id", "created_at"],
    )
    op.create_index(
        "ix_roomrate_price_audits_cache",
        "roomrate_price_recommendation_audits",
        ["account_id", "request_hash", "cache_expires_at"],
    )
    op.execute(
        'ALTER TABLE "roomrate_price_recommendation_audits" ENABLE ROW LEVEL SECURITY'
    )


def downgrade() -> None:
    op.drop_index(
        "ix_roomrate_price_audits_cache",
        table_name="roomrate_price_recommendation_audits",
    )
    op.drop_index(
        "ix_roomrate_price_audits_account_created",
        table_name="roomrate_price_recommendation_audits",
    )
    op.drop_table("roomrate_price_recommendation_audits")
