from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from api.db import get_engine
from api.services.destination_aliases import canonical_destination as canonical_destination_key
from api.services.room_rates_normalizer import NormalizedRoomRate, normalize_amenity

DEFAULT_ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _scalar_id(connection: Any, sql: Any, params: dict[str, Any]) -> uuid.UUID:
    statement = text(sql) if isinstance(sql, str) else sql
    return connection.execute(statement, params).scalar_one()


def upsert_scrape_run(
    connection: Any,
    rate: NormalizedRoomRate,
    account_id: uuid.UUID,
    ingestion_source: str = "scraper",
    scrape_job_id: uuid.UUID | None = None,
    raw_destination: str | None = None,
    canonical_destination: str | None = None,
) -> uuid.UUID:
    resolved_raw_destination = (raw_destination or rate.raw_destination or rate.destination).strip()
    resolved_canonical_destination = (
        canonical_destination
        or rate.canonical_destination
        or canonical_destination_key(resolved_raw_destination)
    )
    raw_metadata = {"ingestion_source": ingestion_source}
    if scrape_job_id:
        raw_metadata["scrape_job_id"] = str(scrape_job_id)
    statement = text(
        """
        INSERT INTO roomrate_scrape_runs (
            id, account_id, scrape_job_id, provider, source_run_key,
            destination, raw_destination, canonical_destination, check_in, check_out,
            adults, children, rooms, status, started_at, finished_at, raw_metadata
        )
        VALUES (
            :id, :account_id, :scrape_job_id, :provider, :source_run_key,
            :destination, :raw_destination, :canonical_destination, :check_in, :check_out,
            :adults, :children, :rooms, 'completed', :observed_at, :observed_at, :raw_metadata
        )
        ON CONFLICT (account_id, provider, source_run_key) DO UPDATE SET
            updated_at = now(),
            scrape_job_id = COALESCE(EXCLUDED.scrape_job_id, roomrate_scrape_runs.scrape_job_id),
            raw_destination = EXCLUDED.raw_destination,
            canonical_destination = EXCLUDED.canonical_destination,
            status = EXCLUDED.status,
            finished_at = EXCLUDED.finished_at,
            raw_metadata = COALESCE(roomrate_scrape_runs.raw_metadata, '{}'::jsonb) || EXCLUDED.raw_metadata
        RETURNING id
        """
    ).bindparams(bindparam("raw_metadata", type_=JSONB))
    return _scalar_id(
        connection,
        statement,
        {
            "id": uuid.uuid4(),
            "account_id": account_id,
            "scrape_job_id": scrape_job_id,
            "provider": rate.provider,
            "source_run_key": rate.source_run_key,
            "destination": rate.destination,
            "raw_destination": resolved_raw_destination,
            "canonical_destination": resolved_canonical_destination,
            "check_in": rate.check_in,
            "check_out": rate.check_out,
            "adults": rate.adults,
            "children": rate.children,
            "rooms": rate.rooms,
            "observed_at": rate.observed_at,
            "raw_metadata": raw_metadata,
        },
    )


