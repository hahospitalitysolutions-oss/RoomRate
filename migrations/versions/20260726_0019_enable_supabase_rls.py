"""Enable deny-by-default Row Level Security for every RoomRate table.

The Angular client uses a public Supabase anon key exclusively for Auth. All
application data must continue to flow through FastAPI, where account scoping
is enforced from the verified JWT. Enabling RLS without browser-facing
policies makes accidental PostgREST access deny-by-default while preserving
access for the database owner used by the backend.

Revision ID: 20260726_0019
Revises: 20260723_0018
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260726_0019"
down_revision: str | None = "20260723_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROOMRATE_TABLES = (
    "roomrate_accounts",
    "roomrate_user_identities",
    "roomrate_memberships",
    "roomrate_owned_properties",
    "roomrate_owned_property_room_types",
    "roomrate_scrape_jobs",
    "roomrate_schedule_configs",
    "roomrate_scrape_runs",
    "roomrate_properties",
    "roomrate_rate_observations",
    "roomrate_room_packages",
    "roomrate_tracked_competitors",
    "roomrate_amenities",
    "roomrate_property_amenities",
    "roomrate_raw_ingestion_events",
    "roomrate_alert_rules",
    "roomrate_notifications",
    "roomrate_ws_tickets",
)


def upgrade() -> None:
    for table_name in ROOMRATE_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table_name in ROOMRATE_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
