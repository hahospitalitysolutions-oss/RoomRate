"""Store the schedule's hour on the owner's own clock.

``roomrate_schedule_configs.hour_utc`` held the Greek hour converted with the
offset of the day it was saved, so every daylight-saving change moved the
run (and the hour the settings page showed) by one. ``hour_local`` and
``timezone`` hold what the owner chose; the scheduler decides on the local
calendar (api/services/schedule_timing.py).

Additive only (docs/DEPLOYMENT.md §4): ``hour_utc`` stays and the API keeps
writing it, so the previous image still runs against this schema.
``hour_local`` is backfilled with the hour the settings page shows today, the
UTC hour read with today's offset.

Revision ID: 20260930_0030
Revises: 20260930_0029
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260930_0030"
down_revision: str | None = "20260930_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add hour_local and timezone, filled from hour_utc at today's offset."""
    op.add_column(
        "roomrate_schedule_configs",
        sa.Column("hour_local", sa.Integer(), nullable=False, server_default="8"),
    )
    op.add_column(
        "roomrate_schedule_configs",
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Athens"),
    )
    op.create_check_constraint(
        "ck_roomrate_schedule_configs_hour_local",
        "roomrate_schedule_configs",
        "hour_local BETWEEN 0 AND 23",
    )
    # Today's date at hour_utc, as an instant, read on the schedule's clock.
    op.execute(
        """
        UPDATE roomrate_schedule_configs
        SET hour_local = extract(
            hour FROM (
                (date_trunc('day', now() AT TIME ZONE 'UTC') + hour_utc * interval '1 hour')
                AT TIME ZONE 'UTC'
            ) AT TIME ZONE timezone
        )::int
        """
    )


def downgrade() -> None:
    """Drop the local hour; hour_utc was kept current all along."""
    op.drop_constraint("ck_roomrate_schedule_configs_hour_local", "roomrate_schedule_configs", type_="check")
    op.drop_column("roomrate_schedule_configs", "timezone")
    op.drop_column("roomrate_schedule_configs", "hour_local")
