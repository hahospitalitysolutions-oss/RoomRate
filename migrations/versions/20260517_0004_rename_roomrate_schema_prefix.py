"""Rename legacy RoomRate schema prefix.

Revision ID: 20260517_0004
Revises: 20260510_0003
Create Date: 2026-05-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260517_0004"
down_revision: str | None = "20260510_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rename_schema_prefix(from_prefix: str, to_prefix: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            old_prefix text := {from_prefix!r};
            new_prefix text := {to_prefix!r};
            item record;
            new_name text;
        BEGIN
            FOR item IN
                SELECT c.oid, c.relkind, c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relkind IN ('r', 'p', 'v', 'm')
                  AND c.relname LIKE old_prefix || '\\_%' ESCAPE '\\'
                ORDER BY CASE c.relkind WHEN 'v' THEN 1 WHEN 'm' THEN 2 ELSE 3 END
            LOOP
                new_name := new_prefix || substring(item.relname from length(old_prefix) + 1);
                IF to_regclass(quote_ident(new_name)) IS NULL THEN
                    IF item.relkind IN ('r', 'p') THEN
                        EXECUTE format('ALTER TABLE %I RENAME TO %I', item.relname, new_name);
                    ELSIF item.relkind = 'v' THEN
                        EXECUTE format('ALTER VIEW %I RENAME TO %I', item.relname, new_name);
                    ELSIF item.relkind = 'm' THEN
                        EXECUTE format('ALTER MATERIALIZED VIEW %I RENAME TO %I', item.relname, new_name);
                    END IF;
                END IF;
            END LOOP;

            FOR item IN
                SELECT con.conname, con.conrelid::regclass::text AS table_name
                FROM pg_constraint con
                JOIN pg_namespace n ON n.oid = con.connamespace
                WHERE n.nspname = current_schema()
                  AND con.conname LIKE old_prefix || '\\_%' ESCAPE '\\'
            LOOP
                new_name := new_prefix || substring(item.conname from length(old_prefix) + 1);
                EXECUTE format('ALTER TABLE %s RENAME CONSTRAINT %I TO %I', item.table_name, item.conname, new_name);
            END LOOP;

            FOR item IN
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relkind = 'i'
                  AND c.relname LIKE old_prefix || '\\_%' ESCAPE '\\'
            LOOP
                new_name := new_prefix || substring(item.relname from length(old_prefix) + 1);
                IF to_regclass(quote_ident(new_name)) IS NULL THEN
                    EXECUTE format('ALTER INDEX %I RENAME TO %I', item.relname, new_name);
                END IF;
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    _rename_schema_prefix("room" + "pulse", "roomrate")


def downgrade() -> None:
    _rename_schema_prefix("roomrate", "room" + "pulse")
