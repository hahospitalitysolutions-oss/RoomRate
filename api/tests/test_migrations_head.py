"""Alembic head + ORM column checks for the newest migration.

ScriptDirectory reads the version files only (env.py never runs), so this
needs no DATABASE_URL. CI enforces a single head; this pins the same rule
locally so a parallel stream's migration cannot fork the history unnoticed.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from api.models.market import (
    RoomRateAgentLease,
    RoomRateDemandRun,
    RoomRateDemandSettings,
    RoomRateFerryDestinationProfile,
    RoomRateFerryObservation,
    RoomRateFerryRoute,
    RoomRateFlightObservation,
    RoomRateAgentRun,
    RoomRateRateLimitHit,
    RoomRateScheduleConfig,
    RoomRateProperty,
    RoomRateRoomMatch,
    RoomRateRoomPackage,
    RoomRateScrapeJob,
)
from api.models.performance import (
    RoomRatePerformanceBooking,
    RoomRatePerformanceBookingNight,
    RoomRatePerformanceImportRow,
    RoomRatePerformanceLabelMapping,
    RoomRatePerformanceRevenueSettings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUND6_REVISION = "20260915_0024"
RATE_PLANS_REVISION = "20260929_0025"
AGENT_MATCHING_REVISION = "20260930_0026"
COMPARABLE_REVISION = "20260930_0027"
AGENT_LEASE_REVISION = "20260930_0028"
RATE_LIMIT_REVISION = "20260930_0029"
SCHEDULE_LOCAL_HOUR_REVISION = "20260930_0030"
FERRY_DEMAND_REVISION = "20261011_0031"
FERRY_OPTIONS_REVISION = "20261011_0032"
FLIGHT_OBSERVATIONS_REVISION = "20261011_0033"
DEMAND_SETTINGS_REVISION = "20261012_0034"
PERFORMANCE_REVISION = "20261020_0035"


def _script_directory() -> ScriptDirectory:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_performance_migration_is_the_single_alembic_head():
    assert _script_directory().get_heads() == [PERFORMANCE_REVISION]


def test_performance_migration_revises_the_demand_settings_migration():
    assert _script_directory().get_revision(PERFORMANCE_REVISION).down_revision == DEMAND_SETTINGS_REVISION


def test_orm_declares_the_performance_tables():
    """One booking per reference and property, its nights, labels remembered, deleted with the property."""
    bookings = RoomRatePerformanceBooking.__table__
    nights = RoomRatePerformanceBookingNight.__table__
    mappings = RoomRatePerformanceLabelMapping.__table__

    assert "uq_roomrate_performance_bookings_ref" in {c.name for c in bookings.constraints}
    foreign = {fk.target_fullname: fk.ondelete for fk in bookings.foreign_keys}
    assert foreign == {
        "roomrate_accounts.id": "CASCADE",
        "roomrate_owned_properties.id": "CASCADE",
        "roomrate_performance_imports.id": "SET NULL",
        "roomrate_owned_property_room_types.id": "SET NULL",
    }
    # No guest's name, email, phone or address can be stored.
    assert not {"guest_name", "email", "phone", "address"} & set(bookings.columns.keys())
    assert [column.name for column in nights.primary_key] == ["booking_id", "stay_date"]
    assert {index.name for index in nights.indexes} == {"ix_roomrate_performance_booking_nights_property_date"}
    assert [column.name for column in mappings.primary_key] == ["owned_property_id", "label"]
    assert "ck_roomrate_performance_label_mappings_target" in {c.name for c in mappings.constraints}
    assert [column.name for column in RoomRatePerformanceImportRow.__table__.primary_key] == ["import_id", "row_number"]
    assert RoomRatePerformanceRevenueSettings.__table__.columns["vat_pct"].server_default.arg == "13"


def test_demand_settings_migration_revises_the_flight_observations_migration():
    assert _script_directory().get_revision(DEMAND_SETTINGS_REVISION).down_revision == FLIGHT_OBSERVATIONS_REVISION


def test_orm_declares_the_demand_settings_and_run_tables():
    """Per owned property (deleted with it); one run per kind, scope and Greek day."""
    settings_table = RoomRateDemandSettings.__table__
    runs = RoomRateDemandRun.__table__

    assert [column.name for column in settings_table.primary_key] == ["owned_property_id"]
    foreign = {fk.target_fullname: fk.ondelete for fk in settings_table.foreign_keys}
    assert foreign == {"roomrate_owned_properties.id": "CASCADE", "roomrate_accounts.id": "CASCADE"}
    assert settings_table.columns["hour_local"].server_default.arg == "8"
    assert "ck_roomrate_demand_settings_hour_local" in {c.name for c in settings_table.constraints}
    assert [column.name for column in runs.primary_key] == ["kind", "scope", "run_on"]
    assert "ck_roomrate_demand_runs_kind" in {c.name for c in runs.constraints}


def test_flight_observations_migration_revises_the_ferry_options_migration():
    revision = _script_directory().get_revision(FLIGHT_OBSERVATIONS_REVISION)

    assert revision.down_revision == FERRY_OPTIONS_REVISION


def test_orm_declares_the_flight_observations_table():
    """One look per airport, travel date, window and day; calls counted for the unit budget."""
    table = RoomRateFlightObservation.__table__

    assert table.name == "roomrate_flight_observations"
    assert table.columns["airport_iata"].type.length == 3
    assert table.columns["calls"].server_default.arg == "1"
    assert table.columns["units_remaining"].nullable is True
    constraints = {constraint.name for constraint in table.constraints}
    assert {
        "uq_roomrate_flight_observations_window_day",
        "ck_roomrate_flight_observations_window",
    } <= constraints
    assert {index.name for index in table.indexes} == {"ix_roomrate_flight_observations_observed_at"}


def test_ferry_options_migration_revises_the_ferry_demand_migration():
    revision = _script_directory().get_revision(FERRY_OPTIONS_REVISION)

    assert revision.down_revision == FERRY_DEMAND_REVISION
    assert RoomRateFerryObservation.__table__.columns["accommodation_options"].nullable is True


def test_ferry_demand_migration_revises_the_schedule_local_hour_migration():
    revision = _script_directory().get_revision(FERRY_DEMAND_REVISION)

    assert revision.down_revision == SCHEDULE_LOCAL_HOUR_REVISION


def test_orm_declares_the_ferry_demand_tables():
    """Destination-level profile, its inbound routes, one observation per route-day."""
    profile = RoomRateFerryDestinationProfile.__table__
    route = RoomRateFerryRoute.__table__
    observation = RoomRateFerryObservation.__table__

    assert [column.name for column in profile.primary_key] == ["canonical_destination"]
    assert {constraint.name for constraint in route.constraints} >= {
        "uq_roomrate_ferry_routes_destination_ports",
        "ck_roomrate_ferry_routes_status",
    }
    assert route.columns["status"].server_default.arg == "auto"
    foreign = {fk.target_fullname: fk.ondelete for fk in observation.foreign_keys}
    assert foreign == {"roomrate_ferry_routes.id": "CASCADE"}
    assert "uq_roomrate_ferry_observations_route_day" in {c.name for c in observation.constraints}
    assert {index.name for index in observation.indexes} == {"ix_roomrate_ferry_observations_observed_on"}


def test_schedule_local_hour_migration_revises_the_rate_limit_migration():
    revision = _script_directory().get_revision(SCHEDULE_LOCAL_HOUR_REVISION)

    assert revision.down_revision == RATE_LIMIT_REVISION


def test_orm_declares_the_schedule_local_hour_next_to_the_legacy_utc_hour():
    """Additive: hour_utc stays for older images; the scheduler reads the local pair."""
    table = RoomRateScheduleConfig.__table__

    assert {"hour_local", "timezone", "hour_utc"} <= set(table.columns.keys())
    assert table.columns["hour_local"].server_default.arg == "8"
    assert table.columns["timezone"].server_default.arg == "Europe/Athens"
    checks = {constraint.name for constraint in table.constraints}
    assert "ck_roomrate_schedule_configs_hour_local" in checks


def test_rate_limit_migration_revises_the_agent_lease_migration():
    revision = _script_directory().get_revision(RATE_LIMIT_REVISION)

    assert revision.down_revision == AGENT_LEASE_REVISION


def test_orm_declares_the_shared_rate_limit_table():
    """Hashed caller keys only, indexed for the per-key window and the sweep."""
    table = RoomRateRateLimitHit.__table__

    assert table.name == "roomrate_rate_limit_hits"
    assert table.columns["key_hash"].type.length == 64
    assert table.columns["key_hash"].nullable is False
    assert table.columns["hit_at"].nullable is False
    indexes = {index.name: [column.name for column in index.columns] for index in table.indexes}
    assert indexes == {
        "ix_roomrate_rate_limit_hits_key_hit": ["key_hash", "hit_at"],
        "ix_roomrate_rate_limit_hits_hit_at": ["hit_at"],
    }


def test_agent_lease_migration_revises_the_comparable_migration():
    revision = _script_directory().get_revision(AGENT_LEASE_REVISION)

    assert revision.down_revision == COMPARABLE_REVISION


def test_orm_declares_the_agent_lease_table():
    """One lease per (kind, job, owned room): the primary key IS the scope."""
    table = RoomRateAgentLease.__table__

    assert table.name == "roomrate_agent_leases"
    assert [column.name for column in table.primary_key.columns] == [
        "kind",
        "scrape_job_id",
        "owned_room_type_id",
    ]
    for name in ("account_id", "holder", "acquired_at"):
        assert table.columns[name].nullable is False


def test_comparable_migration_revises_the_agent_matching_migration():
    revision = _script_directory().get_revision(COMPARABLE_REVISION)

    assert revision.down_revision == AGENT_MATCHING_REVISION
    assert RoomRateRoomMatch.__table__.columns["comparable"].nullable is True


def test_agent_matching_migration_revises_the_rate_plans_migration():
    revision = _script_directory().get_revision(AGENT_MATCHING_REVISION)

    assert revision.down_revision == RATE_PLANS_REVISION


def test_rate_plans_migration_revises_the_round6_migration():
    revision = _script_directory().get_revision(RATE_PLANS_REVISION)

    assert revision.down_revision == ROUND6_REVISION


def test_round6_migration_revises_the_result_summary_migration():
    revision = _script_directory().get_revision(ROUND6_REVISION)

    assert revision.down_revision == "20260828_0023"


def test_orm_models_declare_the_round6_columns():
    job_columns = RoomRateScrapeJob.__table__.columns
    property_columns = RoomRateProperty.__table__.columns

    assert job_columns["nearby_destinations"].nullable is True
    assert job_columns["radius_km"].nullable is True
    assert (job_columns["radius_km"].type.precision, job_columns["radius_km"].type.scale) == (5, 1)
    assert property_columns["booking_url"].nullable is True


def test_orm_room_package_declares_the_rate_plan_columns():
    """Spec 2026-09-29 §3: additive, nullable, exact names and types."""
    columns = RoomRateRoomPackage.__table__.columns

    expected = {
        "discounted_price_per_night_eur": (10, 2),
        "discount_pct": (5, 1),
    }
    for name, (precision, scale) in expected.items():
        assert columns[name].nullable is True
        assert (columns[name].type.precision, columns[name].type.scale) == (precision, scale)

    expected_lengths = {
        "discount_label": 40,
        "cancellation_type": 40,
        "payment_label": 60,
        "rate_block_id": 80,
    }
    for name, length in expected_lengths.items():
        assert columns[name].nullable is True
        assert columns[name].type.length == length

    assert columns["has_genius_discount"].nullable is True


def test_orm_declares_the_room_match_table():
    """Spec 2026-09-29 Α.3: exact columns/types + the replace-upsert identity."""
    table = RoomRateRoomMatch.__table__

    assert table.name == "roomrate_room_matches"
    columns = table.columns
    for name in (
        "account_id",
        "scrape_job_id",
        "owned_room_type_id",
        "property_id",
        "room_type",
        "score",
        "category_match",
    ):
        assert columns[name].nullable is False
    assert (columns["score"].type.precision, columns["score"].type.scale) == (5, 1)
    assert columns["category_match"].type.length == 10
    assert columns["model_version"].type.length == 60
    assert columns["reasoning"].nullable is True

    unique_columns = [
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("scrape_job_id", "owned_room_type_id", "property_id", "room_type") in unique_columns


def test_orm_declares_the_agent_run_audit_table():
    """Spec 2026-09-29 Α.3: the light per-run audit row (why no AI estimate)."""
    table = RoomRateAgentRun.__table__

    assert table.name == "roomrate_agent_runs"
    columns = table.columns
    assert columns["kind"].nullable is False
    assert columns["status"].nullable is False
    assert columns["model"].type.length == 60
    # The audit must survive job deletion, so the FK is SET NULL + nullable.
    assert columns["scrape_job_id"].nullable is True
    assert columns["error_message"].nullable is True
