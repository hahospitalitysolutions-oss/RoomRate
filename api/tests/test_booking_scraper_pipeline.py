import json
import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pandas as pd
import pytest

from api.services.room_rates_normalizer import normalize_room_rate_row
from scraper.utils import _append_error_log
from scraper import (
    ActorRunError,
    ScraperConfig,
    build_config_from_args,
    fetch_deep_room_data,
    fetch_hotel_list,
    main,
    persist_results,
    process_and_flatten_data,
)
from scraper.actor import _retry_delay_seconds
from scraper.matching import _filter_by_room_name
from scraper.scout import _load_scout_cache, _save_scout_cache


def _dry_run_cli_args() -> list[str]:
    return [
        "--destination", "Faliraki",
        "--check-in", "2030-06-01",
        "--check-out", "2030-06-05",
        "--dry-run",
    ]


def _sentinel_payloads(caplog) -> list[dict]:
    prefix = "ROOMRATE_RESULT_SUMMARY "
    return [
        json.loads(record.message.split(prefix, 1)[1])
        for record in caplog.records
        if prefix in record.message
    ]


@pytest.mark.parametrize("exit_path", ["no_hotels", "no_raw_data", "empty_dataframe", "dry_run_success"])
def test_every_successful_cli_exit_emits_one_versioned_result_summary(monkeypatch, caplog, exit_path):
    monkeypatch.setattr("scraper.cli.build_client", lambda: object())
    hotels = _hotel_meta()

    def fake_fetch_hotel_lists(client, config, engine=None, progress=None, warnings=None):
        # A failed nearby scout is not fatal; it must still reach the API.
        warnings.append("nearby_scout_failed:Ιξιά")
        return [] if exit_path == "no_hotels" else hotels

    monkeypatch.setattr("scraper.cli.fetch_hotel_lists", fake_fetch_hotel_lists)
    monkeypatch.setattr(
        "scraper.cli.fetch_deep_room_data",
        lambda *args, **kwargs: [] if exit_path == "no_raw_data" else [{}],
    )
    if exit_path == "empty_dataframe":
        frame = pd.DataFrame()
    else:
        frame = pd.DataFrame([
            {
                "hotel_name": "Anonymous Hotel",
                "price_per_night_eur": 100.0,
                "room_type_category": "double",
                "room_type": "Double Room",
            }
        ])
    monkeypatch.setattr("scraper.cli.process_and_flatten_data", lambda *args, **kwargs: frame)

    with caplog.at_level("INFO", logger="roomrate.scraper"):
        main(_dry_run_cli_args())

    payloads = _sentinel_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["version"] == 1
    assert payloads[0]["rows_written"] == 0
    assert payloads[0]["warnings"] == ["nearby_scout_failed:Ιξιά"]


def test_a_radius_that_excludes_every_hotel_is_reported_on_the_empty_exit(monkeypatch, caplog):
    """Swapped coordinates put the origin ~1,000 km away: the scout finds hotels,
    the radius keeps none, and the summary must say why the market is empty.
    """

    def fake_scout_actor(client, actor_input, max_retries, retry_delay, label=""):
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
                "location": {"lat": "36.34", "lng": "28.2"},
            }
        ]

    def fail_deep_crawl(*args, **kwargs):
        raise AssertionError("nothing is inside the radius: the deep crawl must not run")

    monkeypatch.setattr("scraper.cli.build_client", lambda: object())
    monkeypatch.setattr("scraper.scout._run_actor", fake_scout_actor)
    monkeypatch.setattr("scraper.deep_crawl._run_actor", fail_deep_crawl)

    with caplog.at_level("INFO", logger="roomrate.scraper"):
        main([*_dry_run_cli_args(), "--origin-lat", "28.2", "--origin-lng", "36.34", "--radius-km", "10"])

    payloads = _sentinel_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["warnings"] == ["radius_excluded_all"]


def test_main_reports_progress_from_the_scouts_to_persistence(monkeypatch):
    """Round 6 §3.5: one reporter per run, built from the run's engine and job id."""
    reporters = []

    class RecordingProgress:
        def __init__(self, engine, scrape_job_id):
            self.engine = engine
            self.scrape_job_id = scrape_job_id
            self.updates: list[dict] = []
            reporters.append(self)

        def update(self, stage, done=None, total=None, **counts):
            self.updates.append({"stage": stage, "done": done, "total": total, **counts})
            return False

    def fake_scout_actor(client, actor_input, max_retries, retry_delay, label=""):
        if actor_input["search"] != "Faliraki":
            return []
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
                "location": {"lat": "36.34", "lng": "28.2"},
            }
        ]

    monkeypatch.setattr("scraper.cli.build_client", lambda: object())
    monkeypatch.setattr("scraper.cli.ProgressReporter", RecordingProgress)
    monkeypatch.setattr("scraper.scout._run_actor", fake_scout_actor)
    monkeypatch.setattr("scraper.deep_crawl._run_actor", lambda *args, **kwargs: _raw_property())

    main(
        [
            *_dry_run_cli_args(),
            "--scrape-job-id", "00000000-0000-0000-0000-000000000777",
            "--nearby-destination", "Ιξιά",
        ]
    )

    assert len(reporters) == 1
    reporter = reporters[0]
    # Dry run: no engine, so the real reporter would be a no-op.
    assert (reporter.engine, reporter.scrape_job_id) == (None, UUID("00000000-0000-0000-0000-000000000777"))
    assert [update["stage"] for update in reporter.updates] == [
        "scout", "scout", "scout", "scout", "deep_crawl", "deep_crawl", "persist",
    ]
    assert reporter.updates[0]["destinations_total"] == 2
    assert reporter.updates[3]["hotels_in_radius"] == 1
    assert reporter.updates[4:6] == [
        {"stage": "deep_crawl", "done": 0, "total": 1},
        {"stage": "deep_crawl", "done": 1, "total": 1},
    ]


def _hotel_meta() -> list[dict]:
    return [
        {
            "name": "Aegean View",
            "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
            "city": "Faliraki",
            "address": "Faliraki Center",
            "property_type": "Hotel",
            "latitude": 36.340001,
            "longitude": 28.200001,
            "stars": 3.0,
            "review_score": 8.8,
            "review_count": 120,
        }
    ]


def _raw_property() -> list[dict]:
    return [
        {
            "name": " Aegean View ",
            "url": "https://www.booking.com/hotel/gr/aegean-view.el.html?checkin=2026-06-15",
            "price": 400,
            "rooms": [
                {
                    "roomType": " Double Room ",
                    "roomsLeft": 2,
                    "facilities": [{"name": " WiFi "}, {"name": "wifi"}, {"name": "Balcony"}],
                    "options": [
                        {
                            "id": "pkg-1",
                            "persons": 1,
                            "price": 400,
                            "freeCancellation": True,
                            "yourChoices": ["Breakfast included"],
                        }
                    ],
                }
            ],
        }
    ]


def test_flattening_uses_date_aware_record_ids_and_cleans_agent_fields():
    first_config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )
    second_config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 7, 15),
        check_out=datetime(2026, 7, 20),
    )

    first = process_and_flatten_data(_raw_property(), _hotel_meta(), first_config)
    second = process_and_flatten_data(_raw_property(), _hotel_meta(), second_config)

    row = first.iloc[0].to_dict()
    assert row["record_id"] != second.iloc[0]["record_id"]
    assert row["hotel_name"] == "Aegean View"
    assert row["room_type"] == "Double Room"
    assert row["facilities"] == "Balcony|WiFi"
    assert row["free_cancellation"] == "Yes"
    assert row["guests"] == 2
    assert row["max_persons"] == 1  # option-level Apify `persons` field
    # True occupancy fields must reach the normalizer: without them it guesses
    # adults=guests/children=0/rooms=1 and every price-history/alert query
    # (which filters on the exact job occupancy) silently returns nothing.
    assert row["adults"] == 2
    assert row["children"] == 0
    assert row["rooms"] == 1


def test_flattening_occupancy_fields_follow_the_config():
    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
        adults=3,
        children=2,
        rooms=2,
    )

    df = process_and_flatten_data(_raw_property(), _hotel_meta(), config)
    row = df.iloc[0].to_dict()
    assert (row["adults"], row["children"], row["rooms"]) == (3, 2, 2)
    assert row["guests"] == 5

    normalized = normalize_room_rate_row(row)
    assert (normalized.adults, normalized.children, normalized.rooms) == (3, 2, 2)


