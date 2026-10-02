import sqlite3
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from api.repositories.price_history_repository import PriceHistoryRepository
from api.services.price_statistics_service import basis_price_rows, compute_price_statistics
from api.tests._fakes import EchoConnection as FakeConnection
from api.tests._fakes import EchoConnectionContext as FakeConnectionContext
from api.tests._fakes import EchoResult


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _repository(connection: FakeConnection) -> PriceHistoryRepository:
    return PriceHistoryRepository(lambda: FakeConnectionContext(connection))


def test_fetch_price_series_ranks_completed_runs_for_one_market():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(
        account_id=ACCOUNT_ID,
        canonical_destination="faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit_runs=30,
    )

    sql = connection.executed_sql
    assert "roomrate_scrape_runs" in sql
    # Runs sharing a finished_at are fragments of one job: one logical run.
    assert "dense_rank() OVER (ORDER BY sr.finished_at DESC)" in sql
    assert "row_number()" not in sql
    assert "sr.status = 'completed'" in sql
    # Each run still folds to the cheapest offer per property (now split by
    # category scope), over the EFFECTIVE price (discounted, else base).
    assert (
        "MIN(CASE WHEN 1 = 1 THEN COALESCE(rp.discounted_price_per_night_eur,"
        " rp.price_per_night_eur) END) AS min_price"
    ) in sql
    assert "ORDER BY" in sql
    # Direct column comparisons keep ix_roomrate_scrape_runs_history usable:
    # canonical_destination is NOT NULL and completed runs always have
    # finished_at, so COALESCE would only defeat the index.
    assert "COALESCE(sr." not in sql
    assert "sr.canonical_destination = :canonical_destination" in sql
    assert "sr.finished_at AS observed_at" in sql
    params = connection.executed_params
    assert params["account_id"] == str(ACCOUNT_ID)
    assert params["canonical_destination"] == "faliraki"
    assert params["adults"] == 2
    assert params["limit_runs"] == 30
    assert not any(key.startswith("room_type_category_") for key in params)
    # No property filter requested → no array clause in the SQL.
    assert "property_ids" not in sql


def test_fetch_price_series_can_filter_comparable_category_pool_and_properties():
    connection = FakeConnection()
    property_ids = [UUID("00000000-0000-0000-0000-00000000aaa1")]

    _repository(connection).fetch_price_series(
        account_id=ACCOUNT_ID,
        canonical_destination="faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        room_type_category="double",
        property_ids=property_ids,
    )

    sql = connection.executed_sql
    assert "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)" in sql
    assert "ro.property_id = ANY(CAST(:property_ids AS uuid[]))" in sql
    assert connection.executed_params["room_type_category_0"] == "double"
    assert connection.executed_params["room_type_category_1"] == "twin"
    assert connection.executed_params["property_ids"] == [str(property_ids[0])]


def test_fetch_price_series_reads_double_pool_from_twin_side_too():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(
        account_id=ACCOUNT_ID,
        canonical_destination="faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        room_type_category="twin",
    )

    assert "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert connection.executed_params["room_type_category_0"] == "double"
    assert connection.executed_params["room_type_category_1"] == "twin"


def test_fetch_price_series_keeps_non_pooled_category_as_singleton():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(
        account_id=ACCOUNT_ID,
        canonical_destination="faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        room_type_category="suite",
    )

    assert "rp.room_type_category IN (:room_type_category_0)" in connection.executed_sql
    assert connection.executed_params["room_type_category_0"] == "suite"
    assert "room_type_category_1" not in connection.executed_params


def test_fetch_latest_vs_previous_compares_two_most_recent_runs():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(
        account_id=ACCOUNT_ID,
        canonical_destination="faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
    )

    sql = connection.executed_sql
    assert "cur.rn = 1" in sql
    assert "prev.rn = 2" in sql
    assert "current_price" in sql
    assert "previous_price" in sql
    # Only the two latest runs are needed for the comparison.
    assert connection.executed_params["limit_runs"] == 2


def test_fetch_latest_vs_previous_uses_pool_for_alert_evaluation():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(
        account_id=ACCOUNT_ID,
        canonical_destination="faliraki",
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        room_type_category="twin",
    )

    assert "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert connection.executed_params["room_type_category_0"] == "double"
    assert connection.executed_params["room_type_category_1"] == "twin"


# ---------------------------------------------------------------------------
# Round 6 — own-hotel exclusion, same/similar minimums, run id, own live price
# ---------------------------------------------------------------------------


class _RowsResult(EchoResult):
    """Echo result that also hands back scripted rows."""

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _RowsConnection(FakeConnection):
    """Records SQL like EchoConnection and returns the scripted rows."""

    def __init__(self, rows: list[dict]):
        super().__init__()
        self.rows = rows

    def execute(self, sql, params):
        super().execute(sql, params)
        return _RowsResult(self.rows)


MARKET_KEY = {
    "account_id": ACCOUNT_ID,
    "canonical_destination": "faliraki",
    "check_in": date(2026, 7, 1),
    "check_out": date(2026, 7, 5),
    "adults": 2,
    "children": 0,
    "rooms": 1,
}


def test_price_rows_name_hotels_by_their_booking_display_name():
    for fetch in ("fetch_price_series", "fetch_latest_vs_previous"):
        connection = FakeConnection()

        getattr(_repository(connection), fetch)(**MARKET_KEY, room_type_category="double")

        sql = connection.executed_sql
        # «Evita Resort» as Booking shows it, not the canonical «evita resort».
        assert "p.display_name AS hotel_name" in sql
        assert "p.canonical_name AS hotel_name" not in sql
        # The name is grouped next to its property id, so one property never
        # splits into two series.
        assert "GROUP BY rr.rn, rr.observed_at, ro.property_id, p.display_name" in sql


def test_prices_cte_excludes_the_accounts_own_properties():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY, room_type_category="double")

    sql = connection.executed_sql
    assert "NOT EXISTS" in sql
    assert "FROM roomrate_owned_properties op" in sql
    assert "op.account_id = :account_id" in sql
    assert "op.is_active = true" in sql
    # Either spelling of the property name identifies the owner's own row.
    assert "lower(trim(op.display_name)) IN (" in sql
    assert "lower(trim(p.display_name))" in sql
    assert "lower(trim(p.canonical_name))" in sql


def test_fetch_latest_vs_previous_excludes_the_accounts_own_properties_too():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY, room_type_category="double")

    assert "FROM roomrate_owned_properties op" in connection.executed_sql


