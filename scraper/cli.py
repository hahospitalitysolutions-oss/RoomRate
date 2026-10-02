"""Command-line configuration and orchestration for the scraper pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime

from api.services.room_rates_normalizer import normalize_room_type_category_key

from .clients import build_client, build_engine, log_dotenv_resolution
from .config import ScraperConfig
from .logging_config import configure_logging, log
from .persistence import persist_results
from .models import empty_result_summary
from .progress import ProgressReporter
from .provider import ActorRunError, fetch_deep_room_data, fetch_hotel_lists
from .tables import _metadata
from .transform import process_and_flatten_data
from .utils import _normalize_required_amenities, _normalize_url

def _prompt_date(label: str, default: datetime) -> datetime:
    """Ζητά ημερομηνία από τον χρήστη. Enter = χρήση default."""
    while True:
        raw = input(f"  {label} [{default.strftime('%Y-%m-%d')}]: ").strip()
        if not raw:
            return default
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            log.warning("Λάθος μορφή. Χρησιμοποίησε YYYY-MM-DD (π.χ. 2026-07-01)")


def _normalize_nearby_destinations(values: list[str], destination: str) -> tuple[str, ...]:
    """Trim, drop blanks, case-insensitive duplicates and the main destination.

    The main destination is always scouted first, so repeating it as a nearby
    area would only pay for the same Apify run twice.
    """
    seen = {destination.strip().casefold()}
    normalized: list[str] = []
    for value in values:
        entry = str(value or "").strip()
        key = entry.casefold()
        if not entry or key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return tuple(normalized)


def build_config_from_args(argv: list[str]) -> ScraperConfig:
    parser = argparse.ArgumentParser(description="Run RoomRate Booking.com scraping pipeline.")
    parser.add_argument("--account-id", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--scrape-job-id", default=None)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--check-in", required=True)
    parser.add_argument("--check-out", required=True)
    parser.add_argument("--scout-max-items", type=int, default=10)
    parser.add_argument("--deep-crawl-max-items", type=int, default=100)
    parser.add_argument("--rooms", type=int, default=1)
    parser.add_argument("--adults", type=int, default=2)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument(
        "--job-type",
        choices=["owned_property_room_discovery", "competitor_search"],
        default="competitor_search",
    )
    parser.add_argument("--room-type-category", default=None)
    parser.add_argument("--room-name-query", default=None)
    parser.add_argument("--required-meal", default=None)
    parser.add_argument("--required-free-cancellation", default=None)
    parser.add_argument("--required-amenity", action="append", default=[])
    parser.add_argument("--target-url", action="append", default=[])
    parser.add_argument("--scout-cache-hours", type=int, default=24)
    parser.add_argument("--deep-crawl-workers", type=int, default=3)
    parser.add_argument("--deep-crawl-batch-size", type=int, default=8)
    # Round 6 §3.2: the API emits these for a map search with neighbouring
    # areas and a radius centred on the owner's property.
    parser.add_argument("--nearby-destination", action="append", default=[],
                        help="Γειτονική περιοχή για παράλληλο scout (επαναλαμβανόμενο).")
    parser.add_argument("--nearby-max-items", type=int, default=20)
    parser.add_argument("--origin-lat", type=float, default=None)
    parser.add_argument("--origin-lng", type=float, default=None)
    parser.add_argument("--radius-km", type=float, default=None)
    parser.add_argument("--deep-crawl-max-hotels", type=int, default=None)
    parser.add_argument("--output-csv", default=None)
    parser.add_argument("--error-log-csv", default="logs/error_log.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-normalized", action="store_true", help="Skip writes to normalized RoomRate tables.")
    parser.add_argument(
        "--allow-partial-batches",
        action="store_true",
        help="Persist successful batches even if another deep-crawl batch fails.",
    )
    args = parser.parse_args(argv)

    check_in = datetime.strptime(args.check_in, "%Y-%m-%d")
    check_out = datetime.strptime(args.check_out, "%Y-%m-%d")
    if check_out <= check_in:
        raise ValueError("check-out must be after check-in")
    try:
        account_id = uuid.UUID(args.account_id)
    except ValueError as exc:
        raise ValueError("account-id must be a valid UUID") from exc
    try:
        scrape_job_id = uuid.UUID(args.scrape_job_id) if args.scrape_job_id else None
    except ValueError as exc:
        raise ValueError("scrape-job-id must be a valid UUID") from exc

    safe_dest = args.destination.lower().replace(" ", "_").replace("/", "_")
    return ScraperConfig(
        account_id=account_id,
        scrape_job_id=scrape_job_id,
        destination=args.destination,
        check_in=check_in,
        check_out=check_out,
        scout_max_items=args.scout_max_items,
        deep_crawl_max_items=args.deep_crawl_max_items,
        rooms=args.rooms,
        adults=args.adults,
        children=args.children,
        job_type=args.job_type,
        room_type_category=normalize_room_type_category_key(args.room_type_category),
        room_name_query=args.room_name_query.strip() if args.room_name_query and args.room_name_query.strip() else None,
        required_meal=args.required_meal.strip().lower() if args.required_meal and args.required_meal.strip() else None,
        required_free_cancellation=(
            args.required_free_cancellation.strip().lower()
            if args.required_free_cancellation and args.required_free_cancellation.strip()
            else None
        ),
        required_amenities=_normalize_required_amenities(args.required_amenity),
        target_urls=tuple(args.target_url),
        scout_cache_hours=args.scout_cache_hours,
        deep_crawl_workers=args.deep_crawl_workers,
        deep_crawl_batch_size=args.deep_crawl_batch_size,
        nearby_destinations=_normalize_nearby_destinations(args.nearby_destination, args.destination),
        nearby_max_items=args.nearby_max_items,
        origin_lat=args.origin_lat,
        origin_lng=args.origin_lng,
        radius_km=args.radius_km,
        deep_crawl_max_hotels=args.deep_crawl_max_hotels,
        output_csv=args.output_csv or f"room_{safe_dest}.csv",
        error_log_csv=args.error_log_csv,
        dry_run=args.dry_run,
        write_normalized=not args.no_normalized,
        allow_partial_batches=args.allow_partial_batches,
    )


def _build_interactive_config() -> ScraperConfig:
    log.info(
        "RoomRate Scraper — Παράμετροι Αναζήτησης | "
        "δέχεται πόλη ή περιοχή + νησί, σε Ελληνικά ή English"
    )

    destination = input("  Προορισμός (π.χ. Ρόδος, Κως, Αθήνα): ").strip()
    if not destination:
        destination = "Φαληράκι"
        log.info("Χρήση default προορισμού: %s", destination)

    # Επανάληψη μέχρι έγκυρο εύρος — ένα σκέτο return εδώ θα έστελνε
    # config=None στο main() και θα έσκαγε με AttributeError.
    while True:
        check_in  = _prompt_date("Check-in  (YYYY-MM-DD)", datetime(2026, 6, 15))
        check_out = _prompt_date("Check-out (YYYY-MM-DD)", datetime(2026, 6, 20))
        if check_out > check_in:
            break
        log.warning("Το check-out πρέπει να είναι μετά το check-in. Δοκίμασε ξανά.")

    # Καθαρό CSV filename από τον προορισμό
    safe_dest = destination.lower().replace(" ", "_").replace("/", "_")
    output_csv = f"room_{safe_dest}.csv"

    return ScraperConfig(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        scout_max_items=10,
        scout_cache_hours=24,
        deep_crawl_workers=3,
        deep_crawl_batch_size=8,
        output_csv=output_csv,
    )


RESULT_SUMMARY_SENTINEL = "ROOMRATE_RESULT_SUMMARY"


def _emit_result_summary(summary: dict[str, object], warnings: list[str] | None = None) -> None:
    """Emit one machine-readable stdout line for the parent job runner.

    ``warnings`` always lands in the payload as a list (Round 6): the run's
    own entries such as ``nearby_scout_failed:<destination>`` are appended to
    whatever the summary already carries, and the API appends its own.
    """
    existing = list(summary.get("warnings") or [])
    payload = {**summary, "warnings": [*existing, *(warnings or [])]}
    log.info("%s %s", RESULT_SUMMARY_SENTINEL, json.dumps(payload, separators=(",", ":"), sort_keys=True))


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    # Only now do handlers exist; the .env breadcrumb is resolved at import
    # time but must be reported here to actually reach scraper.log.
    log_dotenv_resolution()
    argv = sys.argv[1:] if argv is None else argv
    config = build_config_from_args(argv) if argv else _build_interactive_config()

    log.info(
        "=== Room Scraper v3 START | %s → %s | %s ===",
        config.check_in.date(), config.check_out.date(), config.destination,
    )

    client = build_client()
    engine = None if config.dry_run else build_engine()
    if engine is not None:
        _metadata.create_all(engine)
    # Round 6 §3.5: one reporter for the whole run. It is a no-op without an
    # engine (dry run) or a job id (interactive run) and never raises.
    progress = ProgressReporter(engine, config.scrape_job_id)
    # Non-fatal problems the API should surface (nearby_scout_failed:<area>).
    warnings: list[str] = []

    try:
        if config.target_urls:
            hotels = [
                {
                    "name": _normalize_url(url),
                    "url": _normalize_url(url),
                    "stars": 0.0,
                    "review_score": 0.0,
                    "review_count": 0,
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "property_type": "Hotel",
                    "city": config.destination,
                    "address": "",
                }
                for url in config.target_urls
            ]
            log.info("[ΣΤΑΔΙΟ 1] Direct URL mode -> %d target URLs", len(hotels))
        else:
            # Κύριος προορισμός + γειτονικές περιοχές, ακτίνα και όριο (§3.3).
            hotels = fetch_hotel_lists(client, config, engine, progress=progress, warnings=warnings)
        if not hotels:
            log.error("Δεν βρέθηκαν καταλύματα. Τερματισμός.")
            _emit_result_summary(empty_result_summary(), warnings)
            return

        raw_data = fetch_deep_room_data(client, hotels, config, progress=progress)
    except ActorRunError as exc:
        # Ολική αποτυχία actor ≠ κενή αγορά: μη-μηδενικό exit code ώστε το
        # API (BookingScrapeJobRunner) να μαρκάρει το job failed, όχι completed.
        log.error("Το scrape απέτυχε: %s", exc)
        sys.exit(2)
    if not raw_data:
        log.error("Κανένα δεδομένο από Deep Crawl. Τερματισμός.")
        _emit_result_summary(empty_result_summary(), warnings)
        return

    df = process_and_flatten_data(raw_data, hotels, config)
    progress.update("persist")
    result = persist_results(df, config)
    _emit_result_summary(result.result_summary, warnings)

    if not df.empty:
        log.info(
            "=== SUMMARY → %d records | %d ξενοδοχεία | avg €%.2f/night | CSV: %s ===",
            len(df), df["hotel_name"].nunique(),
            df["price_per_night_eur"].mean(), config.output_csv,
        )
    log.info(
        "Batch done — processed:%d failed:%d skipped:%d",
        result.normalized_written or result.rows_seen,
        result.normalization_failed,
        max(len(df) - result.rows_seen, 0),
    )
