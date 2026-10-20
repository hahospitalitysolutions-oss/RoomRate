"""«Απόδοση» uploads: check a file, map its room labels, store its bookings; and the page's status.

1. ``preview_upload`` reads the file (performance_import), keeps every row for
   24 hours and answers the preview: new / replaced / cancelled bookings, the
   rows that will be skipped, each room label with the room type it maps to.
   A label maps as the owner mapped it in an earlier upload, else to the
   room type of the same name (or nearly: word order, accents, case).
2. ``update_mapping`` stores the owner's choices (remembered for next time).
3. ``commit`` stores the bookings, and a template's rooms and closed periods.

Contract: docs/performance/API.md («Imports in detail»).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from rapidfuzz import fuzz

from api.repositories.performance_repository import PerformanceRepository
from api.services.performance_import import ParsedFile, net_eur, plain_text, read_bookings_file

IMPORT_TTL = timedelta(hours=24)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SAMPLE_ROWS = 8
# token_sort_ratio: «Standard Double Room» = «Double Room Standard», but «Double Room» ≠ «Double Room with Sea View».
_NEAR_MATCH = 90

_DATE_KEYS = ("booked_on", "cancelled_on", "arrival", "departure", "date_from", "date_to", "valid_from", "valid_to")


class ImportNotFound(LookupError):
    """No such upload for the property, or its 24 hours are over (404)."""


class ImportAlreadyCommitted(RuntimeError):
    """The upload's bookings are stored already (409)."""


class ImportRejected(ValueError):
    """The owner must act first; the message is Greek (400)."""


def booking_json(booking: dict[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, date) else value for key, value in booking.items()}


def booking_from_json(data: dict[str, Any]) -> dict[str, Any]:
    booking = dict(data)
    for key in _DATE_KEYS:
        if booking.get(key):
            booking[key] = date.fromisoformat(booking[key])
    return booking


def _extras_json(parsed: ParsedFile) -> dict[str, Any]:
    if parsed.format != "template":
        return {}
    return {
        "rooms": [booking_json(room) for room in parsed.rooms],
        "closures": [booking_json(closure) for closure in parsed.closures],
    }


