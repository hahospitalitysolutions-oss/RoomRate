from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from api.repositories.normalized_market_writer import (
    upsert_amenities_bulk,
    upsert_property,
    upsert_scrape_run,
    write_normalized_rates,
)
from api.services.room_rates_normalizer import NormalizedRoomRate


PROPERTY_A = UUID("00000000-0000-0000-0000-00000000aaa1")
PROPERTY_B = UUID("00000000-0000-0000-0000-00000000aaa2")


class FakeScalarResult:
    def __init__(self, value: UUID):
        self.value = value

    def scalar_one(self) -> UUID:
        return self.value


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[object, dict]] = []

    def execute(self, statement, params):
        self.calls.append((statement, params))
        return FakeScalarResult(UUID("00000000-0000-0000-0000-000000000999"))


class FakeTransaction:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeEngine:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.begin_calls = 0

    def begin(self):
        self.begin_calls += 1
        return FakeTransaction(self.connection)


def _normalized_rate() -> NormalizedRoomRate:
    return NormalizedRoomRate(
        provider="booking_com",
        source_record_id="record-1",
        source_run_key="run-1",
        source_property_key="property-1",
        canonical_name="aegean view",
        display_name="Aegean View",
        destination="Faliraki",
        city="Faliraki",
        country="GR",
        address="Faliraki Center",
        property_type="Hotel",
        latitude=Decimal("36.340001"),
        longitude=Decimal("28.200001"),
        stars=Decimal("3.00"),
        check_in=date(2026, 6, 15),
        check_out=date(2026, 6, 20),
        nights=5,
        guests=2,
        adults=2,
        children=0,
        rooms=1,
        observed_at=datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc),
        review_score=Decimal("8.80"),
        review_count=120,
        room_type="Double Room",
        room_type_category="double",
        meals="Breakfast included",
        free_cancellation="Yes",
        price_per_night_eur=Decimal("80.00"),
        price_total_eur=Decimal("400.00"),
        rooms_left=2,
        amenities=("WiFi", "Balcony"),
        raw_payload={"record_id": "record-1"},
        payload_hash="hash-1",
    )


def test_upsert_scrape_run_records_ingestion_source_metadata():
    connection = FakeConnection()

    upsert_scrape_run(
        connection,
        _normalized_rate(),
        UUID("00000000-0000-0000-0000-000000000001"),
        ingestion_source="backfill_room_rates",
    )

    _, params = connection.calls[0]
    assert params["raw_metadata"] == {"ingestion_source": "backfill_room_rates"}
    assert params["adults"] == 2
    assert params["children"] == 0
    assert params["rooms"] == 1


def test_upsert_scrape_run_stores_raw_and_canonical_destination():
    connection = FakeConnection()

    upsert_scrape_run(
        connection,
        _normalized_rate(),
        UUID("00000000-0000-0000-0000-000000000001"),
        raw_destination="Faliraki",
        canonical_destination="faliraki",
    )

    _, params = connection.calls[0]
    assert params["raw_destination"] == "Faliraki"
    assert params["canonical_destination"] == "faliraki"


def test_upsert_scrape_run_links_scrape_job_id():
    connection = FakeConnection()
    scrape_job_id = UUID("00000000-0000-0000-0000-000000000777")

    upsert_scrape_run(
        connection,
        _normalized_rate(),
        UUID("00000000-0000-0000-0000-000000000001"),
        ingestion_source="scraper",
        scrape_job_id=scrape_job_id,
    )

    _, params = connection.calls[0]
    assert params["scrape_job_id"] == scrape_job_id
    assert params["raw_metadata"] == {
        "ingestion_source": "scraper",
        "scrape_job_id": str(scrape_job_id),
    }


def test_upsert_property_refreshes_location_metadata_on_conflict():
    connection = FakeConnection()

    upsert_property(connection, _normalized_rate())

    statement, _ = connection.calls[0]
    sql = str(statement)
    assert "city = EXCLUDED.city" in sql
    assert "country = COALESCE(EXCLUDED.country, roomrate_properties.country)" in sql
    assert "latitude = COALESCE(EXCLUDED.latitude, roomrate_properties.latitude)" in sql
    assert "longitude = COALESCE(EXCLUDED.longitude, roomrate_properties.longitude)" in sql