def upsert_property(connection: Any, rate: NormalizedRoomRate) -> uuid.UUID:
    return _scalar_id(
        connection,
        """
        INSERT INTO roomrate_properties (
            id, provider, source_property_key, canonical_name, display_name,
            city, country, address, property_type, latitude, longitude, stars, booking_url
        )
        VALUES (
            :id, :provider, :source_property_key, :canonical_name, :display_name,
            :city, :country, :address, :property_type, :latitude, :longitude, :stars, :booking_url
        )
        ON CONFLICT (provider, source_property_key) DO UPDATE SET
            updated_at = now(),
            display_name = EXCLUDED.display_name,
            city = EXCLUDED.city,
            country = COALESCE(EXCLUDED.country, roomrate_properties.country),
            address = COALESCE(EXCLUDED.address, roomrate_properties.address),
            property_type = COALESCE(EXCLUDED.property_type, roomrate_properties.property_type),
            latitude = COALESCE(EXCLUDED.latitude, roomrate_properties.latitude),
            longitude = COALESCE(EXCLUDED.longitude, roomrate_properties.longitude),
            stars = COALESCE(EXCLUDED.stars, roomrate_properties.stars),
            -- Round 6. Pre-existing properties gain the listing URL on their
            -- next scrape, and a row without one never erases a known URL.
            booking_url = COALESCE(EXCLUDED.booking_url, roomrate_properties.booking_url)
        RETURNING id
        """,
        {
            "id": uuid.uuid4(),
            "provider": rate.provider,
            "source_property_key": rate.source_property_key,
            "canonical_name": rate.canonical_name,
            "display_name": rate.display_name,
            "city": rate.city,
            "country": rate.country,
            "address": rate.address,
            "property_type": rate.property_type,
            "latitude": rate.latitude,
            "longitude": rate.longitude,
            "stars": rate.stars,
            "booking_url": rate.booking_url,
        },
    )


def upsert_observation(connection: Any, rate: NormalizedRoomRate, scrape_run_id: uuid.UUID, property_id: uuid.UUID) -> uuid.UUID:
    return _scalar_id(
        connection,
        """
        INSERT INTO roomrate_rate_observations (
            id, scrape_run_id, property_id, observed_at, review_score, review_count,
            rooms_left_min, price_min_eur, price_max_eur
        )
        VALUES (
            :id, :scrape_run_id, :property_id, :observed_at, :review_score, :review_count,
            :rooms_left, :price, :price
        )
        ON CONFLICT (scrape_run_id, property_id) DO UPDATE SET
            updated_at = now(),
            review_score = COALESCE(EXCLUDED.review_score, roomrate_rate_observations.review_score),
            review_count = COALESCE(EXCLUDED.review_count, roomrate_rate_observations.review_count),
            rooms_left_min = LEAST(
                COALESCE(roomrate_rate_observations.rooms_left_min, EXCLUDED.rooms_left_min),
                COALESCE(EXCLUDED.rooms_left_min, roomrate_rate_observations.rooms_left_min)
            ),
            price_min_eur = LEAST(roomrate_rate_observations.price_min_eur, EXCLUDED.price_min_eur),
            price_max_eur = GREATEST(roomrate_rate_observations.price_max_eur, EXCLUDED.price_max_eur)
        RETURNING id
        """,
        {
            "id": uuid.uuid4(),
            "scrape_run_id": scrape_run_id,
            "property_id": property_id,
            "observed_at": rate.observed_at,
            "review_score": rate.review_score,
            "review_count": rate.review_count,
            "rooms_left": rate.rooms_left,
            "price": rate.price_per_night_eur,
        },
    )


