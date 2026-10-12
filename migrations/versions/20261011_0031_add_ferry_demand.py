"""Ferry demand: which port serves a destination, its inbound routes, daily sailings.

The first outside demand signal for the price advisor. Ferry sailings INTO an
island destination are read daily from Ferryhopper's MCP server
(api/services/ferry_demand_service.py):

* ``roomrate_ferry_destination_profiles``: one row per canonical destination,
  the island port found from the owners' coordinates (shared by accounts);
* ``roomrate_ferry_routes``: the direct routes into that port;
* ``roomrate_ferry_observations``: per route, travel date and observation
  day, the sailings, how many had no seats left, the min/median fare.

Deny-by-default RLS like every RoomRate table (migration 0019): only FastAPI
touches them. Additive only (docs/DEPLOYMENT.md §4).

Revision ID: 20261011_0031
Revises: 20260930_0030
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261011_0031"
down_revision: str | None = "20260930_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "roomrate_ferry_destination_profiles",
    "roomrate_ferry_routes",
    "roomrate_ferry_observations",
)


def upgrade() -> None:
    """Create the ferry profile, route and observation tables."""
    op.create_table(
        "roomrate_ferry_destination_profiles",
        sa.Column("canonical_destination", sa.String(255), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("island_port", sa.String(255)),
        sa.Column("island_port_code", sa.String(100)),
        sa.Column("port_distance_km", sa.Numeric(7, 2)),
        sa.Column("matched_by", sa.String(20)),
        sa.Column("detail", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ferry', 'no_ferry', 'error')",
            name="ck_roomrate_ferry_destination_profiles_status",
        ),
    )
    op.create_table(
        "roomrate_ferry_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "canonical_destination",
            sa.String(255),
            sa.ForeignKey(
                "roomrate_ferry_destination_profiles.canonical_destination",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("origin_port", sa.String(255), nullable=False),
        sa.Column("destination_port", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), server_default="auto", nullable=False),
        sa.Column("sample_sailings", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "canonical_destination",
            "origin_port",
            "destination_port",
            name="uq_roomrate_ferry_routes_destination_ports",
        ),
        sa.CheckConstraint(
            "status IN ('auto', 'confirmed', 'disabled')",
            name="ck_roomrate_ferry_routes_status",
        ),
    )
    op.create_table(
        "roomrate_ferry_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_ferry_routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("travel_date", sa.Date(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sailings", sa.Integer(), nullable=False),
        sa.Column("availability_known", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sold_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("min_price_eur", sa.Numeric(10, 2)),
        sa.Column("median_price_eur", sa.Numeric(10, 2)),
        sa.Column("operators", sa.Integer(), server_default="0", nullable=False),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column("parser_version", sa.String(20), nullable=False),
        sa.UniqueConstraint(
            "route_id",
            "travel_date",
            "observed_on",
            name="uq_roomrate_ferry_observations_route_day",
        ),
    )
    op.create_index(
        "ix_roomrate_ferry_observations_observed_on",
        "roomrate_ferry_observations",
        ["observed_on"],
    )
    for table_name in _TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop the ferry demand tables."""
    op.drop_index("ix_roomrate_ferry_observations_observed_on", table_name="roomrate_ferry_observations")
    for table_name in reversed(_TABLES):
        op.drop_table(table_name)