def test_flattening_null_option_price_falls_back_to_base_price():
    raw = _raw_property()
    # "price": null is common for sold-out packages; dict.get's default only
    # applies when the key is MISSING, so null used to become €0.00.
    raw[0]["rooms"][0]["options"][0]["price"] = None

    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )
    df = process_and_flatten_data(raw, _hotel_meta(), config)

    row = df.iloc[0].to_dict()
    assert row["price_total_eur"] == 400  # property-level base price
    assert row["price_per_night_eur"] > 0


def test_a_sold_out_option_never_takes_another_rooms_price():
    """The property's «from» price (400) belongs to the cheapest room, not to this one."""
    raw = _raw_property()
    raw[0]["rooms"].append(
        {"roomType": "Suite", "options": [{"id": "suite-1", "persons": 2, "price": None}]}
    )

    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )
    df = process_and_flatten_data(raw, _hotel_meta(), config)

    assert df["room_type"].tolist() == ["Double Room"]


@pytest.mark.parametrize(
    ("choices", "meals"),
    [
        (["Breakfast included"], "Breakfast included"),
        (["Πολύ καλό πρωινό συμπεριλαμβάνεται"], "Πολύ καλό πρωινό συμπεριλαμβάνεται"),
        (["Πρωινό 12 € (προαιρετικό)"], "Δεν περιλαμβάνεται"),
        (["Breakfast €12 (optional)"], "Δεν περιλαμβάνεται"),
        (["Δεν περιλαμβάνεται γεύμα"], "Δεν περιλαμβάνεται"),
        ([], "Δεν περιλαμβάνεται"),
    ],
)
def test_flattening_keeps_only_the_meal_the_rate_includes(choices, meals):
    raw = _raw_property()
    # The property serves breakfast; that alone does not put it in the rate.
    raw[0]["breakfast"] = "Continental, Buffet"
    raw[0]["rooms"][0]["options"][0]["yourChoices"] = choices

    config = ScraperConfig(destination="Faliraki", check_in=datetime(2026, 6, 15), check_out=datetime(2026, 6, 20))
    df = process_and_flatten_data(raw, _hotel_meta(), config)

    assert df.iloc[0]["meals"] == meals


def test_flattening_skips_zero_price_options_instead_of_persisting_them():
    raw = _raw_property()
    raw[0]["price"] = None  # no base price either
    raw[0]["rooms"][0]["options"][0]["price"] = None

    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )
    df = process_and_flatten_data(raw, _hotel_meta(), config)

    # A €0.00 row would poison every MIN(price) in market stats and alerts.
    assert df.empty


def test_flattening_keeps_distinct_idless_options():
    raw = _raw_property()
    raw[0]["rooms"][0]["options"] = [
        {"id": None, "price": 400, "freeCancellation": True, "yourChoices": ["Breakfast included"]},
        {"price": 350, "freeCancellation": False, "yourChoices": []},
    ]

    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )
    df = process_and_flatten_data(raw, _hotel_meta(), config)

    # Without the per-option index fallback both options collapsed into one
    # record_id and the second rate variant was silently dropped as duplicate.
    assert len(df) == 2
    assert df["record_id"].nunique() == 2
    assert sorted(df["price_total_eur"].tolist()) == [350, 400]
    # Rate plans (spec 2026-09-29 §2): the plan identity column stores the
    # option id or the same per-option fallback the record_id already uses.
    assert sorted(df["rate_block_id"].tolist()) == ["opt0", "opt1"]


def _rate_plan_config() -> ScraperConfig:
    return ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )


def test_flattening_extracts_rate_plan_fields_for_a_discounted_genius_option():
    """Spec 2026-09-29 §2: discount, Genius, cancellation and payment per option."""
    raw = _raw_property()
    raw[0]["rooms"][0]["options"][0].update(
        {
            "discount": {"discountedPrice": 368, "text": "- 8%", "description": "Έκπτωση"},
            "hasGeniusDiscount": True,
            "cancellationType": "non_refundable",
            "yourChoices": ["Breakfast included", "Μη επιστρέψιμη τιμή", "Πληρωμή online"],
        }
    )

    row = process_and_flatten_data(raw, _hotel_meta(), _rate_plan_config()).iloc[0].to_dict()

    # 368 EUR for 5 nights; the MAIN price column keeps its historical meaning.
    assert row["discounted_price_per_night_eur"] == 73.6
    assert row["price_per_night_eur"] == 80.0
    assert row["discount_pct"] == 8.0  # parsed from «- 8%»
    assert row["discount_label"] == "-8%"
    assert bool(row["has_genius_discount"]) is True
    assert row["cancellation_type"] == "non_refundable"
    assert row["payment_label"] == "Πληρωμή online"
    assert row["rate_block_id"] == "pkg-1"


def test_flattening_rate_plan_defaults_without_discount_or_genius():
    raw = _raw_property()
    raw[0]["rooms"][0]["options"][0]["discount"] = None  # the actor sends null

    row = process_and_flatten_data(raw, _hotel_meta(), _rate_plan_config()).iloc[0].to_dict()

    # No discount: the discounted price defaults to the main price.
    assert row["discounted_price_per_night_eur"] == row["price_per_night_eur"] == 80.0
    assert row["discount_pct"] is None
    assert row["discount_label"] is None
    assert bool(row["has_genius_discount"]) is False
    assert row["cancellation_type"] is None
    assert row["payment_label"] is None
    assert row["rate_block_id"] == "pkg-1"


def test_flattening_discount_pct_is_computed_when_the_text_is_missing():
    raw = _raw_property()
    raw[0]["rooms"][0]["options"][0]["discount"] = {"discountedPrice": 360}

    row = process_and_flatten_data(raw, _hotel_meta(), _rate_plan_config()).iloc[0].to_dict()

    assert row["discounted_price_per_night_eur"] == 72.0
    assert row["discount_pct"] == 10.0  # round((1 - 360/400) * 100, 1)
    assert row["discount_label"] is None


@pytest.mark.parametrize(
    "discount",
    [
        {"text": "- 8%"},  # discountedPrice missing entirely
        {"discountedPrice": None, "text": "- 8%"},
        {"discountedPrice": 0, "text": "- 8%"},
        {"discountedPrice": 400, "text": "- 8%"},  # equal to the main price
    ],
)
def test_flattening_drops_the_discount_chip_without_a_real_price_gap(discount):
    """Review 2026-09-29: a discount object whose discountedPrice is
    missing/zero (or equal to the main price) leaves the discounted price
    EQUAL to the main one — the row must then carry NULL discount_pct and
    discount_label, never a «-8%» chip without a real gap."""
    raw = _raw_property()
    raw[0]["rooms"][0]["options"][0]["discount"] = discount

    row = process_and_flatten_data(raw, _hotel_meta(), _rate_plan_config()).iloc[0].to_dict()

    assert row["discounted_price_per_night_eur"] == row["price_per_night_eur"] == 80.0
    assert row["discount_pct"] is None
    assert row["discount_label"] is None


@pytest.mark.parametrize(
    "choice, expected",
    [
        ("Πληρωμή online", "Πληρωμή online"),
        ("πληρωμή online με πιστωτική κάρτα", "Πληρωμή online"),
        ("Πληρωμή στο κατάλυμα", "Πληρωμή στο κατάλυμα"),
        ("Δωρεάν ακύρωση", None),
    ],
)
def test_flattening_detects_the_payment_label_from_your_choices(choice, expected):
    raw = _raw_property()
    raw[0]["rooms"][0]["options"][0]["yourChoices"] = ["Breakfast included", choice]

    row = process_and_flatten_data(raw, _hotel_meta(), _rate_plan_config()).iloc[0].to_dict()

    assert row["payment_label"] == expected


def test_payload_max_persons_fills_capacity_when_room_name_says_nothing():
    raw = [
        {
            "name": "Aegean View",
            "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
            "price": 300,
            "rooms": [
                {
                    "roomType": "Mystery Offer",
                    "roomsLeft": 1,
                    "facilities": [{"name": "WiFi"}],
                    "options": [
                        {
                            "id": "pkg-9",
                            "persons": 3,
                            "price": 300,
                            "freeCancellation": False,
                            "yourChoices": [],
                        }
                    ],
                }
            ],
        }
    ]
    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )

    df = process_and_flatten_data(raw, _hotel_meta(), config)
    row = df.iloc[0].to_dict()
    assert row["max_persons"] == 3

    # End-to-end: the flattened row becomes package_payload, so the payload
    # capacity fills the gap left by the uninformative room label.
    normalized = normalize_room_rate_row(row)
    assert normalized.room_attributes["capacity"] == 3


