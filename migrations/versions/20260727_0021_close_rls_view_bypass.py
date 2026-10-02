"""Close the RLS bypass through the two reporting views (and scout_cache).

Migration 0019 enabled deny-by-default RLS on every RoomRate table so a leaked
Supabase anon key cannot read application data straight from PostgREST. Two
holes remained:

* ``roomrate_latest_room_rates`` and ``roomrate_competitor_markers`` were
  created as plain views, so PostgreSQL runs them with the VIEW OWNER's
  privileges. The owner (the migration role) is exempt from RLS, so the anon
  role could still read every tenant's rates, prices and coordinates through
  the views — exactly what 0019 set out to prevent. Recreating them ``WITH
  (security_invoker = true)`` makes the base-table policies apply to whoever
  queries the view.
* ``scout_cache`` and the legacy ``room_rates`` table were not in 0019's list
  even though they live in the same schema and still hold scraped competitor
  inventory (hotel names, coordinates, review scores, prices) plus each
  account's destination/date search windows.

The API path is unchanged: it connects as the owner and enforces account
scoping in SQL, so RLS never applied to it in the first place.

Requires PostgreSQL 15+ (``security_invoker``). Supabase runs 15+.

Revision ID: 20260727_0021
Revises: 20260726_0020
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

from api.db_views import (
    COMPETITOR_MARKERS_V2,
    COMPETITOR_MARKERS_VIEW_NAME,
    LATEST_ROOM_RATES_V3,
    LATEST_ROOM_RATES_VIEW_NAME,
)


revision: str = "20260727_0021"
down_revision: str | None = "20260726_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER VIEW ... SET is enough and keeps dependent objects intact; the
    # view bodies are unchanged (the SQL constants now carry the option so a
    # fresh CREATE from db_views.py is secure too).
    op.execute(f"ALTER VIEW {LATEST_ROOM_RATES_VIEW_NAME} SET (security_invoker = true)")
    op.execute(f"ALTER VIEW {COMPETITOR_MARKERS_VIEW_NAME} SET (security_invoker = true)")

    # Both are created outside alembic (scraper/tables.py) on first scraper
    # run, so they may legitimately not exist yet in a fresh database.
    for table_name in ("scout_cache", "room_rates"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{table_name}') IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY';
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for table_name in ("room_rates", "scout_cache"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{table_name}') IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY';
                END IF;
            END $$;
            """
        )
    op.execute(f"ALTER VIEW {COMPETITOR_MARKERS_VIEW_NAME} SET (security_invoker = false)")
    op.execute(f"ALTER VIEW {LATEST_ROOM_RATES_VIEW_NAME} SET (security_invoker = false)")


# Unused here, but keeps the view SQL constants imported so a rename in
# api/db_views.py fails loudly at migration import instead of at runtime.
_VIEW_SQL_GUARD = (LATEST_ROOM_RATES_V3, COMPETITOR_MARKERS_V2)
