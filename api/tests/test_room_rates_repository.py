from uuid import UUID

import pytest

from api.repositories.room_rates_repository import RoomRatesRepository
from api.services.market_service import RoomRateFilters
from api.tests._fakes import EchoConnection as FakeConnection
from api.tests._fakes import EchoConnectionContext as FakeConnectionContext


def test_room_rates_repository_uses_legacy_source_by_default():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection))

    repository.fetch_room_rates(RoomRateFilters(destination="Faliraki"))

    assert "FROM room_rates" in connection.executed_sql
    assert "roomrate_latest_room_rates" not in connection.executed_sql
    assert "Faliraki" in {
        value
        for key, value in connection.executed_params.items()
        if key.startswith("destination_")
    }


def test_room_rates_repository_can_read_from_normalized_view():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters())

    assert "FROM roomrate_latest_room_rates" in connection.executed_sql
    assert "FROM room_rates" not in connection.executed_sql


@pytest.mark.parametrize(
    "source, filters, expected_order",
    [
        (
            "normalized",
            RoomRateFilters(),
            "ORDER BY rates.scraped_at DESC,"
            " COALESCE(plan.discounted_price_per_night_eur, rates.price_per_night_eur) ASC",
        ),
        (
            "normalized",
            RoomRateFilters(scrape_job_id=UUID("00000000-0000-0000-0000-000000000abc")),
            "ORDER BY rates.scraped_at DESC,"
            " COALESCE(rates.discounted_price_per_night_eur, rates.price_per_night_eur) ASC",
        ),
        # The legacy table has no discount column: base order, as before.
        ("legacy", RoomRateFilters(), "ORDER BY rates.scraped_at DESC, rates.price_per_night_eur ASC"),
    ],
)
def test_competitor_reads_order_by_the_effective_price(source, filters, expected_order):
    """Owner decision 2026-09-30: a LIMIT keeps the effectively cheapest packages."""
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source=source)

    repository.fetch_room_rates(filters)

    assert expected_order in _flat_sql(connection)


def test_room_rates_repository_filters_normalized_rows_by_account_id():
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )

    repository.fetch_room_rates(RoomRateFilters(destination="Faliraki"))

    assert "account_id = :account_id" in connection.executed_sql
    assert connection.executed_params["account_id"] == "00000000-0000-0000-0000-000000000123"


def test_room_rates_repository_expands_known_destination_aliases():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(destination="Faliraki"))

    destination_values = {
        value
        for key, value in connection.executed_params.items()
        if key.startswith("destination_")
    }
    assert "rates.city IN" in connection.executed_sql
    assert "Faliraki" in destination_values
    assert "Φαληράκι" in destination_values


def test_room_rates_repository_filters_normalized_rows_by_occupancy():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(adults=2, children=1, rooms=1))

    assert "adults = :adults" in connection.executed_sql
    assert "children = :children" in connection.executed_sql
    assert "rooms = :rooms" in connection.executed_sql
    assert connection.executed_params["adults"] == 2
    assert connection.executed_params["children"] == 1
    assert connection.executed_params["rooms"] == 1


def _category_params(connection: FakeConnection) -> set[str]:
    return {
        value
        for key, value in connection.executed_params.items()
        if key.startswith("room_type_category_")
    }


def test_room_rates_repository_filters_normalized_rows_by_room_type_category():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(room_type_category="suite"))

    assert "rates.room_type_category IN (:room_type_category_0)" in connection.executed_sql
    assert _category_params(connection) == {"suite"}


def test_room_rates_repository_reads_the_whole_comparable_category_pool():
    """A 'double' search must still see the twin-bucketed «1 Διπλό ή 2 Μονά» rooms."""
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(room_type_category="double"))

    assert "rates.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert _category_params(connection) == {"double", "twin"}


def test_room_rates_repository_reads_the_comparable_pool_from_the_twin_side_too():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(room_type_category="twin"))

    assert _category_params(connection) == {"double", "twin"}