def test_room_label_capacity_wins_over_payload_max_persons():
    # _raw_property is a "Double Room" with option persons=1: the label wins.
    config = ScraperConfig(
        destination="Faliraki",
        check_in=datetime(2026, 6, 15),
        check_out=datetime(2026, 6, 20),
    )

    df = process_and_flatten_data(_raw_property(), _hotel_meta(), config)
    normalized = normalize_room_rate_row(df.iloc[0].to_dict())

    assert normalized.room_attributes["capacity"] == 2


def test_persist_results_dry_run_does_not_write(monkeypatch):
    df = process_and_flatten_data(_raw_property(), _hotel_meta(), ScraperConfig())
    output_path = Path("tmp/test_scraper_dry_run.csv")
    output_path.parent.mkdir(exist_ok=True)
    output_path.unlink(missing_ok=True)
    # one-adult job: the fixture's 1-person package must survive the capacity filter
    config = ScraperConfig(dry_run=True, output_csv=str(output_path), adults=1)

    def fail_write(*args, **kwargs):
        raise AssertionError("dry-run should not write to the database")

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fail_write)

    result = persist_results(df, config)

    assert result.normalized_written == 0
    assert not output_path.exists()


def test_persist_results_writes_normalized_rates(monkeypatch):
    df = process_and_flatten_data(_raw_property(), _hotel_meta(), ScraperConfig())
    output_path = Path("tmp/test_scraper_normalized.csv")
    output_path.parent.mkdir(exist_ok=True)
    output_path.unlink(missing_ok=True)
    config = ScraperConfig(
        output_csv=str(output_path),
        write_normalized=True,
        # one-adult job: the fixture's 1-person package must survive the capacity filter
        adults=1,
    )
    calls = {}

    def fake_normalize(row, provider="booking_com"):
        calls.setdefault("normalized_rows", []).append((row, provider))
        return row

    def fake_write(
        rates,
        account_id,
        ingestion_source="scraper",
        scrape_job_id=None,
        raw_destination=None,
        canonical_destination=None,
    ):
        calls["written_rates"] = list(rates)
        calls["account_id"] = account_id
        calls["ingestion_source"] = ingestion_source
        calls["scrape_job_id"] = scrape_job_id
        calls["raw_destination"] = raw_destination
        calls["canonical_destination"] = canonical_destination
        return len(rates)

    monkeypatch.setattr("scraper.persistence.normalize_room_rate_row", fake_normalize)
    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fake_write)

    result = persist_results(df, config)

    assert result.normalized_written == 1
    assert str(calls["account_id"]) == "00000000-0000-0000-0000-000000000001"
    assert calls["ingestion_source"] == "scraper"
    assert calls["scrape_job_id"] is None
    assert calls["raw_destination"] == config.destination
    assert calls["canonical_destination"] == "faliraki"
    assert calls["normalized_rows"][0][1] == "booking_com"
    assert calls["written_rates"][0]["hotel_name"] == "Aegean View"
    output_path.unlink(missing_ok=True)


def test_persist_results_passes_scrape_job_id_to_normalized_writer(monkeypatch):
    df = process_and_flatten_data(_raw_property(), _hotel_meta(), ScraperConfig())
    scrape_job_id = UUID("00000000-0000-0000-0000-000000000777")
    config = ScraperConfig(
        output_csv="tmp/test_scraper_job_link.csv",
        write_normalized=True,
        scrape_job_id=scrape_job_id,
        # one-adult job: the fixture's 1-person package must survive the capacity filter
        adults=1,
    )
    calls = {}

    monkeypatch.setattr("scraper.persistence.normalize_room_rate_row", lambda row, provider="booking_com": row)

    def fake_write(
        rates,
        account_id,
        ingestion_source="scraper",
        scrape_job_id=None,
        raw_destination=None,
        canonical_destination=None,
    ):
        calls["scrape_job_id"] = scrape_job_id
        calls["raw_destination"] = raw_destination
        calls["canonical_destination"] = canonical_destination
        return len(list(rates))

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fake_write)

    persist_results(df, config)

    assert calls["scrape_job_id"] == scrape_job_id
    assert calls["raw_destination"] == config.destination
    assert calls["canonical_destination"] == "faliraki"
    Path(config.output_csv).unlink(missing_ok=True)


def test_build_config_from_args_defaults_to_normalized_only():
    config = build_config_from_args(
        [
            "--destination",
            "Rhodes",
            "--check-in",
            "2026-06-15",
            "--check-out",
            "2026-06-20",
            "--dry-run",
        ]
    )

    assert config.destination == "Rhodes"
    assert str(config.account_id) == "00000000-0000-0000-0000-000000000001"
    assert config.check_in.strftime("%Y-%m-%d") == "2026-06-15"
    assert config.check_out.strftime("%Y-%m-%d") == "2026-06-20"
    assert config.dry_run is True
    assert config.write_normalized is True


def test_build_config_from_args_accepts_account_id():
    config = build_config_from_args(
        [
            "--account-id",
            "00000000-0000-0000-0000-000000000123",
            "--destination",
            "Santorini",
            "--check-in",
            "2026-07-01",
            "--check-out",
            "2026-07-05",
        ]
    )

    assert str(config.account_id) == "00000000-0000-0000-0000-000000000123"


def test_scraper_dotenv_loader_accepts_utf8_bom(monkeypatch, tmp_path):
    from scraper import clients

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("\ufeffAPIFY_TOKEN=apify_test_token\n", encoding="utf-8")
    monkeypatch.delenv("APIFY_TOKEN", raising=False)

    loaded = clients._load_scraper_dotenv(str(dotenv_path))

    assert loaded is True
    assert os.getenv("APIFY_TOKEN") == "apify_test_token"


def test_scraper_package_resolves_dotenv_from_project_root():
    from scraper import clients

    expected_path = Path(__file__).resolve().parents[2] / ".env"

    assert clients._script_dir_env == expected_path


def test_build_config_from_args_accepts_scrape_job_id():
    config = build_config_from_args(
        [
            "--account-id",
            "00000000-0000-0000-0000-000000000123",
            "--scrape-job-id",
            "00000000-0000-0000-0000-000000000777",
            "--destination",
            "Santorini",
            "--check-in",
            "2026-07-01",
            "--check-out",
            "2026-07-05",
        ]
    )

    assert str(config.scrape_job_id) == "00000000-0000-0000-0000-000000000777"


def test_build_config_from_args_accepts_required_amenities():
    config = build_config_from_args(
        [
            "--destination",
            "Santorini",
            "--check-in",
            "2026-07-01",
            "--check-out",
            "2026-07-05",
            "--required-amenity",
            " WiFi ",
            "--required-amenity",
            "Pool",
        ]
    )

    assert config.required_amenities == ("wifi", "pool")


def test_build_config_from_args_accepts_room_catalog_matching_filters():
    config = build_config_from_args(
        [
            "--destination",
            "Santorini",
            "--check-in",
            "2026-07-01",
            "--check-out",
            "2026-07-05",
            "--room-name-query",
            " Suite with Private Pool ",
            "--required-meal",
            " Very good breakfast included ",
            "--required-free-cancellation",
            " Yes ",
        ]
    )

    assert config.room_name_query == "Suite with Private Pool"
    assert config.required_meal == "very good breakfast included"
    assert config.required_free_cancellation == "yes"


def test_persist_results_filters_by_required_amenities_before_writes(monkeypatch):
    df = process_and_flatten_data(_raw_property(), _hotel_meta(), ScraperConfig())
    output_path = Path("tmp/test_required_amenities.csv")
    output_path.unlink(missing_ok=True)
    config = ScraperConfig(
        dry_run=True,
        output_csv=str(output_path),
        required_amenities=("pool",),
        # one-adult job: the fixture's 1-person package must survive the capacity filter
        adults=1,
    )

    def fail_write(*args, **kwargs):
        raise AssertionError("dry-run should not write to the database")

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fail_write)

    result = persist_results(df, config)

    assert result.rows_seen == 1
    assert result.normalized_written == 0
    assert not output_path.exists()


