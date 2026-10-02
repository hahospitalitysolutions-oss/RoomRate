"""Add per-account scrape schedule configuration.

Creates ``roomrate_schedule_configs``: one row per account describing the
recurring competitor-scrape cadence (frequency/hour-of-day), the booking
window (lead days/nights/occupancy), and the circuit-breaker state
(``consecutive_failures`` — the scheduler disables a schedule after repeated
scheduled-job failures). The scheduler tick reads due rows via ``enabled``,
``last_run_at`` and ``hour_utc``; ``account_id`` is UNIQUE so the table can be
upserted with ``ON CONFLICT (account_id)``.

Revision ID: 20260613_0011
Revises: 20260612_0010
Create Date: 2026-06-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260613_0011"
down_revision: str | None = "20260612_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roomrate_schedule_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("frequency_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("hour_utc", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("lead_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("nights", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("adults", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("children", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rooms", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        # One schedule per tenant; also the upsert conflict target.
        sa.UniqueConstraint("account_id", name="uq_roomrate_schedule_configs_account"),
        sa.CheckConstraint("frequency_hours > 0", name="ck_roomrate_schedule_configs_frequency_hours"),
        sa.CheckConstraint("hour_utc BETWEEN 0 AND 23", name="ck_roomrate_schedule_configs_hour_utc"),
        sa.CheckConstraint("lead_days >= 0", name="ck_roomrate_schedule_configs_lead_days"),
        sa.CheckConstraint("nights > 0", name="ck_roomrate_schedule_configs_nights"),
        sa.CheckConstraint("adults >= 1", name="ck_roomrate_schedule_configs_adults"),
        sa.CheckConstraint("children >= 0", name="ck_roomrate_schedule_configs_children"),
        sa.CheckConstraint("rooms >= 1", name="ck_roomrate_schedule_configs_rooms"),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_roomrate_schedule_configs_failures"),
    )


def downgrade() -> None:
    op.drop_table("roomrate_schedule_configs")
