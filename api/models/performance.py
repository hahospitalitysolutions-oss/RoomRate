"""«Απόδοση»: the owner's own bookings and what the performance KPIs need.

Migration 20261020_0035; contract docs/performance/API.md. Like market.py,
these classes are the schema Alembic autogenerate diffs against (the API
reads and writes with Core text()). Every table hangs off an owned property
and is deleted with it; guests' personal data is never stored.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base
from api.models.market import TimestampMixin

ROOM_TYPES_ID = "roomrate_owned_property_room_types.id"


def _account_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("roomrate_accounts.id", ondelete="CASCADE"), nullable=False)


def _property_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
        nullable=False,
    )


class RoomRatePerformanceRevenueSettings(TimestampMixin, Base):
    """VAT and resilience fee behind the net amounts; a missing row is the defaults (13%, no fee)."""

    __tablename__ = "roomrate_performance_revenue_settings"
    __table_args__ = (
        CheckConstraint("vat_pct BETWEEN 0 AND 100", name="ck_roomrate_performance_revenue_settings_vat"),
        CheckConstraint("resilience_fee_eur >= 0", name="ck_roomrate_performance_revenue_settings_fee"),
        Index("ix_roomrate_performance_revenue_settings_account_id", "account_id"),
    )

    owned_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[uuid.UUID] = _account_fk()
    vat_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default="13")
    # Per room and night, €.
    resilience_fee_eur: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, server_default="0")
    fee_included_in_price: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class RoomRatePerformanceRoomInventory(TimestampMixin, Base):
    """Rooms of an owned room type, for every date or for ``valid_from``–``valid_to``."""

    __tablename__ = "roomrate_performance_room_inventory"
    __table_args__ = (
        CheckConstraint("rooms >= 0", name="ck_roomrate_performance_room_inventory_rooms"),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
            name="ck_roomrate_performance_room_inventory_range",
        ),
        Index("ix_roomrate_performance_room_inventory_property", "owned_property_id", "room_type_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = _account_fk()
    owned_property_id: Mapped[uuid.UUID] = _property_fk()
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(ROOM_TYPES_ID, ondelete="CASCADE"),
        nullable=False,
    )
    rooms: Mapped[int] = mapped_column(nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class RoomRatePerformanceClosure(TimestampMixin, Base):
    """Closed dates: the whole property (no room type), a room type, or ``rooms`` of it."""

    __tablename__ = "roomrate_performance_closures"
    __table_args__ = (
        CheckConstraint("date_to >= date_from", name="ck_roomrate_performance_closures_range"),
        CheckConstraint("rooms IS NULL OR rooms > 0", name="ck_roomrate_performance_closures_rooms"),
        Index("ix_roomrate_performance_closures_property_dates", "owned_property_id", "date_from"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = _account_fk()
    owned_property_id: Mapped[uuid.UUID] = _property_fk()
    room_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(ROOM_TYPES_ID, ondelete="CASCADE"),
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    rooms: Mapped[int | None] = mapped_column()
    reason: Mapped[str | None] = mapped_column(String(255))


class RoomRatePerformanceLabelMapping(TimestampMixin, Base):
    """A room label of the owner's files → an owned room type, or ignored; reused on every upload."""

    __tablename__ = "roomrate_performance_label_mappings"
    __table_args__ = (
        CheckConstraint("(room_type_id IS NULL) = ignored", name="ck_roomrate_performance_label_mappings_target"),
        Index("ix_roomrate_performance_label_mappings_account_id", "account_id"),
    )

    owned_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_id: Mapped[uuid.UUID] = _account_fk()
    room_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(ROOM_TYPES_ID, ondelete="CASCADE"),
    )
    ignored: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class RoomRatePerformanceImport(TimestampMixin, Base):
    """An uploaded bookings file: a preview until ``committed_at``, gone after ``expires_at`` if never committed."""

    __tablename__ = "roomrate_performance_imports"
    __table_args__ = (
        CheckConstraint("format IN ('template', 'booking')", name="ck_roomrate_performance_imports_format"),
        Index("ix_roomrate_performance_imports_property_created", "owned_property_id", "created_at"),
        Index("ix_roomrate_performance_imports_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = _account_fk()
    owned_property_id: Mapped[uuid.UUID] = _property_fk()
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_total: Mapped[int] = mapped_column(nullable=False, server_default="0")
    # [{"row": <Excel row>, "booking_ref": ..., "message": <Greek>}]
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # A template's «Δωμάτια» and «Κλειστές περίοδοι» until commit (migration 20261021_0036).
    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bookings_imported: Mapped[int | None] = mapped_column()
    bookings_updated: Mapped[int | None] = mapped_column()
    rows_skipped: Mapped[int | None] = mapped_column()


class RoomRatePerformanceImportRow(Base):
    """One checked row of an uploaded file (``data``: the parsed booking), deleted on commit."""

    __tablename__ = "roomrate_performance_import_rows"

    import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_performance_imports.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # The row number the owner sees in Excel.
    row_number: Mapped[int] = mapped_column(primary_key=True)
    booking_ref: Mapped[str | None] = mapped_column(String(100))
    room_label: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class RoomRatePerformanceBooking(TimestampMixin, Base):
    """One booking of an owned property; ``gross_eur`` is the whole stay, every room, as the guest paid it."""

    __tablename__ = "roomrate_performance_bookings"
    __table_args__ = (
        UniqueConstraint("owned_property_id", "booking_ref", name="uq_roomrate_performance_bookings_ref"),
        CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'no_show')",
            name="ck_roomrate_performance_bookings_status",
        ),
        CheckConstraint("departure > arrival", name="ck_roomrate_performance_bookings_stay"),
        CheckConstraint("rooms >= 1", name="ck_roomrate_performance_bookings_rooms"),
        CheckConstraint("gross_eur >= 0 AND commission_eur >= 0", name="ck_roomrate_performance_bookings_amounts"),
        Index("ix_roomrate_performance_bookings_property_arrival", "owned_property_id", "arrival"),
        Index("ix_roomrate_performance_bookings_property_booked_on", "owned_property_id", "booked_on"),
        Index("ix_roomrate_performance_bookings_account_id", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = _account_fk()
    owned_property_id: Mapped[uuid.UUID] = _property_fk()
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_performance_imports.id", ondelete="SET NULL"),
    )
    room_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(ROOM_TYPES_ID, ondelete="SET NULL"),
    )
    booking_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    room_label: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    booked_on: Mapped[date | None] = mapped_column(Date)
    cancelled_on: Mapped[date | None] = mapped_column(Date)
    arrival: Mapped[date] = mapped_column(Date, nullable=False)
    departure: Mapped[date] = mapped_column(Date, nullable=False)
    rooms: Mapped[int] = mapped_column(nullable=False, server_default="1")
    adults: Mapped[int | None] = mapped_column()
    children: Mapped[int | None] = mapped_column()
    gross_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    commission_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    # ISO 3166-1 alpha-2 of the booker; the API answers Greek names.
    country_code: Mapped[str | None] = mapped_column(String(2))


class RoomRatePerformanceBookingNight(Base):
    """One night of a booking: its share of the revenue and commission; ``sold`` = confirmed."""

    __tablename__ = "roomrate_performance_booking_nights"
    __table_args__ = (
        Index("ix_roomrate_performance_booking_nights_property_date", "owned_property_id", "stay_date"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roomrate_performance_bookings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stay_date: Mapped[date] = mapped_column(Date, primary_key=True)
    owned_property_id: Mapped[uuid.UUID] = _property_fk()
    room_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(ROOM_TYPES_ID, ondelete="SET NULL"),
    )
    rooms: Mapped[int] = mapped_column(nullable=False)
    gross_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    commission_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sold: Mapped[bool] = mapped_column(Boolean, nullable=False)