def test_room_rates_repository_never_composes_a_category_value_into_sql_text():
    """The pool builds placeholder NAMES; the category itself stays a parameter."""
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )
    hostile_category = "double' OR 1=1 --"

    repository.fetch_room_rates(
        RoomRateFilters(
            owned_property_id="00000000-0000-0000-0000-000000000456",
            room_type_category=hostile_category,
            selected_competitors_only=True,
        )
    )

    assert hostile_category not in connection.executed_sql
    # Precise about the injected fragment: the repository's own destination
    # fallback legitimately emits "1 = 1", so a bare "1=1" check would break
    # on a whitespace reformat while catching nothing extra.
    assert "OR 1=1" not in connection.executed_sql
    # Both places the pool reaches carry placeholders only.
    assert "rates.room_type_category IN (:room_type_category_0)" in connection.executed_sql
    assert "tc.room_type_category IN (:room_type_category_0)" in connection.executed_sql
    assert _category_params(connection) == {hostile_category}


def test_room_rates_repository_omits_the_category_pool_when_none_is_requested():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(destination="Faliraki"))

    assert "room_type_category IN" not in connection.executed_sql
    assert not _category_params(connection)
    # Round 6: only single rooms stay out (see the include_similar tests).
    assert "AND rates.room_type_category IS DISTINCT FROM 'single'" in connection.executed_sql


def test_room_rates_repository_filters_rows_by_required_amenities():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(amenities=("wifi", "pool")))

    assert "required_amenity_0" in connection.executed_sql
    assert "required_amenity_1" in connection.executed_sql
    assert "COALESCE(rates.facilities" in connection.executed_sql
    assert "ILIKE" in connection.executed_sql
    assert connection.executed_params["required_amenity_0_0"] == "%wifi%"
    assert connection.executed_params["required_amenity_1_0"] == "%pool%"


def test_room_rates_repository_expands_popular_facility_aliases():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters(amenities=("Free WiFi", "Swimming pool")))

    assert "OR" in connection.executed_sql
    assert connection.executed_params["required_amenity_0_0"] == "%wifi%"
    assert connection.executed_params["required_amenity_1_0"] == "%pool%"
    assert connection.executed_params["required_amenity_1_2"] == "%πισίνα%"


def test_room_rates_repository_can_limit_to_selected_competitors():
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )

    repository.fetch_room_rates(
        RoomRateFilters(
            owned_property_id="00000000-0000-0000-0000-000000000456",
            room_type_category="double",
            selected_competitors_only=True,
        )
    )

    assert "roomrate_tracked_competitors" in connection.executed_sql
    # Tracked rows carry the category the USER searched under ("double"), while
    # a pooled read also returns twin-bucketed rate rows: comparing the two
    # columns for equality would hide exactly those tracked competitors.
    assert "tc.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert "tc.room_type_category = rates.room_type_category" not in connection.executed_sql
    assert "tc.competitor_property_id = rates.property_id" in connection.executed_sql
    assert connection.executed_params["owned_property_id"] == "00000000-0000-0000-0000-000000000456"


def test_room_rates_repository_matches_tracked_rows_on_row_category_without_a_filter():
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )

    repository.fetch_room_rates(
        RoomRateFilters(
            owned_property_id="00000000-0000-0000-0000-000000000456",
            selected_competitors_only=True,
        )
    )

    # No requested category means no pool to scope by: fall back to matching
    # each tracked row against the rate row's own category.
    assert "tc.room_type_category = rates.room_type_category" in connection.executed_sql


def test_room_rates_repository_excludes_owned_property_from_competitor_rows():
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )

    repository.fetch_room_rates(
        RoomRateFilters(
            owned_property_id="00000000-0000-0000-0000-000000000456",
            room_type_category="twin",
        )
    )

    assert "roomrate_owned_properties op" in connection.executed_sql
    assert "lower(trim(op.display_name)) = lower(trim(rates.hotel_name))" in connection.executed_sql
    assert connection.executed_params["owned_property_id"] == "00000000-0000-0000-0000-000000000456"


def test_room_rates_repository_reads_from_latest_view_for_normalized_source():
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    repository.fetch_room_rates(RoomRateFilters())

    assert "FROM roomrate_latest_room_rates" in connection.executed_sql


def test_room_rates_repository_selects_room_attributes_per_source():
    connection = FakeConnection()

    RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized").fetch_room_rates(
        RoomRateFilters()
    )
    assert "rates.room_attributes" in connection.executed_sql

    RoomRatesRepository(lambda: FakeConnectionContext(connection), source="legacy").fetch_room_rates(
        RoomRateFilters()
    )
    # Legacy room_rates has no attributes column: expose a NULL placeholder.
    assert "NULL AS room_attributes" in connection.executed_sql


