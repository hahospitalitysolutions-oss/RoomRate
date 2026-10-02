"""Single source of truth for RoomRate SQL view definitions.

Alembic migrations import these constants (``migrations/env.py`` already
imports from ``api``), so the view SQL lives in exactly one place and
upgrade/downgrade pairs cannot drift apart.

Versions:
    * ``LATEST_ROOM_RATES_V1`` — verbatim copy of the view created by
      migration 20260521_0006 (canonical-destination variant).
    * ``LATEST_ROOM_RATES_V2`` — V1 plus ``rp.room_attributes`` in the select
      list (migration 20260612_0010); everything else is identical.
    * ``LATEST_ROOM_RATES_V3`` — V2 with the market partition keyed on
      ``sr.canonical_destination`` directly (NOT NULL since 20260521_0006; the
      COALESCE fallback prevented predicate pushdown into the window subquery,
      forcing every view query to rank ALL of an account's completed runs) and
      with ``nights``/``guests`` computed from their source columns after
      migration 20260707_0014 dropped them from ``roomrate_scrape_runs``.
    * ``COMPETITOR_MARKERS_V1`` — verbatim copy of the view created by
      migration 20260521_0006 (lived only in that migration until 0014).
    * ``COMPETITOR_MARKERS_V2`` — V1 with the same canonical-destination
      partition fix as ``LATEST_ROOM_RATES_V3``.
"""

LATEST_ROOM_RATES_VIEW_NAME = "roomrate_latest_room_rates"
COMPETITOR_MARKERS_VIEW_NAME = "roomrate_competitor_markers"

LATEST_ROOM_RATES_V1 = """
CREATE VIEW roomrate_latest_room_rates AS
WITH latest_runs AS (
    SELECT id
    FROM (
        SELECT
            sr.id,
            row_number() OVER (
                PARTITION BY
                    sr.account_id,
                    COALESCE(sr.canonical_destination, sr.destination),
                    sr.check_in,
                    sr.check_out,
                    sr.adults,
                    sr.children,
                    sr.rooms
                ORDER BY
                    COALESCE(sr.finished_at, sr.started_at, sr.created_at) DESC,
                    sr.created_at DESC,
                    sr.id DESC
            ) AS rn
        FROM roomrate_scrape_runs sr
        WHERE sr.status = 'completed'
    ) ranked_runs
    WHERE rn = 1
)
SELECT
    sr.account_id,
    sr.raw_destination,
    sr.canonical_destination,
    p.id AS property_id,
    rp.id AS room_package_id,
    rp.source_record_id AS record_id,
    ro.observed_at AS scraped_at,
    sr.check_in,
    sr.check_out,
    p.display_name AS hotel_name,
    p.city,
    p.address,
    p.property_type,
    p.latitude,
    p.longitude,
    p.stars,
    ro.review_score,
    ro.review_count,
    rp.price_per_night_eur,
    sr.nights,
    sr.guests,
    sr.adults,
    sr.children,
    sr.rooms,
    rp.room_type,
    rp.room_type_category,
    rp.meals,
    rp.free_cancellation,
    rp.price_total_eur,
    COALESCE(amenities.facilities, '') AS facilities,
    rp.rooms_left
FROM roomrate_room_packages rp
JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
JOIN latest_runs lr ON lr.id = sr.id
JOIN roomrate_properties p ON p.id = ro.property_id
LEFT JOIN LATERAL (
    SELECT string_agg(a.name, '|' ORDER BY a.name) AS facilities
    FROM roomrate_property_amenities pa
    JOIN roomrate_amenities a ON a.id = pa.amenity_id
    WHERE pa.property_id = p.id
) amenities ON TRUE;
"""