def upsert_package(connection: Any, rate: NormalizedRoomRate, observation_id: uuid.UUID) -> None:
    # Rate plan (spec 2026-09-29 §3): the plan columns ride every INSERT and,
    # via EXCLUDED, every conflict update, so an idempotent re-run of the same
    # batch rewrites the same values instead of erasing them.
    statement = text(
        """
        INSERT INTO roomrate_room_packages (
            id, rate_observation_id, source_record_id, room_type, room_type_category, meals,
            free_cancellation, price_per_night_eur, price_total_eur,
            rooms_left, package_payload, room_attributes,
            discounted_price_per_night_eur, discount_pct, discount_label,
            has_genius_discount, cancellation_type, payment_label, rate_block_id
        )
        VALUES (
            :id, :rate_observation_id, :source_record_id, :room_type, :room_type_category, :meals,
            :free_cancellation, :price_per_night_eur, :price_total_eur,
            :rooms_left, :package_payload, :room_attributes,
            :discounted_price_per_night_eur, :discount_pct, :discount_label,
            :has_genius_discount, :cancellation_type, :payment_label, :rate_block_id
        )
        ON CONFLICT (rate_observation_id, source_record_id) DO UPDATE SET
            updated_at = now(),
            room_type = EXCLUDED.room_type,
            room_type_category = EXCLUDED.room_type_category,
            meals = EXCLUDED.meals,
            free_cancellation = EXCLUDED.free_cancellation,
            price_per_night_eur = EXCLUDED.price_per_night_eur,
            price_total_eur = EXCLUDED.price_total_eur,
            rooms_left = EXCLUDED.rooms_left,
            package_payload = EXCLUDED.package_payload,
            room_attributes = EXCLUDED.room_attributes,
            discounted_price_per_night_eur = EXCLUDED.discounted_price_per_night_eur,
            discount_pct = EXCLUDED.discount_pct,
            discount_label = EXCLUDED.discount_label,
            has_genius_discount = EXCLUDED.has_genius_discount,
            cancellation_type = EXCLUDED.cancellation_type,
            payment_label = EXCLUDED.payment_label,
            rate_block_id = EXCLUDED.rate_block_id
        """
    ).bindparams(
        bindparam("package_payload", type_=JSONB),
        bindparam("room_attributes", type_=JSONB),
    )
    connection.execute(
        statement,
        {
            "id": uuid.uuid4(),
            "rate_observation_id": observation_id,
            "source_record_id": rate.source_record_id,
            "room_type": rate.room_type,
            "room_type_category": rate.room_type_category,
            "meals": rate.meals,
            "free_cancellation": rate.free_cancellation,
            "price_per_night_eur": rate.price_per_night_eur,
            "price_total_eur": rate.price_total_eur,
            "rooms_left": rate.rooms_left,
            "package_payload": rate.raw_payload,
            "room_attributes": rate.room_attributes,
            "discounted_price_per_night_eur": rate.discounted_price_per_night_eur,
            "discount_pct": rate.discount_pct,
            "discount_label": rate.discount_label,
            "has_genius_discount": rate.has_genius_discount,
            "cancellation_type": rate.cancellation_type,
            "payment_label": rate.payment_label,
            "rate_block_id": rate.rate_block_id,
        },
    )


def upsert_amenities_bulk(connection: Any, amenities_by_property: dict[uuid.UUID, set[str]]) -> None:
    """Write all amenities for a batch with three set-based statements.

    Replaces the old row-by-row upsert (2 round-trips per amenity per rate row)
    with: one dimension insert, one property-link insert and one write-time
    refresh of ``roomrate_properties.amenities_cached``.
    """
    if not amenities_by_property:
        return

    # Deduplicate display names by normalized key across the whole batch.
    names_by_normalized: dict[str, str] = {}
    for amenities in amenities_by_property.values():
        for amenity in amenities:
            names_by_normalized.setdefault(normalize_amenity(amenity), amenity)
    if not names_by_normalized:
        return
    sorted_names = sorted(names_by_normalized.values())
    sorted_normalized = [normalize_amenity(name) for name in sorted_names]

    connection.execute(
        text(
            """
            INSERT INTO roomrate_amenities (id, name, normalized_name)
            SELECT gen_random_uuid(), incoming.name, incoming.normalized_name
            FROM unnest(
                CAST(:names AS text[]),
                CAST(:normalized_names AS text[])
            ) AS incoming(name, normalized_name)
            ON CONFLICT (normalized_name) DO NOTHING
            """
        ),
        {"names": sorted_names, "normalized_names": sorted_normalized},
    )

    # One (property_id, normalized_name) pair per link row, joined back to the
    # dimension to resolve amenity ids in SQL instead of per-row round-trips.
    link_property_ids: list[str] = []
    link_normalized_names: list[str] = []
    for property_id, amenities in amenities_by_property.items():
        for normalized_name in sorted({normalize_amenity(amenity) for amenity in amenities}):
            link_property_ids.append(str(property_id))
            link_normalized_names.append(normalized_name)
    connection.execute(
        text(
            """
            INSERT INTO roomrate_property_amenities (property_id, amenity_id)
            SELECT pairs.property_id, a.id
            FROM unnest(
                CAST(:property_ids AS uuid[]),
                CAST(:normalized_names AS text[])
            ) AS pairs(property_id, normalized_name)
            JOIN roomrate_amenities a ON a.normalized_name = pairs.normalized_name
            ON CONFLICT (property_id, amenity_id) DO NOTHING
            """
        ),
        {"property_ids": link_property_ids, "normalized_names": link_normalized_names},
    )

    # Refresh the read-optimized cache from the bridge table (source of truth)
    # so it also includes amenities discovered by earlier runs.
    connection.execute(
        text(
            """
            UPDATE roomrate_properties p
            SET amenities_cached = cached.facilities, updated_at = now()
            FROM (
                SELECT pa.property_id, string_agg(a.name, '|' ORDER BY a.name) AS facilities
                FROM roomrate_property_amenities pa
                JOIN roomrate_amenities a ON a.id = pa.amenity_id
                WHERE pa.property_id = ANY(CAST(:property_ids AS uuid[]))
                GROUP BY pa.property_id
            ) cached
            WHERE p.id = cached.property_id
            """
        ),
        {"property_ids": [str(property_id) for property_id in amenities_by_property]},
    )


def insert_raw_event(connection: Any, rate: NormalizedRoomRate, scrape_run_id: uuid.UUID) -> None:
    statement = text(
        """
        INSERT INTO roomrate_raw_ingestion_events (
            id, scrape_run_id, source, source_run_id, payload_hash, captured_at, payload
        )
        VALUES (:id, :scrape_run_id, :source, :source_run_id, :payload_hash, :captured_at, :payload)
        ON CONFLICT (payload_hash) DO NOTHING
        """
    ).bindparams(bindparam("payload", type_=JSONB))
    connection.execute(
        statement,
        {
            "id": uuid.uuid4(),
            "scrape_run_id": scrape_run_id,
            "source": rate.provider,
            "source_run_id": rate.source_run_key,
            "payload_hash": rate.payload_hash,
            "captured_at": rate.observed_at,
            "payload": rate.raw_payload,
        },
    )


# ---------------------------------------------------------------------------
# Set-based writes for a whole batch. The row-by-row helpers above made three
# round trips per package (observation, package, raw event): instant on a
# local PostgreSQL, ~4 minutes for 2,000 packages against Neon at ~40 ms per
# round trip. Each helper below is ONE statement over unnest()-ed arrays and
# keeps the row-by-row semantics: the same LEAST/GREATEST/COALESCE merge for
# observations, the last row winning for a repeated package, DO NOTHING for a
# repeated raw payload.
# ---------------------------------------------------------------------------


def _json_or_none(value: Any) -> str | None:
    # json.dumps, as SQLAlchemy's JSONB bind uses for the row-by-row writes.
    return None if value is None else json.dumps(value)


def upsert_properties_bulk(connection: Any, rates: list[NormalizedRoomRate]) -> dict[str, uuid.UUID]:
    """Every hotel of the batch in one upsert; returns its id keyed by source_property_key.

    The FIRST rate of a hotel supplies its fields, as the per-key cache of
    the row-by-row loop did (every package of a hotel carries the same ones).
    """
    first: dict[str, NormalizedRoomRate] = {}
    for rate in rates:
        first.setdefault(rate.source_property_key, rate)
    if not first:
        return {}
    hotels = list(first.values())
    result = connection.execute(
        text(
            """
            INSERT INTO roomrate_properties (
                id, provider, source_property_key, canonical_name, display_name,
                city, country, address, property_type, latitude, longitude, stars, booking_url
            )
            SELECT gen_random_uuid(), p.provider, p.source_property_key, p.canonical_name, p.display_name,
                   p.city, p.country, p.address, p.property_type, p.latitude, p.longitude, p.stars,
                   p.booking_url
            FROM unnest(
                CAST(:providers AS text[]),
                CAST(:source_property_keys AS text[]),
                CAST(:canonical_names AS text[]),
                CAST(:display_names AS text[]),
                CAST(:cities AS text[]),
                CAST(:countries AS text[]),
                CAST(:addresses AS text[]),
                CAST(:property_types AS text[]),
                CAST(:latitudes AS numeric[]),
                CAST(:longitudes AS numeric[]),
                CAST(:stars AS numeric[]),
                CAST(:booking_urls AS text[])
            ) AS p(provider, source_property_key, canonical_name, display_name, city, country, address,
                   property_type, latitude, longitude, stars, booking_url)
            ON CONFLICT (provider, source_property_key) DO UPDATE SET
                updated_at = now(),
                display_name = EXCLUDED.display_name,
                city = EXCLUDED.city,
                country = COALESCE(EXCLUDED.country, roomrate_properties.country),
                address = COALESCE(EXCLUDED.address, roomrate_properties.address),
                property_type = COALESCE(EXCLUDED.property_type, roomrate_properties.property_type),
                latitude = COALESCE(EXCLUDED.latitude, roomrate_properties.latitude),
                longitude = COALESCE(EXCLUDED.longitude, roomrate_properties.longitude),
                stars = COALESCE(EXCLUDED.stars, roomrate_properties.stars),
                booking_url = COALESCE(EXCLUDED.booking_url, roomrate_properties.booking_url)
            RETURNING id, source_property_key
            """
        ),
        {
            "providers": [rate.provider for rate in hotels],
            "source_property_keys": [rate.source_property_key for rate in hotels],
            "canonical_names": [rate.canonical_name for rate in hotels],
            "display_names": [rate.display_name for rate in hotels],
            "cities": [rate.city for rate in hotels],
            "countries": [rate.country for rate in hotels],
            "addresses": [rate.address for rate in hotels],
            "property_types": [rate.property_type for rate in hotels],
            "latitudes": [rate.latitude for rate in hotels],
            "longitudes": [rate.longitude for rate in hotels],
            "stars": [rate.stars for rate in hotels],
            "booking_urls": [rate.booking_url for rate in hotels],
        },
    )
    return {row.source_property_key: row.id for row in result}


def upsert_observations_bulk(
    connection: Any,
    rows: list[tuple[NormalizedRoomRate, uuid.UUID, uuid.UUID]],
) -> dict[tuple[str, str], uuid.UUID]:
    """One observation per (run, property); returns its id keyed by (run id, property id).

    The batch is merged first exactly as successive upserts would merge it:
    the lowest/highest price, the lowest known rooms_left, the last known
    review score/count, the first observed_at (never updated on conflict).
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for rate, scrape_run_id, property_id in rows:
        key = (str(scrape_run_id), str(property_id))
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "observed_at": rate.observed_at,
                "review_score": rate.review_score,
                "review_count": rate.review_count,
                "rooms_left": rate.rooms_left,
                "price_min": rate.price_per_night_eur,
                "price_max": rate.price_per_night_eur,
            }
            continue
        if rate.review_score is not None:
            current["review_score"] = rate.review_score
        if rate.review_count is not None:
            current["review_count"] = rate.review_count
        if rate.rooms_left is not None:
            current["rooms_left"] = (
                rate.rooms_left if current["rooms_left"] is None else min(current["rooms_left"], rate.rooms_left)
            )
        current["price_min"] = min(current["price_min"], rate.price_per_night_eur)
        current["price_max"] = max(current["price_max"], rate.price_per_night_eur)
    if not merged:
        return {}
    keys = list(merged)
    result = connection.execute(
        text(
            """
            INSERT INTO roomrate_rate_observations (
                id, scrape_run_id, property_id, observed_at, review_score, review_count,
                rooms_left_min, price_min_eur, price_max_eur
            )
            SELECT gen_random_uuid(), o.scrape_run_id, o.property_id, o.observed_at, o.review_score,
                   o.review_count, o.rooms_left, o.price_min, o.price_max
            FROM unnest(
                CAST(:scrape_run_ids AS uuid[]),
                CAST(:property_ids AS uuid[]),
                CAST(:observed_at AS timestamptz[]),
                CAST(:review_scores AS numeric[]),
                CAST(:review_counts AS integer[]),
                CAST(:rooms_left AS integer[]),
                CAST(:price_min AS numeric[]),
                CAST(:price_max AS numeric[])
            ) AS o(scrape_run_id, property_id, observed_at, review_score, review_count,
                   rooms_left, price_min, price_max)
            ON CONFLICT (scrape_run_id, property_id) DO UPDATE SET
                updated_at = now(),
                review_score = COALESCE(EXCLUDED.review_score, roomrate_rate_observations.review_score),
                review_count = COALESCE(EXCLUDED.review_count, roomrate_rate_observations.review_count),
                rooms_left_min = LEAST(
                    COALESCE(roomrate_rate_observations.rooms_left_min, EXCLUDED.rooms_left_min),
                    COALESCE(EXCLUDED.rooms_left_min, roomrate_rate_observations.rooms_left_min)
                ),
                price_min_eur = LEAST(roomrate_rate_observations.price_min_eur, EXCLUDED.price_min_eur),
                price_max_eur = GREATEST(roomrate_rate_observations.price_max_eur, EXCLUDED.price_max_eur)
            RETURNING id, scrape_run_id, property_id
            """
        ),
        {
            "scrape_run_ids": [run_id for run_id, _ in keys],
            "property_ids": [property_id for _, property_id in keys],
            "observed_at": [merged[key]["observed_at"] for key in keys],
            "review_scores": [merged[key]["review_score"] for key in keys],
            "review_counts": [merged[key]["review_count"] for key in keys],
            "rooms_left": [merged[key]["rooms_left"] for key in keys],
            "price_min": [merged[key]["price_min"] for key in keys],
            "price_max": [merged[key]["price_max"] for key in keys],
        },
    )
    return {(str(row.scrape_run_id), str(row.property_id)): row.id for row in result}


_PACKAGE_COLUMNS = (
    "source_record_id", "room_type", "room_type_category", "meals", "free_cancellation",
    "price_per_night_eur", "price_total_eur", "rooms_left", "package_payload", "room_attributes",
    "discounted_price_per_night_eur", "discount_pct", "discount_label", "has_genius_discount",
    "cancellation_type", "payment_label", "rate_block_id",
)
_PACKAGE_ARRAY_TYPES = {
    "source_record_id": "text", "room_type": "text", "room_type_category": "text", "meals": "text",
    "free_cancellation": "text", "price_per_night_eur": "numeric", "price_total_eur": "numeric",
    "rooms_left": "integer", "package_payload": "jsonb", "room_attributes": "jsonb",
    "discounted_price_per_night_eur": "numeric", "discount_pct": "numeric", "discount_label": "text",
    "has_genius_discount": "boolean", "cancellation_type": "text", "payment_label": "text",
    "rate_block_id": "text",
}


def upsert_packages_bulk(connection: Any, rows: list[tuple[NormalizedRoomRate, uuid.UUID]]) -> None:
    """Every package of the batch in one upsert; a repeated package keeps its last row."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for rate, observation_id in rows:
        latest[(str(observation_id), rate.source_record_id)] = {
            "source_record_id": rate.source_record_id,
            "room_type": rate.room_type,
            "room_type_category": rate.room_type_category,
            "meals": rate.meals,
            "free_cancellation": rate.free_cancellation,
            "price_per_night_eur": rate.price_per_night_eur,
            "price_total_eur": rate.price_total_eur,
            "rooms_left": rate.rooms_left,
            "package_payload": _json_or_none(rate.raw_payload),
            "room_attributes": _json_or_none(rate.room_attributes),
            "discounted_price_per_night_eur": rate.discounted_price_per_night_eur,
            "discount_pct": rate.discount_pct,
            "discount_label": rate.discount_label,
            "has_genius_discount": rate.has_genius_discount,
            "cancellation_type": rate.cancellation_type,
            "payment_label": rate.payment_label,
            "rate_block_id": rate.rate_block_id,
        }
    if not latest:
        return
    keys = list(latest)
    columns = ", ".join(_PACKAGE_COLUMNS)
    arrays = ",\n                ".join(
        f"CAST(:{column} AS {_PACKAGE_ARRAY_TYPES[column]}[])" for column in _PACKAGE_COLUMNS
    )
    updates = ",\n                ".join(
        f"{column} = EXCLUDED.{column}" for column in _PACKAGE_COLUMNS if column != "source_record_id"
    )
    params: dict[str, Any] = {
        "observation_ids": [observation_id for observation_id, _ in keys],
        **{column: [latest[key][column] for key in keys] for column in _PACKAGE_COLUMNS},
    }
    connection.execute(
        text(
            f"""
            INSERT INTO roomrate_room_packages (id, rate_observation_id, {columns})
            SELECT gen_random_uuid(), p.rate_observation_id, {", ".join(f"p.{c}" for c in _PACKAGE_COLUMNS)}
            FROM unnest(
                CAST(:observation_ids AS uuid[]),
                {arrays}
            ) AS p(rate_observation_id, {columns})
            ON CONFLICT (rate_observation_id, source_record_id) DO UPDATE SET
                updated_at = now(),
                {updates}
            """
        ),
        params,
    )


