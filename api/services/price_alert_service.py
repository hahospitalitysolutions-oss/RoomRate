from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from api.schemas.notifications import (
    AlertRuleResponse,
    MarkAllReadResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from api.schemas.scrape_jobs import JOB_TYPE_COMPETITOR_SEARCH
from api.services.market_helpers import as_optional_float

logger = logging.getLogger(__name__)

# notification_type for competitor price-change alerts.
PRICE_CHANGE_TYPE = "price_change"
# notification_type the schedule circuit breaker emits when it disables a schedule.
SCHEDULE_DISABLED_TYPE = "schedule_disabled"

# The "previous" run of an alert comparison must be at least this old.
# Scheduled scrapes rerun every few minutes; comparing two of them reported a
# «+113%» move that was really Booking's intra-hour noise (Round 6 §5.2).
ALERT_MIN_GAP_HOURS = 12.0


class AlertsRepositoryProtocol(Protocol):
    def list_active_rules(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID | None = None
    ) -> list[dict]: ...

    def insert_notifications(self, rows: list[dict]) -> list[dict]: ...

    def list_rules(self, account_id: uuid.UUID) -> list[dict]: ...

    def create_rule(self, account_id: uuid.UUID, values: dict[str, Any]) -> dict | None: ...

    def update_rule(
        self, account_id: uuid.UUID, rule_id: uuid.UUID, values: dict[str, Any]
    ) -> dict | None: ...

    def delete_rule(self, account_id: uuid.UUID, rule_id: uuid.UUID) -> bool: ...

    def list_notifications(
        self, account_id: uuid.UUID, unread_only: bool = False, limit: int = 50, offset: int = 0
    ) -> list[dict]: ...

    def count_unread(self, account_id: uuid.UUID) -> int: ...

    def delete_expired_notifications(
        self, account_id: uuid.UUID, retention_days: int
    ) -> int: ...

    def mark_read(self, account_id: uuid.UUID, notification_id: uuid.UUID) -> bool: ...

    def mark_all_read(self, account_id: uuid.UUID) -> int: ...


class PriceHistoryRepositoryProtocol(Protocol):
    def fetch_latest_vs_previous(
        self,
        account_id: uuid.UUID,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        room_type_category: str | None = None,
        property_ids: list[uuid.UUID] | None = None,
        min_gap_hours: float | None = None,
    ) -> list[dict]: ...


class TrackingRepositoryProtocol(Protocol):
    def list_tracked_competitors(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID, room_type_category: str
    ) -> list[dict]: ...


class NotifierProtocol(Protocol):
    """Cross-process publish side of the notification broadcaster.

    Typed as a Protocol so tests can inject a fake; production passes the
    PostgreSQL LISTEN/NOTIFY broadcaster.
    """

    def publish(self, account_id: uuid.UUID, payload: dict) -> None: ...

    def publish_many(self, account_id: uuid.UUID, payloads: list[dict]) -> None: ...


class PriceAlertService:
    """Evaluate completed competitor scrapes against alert rules and notify."""

    def __init__(
        self,
        alerts_repository: AlertsRepositoryProtocol,
        price_history_repository: PriceHistoryRepositoryProtocol,
        tracking_repository: TrackingRepositoryProtocol,
        notifier: NotifierProtocol,
        default_threshold_pct: float,
        notification_retention_days: int = 180,
    ):
        self.alerts_repository = alerts_repository
        self.price_history_repository = price_history_repository
        self.tracking_repository = tracking_repository
        self.notifier = notifier
        self.default_threshold_pct = default_threshold_pct
        self.notification_retention_days = notification_retention_days

    def evaluate_completed_job(self, account_id: uuid.UUID, job: Any) -> int:
        """Raise price-change notifications for a completed competitor scrape.

        Returns the number of notifications inserted. Defensive against being
        called for the wrong job type, and skips the price-history query
        entirely when the market has no tracked competitors.
        """
        # The caller (run_job) already filters, but double-check the contract.
        if getattr(job, "job_type", None) != JOB_TYPE_COMPETITOR_SEARCH:
            return 0

        owned_property_id = job.owned_property_id
        room_type_category = job.room_type_category

        logger.info(
            "Price-alert evaluation started: account_id=%s job_id=%s",
            account_id,
            getattr(job, "id", None),
            extra={
                "account_id": str(account_id),
                "job_id": str(getattr(job, "id", None)),
                "owned_property_id": str(owned_property_id) if owned_property_id else None,
            },
        )

        # 1) Which competitor properties does this account actually compare
        #    against for this (property, category)? No scope -> nothing to do.
        tracked_property_ids = self._tracked_property_ids(
            account_id, owned_property_id, room_type_category
        )
        if not tracked_property_ids:
            return 0

        # 2) Latest-vs-previous cheapest price per tracked competitor.
        history_rows = self.price_history_repository.fetch_latest_vs_previous(
            account_id=account_id,
            canonical_destination=job.canonical_destination,
            check_in=job.check_in,
            check_out=job.check_out,
            adults=job.adults,
            children=job.children,
            rooms=job.rooms,
            room_type_category=room_type_category,
            property_ids=tracked_property_ids,
            min_gap_hours=ALERT_MIN_GAP_HOURS,
        )

        # 4) Applicable rules (or a synthesized default when the account has none).
        rules = self.alerts_repository.list_active_rules(account_id, owned_property_id)
        if not rules:
            rules = [self._default_rule()]

        notification_rows: list[dict] = []
        for history_row in history_rows:
            # Decimal/None -> float for the % arithmetic and JSON payloads.
            current_price = as_optional_float(history_row.get("current_price"))
            previous_price = as_optional_float(history_row.get("previous_price"))
            # 3) Need both prices, a positive baseline and a real time gap.
            if current_price is None or previous_price is None or previous_price <= 0:
                continue
            if not self._gap_is_wide_enough(history_row):
                continue
            change_pct = round((current_price - previous_price) / previous_price * 100.0, 1)
            for rule in rules:
                if self._rule_breached(rule, change_pct):
                    notification_rows.append(
                        self._build_notification_row(
                            account_id=account_id,
                            job=job,
                            rule=rule,
                            history_row=history_row,
                            current_price=current_price,
                            previous_price=previous_price,
                            change_pct=change_pct,
                        )
                    )

        if not notification_rows:
            return 0

        inserted = self.alerts_repository.insert_notifications(notification_rows)
        self._publish_many(account_id, inserted)
        logger.info(
            "Price-alert notifications created: account_id=%s job_id=%s count=%s",
            account_id,
            getattr(job, "id", None),
            len(inserted),
            extra={
                "account_id": str(account_id),
                "job_id": str(getattr(job, "id", None)),
                "notification_count": len(inserted),
            },
        )
        return len(inserted)

    def notify_schedule_disabled(self, account_id: uuid.UUID, consecutive_failures: int) -> None:
        """Notify an account that its schedule was auto-disabled. Never raises."""
        try:
            row = {
                "account_id": account_id,
                "alert_rule_id": None,
                "notification_type": SCHEDULE_DISABLED_TYPE,
                "title": "Η αυτόματη αναζήτηση σταμάτησε",
                # The breaker only fires at 3+ failures, so the plural always fits.
                "message": (
                    "Οι επαναλαμβανόμενες αναζητήσεις ανταγωνιστών διακόπηκαν αυτόματα μετά από "
                    f"{consecutive_failures} συνεχόμενες αποτυχίες. Ενεργοποιήστε τις ξανά από "
                    "τις ρυθμίσεις προγραμματισμένων αναζητήσεων μόλις επιλυθεί το πρόβλημα."
                ),
                "payload": {"consecutive_failures": int(consecutive_failures)},
            }
            inserted = self.alerts_repository.insert_notifications([row])
            for inserted_row in inserted:
                self._publish(account_id, inserted_row)
        except Exception:
            # Breaker bookkeeping must never be aborted by a notification failure.
            logger.exception("notify_schedule_disabled failed: account_id=%s", account_id)

    # ------------------------------------------------------------------
    # Read pass-throughs (the router routes notification reads through the
    # service, matching the rest of the codebase's service layering).
    # ------------------------------------------------------------------

    def list_notifications(
        self, account_id: uuid.UUID, unread_only: bool = False, limit: int = 50, offset: int = 0
    ) -> list[NotificationResponse]:
        cleanup = getattr(self.alerts_repository, "delete_expired_notifications", None)
        deleted = (
            cleanup(account_id, self.notification_retention_days)
            if callable(cleanup)
            else 0
        )
        if deleted:
            logger.info(
                "Expired notifications removed: account_id=%s count=%s",
                account_id,
                deleted,
            )
        rows = self.alerts_repository.list_notifications(
            account_id, unread_only=unread_only, limit=limit, offset=offset
        )
        return [NotificationResponse.model_validate(row) for row in rows]

    def count_unread(self, account_id: uuid.UUID) -> UnreadCountResponse:
        return UnreadCountResponse(count=self.alerts_repository.count_unread(account_id))

    def mark_read(self, account_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
        return self.alerts_repository.mark_read(account_id, notification_id)

    def mark_all_read(self, account_id: uuid.UUID) -> MarkAllReadResponse:
        return MarkAllReadResponse(updated=self.alerts_repository.mark_all_read(account_id))

    def list_rules(self, account_id: uuid.UUID) -> list[AlertRuleResponse]:
        """Return all editable alert rules for one account."""
        return [
            AlertRuleResponse.model_validate(row)
            for row in self.alerts_repository.list_rules(account_id)
        ]

    def create_rule(self, account_id: uuid.UUID, values: dict[str, Any]) -> AlertRuleResponse | None:
        """Create an alert rule inside the account boundary."""
        row = self.alerts_repository.create_rule(account_id, values)
        return AlertRuleResponse.model_validate(row) if row else None

    def update_rule(
        self,
        account_id: uuid.UUID,
        rule_id: uuid.UUID,
        values: dict[str, Any],
    ) -> AlertRuleResponse | None:
        """Update an owned rule or return None when it is inaccessible."""
        row = self.alerts_repository.update_rule(account_id, rule_id, values)
        return AlertRuleResponse.model_validate(row) if row else None

    def delete_rule(self, account_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
        """Delete one rule inside its account boundary."""
        return self.alerts_repository.delete_rule(account_id, rule_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tracked_property_ids(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID | None,
        room_type_category: str | None,
    ) -> list[uuid.UUID]:
        """Tracked competitor property ids for one comparable room pool.

        Tracking writes keep their exact category key, while the repository
        widens this read to the job category's comparable pool. A job missing
        either the property or category cannot resolve a tracked set.
        """
        if owned_property_id is None or not room_type_category:
            return []
        tracked = self.tracking_repository.list_tracked_competitors(
            account_id=account_id,
            owned_property_id=owned_property_id,
            room_type_category=room_type_category,
        )
        return [row["property_id"] for row in tracked]

    @staticmethod
    def _gap_is_wide_enough(history_row: dict) -> bool:
        """Second line of defence for the 12-hour rule (the SQL is the first).

        Rows that carry both timestamps are re-checked here so the rule is
        unit-testable with fakes; a row without ``previous_observed_at`` is
        trusted — the repository already picked a run that is old enough.
        """
        observed_at = history_row.get("observed_at")
        previous_observed_at = history_row.get("previous_observed_at")
        if not isinstance(observed_at, datetime) or not isinstance(previous_observed_at, datetime):
            return True
        return observed_at - previous_observed_at >= timedelta(hours=ALERT_MIN_GAP_HOURS)

    def _default_rule(self) -> dict:
        """Synthesize the implicit account default rule (id=None)."""
        return {
            "id": None,
            "threshold_pct": self.default_threshold_pct,
            "direction": "any",
            "owned_property_id": None,
        }

    @staticmethod
    def _rule_breached(rule: dict, change_pct: float) -> bool:
        """A rule breaches when the move clears the threshold AND matches direction."""
        threshold = float(rule["threshold_pct"])
        if abs(change_pct) < threshold:
            return False
        direction = rule.get("direction", "any")
        if direction == "drop":
            return change_pct < 0
        if direction == "rise":
            return change_pct > 0
        return True  # "any"

    @staticmethod
    def _price_change_event_key(
        job: Any,
        rule: dict,
        history_row: dict,
        current_price: float,
        previous_price: float,
    ) -> str:
        """Deterministic identity of one logical price transition.

        Excludes the scrape-job id on purpose: a re-run or re-evaluation that
        observes the SAME move (same property, market key, rule and prices)
        must collide on the DB unique index instead of alerting twice. The
        current observation DAY is included so a genuinely recurring move on a
        later day still raises a fresh alert.
        """
        observed_at = history_row.get("observed_at")
        if hasattr(observed_at, "date"):
            observed_day = observed_at.date().isoformat()
        elif observed_at:
            observed_day = str(observed_at)[:10]
        else:
            observed_day = date.today().isoformat()
        return ":".join(
            [
                PRICE_CHANGE_TYPE,
                str(rule.get("id") or "default"),
                str(history_row.get("property_id")),
                str(job.canonical_destination),
                f"{job.check_in.isoformat()}_{job.check_out.isoformat()}",
                f"{job.adults}a{job.children}c{job.rooms}r",
                str(job.room_type_category or "any"),
                f"{previous_price:.2f}->{current_price:.2f}",
                observed_day,
            ]
        )

    @staticmethod
    def _build_notification_row(
        account_id: uuid.UUID,
        job: Any,
        rule: dict,
        history_row: dict,
        current_price: float,
        previous_price: float,
        change_pct: float,
    ) -> dict:
        hotel_name = history_row.get("hotel_name") or "competitor"
        # Greek, sign-aware title/message: the bell and the toasts show them
        # verbatim. The pct is an absolute magnitude with a decimal comma and
        # prices are whole euros.
        display_name = str(history_row.get("hotel_name") or "").strip()
        if change_pct < 0:
            title_prefix, verb = "Πτώση τιμής", "μειώθηκε"
        else:
            title_prefix, verb = "Άνοδος τιμής", "αυξήθηκε"
        # The job's destination as typed reads better than the canonical slug.
        destination = getattr(job, "destination", None) or job.canonical_destination
        where = f" στην περιοχή {destination}" if destination else ""
        pct_text = f"{abs(change_pct):.1f}".replace(".", ",")
        title = f"{title_prefix}: {display_name or 'ανταγωνιστής'}"
        message = (
            f"Η τιμή του {display_name or 'ανταγωνιστή'} {verb} κατά {pct_text}% "
            f"({_whole_euros(previous_price)} € → {_whole_euros(current_price)} €){where}."
        )
        payload = {
            "property_id": str(history_row.get("property_id")),
            "hotel_name": hotel_name,
            "room_type_category": job.room_type_category,
            "previous_price": previous_price,
            "current_price": current_price,
            "change_pct": change_pct,
            "scrape_job_id": str(job.id),
            "canonical_destination": job.canonical_destination,
            "check_in": job.check_in.isoformat(),
            "check_out": job.check_out.isoformat(),
        }
        return {
            "account_id": account_id,
            "alert_rule_id": rule.get("id"),
            "notification_type": PRICE_CHANGE_TYPE,
            "title": title,
            "message": message,
            "payload": payload,
            "event_key": PriceAlertService._price_change_event_key(
                job, rule, history_row, current_price, previous_price
            ),
        }

    def _publish(self, account_id: uuid.UUID, notification_row: dict) -> None:
        """Publish one notification; broadcaster failures are logged, not raised."""
        try:
            self.notifier.publish(account_id, _serializable_notification(notification_row))
        except Exception:
            logger.warning(
                "Notification publish failed (notification still persisted): account_id=%s",
                account_id,
                exc_info=True,
            )

    def _publish_many(self, account_id: uuid.UUID, notification_rows: list[dict]) -> None:
        """Publish a batch in one broadcaster call; failures logged, not raised."""
        try:
            self.notifier.publish_many(
                account_id,
                [_serializable_notification(row) for row in notification_rows],
            )
        except Exception:
            logger.warning(
                "Notification publish failed (notifications still persisted): account_id=%s",
                account_id,
                exc_info=True,
            )


def _whole_euros(value: float) -> str:
    """Whole euros with halves rounded up (80.50 -> "81"); float formatting rounds halves to even."""
    return str(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _serializable_notification(row: dict) -> dict:
    """Return a JSON-serializable copy of a persisted notification row."""
    created_at = row.get("created_at")
    notification_id = row.get("id")
    return {
        "id": str(notification_id) if notification_id is not None else None,
        "account_id": str(row["account_id"]),
        "alert_rule_id": str(row["alert_rule_id"]) if row.get("alert_rule_id") else None,
        "notification_type": row["notification_type"],
        "title": row["title"],
        "message": row["message"],
        "payload": row.get("payload"),
        "is_read": bool(row.get("is_read", False)),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }
