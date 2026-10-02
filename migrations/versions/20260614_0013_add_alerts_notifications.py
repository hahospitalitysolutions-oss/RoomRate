"""Add alert rules and in-app notifications.

Phase D introduces price-change alerting and cross-process in-app
notifications. Two tables:

* ``roomrate_alert_rules``: per-account (optionally per-owned-property) rules
  describing when a competitor price change should raise a notification — a
  threshold percentage plus a direction (drop/rise/any). ``owned_property_id``
  NULL means the rule applies to every property in the account.
* ``roomrate_notifications``: the in-app notification feed. Rows are written by
  the price-alert evaluator (after a competitor scrape completes) and by the
  schedule circuit breaker. ``alert_rule_id`` is SET NULL on rule delete so a
  notification outlives the rule that produced it. The ``(account_id, is_read,
  created_at DESC)`` index backs the unread feed query.

Revision ID: 20260614_0013
Revises: 20260613_0012
Create Date: 2026-06-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260614_0013"
down_revision: str | None = "20260613_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roomrate_alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL = the rule applies to every owned property in the account.
        sa.Column("owned_property_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_type", sa.String(length=50), nullable=False, server_default="price_change"),
        sa.Column("threshold_pct", sa.Numeric(5, 2), nullable=False, server_default="10.0"),
        sa.Column("direction", sa.String(length=10), nullable=False, server_default="any"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owned_property_id"], ["roomrate_owned_properties.id"], ondelete="CASCADE"),
        sa.CheckConstraint("threshold_pct > 0", name="ck_roomrate_alert_rules_threshold_pct"),
        sa.CheckConstraint("direction IN ('any', 'drop', 'rise')", name="ck_roomrate_alert_rules_direction"),
    )
    op.create_index(
        "ix_roomrate_alert_rules_account_active",
        "roomrate_alert_rules",
        ["account_id", "is_active"],
    )

    op.create_table(
        "roomrate_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["roomrate_accounts.id"], ondelete="CASCADE"),
        # SET NULL so a notification survives the deletion of its rule.
        sa.ForeignKeyConstraint(["alert_rule_id"], ["roomrate_alert_rules.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_roomrate_notifications_account_unread",
        "roomrate_notifications",
        ["account_id", "is_read", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_roomrate_notifications_account_unread", table_name="roomrate_notifications")
    op.drop_table("roomrate_notifications")
    op.drop_index("ix_roomrate_alert_rules_account_active", table_name="roomrate_alert_rules")
    op.drop_table("roomrate_alert_rules")