def insert_raw_events_bulk(connection: Any, rows: list[tuple[NormalizedRoomRate, uuid.UUID]]) -> None:
    """Every raw payload of the batch in one insert; a known payload hash is skipped."""
    if not rows:
        return
    connection.execute(
        text(
            """
            INSERT INTO roomrate_raw_ingestion_events (
                id, scrape_run_id, source, source_run_id, payload_hash, captured_at, payload
            )
            SELECT gen_random_uuid(), e.scrape_run_id, e.source, e.source_run_id, e.payload_hash,
                   e.captured_at, e.payload
            FROM unnest(
                CAST(:scrape_run_ids AS uuid[]),
                CAST(:sources AS text[]),
                CAST(:source_run_ids AS text[]),
                CAST(:payload_hashes AS text[]),
                CAST(:captured_at AS timestamptz[]),
                CAST(:payloads AS jsonb[])
            ) AS e(scrape_run_id, source, source_run_id, payload_hash, captured_at, payload)
            ON CONFLICT (payload_hash) DO NOTHING
            """
        ),
        {
            "scrape_run_ids": [str(scrape_run_id) for _, scrape_run_id in rows],
            "sources": [rate.provider for rate, _ in rows],
            "source_run_ids": [rate.source_run_key for rate, _ in rows],
            "payload_hashes": [rate.payload_hash for rate, _ in rows],
            "captured_at": [rate.observed_at for rate, _ in rows],
            "payloads": [_json_or_none(rate.raw_payload) for rate, _ in rows],
        },
    )


def job_source_run_key(rate: NormalizedRoomRate, scrape_job_id: uuid.UUID) -> str:
    """Run key for a whole scrape job: the job plus the stay and party it searched.

    Replaces the normalizer's per-city ``source_run_key`` for job batches.
    The Round 6 nearby-area scouts return hotels whose Booking city is Ιξιά
    or Κολύμπια next to Φαληράκι, and per-city runs split one search into
    fragments that pricing, price history and alerts each took for "the
    latest run". No scrape timestamp either, so a retried attempt of the same
    job rewrites the same run instead of adding one.
    """
    return "|".join(
        [
            rate.provider,
            f"job:{scrape_job_id}",
            rate.check_in.isoformat(),
            rate.check_out.isoformat(),
            str(rate.adults),
            str(rate.children),
            str(rate.rooms),
        ]
    )