def test_fetch_price_series_returns_same_and_similar_minimums():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY, room_type_category="double")

    sql = connection.executed_sql
    assert "AS min_price_same" in sql
    assert "AS min_price_similar" in sql
    # The legacy column keeps its meaning (same-category minimum) for market.py and alerts.
    assert "AS min_price" in sql
    assert "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)" in sql
    assert "IS DISTINCT FROM 'single'" in sql
    # Without opting in, similar-only hotels are not returned (default behaviour).
    assert "WHERE min_price_same IS NOT NULL" in sql
    assert connection.executed_params["room_type_category_0"] == "double"
    assert connection.executed_params["room_type_category_1"] == "twin"


def test_fetch_price_series_include_similar_keeps_similar_only_hotels():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(
        **MARKET_KEY, room_type_category="double", include_similar=True
    )

    assert "(min_price_same IS NOT NULL OR min_price_similar IS NOT NULL)" in connection.executed_sql


def test_fetch_price_series_without_category_treats_every_package_as_same():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY)

    sql = connection.executed_sql
    assert (
        "CASE WHEN 1 = 1 THEN COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur) END"
    ) in sql
    assert not any(key.startswith("room_type_category_") for key in connection.executed_params)


# ---------------------------------------------------------------------------
# Rate plans (spec 2026-09-29 §4) — like-for-like minimum per cancellation class
# ---------------------------------------------------------------------------


def test_fetch_price_series_scopes_the_minimums_to_the_own_cancellation_class():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(
        **MARKET_KEY, room_type_category="double", cancellation_type="non_refundable"
    )

    sql = connection.executed_sql
    # Per hotel: min inside the own class FIRST (NULL/unknown always
    # participates), else that hotel's overall min as today.
    assert "(rp.cancellation_type IS NULL OR rp.cancellation_type = :own_cancellation_class)" in sql
    assert "COALESCE(MIN(CASE WHEN" in sql
    assert connection.executed_params["own_cancellation_class"] == "non_refundable"
    # Review fix: ONE matched flag per basis class, each scoped to exactly the
    # rows that can enter that basis, so _stats_scope can decide AFTER the
    # widening decision which flag counts.
    pool = "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)"
    assert (
        f"(MAX(CASE WHEN {pool} AND rp.cancellation_type = :own_cancellation_class"
        " THEN 1 ELSE 0 END) = 1) AS cancellation_matched_same"
    ) in sql
    assert (
        f"(MAX(CASE WHEN NOT ({pool}) AND rp.room_type_category IS DISTINCT FROM 'single'"
        " AND rp.cancellation_type = :own_cancellation_class"
        " THEN 1 ELSE 0 END) = 1) AS cancellation_matched_similar"
    ) in sql


def test_fetch_price_series_without_a_cancellation_class_keeps_todays_minimums():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY, room_type_category="double")

    sql = connection.executed_sql
    assert "own_cancellation_class" not in connection.executed_params
    assert "COALESCE(MIN(" not in sql
    assert "FALSE AS cancellation_matched_same" in sql
    assert "FALSE AS cancellation_matched_similar" in sql


def test_fetch_latest_vs_previous_never_scopes_by_cancellation_class():
    """Alerts have no own reference package: their minimums stay as today."""
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY, room_type_category="double")

    assert "own_cancellation_class" not in connection.executed_params
    assert "COALESCE(MIN(" not in connection.executed_sql


def test_fetch_latest_vs_previous_default_keeps_the_immediately_previous_run():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY)

    sql = connection.executed_sql
    assert "prev.rn = 2" in sql
    assert "min_gap_hours" not in connection.executed_params
    assert connection.executed_params["limit_runs"] == 2


def test_fetch_latest_vs_previous_with_min_gap_picks_a_run_at_least_that_old():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY, min_gap_hours=12)

    sql = connection.executed_sql
    assert "prev.rn = 2" not in sql
    assert "older.rn > 1" in sql
    assert "(:min_gap_hours * interval '1 hour')" in sql
    assert "AS previous_observed_at" in sql
    assert connection.executed_params["min_gap_hours"] == 12.0
    # Back-to-back scrapes must not push the 12-hour-old run out of the window.
    assert connection.executed_params["limit_runs"] > 2


def test_fetch_latest_completed_run_id_returns_none_without_runs():
    connection = FakeConnection()

    run_id = _repository(connection).fetch_latest_completed_run_id(**MARKET_KEY)

    assert run_id is None
    sql = connection.executed_sql
    assert "FROM roomrate_scrape_runs sr" in sql
    assert "sr.status = 'completed'" in sql
    assert "ORDER BY sr.finished_at DESC, sr.id DESC" in sql
    assert "LIMIT 1" in sql
    params = connection.executed_params
    assert params["account_id"] == str(ACCOUNT_ID)
    assert params["canonical_destination"] == "faliraki"
    assert params["check_in"] == "2026-07-01"
    assert params["rooms"] == 1


def test_fetch_latest_completed_run_id_returns_the_id_as_a_string():
    run_id = UUID("00000000-0000-0000-0000-00000000f00d")
    connection = _RowsConnection([{"id": run_id}])

    assert _repository(connection).fetch_latest_completed_run_id(**MARKET_KEY) == str(run_id)


def test_fetch_own_live_price_matches_the_display_name_in_the_latest_run():
    connection = FakeConnection()

    reference = _repository(connection).fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="  Rea Hotel "
    )

    assert reference is None  # the echo connection returns no rows
    sql = connection.executed_sql
    assert "WITH latest_runs AS" in sql
    assert "sr.status = 'completed'" in sql
    assert "lower(trim(:display_name)) IN (" in sql
    assert "lower(trim(p.display_name))" in sql
    assert "lower(trim(p.canonical_name))" in sql
    # The reference PACKAGE, not an aggregate: cheapest in the comparable
    # pool first, then any category, so its cancellation_type rides along.
    assert "rp.cancellation_type" in sql
    assert "CASE WHEN rp.room_type_category IN (:room_type_category_0, :room_type_category_1) THEN 0 ELSE 1 END AS pool_rank" in sql
    # The EFFECTIVE price, like-for-like with the competitors' minimums.
    assert (
        "COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur) AS price" in sql
    )
    assert (
        "ORDER BY pool_rank ASC,"
        " COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur) ASC"
    ) in sql
    assert "LIMIT 1" in sql
    params = connection.executed_params
    assert params["display_name"] == "Rea Hotel"
    assert params["room_type_category_0"] == "double"
    assert params["canonical_destination"] == "faliraki"


def test_fetch_own_live_price_returns_the_reference_package_price_and_class():
    connection = _RowsConnection(
        [{"price": Decimal("92.00"), "cancellation_type": "non_refundable", "pool_rank": 0}]
    )

    reference = _repository(connection).fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
    )

    assert reference == {"price": 92.0, "cancellation_type": "non_refundable"}