def test_upsert_property_writes_the_booking_url_and_keeps_a_known_one_on_conflict():
    """Round 6: pre-existing properties gain the URL on their next scrape, and
    a run whose CSV lacks it never erases the one already stored."""
    connection = FakeConnection()
    rate = replace(_normalized_rate(), booking_url="https://www.booking.com/hotel/gr/aegean-view.html")

    upsert_property(connection, rate)

    statement, params = connection.calls[0]
    sql = " ".join(str(statement).split())
    assert "stars, booking_url )" in sql
    assert ":stars, :booking_url )" in sql
    assert "booking_url = COALESCE(EXCLUDED.booking_url, roomrate_properties.booking_url)" in sql
    assert params["booking_url"] == "https://www.booking.com/hotel/gr/aegean-view.html"
    # The identity of the property never depends on the URL.
    assert params["source_property_key"] == "property-1"


def test_upsert_property_binds_a_null_booking_url_when_the_rate_has_none():
    connection = FakeConnection()

    upsert_property(connection, _normalized_rate())

    _, params = connection.calls[0]
    assert params["booking_url"] is None


def test_upsert_package_writes_room_attributes_on_insert_and_conflict():
    from api.repositories.normalized_market_writer import upsert_package

    connection = FakeConnection()
    rate = replace(
        _normalized_rate(),
        room_attributes={"capacity": 2, "bed_hint": "double"},
    )

    upsert_package(connection, rate, UUID("00000000-0000-0000-0000-000000000998"))

    statement, params = connection.calls[0]
    sql = str(statement)
    assert "room_attributes" in sql
    assert "room_attributes = EXCLUDED.room_attributes" in sql
    assert params["room_attributes"] == {"capacity": 2, "bed_hint": "double"}


def test_upsert_package_writes_null_room_attributes_when_empty():
    from api.repositories.normalized_market_writer import upsert_package

    connection = FakeConnection()

    upsert_package(connection, _normalized_rate(), UUID("00000000-0000-0000-0000-000000000998"))

    _, params = connection.calls[0]
    assert params["room_attributes"] is None


RATE_PLAN_COLUMNS = (
    "discounted_price_per_night_eur",
    "discount_pct",
    "discount_label",
    "has_genius_discount",
    "cancellation_type",
    "payment_label",
    "rate_block_id",
)


def test_upsert_package_writes_the_rate_plan_columns_on_insert_and_conflict():
    """Spec 2026-09-29 §3: the plan facts ride every INSERT and, via EXCLUDED,
    every conflict update — so an idempotent re-run of the same batch rewrites
    the same values instead of erasing them."""
    from api.repositories.normalized_market_writer import upsert_package

    connection = FakeConnection()
    rate = replace(
        _normalized_rate(),
        discounted_price_per_night_eur=Decimal("73.60"),
        discount_pct=Decimal("8.0"),
        discount_label="-8%",
        has_genius_discount=True,
        cancellation_type="non_refundable",
        payment_label="Πληρωμή online",
        rate_block_id="123456789_0_2_0",
    )

    upsert_package(connection, rate, UUID("00000000-0000-0000-0000-000000000998"))
    upsert_package(connection, rate, UUID("00000000-0000-0000-0000-000000000998"))

    statement, params = connection.calls[0]
    sql = " ".join(str(statement).split())
    for column in RATE_PLAN_COLUMNS:
        assert f":{column}" in sql
        assert f"{column} = EXCLUDED.{column}" in sql
    assert params["discounted_price_per_night_eur"] == Decimal("73.60")
    assert params["discount_pct"] == Decimal("8.0")
    assert params["discount_label"] == "-8%"
    assert params["has_genius_discount"] is True
    assert params["cancellation_type"] == "non_refundable"
    assert params["payment_label"] == "Πληρωμή online"
    assert params["rate_block_id"] == "123456789_0_2_0"
    # The re-run binds byte-identical values: the upsert keeps them.
    assert connection.calls[1][1] == {**params, "id": connection.calls[1][1]["id"]}


