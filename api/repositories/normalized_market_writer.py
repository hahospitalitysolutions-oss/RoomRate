from __future__ import annotations

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
    # Per-batch caches: one scrape-run upsert per run key (the job, or the
    # normalizer's per-city key without a job) and one property upsert per
    # source property key (every package of a hotel in a live scrape carries
    # identical property fields), plus one set-based amenity write at the end
    # of the transaction. Caveat: within one batch the FIRST occurrence of a
    # key wins — callers that replay historical rows spanning many runs
    # (scripts/backfill_roomrate_market.py) end up with the oldest row's
    # property metadata until the next live scrape refreshes it.
    scrape_run_ids: dict[tuple[str, str], uuid.UUID] = {}
    property_ids: dict[str, uuid.UUID] = {}
    amenities_by_property: dict[uuid.UUID, set[str]] = {}
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
            scrape_run_id = scrape_run_ids[run_key]
            if rate.source_property_key not in property_ids:
                property_ids[rate.source_property_key] = upsert_property(connection, rate)
            property_id = property_ids[rate.source_property_key]
            observation_id = upsert_observation(connection, rate, scrape_run_id, property_id)
            upsert_package(connection, rate, observation_id)
            if rate.amenities:
                amenities_by_property.setdefault(property_id, set()).update(rate.amenities)
            insert_raw_event(connection, rate, scrape_run_id)
        upsert_amenities_bulk(connection, amenities_by_property)
    return len(rates)