def test_fetch_own_live_price_maps_an_unknown_class_to_none():
    connection = _RowsConnection([{"price": Decimal("70.00"), "cancellation_type": None, "pool_rank": 1}])

    reference = _repository(connection).fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
    )

    assert reference == {"price": 70.0, "cancellation_type": None}


def test_fetch_own_live_price_returns_none_when_the_hotel_is_absent_from_the_run():
    connection = _RowsConnection([])

    assert (
        _repository(connection).fetch_own_live_price(
            **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
        )
        is None
    )


def test_fetch_own_live_price_skips_the_query_without_a_display_name():
    connection = FakeConnection()

    assert (
        _repository(connection).fetch_own_live_price(
            **MARKET_KEY, room_type_category="double", display_name="   "
        )
        is None
    )
    assert connection.executed_sql == ""


# ---------------------------------------------------------------------------
# Fragments: one job can write one run per Booking city, all sharing a
# finished_at. Every read must treat them as ONE logical run.
# ---------------------------------------------------------------------------


def test_every_run_ranking_merges_fragments_and_skips_unfinished_runs():
    calls = {
        "fetch_price_series": {},
        "fetch_latest_vs_previous": {"min_gap_hours": 12},
        "fetch_own_live_price": {"room_type_category": "double", "display_name": "Rea Hotel"},
    }
    for method, extra in calls.items():
        connection = FakeConnection()

        getattr(_repository(connection), method)(**MARKET_KEY, **extra)

        sql = connection.executed_sql
        assert "dense_rank() OVER (ORDER BY sr.finished_at DESC)" in sql, method
        assert "row_number()" not in sql, method
        # PostgreSQL sorts NULLs first under DESC: an unfinished run would rank as newest.
        assert "sr.finished_at IS NOT NULL" in sql, method


def test_alert_window_reads_the_latest_logical_run_as_one_timestamp():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY, min_gap_hours=12)

    # Several fragments share rn = 1; a bare scalar subquery would raise
    # CardinalityViolation in PostgreSQL.
    assert (
        "(SELECT MAX(latest.observed_at) FROM ranked_runs latest WHERE latest.rn = 1)"
        in connection.executed_sql
    )


def test_latest_completed_run_id_skips_unfinished_runs():
    connection = FakeConnection()

    _repository(connection).fetch_latest_completed_run_id(**MARKET_KEY)

    assert "sr.finished_at IS NOT NULL" in connection.executed_sql


_SQLITE_SCHEMA = (
    "CREATE TABLE roomrate_scrape_runs (id TEXT PRIMARY KEY, account_id TEXT,"
    " canonical_destination TEXT, check_in TEXT, check_out TEXT, adults INTEGER,"
    " children INTEGER, rooms INTEGER, status TEXT, finished_at TEXT)",
    "CREATE TABLE roomrate_properties (id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT)",
    "CREATE TABLE roomrate_rate_observations (id TEXT PRIMARY KEY, scrape_run_id TEXT, property_id TEXT)",
    "CREATE TABLE roomrate_room_packages (id TEXT PRIMARY KEY, rate_observation_id TEXT,"
    " room_type_category TEXT, cancellation_type TEXT, price_per_night_eur NUMERIC,"
    " discounted_price_per_night_eur NUMERIC)",
    "CREATE TABLE roomrate_owned_properties (id TEXT PRIMARY KEY, account_id TEXT,"
    " display_name TEXT, is_active BOOLEAN)",
)

# One job, two fragments with the same finished_at. The Faliraki fragment holds
# 7 competitors plus the owner's Rea Hotel; the Kolymbia fragment holds 2 more
# competitors and has the larger run id, so "newest id wins" would see only it.
_FRAGMENTS = {
    "run-faliraki": [(f"Faliraki Hotel {index}", 100 + 5 * index) for index in range(7)]
    + [("Rea Hotel", 92)],
    "run-kolymbia": [("Kolymbia Hotel 0", 90), ("Kolymbia Hotel 1", 140)],
}


def _seed_one_job_in_two_fragments(connection) -> None:
    connection.execute(
        text("INSERT INTO roomrate_owned_properties VALUES ('own-1', :account_id, 'Rea Hotel', 1)"),
        {"account_id": str(ACCOUNT_ID)},
    )
    for run_id, hotels in _FRAGMENTS.items():
        connection.execute(
            text(
                "INSERT INTO roomrate_scrape_runs VALUES (:id, :account_id, 'faliraki',"
                " '2026-07-01', '2026-07-05', 2, 0, 1, 'completed', '2026-09-14T08:00:00+00:00')"
            ),
            {"id": run_id, "account_id": str(ACCOUNT_ID)},
        )
        for hotel_name, price in hotels:
            property_id = "prop-" + hotel_name.lower().replace(" ", "-")
            observation_id = f"obs-{run_id}-{property_id}"
            connection.execute(
                text("INSERT INTO roomrate_properties VALUES (:id, :canonical_name, :display_name)"),
                {"id": property_id, "canonical_name": hotel_name.lower(), "display_name": hotel_name},
            )
            connection.execute(
                text("INSERT INTO roomrate_rate_observations VALUES (:id, :run_id, :property_id)"),
                {"id": observation_id, "run_id": run_id, "property_id": property_id},
            )
            connection.execute(
                text(
                    "INSERT INTO roomrate_room_packages VALUES"
                    " (:id, :observation_id, 'double', NULL, :price, NULL)"
                ),
                {"id": f"pkg-{observation_id}", "observation_id": observation_id, "price": price},
            )


@pytest.fixture()
def fragmented_market_repository():
    """The real repository SQL over an in-memory SQLite copy of the five tables it reads."""
    if sqlite3.sqlite_version_info < (3, 39, 0):
        pytest.skip("IS DISTINCT FROM needs SQLite 3.39+")
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        for statement in _SQLITE_SCHEMA:
            connection.execute(text(statement))
        _seed_one_job_in_two_fragments(connection)
    yield PriceHistoryRepository(engine.connect)
    engine.dispose()


def test_one_job_written_as_two_fragments_is_one_logical_run(fragmented_market_repository):
    repository = fragmented_market_repository

    rows = repository.fetch_price_series(**MARKET_KEY, room_type_category="double", include_similar=True)
    own = repository.fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
    )
    stats = compute_price_statistics(rows, own["price"], check_in=MARKET_KEY["check_in"], as_of=date(2026, 6, 20))

    assert {row["rn"] for row in rows} == {1}
    assert len(rows) == 9  # 7 Faliraki + 2 Kolymbia competitors ...
    assert "Rea Hotel" not in {row["hotel_name"] for row in rows}  # ... never the own hotel
    assert own == {"price": 92.0, "cancellation_type": None}  # the Faliraki fragment
    assert stats.sample_runs == 1
    assert stats.position.total == 9
    assert stats.stats_scope.same_category == 9