def test_room_rates_repository_can_read_one_scrape_job_result_set():
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )

    repository.fetch_room_rates(
        RoomRateFilters(
            scrape_job_id="00000000-0000-0000-0000-000000000777",
            room_type_category="twin",
        )
    )

    assert "FROM roomrate_room_packages" in connection.executed_sql
    assert "sr.scrape_job_id = :scrape_job_id" in connection.executed_sql
    assert "roomrate_latest_room_rates" not in connection.executed_sql
    # Facilities come from the write-time cache, not a per-row LATERAL aggregate.
    assert "amenities_cached" in connection.executed_sql
    assert "string_agg" not in connection.executed_sql
    assert connection.executed_params["scrape_job_id"] == "00000000-0000-0000-0000-000000000777"
    # The job-scoped read is the one the map calls after a live scrape, so it
    # must widen to the comparable pool exactly like the latest-snapshot read.
    assert "rates.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert _category_params(connection) == {"double", "twin"}


def test_room_rates_repository_lists_available_amenities_for_current_filters():
    connection = FakeConnection()
    repository = RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id="00000000-0000-0000-0000-000000000123",
    )

    repository.fetch_available_amenities(RoomRateFilters(destination="Faliraki", room_type_category="double"))

    assert "string_to_array" in connection.executed_sql
    # Facilities are pipe-joined by every producer (view string_agg,
    # amenities_cached refresh, scraper): the split MUST be on '|', with the
    # legacy ','/';' separators folded in via translate. Splitting only on
    # ','/';' returned each property's whole facility string as one "amenity".
    assert "translate(COALESCE(rates.facilities, ''), ',;', '||')" in connection.executed_sql
    assert ", '|')" in connection.executed_sql
    assert "roomrate_latest_room_rates" in connection.executed_sql
    # Amenity options describe the rooms the map is about to show, so they are
    # collected over the same comparable pool the rate read uses.
    assert "rates.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert connection.executed_params["account_id"] == "00000000-0000-0000-0000-000000000123"
    assert _category_params(connection) == {"double", "twin"}


# ----------------------------------------------------------------------------
# Round 6 (§3.6): include_similar, the job-scoped read without the city
# filter, booking_url, and the owner's own rows
# ----------------------------------------------------------------------------

ACCOUNT_ID = "00000000-0000-0000-0000-000000000123"
OWNED_PROPERTY_ID = "00000000-0000-0000-0000-000000000456"
JOB_ID = "00000000-0000-0000-0000-000000000777"


def _normalized_repository(connection: FakeConnection) -> RoomRatesRepository:
    return RoomRatesRepository(
        lambda: FakeConnectionContext(connection),
        source="normalized",
        account_id=ACCOUNT_ID,
    )


def _flat_sql(connection: FakeConnection) -> str:
    return " ".join(connection.executed_sql.split())


@pytest.mark.parametrize("scrape_job_id", [None, JOB_ID])
def test_include_similar_reads_every_category_except_single_rooms(scrape_job_id):
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(
        RoomRateFilters(room_type_category="double", scrape_job_id=scrape_job_id, include_similar=True)
    )

    assert (
        "AND (rates.room_type_category IS DISTINCT FROM 'single' "
        "OR rates.room_type_category IN (:room_type_category_0, :room_type_category_1))"
    ) in connection.executed_sql
    assert _category_params(connection) == {"double", "twin"}


@pytest.mark.parametrize("scrape_job_id", [None, JOB_ID])
def test_include_similar_never_hides_the_owner_own_single_category(scrape_job_id):
    """A single-room owner compares singles: the pool always stays in."""
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(
        RoomRateFilters(room_type_category="single", scrape_job_id=scrape_job_id, include_similar=True)
    )

    assert (
        "AND (rates.room_type_category IS DISTINCT FROM 'single' "
        "OR rates.room_type_category IN (:room_type_category_0))"
    ) in connection.executed_sql
    assert _category_params(connection) == {"single"}


@pytest.mark.parametrize("scrape_job_id", [None, JOB_ID])
def test_include_similar_false_keeps_the_comparable_pool(scrape_job_id):
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(
        RoomRateFilters(room_type_category="double", scrape_job_id=scrape_job_id, include_similar=False)
    )

    assert "rates.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert "IS DISTINCT FROM 'single'" not in connection.executed_sql
    assert _category_params(connection) == {"double", "twin"}


