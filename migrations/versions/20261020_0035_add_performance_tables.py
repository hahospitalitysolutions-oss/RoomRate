"""«Απόδοση»: the owner's own bookings, imported from Excel, and what they need.

- ``roomrate_performance_bookings``: one row per booking of an owned property
  (``booking_ref`` unique per property: re-uploading a file replaces, never
  duplicates). No guest names, emails, phones or addresses, ever.
- ``roomrate_performance_booking_nights``: the same bookings one row per
  night (revenue and commission spread evenly), for every stay-date KPI.
- ``roomrate_performance_room_inventory``: rooms per owned room type
  (optionally for a date range): the denominator of occupancy and RevPAR.
- ``roomrate_performance_closures``: dates the property, a room type or some
  rooms were closed: never «unsold».
- ``roomrate_performance_label_mappings``: a file's room label → the owned
  room type (or ignored), remembered for the next upload.
- ``roomrate_performance_imports`` / ``roomrate_performance_import_rows``: an
  uploaded file and its checked rows, kept until commit (or 24 hours).
- ``roomrate_performance_revenue_settings``: VAT and resilience fee for the
  net amounts; a missing row is the defaults.

Contract: docs/performance/API.md. Deny-by-default RLS like every RoomRate
table (migration 0019). Additive only (docs/DEPLOYMENT.md §4).

Revision ID: 20261020_0035
Revises: 20261012_0034
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261020_0035"
down_revision: str | None = "20261012_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "roomrate_performance_revenue_settings",
    "roomrate_performance_room_inventory",
    "roomrate_performance_closures",
    "roomrate_performance_label_mappings",
    "roomrate_performance_imports",
    "roomrate_performance_import_rows",
    "roomrate_performance_bookings",
    "roomrate_performance_booking_nights",
)


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _owner_columns() -> list[sa.Column]:
    return [
        sa.Column("account_id", _uuid(), sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "owned_property_id",
            _uuid(),
            sa.ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _room_type(nullable: bool, ondelete: str) -> sa.Column:
    return sa.Column(
        "room_type_id",
        _uuid(),
        sa.ForeignKey("roomrate_owned_property_room_types.id", ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    """Create the eight «Απόδοση» tables."""
    op.create_table(
        "roomrate_performance_revenue_settings",
        sa.Column(
            "owned_property_id",
            _uuid(),
            sa.ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("account_id", _uuid(), sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vat_pct", sa.Numeric(5, 2), server_default="13", nullable=False),
        sa.Column("resilience_fee_eur", sa.Numeric(6, 2), server_default="0", nullable=False),
        sa.Column("fee_included_in_price", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("vat_pct BETWEEN 0 AND 100", name="ck_roomrate_performance_revenue_settings_vat"),
        sa.CheckConstraint("resilience_fee_eur >= 0", name="ck_roomrate_performance_revenue_settings_fee"),
    )
    op.create_index(
        "ix_roomrate_performance_revenue_settings_account_id",
        "roomrate_performance_revenue_settings",
        ["account_id"],
    )

    op.create_table(
        "roomrate_performance_room_inventory",
        sa.Column("id", _uuid(), primary_key=True),
        *_owner_columns(),
        _room_type(nullable=False, ondelete="CASCADE"),
        sa.Column("rooms", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        *_timestamps(),
        sa.CheckConstraint("rooms >= 0", name="ck_roomrate_performance_room_inventory_rooms"),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
            name="ck_roomrate_performance_room_inventory_range",
        ),
    )
    op.create_index(
        "ix_roomrate_performance_room_inventory_property",
        "roomrate_performance_room_inventory",
        ["owned_property_id", "room_type_id"],
    )

    op.create_table(
        "roomrate_performance_closures",
        sa.Column("id", _uuid(), primary_key=True),
        *_owner_columns(),
        _room_type(nullable=True, ondelete="CASCADE"),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("rooms", sa.Integer()),
        sa.Column("reason", sa.String(255)),
        *_timestamps(),
        sa.CheckConstraint("date_to >= date_from", name="ck_roomrate_performance_closures_range"),
        sa.CheckConstraint("rooms IS NULL OR rooms > 0", name="ck_roomrate_performance_closures_rooms"),
    )
    op.create_index(
        "ix_roomrate_performance_closures_property_dates",
        "roomrate_performance_closures",
        ["owned_property_id", "date_from"],
    )

    op.create_table(
        "roomrate_performance_label_mappings",
        sa.Column(
            "owned_property_id",
            _uuid(),
            sa.ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("label", sa.String(255), primary_key=True),
        sa.Column("account_id", _uuid(), sa.ForeignKey("roomrate_accounts.id", ondelete="CASCADE"), nullable=False),
        _room_type(nullable=True, ondelete="CASCADE"),
        sa.Column("ignored", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "(room_type_id IS NULL) = ignored",
            name="ck_roomrate_performance_label_mappings_target",
        ),
    )
    op.create_index(
        "ix_roomrate_performance_label_mappings_account_id",
        "roomrate_performance_label_mappings",
        ["account_id"],
    )

    op.create_table(
        "roomrate_performance_imports",
        sa.Column("id", _uuid(), primary_key=True),
        *_owner_columns(),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("rows_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("errors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("bookings_imported", sa.Integer()),
        sa.Column("bookings_updated", sa.Integer()),
        sa.Column("rows_skipped", sa.Integer()),
        *_timestamps(),
        sa.CheckConstraint("format IN ('template', 'booking')", name="ck_roomrate_performance_imports_format"),
    )
    op.create_index(
        "ix_roomrate_performance_imports_property_created",
        "roomrate_performance_imports",
        ["owned_property_id", "created_at"],
    )
    op.create_index("ix_roomrate_performance_imports_expires_at", "roomrate_performance_imports", ["expires_at"])

    op.create_table(
        "roomrate_performance_import_rows",
        sa.Column(
            "import_id",
            _uuid(),
            sa.ForeignKey("roomrate_performance_imports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("row_number", sa.Integer(), primary_key=True),
        sa.Column("booking_ref", sa.String(100)),
        sa.Column("room_label", sa.String(255)),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
    )

    op.create_table(
        "roomrate_performance_bookings",
        sa.Column("id", _uuid(), primary_key=True),
        *_owner_columns(),
        sa.Column(
            "import_id",
            _uuid(),
            sa.ForeignKey("roomrate_performance_imports.id", ondelete="SET NULL"),
        ),
        _room_type(nullable=True, ondelete="SET NULL"),
        sa.Column("booking_ref", sa.String(100), nullable=False),
        sa.Column("room_label", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("booked_on", sa.Date()),
        sa.Column("cancelled_on", sa.Date()),
        sa.Column("arrival", sa.Date(), nullable=False),
        sa.Column("departure", sa.Date(), nullable=False),
        sa.Column("rooms", sa.Integer(), server_default="1", nullable=False),
        sa.Column("adults", sa.Integer()),
        sa.Column("children", sa.Integer()),
        sa.Column("gross_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("commission_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("country_code", sa.String(2)),
        *_timestamps(),
        sa.UniqueConstraint("owned_property_id", "booking_ref", name="uq_roomrate_performance_bookings_ref"),
        sa.CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'no_show')",
            name="ck_roomrate_performance_bookings_status",
        ),
        sa.CheckConstraint("departure > arrival", name="ck_roomrate_performance_bookings_stay"),
        sa.CheckConstraint("rooms >= 1", name="ck_roomrate_performance_bookings_rooms"),
        sa.CheckConstraint(
            "gross_eur >= 0 AND commission_eur >= 0",
            name="ck_roomrate_performance_bookings_amounts",
        ),
    )
    op.create_index(
        "ix_roomrate_performance_bookings_property_arrival",
        "roomrate_performance_bookings",
        ["owned_property_id", "arrival"],
    )
    op.create_index(
        "ix_roomrate_performance_bookings_property_booked_on",
        "roomrate_performance_bookings",
        ["owned_property_id", "booked_on"],
    )
    op.create_index("ix_roomrate_performance_bookings_account_id", "roomrate_performance_bookings", ["account_id"])

    op.create_table(
        "roomrate_performance_booking_nights",
        sa.Column(
            "booking_id",
            _uuid(),
            sa.ForeignKey("roomrate_performance_bookings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("stay_date", sa.Date(), primary_key=True),
        sa.Column(
            "owned_property_id",
            _uuid(),
            sa.ForeignKey("roomrate_owned_properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _room_type(nullable=True, ondelete="SET NULL"),
        sa.Column("rooms", sa.Integer(), nullable=False),
        sa.Column("gross_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("commission_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("sold", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_roomrate_performance_booking_nights_property_date",
        "roomrate_performance_booking_nights",
        ["owned_property_id", "stay_date"],
    )

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Drop the eight «Απόδοση» tables, dependants first."""
    for table in reversed(TABLES):
        op.drop_table(table)