def test_latest_run_id_is_the_largest_id_among_the_latest_fragments(fragmented_market_repository):
    assert fragmented_market_repository.fetch_latest_completed_run_id(**MARKET_KEY) == "run-kolymbia"


# ---------------------------------------------------------------------------
# Like-for-like (spec 2026-09-29 §4) over real SQL: the cheapest price per
# hotel is computed FIRST inside the own reference package's cancellation
# class (NULL/unknown always participates), else the hotel's overall minimum.
# ---------------------------------------------------------------------------

LINDOS_KEY = {**MARKET_KEY, "canonical_destination": "lindos"}
IALYSOS_KEY = {**MARKET_KEY, "canonical_destination": "ialysos"}
AFANDOU_KEY = {**MARKET_KEY, "canonical_destination": "afandou"}

_LIKE_FOR_LIKE_MARKETS = {
    # Mixed classes around a non_refundable own package.
    ("run-lindos", "lindos"): {
        "Rea Hotel": [("double", "non_refundable", 92), ("double", "free_cancellation", 99)],
        "Hotel Match": [
            ("double", "free_cancellation", 70),
            ("double", "non_refundable", 85),
            ("double", None, 100),
        ],
        "Hotel NullOnly": [("double", None, 60)],
        "Hotel OtherClass": [("double", "free_cancellation", 50)],
    },
    # Every competitor row carries a DIFFERENT known class: nothing matches.
    ("run-ialysos", "ialysos"): {
        "Hotel FreeA": [("double", "free_cancellation", 70), ("double", "free_cancellation", 85)],
        "Hotel FreeB": [("double", "free_cancellation", 50)],
    },
    # The only equal-class row sits in a similar-only hotel (review fix: the
    # label follows the basis, so it counts exactly when widening pulls it in).
    ("run-afandou", "afandou"): {
        "Hotel SameA": [("double", "free_cancellation", 100)],
        "Hotel SameB": [("double", "free_cancellation", 120)],
        "Hotel SimOnly": [("suite", "non_refundable", 80)],
    },
}


@pytest.fixture()
def like_for_like_repository():
    """The real repository SQL over SQLite with mixed cancellation classes."""
    if sqlite3.sqlite_version_info < (3, 39, 0):
        pytest.skip("IS DISTINCT FROM needs SQLite 3.39+")
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        for statement in _SQLITE_SCHEMA:
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO roomrate_owned_properties VALUES ('own-1', :account_id, 'Rea Hotel', 1)"),
            {"account_id": str(ACCOUNT_ID)},
        )
        for (run_id, destination), hotels in _LIKE_FOR_LIKE_MARKETS.items():
            connection.execute(
                text(
                    "INSERT INTO roomrate_scrape_runs VALUES (:id, :account_id, :destination,"
                    " '2026-07-01', '2026-07-05', 2, 0, 1, 'completed', '2026-09-14T08:00:00+00:00')"
                ),
                {"id": run_id, "account_id": str(ACCOUNT_ID), "destination": destination},
            )
            for hotel_name, packages in hotels.items():
                property_id = "prop-" + hotel_name.lower().replace(" ", "-")
                observation_id = f"obs-{run_id}-{property_id}"
                connection.execute(
                    text("INSERT INTO roomrate_properties VALUES (:id, :canonical_name, :display_name)"),
                    {"id": property_id, "canonical_name": hotel_name.lower(), "display_name": hotel_name},
                )
                connection.execute(
                    text("INSERT INTO roomrate_rate_observations VALUES (:id, :run_id, :property_id)"),
                    {"id": observation_id, "run_id": run_id, "property_id": property_id},
                )
                for index, (category, cancellation, price) in enumerate(packages):
                    connection.execute(
                        text(
                            "INSERT INTO roomrate_room_packages VALUES"
                            " (:id, :observation_id, :category, :cancellation, :price, NULL)"
                        ),
                        {
                            "id": f"pkg-{observation_id}-{index}",
                            "observation_id": observation_id,
                            "category": category,
                            "cancellation": cancellation,
                            "price": price,
                        },
                    )
    yield PriceHistoryRepository(engine.connect)
    engine.dispose()


def _series_by_hotel(rows: list[dict]) -> dict[str, dict]:
    return {row["hotel_name"]: row for row in rows}


def test_like_for_like_minimum_prefers_the_own_cancellation_class(like_for_like_repository):
    repository = like_for_like_repository

    own = repository.fetch_own_live_price(
        **LINDOS_KEY, room_type_category="double", display_name="Rea Hotel"
    )
    assert own == {"price": 92.0, "cancellation_type": "non_refundable"}

    rows = repository.fetch_price_series(
        **LINDOS_KEY,
        room_type_category="double",
        include_similar=True,
        cancellation_type=own["cancellation_type"],
    )
    by_hotel = _series_by_hotel(rows)

    # Match: min over {non_refundable 85, NULL 100} — never its 70 free offer.
    assert float(by_hotel["Hotel Match"]["min_price_same"]) == 85.0
    # NULL/unknown always participates in the class.
    assert float(by_hotel["Hotel NullOnly"]["min_price_same"]) == 60.0
    # No row in the class set at all: that hotel's overall minimum, as today.
    assert float(by_hotel["Hotel OtherClass"]["min_price_same"]) == 50.0
    assert float(by_hotel["Hotel Match"]["min_price"]) == 85.0  # legacy alias follows

    stats = compute_price_statistics(
        rows,
        own["price"],
        check_in=LINDOS_KEY["check_in"],
        as_of=date(2026, 6, 20),
        own_cancellation_type=own["cancellation_type"],
    )
    assert stats.stats_scope.cancellation_class == "matched"
    assert stats.market_median_eur == 60.0  # median of 85 / 60 / 50


def test_like_for_like_falls_back_to_the_overall_minimum_without_a_class_match(like_for_like_repository):
    rows = like_for_like_repository.fetch_price_series(
        **IALYSOS_KEY,
        room_type_category="double",
        include_similar=True,
        cancellation_type="non_refundable",
    )
    by_hotel = _series_by_hotel(rows)

    # No competitor row shares the class (and none is NULL): today's overall minimums.
    assert float(by_hotel["Hotel FreeA"]["min_price_same"]) == 70.0
    assert float(by_hotel["Hotel FreeB"]["min_price_same"]) == 50.0

    stats = compute_price_statistics(
        rows,
        60.0,
        check_in=IALYSOS_KEY["check_in"],
        as_of=date(2026, 6, 20),
        own_cancellation_type="non_refundable",
    )
    assert stats.stats_scope.cancellation_class == "all"
    assert stats.market_median_eur == 60.0  # (70 + 50) / 2