def test_upsert_package_binds_null_plan_values_for_rates_without_them():
    from api.repositories.normalized_market_writer import upsert_package

    connection = FakeConnection()

    upsert_package(connection, _normalized_rate(), UUID("00000000-0000-0000-0000-000000000998"))

    _, params = connection.calls[0]
    # has_genius_discount included (review 2026-09-29): a rate without the
    # plan columns binds NULL everywhere so a legacy re-ingest keeps
    # rate_plan null instead of materializing False.
    for column in RATE_PLAN_COLUMNS:
        assert params[column] is None


def test_upsert_amenities_bulk_writes_dimension_links_and_cache_set_based():
    connection = FakeConnection()

    upsert_amenities_bulk(
        connection,
        {
            PROPERTY_A: {"WiFi", "Balcony"},
            PROPERTY_B: {"WiFi"},
        },
    )

    # Set-based writes: amenity dimension + property links + cache refresh,
    # NOT 2 statements per amenity per property.
    assert len(connection.calls) == 3

    dimension_sql = str(connection.calls[0][0])
    assert "INSERT INTO roomrate_amenities" in dimension_sql
    assert "unnest" in dimension_sql
    assert "ON CONFLICT (normalized_name) DO NOTHING" in dimension_sql
    dimension_params = connection.calls[0][1]
    assert dimension_params["names"] == ["Balcony", "WiFi"]
    assert len(dimension_params["names"]) == len(dimension_params["normalized_names"])

    links_sql = str(connection.calls[1][0])
    assert "INSERT INTO roomrate_property_amenities" in links_sql
    assert "unnest" in links_sql
    assert "ON CONFLICT (property_id, amenity_id) DO NOTHING" in links_sql
    links_params = connection.calls[1][1]
    assert len(links_params["property_ids"]) == 3  # one row per property/amenity pair
    assert len(links_params["property_ids"]) == len(links_params["normalized_names"])

    cache_sql = str(connection.calls[2][0])
    assert "amenities_cached" in cache_sql
    assert "string_agg" in cache_sql
    cache_params = connection.calls[2][1]
    assert sorted(cache_params["property_ids"]) == sorted([str(PROPERTY_A), str(PROPERTY_B)])


def test_upsert_amenities_bulk_is_noop_for_empty_input():
    connection = FakeConnection()

    upsert_amenities_bulk(connection, {})

    assert connection.calls == []


def test_write_normalized_rates_caches_scrape_run_and_bulks_amenities(monkeypatch):
    connection = FakeConnection()
    engine = FakeEngine(connection)
    monkeypatch.setattr("api.repositories.normalized_market_writer.get_engine", lambda *a, **k: engine)

    first_rate = _normalized_rate()
    second_rate = replace(
        first_rate,
        source_record_id="record-2",
        payload_hash="hash-2",
        amenities=("Pool",),
    )

    written = write_normalized_rates([first_rate, second_rate])

    assert written == 2
    assert engine.begin_calls == 1  # one transaction for the whole batch
    executed_sql = [str(statement) for statement, _ in connection.calls]
    # Same source_run_key → the scrape-run upsert must run exactly once.
    assert sum("INSERT INTO roomrate_scrape_runs" in sql for sql in executed_sql) == 1
    # Same source_property_key → the property upsert must run exactly once too.
    assert sum("INSERT INTO roomrate_properties " in sql for sql in executed_sql) == 1
    # Amenities are written once, set-based, at the end of the batch.
    assert sum("INSERT INTO roomrate_amenities" in sql for sql in executed_sql) == 1
    assert sum("INSERT INTO roomrate_property_amenities" in sql for sql in executed_sql) == 1
    assert "unnest" in executed_sql[-3]
    # No legacy row-by-row amenity upserts remain.
    assert not any("VALUES (:property_id, :amenity_id)" in sql for sql in executed_sql)