def write_normalized_rates(
    rates: list[NormalizedRoomRate],
    account_id: uuid.UUID | str = DEFAULT_ACCOUNT_ID,
    ingestion_source: str = "scraper",
    scrape_job_id: uuid.UUID | str | None = None,
    raw_destination: str | None = None,
    canonical_destination: str | None = None,
) -> int:
    """Write normalized rates inside one transaction.

    With ``scrape_job_id`` every rate of the batch joins ONE scrape run
    (``job_source_run_key``) whose destination is ``raw_destination`` (the
    job's) when given, else the first rate's. Without it (backfill scripts,
    legacy callers) each normalizer run key, one per city and scrape time,
    keeps its own run as before. ``source_property_key`` is never touched.
    """
    resolved_account_id = account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))
    resolved_scrape_job_id = (
        scrape_job_id
        if isinstance(scrape_job_id, uuid.UUID) or scrape_job_id is None
        else uuid.UUID(str(scrape_job_id))
    )
    job_destination = (raw_destination or "").strip()
    # One scrape-run upsert per run key (the job, or the normalizer's per-city
    # key without a job), then set-based writes for the hotels (one row per
    # source property key: every package of a hotel in a live scrape carries
    # identical property fields), observations, packages, raw events and
    # amenities. Caveat: within one batch the FIRST occurrence of a
    # key wins — callers that replay historical rows spanning many runs
    # (scripts/backfill_roomrate_market.py) end up with the oldest row's
    # property metadata until the next live scrape refreshes it.
    scrape_run_ids: dict[tuple[str, str], uuid.UUID] = {}
    amenities_by_property: dict[uuid.UUID, set[str]] = {}
    with_runs: list[tuple[NormalizedRoomRate, uuid.UUID]] = []
    # Writer role: no API statement timeout — bulk scrape writes can be long.
    with get_engine(role="writer").begin() as connection:
        for normalized_rate in rates:
            if resolved_scrape_job_id is not None:
                # The run key and destination describe the job's search; the
                # raw event's source_run_id follows the run it is linked to.
                rate = replace(
                    normalized_rate,
                    source_run_key=job_source_run_key(normalized_rate, resolved_scrape_job_id),
                    destination=job_destination or normalized_rate.destination,
                )
                run_key = (rate.provider, str(resolved_scrape_job_id))
            else:
                rate = normalized_rate
                run_key = (rate.provider, rate.source_run_key)
            if run_key not in scrape_run_ids:
                scrape_run_ids[run_key] = upsert_scrape_run(
                    connection,
                    rate,
                    resolved_account_id,
                    ingestion_source=ingestion_source,
                    scrape_job_id=resolved_scrape_job_id,
                    raw_destination=raw_destination,
                    canonical_destination=canonical_destination,
                )
            with_runs.append((rate, scrape_run_ids[run_key]))
        # One statement each for every hotel, observation, package and raw
        # event of the batch, instead of round trips per row.
        property_ids = upsert_properties_bulk(connection, [rate for rate, _ in with_runs])
        resolved = [
            (rate, scrape_run_id, property_ids[rate.source_property_key]) for rate, scrape_run_id in with_runs
        ]
        for rate, _, property_id in resolved:
            if rate.amenities:
                amenities_by_property.setdefault(property_id, set()).update(rate.amenities)
        observation_ids = upsert_observations_bulk(connection, resolved)
        upsert_packages_bulk(
            connection,
            [
                (rate, observation_ids[(str(scrape_run_id), str(property_id))])
                for rate, scrape_run_id, property_id in resolved
            ],
        )
        insert_raw_events_bulk(connection, [(rate, scrape_run_id) for rate, scrape_run_id, _ in resolved])
        upsert_amenities_bulk(connection, amenities_by_property)
    return len(rates)