def test_like_for_like_is_off_when_the_own_class_is_unknown(like_for_like_repository):
    rows = like_for_like_repository.fetch_price_series(
        **LINDOS_KEY, room_type_category="double", include_similar=True
    )
    by_hotel = _series_by_hotel(rows)

    # Without a reference class every hotel keeps today's overall minimum.
    assert float(by_hotel["Hotel Match"]["min_price_same"]) == 70.0

    stats = compute_price_statistics(
        rows, 92.0, check_in=LINDOS_KEY["check_in"], as_of=date(2026, 6, 20)
    )
    assert stats.stats_scope.cancellation_class == "all"


def test_a_similar_only_class_match_labels_matched_only_via_widening(like_for_like_repository):
    """End to end over real SQL: the equal-class suite of a similar-only hotel
    flags cancellation_matched_similar, and the label follows the basis — the
    widened Afandou market (2 same hotels) says «matched»."""
    rows = like_for_like_repository.fetch_price_series(
        **AFANDOU_KEY,
        room_type_category="double",
        include_similar=True,
        cancellation_type="non_refundable",
    )
    by_hotel = _series_by_hotel(rows)

    assert not bool(by_hotel["Hotel SameA"]["cancellation_matched_same"])
    assert bool(by_hotel["Hotel SimOnly"]["cancellation_matched_similar"])
    assert float(by_hotel["Hotel SimOnly"]["min_price_similar"]) == 80.0

    stats = compute_price_statistics(
        rows,
        95.0,
        check_in=AFANDOU_KEY["check_in"],
        as_of=date(2026, 6, 20),
        own_cancellation_type="non_refundable",
    )
    assert stats.stats_scope.used == "same_plus_similar"
    assert stats.stats_scope.cancellation_class == "matched"


# ---------------------------------------------------------------------------
# Agent basis (owner decision 2026-09-30) over real SQL: when a run's scrape
# job has room-matching agent rows for the owned room type, each hotel's price
# is its cheapest agent-comparable room; runs without rows keep the category
# pool. EFFECTIVE comparable = comparable when set, else score >= 50.
# ---------------------------------------------------------------------------

ROOM_A = "00000000-0000-0000-0000-0000000000ra"
ROOM_B = "00000000-0000-0000-0000-0000000000rb"
AGENT_KEY = {**MARKET_KEY, "canonical_destination": "kallithea"}

_AGENT_SCHEMA = (
    "CREATE TABLE roomrate_scrape_runs (id TEXT PRIMARY KEY, account_id TEXT,"
    " canonical_destination TEXT, check_in TEXT, check_out TEXT, adults INTEGER,"
    " children INTEGER, rooms INTEGER, status TEXT, finished_at TEXT, scrape_job_id TEXT)",
    "CREATE TABLE roomrate_properties (id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT)",
    "CREATE TABLE roomrate_rate_observations (id TEXT PRIMARY KEY, scrape_run_id TEXT, property_id TEXT)",
    "CREATE TABLE roomrate_room_packages (id TEXT PRIMARY KEY, rate_observation_id TEXT, room_type TEXT,"
    " room_type_category TEXT, cancellation_type TEXT, price_per_night_eur NUMERIC,"
    " discounted_price_per_night_eur NUMERIC)",
    "CREATE TABLE roomrate_owned_properties (id TEXT PRIMARY KEY, account_id TEXT,"
    " display_name TEXT, is_active BOOLEAN)",
    "CREATE TABLE roomrate_room_matches (account_id TEXT, scrape_job_id TEXT, owned_room_type_id TEXT,"
    " property_id TEXT, room_type TEXT, score NUMERIC, comparable BOOLEAN, reasoning TEXT,"
    " created_at TEXT)",
)

# (run id, job id, finished_at) -> hotel -> [(room_type, category, price)]
_AGENT_RUNS = {
    ("run-new", "job-new", "2026-09-14T08:00:00+00:00"): {
        "Hotel A": [("Double Room", "double", 60), ("Deluxe Double Sea View", "double", 90)],
        "Hotel B": [("Suite", "suite", 120), ("Studio", "studio", 50)],
        "Hotel C": [("Double Room", "double", 70)],
        "Hotel D": [("Twin Room", "twin", 80)],
        "Rea Hotel": [("Double Room", "double", 95)],
    },
    # No agent rows for this job: the category pool, exactly as before.
    ("run-old", "job-old", "2026-09-07T08:00:00+00:00"): {
        "Hotel A": [("Double Room", "double", 55)],
        "Hotel C": [("Double Room", "double", 65)],
        "Hotel D": [("Twin Room", "twin", 75)],
    },
}

# (owned room, hotel, room_type, score, comparable)
_AGENT_MATCHES = (
    (ROOM_A, "Hotel A", "Deluxe Double Sea View", 85, 1),
    (ROOM_A, "Hotel A", "Double Room", 70, 0),  # explicit verdict beats score >= 50
    (ROOM_A, "Hotel B", "Suite", 72, None),  # NULL verdict: score >= 50 -> comparable
    (ROOM_A, "Hotel B", "Studio", 30, None),  # NULL verdict: score < 50 -> not
    (ROOM_A, "Hotel C", "Double Room", 40, 0),
    (ROOM_A, "Rea Hotel", "Double Room", 99, 1),  # own hotel: never a competitor
    (ROOM_B, "Hotel C", "Double Room", 90, 1),
)


def _property_id(hotel_name: str) -> str:
    return "prop-" + hotel_name.lower().replace(" ", "-")


