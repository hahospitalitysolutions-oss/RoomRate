"""Add a unique event_key to notifications for idempotent alert inserts.

Duplicate price-change alerts could previously be raised when the same logical
price move was evaluated more than once (job re-runs after sweeper requeues,
racing evaluators, same-day re-scrapes observing the identical transition).
``event_key`` carries a deterministic identity of the event and the partial
unique index lets ``AlertsRepository.insert_notifications`` use
``ON CONFLICT DO NOTHING`` so the duplicate is silently dropped.

Existing rows keep ``event_key`` NULL and are exempt from the index, so no
backfill is required and non-deduplicated notification types (e.g.
``schedule_disabled``) simply never set a key.

Revision ID: 20260723_0017
Revises: 20260722_0016
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_0017"
down_revision: str | None = "20260722_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roomrate_notifications", sa.Column("event_key", sa.Text(), nullable=True))
    op.create_index(
        "uq_roomrate_notifications_account_event_key",
        "roomrate_notifications",
        ["account_id", "event_key"],
        unique=True,
        postgresql_where=sa.text("event_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_roomrate_notifications_account_event_key", table_name="roomrate_notifications"
    )
    op.drop_column("roomrate_notifications", "event_key")