# V2 = V1 + rp.room_attributes in the select list. Everything else identical.
LATEST_ROOM_RATES_V2 = """
CREATE VIEW roomrate_latest_room_rates AS
WITH latest_runs AS (
    SELECT id
    FROM (
        SELECT
            sr.id,
            row_number() OVER (
                PARTITION BY
                    sr.account_id,
                    COALESCE(sr.canonical_destination, sr.destination),
                    sr.check_in,
                    sr.check_out,
                    sr.adults,
                    sr.children,
                    sr.rooms
                ORDER BY
                    COALESCE(sr.finished_at, sr.started_at, sr.created_at) DESC,
                    sr.created_at DESC,
                    sr.id DESC
            ) AS rn
        FROM roomrate_scrape_runs sr
        WHERE sr.status = 'completed'
    ) ranked_runs
    WHERE rn = 1
)
SELECT
    sr.account_id,
    sr.raw_destination,
    sr.canonical_destination,
    p.id AS property_id,
    rp.id AS room_package_id,
    rp.source_record_id AS record_id,
    ro.observed_at AS scraped_at,
    sr.check_in,
    sr.check_out,
    p.display_name AS hotel_name,
    p.city,
    p.address,
    p.property_type,
    p.latitude,
    p.longitude,
    p.stars,
    ro.review_score,
    ro.review_count,
    rp.price_per_night_eur,
    sr.nights,
    sr.guests,
    sr.adults,
    sr.children,
    sr.rooms,
    rp.room_type,
    rp.room_type_category,
    rp.room_attributes,
    rp.meals,
    rp.free_cancellation,
    rp.price_total_eur,
    COALESCE(amenities.facilities, '') AS facilities,
    rp.rooms_left
FROM roomrate_room_packages rp
JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
JOIN latest_runs lr ON lr.id = sr.id
JOIN roomrate_properties p ON p.id = ro.property_id
LEFT JOIN LATERAL (
    SELECT string_agg(a.name, '|' ORDER BY a.name) AS facilities
    FROM roomrate_property_amenities pa
    JOIN roomrate_amenities a ON a.id = pa.amenity_id
    WHERE pa.property_id = p.id
) amenities ON TRUE;
"""

# V3 = V2 with the partition keyed on canonical_destination directly and with
# nights/guests derived (3NF: migration 0014 dropped the stored copies).
# Output column names, order and types are identical to V2: date - date and
# int + int are both integers, matching the dropped column types.
#
# security_invoker (PostgreSQL 15+) is load-bearing, not cosmetic: without it
# the view executes as its OWNER, who is exempt from the RLS that migration
# 0019 enabled on every base table — so the Supabase anon role would still
# read every tenant's rates through this view. The API is unaffected either
# way (it connects as the owner and scopes by account_id itself).
LATEST_ROOM_RATES_V3 = """
CREATE VIEW roomrate_latest_room_rates WITH (security_invoker = true) AS
WITH latest_runs AS (
    SELECT id
    FROM (
        SELECT
            sr.id,
            row_number() OVER (
                PARTITION BY
                    sr.account_id,
                    sr.canonical_destination,
                    sr.check_in,
                    sr.check_out,
                    sr.adults,
                    sr.children,
                    sr.rooms
                ORDER BY
                    COALESCE(sr.finished_at, sr.started_at, sr.created_at) DESC,
                    sr.created_at DESC,
                    sr.id DESC
            ) AS rn
        FROM roomrate_scrape_runs sr
        WHERE sr.status = 'completed'
    ) ranked_runs
    WHERE rn = 1
)
SELECT
    sr.account_id,
    sr.raw_destination,
    sr.canonical_destination,
    p.id AS property_id,
    rp.id AS room_package_id,
    rp.source_record_id AS record_id,
    ro.observed_at AS scraped_at,
    sr.check_in,
    sr.check_out,
    p.display_name AS hotel_name,
    p.city,
    p.address,
    p.property_type,
    p.latitude,
    p.longitude,
    p.stars,
    ro.review_score,
    ro.review_count,
    rp.price_per_night_eur,
    (sr.check_out - sr.check_in) AS nights,
    (sr.adults + sr.children) AS guests,
    sr.adults,
    sr.children,
    sr.rooms,
    rp.room_type,
    rp.room_type_category,
    rp.room_attributes,
    rp.meals,
    rp.free_cancellation,
    rp.price_total_eur,
    COALESCE(amenities.facilities, '') AS facilities,
    rp.rooms_left
FROM roomrate_room_packages rp
JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
JOIN latest_runs lr ON lr.id = sr.id
JOIN roomrate_properties p ON p.id = ro.property_id
LEFT JOIN LATERAL (
    SELECT string_agg(a.name, '|' ORDER BY a.name) AS facilities
    FROM roomrate_property_amenities pa
    JOIN roomrate_amenities a ON a.id = pa.amenity_id
    WHERE pa.property_id = p.id
) amenities ON TRUE;
"""

