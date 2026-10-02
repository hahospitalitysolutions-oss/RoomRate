from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.db import get_engine
from api.repositories.normalized_market_writer import write_normalized_rates
from api.services.room_rates_normalizer import (
    NormalizedRoomRate,
    RoomRateNormalizationError,
    normalize_room_rate_row,
)

logger = logging.getLogger(__name__)


@dataclass
class BackfillStats:
    scanned: int = 0
    normalized: int = 0
    failed: int = 0
    written_packages: int = 0


def fetch_legacy_rows(limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT
            record_id, scraped_at, check_in, check_out, hotel_name, city,
            address, property_type, latitude, longitude, stars, review_score,
            review_count, price_per_night_eur, nights, guests, room_type,
            meals, free_cancellation, price_total_eur, facilities, rooms_left
        FROM room_rates
        ORDER BY scraped_at ASC, hotel_name ASC, price_per_night_eur ASC
    """
    params: dict[str, Any] = {}
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit

    # Writer role: a full-table backfill read can legitimately exceed the
    # API statement timeout.
    with get_engine(role="writer").connect() as connection:
        result = connection.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]


def normalize_rows(rows: list[dict[str, Any]], provider: str) -> tuple[list[NormalizedRoomRate], list[dict[str, Any]]]:
    normalized: list[NormalizedRoomRate] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        try:
            normalized.append(normalize_room_rate_row(row, provider=provider))
        except RoomRateNormalizationError as exc:
            errors.append(
                {
                    "record_id": row.get("record_id"),
                    "hotel_name": row.get("hotel_name"),
                    "issue": str(exc),
                }
            )
    return normalized, errors


def run_backfill(provider: str, limit: int | None, dry_run: bool, account_id: uuid.UUID) -> BackfillStats:
    rows = fetch_legacy_rows(limit=limit)
    normalized, errors = normalize_rows(rows, provider=provider)
    stats = BackfillStats(scanned=len(rows), normalized=len(normalized), failed=len(errors))

    for error in errors[:20]:
        logger.warning("Skipped legacy row during normalization: %s", error)
    if len(errors) > 20:
        logger.warning("Skipped %s additional invalid rows", len(errors) - 20)

    if dry_run:
        logger.info(
            "Dry run complete: scanned=%s normalized=%s failed=%s",
            stats.scanned,
            stats.normalized,
            stats.failed,
        )
        return stats

    stats.written_packages = write_normalized_rates(
        normalized,
        account_id=account_id,
        ingestion_source="backfill_room_rates",
    )
    logger.info(
        "Backfill complete: scanned=%s normalized=%s failed=%s written_packages=%s",
        stats.scanned,
        stats.normalized,
        stats.failed,
        stats.written_packages,
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill RoomRate normalized market tables from legacy room_rates.")
    parser.add_argument("--account-id", default="00000000-0000-0000-0000-000000000001", help="Tenant account UUID.")
    parser.add_argument("--provider", default="booking_com", help="Source provider key.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum legacy rows to scan.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize without writing.")
    parser.add_argument("--apply", action="store_true", help="Write normalized rows to PostgreSQL.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    args = parse_args()
    dry_run = args.dry_run or not args.apply
    if dry_run and args.apply:
        raise ValueError("Use either --dry-run or --apply, not both")
    run_backfill(provider=args.provider, limit=args.limit, dry_run=dry_run, account_id=uuid.UUID(args.account_id))


if __name__ == "__main__":
    main()
