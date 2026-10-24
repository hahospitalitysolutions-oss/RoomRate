"""«Απόδοση» uploads end to end on an in-memory repository: preview, mapping, commit, status."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from api.services.performance_import_service import (
    ImportAlreadyCommitted,
    ImportNotFound,
    ImportRejected,
    PerformanceImportService,
    suggest_room_type,
)
from api.services.performance_template import build_template

ACCOUNT_ID = uuid.UUID(int=1)
PROPERTY_ID = uuid.UUID(int=2)
DOUBLE, SUITE = uuid.UUID(int=11), uuid.UUID(int=12)
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)

HEADER = "Book number,Check-in,Check-out,Booked on,Status,Rooms,Adults,Price,Commission amount,Booker country,Unit type"


def export(*rows: str) -> bytes:
    return ("\n".join((HEADER, *rows)) + "\n").encode()


class MemoryRepository:
    """What PerformanceImportService needs of PerformanceRepository, in memory."""

    def __init__(self):
        self.types = [{"id": DOUBLE, "label": "Double Room", "rooms": 5}, {"id": SUITE, "label": "Junior Suite", "rooms": None}]
        self.mappings: dict[str, dict] = {}
        self.uploads: dict[uuid.UUID, dict] = {}
        self.rows: dict[uuid.UUID, list[dict]] = {}
        self.bookings: dict[str, dict] = {}
        self.inventory_written = None
        self.closures_written = None
        self.settings = {"vat_pct": 13.0, "resilience_fee_eur": 0.0, "fee_included_in_price": False}

    def room_types(self, account_id, owned_property_id):
        return [dict(room) for room in self.types]

    def label_mappings(self, account_id, owned_property_id):
        return dict(self.mappings)

    def save_label_mappings(self, account_id, owned_property_id, mappings):
        for mapping in mappings:
            self.mappings[mapping["label"]] = {
                "room_type_id": None if mapping.get("ignored") else mapping["room_type_id"],
                "ignored": bool(mapping.get("ignored")),
            }

    def delete_label_mappings(self, account_id, owned_property_id, labels):
        for label in labels:
            self.mappings.pop(label, None)

    def create_import(self, account_id, owned_property_id, *, format, filename, rows_total, errors, extras, rows, expires_at):
        import_id = uuid.uuid4()
        self.uploads[import_id] = {
            "id": import_id, "format": format, "filename": filename, "rows_total": rows_total, "errors": errors,
            "extras": extras, "expires_at": expires_at, "committed_at": None,
        }
        self.rows[import_id] = [dict(row) for row in rows]
        return import_id

    def get_import(self, account_id, owned_property_id, import_id):
        upload = self.uploads.get(import_id)
        return dict(upload) if upload else None

    def import_rows(self, account_id, import_id):
        return [dict(row) for row in self.rows.get(import_id, [])]

    def existing_refs(self, account_id, owned_property_id, refs):
        return {ref for ref in refs if ref in self.bookings}

    def revenue_settings(self, account_id, owned_property_id):
        return dict(self.settings)

    def commit_import(self, account_id, owned_property_id, import_id, bookings, *, skipped, inventory=None, closures=None):
        upload = self.uploads[import_id]
        if upload["committed_at"]:
            return None
        upload["committed_at"] = NOW
        inserted = sum(1 for booking in bookings if booking["booking_ref"] not in self.bookings)
        for booking in bookings:
            self.bookings[booking["booking_ref"]] = booking
        self.inventory_written, self.closures_written = inventory, closures
        upload["counts"] = (inserted, len(bookings) - inserted, skipped)
        return {"inserted": inserted, "updated": len(bookings) - inserted}

    def imports(self, account_id, owned_property_id, limit=50):
        done = [upload for upload in self.uploads.values() if upload["committed_at"]]
        return [{"id": u["id"], "format": u["format"], "filename": u["filename"], "committed_at": u["committed_at"],
                 "bookings_imported": u["counts"][0] + u["counts"][1]} for u in done][:limit]

    def status_counts(self, account_id, owned_property_id):
        held = [booking for booking in self.bookings.values() if booking["status"] != "cancelled"]
        return {
            "bookings": len(self.bookings),
            "first_stay_date": min((booking["arrival"] for booking in held), default=None),
            "last_stay_date": max((booking["departure"] for booking in held), default=None),
            "room_type_ids": {booking["room_type_id"] for booking in self.bookings.values() if booking["room_type_id"]},
            "seasons": sorted({booking["arrival"].year for booking in held}, reverse=True),
        }


@pytest.fixture
def repo():
    return MemoryRepository()


@pytest.fixture
def service(repo):
    return PerformanceImportService(repo)


def upload(service, content, filename="export.csv", fmt="booking"):
    return service.preview_upload(ACCOUNT_ID, PROPERTY_ID, content, filename, fmt, now=NOW)


# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Double Room", DOUBLE),
        ("double  room", DOUBLE),
        ("Room Double", DOUBLE),  # word order
        ("Junior Suíte", SUITE),  # accents
        ("Double Room with Sea View", None),  # more than the name is another room
        ("Family Room", None),
    ],
)
def test_labels_map_by_name(label, expected):
    room_types = [{"id": DOUBLE, "label": "Double Room"}, {"id": SUITE, "label": "Junior Suite"}]

    assert suggest_room_type(label, room_types) == expected


def test_preview_counts_maps_and_samples_without_storing_bookings(service, repo):
    repo.bookings["2"] = {"booking_ref": "2"}
    preview = upload(service, export(
        "1,2026-07-01,2026-07-04,2026-03-01,ok,1,2,339 EUR,51 EUR,de,Double Room",
        "2,2026-07-10,2026-07-12,2026-03-02,cancelled_by_guest,1,2,200 EUR,0,fr,Junior Suite",
        "3,2026-07-10,,2026-03-02,ok,1,2,200 EUR,0,fr,Junior Suite",
    ))

    assert preview["format"] == "booking" and preview["rows_total"] == 3
    assert (preview["new"], preview["updated"], preview["cancelled"]) == (1, 1, 1)
    assert preview["errors"] == [{"row": 4, "booking_ref": "3", "message": "Λείπει η ημερομηνία αναχώρησης."}]
    assert preview["mapping"] == [
        {"label": "Double Room", "bookings": 1, "room_type_id": DOUBLE, "ignored": False},
        {"label": "Junior Suite", "bookings": 1, "room_type_id": SUITE, "ignored": False},
    ]
    assert preview["ready"] is True
    first = preview["sample"][0]
    assert first["nights"] == 3 and first["net_eur"] == round(339 / 1.13 - 51, 2)
    assert preview["sample"][2]["error"] == "Λείπει η ημερομηνία αναχώρησης."
    assert repo.uploads[preview["id"]]["expires_at"] == NOW + timedelta(hours=24)
    assert set(repo.bookings) == {"2"}  # nothing stored yet


def test_an_unknown_label_holds_the_commit_until_mapped_then_is_remembered(service, repo):
    preview = upload(service, export("1,2026-07-01,2026-07-04,,ok,1,2,300,0,gr,Economy Twin"))
    assert preview["ready"] is False and preview["mapping"][0]["room_type_id"] is None
    with pytest.raises(ImportRejected, match="Αντιστοιχίστε πρώτα"):
        service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=True)

    mapped = service.update_mapping(
        ACCOUNT_ID, PROPERTY_ID, preview["id"], [{"label": "Economy Twin", "room_type_id": DOUBLE}]
    )
    assert mapped["ready"] is True and mapped["mapping"][0]["room_type_id"] == DOUBLE
    result = service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)

    assert result["imported"] == 1 and repo.bookings["1"]["room_type_id"] == DOUBLE
    # The next upload with the same label comes mapped.
    again = upload(service, export("9,2026-08-01,2026-08-03,,ok,1,2,300,0,gr,Economy Twin"))
    assert again["ready"] is True and again["mapping"][0]["room_type_id"] == DOUBLE


def test_an_ignored_label_leaves_its_bookings_out(service, repo):
    preview = upload(service, export(
        "1,2026-07-01,2026-07-04,,ok,1,2,300,0,gr,Double Room",
        "2,2026-07-01,2026-07-04,,ok,1,2,300,0,gr,Parking",
    ))
    mapped = service.update_mapping(
        ACCOUNT_ID, PROPERTY_ID, preview["id"], [{"label": "Parking", "room_type_id": None, "ignore": True}]
    )
    # An ignored label is told apart from one still to map.
    assert {"label": "Parking", "bookings": 1, "room_type_id": None, "ignored": True} in mapped["mapping"]
    assert mapped["ready"] is True
    result = service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)

    assert (result["imported"], result["skipped"]) == (1, 1)
    assert set(repo.bookings) == {"1"}
    assert repo.mappings["Parking"] == {"room_type_id": None, "ignored": True}


def test_errors_need_the_owner_to_skip_them(service):
    preview = upload(service, export("1,2026-07-01,,,ok,1,2,300,0,gr,Double Room", "2,2026-07-01,2026-07-02,,ok,1,2,90,0,gr,Double Room"))

    with pytest.raises(ImportRejected, match="1 γραμμές με λάθη"):
        service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)
    assert service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=True)["skipped"] == 1


def test_a_second_commit_and_a_mapping_after_commit_are_refused(service):
    preview = upload(service, export("1,2026-07-01,2026-07-04,,ok,1,2,300,0,gr,Double Room"))
    service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)

    with pytest.raises(ImportAlreadyCommitted):
        service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)
    with pytest.raises(ImportAlreadyCommitted):
        service.update_mapping(ACCOUNT_ID, PROPERTY_ID, preview["id"], [{"label": "Double Room", "room_type_id": DOUBLE}])
    with pytest.raises(ImportNotFound):
        service.preview(ACCOUNT_ID, PROPERTY_ID, uuid.uuid4())


def test_mapping_to_another_property_room_type_is_refused(service):
    preview = upload(service, export("1,2026-07-01,2026-07-04,,ok,1,2,300,0,gr,Twin"))

    with pytest.raises(ImportRejected, match="δεν ανήκει"):
        service.update_mapping(ACCOUNT_ID, PROPERTY_ID, preview["id"], [{"label": "Twin", "room_type_id": uuid.UUID(int=99)}])


def test_a_template_brings_its_rooms_and_closed_periods(service, repo):
    content = build_template(
        [{"label": "Double Room", "rooms": 6}, {"label": "Junior Suite", "rooms": 2}, {"label": "Loft", "rooms": 1}],
        bookings=[{"booking_ref": "T1", "arrival": date(2026, 7, 1), "departure": date(2026, 7, 3),
                   "room_label": "Double Room", "gross_eur": 200.0}],
        closures=[
            {"date_from": date(2026, 11, 1), "date_to": date(2027, 4, 30), "reason": "Εκτός σεζόν"},
            {"date_from": date(2026, 6, 1), "date_to": date(2026, 6, 5), "room_label": "Junior Suite", "rooms": 1},
            {"date_from": date(2026, 6, 1), "date_to": date(2026, 6, 5), "room_label": "Loft"},
        ],
    )
    preview = upload(service, content, "RoomRate_kratiseis.xlsx", "template")

    # «Loft» has no booking: listed to map, never holding the upload back.
    assert {"label": "Loft", "bookings": 0, "room_type_id": None, "ignored": False} in preview["mapping"]
    assert preview["ready"] is True
    service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)

    assert [(row["room_type_id"], row["rooms"]) for row in repo.inventory_written] == [(DOUBLE, 6), (SUITE, 2)]
    assert [(row["room_type_id"], row["date_from"]) for row in repo.closures_written] == [
        (None, date(2026, 11, 1)),
        (SUITE, date(2026, 6, 1)),
    ]


def test_status_needs_the_rooms_of_the_booked_room_types_only(service, repo):
    assert service.status(ACCOUNT_ID, PROPERTY_ID)["inventory_complete"] is False  # no bookings: every type

    preview = upload(service, export("1,2026-07-01,2026-07-04,,ok,1,2,300,0,gr,Double Room"))
    result = service.commit(ACCOUNT_ID, PROPERTY_ID, preview["id"], skip_errors=False)
    status = result["status"]

    assert status["has_data"] is True and status["bookings"] == 1
    assert status["inventory_complete"] is True  # the suite has no rooms but no bookings either
    assert status["last_import"]["bookings_imported"] == 1
    assert status["seasons"] == [2026]
    assert status["room_types"] == [
        {"id": DOUBLE, "label": "Double Room", "rooms": 5},
        {"id": SUITE, "label": "Junior Suite", "rooms": None},
    ]
