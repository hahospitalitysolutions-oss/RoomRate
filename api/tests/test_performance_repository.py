"""PerformanceRepository against a scripted engine: the statements and what they are given.

The same statements ran against PostgreSQL 16 (scripts/seed_demo_performance.py
on a migrated database): nights add up to each booking to the cent, and a
second run updates instead of duplicating.
"""

from datetime import date
from uuid import UUID

from api.repositories.performance_repository import DEFAULT_REVENUE_SETTINGS, PerformanceRepository
from api.tests._fakes import FakeConnection, FakeEngine, FakeResult

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
ROOM_TYPE_ID = UUID("00000000-0000-0000-0000-0000000000a1")


def repository(results=None):
    engine = FakeEngine(FakeConnection(results))
    roles = []

    def factory(role):
        roles.append(role)
        return engine

    return PerformanceRepository(engine_factory=factory), engine.connection, roles


def booking(ref, **overrides):
    return {
        "booking_ref": ref, "room_label": "Double", "room_type_id": ROOM_TYPE_ID, "channel": "Booking.com",
        "status": "confirmed", "booked_on": date(2026, 3, 1), "cancelled_on": None, "arrival": date(2026, 7, 1),
        "departure": date(2026, 7, 4), "rooms": 1, "adults": 2, "children": 0, "gross_eur": 300.0,
        "commission_eur": 51.0, "country_code": "DE", **overrides,
    }


def test_save_bookings_writes_the_batch_in_one_statement_and_rebuilds_the_nights():
    written = FakeResult([{"id": "b-1", "inserted": True}, {"id": "b-2", "inserted": False}])
    repo, connection, roles = repository([written, FakeResult(), FakeResult()])

    result = repo.save_bookings(ACCOUNT_ID, PROPERTY_ID, [booking("1"), booking("2", gross_eur=99.0)])

    assert result == {"inserted": 1, "updated": 1}
    assert roles == ["writer"]
    upsert, params = connection.calls[0]
    assert "ON CONFLICT (owned_property_id, booking_ref) DO UPDATE" in upsert
    assert "WHERE roomrate_performance_bookings.account_id = :account_id" in upsert
    # A room type of another property is stored as none.
    assert "LEFT JOIN roomrate_owned_property_room_types rt" in upsert
    assert "rt.owned_property_id = :owned_property_id" in upsert
    assert params["booking_ref"] == ["1", "2"]
    assert params["room_type_id"] == [str(ROOM_TYPE_ID), str(ROOM_TYPE_ID)]
    assert params["gross_eur"] == [300.0, 99.0]
    assert (params["account_id"], params["owned_property_id"]) == (ACCOUNT_ID, PROPERTY_ID)
    delete, delete_params = connection.calls[1]
    assert "DELETE FROM roomrate_performance_booking_nights" in delete and delete_params == {"ids": ["b-1", "b-2"]}
    nights, _ = connection.calls[2]
    assert "trunc(b.gross_eur / (b.departure - b.arrival), 2)" in nights
    assert "b.status = 'confirmed'" in nights


def test_save_bookings_keeps_the_last_row_of_a_repeated_reference():
    repo, connection, _ = repository([FakeResult([{"id": "b-1", "inserted": True}]), FakeResult(), FakeResult()])

    repo.save_bookings(ACCOUNT_ID, PROPERTY_ID, [booking("7", gross_eur=100.0), booking("7", gross_eur=120.0)])

    assert connection.calls[0][1]["gross_eur"] == [120.0]


def test_save_bookings_of_nothing_touches_nothing():
    repo, connection, roles = repository([])

    assert repo.save_bookings(ACCOUNT_ID, PROPERTY_ID, []) == {"inserted": 0, "updated": 0}
    assert connection.calls == [] and roles == []


def test_save_inventory_replaces_and_stores_only_known_counts():
    repo, connection, _ = repository([FakeResult(), FakeResult()])

    repo.save_inventory(
        ACCOUNT_ID,
        PROPERTY_ID,
        [{"room_type_id": ROOM_TYPE_ID, "rooms": 5}, {"room_type_id": UUID(int=9), "rooms": None}],
    )

    assert "DELETE FROM roomrate_performance_room_inventory" in connection.calls[0][0]
    insert, params = connection.calls[1]
    assert "rt.owned_property_id = :owned_property_id" in insert
    assert params["room_type_ids"] == [str(ROOM_TYPE_ID)] and params["rooms"] == [5]


def test_save_inventory_with_no_counts_only_clears():
    repo, connection, _ = repository([FakeResult()])

    repo.save_inventory(ACCOUNT_ID, PROPERTY_ID, [{"room_type_id": ROOM_TYPE_ID, "rooms": None}])

    assert len(connection.calls) == 1


def test_save_closures_keeps_whole_property_rows():
    repo, connection, _ = repository([FakeResult(), FakeResult()])

    repo.save_closures(
        ACCOUNT_ID,
        PROPERTY_ID,
        [{"date_from": date(2026, 11, 1), "date_to": date(2027, 4, 30), "room_type_id": None, "reason": "Εκτός σεζόν"}],
    )

    insert, params = connection.calls[1]
    assert "r.room_type_id IS NULL OR EXISTS" in insert
    assert params["room_type_ids"] == [None] and params["rooms"] == [None]


def test_revenue_settings_default_without_a_row():
    repo, _, roles = repository([FakeResult([])])

    assert repo.revenue_settings(ACCOUNT_ID, PROPERTY_ID) == DEFAULT_REVENUE_SETTINGS
    assert roles == ["api"]


def test_label_mappings_store_mapped_or_ignored_labels_once():
    repo, connection, _ = repository([FakeResult()])

    repo.save_label_mappings(
        ACCOUNT_ID,
        PROPERTY_ID,
        [
            {"label": "Double", "room_type_id": UUID(int=1)},
            {"label": "Double", "room_type_id": ROOM_TYPE_ID},
            {"label": "Twin", "ignored": True},
            {"label": "Unmapped", "room_type_id": None},
        ],
    )

    _, params = connection.calls[0]
    assert params["labels"] == ["Double", "Twin"]
    assert params["room_type_ids"] == [str(ROOM_TYPE_ID), None]
    assert params["ignored"] == [False, True]


def test_delete_bookings_also_clears_the_upload_history():
    deleted = FakeResult(rowcount=12)
    repo, connection, _ = repository([deleted, FakeResult()])

    assert repo.delete_bookings(ACCOUNT_ID, PROPERTY_ID) == 12
    assert "DELETE FROM roomrate_performance_bookings" in connection.calls[0][0]
    assert "DELETE FROM roomrate_performance_imports" in connection.calls[1][0]