@pytest.mark.parametrize("scrape_job_id", [None, JOB_ID])
def test_without_a_category_include_similar_false_never_returns_more_rows_than_true(scrape_job_id):
    """No requested category means no pool of the owner's own: single rooms
    stay out either way, so both reads run the very same SQL."""
    sql_by_mode = {}
    for include_similar in (True, False):
        connection = FakeConnection()
        _normalized_repository(connection).fetch_room_rates(
            RoomRateFilters(scrape_job_id=scrape_job_id, include_similar=include_similar)
        )
        sql_by_mode[include_similar] = connection.executed_sql
        assert "AND rates.room_type_category IS DISTINCT FROM 'single'" in connection.executed_sql
        assert "room_type_category IN" not in connection.executed_sql
        assert not _category_params(connection)

    assert sql_by_mode[False] == sql_by_mode[True]


def test_internal_callers_default_to_the_comparable_pool_only():
    """The dataclass defaults to include_similar=False (only the HTTP routes
    default to true), so the price advisor in api/routers/agents.py, which
    builds these exact filters without the flag, compares comparable
    categories only."""
    connection = FakeConnection()
    advisor_filters = RoomRateFilters(
        owned_property_id=OWNED_PROPERTY_ID,
        room_type_category="double",
        selected_competitors_only=True,
    )

    _normalized_repository(connection).fetch_room_rates(advisor_filters)

    assert advisor_filters.include_similar is False
    assert "AND rates.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert "IS DISTINCT FROM 'single'" not in connection.executed_sql


def test_include_similar_still_scopes_tracked_competitors_by_the_requested_pool():
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(
        RoomRateFilters(
            owned_property_id=OWNED_PROPERTY_ID,
            room_type_category="double",
            selected_competitors_only=True,
            include_similar=True,
        )
    )

    assert "IS DISTINCT FROM 'single'" in connection.executed_sql
    assert "tc.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql
    assert _category_params(connection) == {"double", "twin"}


def test_legacy_source_never_filters_on_its_missing_category_column():
    connection = FakeConnection()

    RoomRatesRepository(lambda: FakeConnectionContext(connection)).fetch_room_rates(
        RoomRateFilters(room_type_category="double")
    )

    assert "IS DISTINCT FROM 'single'" not in connection.executed_sql
    assert "rates.room_type_category IN" not in connection.executed_sql


def test_job_scoped_read_lets_the_job_define_the_set_instead_of_the_city():
    """Nearby areas (Ιξιά, Καλλιθέα) are stored under their own city names, so
    a city filter on the job-scoped read would hide exactly the hotels the
    nearby scouts found. The job already scopes the market."""
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(
        RoomRateFilters(destination="Faliraki", scrape_job_id=JOB_ID)
    )

    sql = _flat_sql(connection)
    assert "rates.city IN" not in sql
    assert not any(key.startswith("destination_") for key in connection.executed_params)
    assert "sr.scrape_job_id = :scrape_job_id" in sql
    assert "(:account_id IS NULL OR rates.account_id = :account_id)" in sql
    assert connection.executed_params["account_id"] == ACCOUNT_ID


def test_latest_view_read_keeps_the_destination_alias_filter():
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(RoomRateFilters(destination="Faliraki"))

    assert "rates.city IN" in connection.executed_sql
    assert "Φαληράκι" in connection.executed_params.values()


def test_rate_reads_select_the_property_booking_url_per_source():
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(RoomRateFilters(scrape_job_id=JOB_ID))
    job_sql = _flat_sql(connection)
    assert "p.booking_url," in job_sql
    assert "rates.booking_url," in job_sql

    _normalized_repository(connection).fetch_room_rates(RoomRateFilters())
    # The latest-rates view predates the column, so the URL is read from the
    # property row itself (no view migration needed).
    assert (
        "(SELECT bp.booking_url FROM roomrate_properties bp WHERE bp.id = rates.property_id) AS booking_url"
        in _flat_sql(connection)
    )

    RoomRatesRepository(lambda: FakeConnectionContext(connection)).fetch_room_rates(RoomRateFilters())
    assert "NULL AS booking_url" in connection.executed_sql


