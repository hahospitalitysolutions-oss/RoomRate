from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base

# NOTE: these ORM classes look unused — the API queries with raw SQLAlchemy
# Core text() and never imports them. They are NOT dead code: migrations/env.py
# imports Base as Alembic's `target_metadata`, so this module IS the schema
# definition autogenerate diffs the database against. Deleting a class here
# silently changes what future migrations think the schema should be.


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RoomRateAccount(TimestampMixin, Base):
    """Tenant account for one hotel operator or operating company."""

    __tablename__ = "roomrate_accounts"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_roomrate_accounts_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")

    memberships: Mapped[list[RoomRateMembership]] = relationship(back_populates="account")
    owned_properties: Mapped[list[RoomRateOwnedProperty]] = relationship(back_populates="account")
    scrape_runs: Mapped[list[RoomRateScrapeRun]] = relationship(back_populates="account")
    scrape_jobs: Mapped[list[RoomRateScrapeJob]] = relationship(back_populates="account")
    tracked_competitors: Mapped[list[RoomRateTrackedCompetitor]] = relationship(back_populates="account")


class RoomRateUserIdentity(TimestampMixin, Base):
    """External auth user mapped into RoomRate."""

    __tablename__ = "roomrate_user_identities"
    __table_args__ = (
        UniqueConstraint("auth_provider", "auth_subject", name="uq_roomrate_user_identities_provider_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list[RoomRateMembership]] = relationship(back_populates="user")


class RoomRateMembership(TimestampMixin, Base):
    """User membership inside one tenant account."""

    __tablename__ = "roomrate_memberships"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_roomrate_memberships_account_user"),
        Index("ix_roomrate_memberships_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_user_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="owner")

    account: Mapped[RoomRateAccount] = relationship(back_populates="memberships")
    user: Mapped[RoomRateUserIdentity] = relationship(back_populates="memberships")


class RoomRateOwnedProperty(TimestampMixin, Base):
    """A property owned or managed by a RoomRate account."""

    __tablename__ = "roomrate_owned_properties"
    __table_args__ = (
        Index("ix_roomrate_owned_properties_account_city", "account_id", "city"),
        Index(
            "ix_roomrate_owned_properties_account_canonical_destination",
            "account_id",
            "canonical_destination",
        ),
        Index("ix_roomrate_owned_properties_matched_property", "matched_property_id"),
        Index("ix_roomrate_owned_properties_selected_room_type", "account_id", "selected_room_type_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    matched_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_properties.id", ondelete="SET NULL"),
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    booking_url: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    selected_room_type_category: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(100))
    property_type: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    location_source: Mapped[str | None] = mapped_column(String(50))
    location_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped[RoomRateAccount] = relationship(back_populates="owned_properties")
    scrape_jobs: Mapped[list[RoomRateScrapeJob]] = relationship(back_populates="owned_property")
    discovered_room_types: Mapped[list[RoomRateOwnedPropertyRoomType]] = relationship(back_populates="owned_property")
    tracked_competitors: Mapped[list[RoomRateTrackedCompetitor]] = relationship(back_populates="owned_property")


class RoomRateOwnedPropertyRoomType(TimestampMixin, Base):
    """Room type discovered from the user's own property discovery scrape."""

    __tablename__ = "roomrate_owned_property_room_types"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "owned_property_id",
            "room_type",
            "room_type_category",
            name="uq_roomrate_owned_property_room_types_label",
        ),
        Index(
            "ix_roomrate_owned_property_room_types_property_category",
            "owned_property_id",
            "room_type_category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    owned_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    first_seen_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_jobs.id", ondelete="SET NULL"),
    )
    room_type: Mapped[str] = mapped_column(String(255), nullable=False)
    room_type_category: Mapped[str] = mapped_column(String(50), nullable=False)
    sample_meals: Mapped[str | None] = mapped_column(Text)
    sample_free_cancellation: Mapped[str | None] = mapped_column(String(100))
    sample_facilities: Mapped[str | None] = mapped_column(Text)
    sample_price_per_night_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped[RoomRateAccount] = relationship()
    owned_property: Mapped[RoomRateOwnedProperty] = relationship(back_populates="discovered_room_types")


class RoomRateScrapeJob(TimestampMixin, Base):
    """User-requested scrape configuration and execution state."""

    __tablename__ = "roomrate_scrape_jobs"
    __table_args__ = (
        # Serves the account job list (ORDER BY requested_at DESC) and the
        # daily-quota count; the active-jobs count is covered by the partial
        # unique index below (leading account_id, active-status predicate).
        Index("ix_roomrate_scrape_jobs_account_requested", "account_id", text("requested_at DESC")),
        Index("ix_roomrate_scrape_jobs_account_status", "account_id", "status"),
        Index("ix_roomrate_scrape_jobs_requested", "requested_at"),
        # Executor's global oldest-first queue scan without indexing the
        # ever-growing terminal rows.
        Index(
            "ix_roomrate_scrape_jobs_queued_requested",
            "requested_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_roomrate_scrape_jobs_queued_ready",
            "next_attempt_at",
            "requested_at",
            postgresql_where=text("status = 'queued'"),
        ),
        # Partial index backing the scheduler's rotation-fairness ORDER BY:
        # max(requested_at) per property over scheduled jobs only.
        Index(
            "ix_roomrate_scrape_jobs_property_scheduled_requested",
            "owned_property_id",
            text("requested_at DESC"),
            postgresql_where=text("scheduled"),
        ),
        # Partial unique index: at most one active job per account + market scope.
        Index(
            "uq_roomrate_scrape_jobs_active_market",
            "account_id",
            "canonical_destination",
            "check_in",
            "check_out",
            "adults",
            "children",
            "rooms",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_roomrate_scrape_jobs_attempt_count_non_negative",
        ),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 10",
            name="ck_roomrate_scrape_jobs_max_attempts_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    owned_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="SET NULL"),
    )
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    adults: Mapped[int] = mapped_column(nullable=False, default=2)
    children: Mapped[int] = mapped_column(nullable=False, default=0)
    rooms: Mapped[int] = mapped_column(nullable=False, default=1)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, default="competitor_search")
    room_type_category: Mapped[str | None] = mapped_column(String(50))
    filters_payload: Mapped[dict | None] = mapped_column(JSONB)
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
    # Round 6: what the map form sent, echoed back so «Επαναφορά τελευταίας
    # αναζήτησης» can refill the nearby-area chips and the radius. NULL for
    # jobs created before migration 20260915_0024.
    nearby_destinations: Mapped[list | None] = mapped_column(JSONB)
    radius_km: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    # True only for scheduler-enqueued jobs (rotation fairness + scout-cache
    # sharing). Set exclusively via ScrapeJobService.create_job(scheduled=True);
    # never client-settable.
    scheduled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        nullable=False, default=3, server_default=text("3")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Worker identity ("host:pid") and liveness for atomic claim-based execution.
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[RoomRateAccount] = relationship(back_populates="scrape_jobs")
    owned_property: Mapped[RoomRateOwnedProperty | None] = relationship(back_populates="scrape_jobs")
    scrape_runs: Mapped[list[RoomRateScrapeRun]] = relationship(back_populates="scrape_job")


