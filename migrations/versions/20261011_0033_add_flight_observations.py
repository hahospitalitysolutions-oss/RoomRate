"""Flight demand: scheduled arrivals at island airports, one look per window and day.

From AeroDataBox (api/services/flight_demand_service.py). Airport-level,
shared by every account on the island. ``calls`` counts the calls behind a
row (a repeat the same day overwrites it): the monthly unit budget sums them
over the month. Deny-by-default RLS like every RoomRate table (migration
0019). Additive only (docs/DEPLOYMENT.md §4).

Revision ID: 20261011_0033
Revises: 20261011_0032
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261011_0033"
down_revision: str | None = "20261011_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create roomrate_flight_observations."""
    op.create_table(
        "roomrate_flight_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("airport_iata", sa.String(3), nullable=False),
        sa.Column("travel_date", sa.Date(), nullable=False),
        sa.Column("window_start_hour", sa.Integer(), nullable=False),
        sa.Column("window_end_hour", sa.Integer(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("arrivals", sa.Integer(), nullable=False),
        sa.Column("airlines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("origins", postgresql.JSONB()),
        sa.Column("calls", sa.Integer(), server_default="1", nullable=False),
        sa.Column("units_remaining", sa.Integer()),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column("parser_version", sa.String(20), nullable=False),
        sa.UniqueConstraint(
            "airport_iata",
            "travel_date",
            "window_start_hour",
            "observed_on",
            name="uq_roomrate_flight_observations_window_day",
        ),
        sa.CheckConstraint(
            "window_start_hour >= 0 AND window_end_hour <= 24 AND window_start_hour < window_end_hour",
            name="ck_roomrate_flight_observations_window",
        ),
    )
    op.create_index(
        "ix_roomrate_flight_observations_observed_at",
        "roomrate_flight_observations",
        ["observed_at"],
    )
    op.execute('ALTER TABLE "roomrate_flight_observations" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop roomrate_flight_observations."""
    op.drop_index("ix_roomrate_flight_observations_observed_at", table_name="roomrate_flight_observations")
    op.drop_table("roomrate_flight_observations")