def _agent_basis_engine(runs=None, matches=_AGENT_MATCHES, match_job="job-new"):
    """SQLite with the repository's tables, the given runs and one job's agent rows."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        for statement in _AGENT_SCHEMA:
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO roomrate_owned_properties VALUES ('own-1', :account_id, 'Rea Hotel', 1)"),
            {"account_id": str(ACCOUNT_ID)},
        )
        for (run_id, job_id, finished_at), hotels in (runs or _AGENT_RUNS).items():
            connection.execute(
                text(
                    "INSERT INTO roomrate_scrape_runs VALUES (:id, :account_id, 'kallithea',"
                    " '2026-07-01', '2026-07-05', 2, 0, 1, 'completed', :finished_at, :job_id)"
                ),
                {"id": run_id, "account_id": str(ACCOUNT_ID), "finished_at": finished_at, "job_id": job_id},
            )
            for hotel_name, packages in hotels.items():
                property_id = _property_id(hotel_name)
                observation_id = f"obs-{run_id}-{property_id}"
                connection.execute(
                    text("INSERT OR IGNORE INTO roomrate_properties VALUES (:id, :canonical_name, :display_name)"),
                    {"id": property_id, "canonical_name": hotel_name.lower(), "display_name": hotel_name},
                )
                connection.execute(
                    text("INSERT INTO roomrate_rate_observations VALUES (:id, :run_id, :property_id)"),
                    {"id": observation_id, "run_id": run_id, "property_id": property_id},
                )
                for index, (room_type, category, price) in enumerate(packages):
                    connection.execute(
                        text(
                            "INSERT INTO roomrate_room_packages VALUES"
                            " (:id, :observation_id, :room_type, :category, NULL, :price, NULL)"
                        ),
                        {
                            "id": f"pkg-{observation_id}-{index}",
                            "observation_id": observation_id,
                            "room_type": room_type,
                            "category": category,
                            "price": price,
                        },
                    )
        for owned_room, hotel_name, room_type, score, comparable in matches:
            connection.execute(
                text(
                    "INSERT INTO roomrate_room_matches (account_id, scrape_job_id, owned_room_type_id,"
                    " property_id, room_type, score, comparable, reasoning, created_at) VALUES"
                    " (:account_id, :job_id, :owned_room, :property_id, :room_type, :score, :comparable,"
                    " 'αιτιολόγηση', '2026-09-14T08:05:00+00:00')"
                ),
                {
                    "account_id": str(ACCOUNT_ID),
                    "job_id": match_job,
                    "owned_room": owned_room,
                    "property_id": _property_id(hotel_name),
                    "room_type": room_type,
                    "score": score,
                    "comparable": comparable,
                },
            )
    return engine


def _skip_without_is_distinct_from() -> None:
    if sqlite3.sqlite_version_info < (3, 39, 0):
        pytest.skip("IS DISTINCT FROM needs SQLite 3.39+")


@pytest.fixture()
def agent_basis_repository():
    """The real repository SQL over SQLite with agent match rows for one job."""
    _skip_without_is_distinct_from()
    engine = _agent_basis_engine()
    yield PriceHistoryRepository(engine.connect)
    engine.dispose()


def _run_prices(rows: list[dict], run_index: int) -> dict[str, float]:
    # Similar-only hotels (category path) carry no same price: not in the map.
    return {
        row["hotel_name"]: float(row["min_price_same"])
        for row in rows
        if row["rn"] == run_index and row["min_price_same"] is not None
    }


def test_agent_basis_prices_each_hotel_at_its_cheapest_comparable_room(agent_basis_repository):
    rows = agent_basis_repository.fetch_price_series(
        **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_A
    )

    # A: the 60 Double Room is scored 70 but the agent said "not comparable".
    # B: its NULL-verdict Suite counts through score >= 50, never its 50 studio.
    # D: the agent left its twin room unscored (a failed chunk, spec Α.5), so
    # the double pool decides, exactly as the map reads the same job.
    # C (only a non-comparable room) and the own hotel drop out.
    assert _run_prices(rows, 1) == {"Hotel A": 90.0, "Hotel B": 120.0, "Hotel D": 80.0}
    latest = [row for row in rows if row["rn"] == 1]
    assert all(int(row["agent_basis"]) == 1 for row in latest)
    assert all(row["min_price_similar"] is None for row in latest)  # no widening pool


def test_runs_without_agent_matches_keep_the_category_pool(agent_basis_repository):
    rows = agent_basis_repository.fetch_price_series(
        **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_A
    )

    assert _run_prices(rows, 2) == {"Hotel A": 55.0, "Hotel C": 65.0, "Hotel D": 75.0}
    assert all(int(row["agent_basis"]) == 0 for row in rows if row["rn"] == 2)


def test_without_an_owned_room_type_every_run_keeps_the_category_pool(agent_basis_repository):
    rows = agent_basis_repository.fetch_price_series(
        **AGENT_KEY, room_type_category="double", include_similar=True
    )

    assert _run_prices(rows, 1)["Hotel A"] == 60.0
    assert all(int(row["agent_basis"]) == 0 for row in rows)


def test_owned_room_type_id_selects_its_own_agent_matches(agent_basis_repository):
    rows = agent_basis_repository.fetch_price_series(
        **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_B
    )

    # ROOM_B's run judged only C's Double Room — comparable for this room,
    # while ROOM_A's verdict drops it. A's and D's rooms carry no ROOM_B row,
    # so the double pool prices them (A at its 60 Double Room, which ROOM_A's
    # agent rejected); B sells no double-pool room.
    assert _run_prices(rows, 1) == {"Hotel A": 60.0, "Hotel C": 70.0, "Hotel D": 80.0}


def test_chart_and_statistics_read_the_same_agent_basis(agent_basis_repository):
    chart_rows = agent_basis_repository.fetch_price_series(
        **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_A
    )
    stats = compute_price_statistics(
        chart_rows, 100.0, check_in=AGENT_KEY["check_in"], as_of=date(2026, 6, 20)
    )
    chart_latest = sorted(row["min_price"] for row in basis_price_rows(chart_rows) if row["rn"] == 1)

    assert stats.stats_scope.used == "agent"
    assert stats.stats_scope.comparable == 3
    assert stats.stats_scope.similar == 0
    assert chart_latest == [80.0, 90.0, 120.0]
    assert stats.market_median_eur == 90.0  # the chart's own latest median
    assert stats.position.total == len(chart_latest)


def test_alerts_never_pair_runs_on_different_bases(agent_basis_repository):
    rows = agent_basis_repository.fetch_latest_vs_previous(
        **AGENT_KEY, room_type_category="double", owned_room_type_id=ROOM_A
    )
    by_hotel = {row["hotel_name"]: row for row in rows}

    assert float(by_hotel["Hotel A"]["current_price"]) == 90.0
    # The previous run priced A over the category pool: a basis switch is not
    # a market price change, so no alert pair forms.
    assert by_hotel["Hotel A"]["previous_price"] is None


def test_latest_run_agent_matches_are_every_verdict_of_the_latest_job(agent_basis_repository):
    rows = agent_basis_repository.fetch_latest_run_agent_matches(
        **AGENT_KEY, owned_room_type_id=ROOM_A
    )

    # Comparable and not alike: the advisor needs the whole lookup to tell a
    # rejected room from one the agent never scored.
    verdicts = {(row["property_id"], row["room_type"]): row["comparable"] for row in rows}
    assert verdicts == {
        ("prop-hotel-a", "Deluxe Double Sea View"): 1,
        ("prop-hotel-a", "Double Room"): 0,
        ("prop-hotel-b", "Suite"): None,
        ("prop-hotel-b", "Studio"): None,
        ("prop-hotel-c", "Double Room"): 0,
        ("prop-rea-hotel", "Double Room"): 1,
    }
    # The write stamp the recommendation cache key folds in.
    assert {row["created_at"] for row in rows} == {"2026-09-14T08:05:00+00:00"}
    assert all(row["reasoning"] == "αιτιολόγηση" for row in rows)


def test_latest_run_agent_matches_are_empty_for_a_room_without_rows(agent_basis_repository):
    rooms = agent_basis_repository.fetch_latest_run_agent_matches(
        **AGENT_KEY, owned_room_type_id="00000000-0000-0000-0000-0000000000rc"
    )

    assert rooms == []


def test_an_agent_row_prices_every_spelling_of_its_room():
    """Booking spells one room differently across packages; the agent stores
    one row per room (case and surrounding spaces folded, like the read
    side's lookup), and every spelling must find it."""
    _skip_without_is_distinct_from()
    runs = {
        ("run-new", "job-new", "2026-09-14T08:00:00+00:00"): {
            "Hotel A": [("studio apartment ", "studio", 60), ("Studio Apartment", "studio", 64)],
            "Hotel B": [("Double Room", "double", 50)],
        },
    }
    matches = (
        (ROOM_A, "Hotel A", "Studio Apartment", 88, 1),
        (ROOM_A, "Hotel B", "DOUBLE ROOM", 20, 0),
    )
    engine = _agent_basis_engine(runs, matches)
    try:
        rows = PriceHistoryRepository(engine.connect).fetch_price_series(
            **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_A
        )
    finally:
        engine.dispose()

    # A's studio is comparable in both spellings, so its cheaper one (60)
    # prices it; an exact match would leave that one unscored, outside the
    # double pool. B's rejection binds to «Double Room» too, so B stays out
    # instead of falling back to the double pool at 50.
    assert _run_prices(rows, 1) == {"Hotel A": 60.0}


def test_a_newest_run_with_no_comparable_room_is_never_replaced_by_an_older_one():
    """The agent rejected every room of the newest search: its hotels come
    back without a basis price, and the statistics report no current market
    instead of promoting the older search to today's."""
    _skip_without_is_distinct_from()
    runs = {
        ("run-new", "job-new", "2026-09-14T08:00:00+00:00"): {
            "Hotel A": [("Double Room", "double", 60)],
            "Hotel C": [("Double Room", "double", 70)],
        },
        ("run-old", "job-old", "2026-09-07T08:00:00+00:00"): {
            "Hotel A": [("Double Room", "double", 55)],
            "Hotel C": [("Double Room", "double", 65)],
            "Hotel D": [("Twin Room", "twin", 75)],
        },
    }
    matches = (
        (ROOM_A, "Hotel A", "Double Room", 30, 0),
        (ROOM_A, "Hotel C", "Double Room", 25, 0),
    )
    engine = _agent_basis_engine(runs, matches)
    try:
        rows = PriceHistoryRepository(engine.connect).fetch_price_series(
            **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_A
        )
    finally:
        engine.dispose()
    stats = compute_price_statistics(rows, 100.0, check_in=AGENT_KEY["check_in"], as_of=date(2026, 6, 20))

    latest = [row for row in rows if row["rn"] == 1]
    assert {row["hotel_name"] for row in latest} == {"Hotel A", "Hotel C"}
    assert all(row["min_price_same"] is None for row in latest)
    assert stats.market_median_eur is None
    assert stats.statistical_recommendation_eur is None
    assert stats.sample_runs == 0
    assert stats.stats_scope.used == "agent"
    assert stats.stats_scope.comparable == 0
    assert (
        "Στην τελευταία αναζήτηση η εκτίμηση AI δεν βρήκε δωμάτιο συγκρίσιμο "
        "με το δικό σας· δεν υπάρχει τρέχουσα βάση σύγκρισης."
    ) in stats.notes
    # The chart keeps the older searches: history, dated as such.
    assert {row["rn"] for row in basis_price_rows(rows)} == {2}


def test_only_the_agent_basis_returns_unpriced_latest_hotels():
    category = FakeConnection()
    _repository(category).fetch_price_series(**MARKET_KEY, room_type_category="double")
    agent = FakeConnection()
    _repository(agent).fetch_price_series(
        **MARKET_KEY, room_type_category="double", owned_room_type_id=ROOM_A
    )

    assert "rn = 1 AND agent_basis = 1" not in category.executed_sql
    assert "WHERE (min_price_same IS NOT NULL) OR (rn = 1 AND agent_basis = 1)" in agent.executed_sql
    # An unscored room falls back to the category pool on the agent basis.
    assert (
        "COALESCE(rm.comparable, rm.score >= 50, rp.room_type_category IN"
        " (:room_type_category_0, :room_type_category_1))"
    ) in agent.executed_sql
    assert "lower(trim(rm.room_type)) = lower(trim(rp.room_type))" in agent.executed_sql


# ---------------------------------------------------------------------------
# Effective price (owner decision 2026-09-30): every minimum reads
# COALESCE(discounted, base) — except the price-change alerts, which keep
# comparing BASE prices because older runs carry no discount data.
# ---------------------------------------------------------------------------

EFFECTIVE_KEY = {**MARKET_KEY, "canonical_destination": "pefkos"}

# (run id, finished_at) -> hotel -> [(category, base price, discounted price)]
_EFFECTIVE_RUNS = {
    # The latest run carries the «-X% πληρωμή online» prices.
    ("run-eff-new", "2026-09-14T08:00:00+00:00"): {
        "Rea Hotel": [("double", 100, 92)],
        "Hotel Discounted": [("double", 120, 96)],
        "Hotel NoDiscount": [("double", 100, None)],
        "Hotel Mixed": [("double", 110, 99), ("double", 105, None)],
    },
    # An older run written before migration 0025: no discount data at all.
    ("run-eff-old", "2026-09-01T08:00:00+00:00"): {
        "Hotel Discounted": [("double", 120, None)],
        "Hotel NoDiscount": [("double", 100, None)],
        "Hotel Mixed": [("double", 105, None)],
    },
}


@pytest.fixture()
def effective_price_repository():
    """The real repository SQL over SQLite with discounted and base-only packages."""
    if sqlite3.sqlite_version_info < (3, 39, 0):
        pytest.skip("IS DISTINCT FROM needs SQLite 3.39+")
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        for statement in _SQLITE_SCHEMA:
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO roomrate_owned_properties VALUES ('own-1', :account_id, 'Rea Hotel', 1)"),
            {"account_id": str(ACCOUNT_ID)},
        )
        for (run_id, finished_at), hotels in _EFFECTIVE_RUNS.items():
            connection.execute(
                text(
                    "INSERT INTO roomrate_scrape_runs VALUES (:id, :account_id, 'pefkos',"
                    " '2026-07-01', '2026-07-05', 2, 0, 1, 'completed', :finished_at)"
                ),
                {"id": run_id, "account_id": str(ACCOUNT_ID), "finished_at": finished_at},
            )
            for hotel_name, packages in hotels.items():
                property_id = "prop-" + hotel_name.lower().replace(" ", "-")
                observation_id = f"obs-{run_id}-{property_id}"
                connection.execute(
                    text("INSERT OR IGNORE INTO roomrate_properties VALUES (:id, :canonical_name, :display_name)"),
                    {"id": property_id, "canonical_name": hotel_name.lower(), "display_name": hotel_name},
                )
                connection.execute(
                    text("INSERT INTO roomrate_rate_observations VALUES (:id, :run_id, :property_id)"),
                    {"id": observation_id, "run_id": run_id, "property_id": property_id},
                )
                for index, (category, base_price, discounted_price) in enumerate(packages):
                    connection.execute(
                        text(
                            "INSERT INTO roomrate_room_packages VALUES"
                            " (:id, :observation_id, :category, NULL, :price, :discounted)"
                        ),
                        {
                            "id": f"pkg-{observation_id}-{index}",
                            "observation_id": observation_id,
                            "category": category,
                            "price": base_price,
                            "discounted": discounted_price,
                        },
                    )
    yield PriceHistoryRepository(engine.connect)
    engine.dispose()