RATE_PLAN_COLUMNS = (
    "discounted_price_per_night_eur",
    "discount_pct",
    "discount_label",
    "has_genius_discount",
    "cancellation_type",
    "payment_label",
    "rate_block_id",
)


def test_latest_view_read_joins_the_package_row_for_the_rate_plan_columns():
    """Spec 2026-09-29 §4: the latest-rates view predates the plan columns, so
    they are read from the package row by primary key (the booking_url
    approach — no view migration needed)."""
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(RoomRateFilters())

    sql = _flat_sql(connection)
    assert "LEFT JOIN roomrate_room_packages plan ON plan.id = rates.room_package_id" in sql
    for column in RATE_PLAN_COLUMNS:
        assert f"plan.{column}" in sql


def test_job_scoped_read_selects_the_rate_plan_columns_from_the_package_row():
    connection = FakeConnection()

    _normalized_repository(connection).fetch_room_rates(RoomRateFilters(scrape_job_id=JOB_ID))

    sql = _flat_sql(connection)
    # The job read already selects from roomrate_room_packages: no extra join.
    assert "LEFT JOIN roomrate_room_packages plan" not in sql
    for column in RATE_PLAN_COLUMNS:
        assert f"rp.{column}" in sql
        assert f"rates.{column}" in sql


def test_legacy_source_exposes_null_rate_plan_columns_without_the_join():
    connection = FakeConnection()

    RoomRatesRepository(lambda: FakeConnectionContext(connection)).fetch_room_rates(RoomRateFilters())

    sql = _flat_sql(connection)
    assert "LEFT JOIN roomrate_room_packages" not in sql
    for column in RATE_PLAN_COLUMNS:
        assert f"NULL AS {column}" in sql


def test_available_amenities_follow_include_similar():
    connection = FakeConnection()
    repository = _normalized_repository(connection)

    repository.fetch_available_amenities(RoomRateFilters(room_type_category="double", include_similar=True))
    assert "IS DISTINCT FROM 'single'" in connection.executed_sql

    repository.fetch_available_amenities(RoomRateFilters(room_type_category="double", include_similar=False))
    assert "rates.room_type_category IN (:room_type_category_0, :room_type_category_1)" in connection.executed_sql


class _RowsConnection(FakeConnection):
    """Echo connection that also returns scripted rows."""

    def __init__(self, rows: list[dict]):
        super().__init__()
        self.rows = rows

    def execute(self, sql, params):
        super().execute(sql, params)
        rows = self.rows

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _Result()


def test_fetch_own_property_rates_returns_the_rows_the_exclusion_removes():
    own_row = {"hotel_name": "Rea Hotel", "room_type_category": "twin", "price_per_night_eur": 92}
    connection = _RowsConnection([own_row])
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source="normalized")

    rows = repository.fetch_own_property_rates(
        UUID(ACCOUNT_ID), UUID(JOB_ID), "  Rea Hotel "
    )

    assert rows == [own_row]
    sql = _flat_sql(connection)
    assert "sr.scrape_job_id = :scrape_job_id" in sql
    assert "sr.account_id = :account_id" in sql
    assert "sr.status = 'completed'" in sql
    # The same name equality OWNED_PROPERTY_EXCLUSION_FILTER uses, inverted.
    assert "lower(trim(p.display_name)) = lower(trim(:display_name))" in sql
    for column in (
        "rp.room_type,",
        "rp.room_type_category,",
        "rp.price_per_night_eur,",
        "rp.discounted_price_per_night_eur,",
        "p.booking_url,",
        "p.latitude,",
    ):
        assert column in sql
    # Cheapest EFFECTIVE price first (discounted when shown, else base).
    assert "ORDER BY COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur) ASC" in sql
    assert connection.executed_params == {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "display_name": "Rea Hotel",
    }


@pytest.mark.parametrize(
    "source, scrape_job_id, display_name",
    [("normalized", None, "Rea Hotel"), ("normalized", JOB_ID, "   "), ("legacy", JOB_ID, "Rea Hotel")],
)
def test_fetch_own_property_rates_skips_the_query_without_a_job_a_name_or_normalized_data(
    source, scrape_job_id, display_name
):
    connection = FakeConnection()
    repository = RoomRatesRepository(lambda: FakeConnectionContext(connection), source=source)

    assert repository.fetch_own_property_rates(UUID(ACCOUNT_ID), scrape_job_id, display_name) == []
    assert connection.executed_sql == ""