def test_persist_results_filters_by_room_name_when_exact_catalog_match_exists(monkeypatch):
    df = pd.concat(
        [
            process_and_flatten_data(_raw_property(), _hotel_meta(), ScraperConfig()),
            process_and_flatten_data(
                [
                    {
                        "name": "Aegean View",
                        "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
                        "price": 700,
                        "rooms": [
                            {
                                "roomType": "Suite with Private Pool",
                                "roomsLeft": 1,
                                "facilities": [{"name": "Private pool"}, {"name": "WiFi"}],
                                "options": [
                                    {
                                        "id": "pkg-2",
                                        "persons": 2,
                                        "price": 700,
                                        "freeCancellation": True,
                                        "yourChoices": ["Breakfast included"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                _hotel_meta(),
                ScraperConfig(),
            ),
        ],
        ignore_index=True,
    )
    output_path = Path("tmp/test_room_name_filter.csv")
    output_path.unlink(missing_ok=True)
    config = ScraperConfig(
        dry_run=True,
        output_csv=str(output_path),
        job_type="competitor_search",
        room_type_category="suite",
        room_name_query="Suite with Private Pool",
        # one-adult job: the 1-person Double Room must reach the room-name filter
        adults=1,
    )

    def fail_write(*args, **kwargs):
        raise AssertionError("dry-run should not write to the database")

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fail_write)

    result = persist_results(df, config)

    assert result.rows_seen == 1
    assert not output_path.exists()


def _greek_bed_variant_property() -> list[dict]:
    """The two labels Booking's Greek listings really use for one 2-bed room.

    The first is bucketed `double`, the second `twin` (the twin keywords win),
    yet a hotelier comparing either one wants to see both.
    """
    return [
        {
            "name": "Aegean View",
            "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
            "price": 300,
            "rooms": [
                {
                    "roomType": "Deluxe Δίκλινο Δωμάτιο με θέα στη Θάλασσα",
                    "roomsLeft": 3,
                    "facilities": [{"name": "WiFi"}],
                    "options": [{"id": "pkg-double", "persons": 2, "price": 300, "yourChoices": []}],
                },
                {
                    "roomType": "Δίκλινο Δωμάτιο με 1 Διπλό ή 2 Μονά Κρεβάτια και Μερική Θέα στη Θάλασσα",
                    "roomsLeft": 2,
                    "facilities": [{"name": "WiFi"}],
                    "options": [{"id": "pkg-twin", "persons": 2, "price": 340, "yourChoices": []}],
                },
                {
                    "roomType": "Σουίτα με Ιδιωτική Πισίνα",
                    "roomsLeft": 1,
                    "facilities": [{"name": "Private pool"}],
                    "options": [{"id": "pkg-suite", "persons": 2, "price": 900, "yourChoices": []}],
                },
            ],
        }
    ]


def test_persist_results_keeps_every_non_single_category_for_competitor_search(monkeypatch):
    """Round 6 §3.4: the double/twin pool filter is gone — the suite stays as «Παρόμοιο»."""
    df = process_and_flatten_data(_greek_bed_variant_property(), _hotel_meta(), ScraperConfig())
    config = ScraperConfig(dry_run=True, job_type="competitor_search", room_type_category="double")

    def fail_write(*args, **kwargs):
        raise AssertionError("dry-run should not write to the database")

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fail_write)

    result = persist_results(df, config)

    assert result.rows_seen == 3
    assert result.result_summary["filter_counts"] == {
        "single_rooms": {"before": 3, "after": 3},
        "capacity": {"before": 3, "after": 3},
    }


def test_persist_results_drops_single_rooms_and_rooms_too_small_for_the_party():
    config = ScraperConfig(dry_run=True, job_type="competitor_search", adults=2)
    df = pd.DataFrame(
        [
            {"room_type_category": "single", "room_type": "Μονόκλινο", "max_persons": 1},
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 1},  # known 1 < 2 adults
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 0},  # unknown capacity stays
            {"room_type_category": None, "room_type": "Mystery", "max_persons": 3},  # unknown category stays
            {"room_type_category": "apartment", "room_type": "Διαμέρισμα", "max_persons": 4},
        ]
    )

    result = persist_results(df, config)

    assert result.rows_seen == 3
    assert result.result_summary["filter_counts"] == {
        "single_rooms": {"before": 5, "after": 4},
        "capacity": {"before": 4, "after": 3},
    }


@pytest.mark.parametrize(
    ("adults", "rooms", "rates_kept"),
    [
        (4, 2, 2),  # 2 adults per room: the 2-person rate fits, only the 1-person rate goes
        (2, 1, 2),  # one room for 2 adults: the 1-person rate still goes
        (3, 2, 2),  # ceil(3 / 2) = 2 per room, never rounded down to 1
    ],
)
def test_capacity_filter_compares_the_rate_occupancy_per_room(adults, rooms, rates_kept):
    """max_persons is Booking's persons PER ROOM for the rate, not for the party."""
    config = ScraperConfig(dry_run=True, job_type="competitor_search", adults=adults, rooms=rooms)
    df = pd.DataFrame(
        [
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 2},
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 1},
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 0},  # unknown stays
        ]
    )

    result = persist_results(df, config)

    assert result.result_summary["filter_counts"]["capacity"] == {"before": 3, "after": rates_kept}


def test_persist_results_keeps_single_rooms_for_a_one_adult_search():
    """A solo traveller competes with single rooms, so they are not cut."""
    config = ScraperConfig(dry_run=True, job_type="competitor_search", adults=1)
    df = pd.DataFrame(
        [
            {"room_type_category": "single", "room_type": "Μονόκλινο", "max_persons": 1},
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 2},
        ]
    )

    result = persist_results(df, config)

    assert result.rows_seen == 2
    assert "single_rooms" not in result.result_summary["filter_counts"]
    assert result.result_summary["filter_counts"]["capacity"] == {"before": 2, "after": 2}


@pytest.mark.parametrize(
    ("adults", "rooms", "expected_filter_counts"),
    [
        # One adult per room: every room is a single occupancy, singles stay.
        (2, 2, {"capacity": {"before": 2, "after": 2}}),
        # Two adults share the room: a single cannot host them.
        (2, 1, {"single_rooms": {"before": 2, "after": 1}, "capacity": {"before": 1, "after": 1}}),
    ],
)
def test_single_rooms_are_dropped_only_when_a_room_must_host_two_adults(adults, rooms, expected_filter_counts):
    config = ScraperConfig(dry_run=True, job_type="competitor_search", adults=adults, rooms=rooms)
    df = pd.DataFrame(
        [
            {"room_type_category": "single", "room_type": "Μονόκλινο", "max_persons": 1},
            {"room_type_category": "double", "room_type": "Δίκλινο", "max_persons": 2},
        ]
    )

    result = persist_results(df, config)

    assert result.result_summary["filter_counts"] == expected_filter_counts


def test_persist_results_round6_filters_are_skipped_for_room_discovery():
    config = ScraperConfig(dry_run=True, job_type="owned_property_room_discovery", adults=2)
    df = pd.DataFrame([{"room_type_category": "single", "room_type": "Μονόκλινο", "max_persons": 1}])

    result = persist_results(df, config)

    # The owner's single room stays in the catalog: it has no rate for 2.
    assert result.rows_seen == 1
    assert result.result_summary["filter_counts"] == {"room_capacity": {"before": 1, "after": 1}}


def test_room_discovery_prices_each_room_for_the_searched_party(monkeypatch):
    """Booking's 1-person rate (80 €) is not the 2-adult price (100 €) of the room."""
    config = ScraperConfig(job_type="owned_property_room_discovery", adults=2, output_csv="/dev/null")
    written: list = []
    monkeypatch.setattr("scraper.persistence.write_normalized_rates", lambda rates, **_: written.extend(rates) or len(rates))
    monkeypatch.setattr("scraper.persistence.normalize_room_rate_row", lambda row, provider: row)
    url = "https://www.booking.com/hotel/gr/sabbal.html"
    df = pd.DataFrame(
        [
            {"hotel_url": url, "room_type": "Διαμέρισμα 1/3", "max_persons": 1, "price_per_night_eur": 80.0},
            {"hotel_url": url, "room_type": "Διαμέρισμα 2/3", "max_persons": 2, "price_per_night_eur": 100.0},
            {"hotel_url": url, "room_type": "Studio", "max_persons": 1, "price_per_night_eur": 100.0},
            {"hotel_url": url, "room_type": "Studio", "max_persons": 0, "price_per_night_eur": 110.0},
            {"hotel_url": url, "room_type": "Μονόκλινο", "max_persons": 1, "price_per_night_eur": 50.0},
        ]
    )

    result = persist_results(df, config)

    kept = sorted((row["room_type"], row["price_per_night_eur"]) for row in written)
    assert kept == [("Studio", 110.0), ("Διαμέρισμα 2/3", 100.0), ("Μονόκλινο", 50.0)]
    assert result.result_summary["filter_counts"] == {"room_capacity": {"before": 5, "after": 3}}


