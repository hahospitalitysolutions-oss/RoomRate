"""Add RoomRate onboarding and competitor workflow tables.

Revision ID: 20260517_0005
Revises: 20260517_0004
Create Date: 2026-05-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260517_0005"
down_revision: str | None = "20260517_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_workflow_views() -> None:
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
            WHERE p.latitude IS NOT NULL
              AND p.longitude IS NOT NULL
        ) ranked
        WHERE rn = 1;
        """
    )


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")

    op.add_column("roomrate_scrape_jobs", sa.Column("job_type", sa.String(length=50), nullable=False, server_default="competitor_search"))
    op.add_column("roomrate_scrape_jobs", sa.Column("room_type_category", sa.String(length=50), nullable=True))
    op.add_column("roomrate_scrape_jobs", sa.Column("filters_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.alter_column("roomrate_scrape_jobs", "job_type", server_default=None)
    op.create_index("ix_roomrate_scrape_jobs_type_status", "roomrate_scrape_jobs", ["job_type", "status"])

    op.add_column("roomrate_room_packages", sa.Column("room_type_category", sa.String(length=50), nullable=False, server_default="other"))
    op.alter_column("roomrate_room_packages", "room_type_category", server_default=None)
    op.create_index("ix_roomrate_room_packages_room_type_category", "roomrate_room_packages", ["room_type_category"])

    op.create_table(
        "roomrate_owned_property_room_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owned_property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_seen_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("room_type", sa.String(length=255), nullable=False),
        sa.Column("room_type_category", sa.String(length=50), nullable=False),
        sa.Column("sample_price_per_night_eur", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owned_property_id"], ["roomrate_owned_properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["first_seen_job_id"], ["roomrate_scrape_jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "account_id",
            "owned_property_id",
            "room_type",
            "room_type_category",
            name="uq_roomrate_owned_property_room_types_label",
        ),
    )
    op.create_index(
        "ix_roomrate_owned_property_room_types_property_category",
        "roomrate_owned_property_room_types",
        ["owned_property_id", "room_type_category"],
    )

    op.create_table(
        "roomrate_tracked_competitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owned_property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("competitor_property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("competitor_room_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("room_type_category", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owned_property_id"], ["roomrate_owned_properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["competitor_property_id"], ["roomrate_properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["competitor_room_package_id"], ["roomrate_room_packages.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "account_id",
            "owned_property_id",
            "room_type_category",
            "competitor_property_id",
            name="uq_roomrate_tracked_competitors_scope",
        ),
    )
    op.create_index(
        "ix_roomrate_tracked_competitors_scope",
        "roomrate_tracked_competitors",
        ["account_id", "owned_property_id", "room_type_category", "is_active"],
    )

    _create_workflow_views()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")

    op.drop_index("ix_roomrate_tracked_competitors_scope", table_name="roomrate_tracked_competitors")
    op.drop_table("roomrate_tracked_competitors")
    op.drop_index(
        "ix_roomrate_owned_property_room_types_property_category",
        table_name="roomrate_owned_property_room_types",
    )
    op.drop_table("roomrate_owned_property_room_types")
    op.drop_index("ix_roomrate_room_packages_room_type_category", table_name="roomrate_room_packages")
    op.drop_column("roomrate_room_packages", "room_type_category")
    op.drop_index("ix_roomrate_scrape_jobs_type_status", table_name="roomrate_scrape_jobs")
    op.drop_column("roomrate_scrape_jobs", "filters_payload")
    op.drop_column("roomrate_scrape_jobs", "room_type_category")
    op.drop_column("roomrate_scrape_jobs", "job_type")

    _create_previous_views()
