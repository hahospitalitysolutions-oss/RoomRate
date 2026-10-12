"""Count the accommodations each ferry sailing still offers.

The first live Ferryhopper answers (probe 2026-10-11) flag no sold-out
sailing: a full cabin or fare tier simply leaves the sailing's accommodation
list. ``accommodation_options`` is the priced accommodations of a route-day's
sailings; against the same route's usual count it is the filling-up signal.
NULL when an answer listed none. Additive only (docs/DEPLOYMENT.md §4).

Revision ID: 20261011_0032
Revises: 20261011_0031
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261011_0032"
down_revision: str | None = "20261011_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add accommodation_options to the ferry observations."""
    op.add_column("roomrate_ferry_observations", sa.Column("accommodation_options", sa.Integer()))


def downgrade() -> None:
    """Drop accommodation_options."""
    op.drop_column("roomrate_ferry_observations", "accommodation_options")
