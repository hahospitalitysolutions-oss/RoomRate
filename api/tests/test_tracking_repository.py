"""Unit tests for TrackingRepository's unnest-based competitor upsert.

Fake-connection style (no live DB): the whole selection must be written in one
multi-row upsert round trip instead of one INSERT per competitor.
"""

from __future__ import annotations

from uuid import UUID

from api.repositories.tracking_repository import TrackingRepository
from api.schemas.tracking import TrackedCompetitorSetRequest
from api.tests._fakes import FakeConnection, FakeResult, install_scripted_engine


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
PROP_A = UUID("00000000-0000-0000-0000-0000000000a1")
PROP_B = UUID("00000000-0000-0000-0000-0000000000b2")
PKG_1 = UUID("00000000-0000-0000-0000-0000000000c1")
PKG_2 = UUID("00000000-0000-0000-0000-0000000000c2")


def _patch_engine(monkeypatch, results: list[FakeResult] | None = None) -> FakeConnection:
    # Unscripted (results=None): these tests ignore result values and only
    # assert on the recorded connection.calls.
    engine = install_scripted_engine(
        monkeypatch,
        "api.repositories.tracking_repository",
        results=results,
    )
    return engine.connection


def _request(competitors) -> TrackedCompetitorSetRequest:
    return TrackedCompetitorSetRequest(
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="double",
        competitors=competitors,
    )


def test_add_tracked_competitors_uses_one_unnest_upsert(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    saved = repository.add_tracked_competitors(
        ACCOUNT_ID,
        _request(
            [
                {"property_id": PROP_A, "room_package_id": PKG_1},
                {"property_id": PROP_B, "room_package_id": None},
            ]
        ),
    )

    assert saved == 2
    # ONE round trip for the whole batch, not one INSERT per competitor.
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "INSERT INTO roomrate_tracked_competitors" in sql
    assert "unnest" in sql
    assert "ON CONFLICT" in sql
    assert "competitor_room_package_id = EXCLUDED.competitor_room_package_id" in sql
    assert "is_active = true" in sql
    assert params["competitor_property_ids"] == [PROP_A, PROP_B]
    assert params["competitor_room_package_ids"] == [PKG_1, None]
    assert len(params["ids"]) == 2
    assert params["account_id"] == ACCOUNT_ID
    assert params["owned_property_id"] == OWNED_PROPERTY_ID
    assert params["room_type_category"] == "double"


def test_replace_tracked_competitors_deactivates_then_upserts_once(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    saved = repository.replace_tracked_competitors(
        ACCOUNT_ID, _request([{"property_id": PROP_A}])
    )

    assert saved == 1
    # Deactivate sweep + the single batch upsert.
    assert len(connection.calls) == 2
    deactivate_sql, deactivate_params = connection.calls[0]
    assert "SET is_active = false" in deactivate_sql
    assert "room_type_category = :room_type_category" in deactivate_sql
    assert "room_type_category IN" not in deactivate_sql
    assert deactivate_params["room_type_category"] == "double"
    assert "unnest" in connection.calls[1][0]


def test_duplicate_property_ids_collapse_to_last_occurrence(monkeypatch):
    # A multi-row upsert may not hit the same conflict target twice; the last
    # occurrence must win, matching the old per-row loop's final state.
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    saved = repository.add_tracked_competitors(
        ACCOUNT_ID,
        _request(
            [
                {"property_id": PROP_A, "room_package_id": PKG_1},
                {"property_id": PROP_A, "room_package_id": PKG_2},
            ]
        ),
    )

    # The response still counts the request size, like the old loop did.
    assert saved == 2
    _, params = connection.calls[0]
    assert params["competitor_property_ids"] == [PROP_A]
    assert params["competitor_room_package_ids"] == [PKG_2]


def test_empty_selection_skips_the_upsert(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    saved = repository.add_tracked_competitors(ACCOUNT_ID, _request([]))

    assert saved == 0
    assert connection.calls == []


def test_list_tracked_competitors_reads_the_comparable_category_pool(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    repository.list_tracked_competitors(
        account_id=ACCOUNT_ID,
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="double",
    )

    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "room_type_category IN (:room_type_category_0, :room_type_category_1)" in sql
    assert params["room_type_category_0"] == "double"
    assert params["room_type_category_1"] == "twin"
    assert "room_type_category" not in params
    assert "PARTITION BY competitor_property_id" in sql
    assert "WHERE rn = 1" in sql


def test_list_tracked_competitors_reads_double_tracking_from_twin_side(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    repository.list_tracked_competitors(
        account_id=ACCOUNT_ID,
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="twin",
    )

    _, params = connection.calls[0]
    assert {params["room_type_category_0"], params["room_type_category_1"]} == {
        "double",
        "twin",
    }


def test_list_tracked_competitors_keeps_other_categories_isolated(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    repository.list_tracked_competitors(
        account_id=ACCOUNT_ID,
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="suite",
    )

    sql, params = connection.calls[0]
    assert "room_type_category IN (:room_type_category_0)" in sql
    assert params["room_type_category_0"] == "suite"
    assert "room_type_category_1" not in params


def test_list_tracked_competitors_skips_query_for_blank_category(monkeypatch):
    connection = _patch_engine(monkeypatch)
    repository = TrackingRepository()

    rows = repository.list_tracked_competitors(
        account_id=ACCOUNT_ID,
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="   ",
    )

    assert rows == []
    assert connection.calls == []


def test_list_tracked_competitors_defensively_deduplicates_pooled_rows(monkeypatch):
    connection = _patch_engine(
        monkeypatch,
        results=[
            FakeResult(
                rows=[
                    {"property_id": PROP_A, "room_package_id": PKG_2},
                    {"property_id": PROP_A, "room_package_id": PKG_1},
                    {"property_id": PROP_B, "room_package_id": None},
                ]
            )
        ],
    )
    repository = TrackingRepository()

    rows = repository.list_tracked_competitors(
        account_id=ACCOUNT_ID,
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="double",
    )

    assert rows == [
        {"property_id": PROP_A, "room_package_id": PKG_2},
        {"property_id": PROP_B, "room_package_id": None},
    ]
    assert len(connection.calls) == 1