@pytest.mark.parametrize(("adults", "rooms", "kept"), [(1, 1, 3), (2, 1, 2), (4, 2, 2), (3, 1, 1)])
def test_room_discovery_compares_rates_with_the_party_per_room(adults, rooms, kept):
    config = ScraperConfig(dry_run=True, job_type="owned_property_room_discovery", adults=adults, rooms=rooms)
    df = pd.DataFrame(
        [
            {"room_type": "Διαμέρισμα", "max_persons": 1},
            {"room_type": "Διαμέρισμα", "max_persons": 2},
            {"room_type": "Διαμέρισμα", "max_persons": 0},  # unknown capacity always stays
        ]
    )

    result = persist_results(df, config)

    # (3, 1): no rate is known to fit 3, but the unknown one might, so the
    # known 1- and 2-person rates go.
    assert result.result_summary["filter_counts"]["room_capacity"] == {"before": 3, "after": kept}


def test_persist_results_records_the_filter_that_emptied_the_pipeline():
    config = ScraperConfig(room_type_category="double", room_name_query="Exact catalog name", dry_run=True)
    df = pd.DataFrame([{"room_type_category": "single", "room_type": "Single", "max_persons": 1}])

    result = persist_results(df, config)

    assert result.result_summary["rows_seen"] == 1
    assert result.result_summary["filter_counts"] == {"single_rooms": {"before": 1, "after": 0}}
    assert result.result_summary["rows_written"] == 0
    # Always a list: the API appends its own warnings before completing the job.
    assert result.result_summary["warnings"] == []


def test_persist_results_reports_a_later_rate_filter_after_the_round6_filters_survive():
    config = ScraperConfig(room_type_category="double", required_meal="dinner included", dry_run=True)
    df = pd.DataFrame(
        [
            {"room_type_category": "double", "meals": "Breakfast included"},
            {"room_type_category": "twin", "meals": "No meals"},
        ]
    )

    result = persist_results(df, config)

    # No max_persons column: the capacity stage records counts and keeps every row.
    assert result.result_summary["filter_counts"] == {
        "single_rooms": {"before": 2, "after": 2},
        "capacity": {"before": 2, "after": 2},
        "meal": {"before": 2, "after": 0},
    }


def test_room_name_filter_is_bucket_blind(monkeypatch):
    """The category no longer filters before storage (Round 6 §3.4).

    The untouched name filter matches on the compacted room label alone, so a
    twin-bucketed room that name-matches survives on exactly the same terms as
    the double one — and a room of EITHER bucket that does not name-match is
    still dropped.
    """
    raw = [
        {
            "name": "Aegean View",
            "url": "https://www.booking.com/hotel/gr/aegean-view.el.html",
            "price": 200,
            "rooms": [
                # `double`, name-matches by containing the query.
                {
                    "roomType": "Deluxe Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
                    "options": [{"id": "pkg-a", "price": 210, "yourChoices": []}],
                },
                # `twin` (the "2 μονά" keywords win), name-matches the same way.
                {
                    "roomType": "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα και 2 Μονά Κρεβάτια",
                    "options": [{"id": "pkg-b", "price": 230, "yourChoices": []}],
                },
                # `double`, does NOT name-match.
                {
                    "roomType": "Δίκλινο Δωμάτιο με Θέα στον Κήπο",
                    "options": [{"id": "pkg-c", "price": 180, "yourChoices": []}],
                },
                # `twin`, does NOT name-match.
                {
                    "roomType": "Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια και Θέα στον Κήπο",
                    "options": [{"id": "pkg-d", "price": 190, "yourChoices": []}],
                },
            ],
        }
    ]
    df = process_and_flatten_data(raw, _hotel_meta(), ScraperConfig())
    assert sorted(df["room_type_category"].unique()) == ["double", "twin"]

    output_path = Path("tmp/test_pool_then_room_name_filter.csv")
    output_path.parent.mkdir(exist_ok=True)
    output_path.unlink(missing_ok=True)
    config = ScraperConfig(
        output_csv=str(output_path),
        job_type="competitor_search",
        room_type_category="double",
        room_name_query="Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
        write_normalized=False,
    )

    result = persist_results(df, config)

    written = pd.read_csv(output_path, encoding="utf-8-sig")
    assert result.rows_seen == 2
    assert sorted(written["room_type"].tolist()) == [
        "Deluxe Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
        "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα και 2 Μονά Κρεβάτια",
    ]
    assert sorted(written["room_type_category"].tolist()) == ["double", "twin"]
    output_path.unlink(missing_ok=True)


def test_room_name_filter_matches_similar_catalog_labels_without_exact_text():
    df = pd.DataFrame(
        [
            {
                "hotel_name": "Aegean View",
                "room_type": "Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια και Θέα στην Πισίνα",
            },
            {"hotel_name": "Aegean View", "room_type": "Σουίτα με Ιδιωτική Πισίνα"},
        ]
    )

    result = _filter_by_room_name(
        df,
        "Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια, Ντους και Μπαλκόνι",
    )

    assert result["room_type"].tolist() == ["Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια και Θέα στην Πισίνα"]


def test_room_name_filter_chooses_the_best_match_tier_per_hotel_for_live_four_to_two_case():
    """Το exact ενός ξενοδοχείου δεν πρέπει να εξαφανίζει άλλο ανταγωνιστή."""
    df = pd.DataFrame(
        [
            {
                "hotel_name": "Lago Beach Living",
                "room_type": "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
                "package_id": "lago-flex",
            },
            {
                "hotel_name": "Lago Beach Living",
                "room_type": "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
                "package_id": "lago-nonref",
            },
            {
                "hotel_name": "Atalanti Boutique Hotel",
                "room_type": "Deluxe Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
                "package_id": "atalanti-flex",
            },
            {
                "hotel_name": "Atalanti Boutique Hotel",
                "room_type": "Deluxe Δίκλινο Δωμάτιο με Θέα στη Θάλασσα",
                "package_id": "atalanti-nonref",
            },
        ]
    )

    result = _filter_by_room_name(df, "Δίκλινο Δωμάτιο με Θέα στη Θάλασσα")

    assert result["package_id"].tolist() == [
        "lago-flex",
        "lago-nonref",
        "atalanti-flex",
        "atalanti-nonref",
    ]


def test_room_name_filter_uses_exact_partial_fuzzy_and_fallback_independently_per_hotel():
    df = pd.DataFrame(
        [
            {"hotel_name": "Exact", "room_type": "Suite Private Pool", "offer": "exact"},
            {"hotel_name": "Exact", "room_type": "Suite Garden View", "offer": "exact-weaker"},
            {"hotel_name": "Partial", "room_type": "Deluxe Suite Private Pool", "offer": "partial"},
            {"hotel_name": "Partial", "room_type": "Standard Double", "offer": "partial-weaker"},
            {"hotel_name": "Fuzzy", "room_type": "Suite Pool and Balcony", "offer": "fuzzy"},
            {"hotel_name": "Fuzzy", "room_type": "Standard Double", "offer": "fuzzy-weaker"},
            {"hotel_name": "Fallback", "room_type": "Standard Double", "offer": "fallback-a"},
            {"hotel_name": "Fallback", "room_type": "Twin Garden View", "offer": "fallback-b"},
        ]
    )

    result = _filter_by_room_name(df, "Suite Private Pool")

    assert result["offer"].tolist() == [
        "exact",
        "partial",
        "fuzzy",
        "fallback-a",
        "fallback-b",
    ]


def test_room_name_filter_prefers_reverse_partial_over_fuzzy_within_one_hotel():
    df = pd.DataFrame(
        [
            {
                "hotel_name": "Aegean View",
                "room_type": "Suite Private Pool",
                "offer": "reverse-partial",
            },
            {
                "hotel_name": "Aegean View",
                "room_type": "Deluxe Suite Pool and Balcony",
                "offer": "fuzzy",
            },
            {
                "hotel_name": "Aegean View",
                "room_type": "Standard Double",
                "offer": "fallback",
            },
        ]
    )

    result = _filter_by_room_name(df, "Deluxe Suite Private Pool Sea View")

    assert result["offer"].tolist() == ["reverse-partial"]