def test_write_normalized_rates_upserts_each_distinct_property_once(monkeypatch):
    connection = FakeConnection()
    engine = FakeEngine(connection)
    monkeypatch.setattr("api.repositories.normalized_market_writer.get_engine", lambda *a, **k: engine)

    hotel_a_double = _normalized_rate()
    hotel_a_suite = replace(hotel_a_double, source_record_id="record-2", payload_hash="hash-2")
    hotel_b = replace(
        hotel_a_double,
        source_record_id="record-3",
        payload_hash="hash-3",
        source_property_key="property-2",
        canonical_name="blue bay",
        display_name="Blue Bay",
    )

    write_normalized_rates([hotel_a_double, hotel_a_suite, hotel_b])

    executed_sql = [str(statement) for statement, _ in connection.calls]
    # Two distinct source_property_keys → exactly two property upserts, while
    # observations/packages/raw events stay per-row (3 each).
    assert sum("INSERT INTO roomrate_properties " in sql for sql in executed_sql) == 2
    assert sum("INSERT INTO roomrate_rate_observations" in sql for sql in executed_sql) == 3
    assert sum("INSERT INTO roomrate_room_packages" in sql for sql in executed_sql) == 3
    assert sum("INSERT INTO roomrate_raw_ingestion_events" in sql for sql in executed_sql) == 3


# ----------------------------------------------------------------------------
# Round 6: one scrape job is ONE run, whatever Booking city each hotel reports
# (the nearby-area scouts return Ιξιά, Κολύμπια... next to Φαληράκι)
# ----------------------------------------------------------------------------

JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
JOB_RUN_KEY = f"booking_com|job:{JOB_ID}|2026-06-15|2026-06-20|2|0|1"


class IdIssuingConnection(FakeConnection):
    """Hands out a fresh id per statement so run links can be asserted."""

    def __init__(self):
        super().__init__()
        self.issued_ids: list[UUID] = []

    def execute(self, statement, params):
        self.calls.append((statement, params))
        issued = uuid4()
        self.issued_ids.append(issued)
        return FakeScalarResult(issued)

    def params_of(self, table_insert: str) -> list[dict]:
        return [params for statement, params in self.calls if table_insert in str(statement)]

    def issued_for(self, table_insert: str) -> list[UUID]:
        return [
            issued
            for (statement, _), issued in zip(self.calls, self.issued_ids)
            if table_insert in str(statement)
        ]


def _city_rate(index: int, city: str) -> NormalizedRoomRate:
    """A hotel as the normalizer builds it: the run key carries its own city."""
    return replace(
        _normalized_rate(),
        source_record_id=f"record-{index}",
        payload_hash=f"hash-{index}",
        source_property_key=f"property-{index}",
        canonical_name=f"hotel {index}",
        display_name=f"Hotel {index}",
        destination=city,
        raw_destination=city,
        city=city,
        source_run_key=f"booking_com|{city.casefold()}|2026-06-15|2026-06-20|2|0|1|2026-05-10T10:00:00+00:00",
    )


def _three_city_batch() -> list[NormalizedRoomRate]:
    return [_city_rate(1, "Φαληράκι"), _city_rate(2, "Ιξιά"), _city_rate(3, "Κολύμπια")]


def _install(monkeypatch, connection: FakeConnection) -> FakeEngine:
    engine = FakeEngine(connection)
    monkeypatch.setattr("api.repositories.normalized_market_writer.get_engine", lambda *a, **k: engine)
    return engine