# Verbatim copy of the view created by migration 20260521_0006 (needed by
# migration 0014's downgrade).
COMPETITOR_MARKERS_V1 = """
CREATE VIEW roomrate_competitor_markers AS
SELECT *
FROM (
    SELECT
        sr.account_id,
        sr.raw_destination,
        sr.canonical_destination,
        p.id AS property_id,
        rp.id AS room_package_id,
        p.display_name AS hotel_name,
        p.property_type,
        p.latitude,
        p.longitude,
        rp.room_type,
        rp.room_type_category,
        rp.price_per_night_eur,
        ro.review_score,
        ro.review_count,
        rp.rooms_left,
        sr.destination,
        sr.check_in,
        sr.check_out,
        row_number() OVER (
            PARTITION BY
                sr.account_id,
                p.id,
                rp.room_type_category,
                COALESCE(sr.canonical_destination, sr.destination),
                sr.check_in,
                sr.check_out,
                sr.adults,
                sr.children,
                sr.rooms
            ORDER BY ro.observed_at DESC, rp.price_per_night_eur ASC
        ) AS rn
    FROM roomrate_room_packages rp
    JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
    JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
    JOIN roomrate_properties p ON p.id = ro.property_id
    WHERE sr.id IN (
        SELECT id
        FROM (
            SELECT
                latest_sr.id,
                row_number() OVER (
                    PARTITION BY
                        latest_sr.account_id,
                        COALESCE(latest_sr.canonical_destination, latest_sr.destination),
                        latest_sr.check_in,
                        latest_sr.check_out,
                        latest_sr.adults,
                        latest_sr.children,
                        latest_sr.rooms
                    ORDER BY
                        COALESCE(latest_sr.finished_at, latest_sr.started_at, latest_sr.created_at) DESC,
                        latest_sr.created_at DESC,
                        latest_sr.id DESC
                ) AS latest_rn
            FROM roomrate_scrape_runs latest_sr
            WHERE latest_sr.status = 'completed'
        ) ranked_latest_runs
        WHERE latest_rn = 1
    )
      AND p.latitude IS NOT NULL
      AND p.longitude IS NOT NULL
) ranked
WHERE rn = 1;
"""

# V2 = V1 with both window partitions keyed on canonical_destination directly
# (same rationale as LATEST_ROOM_RATES_V3). Output columns are identical.
COMPETITOR_MARKERS_V2 = """
CREATE VIEW roomrate_competitor_markers WITH (security_invoker = true) AS
SELECT *
FROM (
    SELECT
        sr.account_id,
        sr.raw_destination,
        sr.canonical_destination,
        p.id AS property_id,
        rp.id AS room_package_id,
        p.display_name AS hotel_name,
        p.property_type,
        p.latitude,
        p.longitude,
        rp.room_type,
        rp.room_type_category,
        rp.price_per_night_eur,
        ro.review_score,
        ro.review_count,
        rp.rooms_left,
        sr.destination,
        sr.check_in,
        sr.check_out,
        row_number() OVER (
            PARTITION BY
                sr.account_id,
                p.id,
                rp.room_type_category,
                sr.canonical_destination,
                sr.check_in,
                sr.check_out,
                sr.adults,
                sr.children,
                sr.rooms
            ORDER BY ro.observed_at DESC, rp.price_per_night_eur ASC
        ) AS rn
    FROM roomrate_room_packages rp
    JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
    JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
    JOIN roomrate_properties p ON p.id = ro.property_id
    WHERE sr.id IN (
        SELECT id
        FROM (
            SELECT
                latest_sr.id,
                row_number() OVER (
                    PARTITION BY
                        latest_sr.account_id,
                        latest_sr.canonical_destination,
                        latest_sr.check_in,
                        latest_sr.check_out,
                        latest_sr.adults,
                        latest_sr.children,
                        latest_sr.rooms
                    ORDER BY
                        COALESCE(latest_sr.finished_at, latest_sr.started_at, latest_sr.created_at) DESC,
                        latest_sr.created_at DESC,
                        latest_sr.id DESC
                ) AS latest_rn
            FROM roomrate_scrape_runs latest_sr
            WHERE latest_sr.status = 'completed'
        ) ranked_latest_runs
        WHERE latest_rn = 1
    )
      AND p.latitude IS NOT NULL
      AND p.longitude IS NOT NULL
) ranked
WHERE rn = 1;
"""
