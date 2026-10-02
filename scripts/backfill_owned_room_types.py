"""One-off backfill: union the owner's room types from every completed run.

Room discovery ran once (2 adults), so ``roomrate_owned_property_room_types``
only holds the rooms Booking listed for that party. The owner's hotel also
appears in competitor runs; this script runs the SAME union the completion
hook runs (``ScrapeJobRepository.union_owned_room_types``) over each account's
whole history. It only ADDS rows (never updates, deactivates or renames one,
never touches the selected room), so it is idempotent: a second run adds 0.

Prints counts only — no connection string, account ids or room names.

Usage:
    python scripts/backfill_owned_room_types.py --dry-run
    python scripts/backfill_owned_room_types.py
    python scripts/backfill_owned_room_types.py --account-id <uuid> [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Before importing api.*: api.config builds its settings at import time.
# utf-8-sig because the owner's .env is saved by a Windows editor with a BOM.
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

from api.db import get_engine  # noqa: E402
from api.repositories.scrape_jobs_repository import ScrapeJobRepository  # noqa: E402

# Accounts with an active owned property; the optional-account variant is a
# second constant rather than string-built SQL.
ACCOUNTS_SQL = """
    SELECT DISTINCT account_id
    FROM roomrate_owned_properties
    WHERE is_active = true
    ORDER BY account_id
"""
ONE_ACCOUNT_SQL = """
    SELECT DISTINCT account_id
    FROM roomrate_owned_properties
    WHERE is_active = true
      AND account_id = :account_id
"""


@dataclass
class BackfillTotals:
    accounts: int = 0
    room_types_seen: int = 0
    room_types_added: int = 0


def list_account_ids(engine: Any, account_id: uuid.UUID | None) -> list[uuid.UUID]:
    """Accounts to scan: the given one (if it has an active property) or all."""
    with engine.connect() as connection:
        if account_id is None:
            rows = connection.execute(text(ACCOUNTS_SQL)).mappings().all()
        else:
            rows = connection.execute(text(ONE_ACCOUNT_SQL), {"account_id": account_id}).mappings().all()
    return [row["account_id"] for row in rows]


def run_backfill(engine: Any, account_id: uuid.UUID | None, dry_run: bool) -> BackfillTotals:
    """Run the owned-room union over every completed run, one transaction per account."""
    totals = BackfillTotals()
    account_ids = list_account_ids(engine, account_id)
    for index, current_account_id in enumerate(account_ids, start=1):
        # A dry run never opens a write transaction: connect() rolls back on close.
        scope = engine.connect() if dry_run else engine.begin()
        with scope as connection:
            result = ScrapeJobRepository.union_owned_room_types(
                connection,
                account_id=current_account_id,
                job_id=None,
                dry_run=dry_run,
            )
        totals.accounts += 1
        totals.room_types_seen += result.candidates
        totals.room_types_added += result.inserted
        verb = "would add" if dry_run else "added"
        print(f"account {index}/{len(account_ids)}: room types seen={result.candidates} {verb}={result.inserted}")
    return totals


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Union the owner's room types from all completed runs into the owned room catalog."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would be added without writing.")
    parser.add_argument("--account-id", type=uuid.UUID, default=None, help="Limit to one account.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Writer role: a whole-history scan must not hit the API statement timeout.
    totals = run_backfill(get_engine(role="writer"), account_id=args.account_id, dry_run=args.dry_run)
    mode = "dry run, nothing written" if args.dry_run else "applied"
    print(
        f"Done ({mode}): accounts={totals.accounts} "
        f"room types seen={totals.room_types_seen} "
        f"{'would add' if args.dry_run else 'added'}={totals.room_types_added}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
