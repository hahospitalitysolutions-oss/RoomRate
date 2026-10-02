"""Add one-time WebSocket auth tickets.

``/ws/alerts`` previously authenticated browsers with the full Supabase JWT in
the URL query string, which leaks into server/proxy logs and browser history.
Browsers now mint a short-lived one-time ticket over authenticated REST and
hand THAT to the WebSocket handshake. Only the SHA-256 of the ticket is
stored; redemption flips ``used_at`` atomically so each ticket works once.

The table is PostgreSQL-backed (not in-process memory) so the handshake may
land on a different API process than the mint request.

Revision ID: 20260723_0018
Revises: 20260723_0017
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260723_0018"
down_revision: str | None = "20260723_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roomrate_ws_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        # SHA-256 hex digest of the raw ticket; the raw value is never stored.
        sa.Column("ticket_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
    )
    # Backs the opportunistic DELETE of long-expired rows in issue().
    op.create_index("ix_roomrate_ws_tickets_expires_at", "roomrate_ws_tickets", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_roomrate_ws_tickets_expires_at", table_name="roomrate_ws_tickets")
    op.drop_table("roomrate_ws_tickets")
