"""Hold one in-flight room-matching run per (job, owned room) scope.

Review fix 2026-09-30: the map's automatic run could start while the
post-scrape run of the same (job, selected room) was still waiting on the
model — two paid runs racing on the same rows. A lease row names the run
that is scoring a scope right now; a second caller waits for it (the
endpoint) or skips (the post-scrape hook) instead of paying for a duplicate.

``holder`` is the claiming run's token, so only that run releases its lease.
A lease older than the longest possible run is stale (the process died
mid-run, e.g. a worker restart) and the next claim takes it over, so a lost
process never blocks a scope for good. Deny-by-default RLS like every
RoomRate table (migration 0019): all access goes through FastAPI.

Revision ID: 20260930_0028
Revises: 20260930_0027
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260930_0028"
down_revision: str | None = "20260930_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the agent-run lease table."""
    op.create_table(
        "roomrate_agent_leases",
        sa.Column("kind", sa.String(30), nullable=False),
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
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("holder", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "kind", "scrape_job_id", "owned_room_type_id", name="pk_roomrate_agent_leases"
        ),
    )
    op.execute('ALTER TABLE "roomrate_agent_leases" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop the agent-run lease table."""
    op.drop_table("roomrate_agent_leases")