def _min_price_by_hotel(rows: list[dict], rn: int) -> dict[str, float]:
    return {row["hotel_name"]: float(row["min_price"]) for row in rows if row["rn"] == rn}


def test_a_discounted_package_lowers_the_hotels_minimum_and_the_median(effective_price_repository):
    repository = effective_price_repository

    rows = repository.fetch_price_series(**EFFECTIVE_KEY, room_type_category="double", include_similar=True)
    own = repository.fetch_own_live_price(
        **EFFECTIVE_KEY, room_type_category="double", display_name="Rea Hotel"
    )
    stats = compute_price_statistics(
        rows, own["price"], check_in=EFFECTIVE_KEY["check_in"], as_of=date(2026, 6, 20)
    )

    # Latest run: the discounted price wins; a NULL discount falls back to base.
    assert _min_price_by_hotel(rows, 1) == {
        "Hotel Discounted": 96.0,
        "Hotel NoDiscount": 100.0,
        "Hotel Mixed": 99.0,  # 99 discounted beats the 105 base-only package
    }
    # Older run without discount data: exactly the base minimums.
    assert _min_price_by_hotel(rows, 2) == {
        "Hotel Discounted": 120.0,
        "Hotel NoDiscount": 100.0,
        "Hotel Mixed": 105.0,
    }
    # The owner's live reference is effective too: like-for-like.
    assert own == {"price": 92.0, "cancellation_type": None}
    assert stats.market_median_eur == 99.0  # base prices would say 105.0


