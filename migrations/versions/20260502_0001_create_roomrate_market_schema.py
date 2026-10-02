"""Create RoomRate production market schema.

Revision ID: 20260502_0001
Revises:
Create Date: 2026-05-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260502_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roomrate_scrape_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_run_key", sa.String(length=255), nullable=False),
        sa.Column("source_run_id", sa.String(length=255), nullable=True),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("check_out", sa.Date(), nullable=False),
        sa.Column("nights", sa.Integer(), nullable=False),
        sa.Column("guests", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("check_out > check_in", name="ck_roomrate_scrape_runs_date_range"),
        sa.CheckConstraint("nights > 0", name="ck_roomrate_scrape_runs_nights_positive"),
        sa.CheckConstraint("guests > 0", name="ck_roomrate_scrape_runs_guests_positive"),
        sa.UniqueConstraint("provider", "source_run_key", name="uq_roomrate_scrape_runs_provider_source_key"),
    )
    op.create_index(
        "ix_roomrate_scrape_runs_market_dates",
        "roomrate_scrape_runs",
        ["destination", "check_in", "check_out", "guests"],
    )
    op.create_index("ix_roomrate_scrape_runs_status_started", "roomrate_scrape_runs", ["status", "started_at"])

    op.create_table(
        "roomrate_properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_property_key", sa.String(length=255), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("property_type", sa.String(length=100), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("stars", sa.Numeric(2, 1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_roomrate_properties_latitude"),
        sa.CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180", name="ck_roomrate_properties_longitude"),
        sa.UniqueConstraint("provider", "source_property_key", name="uq_roomrate_properties_provider_source_key"),
    )
    op.create_index("ix_roomrate_properties_market", "roomrate_properties", ["city", "canonical_name"])
    op.create_index("ix_roomrate_properties_coordinates", "roomrate_properties", ["latitude", "longitude"])

    op.create_table(
        "roomrate_rate_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scrape_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_score", sa.Numeric(3, 1), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("rooms_left_min", sa.Integer(), nullable=True),
        sa.Column("price_min_eur", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_max_eur", sa.Numeric(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("review_score IS NULL OR review_score BETWEEN 0 AND 10", name="ck_roomrate_rate_observations_review_score"),
        sa.CheckConstraint("review_count IS NULL OR review_count >= 0", name="ck_roomrate_rate_observations_review_count"),
        sa.CheckConstraint("rooms_left_min IS NULL OR rooms_left_min >= 0", name="ck_roomrate_rate_observations_rooms_left"),
        sa.CheckConstraint("price_min_eur IS NULL OR price_min_eur >= 0", name="ck_roomrate_rate_observations_price_min"),
        sa.CheckConstraint("price_max_eur IS NULL OR price_max_eur >= 0", name="ck_roomrate_rate_observations_price_max"),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["roomrate_scrape_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["property_id"], ["roomrate_properties.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("scrape_run_id", "property_id", name="uq_roomrate_rate_observations_run_property"),
    )
    op.create_index(
        "ix_roomrate_rate_observations_property_observed",
        "roomrate_rate_observations",
        ["property_id", "observed_at"],
    )
    op.create_index("ix_roomrate_rate_observations_review", "roomrate_rate_observations", ["review_score", "review_count"])

    op.create_table(
        "roomrate_room_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rate_observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("room_type", sa.String(length=255), nullable=False),
        sa.Column("meals", sa.Text(), nullable=True),
        sa.Column("free_cancellation", sa.String(length=100), nullable=True),
        sa.Column("price_per_night_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("price_total_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("rooms_left", sa.Integer(), nullable=True),
        sa.Column("package_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price_per_night_eur >= 0", name="ck_roomrate_room_packages_price_night"),
        sa.CheckConstraint("price_total_eur >= 0", name="ck_roomrate_room_packages_price_total"),
        sa.CheckConstraint("rooms_left IS NULL OR rooms_left >= 0", name="ck_roomrate_room_packages_rooms_left"),
        sa.ForeignKeyConstraint(["rate_observation_id"], ["roomrate_rate_observations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("rate_observation_id", "source_record_id", name="uq_roomrate_room_packages_observation_record"),
    )
    op.create_index("ix_roomrate_room_packages_price", "roomrate_room_packages", ["price_per_night_eur"])
    op.create_index("ix_roomrate_room_packages_room_type", "roomrate_room_packages", ["room_type"])

    op.create_table(
        "roomrate_amenities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("normalized_name", name="uq_roomrate_amenities_normalized_name"),
    )

    op.create_table(
        "roomrate_property_amenities",
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amenity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["property_id"], ["roomrate_properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["amenity_id"], ["roomrate_amenities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("property_id", "amenity_id"),
        sa.UniqueConstraint("property_id", "amenity_id", name="uq_roomrate_property_amenities_property_amenity"),
    )

    op.create_table(
        "roomrate_raw_ingestion_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scrape_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_run_id", sa.String(length=255), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["roomrate_scrape_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("payload_hash", name="uq_roomrate_raw_ingestion_events_payload_hash"),
    )
    op.create_index("ix_roomrate_raw_ingestion_events_source_run", "roomrate_raw_ingestion_events", ["source", "source_run_id"])

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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS roomrate_competitor_markers")
    op.execute("DROP VIEW IF EXISTS roomrate_latest_room_rates")
    op.drop_index("ix_roomrate_raw_ingestion_events_source_run", table_name="roomrate_raw_ingestion_events")
    op.drop_table("roomrate_raw_ingestion_events")
    op.drop_table("roomrate_property_amenities")
    op.drop_table("roomrate_amenities")
    op.drop_index("ix_roomrate_room_packages_room_type", table_name="roomrate_room_packages")
    op.drop_index("ix_roomrate_room_packages_price", table_name="roomrate_room_packages")
    op.drop_table("roomrate_room_packages")
    op.drop_index("ix_roomrate_rate_observations_review", table_name="roomrate_rate_observations")
    op.drop_index("ix_roomrate_rate_observations_property_observed", table_name="roomrate_rate_observations")
    op.drop_table("roomrate_rate_observations")
    op.drop_index("ix_roomrate_properties_coordinates", table_name="roomrate_properties")
    op.drop_index("ix_roomrate_properties_market", table_name="roomrate_properties")
    op.drop_table("roomrate_properties")
    op.drop_index("ix_roomrate_scrape_runs_status_started", table_name="roomrate_scrape_runs")
    op.drop_index("ix_roomrate_scrape_runs_market_dates", table_name="roomrate_scrape_runs")
    op.drop_table("roomrate_scrape_runs")