class RoomRateScheduleConfig(TimestampMixin, Base):
    """Per-account recurring scrape schedule and circuit-breaker state."""

    __tablename__ = "roomrate_schedule_configs"
    __table_args__ = (
        # One schedule per tenant; also the ON CONFLICT upsert target.
        UniqueConstraint("account_id", name="uq_roomrate_schedule_configs_account"),
        CheckConstraint("frequency_hours > 0", name="ck_roomrate_schedule_configs_frequency_hours"),
        CheckConstraint("hour_utc BETWEEN 0 AND 23", name="ck_roomrate_schedule_configs_hour_utc"),
        CheckConstraint("hour_local BETWEEN 0 AND 23", name="ck_roomrate_schedule_configs_hour_local"),
        CheckConstraint("lead_days >= 0", name="ck_roomrate_schedule_configs_lead_days"),
        CheckConstraint("nights > 0", name="ck_roomrate_schedule_configs_nights"),
        CheckConstraint("adults >= 1", name="ck_roomrate_schedule_configs_adults"),
        CheckConstraint("children >= 0", name="ck_roomrate_schedule_configs_children"),
        CheckConstraint("rooms >= 1", name="ck_roomrate_schedule_configs_rooms"),
        CheckConstraint("consecutive_failures >= 0", name="ck_roomrate_schedule_configs_failures"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    frequency_hours: Mapped[int] = mapped_column(nullable=False, default=24)
    # The owner's hour on their own clock; the scheduler reads these two.
    hour_local: Mapped[int] = mapped_column(nullable=False, default=8, server_default="8")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Athens", server_default="Europe/Athens")
    # Deprecated: hour_local as a UTC hour on the day of the last save, kept
    # current for older API images (migration 20260930_0030).
    hour_utc: Mapped[int] = mapped_column(nullable=False, default=5)
    lead_days: Mapped[int] = mapped_column(nullable=False, default=30)
    nights: Mapped[int] = mapped_column(nullable=False, default=3)
    adults: Mapped[int] = mapped_column(nullable=False, default=2)
    children: Mapped[int] = mapped_column(nullable=False, default=0)
    rooms: Mapped[int] = mapped_column(nullable=False, default=1)
    # Circuit breaker: incremented on scheduled-job failure, reset on success;
    # the scheduler disables the schedule once the threshold is reached.
    consecutive_failures: Mapped[int] = mapped_column(nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[RoomRateAccount] = relationship()


class RoomRateScrapeRun(TimestampMixin, Base):
    """A single external market scrape request and its execution metadata."""

    __tablename__ = "roomrate_scrape_runs"
    __table_args__ = (
        UniqueConstraint("account_id", "provider", "source_run_key", name="uq_roomrate_scrape_runs_account_provider_source_key"),
        Index(
            "ix_roomrate_scrape_runs_market_dates",
            "account_id",
            "destination",
            "check_in",
            "check_out",
            "adults",
            "children",
            "rooms",
        ),
        Index("ix_roomrate_scrape_runs_scrape_job", "scrape_job_id"),
        Index("ix_roomrate_scrape_runs_status_started", "status", "started_at"),
        Index(
            "ix_roomrate_scrape_runs_market_canonical_dates",
            "account_id",
            "canonical_destination",
            "check_in",
            "check_out",
            "adults",
            "children",
            "rooms",
        ),
        Index(
            "ix_roomrate_scrape_runs_history",
            "account_id",
            "canonical_destination",
            "check_in",
            "check_out",
            "status",
            text("finished_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    scrape_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_jobs.id", ondelete="SET NULL"),
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="booking_com")
    source_run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(String(255))
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    # nights and guests are NOT stored (3NF): the views derive them as
    # (check_out - check_in) and (adults + children).
    adults: Mapped[int] = mapped_column(nullable=False, default=2)
    children: Mapped[int] = mapped_column(nullable=False, default=0)
    rooms: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_metadata: Mapped[dict | None] = mapped_column(JSONB)

    account: Mapped[RoomRateAccount] = relationship(back_populates="scrape_runs")
    scrape_job: Mapped[RoomRateScrapeJob | None] = relationship(back_populates="scrape_runs")
    observations: Mapped[list[RoomRateRateObservation]] = relationship(back_populates="scrape_run")
    raw_events: Mapped[list[RoomRateRawIngestionEvent]] = relationship(back_populates="scrape_run")


class RoomRateProperty(TimestampMixin, Base):
    """Canonical competitor property used by maps, pricing and amenities analytics."""

    __tablename__ = "roomrate_properties"
    __table_args__ = (
        UniqueConstraint("provider", "source_property_key", name="uq_roomrate_properties_provider_source_key"),
        Index("ix_roomrate_properties_market", "city", "canonical_name"),
        Index("ix_roomrate_properties_coordinates", "latitude", "longitude"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="booking_com")
    source_property_key: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    stars: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))
    # Round 6: the Booking listing URL (scraper CSV hotel_url), refreshed on
    # every upsert so the map popup can link «Άνοιγμα στο Booking».
    booking_url: Mapped[str | None] = mapped_column(Text)
    # Pipe-joined, sorted amenity display names refreshed at write time.
    amenities_cached: Mapped[str | None] = mapped_column(Text)

    observations: Mapped[list[RoomRateRateObservation]] = relationship(back_populates="property")
    amenities: Mapped[list[RoomRatePropertyAmenity]] = relationship(back_populates="property")
    tracked_by_accounts: Mapped[list[RoomRateTrackedCompetitor]] = relationship(back_populates="competitor_property")


class RoomRateRateObservation(TimestampMixin, Base):
    """Property-level market state observed inside one scrape run."""

    __tablename__ = "roomrate_rate_observations"
    __table_args__ = (
        UniqueConstraint("scrape_run_id", "property_id", name="uq_roomrate_rate_observations_run_property"),
        Index("ix_roomrate_rate_observations_property_observed", "property_id", "observed_at"),
        Index("ix_roomrate_rate_observations_review", "review_score", "review_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scrape_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    review_count: Mapped[int | None] = mapped_column()
    rooms_left_min: Mapped[int | None] = mapped_column()
    price_min_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_max_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    scrape_run: Mapped[RoomRateScrapeRun] = relationship(back_populates="observations")
    property: Mapped[RoomRateProperty] = relationship(back_populates="observations")
    packages: Mapped[list[RoomRateRoomPackage]] = relationship(back_populates="observation")


class RoomRateRoomPackage(TimestampMixin, Base):
    """Room/package offer under a property observation."""

    __tablename__ = "roomrate_room_packages"
    __table_args__ = (
        UniqueConstraint("rate_observation_id", "source_record_id", name="uq_roomrate_room_packages_observation_record"),
        Index("ix_roomrate_room_packages_category_price", "room_type_category", "price_per_night_eur"),
        Index("ix_roomrate_room_packages_price", "price_per_night_eur"),
        Index("ix_roomrate_room_packages_room_type", "room_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rate_observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_rate_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    room_type: Mapped[str] = mapped_column(String(255), nullable=False)
    room_type_category: Mapped[str] = mapped_column(String(50), nullable=False, default="other")
    meals: Mapped[str | None] = mapped_column(Text)
    free_cancellation: Mapped[str | None] = mapped_column(String(100))
    price_per_night_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    price_total_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rooms_left: Mapped[int | None] = mapped_column()
    package_payload: Mapped[dict | None] = mapped_column(JSONB)
    # Extracted room facts (capacity/view/balcony/size/bed) for match scoring.
    room_attributes: Mapped[dict | None] = mapped_column(JSONB)
    # Rate plan (spec 2026-09-29 §3, migration 20260929_0025). All nullable,
    # no backfill: pre-existing rows read as «χωρίς στοιχεία πλάνου». The
    # discounted price defaults to the main price at write time, so the main
    # price_per_night_eur keeps its historical meaning for markers/history.
    discounted_price_per_night_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    discount_label: Mapped[str | None] = mapped_column(String(40))
    has_genius_discount: Mapped[bool | None] = mapped_column(Boolean)
    cancellation_type: Mapped[str | None] = mapped_column(String(40))
    payment_label: Mapped[str | None] = mapped_column(String(60))
    rate_block_id: Mapped[str | None] = mapped_column(String(80))

    observation: Mapped[RoomRateRateObservation] = relationship(back_populates="packages")


class RoomRateTrackedCompetitor(TimestampMixin, Base):
    """Competitor selected by a user for one owned property and room category."""

    __tablename__ = "roomrate_tracked_competitors"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "owned_property_id",
            "room_type_category",
            "competitor_property_id",
            name="uq_roomrate_tracked_competitors_scope",
        ),
        Index(
            "ix_roomrate_tracked_competitors_scope",
            "account_id",
            "owned_property_id",
            "room_type_category",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    owned_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    competitor_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    competitor_room_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_room_packages.id", ondelete="SET NULL"),
    )
    room_type_category: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped[RoomRateAccount] = relationship(back_populates="tracked_competitors")
    owned_property: Mapped[RoomRateOwnedProperty] = relationship(back_populates="tracked_competitors")
    competitor_property: Mapped[RoomRateProperty] = relationship(back_populates="tracked_by_accounts")
    competitor_room_package: Mapped[RoomRateRoomPackage | None] = relationship()


class RoomRateAmenity(TimestampMixin, Base):
    """Normalized amenity dimension."""

    __tablename__ = "roomrate_amenities"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_roomrate_amenities_normalized_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)

    properties: Mapped[list[RoomRatePropertyAmenity]] = relationship(back_populates="amenity")


class RoomRatePropertyAmenity(TimestampMixin, Base):
    """Many-to-many bridge between properties and normalized amenities."""

    __tablename__ = "roomrate_property_amenities"
    # Migration 0001 declared both a primary key and a UNIQUE on these two
    # columns. PostgreSQL keeps only one of two identical constraints: the
    # table's primary key, under the UNIQUE's name. The model says the same,
    # so autogenerate does not try to add the UNIQUE again.
    __table_args__ = (
        PrimaryKeyConstraint("property_id", "amenity_id", name="uq_roomrate_property_amenities_property_amenity"),
    )

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_properties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    amenity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_amenities.id", ondelete="CASCADE"),
        primary_key=True,
    )

    property: Mapped[RoomRateProperty] = relationship(back_populates="amenities")
    amenity: Mapped[RoomRateAmenity] = relationship(back_populates="properties")


class RoomRateRawIngestionEvent(TimestampMixin, Base):
    """Immutable raw payload record used for scraper debugging and replay."""

    __tablename__ = "roomrate_raw_ingestion_events"
    __table_args__ = (
        UniqueConstraint("payload_hash", name="uq_roomrate_raw_ingestion_events_payload_hash"),
        Index("ix_roomrate_raw_ingestion_events_source_run", "source", "source_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scrape_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_runs.id", ondelete="SET NULL"),
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="booking_com")
    source_run_id: Mapped[str | None] = mapped_column(String(255))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    scrape_run: Mapped[RoomRateScrapeRun | None] = relationship(back_populates="raw_events")


class RoomRateAlertRule(TimestampMixin, Base):
    """Per-account price-change alert rule, optionally scoped to one property."""

    __tablename__ = "roomrate_alert_rules"
    __table_args__ = (
        CheckConstraint("threshold_pct > 0", name="ck_roomrate_alert_rules_threshold_pct"),
        CheckConstraint(
            "direction IN ('any', 'drop', 'rise')",
            name="ck_roomrate_alert_rules_direction",
        ),
        Index("ix_roomrate_alert_rules_account_active", "account_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL = the rule applies to every owned property in the account.
    owned_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
    )
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False, default="price_change")
    threshold_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("10.0"))
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="any")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped[RoomRateAccount] = relationship()
    owned_property: Mapped[RoomRateOwnedProperty | None] = relationship()


class RoomRateNotification(Base):
    """In-app notification feed row (price alerts, schedule-disabled, ...).

    Deliberately does NOT use TimestampMixin: a notification is immutable except
    for the ``is_read`` flip, so only ``created_at`` is tracked (matching the
    migration, which omits ``updated_at`` for this table).
    """

    __tablename__ = "roomrate_notifications"
    __table_args__ = (
        # Main feed: newest-first regardless of read state.
        Index(
            "ix_roomrate_notifications_account_created",
            "account_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        # Unread feed + unread count.
        Index(
            "ix_roomrate_notifications_account_unread_created",
            "account_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("is_read = false"),
        ),
        # Idempotent alert inserts: a duplicate of an already-raised event
        # (same account + deterministic event_key) is dropped by ON CONFLICT
        # DO NOTHING in AlertsRepository.insert_notifications. NULL keys
        # (e.g. schedule_disabled) are exempt.
        Index(
            "uq_roomrate_notifications_account_event_key",
            "account_id",
            "event_key",
            unique=True,
            postgresql_where=text("event_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL so a notification survives the deletion of its rule.
    alert_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_alert_rules.id", ondelete="SET NULL"),
    )
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    # Deterministic identity of the underlying event (NULL = not deduplicated).
    event_key: Mapped[str | None] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped[RoomRateAccount] = relationship()
    alert_rule: Mapped[RoomRateAlertRule | None] = relationship()


class RoomRateWsTicket(Base):
    """One-time short-lived WebSocket auth ticket (only its SHA-256 is stored).

    Minted over authenticated REST and redeemed exactly once by the /ws/alerts
    handshake, so browser JWTs never appear in WebSocket URLs. No
    TimestampMixin: the row is immutable except for the one-shot ``used_at``
    flip, and rows self-clean via the opportunistic DELETE in issue().
    """

    __tablename__ = "roomrate_ws_tickets"
    __table_args__ = (
        # Backs the opportunistic DELETE of long-expired rows in issue().
        Index("ix_roomrate_ws_tickets_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped[RoomRateAccount] = relationship()


class RoomRateRoomMatch(Base):
    """One AI room-match estimate for a competitor room in one scrape job.

    Spec 2026-09-29 Α.3 (migration 20260930_0026). Written only by the
    matching agent, always as a full replacement for one
    ``(scrape_job_id, owned_room_type_id)`` scope, so the unique constraint
    is the replace-upsert identity. No TimestampMixin: rows are immutable
    (a re-run deletes and re-inserts), so only ``created_at`` is tracked.
    """

    __tablename__ = "roomrate_room_matches"
    __table_args__ = (
        UniqueConstraint(
            "scrape_job_id",
            "owned_room_type_id",
            "property_id",
            "room_type",
            name="uq_roomrate_room_matches_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    scrape_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    owned_room_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_property_room_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    room_type: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)
    category_match: Mapped[str] = mapped_column(String(10), nullable=False)
    # The agent's own «real substitute for the reference room» verdict; NULL on
    # rows written before migration 0027 — readers fall back to score >= 50.
    comparable: Mapped[bool | None] = mapped_column(Boolean)
    # Greek, ≤160 chars by the agent contract; TEXT so a longer answer can
    # never fail the write (the service trims before storing anyway).
    reasoning: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped[RoomRateAccount] = relationship()
    scrape_job: Mapped[RoomRateScrapeJob] = relationship()
    owned_room_type: Mapped[RoomRateOwnedPropertyRoomType] = relationship()
    property: Mapped[RoomRateProperty] = relationship()


class RoomRateAgentRun(Base):
    """One agent execution (ok|error|skipped) — why an AI estimate did or did not appear.

    Spec 2026-09-29 Α.3: a deliberately light audit row per run, also the
    basis of the daily per-account run quota. ``scrape_job_id`` is SET NULL
    (not CASCADE) so the audit history survives job deletion. Immutable, so
    only ``created_at`` is tracked.
    """

    __tablename__ = "roomrate_agent_runs"
    __table_args__ = (
        # Daily quota count + newest-first troubleshooting reads.
        Index("ix_roomrate_agent_runs_account_created", "account_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    scrape_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_jobs.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="room_matching")
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped[RoomRateAccount] = relationship()
    scrape_job: Mapped[RoomRateScrapeJob | None] = relationship()


class RoomRateAgentLease(Base):
    """The run scoring one (job, owned room) scope right now (migration 20260930_0028).

    At most one row per scope (the primary key): a second caller waits for
    the holder or skips instead of paying for a duplicate run. ``holder`` is
    the claiming run's token, so only it releases the lease; a lease older
    than the longest possible run is stale and the next claim takes it over.
    """

    __tablename__ = "roomrate_agent_leases"

    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    scrape_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_scrape_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    owned_room_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_property_room_types.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    holder: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RoomRateRateLimitHit(Base):
    """One allowed hit on a rate-limited POST (migration 20260930_0029).

    Shared by every API process, so RATE_LIMIT_PER_MINUTE holds per caller
    across the whole deployment. ``key_hash`` is a SHA-256 of the caller key
    (never a raw IP, token or account id); rows older than the window are
    pruned by the checks themselves.
    """

    __tablename__ = "roomrate_rate_limit_hits"
    __table_args__ = (
        Index("ix_roomrate_rate_limit_hits_key_hit", "key_hash", "hit_at"),
        Index("ix_roomrate_rate_limit_hits_hit_at", "hit_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RoomRatePriceRecommendationAudit(Base):
    """Immutable, account-scoped audit and cache record for one pricing decision."""

    __tablename__ = "roomrate_price_recommendation_audits"
    __table_args__ = (
        Index("ix_roomrate_price_audits_account_created", "account_id", "created_at"),
        Index(
            "ix_roomrate_price_audits_cache",
            "account_id",
            "request_hash",
            "cache_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    owned_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str | None] = mapped_column(String(20))
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(30), nullable=False)
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    cache_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