def test_runs_flag_discount_data_and_trends_never_pair_across_it(effective_price_repository):
    """A run before migration 0025 carries no discounted price: its base
    prices never pair with the effective ones of a newer run in a trend."""
    rows = effective_price_repository.fetch_price_series(
        **EFFECTIVE_KEY, room_type_category="double", include_similar=True
    )
    stats = compute_price_statistics(rows, None, check_in=EFFECTIVE_KEY["check_in"], as_of=date(2026, 6, 20))

    flags = {(row["rn"], row["hotel_name"]): int(row["has_discount_data"]) for row in rows}
    assert flags == {
        (1, "Hotel Discounted"): 1,
        (1, "Hotel NoDiscount"): 0,
        (1, "Hotel Mixed"): 1,
        (2, "Hotel Discounted"): 0,
        (2, "Hotel NoDiscount"): 0,
        (2, "Hotel Mixed"): 0,
    }
    assert stats.trend_7d_pct is None
    assert (
        "Οι προηγούμενες αναζητήσεις έγιναν με διαφορετική μέθοδο — η τάση 7 ημερών δεν υπολογίζεται."
        in stats.notes
    )


def test_trends_never_pair_an_agent_run_with_a_category_run(agent_basis_repository):
    rows = agent_basis_repository.fetch_price_series(
        **AGENT_KEY, room_type_category="double", include_similar=True, owned_room_type_id=ROOM_A
    )
    stats = compute_price_statistics(rows, None, check_in=AGENT_KEY["check_in"], as_of=date(2026, 6, 20))

    assert stats.trend_7d_pct is None
    assert (
        "Οι προηγούμενες αναζητήσεις έγιναν με διαφορετική μέθοδο — η τάση 7 ημερών δεν υπολογίζεται."
        in stats.notes
    )


def test_alerts_keep_comparing_base_prices(effective_price_repository):
    """No false «Πτώση τιμής»: the older run has no discount data, so an
    effective-vs-base comparison would read 120 -> 96 as a price drop."""
    rows = effective_price_repository.fetch_latest_vs_previous(
        **EFFECTIVE_KEY, room_type_category="double"
    )
    pairs = {
        row["hotel_name"]: (float(row["current_price"]), float(row["previous_price"]))
        for row in rows
    }

    assert pairs == {
        "Hotel Discounted": (120.0, 120.0),
        "Hotel NoDiscount": (100.0, 100.0),
        "Hotel Mixed": (105.0, 105.0),
    }


def test_only_the_alert_query_reads_base_prices():
    alerts = FakeConnection()
    _repository(alerts).fetch_latest_vs_previous(
        **MARKET_KEY, room_type_category="double", min_gap_hours=12, owned_room_type_id=ROOM_A
    )
    series = FakeConnection()
    _repository(series).fetch_price_series(
        **MARKET_KEY,
        room_type_category="double",
        include_similar=True,
        cancellation_type="non_refundable",
        owned_room_type_id=ROOM_A,
    )

    assert "discounted_price_per_night_eur" not in alerts.executed_sql
    assert "THEN rp.price_per_night_eur END" in alerts.executed_sql
    # Agent basis, category fallback and like-for-like: all effective.
    assert "THEN COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur) END" in series.executed_sql
    assert "THEN rp.price_per_night_eur END" not in series.executed_sql
