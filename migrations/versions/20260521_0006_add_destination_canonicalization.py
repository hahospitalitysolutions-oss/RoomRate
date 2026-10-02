"""Add raw and canonical destination workflow fields.

Revision ID: 20260521_0006
Revises: 20260517_0005
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260521_0006"
down_revision: str | None = "20260517_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_expression(column_name: str) -> str:
    return f"""
        CASE
            WHEN lower({column_name}) IN ('faliraki', 'φαληράκι', 'φαληρακι') THEN 'faliraki'
            WHEN lower({column_name}) IN ('rhodes', 'rodos', 'ρόδος', 'ροδος') THEN 'rhodes'
            WHEN lower({column_name}) IN ('santorini', 'σαντορίνη', 'σαντορινη') THEN 'santorini'
            WHEN lower({column_name}) IN ('athens', 'athina', 'αθήνα', 'αθηνα') THEN 'athens'
            ELSE lower(regexp_replace({column_name}, '\\s+', '', 'g'))
        END
    """


def _create_canonical_views() -> None:
    op.execute(
        """
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
    )
    op.execute(
        """
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
    )


def _create_previous_views() -> None:
    op.execute(
        """
        CREATE VIEW roomrate_latest_room_rates AS
        WITH latest_runs AS (
            SELECT id
            FROM (
                SELECT
                    sr.id,
                    row_number() OVER (
                        PARTITION BY
                            sr.account_id,
                            sr.destination,
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
    )
    op.execute(
        """
        CREATE VIEW roomrate_competitor_markers AS
        SELECT *
        FROM (
            SELECT
                sr.account_id,
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
                        sr.destination,
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
                                latest_sr.destination,
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
    )


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")

    op.add_column("roomrate_owned_properties", sa.Column("raw_destination", sa.String(length=255), nullable=True))
    op.add_column("roomrate_owned_properties", sa.Column("canonical_destination", sa.String(length=255), nullable=True))
    op.add_column("roomrate_scrape_jobs", sa.Column("raw_destination", sa.String(length=255), nullable=True))
    op.add_column("roomrate_scrape_jobs", sa.Column("canonical_destination", sa.String(length=255), nullable=True))
    op.add_column("roomrate_scrape_runs", sa.Column("raw_destination", sa.String(length=255), nullable=True))
    op.add_column("roomrate_scrape_runs", sa.Column("canonical_destination", sa.String(length=255), nullable=True))

    op.execute(
        f"""
        UPDATE roomrate_owned_properties
        SET
            raw_destination = COALESCE(raw_destination, city),
            canonical_destination = COALESCE(canonical_destination, {_canonical_expression('city')})
        """
    )
    op.execute(
        f"""
        UPDATE roomrate_scrape_jobs
        SET
            raw_destination = COALESCE(raw_destination, destination),
            canonical_destination = COALESCE(canonical_destination, {_canonical_expression('destination')})
        """
    )
    op.execute(
        f"""
        UPDATE roomrate_scrape_runs
        SET
            raw_destination = COALESCE(raw_destination, destination),
            canonical_destination = COALESCE(canonical_destination, {_canonical_expression('destination')})
        """
    )

    op.alter_column("roomrate_owned_properties", "raw_destination", nullable=False)
    op.alter_column("roomrate_owned_properties", "canonical_destination", nullable=False)
    op.alter_column("roomrate_scrape_jobs", "raw_destination", nullable=False)
    op.alter_column("roomrate_scrape_jobs", "canonical_destination", nullable=False)
    op.alter_column("roomrate_scrape_runs", "raw_destination", nullable=False)
    op.alter_column("roomrate_scrape_runs", "canonical_destination", nullable=False)

    op.create_index(
        "ix_roomrate_owned_properties_account_canonical_destination",
        "roomrate_owned_properties",
        ["account_id", "canonical_destination"],
    )
    op.create_index(
        "ix_roomrate_scrape_jobs_account_canonical_status",
        "roomrate_scrape_jobs",
        ["account_id", "canonical_destination", "status"],
    )
    op.create_index(
        "ix_roomrate_scrape_runs_market_canonical_dates",
        "roomrate_scrape_runs",
        ["account_id", "canonical_destination", "check_in", "check_out", "adults", "children", "rooms"],
    )

    _create_canonical_views()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")

    op.drop_index("ix_roomrate_scrape_runs_market_canonical_dates", table_name="roomrate_scrape_runs")
    op.drop_index("ix_roomrate_scrape_jobs_account_canonical_status", table_name="roomrate_scrape_jobs")
    op.drop_index(
        "ix_roomrate_owned_properties_account_canonical_destination",
        table_name="roomrate_owned_properties",
    )
    op.drop_column("roomrate_scrape_runs", "canonical_destination")
    op.drop_column("roomrate_scrape_runs", "raw_destination")
    op.drop_column("roomrate_scrape_jobs", "canonical_destination")
    op.drop_column("roomrate_scrape_jobs", "raw_destination")
    op.drop_column("roomrate_owned_properties", "canonical_destination")
    op.drop_column("roomrate_owned_properties", "raw_destination")

    _create_previous_views()
