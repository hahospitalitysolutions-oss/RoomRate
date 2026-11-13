"""Uploads: a checked file and its rows wait here (24 hours) until the owner commits them."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text

from api.repositories.performance.base import RepositoryBase, fence
from api.repositories.performance.writers import write_bookings, write_closures, write_inventory


class ImportQueries(RepositoryBase):
    def create_import(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        *,
        format: str,
        filename: str,
        rows_total: int,
        errors: list[dict[str, Any]],
        extras: dict[str, Any],
        rows: Sequence[dict[str, Any]],
        expires_at: datetime,
    ) -> uuid.UUID:
        """Store a checked upload and its rows (``row_number``, ``booking_ref``, ``room_label``, ``data``, ``error``).

        ``errors`` and ``extras`` and each row's ``data`` must be JSON-ready.
        Uploads of every account left uncommitted past ``expires_at`` are
        swept here.
        """
        import_id = uuid.uuid4()
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_imports
                    WHERE committed_at IS NULL AND expires_at < now()
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_performance_imports (
                        id, account_id, owned_property_id, format, filename, rows_total, errors, extras, expires_at
                    )
                    VALUES (
                        :id, :account_id, :owned_property_id, :format, :filename, :rows_total,
                        CAST(:errors AS jsonb), CAST(:extras AS jsonb), :expires_at
                    )
                    """
                ),
                {
                    **fence(account_id, owned_property_id),
                    "id": import_id,
                    "format": format,
                    "filename": filename[:255],
                    "rows_total": rows_total,
                    "errors": json.dumps(errors, ensure_ascii=False),
                    "extras": json.dumps(extras, ensure_ascii=False),
                    "expires_at": expires_at,
                },
            )
            if rows:
                connection.execute(
                    text(
                        """
                        INSERT INTO roomrate_performance_import_rows (
                            import_id, row_number, booking_ref, room_label, data, error
                        )
                        SELECT :import_id, r.row_number, r.booking_ref, r.room_label, r.data, r.error
                        FROM unnest(
                            CAST(:row_numbers AS integer[]),
                            CAST(:booking_refs AS text[]),
                            CAST(:room_labels AS text[]),
                            CAST(:data AS jsonb[]),
                            CAST(:errors AS text[])
                        ) AS r(row_number, booking_ref, room_label, data, error)
                        """
                    ),
                    {
                        "import_id": import_id,
                        "row_numbers": [row["row_number"] for row in rows],
                        "booking_refs": [row.get("booking_ref") for row in rows],
                        "room_labels": [row.get("room_label") for row in rows],
                        "data": [json.dumps(row.get("data") or {}, ensure_ascii=False) for row in rows],
                        "errors": [row.get("error") for row in rows],
                    },
                )
        return import_id

    def get_import(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        import_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """The upload, committed or still within its 24 hours; None otherwise."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, format, filename, rows_total, errors, extras, expires_at, committed_at
                    FROM roomrate_performance_imports
                    WHERE id = :import_id
                      AND account_id = :account_id AND owned_property_id = :owned_property_id
                      AND (committed_at IS NOT NULL OR expires_at > now())
                    """
                ),
                {**fence(account_id, owned_property_id), "import_id": import_id},
            ).mappings().first()
        return dict(row) if row else None

    def import_rows(self, account_id: uuid.UUID, import_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT r.row_number, r.booking_ref, r.room_label, r.data, r.error
                    FROM roomrate_performance_import_rows r
                    JOIN roomrate_performance_imports i ON i.id = r.import_id AND i.account_id = :account_id
                    WHERE r.import_id = :import_id
                    ORDER BY r.row_number
                    """
                ),
                {"account_id": account_id, "import_id": import_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def commit_import(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        import_id: uuid.UUID,
        bookings: Sequence[dict[str, Any]],
        *,
        skipped: int,
        inventory: Sequence[dict[str, Any]] | None = None,
        closures: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int] | None:
        """Store the upload's bookings (and a template's rooms and closures) in one transaction.

        The upload is claimed first: a second commit of the same upload (a
        double click) gets None and writes nothing. ``inventory`` replaces
        the counts of the room types it names only; ``closures`` replaces
        all closed periods. The staged rows are deleted.
        """
        with self._engine("writer").begin() as connection:
            claimed = connection.execute(
                text(
                    """
                    UPDATE roomrate_performance_imports
                    SET committed_at = now(), updated_at = now()
                    WHERE id = :import_id
                      AND account_id = :account_id AND owned_property_id = :owned_property_id
                      AND committed_at IS NULL AND expires_at > now()
                    RETURNING id
                    """
                ),
                {**fence(account_id, owned_property_id), "import_id": import_id},
            ).mappings().first()
            if claimed is None:
                return None
            counts = write_bookings(connection, account_id, owned_property_id, bookings, import_id)
            if inventory:
                named = [row["room_type_id"] for row in inventory if row.get("rooms") is not None]
                write_inventory(connection, account_id, owned_property_id, inventory, only_room_types=named)
            if closures:
                write_closures(connection, account_id, owned_property_id, closures)
            connection.execute(
                text(
                    """
                    UPDATE roomrate_performance_imports
                    SET bookings_imported = :inserted, bookings_updated = :updated, rows_skipped = :skipped,
                        extras = '{}'::jsonb
                    WHERE id = :import_id
                    """
                ),
                {"import_id": import_id, "skipped": skipped, **counts},
            )
            connection.execute(
                text("DELETE FROM roomrate_performance_import_rows WHERE import_id = :import_id"),
                {"import_id": import_id},
            )
        return counts

    def imports(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        """Committed uploads, newest first; ``bookings_imported`` counts the new and the replaced."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, format, filename, committed_at,
                           COALESCE(bookings_imported, 0) + COALESCE(bookings_updated, 0) AS bookings_imported
                    FROM roomrate_performance_imports
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                      AND committed_at IS NOT NULL
                    ORDER BY committed_at DESC
                    LIMIT :limit
                    """
                ),
                {**fence(account_id, owned_property_id), "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