@pytest.mark.parametrize("scrape_job_id", [JOB_ID, str(JOB_ID)])
def test_write_normalized_rates_puts_a_whole_scrape_job_in_one_run(monkeypatch, scrape_job_id):
    connection = IdIssuingConnection()
    _install(monkeypatch, connection)

    write_normalized_rates(
        _three_city_batch(),
        scrape_job_id=scrape_job_id,
        raw_destination="Φαληράκι",
        canonical_destination="faliraki",
    )

    runs = connection.params_of("INSERT INTO roomrate_scrape_runs")
    assert len(runs) == 1
    assert runs[0]["source_run_key"] == JOB_RUN_KEY
    assert runs[0]["scrape_job_id"] == JOB_ID
    # The run is the job's search, not the first hotel's Booking city.
    assert (runs[0]["destination"], runs[0]["raw_destination"], runs[0]["canonical_destination"]) == (
        "Φαληράκι",
        "Φαληράκι",
        "faliraki",
    )
    [run_id] = connection.issued_for("INSERT INTO roomrate_scrape_runs")
    observations = connection.params_of("INSERT INTO roomrate_rate_observations")
    assert [params["scrape_run_id"] for params in observations] == [run_id, run_id, run_id]
    raw_events = connection.params_of("INSERT INTO roomrate_raw_ingestion_events")
    assert {(params["scrape_run_id"], params["source_run_id"]) for params in raw_events} == {(run_id, JOB_RUN_KEY)}
    # Property identity never changes: one upsert per hotel with its own key.
    properties = connection.params_of("INSERT INTO roomrate_properties ")
    assert [params["source_property_key"] for params in properties] == ["property-1", "property-2", "property-3"]
    assert [params["city"] for params in properties] == ["Φαληράκι", "Ιξιά", "Κολύμπια"]


def test_write_normalized_rates_job_run_falls_back_to_the_first_rate_destination(monkeypatch):
    connection = IdIssuingConnection()
    _install(monkeypatch, connection)

    write_normalized_rates(list(reversed(_three_city_batch())), scrape_job_id=JOB_ID)

    [run] = connection.params_of("INSERT INTO roomrate_scrape_runs")
    assert (run["destination"], run["raw_destination"]) == ("Κολύμπια", "Κολύμπια")
    assert run["source_run_key"] == JOB_RUN_KEY


def test_write_normalized_rates_rewrites_the_same_run_when_a_job_batch_is_written_again(monkeypatch):
    """Idempotent: the same job always upserts the same run key (ON CONFLICT
    updates it), even when a retried attempt scraped at a later time."""
    connection = IdIssuingConnection()
    _install(monkeypatch, connection)
    batch = _three_city_batch()
    retried = [replace(rate, observed_at=rate.observed_at + timedelta(minutes=9)) for rate in batch]

    write_normalized_rates(batch, scrape_job_id=JOB_ID, raw_destination="Φαληράκι")
    write_normalized_rates(batch, scrape_job_id=JOB_ID, raw_destination="Φαληράκι")
    write_normalized_rates(retried, scrape_job_id=JOB_ID, raw_destination="Φαληράκι")

    runs = connection.params_of("INSERT INTO roomrate_scrape_runs")
    # One run upsert per write, always the same key: the unique constraint
    # turns the repeats into updates of the same row.
    assert [run["source_run_key"] for run in runs] == [JOB_RUN_KEY, JOB_RUN_KEY, JOB_RUN_KEY]
    run_statement = next(
        statement for statement, _ in connection.calls if "INSERT INTO roomrate_scrape_runs" in str(statement)
    )
    assert "ON CONFLICT (account_id, provider, source_run_key) DO UPDATE SET" in " ".join(str(run_statement).split())


def test_write_normalized_rates_without_a_job_keeps_one_run_per_city(monkeypatch):
    """Backfill scripts and legacy callers pass no job: today's keys stay."""
    connection = IdIssuingConnection()
    _install(monkeypatch, connection)
    batch = _three_city_batch()

    write_normalized_rates(batch, raw_destination="Φαληράκι", canonical_destination="faliraki")

    runs = connection.params_of("INSERT INTO roomrate_scrape_runs")
    assert [run["source_run_key"] for run in runs] == [rate.source_run_key for rate in batch]
    assert [run["destination"] for run in runs] == ["Φαληράκι", "Ιξιά", "Κολύμπια"]
    assert all(run["scrape_job_id"] is None for run in runs)
    observations = connection.params_of("INSERT INTO roomrate_rate_observations")
    assert [params["scrape_run_id"] for params in observations] == connection.issued_for(
        "INSERT INTO roomrate_scrape_runs"
    )
