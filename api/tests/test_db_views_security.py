"""Security properties of the SQL view definitions in api/db_views.py.

Migration 0019 enables deny-by-default RLS on every RoomRate table so that a
leaked/abused Supabase anon key cannot read application data straight from
PostgREST — all access must go through FastAPI, which scopes every query to
the verified JWT's account.

A plain ``CREATE VIEW`` defeats that: PostgreSQL executes it with the VIEW
OWNER's privileges, and the owner (the role that ran the migrations) is exempt
from RLS. Both views join across the RLS-protected tables and select
``account_id`` for every tenant, so without ``security_invoker`` the anon role
still reads every account's rates and competitor markers through them — the
exact hole the migration set out to close.
"""

from __future__ import annotations

import re

from api.db_views import (
    COMPETITOR_MARKERS_V1,
    COMPETITOR_MARKERS_V2,
    LATEST_ROOM_RATES_V1,
    LATEST_ROOM_RATES_V2,
    LATEST_ROOM_RATES_V3,
)

# The views actually installed by the latest migration head.
CURRENT_VIEWS = {
    "roomrate_latest_room_rates": LATEST_ROOM_RATES_V3,
    "roomrate_competitor_markers": COMPETITOR_MARKERS_V2,
}

HISTORICAL_VIEWS = {
    "latest_room_rates_v1": LATEST_ROOM_RATES_V1,
    "latest_room_rates_v2": LATEST_ROOM_RATES_V2,
    "competitor_markers_v1": COMPETITOR_MARKERS_V1,
}


def _security_invoker_clause(sql: str) -> str | None:
    """Return the WITH (...) options of the CREATE VIEW statement, if any."""
    match = re.search(r"CREATE\s+VIEW\s+\w+\s+WITH\s*\(([^)]*)\)", sql, re.IGNORECASE)
    return match.group(1) if match else None


def test_current_views_run_with_the_callers_privileges():
    for view_name, sql in CURRENT_VIEWS.items():
        options = _security_invoker_clause(sql)
        assert options is not None, (
            f"{view_name} has no WITH (...) options, so it runs as its OWNER and "
            "bypasses the RLS enabled by migration 0019"
        )
        assert re.search(r"security_invoker\s*=\s*(true|on)", options, re.IGNORECASE), (
            f"{view_name} must set security_invoker=true; got WITH ({options})"
        )


def test_current_views_still_expose_account_id_for_api_scoping():
    """Sanity: the API filters on account_id, so the column must survive."""
    for view_name, sql in CURRENT_VIEWS.items():
        assert "account_id" in sql, f"{view_name} lost its account_id column"


def test_historical_view_definitions_are_left_untouched():
    """Old versions are replayed by old migrations and must not be rewritten.

    ``security_invoker`` needs PostgreSQL 15+; retrofitting it into already-
    applied migrations would break replays on older engines for no benefit
    (those views are dropped by later revisions anyway).
    """
    for name, sql in HISTORICAL_VIEWS.items():
        assert _security_invoker_clause(sql) is None, f"{name} should stay as-is"
