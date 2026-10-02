"""Unit tests for PriceAlertService.evaluate_completed_job and breaker notify.

The service is pure orchestration over fakes: a fake alerts repository, a fake
price-history repository, a fake tracking repository, and a fake notifier. No
DB is touched. The fakes record calls so tests can assert the history repo is
skipped when nothing is tracked, that notifier failures are swallowed, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from api.repositories.price_history_repository import PriceHistoryRepository
from api.services.price_alert_service import PriceAlertService
from api.tests._fakes import EchoConnectionContext, FakeResult


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
PROP_A = UUID("00000000-0000-0000-0000-0000000000a1")
PROP_B = UUID("00000000-0000-0000-0000-0000000000b2")
RULE_ID = UUID("00000000-0000-0000-0000-0000000000e1")


@dataclass
class FakeJob:
    """Minimal stand-in for the scrape-job object run_job has in scope."""

    account_id: UUID = ACCOUNT_ID
    owned_property_id: UUID | None = OWNED_PROPERTY_ID
    job_type: str = "competitor_search"
    room_type_category: str | None = "double"
    destination: str = "Φαληράκι"
    canonical_destination: str = "faliraki"
    check_in: date = date(2026, 7, 1)
    check_out: date = date(2026, 7, 5)
    adults: int = 2
    children: int = 0
    rooms: int = 1
    id: UUID = UUID("00000000-0000-0000-0000-000000000777")


class FakeAlertsRepository:
    def __init__(self, rules: list[dict] | None = None):
        self.rules = rules if rules is not None else []
        self.list_rules_calls: list[tuple] = []
        self.inserted_rows: list[list[dict]] = []

    def list_active_rules(self, account_id, owned_property_id=None):
        self.list_rules_calls.append((account_id, owned_property_id))
        return list(self.rules)

    def insert_notifications(self, rows):
        self.inserted_rows.append(list(rows))
        # Echo back DB-assigned id/created_at like the real RETURNING clause.
        out = []
        for index, row in enumerate(rows):
            out.append(
                {
                    **row,
                    "id": UUID(int=index + 1),
                    "created_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
                }
            )
        return out


class FakeHistoryRepository:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows if rows is not None else []
        self.calls: list[dict] = []

    def fetch_latest_vs_previous(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.rows)


class FakeTrackingRepository:
    def __init__(self, tracked: list[dict] | None = None):
        self.tracked = tracked if tracked is not None else []
        self.calls: list[tuple] = []

    def list_tracked_competitors(self, account_id, owned_property_id, room_type_category):
        self.calls.append((account_id, owned_property_id, room_type_category))
        return list(self.tracked)


class FakeNotifier:
    def __init__(self, raise_on_publish: bool = False):
        self.published: list[tuple] = []
        self.batch_calls: list[tuple] = []
        self.raise_on_publish = raise_on_publish

    def publish(self, account_id, payload):
        self.published.append((account_id, payload))
        if self.raise_on_publish:
            raise RuntimeError("broadcaster down")

    def publish_many(self, account_id, payloads):
        self.batch_calls.append((account_id, list(payloads)))
        self.published.extend((account_id, payload) for payload in payloads)
        if self.raise_on_publish:
            raise RuntimeError("broadcaster down")


def _tracked(*property_ids):
    return [{"property_id": pid, "room_package_id": None} for pid in property_ids]


def _history_row(property_id, current, previous, hotel_name="Hotel X"):
    return {
        "property_id": property_id,
        "hotel_name": hotel_name,
        "current_price": current,
        "previous_price": previous,
        "observed_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
    }


def _service(alerts, history, tracking, notifier, default_threshold_pct=10.0):
    return PriceAlertService(
        alerts_repository=alerts,
        price_history_repository=history,
        tracking_repository=tracking,
        notifier=notifier,
        default_threshold_pct=default_threshold_pct,
    )


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


def test_non_competitor_search_job_returns_zero_and_touches_nothing():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository()
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    count = service.evaluate_completed_job(
        ACCOUNT_ID, FakeJob(job_type="owned_property_room_discovery")
    )

    assert count == 0
    assert history.calls == []
    assert tracking.calls == []
    assert notifier.published == []


def test_no_tracked_competitors_returns_zero_without_history_call():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("100"), Decimal("80"))])
    tracking = FakeTrackingRepository(tracked=[])  # nothing tracked
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    count = service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert count == 0
    # The whole point: do NOT hit price history when nothing is tracked.
    assert history.calls == []
    assert notifier.published == []


# ---------------------------------------------------------------------------
# Threshold boundary + direction filtering
# ---------------------------------------------------------------------------


def test_threshold_boundary_9_9_does_not_breach():
    # 100 -> 90.1 is a -9.9% drop: just under the 10% default threshold.
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("90.10"), Decimal("100.00"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    count = service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert count == 0
    assert notifier.published == []


def test_threshold_boundary_10_0_breaches_with_gte():
    # 100 -> 90 is exactly -10%: must breach (>= comparison).
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("90.00"), Decimal("100.00"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    count = service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert count == 1
    assert alerts.inserted_rows[0][0]["notification_type"] == "price_change"
    payload = alerts.inserted_rows[0][0]["payload"]
    assert payload["change_pct"] == -10.0
    assert payload["previous_price"] == 100.0
    assert payload["current_price"] == 90.0
    # ISO date strings for JSON-serializability.
    assert payload["check_in"] == "2026-07-01"
    assert payload["scrape_job_id"] == str(FakeJob().id)
    assert notifier.published[0][0] == ACCOUNT_ID


def test_direction_drop_ignores_a_rise():
    rule = {
        "id": RULE_ID,
        "threshold_pct": Decimal("10.0"),
        "direction": "drop",
        "owned_property_id": OWNED_PROPERTY_ID,
    }
    alerts = FakeAlertsRepository(rules=[rule])
    # 100 -> 120 is a +20% rise; a drop-only rule must ignore it.
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("120"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 0


def test_direction_rise_ignores_a_drop():
    rule = {
        "id": RULE_ID,
        "threshold_pct": Decimal("10.0"),
        "direction": "rise",
        "owned_property_id": OWNED_PROPERTY_ID,
    }
    alerts = FakeAlertsRepository(rules=[rule])
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 0


def test_direction_any_catches_both():
    rule = {
        "id": RULE_ID,
        "threshold_pct": Decimal("10.0"),
        "direction": "any",
        "owned_property_id": None,
    }
    alerts = FakeAlertsRepository(rules=[rule])
    history = FakeHistoryRepository(
        rows=[
            _history_row(PROP_A, Decimal("80"), Decimal("100"), "Drop Hotel"),
            _history_row(PROP_B, Decimal("130"), Decimal("100"), "Rise Hotel"),
        ]
    )
    tracking = FakeTrackingRepository(_tracked(PROP_A, PROP_B))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 2


# ---------------------------------------------------------------------------
# Missing prices / divide-by-zero
# ---------------------------------------------------------------------------


def test_rows_missing_previous_price_are_skipped():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(
        rows=[
            _history_row(PROP_A, Decimal("50"), None),  # no previous -> skip
            _history_row(PROP_B, Decimal("50"), Decimal("100")),  # -50% -> breach
        ]
    )
    tracking = FakeTrackingRepository(_tracked(PROP_A, PROP_B))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1


def test_zero_previous_price_is_skipped():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("50"), Decimal("0"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 0


# ---------------------------------------------------------------------------
# Rule synthesis + multiple rules
# ---------------------------------------------------------------------------


def test_default_rule_synthesized_when_none_active():
    alerts = FakeAlertsRepository(rules=[])  # no rules -> synthesize default
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier, default_threshold_pct=15.0)

    # -20% drop breaches the synthesized 15% any-direction default.
    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
    # The synthesized rule has no DB id, so the notification's alert_rule_id is None.
    assert alerts.inserted_rows[0][0]["alert_rule_id"] is None


def test_default_rule_threshold_respected():
    alerts = FakeAlertsRepository(rules=[])
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("90"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    # -10% drop is under the 15% default threshold -> no breach.
    service = _service(alerts, history, tracking, notifier, default_threshold_pct=15.0)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 0


def test_multiple_rules_yield_multiple_notifications_per_property():
    rules = [
        {"id": UUID(int=10), "threshold_pct": Decimal("5.0"), "direction": "any", "owned_property_id": None},
        {"id": UUID(int=11), "threshold_pct": Decimal("8.0"), "direction": "drop", "owned_property_id": None},
    ]
    alerts = FakeAlertsRepository(rules=rules)
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    # -20% drop breaches both rules -> two notifications for the one property.
    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 2
    assert len(notifier.published) == 2
    # ...delivered as ONE broadcaster batch, not per-notification calls.
    assert len(notifier.batch_calls) == 1


# ---------------------------------------------------------------------------
# Decimal math + rounding + title sign-awareness
# ---------------------------------------------------------------------------


def test_change_pct_rounded_to_one_decimal_and_floats_in_payload():
    alerts = FakeAlertsRepository()
    # 100 -> 66.66 => -33.34% which rounds to -33.3.
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("66.66"), Decimal("100.00"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    payload = alerts.inserted_rows[0][0]["payload"]
    assert payload["change_pct"] == -33.3
    assert isinstance(payload["current_price"], float)
    assert isinstance(payload["previous_price"], float)
    assert isinstance(payload["change_pct"], float)


def test_title_is_sign_aware():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(
        rows=[
            _history_row(PROP_A, Decimal("80"), Decimal("100"), "Dropper"),
            _history_row(PROP_B, Decimal("130"), Decimal("100"), "Riser"),
        ]
    )
    tracking = FakeTrackingRepository(_tracked(PROP_A, PROP_B))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    titles = {row["title"] for row in alerts.inserted_rows[0]}
    assert titles == {"Πτώση τιμής: Dropper", "Άνοδος τιμής: Riser"}


# ---------------------------------------------------------------------------
# Greek notification text (the bell and the toasts show title + message)
# ---------------------------------------------------------------------------


def _only_notification(history_row) -> dict:
    alerts = FakeAlertsRepository()
    service = _service(
        alerts,
        FakeHistoryRepository(rows=[history_row]),
        FakeTrackingRepository(_tracked(PROP_A)),
        FakeNotifier(),
    )
    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
    return alerts.inserted_rows[0][0]


def test_rise_notification_is_greek_with_decimal_comma_and_whole_euros():
    row = _only_notification(_history_row(PROP_A, Decimal("142.86"), Decimal("100.00"), "Rea Hotel"))

    assert row["title"] == "Άνοδος τιμής: Rea Hotel"
    assert row["message"] == (
        "Η τιμή του Rea Hotel αυξήθηκε κατά 42,9% (100 € → 143 €) στην περιοχή Φαληράκι."
    )


def test_drop_notification_is_greek_and_rounds_half_euros_up():
    # (70.40 - 80.50) / 80.50 = -12.5%; 80.50 shows as 81 €, not banker's 80 €.
    row = _only_notification(_history_row(PROP_A, Decimal("70.40"), Decimal("80.50"), "Rea Hotel"))

    assert row["title"] == "Πτώση τιμής: Rea Hotel"
    assert row["message"] == (
        "Η τιμή του Rea Hotel μειώθηκε κατά 12,5% (81 € → 70 €) στην περιοχή Φαληράκι."
    )


class _OnePropertyDatabase:
    """Answers price-history SQL for one property whose two names differ in case.

    Booking shows «Evita Resort»; the writer stores the canonical «evita resort»
    next to it. ``hotel_name`` comes back from whichever column the SQL
    selects, as PostgreSQL would.
    """

    NAME_BY_COLUMN = {
        "p.display_name AS hotel_name": "Evita Resort",
        "p.canonical_name AS hotel_name": "evita resort",
    }

    def execute(self, sql, params):
        text = str(sql)
        hotel_name = next(name for column, name in self.NAME_BY_COLUMN.items() if column in text)
        return FakeResult(
            rows=[
                {
                    "rn": 1,
                    "property_id": PROP_A,
                    "hotel_name": hotel_name,
                    "min_price": Decimal("130"),
                    "current_price": Decimal("130"),
                    "previous_price": Decimal("100"),
                    "observed_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
                }
            ]
        )


def test_alert_title_and_history_row_carry_the_booking_display_name():
    repository = PriceHistoryRepository(lambda: EchoConnectionContext(_OnePropertyDatabase()))
    job = FakeJob()
    alerts = FakeAlertsRepository()
    service = _service(alerts, repository, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    history_rows = repository.fetch_price_series(
        account_id=ACCOUNT_ID,
        canonical_destination=job.canonical_destination,
        check_in=job.check_in,
        check_out=job.check_out,
        adults=job.adults,
        children=job.children,
        rooms=job.rooms,
        room_type_category=job.room_type_category,
    )

    assert history_rows[0]["hotel_name"] == "Evita Resort"
    assert service.evaluate_completed_job(ACCOUNT_ID, job) == 1
    assert alerts.inserted_rows[0][0]["title"] == "Άνοδος τιμής: Evita Resort"


def test_notification_without_a_hotel_name_stays_grammatical():
    row = _only_notification(_history_row(PROP_A, Decimal("80"), Decimal("100"), hotel_name=None))

    assert row["title"] == "Πτώση τιμής: ανταγωνιστής"
    assert row["message"].startswith("Η τιμή του ανταγωνιστή μειώθηκε κατά 20,0% (100 € → 80 €)")


# ---------------------------------------------------------------------------
# Notifier failures swallowed
# ---------------------------------------------------------------------------


def test_notifier_publish_failure_is_swallowed_and_count_still_returned():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier(raise_on_publish=True)
    service = _service(alerts, history, tracking, notifier)

    # Even though publish raises, the notification was inserted and counted.
    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
    assert len(alerts.inserted_rows[0]) == 1


def test_history_called_with_tracked_property_ids_only():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[])
    tracking = FakeTrackingRepository(_tracked(PROP_A, PROP_B))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert len(history.calls) == 1
    call = history.calls[0]
    assert set(call["property_ids"]) == {PROP_A, PROP_B}
    assert call["canonical_destination"] == "faliraki"
    assert call["room_type_category"] == "double"


def test_twin_job_evaluates_pooled_tracking_and_history_without_relabeling_payload():
    """Repository pool reads can surface a double-tracked competitor to a twin job.

    The service must evaluate that row while the notification payload keeps the
    job's stored ``twin`` vocabulary; pooling is a read concern, not a migration.
    """
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(
        rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))]
    )
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    notifier = FakeNotifier()
    service = _service(alerts, history, tracking, notifier)

    assert service.evaluate_completed_job(
        ACCOUNT_ID, FakeJob(room_type_category="twin")
    ) == 1

    assert tracking.calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID, "twin")]
    assert history.calls[0]["room_type_category"] == "twin"
    assert alerts.inserted_rows[0][0]["payload"]["room_type_category"] == "twin"


# ---------------------------------------------------------------------------
# notify_schedule_disabled
# ---------------------------------------------------------------------------


def test_notify_schedule_disabled_inserts_and_publishes():
    alerts = FakeAlertsRepository()
    notifier = FakeNotifier()
    service = _service(alerts, FakeHistoryRepository(), FakeTrackingRepository(), notifier)

    service.notify_schedule_disabled(ACCOUNT_ID, consecutive_failures=3)

    row = alerts.inserted_rows[0][0]
    assert row["notification_type"] == "schedule_disabled"
    assert row["payload"]["consecutive_failures"] == 3
    assert row["title"] == "Η αυτόματη αναζήτηση σταμάτησε"
    assert row["message"] == (
        "Οι επαναλαμβανόμενες αναζητήσεις ανταγωνιστών διακόπηκαν αυτόματα μετά από "
        "3 συνεχόμενες αποτυχίες. Ενεργοποιήστε τις ξανά από τις ρυθμίσεις "
        "προγραμματισμένων αναζητήσεων μόλις επιλυθεί το πρόβλημα."
    )
    assert notifier.published[0][0] == ACCOUNT_ID


def test_notify_schedule_disabled_swallows_notifier_failure():
    alerts = FakeAlertsRepository()
    notifier = FakeNotifier(raise_on_publish=True)
    service = _service(alerts, FakeHistoryRepository(), FakeTrackingRepository(), notifier)

    # Must not raise even when the broadcaster is down.
    service.notify_schedule_disabled(ACCOUNT_ID, consecutive_failures=5)
    assert len(alerts.inserted_rows[0]) == 1


# ---------------------------------------------------------------------------
# event_key (idempotent alert inserts)
# ---------------------------------------------------------------------------


def test_price_change_rows_carry_deterministic_event_key():
    """Re-evaluating the same price move (even via a NEW job) builds the same key.

    The key must identify the logical price transition, not the processing
    attempt: a sweeper-requeued job or a same-day re-scrape that observes the
    identical move must map onto the same event_key so the DB unique index can
    drop the duplicate.
    """
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    service = _service(alerts, history, tracking, FakeNotifier())

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())
    service.evaluate_completed_job(ACCOUNT_ID, FakeJob(id=UUID(int=999)))

    first = alerts.inserted_rows[0][0]
    second = alerts.inserted_rows[1][0]
    assert first["event_key"]
    assert first["event_key"] == second["event_key"]
    assert first["event_key"].startswith("price_change:")
    # Dedup bucket is the current observation DAY so a genuinely recurring
    # move weeks later still alerts again.
    assert "2026-06-14" in first["event_key"]
    # The job id must never leak into the key (it would defeat cross-job dedup).
    assert str(FakeJob().id) not in first["event_key"]


def test_event_key_distinguishes_property_rule_and_prices():
    rule_a = {
        "id": RULE_ID,
        "threshold_pct": Decimal("10.0"),
        "direction": "any",
        "owned_property_id": None,
    }
    alerts = FakeAlertsRepository(rules=[rule_a])
    history = FakeHistoryRepository(
        rows=[
            _history_row(PROP_A, Decimal("80"), Decimal("100"), "Drop Hotel"),
            _history_row(PROP_B, Decimal("130"), Decimal("100"), "Rise Hotel"),
        ]
    )
    tracking = FakeTrackingRepository(_tracked(PROP_A, PROP_B))
    service = _service(alerts, history, tracking, FakeNotifier())

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    keys = [row["event_key"] for row in alerts.inserted_rows[0]]
    assert len(keys) == len(set(keys)) == 2
    assert str(RULE_ID) in keys[0]


def test_default_rule_event_key_uses_default_marker():
    alerts = FakeAlertsRepository()  # no rules -> synthesized default rule
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    tracking = FakeTrackingRepository(_tracked(PROP_A))
    service = _service(alerts, history, tracking, FakeNotifier())

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert ":default:" in alerts.inserted_rows[0][0]["event_key"]


def test_schedule_disabled_notification_has_no_event_key():
    alerts = FakeAlertsRepository()
    service = _service(alerts, FakeHistoryRepository(), FakeTrackingRepository(), FakeNotifier())

    service.notify_schedule_disabled(ACCOUNT_ID, consecutive_failures=3)

    assert alerts.inserted_rows[0][0].get("event_key") is None


# ---------------------------------------------------------------------------
# Round 6 — a rerun minutes later is the same market moment, not a change
# ---------------------------------------------------------------------------


def _gap_row(property_id, current, previous, gap: timedelta):
    observed_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    return {
        **_history_row(property_id, current, previous),
        "observed_at": observed_at,
        "previous_observed_at": observed_at - gap,
    }


def test_service_asks_for_a_previous_run_at_least_twelve_hours_old():
    history = FakeHistoryRepository(rows=[])
    service = _service(FakeAlertsRepository(), history, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert history.calls[0]["min_gap_hours"] == 12


def test_a_rerun_three_minutes_later_raises_no_alert():
    # 100 -> 213 would be «+113%» — exactly the demo bug — but 3 minutes is no gap.
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_gap_row(PROP_A, Decimal("213"), Decimal("100"), timedelta(minutes=3))])
    notifier = FakeNotifier()
    service = _service(alerts, history, FakeTrackingRepository(_tracked(PROP_A)), notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 0
    assert alerts.inserted_rows == []
    assert notifier.published == []


def test_a_run_thirteen_hours_later_raises_the_alert():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_gap_row(PROP_A, Decimal("213"), Decimal("100"), timedelta(hours=13))])
    service = _service(alerts, history, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
    assert alerts.inserted_rows[0][0]["payload"]["change_pct"] == 113.0


def test_rows_without_a_previous_timestamp_trust_the_repository():
    # The SQL already picked a run that is old enough; a row that carries no
    # previous_observed_at (older fakes, NULL column) is not rejected here.
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    service = _service(alerts, history, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