def test_flattened_missing_names_use_distinct_hotel_urls_for_per_property_matching():
    raw = [
        {
            "url": "https://www.booking.com/hotel/gr/anonymous-one.html?checkin=2026-06-15",
            "rooms": [
                {
                    "roomType": "Double Room",
                    "options": [{"id": "one-flex", "price": 120, "yourChoices": []}],
                }
            ],
        },
        {
            "url": "https://www.booking.com/hotel/gr/anonymous-two.html?checkin=2026-06-15",
            "rooms": [
                {
                    "roomType": "Twin Garden View",
                    "options": [{"id": "two-flex", "price": 130, "yourChoices": []}],
                }
            ],
        },
    ]
    hotel_meta = [
        {"url": "https://www.booking.com/hotel/gr/anonymous-one.html"},
        {"url": "https://www.booking.com/hotel/gr/anonymous-two.html"},
    ]
    flattened = process_and_flatten_data(raw, hotel_meta, ScraperConfig())

    result = _filter_by_room_name(flattened, "Double Room")

    assert result["hotel_url"].tolist() == [
        "https://www.booking.com/hotel/gr/anonymous-one.html",
        "https://www.booking.com/hotel/gr/anonymous-two.html",
    ]
    assert result["room_type"].tolist() == ["Double Room", "Twin Garden View"]


def test_flattening_keeps_same_named_same_package_rooms_from_distinct_urls():
    raw = [
        {
            "url": f"https://www.booking.com/hotel/gr/anonymous-{suffix}.html",
            "rooms": [
                {
                    "roomType": "Double Room",
                    "options": [{"id": "shared-option", "price": price, "yourChoices": []}],
                }
            ],
        }
        for suffix, price in (("one", 120), ("two", 130))
    ]
    hotel_meta = [{"url": item["url"]} for item in raw]

    flattened = process_and_flatten_data(raw, hotel_meta, ScraperConfig())

    assert len(flattened) == 2
    assert flattened["record_id"].nunique() == 2
    assert flattened["hotel_url"].nunique() == 2


def test_room_name_filter_uses_url_to_separate_same_named_properties():
    """Ίδιο display name σε δύο URLs δεν είναι απόδειξη ίδιου καταλύματος."""
    df = pd.DataFrame(
        [
            {
                "hotel_name": "Aegean Hotel",
                "hotel_url": "https://www.booking.com/hotel/gr/aegean-one.html",
                "room_type": "Double Room",
                "offer": "first-exact",
            },
            {
                "hotel_name": "Aegean Hotel",
                "hotel_url": "https://www.booking.com/hotel/gr/aegean-two.html",
                "room_type": "Twin Garden View",
                "offer": "second-fallback",
            },
        ]
    )

    result = _filter_by_room_name(df, "Double Room")

    assert result["offer"].tolist() == ["first-exact", "second-fallback"]


def test_room_name_filter_preserves_input_order_duplicate_offers_and_duplicate_indices():
    df = pd.DataFrame(
        [
            {"hotel_name": "A", "room_type": "Deluxe Double Room", "offer": "a"},
            {"hotel_name": "B", "room_type": "Double Room", "offer": "b"},
            {"hotel_name": "A", "room_type": "Deluxe Double Room", "offer": "a"},
        ],
        index=[7, 7, 7],
    )

    result = _filter_by_room_name(df, "Double Room")

    assert result.index.tolist() == [7, 7, 7]
    assert result["offer"].tolist() == ["a", "b", "a"]


def test_room_name_filter_treats_each_blank_or_missing_hotel_name_as_an_independent_property():
    """Ανώνυμες γραμμές δεν πρέπει να κόβουν η μία την άλλη σαν ένα ξενοδοχείο."""
    df = pd.DataFrame(
        [
            {"hotel_name": "", "room_type": "Double Room", "offer": "blank-exact"},
            {"hotel_name": "  ", "room_type": "Suite Garden View", "offer": "blank-fallback"},
            {"hotel_name": None, "room_type": "Double Room", "offer": "missing-exact"},
            {"hotel_name": pd.NA, "room_type": "Twin Garden View", "offer": "missing-fallback"},
        ],
        index=[9, 9, 9, 9],
    )

    result = _filter_by_room_name(df, "Double Room")

    assert result["offer"].tolist() == [
        "blank-exact",
        "blank-fallback",
        "missing-exact",
        "missing-fallback",
    ]
    assert result.index.tolist() == [9, 9, 9, 9]


@pytest.mark.parametrize("missing_column", ["hotel_name", "room_type"])
def test_room_name_filter_rejects_frames_without_required_matching_columns(missing_column):
    row = {"hotel_name": "Aegean View", "room_type": "Double Room"}
    df = pd.DataFrame([{key: value for key, value in row.items() if key != missing_column}])

    with pytest.raises(ValueError, match=missing_column):
        _filter_by_room_name(df, "Double Room")


def test_persist_results_filters_by_rate_options_before_writes(monkeypatch):
    df = process_and_flatten_data(_raw_property(), _hotel_meta(), ScraperConfig())
    output_path = Path("tmp/test_rate_option_filter.csv")
    output_path.unlink(missing_ok=True)
    config = ScraperConfig(
        dry_run=True,
        output_csv=str(output_path),
        required_meal="dinner included",
        required_free_cancellation="yes",
        # one-adult job: the fixture's 1-person package must survive the capacity filter
        adults=1,
    )

    def fail_write(*args, **kwargs):
        raise AssertionError("dry-run should not write to the database")

    monkeypatch.setattr("scraper.persistence.write_normalized_rates", fail_write)

    result = persist_results(df, config)

    assert result.rows_seen == 1
    assert result.normalized_written == 0
    assert not output_path.exists()


def test_main_dry_run_skips_database_cache_and_uses_config_output(monkeypatch):
    calls = {}

    def fake_build_client():
        calls["client"] = object()
        return calls["client"]

    def fail_build_engine():
        raise AssertionError("dry-run should not open the database")

    def fake_fetch_hotel_lists(client, config, engine=None, progress=None, warnings=None):
        calls["fetch_engine"] = engine
        return _hotel_meta()

    def fake_fetch_deep_room_data(client, hotels, config, progress=None):
        return _raw_property()

    monkeypatch.setattr("scraper.cli.build_client", fake_build_client)
    monkeypatch.setattr("scraper.cli.build_engine", fail_build_engine)
    monkeypatch.setattr("scraper.cli.fetch_hotel_lists", fake_fetch_hotel_lists)
    monkeypatch.setattr("scraper.cli.fetch_deep_room_data", fake_fetch_deep_room_data)

    main(
        [
            "--destination",
            "Faliraki",
            "--check-in",
            "2026-06-15",
            "--check-out",
            "2026-06-20",
            "--dry-run",
            "--output-csv",
            "tmp/main_dry_run.csv",
        ]
    )

    assert calls["fetch_engine"] is None


def test_live_scout_does_not_write_cache_when_cache_hours_is_zero(monkeypatch):
    calls = {}
    config = ScraperConfig(
        destination="Faliraki",
        scout_cache_hours=0,
        scout_max_items=3,
    )

    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        calls["actor_input"] = actor_input
        return [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
            }
        ]

    def fail_save_cache(engine, hotels, config):
        raise AssertionError("live scout must not write scout_cache")

    monkeypatch.setattr("scraper.scout._run_actor", fake_run_actor)
    monkeypatch.setattr("scraper.scout._save_scout_cache", fail_save_cache)

    hotels = fetch_hotel_list(client=object(), config=config, engine=object())

    assert len(hotels) == 1
    assert calls["actor_input"]["maxItems"] == 3
    assert calls["actor_input"]["adults"] == config.adults
    assert calls["actor_input"]["children"] == config.children
    assert calls["actor_input"]["rooms"] == config.rooms


class _RecordingEngine:
    """Minimal SQLAlchemy engine stand-in that records executed statements.

    scout.py uses ``engine.connect()`` for reads and ``engine.begin()`` for
    writes, both as context managers, so one object plays all three roles.
    """

    def __init__(self, rows: list | None = None):
        self.rows = rows or []
        self.executed: list[tuple] = []

    def connect(self):
        return self

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement, params=None):
        # Statement kept unstringified: pg_insert(...).on_conflict_do_update()
        # cannot compile against the default dialect.
        self.executed.append((statement, params))
        return self

    def fetchall(self):
        return self.rows


# The exact cache key the onboarding plan specifies: destination + stay +
# occupancy + localization. No `limit`, no `account_id` -- that is precisely
# what lets one auto-setup scout warm the wizard's «Δείξε άλλα» and the
# change-flow refine, which ask for different candidate limits.
_SCOUT_CACHE_KEY_PARAMS = {
    "dest", "ci", "co", "adults", "children", "rooms", "currency", "language",
}


