"""CSV, legacy-table and normalized-market persistence."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from api.repositories.normalized_market_writer import write_normalized_rates
from api.services.destination_aliases import canonical_destination
from api.services.room_rates_normalizer import (
    RoomRateNormalizationError,
    normalize_room_rate_row,
)

from .config import ScraperConfig
from .logging_config import log
from .models import PersistResult, empty_result_summary
from .utils import _append_error_log, _filter_by_room_name, _has_required_amenities, _matches_required_free_cancellation, _matches_required_meal


def _drop_single_rooms(frame: pd.DataFrame) -> pd.DataFrame:
    """Round 6 §3.4: a single room never competes with a 2+ person listing.

    Every other category (double/twin/suite/apartment/studio/None) stays and
    is labelled «Παρόμοιο» at read time instead of being cut before storage.
    Only applied when a room must host 2+ adults (ceil(adults / rooms) >= 2):
    a solo traveller, or 2 adults in 2 rooms, competes with single rooms.
    """
    if "room_type_category" not in frame.columns:
        return frame
    return frame[frame["room_type_category"].fillna("") != "single"].copy()


def _drop_undersized_rooms(frame: pd.DataFrame, persons_per_room: int) -> pd.DataFrame:
    """Drop rows whose KNOWN capacity (max_persons > 0) is below the per-room party.

    max_persons is the rate's occupancy PER ROOM (Apify ``persons``), so a
    4-adult, 2-room search competes with 2-person rates. It is 0 when Booking
    did not say, and an unknown capacity must not cost a competitor its place
    in the market.
    """
    if "max_persons" not in frame.columns:
        return frame
    capacity = pd.to_numeric(frame["max_persons"], errors="coerce").fillna(0).astype(int)
    return frame[~((capacity > 0) & (capacity < persons_per_room))].copy()


def persist_results(df: pd.DataFrame, config: ScraperConfig) -> PersistResult:
    """Persist flattened scrape results to CSV, legacy table and normalized tables."""
    result_summary = empty_result_summary(rows_seen=len(df))
    filter_counts = result_summary["filter_counts"]
    assert isinstance(filter_counts, dict)
    if df.empty:
        log.warning("Empty DataFrame; nothing to persist.")
        return PersistResult(result_summary=result_summary)

    # Data-driven row filters: (enabled, info template, warning template,
    # label, filter). Each logs the same before -> after line as before, and an
    # emptied DataFrame short-circuits from the ONE early-return site below.
    is_competitor_search = config.job_type == "competitor_search"
    # The party spread over its rooms, rounded up: 3 adults in 2 rooms still
    # need a room for 2.
    persons_per_room = math.ceil(config.adults / config.rooms)
    row_filters = (
        # Round 6 §3.4: no category pool before storage any more. Cutting to
        # double/twin turned 10 hotels into 2 (aparthotels and studios went
        # whole); every non-single room that fits the party is stored and
        # labelled same/similar at read time from room_type_category.
        (
            "single_rooms",
            is_competitor_search and persons_per_room >= 2,
            "Φίλτρο μονόκλινων (%s): %d -> %d εγγραφές",
            "Καμία εγγραφή δεν έμεινε μετά το φίλτρο μονόκλινων (%s)",
            "single",
            _drop_single_rooms,
        ),
        (
            "capacity",
            is_competitor_search,
            "Φίλτρο χωρητικότητας (>= %s άτομα ανά δωμάτιο): %d -> %d εγγραφές",
            "Καμία εγγραφή δεν χωρά %s άτομα ανά δωμάτιο",
            str(persons_per_room),
            lambda frame: _drop_undersized_rooms(frame, persons_per_room),
        ),
        (
            "room_name",
            bool(config.room_name_query) and is_competitor_search,
            "Room name filter '%s': %d -> %d records",
            "No rows matched room_name_query=%s",
            config.room_name_query,
            lambda frame: _filter_by_room_name(frame, config.room_name_query),
        ),
        (
            "meal",
            bool(config.required_meal),
            "Meal filter '%s': %d -> %d records",
            "No rows matched required_meal=%s",
            config.required_meal,
            lambda frame: frame[
                frame["meals"].fillna("").map(lambda meals: _matches_required_meal(meals, config.required_meal))
            ].copy(),
        ),
        (
            "free_cancellation",
            bool(config.required_free_cancellation),
            "Free cancellation filter '%s': %d -> %d records",
            "No rows matched required_free_cancellation=%s",
            config.required_free_cancellation,
            lambda frame: frame[
                frame["free_cancellation"].fillna("").map(
                    lambda value: _matches_required_free_cancellation(value, config.required_free_cancellation)
                )
            ].copy(),
        ),
        (
            "amenities",
            bool(config.required_amenities),
            "Amenities filter %s: %d -> %d records",
            "No rows matched required amenities=%s",
            ",".join(config.required_amenities),
            lambda frame: frame[
                frame["facilities"].fillna("").map(
                    lambda facilities: _has_required_amenities(str(facilities), config.required_amenities)
                )
            ].copy(),
        ),
    )
    for filter_name, enabled, info_template, warning_template, label, apply_filter in row_filters:
        if not enabled:
            continue
        before_count = len(df)
        df = apply_filter(df)
        filter_counts[filter_name] = {"before": before_count, "after": len(df)}
        log.info(info_template, label, before_count, len(df))
        if df.empty:
            log.warning(warning_template, label)
            return PersistResult(rows_seen=before_count, result_summary=result_summary)

    if config.dry_run:
        log.info("Dry run: validated %d rows; no CSV or database writes.", len(df))
        return PersistResult(rows_seen=len(df), result_summary=result_summary)

    output_path = Path(config.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
    except OSError as exc:
        raise OSError(f"Could not write scrape CSV to {output_path}") from exc
    log.info("CSV -> '%s' (%d rows)", config.output_csv, len(df))

    rows = df.to_dict(orient="records")
    normalized_written = 0
    normalization_failed = 0
    normalization_errors: list[dict[str, object]] = []

    if config.write_normalized:
        normalized_rates = []
        for row in rows:
            try:
                normalized_rates.append(normalize_room_rate_row(row, provider=config.provider))
            except RoomRateNormalizationError as exc:
                normalization_failed += 1
                normalization_errors.append(
                    {
                        "field": "normalized_rate",
                        "issue": str(exc),
                        "value": row.get("record_id", ""),
                    }
                )
                log.warning(
                    "Skipping normalized write for record_id=%s hotel=%s: %s",
                    row.get("record_id"),
                    row.get("hotel_name"),
                    exc,
                )
        if normalized_rates:
            normalized_written = write_normalized_rates(
                normalized_rates,
                account_id=config.account_id,
                ingestion_source="scraper",
                scrape_job_id=config.scrape_job_id,
                raw_destination=config.destination,
                canonical_destination=canonical_destination(config.destination),
            )
            log.info("PostgreSQL normalized market tables -> %d packages written", normalized_written)
        _append_error_log(config, normalization_errors)

    result_summary["rows_written"] = normalized_written
    return PersistResult(
        rows_seen=len(rows),
        csv_written=True,
        normalized_written=normalized_written,
        normalization_failed=normalization_failed,
        result_summary=result_summary,
    )


# ===========================================================
# ΕΚΤΕΛΕΣΗ
# ===========================================================
