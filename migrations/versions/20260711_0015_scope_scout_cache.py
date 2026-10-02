"""Scope scout cache rows to the complete availability query.

The hotel candidate list depends on occupancy, currency and language, not
only destination and dates. Reusing the old broad cache key could therefore
feed unavailable or differently localized properties into a deep crawl.

Revision ID: 20260711_0015
Revises: 20260707_0014
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260711_0015"
down_revision: str | None = "20260707_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add availability dimensions and rebuild the cache lookup index."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                ALTER TABLE scout_cache
                    ADD COLUMN IF NOT EXISTS adults integer NOT NULL DEFAULT 2,
                    ADD COLUMN IF NOT EXISTS children integer NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS rooms integer NOT NULL DEFAULT 1,
                    ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'EUR',
                    ADD COLUMN IF NOT EXISTS language text NOT NULL DEFAULT 'el';

                -- Οι παλιές εγγραφές δεν δημιουργήθηκαν με occupancy-aware
                -- actor input, άρα δεν είναι ασφαλές να επαναχρησιμοποιηθούν.
                DELETE FROM scout_cache;
                DROP INDEX IF EXISTS ix_scout_cache_lookup;
                ALTER TABLE scout_cache
                    DROP CONSTRAINT IF EXISTS uq_scout_cache_query_hotel;
                DROP INDEX IF EXISTS uq_scout_cache_query_hotel;
                ALTER TABLE scout_cache
                    ADD CONSTRAINT uq_scout_cache_query_hotel UNIQUE (
                        destination, check_in, check_out,
                        adults, children, rooms, currency, language, hotel_url
                    );
                CREATE INDEX ix_scout_cache_lookup
                    ON scout_cache (
                        destination, check_in, check_out,
                        adults, children, rooms, currency, language,
                        cached_at DESC
                    );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Restore the original destination/date cache key."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('scout_cache') IS NOT NULL THEN
                DROP INDEX IF EXISTS ix_scout_cache_lookup;
                ALTER TABLE scout_cache
                    DROP CONSTRAINT IF EXISTS uq_scout_cache_query_hotel;
                DROP INDEX IF EXISTS uq_scout_cache_query_hotel;
                ALTER TABLE scout_cache
                    DROP COLUMN IF EXISTS language,
                    DROP COLUMN IF EXISTS currency,
                    DROP COLUMN IF EXISTS rooms,
                    DROP COLUMN IF EXISTS children,
                    DROP COLUMN IF EXISTS adults;
                CREATE INDEX ix_scout_cache_lookup
                    ON scout_cache (destination, check_in, check_out, cached_at DESC);
            END IF;
        END $$;
        """
    )
