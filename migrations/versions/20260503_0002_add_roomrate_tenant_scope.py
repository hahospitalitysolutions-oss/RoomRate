"""Add RoomRate tenant scope.

Revision ID: 20260503_0002
Revises: 20260502_0001
Create Date: 2026-05-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260503_0002"
down_revision: str | None = "20260502_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"


def _create_account_scoped_views() -> None:
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


def _create_legacy_views() -> None:
    op.execute(
        """
        CREATE VIEW roomrate_latest_room_rates AS
        SELECT
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
                    PARTITION BY p.id, sr.destination, sr.check_in, sr.check_out, sr.guests
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
    op.create_table(
        "roomrate_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_roomrate_accounts_slug"),
    )
    op.execute(
        f"""
        INSERT INTO roomrate_accounts (id, slug, display_name, status)
        VALUES ('{DEFAULT_ACCOUNT_ID}'::uuid, 'default-demo-account', 'RoomRate Default Account', 'active')
        ON CONFLICT (slug) DO NOTHING
        """
    )

    op.create_table(
        "roomrate_user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("auth_provider", sa.String(length=50), nullable=False),
        sa.Column("auth_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("auth_provider", "auth_subject", name="uq_roomrate_user_identities_provider_subject"),
    )
    op.create_table(
        "roomrate_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["roomrate_user_identities.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("account_id", "user_id", name="uq_roomrate_memberships_account_user"),
    )
    op.create_index("ix_roomrate_memberships_user", "roomrate_memberships", ["user_id"])

    op.create_table(
        "roomrate_owned_properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("booking_url", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("property_type", sa.String(length=100), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("location_source", sa.String(length=50), nullable=True),
        sa.Column("location_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_roomrate_owned_properties_latitude"),
        sa.CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180", name="ck_roomrate_owned_properties_longitude"),
        sa.CheckConstraint("location_confidence IS NULL OR location_confidence BETWEEN 0 AND 1", name="ck_roomrate_owned_properties_location_confidence"),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_roomrate_owned_properties_account_city",
        "roomrate_owned_properties",
        ["account_id", "city"],
    )

    op.create_table(
        "roomrate_scrape_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owned_property_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("check_out", sa.Date(), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("rooms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("check_out > check_in", name="ck_roomrate_scrape_jobs_date_range"),
        sa.CheckConstraint("adults > 0", name="ck_roomrate_scrape_jobs_adults_positive"),
        sa.CheckConstraint("children >= 0", name="ck_roomrate_scrape_jobs_children_non_negative"),
        sa.CheckConstraint("rooms > 0", name="ck_roomrate_scrape_jobs_rooms_positive"),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owned_property_id"], ["roomrate_owned_properties.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_roomrate_scrape_jobs_account_status", "roomrate_scrape_jobs", ["account_id", "status"])
    op.create_index("ix_roomrate_scrape_jobs_requested", "roomrate_scrape_jobs", ["requested_at"])

    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")
    op.add_column(
        "roomrate_scrape_runs",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_ACCOUNT_ID}'::uuid"),
        ),
    )
    op.alter_column("roomrate_scrape_runs", "account_id", server_default=None)
    op.create_foreign_key(
        "fk_roomrate_scrape_runs_account_id",
        "roomrate_scrape_runs",
        "roomrate_accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_roomrate_scrape_runs_provider_source_key", "roomrate_scrape_runs", type_="unique")
    op.create_unique_constraint(
        "uq_roomrate_scrape_runs_account_provider_source_key",
        "roomrate_scrape_runs",
        ["account_id", "provider", "source_run_key"],
    )
    op.drop_index("ix_roomrate_scrape_runs_market_dates", table_name="roomrate_scrape_runs")
    op.create_index(
        "ix_roomrate_scrape_runs_market_dates",
        "roomrate_scrape_runs",
        ["account_id", "destination", "check_in", "check_out", "guests"],
    )
    _create_account_scoped_views()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")
    op.drop_index("ix_roomrate_scrape_runs_market_dates", table_name="roomrate_scrape_runs")
    op.create_index(
        "ix_roomrate_scrape_runs_market_dates",
        "roomrate_scrape_runs",
        ["destination", "check_in", "check_out", "guests"],
    )
    op.drop_constraint("uq_roomrate_scrape_runs_account_provider_source_key", "roomrate_scrape_runs", type_="unique")
    op.create_unique_constraint(
        "uq_roomrate_scrape_runs_provider_source_key",
        "roomrate_scrape_runs",
        ["provider", "source_run_key"],
    )
    op.drop_constraint("fk_roomrate_scrape_runs_account_id", "roomrate_scrape_runs", type_="foreignkey")
    op.drop_column("roomrate_scrape_runs", "account_id")
    _create_legacy_views()

    op.drop_index("ix_roomrate_scrape_jobs_requested", table_name="roomrate_scrape_jobs")
    op.drop_index("ix_roomrate_scrape_jobs_account_status", table_name="roomrate_scrape_jobs")
    op.drop_table("roomrate_scrape_jobs")
    op.drop_index("ix_roomrate_owned_properties_account_city", table_name="roomrate_owned_properties")
    op.drop_table("roomrate_owned_properties")
    op.drop_index("ix_roomrate_memberships_user", table_name="roomrate_memberships")
    op.drop_table("roomrate_memberships")
    op.drop_table("roomrate_user_identities")
    op.drop_table("roomrate_accounts")
