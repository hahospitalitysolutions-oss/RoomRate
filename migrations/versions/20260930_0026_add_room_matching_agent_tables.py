"""Store AI room-match estimates and a light per-run agent audit.

Spec 2026-09-29 Α.3. Two new tables:

* ``roomrate_room_matches`` — one row per (job, owned room, competitor room)
  scored by the matching agent; the unique constraint is the replace-upsert
  identity (a re-run deletes and re-inserts the whole (job, owned room) scope).
* ``roomrate_agent_runs`` — one ok|error|skipped row per agent execution
  (a skip's reason goes in ``error_message``), enough to answer «γιατί δεν
  βγήκε AI εκτίμηση» and to enforce the daily run quota (skips excluded).
  ``status`` is a plain String(10) without a CHECK, so all three fit.

The matches CASCADE with their job (they describe that job's packages); the
audit rows keep SET NULL so the history survives job deletion. Both tables
get deny-by-default RLS, the posture migration 0019 set for every RoomRate
table (all reads flow through FastAPI, never PostgREST).

Revision ID: 20260930_0026
Revises: 20260929_0025
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260930_0026"
down_revision: str | None = "20260929_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the room-match and agent-run tables."""
    op.create_table(
        "roomrate_room_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scrape_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_scrape_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owned_room_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_owned_property_room_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("room_type", sa.String(255), nullable=False),
        sa.Column("score", sa.Numeric(5, 1), nullable=False),
        sa.Column("category_match", sa.String(10), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "scrape_job_id",
            "owned_room_type_id",
            "property_id",
            "room_type",
            name="uq_roomrate_room_matches_scope",
        ),
    )

    op.create_table(
        "roomrate_agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scrape_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_scrape_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "kind", sa.String(30), nullable=False, server_default=sa.text("'room_matching'")
        ),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_roomrate_agent_runs_account_created",
        "roomrate_agent_runs",
        ["account_id", "created_at"],
    )

    # Deny-by-default RLS, same posture as migration 0019 / 0020: the anon
    # Supabase key must never read these rows over PostgREST.
    op.execute('ALTER TABLE "roomrate_room_matches" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "roomrate_agent_runs" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop the agent-run and room-match tables."""
    op.drop_index("ix_roomrate_agent_runs_account_created", table_name="roomrate_agent_runs")
    op.drop_table("roomrate_agent_runs")
    op.drop_table("roomrate_room_matches")
