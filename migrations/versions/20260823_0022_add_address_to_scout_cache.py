"""Let the scout cache carry the property address.

``scraper/utils.py:_extract_meta`` returns an ``address`` for every scouted
hotel and ``BookingPropertyCandidateProvider._candidate_from_hotel`` maps it
straight onto ``PropertyCandidate.address``, but ``scout_cache`` had no column
for it. That was invisible while onboarding always scouted live; once the
onboarding searches started reading the cache, every cache HIT produced
candidates with a blank address -- blank wizard cards, and an owned property
persisted without one.

The column is nullable on purpose: rows cached before this migration keep a
NULL that ``_load_scout_cache`` reads back as ``""``, which is exactly what a
live scout returns for a hotel with no address.

Revision ID: 20260823_0022
Revises: 20260727_0021
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260823_0022"
down_revision: str | None = "20260727_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable address column when the scraper-owned table exists."""
    # scout_cache is created by scraper/tables.py on the first non-dry-run
    # scraper run, not by alembic, so it may legitimately not exist yet.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                ALTER TABLE scout_cache
                    ADD COLUMN IF NOT EXISTS address text;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop the address column again."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                ALTER TABLE scout_cache
                    DROP COLUMN IF EXISTS address;
            END IF;
        END $$;
        """
    )