_SCOUT_CACHE_KEY_PREDICATES = (
    ("destination", "dest"),
    ("check_in", "ci"),
    ("check_out", "co"),
    ("adults", "adults"),
    ("children", "children"),
    ("rooms", "rooms"),
    ("currency", "currency"),
    ("language", "language"),
)


def _assert_filters_on_every_key_dimension(statement) -> None:
    """Assert the SQL binds each cache-key column, not just that params exist.

    A bound parameter that no WHERE clause references is silently ignored, so
    checking the params dict alone cannot catch a dropped predicate.
    """
    normalized = " ".join(str(statement).split())
    for column, parameter in _SCOUT_CACHE_KEY_PREDICATES:
        assert f"{column} = :{parameter}" in normalized, f"{column} is not part of the cache key"


def _stay_config(**overrides) -> ScraperConfig:
    base = {
        "destination": "Φαληράκι",
        "check_in": datetime(2026, 7, 1),
        "check_out": datetime(2026, 7, 5),
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "scout_cache_hours": 24,
    }
    base.update(overrides)
    return ScraperConfig(**base)


def test_scout_cache_lookup_key_is_the_stay_not_the_limit_or_the_account():
    """Auto-setup (limit 12) must warm the same rows «Δείξε άλλα» (limit 25)
    reads, or threading the engine buys nothing. Also pins that scout_cache is
    deliberately GLOBAL: migration 20260711_0015 scoped it to the availability
    query and added no account column, so two accounts searching the same stay
    share rows -- public Booking search results, not tenant data.
    """
    auto_setup = _RecordingEngine()
    show_others = _RecordingEngine()

    _load_scout_cache(
        auto_setup,
        _stay_config(scout_max_items=12, account_id=UUID("00000000-0000-0000-0000-0000000000aa")),
    )
    _load_scout_cache(
        show_others,
        _stay_config(scout_max_items=25, account_id=UUID("00000000-0000-0000-0000-0000000000bb")),
    )

    # cutoff is now()-based, so it differs between the two calls by design.
    auto_setup_key = {k: v for k, v in auto_setup.executed[0][1].items() if k != "cutoff"}
    show_others_key = {k: v for k, v in show_others.executed[0][1].items() if k != "cutoff"}

    assert set(auto_setup_key) == _SCOUT_CACHE_KEY_PARAMS
    assert auto_setup_key == show_others_key
    # The bound values above are only half the key: assert the SQL actually
    # filters on every dimension, or a dropped WHERE clause would widen the
    # cache silently (e.g. serving Greek-language rows to an English search).
    _assert_filters_on_every_key_dimension(auto_setup.executed[0][0])


def test_scout_cache_write_evicts_on_the_same_stay_key():
    """The pre-insert DELETE must be scoped to the identical key the read uses;
    a narrower key would leave stale duplicate rows, a wider one would evict a
    different stay's cache.
    """
    engine = _RecordingEngine()

    _save_scout_cache(
        engine,
        [{"name": "Aegean View", "url": "https://www.booking.com/hotel/gr/aegean-view.html"}],
        _stay_config(scout_max_items=12),
    )

    delete_statement, delete_params = engine.executed[0]

    assert set(delete_params) == _SCOUT_CACHE_KEY_PARAMS
    _assert_filters_on_every_key_dimension(delete_statement)


class _CachedRow:
    """Stands in for a SQLAlchemy Row: _load_scout_cache reads ``._mapping``."""

    def __init__(self, mapping: dict):
        self._mapping = mapping


def _compiled_insert(statement):
    """Render a pg_insert so its columns, values and conflict-update are visible."""
    from sqlalchemy.dialects import postgresql

    compiled = statement.compile(dialect=postgresql.dialect())
    return " ".join(str(compiled).split()), compiled.params


def test_scout_cache_write_carries_the_address_the_scout_returned():
    """address is the one _extract_meta field the cache used to drop.

    _candidate_from_hotel maps it straight onto PropertyCandidate.address, so a
    cache that cannot store it hands the wizard blank address cards and
    persists the chosen property with an empty address -- invisible to any fake
    that returns hotels directly.
    """
    engine = _RecordingEngine()

    _save_scout_cache(
        engine,
        [
            {
                "name": "Aegean View",
                "url": "https://www.booking.com/hotel/gr/aegean-view.html",
                "address": "Leoforos Kalithea 12, Faliraki",
            }
        ],
        _stay_config(),
    )

    # executed[0] is the eviction DELETE; executed[1] is the upsert.
    insert_sql, insert_params = _compiled_insert(engine.executed[1][0])

    assert "address" in insert_sql[: insert_sql.index("VALUES")]
    assert insert_params["address_m0"] == "Leoforos Kalithea 12, Faliraki"
    # Without this, a refreshed cache row would keep the first write's address.
    assert "address = excluded.address" in insert_sql


def test_scout_cache_read_round_trips_the_address():
    """The read half of the same contract: selected, and surfaced under the key
    _candidate_from_hotel looks up. A NULL from a row written before the column
    existed must read back as "", exactly what the live scout returns for a
    hotel with no address, so cached and cold candidates are indistinguishable.
    """
    engine = _RecordingEngine(
        rows=[
            _CachedRow(
                {
                    "hotel_name": "Aegean View",
                    "hotel_url": "https://www.booking.com/hotel/gr/aegean-view.html",
                    "stars": 3.0,
                    "review_score": 8.8,
                    "review_count": 120,
                    "latitude": 36.34,
                    "longitude": 28.2,
                    "property_type": "Hotel",
                    "city": "Faliraki",
                    "address": "Leoforos Kalithea 12, Faliraki",
                }
            ),
            _CachedRow(
                {
                    "hotel_name": "Legacy Row",
                    "hotel_url": "https://www.booking.com/hotel/gr/legacy.html",
                    "stars": 0.0,
                    "review_score": 0.0,
                    "review_count": 0,
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "property_type": "Hotel",
                    "city": "Faliraki",
                    "address": None,
                }
            ),
        ]
    )

    # Two cached rows answer a request for two (a smaller cache is a MISS).
    hotels = _load_scout_cache(engine, _stay_config(scout_max_items=2))

    select_sql = " ".join(str(engine.executed[0][0]).split())

    assert "address" in select_sql
    assert hotels[0]["address"] == "Leoforos Kalithea 12, Faliraki"
    assert hotels[1]["address"] == ""


def test_scout_cache_hours_zero_never_touches_the_database():
    """.env.example promises 0 forces a live re-scrape. The read gate is
    `engine is not None`, so with the engine now threaded through from the API
    it is _load_scout_cache's own 0-check that has to hold the line -- and it
    must skip the query entirely, not rely on a cutoff that filters rows out.

    Observed by recording, not by raising: _load_scout_cache swallows every
    Exception ("table missing -> run the actor"), so an engine that raised
    would be caught and this test would pass no matter what.
    """
    engine = _RecordingEngine(rows=[])

    assert _load_scout_cache(engine, _stay_config(scout_cache_hours=0)) == []
    assert engine.executed == []


def _cached_rows(count: int) -> list[_CachedRow]:
    return [
        _CachedRow(
            {
                "hotel_name": f"Hotel {index}",
                "hotel_url": f"https://www.booking.com/hotel/gr/hotel-{index}.html",
                "stars": 3.0,
                "review_score": 8.0,
                "review_count": 10,
                "latitude": 36.34,
                "longitude": 28.2,
                "property_type": "Hotel",
                "city": "Faliraki",
                "address": "",
            }
        )
        for index in range(count)
    ]


def test_scout_cache_smaller_than_the_request_is_a_miss(caplog):
    """Round 6 §3.3: the key has no limit, so an old list of 10 used to answer
    a request for 40 and the map search never grew past it.
    """
    engine = _RecordingEngine(rows=_cached_rows(10))

    with caplog.at_level("INFO", logger="roomrate.scraper"):
        hotels = _load_scout_cache(engine, _stay_config(scout_max_items=40))

    assert hotels == []
    assert "Η cache του scout είναι μικρότερη από το αίτημα: 10 < 40" in caplog.text


def test_scout_cache_at_least_as_large_as_the_request_is_a_hit():
    engine = _RecordingEngine(rows=_cached_rows(12))

    hotels = _load_scout_cache(engine, _stay_config(scout_max_items=12))

    assert [hotel["name"] for hotel in hotels] == [f"Hotel {index}" for index in range(12)]


def test_retry_backoff_grows_exponentially_with_full_jitter(monkeypatch):
    monkeypatch.setattr("scraper.actor.random.uniform", lambda lower, upper: upper)

    assert _retry_delay_seconds(base_delay=5, attempt=3) == 20


