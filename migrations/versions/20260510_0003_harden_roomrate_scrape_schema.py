"""Harden RoomRate scrape schema.

Revision ID: 20260510_0003
Revises: 20260503_0002
Create Date: 2026-05-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260510_0003"
down_revision: str | None = "20260503_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_hardened_views() -> None:
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
                p.display_name AS hotel_name,
                p.property_type,
                p.latitude,
                p.longitude,
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


def _create_previous_views() -> None:
    op.execute(
        """
        CREATE VIEW roomrate_latest_room_rates AS
        SELECT
            sr.account_id,
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
            NULL::integer AS adults,
            NULL::integer AS children,
            NULL::integer AS rooms,
            rp.room_type,
            rp.meals,
            rp.free_cancellation,
            rp.price_total_eur,
            COALESCE(amenities.facilities, '') AS facilities,
            rp.rooms_left
        FROM roomrate_room_packages rp
        JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
        JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
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
                p.display_name AS hotel_name,
                p.property_type,
                p.latitude,
                p.longitude,
                rp.price_per_night_eur,
                ro.review_score,
                ro.review_count,
                rp.rooms_left,
                sr.destination,
                sr.check_in,
                sr.check_out,
                row_number() OVER (
                    PARTITION BY sr.account_id, p.id, sr.destination, sr.check_in, sr.check_out, sr.guests
                    ORDER BY ro.observed_at DESC, rp.price_per_night_eur ASC
                ) AS rn
            FROM roomrate_room_packages rp
            JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
            JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
            JOIN roomrate_properties p ON p.id = ro.property_id
            WHERE p.latitude IS NOT NULL
              AND p.longitude IS NOT NULL
        ) ranked
        WHERE rn = 1;
        """
    )


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")

    op.add_column(
        "roomrate_owned_properties",
        sa.Column("matched_property_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_roomrate_owned_properties_matched_property_id",
        "roomrate_owned_properties",
        "roomrate_properties",
        ["matched_property_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_roomrate_owned_properties_matched_property",
        "roomrate_owned_properties",
        ["matched_property_id"],
    )

    op.add_column("roomrate_scrape_runs", sa.Column("scrape_job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("roomrate_scrape_runs", sa.Column("adults", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("roomrate_scrape_runs", sa.Column("children", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("roomrate_scrape_runs", sa.Column("rooms", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("roomrate_scrape_runs", "adults", server_default=None)
    op.alter_column("roomrate_scrape_runs", "children", server_default=None)
    op.alter_column("roomrate_scrape_runs", "rooms", server_default=None)
    op.create_foreign_key(
        "fk_roomrate_scrape_runs_scrape_job_id",
        "roomrate_scrape_runs",
        "roomrate_scrape_jobs",
        ["scrape_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_roomrate_scrape_runs_scrape_job", "roomrate_scrape_runs", ["scrape_job_id"])
    op.create_check_constraint("ck_roomrate_scrape_runs_adults_positive", "roomrate_scrape_runs", "adults > 0")
    op.create_check_constraint("ck_roomrate_scrape_runs_children_non_negative", "roomrate_scrape_runs", "children >= 0")
    op.create_check_constraint("ck_roomrate_scrape_runs_rooms_positive", "roomrate_scrape_runs", "rooms > 0")
    op.drop_index("ix_roomrate_scrape_runs_market_dates", table_name="roomrate_scrape_runs")
    op.create_index(
        "ix_roomrate_scrape_runs_market_dates",
        "roomrate_scrape_runs",
        ["account_id", "destination", "check_in", "check_out", "adults", "children", "rooms"],
    )

    _create_hardened_views()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")

    op.drop_index("ix_roomrate_scrape_runs_market_dates", table_name="roomrate_scrape_runs")
    op.create_index(
        "ix_roomrate_scrape_runs_market_dates",
        "roomrate_scrape_runs",
        ["account_id", "destination", "check_in", "check_out", "guests"],
    )
    op.drop_constraint("ck_roomrate_scrape_runs_rooms_positive", "roomrate_scrape_runs", type_="check")
    op.drop_constraint("ck_roomrate_scrape_runs_children_non_negative", "roomrate_scrape_runs", type_="check")
    op.drop_constraint("ck_roomrate_scrape_runs_adults_positive", "roomrate_scrape_runs", type_="check")
    op.drop_index("ix_roomrate_scrape_runs_scrape_job", table_name="roomrate_scrape_runs")
    op.drop_constraint("fk_roomrate_scrape_runs_scrape_job_id", "roomrate_scrape_runs", type_="foreignkey")
    op.drop_column("roomrate_scrape_runs", "rooms")
    op.drop_column("roomrate_scrape_runs", "children")
    op.drop_column("roomrate_scrape_runs", "adults")
    op.drop_column("roomrate_scrape_runs", "scrape_job_id")

    op.drop_index("ix_roomrate_owned_properties_matched_property", table_name="roomrate_owned_properties")
    op.drop_constraint(
        "fk_roomrate_owned_properties_matched_property_id",
        "roomrate_owned_properties",
        type_="foreignkey",
    )
    op.drop_column("roomrate_owned_properties", "matched_property_id")

    _create_previous_views()