def suggest_room_type(label: str, room_types: list[dict[str, Any]]) -> uuid.UUID | None:
    """The owned room type a file label names: the same name, else one clearly closest (≥ 90, no tie)."""
    plain = plain_text(label)
    for room_type in room_types:
        if plain_text(room_type["label"]) == plain:
            return room_type["id"]
    scored = sorted(
        ((fuzz.token_sort_ratio(plain, plain_text(room_type["label"])), room_type["id"]) for room_type in room_types),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= _NEAR_MATCH and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


class PerformanceImportService:
    def __init__(self, repository: PerformanceRepository):
        self.repository = repository

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

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
    # Upload → preview
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
        rows = [
            {
                "row_number": row.row,
                "booking_ref": row.booking_ref,
                "room_label": row.room_label,
                "data": booking_json(row.booking) if row.booking else None,
                "error": row.error,
            }
            for row in parsed.rows
        ]
        import_id = self.repository.create_import(
            account_id,
            owned_property_id,
            format=parsed.format,
            filename=filename or "kratiseis",
            rows_total=len(rows),
            errors=parsed.errors,
            extras=_extras_json(parsed),
            rows=rows,
            expires_at=(now or datetime.now(timezone.utc)) + IMPORT_TTL,
        )
        return self.preview(account_id, owned_property_id, import_id)

    def preview(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, import_id: uuid.UUID) -> dict[str, Any]:
        upload = self._upload(account_id, owned_property_id, import_id)
        state = self._state(account_id, owned_property_id, upload)
        settings = self.repository.revenue_settings(account_id, owned_property_id)
        kept = [booking for booking in state["bookings"] if not state["ignored"].get(booking["room_label"])]
        stored = self.repository.existing_refs(
            account_id, owned_property_id, [booking["booking_ref"] for booking in kept]
        )
        return {
            "id": upload["id"],
            "format": upload["format"],
            "filename": upload["filename"],
            "rows_total": upload["rows_total"],
            "new": sum(1 for booking in kept if booking["booking_ref"] not in stored),
            "updated": sum(1 for booking in kept if booking["booking_ref"] in stored),
            "cancelled": sum(1 for booking in kept if booking["status"] == "cancelled"),
            "errors": upload["errors"],
            "mapping": [
                {
                    "label": label,
                    "bookings": count,
                    "room_type_id": state["mapped"].get(label),
                    "ignored": bool(state["ignored"].get(label)),
                }
                for label, count in state["labels"].items()
            ],
            "sample": [self._sample(row, state, settings) for row in state["rows"][:SAMPLE_ROWS]],
            "ready": state["ready"],
        }

    def update_mapping(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        import_id: uuid.UUID,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Map, ignore or unmap labels of the upload; the choice is remembered for the next uploads."""
        upload = self._upload(account_id, owned_property_id, import_id)
        if upload["committed_at"] is not None:
            raise ImportAlreadyCommitted()
        owned = {room["id"] for room in self.repository.room_types(account_id, owned_property_id)}
        for item in items:
            if not item.get("ignore") and item.get("room_type_id") is not None and item["room_type_id"] not in owned:
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
        return self.preview(account_id, owned_property_id, import_id)

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
        upload = self._upload(account_id, owned_property_id, import_id)
        if upload["committed_at"] is not None:
            raise ImportAlreadyCommitted()
        state = self._state(account_id, owned_property_id, upload)
        if not state["ready"]:
            raise ImportRejected("Αντιστοιχίστε πρώτα κάθε τύπο δωματίου του αρχείου (ή αγνοήστε τον).")
        if upload["errors"] and not skip_errors:
            raise ImportRejected(
                f"Το αρχείο έχει {len(upload['errors'])} γραμμές με λάθη: διορθώστε τες ή επιλέξτε να παραλειφθούν."
            )
        mapped, ignored = state["mapped"], state["ignored"]
        bookings = [
            {**booking, "room_type_id": mapped[booking["room_label"]]}
            for booking in state["bookings"]
            if not ignored.get(booking["room_label"])
        ]
        skipped = sum(1 for row in state["rows"] if row["error"]) + len(state["bookings"]) - len(bookings)
        extras = upload.get("extras") or {}
        inventory = [
            {**booking_from_json(room), "room_type_id": mapped[room["room_label"]]}
            for room in extras.get("rooms", [])
            if mapped.get(room["room_label"])
        ]
        closures = [
            {**booking_from_json(closure), "room_type_id": mapped.get(closure["room_label"])}
            for closure in extras.get("closures", [])
            if closure.get("room_label") is None or mapped.get(closure["room_label"])
        ]
        # Remember this upload's labels, the ones mapped by name too.
        self.repository.save_label_mappings(
            account_id,
            owned_property_id,
            [{"label": label, "room_type_id": room_type_id} for label, room_type_id in mapped.items() if room_type_id]
            + [{"label": label, "ignored": True} for label, flag in ignored.items() if flag],
        )
        counts = self.repository.commit_import(
            account_id,
            owned_property_id,
            import_id,
            bookings,
            skipped=skipped,
            inventory=inventory or None,
            closures=closures or None,
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

    def _state(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, upload: dict[str, Any]) -> dict[str, Any]:
        """The upload's rows, its labels with their bookings, and where each label goes now."""
        rows = self.repository.import_rows(account_id, upload["id"])
        bookings = [booking_from_json(row["data"]) for row in rows if row["data"] and not row["error"]]
        labels: dict[str, int] = {}
        for booking in bookings:
            labels[booking["room_label"]] = labels.get(booking["room_label"], 0) + 1
        for room in (upload.get("extras") or {}).get("rooms", []):
            labels.setdefault(room["room_label"], 0)
        labels = dict(sorted(labels.items(), key=lambda item: (-item[1], item[0])))

        room_types = self.repository.room_types(account_id, owned_property_id)
        owned = {room["id"] for room in room_types}
        remembered = self.repository.label_mappings(account_id, owned_property_id)
        mapped: dict[str, uuid.UUID | None] = {}
        ignored: dict[str, bool] = {}
        for label in labels:
            choice = remembered.get(label)
            if choice and choice["ignored"]:
                ignored[label] = True
                mapped[label] = None
            elif choice and choice["room_type_id"] in owned:
                mapped[label] = choice["room_type_id"]
            else:
                mapped[label] = suggest_room_type(label, room_types)
        # A label of the «Δωμάτια» sheet with no booking never holds the upload back.
        ready = all(mapped[label] or ignored.get(label) for label, count in labels.items() if count)
        return {
            "rows": rows, "bookings": bookings, "labels": labels, "mapped": mapped, "ignored": ignored, "ready": ready,
        }

    @staticmethod
    def _sample(row: dict[str, Any], state: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        booking = booking_from_json(row["data"]) if row["data"] and not row["error"] else {}
        label = booking.get("room_label") or row["room_label"] or ""
        nights = (booking["departure"] - booking["arrival"]).days if booking else None
        gross, commission = booking.get("gross_eur"), booking.get("commission_eur")
        return {
            "booking_ref": booking.get("booking_ref") or row["booking_ref"] or "",
            "booked_on": booking.get("booked_on"),
            "arrival": booking.get("arrival"),
            "departure": booking.get("departure"),
            "nights": nights,
            "room_label": label,
            "room_type_id": state["mapped"].get(label),
            "rooms": booking.get("rooms") or 1,
            "gross_eur": gross,
            "commission_eur": commission,
            "net_eur": net_eur(gross, commission or 0.0, rooms=booking["rooms"], nights=nights, **settings)
            if booking else None,
            "channel": booking.get("channel") or "",
            "status": booking.get("status") or "confirmed",
            "error": row["error"],
        }