def test_retry_backoff_is_capped(monkeypatch):
    monkeypatch.setattr("scraper.actor.random.uniform", lambda lower, upper: upper)

    assert _retry_delay_seconds(base_delay=5, attempt=10, max_delay=60) == 60


@pytest.mark.parametrize(
    ("base_delay", "attempt", "max_delay"),
    [(0, 1, 60), (5, 0, 60), (5, 1, 4)],
)
def test_retry_backoff_rejects_invalid_parameters(base_delay, attempt, max_delay):
    with pytest.raises(ValueError, match="Invalid retry backoff parameters"):
        _retry_delay_seconds(base_delay, attempt, max_delay)


def test_deep_crawl_splits_max_items_across_parallel_batches(monkeypatch):
    calls = []
    hotels = [
        {"url": f"https://www.booking.com/hotel/gr/example-{index}.html"}
        for index in range(10)
    ]
    config = ScraperConfig(
        deep_crawl_batch_size=4,
        deep_crawl_workers=1,
        deep_crawl_max_items=60,
    )

    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        calls.append(actor_input["maxItems"])
        return []

    monkeypatch.setattr("scraper.deep_crawl._run_actor", fake_run_actor)

    fetch_deep_room_data(client=object(), hotel_list=hotels, config=config)

    assert calls == [20, 20, 20]


def test_deep_crawl_reports_hotels_done_after_each_batch(monkeypatch):
    hotels = [
        {"url": f"https://www.booking.com/hotel/gr/example-{index}.html"}
        for index in range(10)
    ]
    config = ScraperConfig(deep_crawl_batch_size=4, deep_crawl_workers=2)
    updates: list[dict] = []

    class RecordingProgress:
        def update(self, stage, done=None, total=None, **counts):
            updates.append({"stage": stage, "done": done, "total": total, **counts})

    monkeypatch.setattr("scraper.deep_crawl._run_actor", lambda *args, **kwargs: [])

    fetch_deep_room_data(client=object(), hotel_list=hotels, config=config, progress=RecordingProgress())

    # The stage flips as soon as the crawl starts, not after the first batch.
    assert updates[0] == {"stage": "deep_crawl", "done": 0, "total": 10}
    # Batches of 4, 4 and 2 hotels may finish in any order; done only grows.
    done_after_batches = [update["done"] for update in updates[1:]]
    assert len(done_after_batches) == 3
    assert done_after_batches == sorted(done_after_batches)
    assert done_after_batches[-1] == 10
    assert {(update["stage"], update["total"]) for update in updates} == {("deep_crawl", 10)}


def test_deep_crawl_deduplicates_urls_before_batching(monkeypatch):
    called_urls = []
    hotels = [
        {"url": "https://www.booking.com/hotel/gr/example.html"},
        {"url": "https://www.booking.com/hotel/gr/example.html?checkin=2026-07-01"},
        {"url": "https://www.booking.com/hotel/gr/other.html"},
    ]
    config = ScraperConfig(deep_crawl_batch_size=10, deep_crawl_workers=1)

    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        called_urls.extend(item["url"] for item in actor_input["startUrls"])
        return []

    monkeypatch.setattr("scraper.deep_crawl._run_actor", fake_run_actor)

    fetch_deep_room_data(client=object(), hotel_list=hotels, config=config)

    assert called_urls == [
        "https://www.booking.com/hotel/gr/example.html",
        "https://www.booking.com/hotel/gr/other.html",
    ]


def test_deep_crawl_rejects_partial_market_snapshot_by_default(monkeypatch):
    hotels = [
        {"url": "https://www.booking.com/hotel/gr/first.html"},
        {"url": "https://www.booking.com/hotel/gr/second.html"},
    ]
    config = ScraperConfig(
        deep_crawl_batch_size=1,
        deep_crawl_workers=1,
        dry_run=True,
    )

    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        if "1/2" in label:
            raise ActorRunError("temporary actor failure")
        return [{"name": "Second"}]

    monkeypatch.setattr("scraper.deep_crawl._run_actor", fake_run_actor)

    with pytest.raises(ActorRunError, match="ελλιπές snapshot"):
        fetch_deep_room_data(client=object(), hotel_list=hotels, config=config)


def test_deep_crawl_allows_explicit_partial_market_snapshot(monkeypatch):
    hotels = [
        {"url": "https://www.booking.com/hotel/gr/first.html"},
        {"url": "https://www.booking.com/hotel/gr/second.html"},
    ]
    config = ScraperConfig(
        deep_crawl_batch_size=1,
        deep_crawl_workers=1,
        dry_run=True,
        allow_partial_batches=True,
    )

    def fake_run_actor(client, actor_input, max_retries, retry_delay, label=""):
        if "1/2" in label:
            raise ActorRunError("temporary actor failure")
        return [{"name": "Second"}]

    monkeypatch.setattr("scraper.deep_crawl._run_actor", fake_run_actor)

    result = fetch_deep_room_data(client=object(), hotel_list=hotels, config=config)

    assert result == [{"name": "Second"}]


# ---------------------------------------------------------------------------
# Error-log CSV (the only non-dry-run write path in the scraper's utils)
# ---------------------------------------------------------------------------


def test_append_error_log_writes_rows_to_csv(tmp_path):
    """A real (non-dry-run) write must produce the anonymized error CSV.

    Regression: the package split dropped utils' ``pandas`` import, so this
    path raised NameError. Both call sites run AFTER the market data is
    written, so the crash left the job retryable — it was requeued and
    re-scraped (burning Apify credits) while duplicating normalized rows.
    """
    log_path = tmp_path / "nested" / "error_log.csv"
    config = ScraperConfig(dry_run=False, error_log_csv=str(log_path))

    _append_error_log(
        config,
        [{"client_id": "abc123", "field": "price", "issue": "not a number", "value": "n/a"}],
    )

    assert log_path.exists()
    written = log_path.read_text(encoding="utf-8-sig")
    assert "price" in written
    assert "not a number" in written
    # Anonymized: the raw hotel identity never reaches the log, only client_id.
    assert "client_id" in written


def test_append_error_log_appends_without_repeating_the_header(tmp_path):
    log_path = tmp_path / "error_log.csv"
    config = ScraperConfig(dry_run=False, error_log_csv=str(log_path))
    row = [{"client_id": "abc", "field": "price", "issue": "bad", "value": "x"}]

    _append_error_log(config, row)
    _append_error_log(config, row)

    lines = [line for line in log_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert len(lines) == 3  # one header + two data rows
    assert lines[0].startswith("client_id")


def test_append_error_log_is_a_noop_in_dry_run(tmp_path):
    log_path = tmp_path / "error_log.csv"
    config = ScraperConfig(dry_run=True, error_log_csv=str(log_path))

    _append_error_log(config, [{"client_id": "abc", "field": "price", "issue": "bad", "value": "x"}])

    assert not log_path.exists()


# ----------------------------------------------------------------------------
# Round 6 §3.2: nearby areas, radius origin, hotel cap, batch size 8
# ----------------------------------------------------------------------------


def test_build_config_from_args_accepts_the_nearby_search_flags():
    config = build_config_from_args(
        [
            *_dry_run_cli_args(),
            "--nearby-destination", " Ιξιά ",
            "--nearby-destination", "ιξιά",
            "--nearby-destination", "faliraki",  # the main destination itself
            "--nearby-destination", "Αφάντου",
            "--nearby-max-items", "15",
            "--origin-lat", "36.34",
            "--origin-lng", "28.2",
            "--radius-km", "10",
            "--deep-crawl-max-hotels", "120",
        ]
    )

    assert config.nearby_destinations == ("Ιξιά", "Αφάντου")
    assert config.nearby_max_items == 15
    assert (config.origin_lat, config.origin_lng, config.radius_km) == (36.34, 28.2, 10.0)
    assert config.radius_active is True
    assert config.deep_crawl_max_hotels == 120


def test_build_config_from_args_round6_defaults():
    config = build_config_from_args(_dry_run_cli_args())

    assert config.nearby_destinations == ()
    assert config.nearby_max_items == 20
    assert config.radius_active is False
    assert config.deep_crawl_max_hotels is None
    assert config.deep_crawl_batch_size == 8


@pytest.mark.parametrize(
    "overrides",
    [{"origin_lat": 36.34}, {"radius_km": 0}, {"deep_crawl_max_hotels": 0}, {"nearby_max_items": 0}],
)
def test_scraper_config_rejects_half_an_origin_and_non_positive_round6_limits(overrides):
    with pytest.raises(ValueError):
        ScraperConfig(**overrides)
