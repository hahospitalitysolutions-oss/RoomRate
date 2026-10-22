"""«Απόδοση» uploads: check a file, map its room labels, store its bookings; and the page's status.

1. ``preview_upload`` reads the file, keeps every row for 24 hours and
   answers the preview: new / replaced / cancelled bookings, the rows that
   will be skipped, each room label with the room type it maps to.
2. ``update_mapping`` stores the owner's choices (remembered for next time).
3. ``commit`` stores the bookings, and a template's rooms and closed periods.

Contract: docs/performance/API.md («Imports in detail»).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from api.repositories.performance import PerformanceRepository
from api.services.performance.amounts import net_amount
from api.services.performance.importing.labels import LabelMap, label_counts, map_labels
from api.services.performance.importing.reader import read_bookings_file
from api.services.performance.importing.staging import (
    from_json,
    staged_bookings,
    staged_extras,
    staged_rows,
)

IMPORT_TTL = timedelta(hours=24)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SAMPLE_ROWS = 8


class ImportNotFound(LookupError):
    """No such upload for the property, or its 24 hours are over (404)."""


class ImportAlreadyCommitted(RuntimeError):
    """The upload's bookings are stored already (409)."""


class ImportRejected(ValueError):
    """The owner must act first; the message is Greek (400)."""


