"""Demand collection settings per owned property, and a log of the daily runs.

``roomrate_demand_settings``: whether the owner wants ferries and flights
read for their island and at which Greek hour each day; a missing row is
the defaults (both on, 08:00). ``collect_requested_at``: «Συλλογή τώρα».
``roomrate_demand_runs``: one row per kind (ferries per destination,
flights for every airport) and Greek day, claimed before a run starts, so
the worker's frequent tick collects each scope once a day. Deny-by-default
RLS like every RoomRate table (migration 0019). Additive only
(docs/DEPLOYMENT.md §4).

Revision ID: 20261012_0034
Revises: 20261011_0033
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261012_0034"
down_revision: str | None = "20261011_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create roomrate_demand_settings and roomrate_demand_runs."""
    op.create_table(
        "roomrate_demand_settings",
        sa.Column(
            "owned_property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ferries_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("flights_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("hour_local", sa.Integer(), server_default="8", nullable=False),
        sa.Column("collect_requested_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("hour_local BETWEEN 0 AND 23", name="ck_roomrate_demand_settings_hour_local"),
    )
    op.create_index("ix_roomrate_demand_settings_account_id", "roomrate_demand_settings", ["account_id"])
    op.create_table(
        "roomrate_demand_runs",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("scope", sa.String(255), primary_key=True),
        sa.Column("run_on", sa.Date(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("saved", sa.Integer()),
        sa.CheckConstraint("kind IN ('ferries', 'flights')", name="ck_roomrate_demand_runs_kind"),
    )
    op.execute('ALTER TABLE "roomrate_demand_settings" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "roomrate_demand_runs" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop roomrate_demand_runs and roomrate_demand_settings."""
    op.drop_table("roomrate_demand_runs")
    op.drop_index("ix_roomrate_demand_settings_account_id", table_name="roomrate_demand_settings")
    op.drop_table("roomrate_demand_settings")