class PerformanceImportService:
    def __init__(self, repository: PerformanceRepository):
        self.repository = repository

    def status(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict[str, Any]:
        """PerformanceStatus. Occupancy needs the rooms of every room type that has bookings (of all, before any)."""
        counts = self.repository.status_counts(account_id, owned_property_id)
        room_types = self.repository.room_types(account_id, owned_property_id)
        last = self.repository.imports(account_id, owned_property_id, limit=1)
        booked = [room for room in room_types if room["id"] in counts["room_type_ids"]] or room_types
        return {
            "has_data": counts["bookings"] > 0,
            "bookings": counts["bookings"],
            "first_stay_date": counts["first_stay_date"],
            "last_stay_date": counts["last_stay_date"],
            "last_import": last[0] if last else None,
            "inventory_complete": bool(booked) and all(room["rooms"] is not None for room in booked),
            "room_types": [{"id": room["id"], "label": room["label"], "rooms": room["rooms"]} for room in room_types],
            "seasons": counts["seasons"],
        }

    # ------------------------------------------------------------------
    # Upload → preview → mapping
    # ------------------------------------------------------------------

    def preview_upload(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        content: bytes,
        filename: str,
        requested_format: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Read and keep the file; ImportFileError (400) when it cannot be read at all."""
        parsed = read_bookings_file(content, filename, requested_format)
        rows = staged_rows(parsed)
        upload = {
            "format": parsed.format,
            "filename": filename or "kratiseis",
            "rows_total": len(rows),
            "errors": parsed.errors,
            "extras": staged_extras(parsed),
            "committed_at": None,
        }
        upload["id"] = self.repository.create_import(
            account_id,
            owned_property_id,
            format=upload["format"],
            filename=upload["filename"],
            rows_total=upload["rows_total"],
            errors=upload["errors"],
            extras=upload["extras"],
            rows=rows,
            expires_at=(now or datetime.now(timezone.utc)) + IMPORT_TTL,
        )
        # The rows are in hand: no need to read back what was just stored.
        return self._preview(account_id, owned_property_id, upload, rows)

    def preview(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, import_id: uuid.UUID) -> dict[str, Any]:
        upload = self._upload(account_id, owned_property_id, import_id)
        return self._preview(account_id, owned_property_id, upload, self.repository.import_rows(account_id, import_id))

    def update_mapping(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        import_id: uuid.UUID,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Map, ignore or unmap labels of the upload; the choice is remembered for the next uploads."""
        upload = self._open_upload(account_id, owned_property_id, import_id)
        owned = {room["id"] for room in self.repository.room_types(account_id, owned_property_id)}
        if any(not item.get("ignore") and item.get("room_type_id") not in (None, *owned) for item in items):
            raise ImportRejected("Ο τύπος δωματίου δεν ανήκει στο κατάλυμα.")
        self.repository.save_label_mappings(
            account_id,
            owned_property_id,
            [
                {"label": item["label"], "room_type_id": item.get("room_type_id"), "ignored": bool(item.get("ignore"))}
                for item in items
                if item.get("ignore") or item.get("room_type_id") is not None
            ],
        )
        self.repository.delete_label_mappings(
            account_id,
            owned_property_id,
            [item["label"] for item in items if not item.get("ignore") and item.get("room_type_id") is None],
        )
        return self._preview(account_id, owned_property_id, upload, self.repository.import_rows(account_id, import_id))

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    def commit(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        import_id: uuid.UUID,
        *,
        skip_errors: bool,
    ) -> dict[str, Any]:
        upload = self._open_upload(account_id, owned_property_id, import_id)
        rows = self.repository.import_rows(account_id, import_id)
        bookings = staged_bookings(rows)
        labels = self._labels(account_id, owned_property_id, upload, bookings)
        if not labels.ready:
            raise ImportRejected("Αντιστοιχίστε πρώτα κάθε τύπο δωματίου του αρχείου (ή αγνοήστε τον).")
        if upload["errors"] and not skip_errors:
            raise ImportRejected(
                f"Το αρχείο έχει {len(upload['errors'])} γραμμές με λάθη: διορθώστε τες ή επιλέξτε να παραλειφθούν."
            )
        kept = [
            {**booking, "room_type_id": labels.mapped[booking["room_label"]]}
            for booking in bookings
            if booking["room_label"] not in labels.ignored
        ]
        skipped = sum(1 for row in rows if row["error"]) + len(bookings) - len(kept)
        extras = upload.get("extras") or {}
        inventory = [
            {**from_json(room), "room_type_id": labels.mapped[room["room_label"]]}
            for room in extras.get("rooms", [])
            if labels.mapped.get(room["room_label"])
        ]
        closures = [
            {**from_json(closure), "room_type_id": labels.mapped.get(closure["room_label"])}
            for closure in extras.get("closures", [])
            if closure.get("room_label") is None or labels.mapped.get(closure["room_label"])
        ]
        # Remember this upload's labels, the ones mapped by name too.
        self.repository.save_label_mappings(
            account_id,
            owned_property_id,
            [{"label": label, "room_type_id": room} for label, room in labels.mapped.items() if room]
            + [{"label": label, "ignored": True} for label in labels.ignored],
        )
        counts = self.repository.commit_import(
            account_id, owned_property_id, import_id, kept,
            skipped=skipped, inventory=inventory or None, closures=closures or None,
        )
        if counts is None:
            raise ImportAlreadyCommitted()
        return {
            "imported": counts["inserted"],
            "updated": counts["updated"],
            "skipped": skipped,
            "status": self.status(account_id, owned_property_id),
        }

    # ------------------------------------------------------------------

    def _upload(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, import_id: uuid.UUID) -> dict[str, Any]:
        upload = self.repository.get_import(account_id, owned_property_id, import_id)
        if upload is None:
            raise ImportNotFound()
        return upload

    def _open_upload(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, import_id: uuid.UUID) -> dict[str, Any]:
        """The upload, refused when its bookings are stored already."""
        upload = self._upload(account_id, owned_property_id, import_id)
        if upload["committed_at"] is not None:
            raise ImportAlreadyCommitted()
        return upload

    def _labels(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID, upload: dict[str, Any], bookings: list[dict[str, Any]]
    ) -> LabelMap:
        rooms_sheet = [room["room_label"] for room in (upload.get("extras") or {}).get("rooms", [])]
        return map_labels(
            label_counts(bookings, rooms_sheet),
            self.repository.room_types(account_id, owned_property_id),
            self.repository.label_mappings(account_id, owned_property_id),
        )

    def _preview(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        upload: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bookings = staged_bookings(rows)
        labels = self._labels(account_id, owned_property_id, upload, bookings)
        kept = [booking for booking in bookings if booking["room_label"] not in labels.ignored]
        stored = self.repository.existing_refs(account_id, owned_property_id, [b["booking_ref"] for b in kept])
        settings = self.repository.revenue_settings(account_id, owned_property_id)
        return {
            "id": upload["id"],
            "format": upload["format"],
            "filename": upload["filename"],
            "rows_total": upload["rows_total"],
            "new": sum(1 for booking in kept if booking["booking_ref"] not in stored),
            "updated": sum(1 for booking in kept if booking["booking_ref"] in stored),
            "cancelled": sum(1 for booking in kept if booking["status"] == "cancelled"),
            "errors": upload["errors"],
            "mapping": labels.rows(),
            "sample": [_sample(row, labels, settings) for row in rows[:SAMPLE_ROWS]],
            "ready": labels.ready,
        }


def _sample(row: dict[str, Any], labels: LabelMap, settings: dict[str, Any]) -> dict[str, Any]:
    """One row of the preview's ``sample``, as read; an error row keeps what is known of it."""
    booking = from_json(row["data"]) if row["data"] and not row["error"] else {}
    label = booking.get("room_label") or row["room_label"] or ""
    nights = (booking["departure"] - booking["arrival"]).days if booking else None
    net = (
        round(net_amount(booking["gross_eur"], booking["commission_eur"], rooms=booking["rooms"], nights=nights,
                         settings=settings), 2)
        if booking else None
    )
    return {
        "booking_ref": booking.get("booking_ref") or row["booking_ref"] or "",
        "booked_on": booking.get("booked_on"),
        "arrival": booking.get("arrival"),
        "departure": booking.get("departure"),
        "nights": nights,
        "room_label": label,
        "room_type_id": labels.mapped.get(label),
        "rooms": booking.get("rooms") or 1,
        "gross_eur": booking.get("gross_eur"),
        "commission_eur": booking.get("commission_eur"),
        "net_eur": net,
        "channel": booking.get("channel") or "",
        "status": booking.get("status") or "confirmed",
        "error": row["error"],
    }
