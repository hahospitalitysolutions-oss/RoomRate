from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from api.db import get_engine
from api.schemas.scrape_jobs import ScrapeJobCreate
from api.services.room_rates_normalizer import normalize_room_type_category

# Booking's room list sometimes leaks a list counter into the scraped room name
# («…Μπαλκόνι 1/11»). Tight on purpose: only a trailing, space-separated «N/M»
# is stripped, so a real label never loses its own digits («2 Μονά Κρεβάτια»).
ROOM_NAME_COUNTER_ARTIFACT = re.compile(r"\s+\d+/\d+$")

# Owned-room union (see union_owned_room_types). The owner's hotel is found in
# ANY run by the same name equality the competitor view uses to EXCLUDE it
# (room_rates_repository.OWNED_PROPERTY_EXCLUSION_FILTER), account-scoped.
# ``{run_scope}`` is filled only with one of the two constants below, never
# with request input. Oldest run first, so the first row of a room name is
# the run that first saw it.
OWNED_ROOM_CANDIDATES_SQL = """
    SELECT
        op.id AS owned_property_id,
        sr.scrape_job_id AS run_job_id,
        rp.room_type,
        rp.meals,
        rp.free_cancellation,
        NULLIF(rp.package_payload ->> 'facilities', '') AS facilities,
        rp.price_per_night_eur
    FROM roomrate_scrape_runs sr
    JOIN roomrate_rate_observations ro ON ro.scrape_run_id = sr.id
    JOIN roomrate_properties p ON p.id = ro.property_id
    JOIN roomrate_owned_properties op
      ON op.account_id = sr.account_id
     AND op.is_active = true
     AND lower(trim(op.display_name)) = lower(trim(p.display_name))
    JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
    WHERE sr.account_id = :account_id
      AND {run_scope}
    ORDER BY sr.created_at ASC, rp.price_per_night_eur ASC
"""
# One job's runs (the completion hook) vs the account's whole history (backfill).
OWNED_ROOM_JOB_RUN_SCOPE = "sr.scrape_job_id = :job_id"
OWNED_ROOM_HISTORY_RUN_SCOPE = "sr.status = 'completed'"


def clean_owned_room_type_name(value: object) -> str:
    """Strip surrounding whitespace and a trailing « N/M» scrape artifact."""
    return ROOM_NAME_COUNTER_ARTIFACT.sub("", str(value or "").strip()).strip()


def _owned_room_name_key(name: str) -> str:
    """Case/whitespace-insensitive identity of a cleaned room name."""
    return " ".join(name.split()).casefold()


def _room_samples(row: Mapping[str, Any]) -> dict[str, Any]:
    """Sample columns carried from a package, the same set discovery stores."""
    return {
        "sample_meals": row["meals"],
        "sample_free_cancellation": row["free_cancellation"],
        "sample_facilities": row["facilities"],
        "sample_price_per_night_eur": row["price_per_night_eur"],
    }


def _collapse_owned_room_candidates(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[Any, str], dict[str, Any]]:
    """One candidate per (owned property, cleaned room name).

    «X 1/11», «X 2/11» and «X» are the same room, so they collapse to one
    entry named «X». The category is re-derived from the CLEANED name with
    the current normalizer (stored package categories may predate it). Like
    the discovery upsert, the samples come from the cheapest package; the
    first-seen job is the earliest run's (rows arrive oldest run first).
    """
    collapsed: dict[tuple[Any, str], dict[str, Any]] = {}
    for row in rows:
        name = clean_owned_room_type_name(row["room_type"])
        if not name:
            continue
        key = (row["owned_property_id"], _owned_room_name_key(name))
        current = collapsed.get(key)
        if current is None:
            collapsed[key] = {
                "owned_property_id": row["owned_property_id"],
                "first_seen_job_id": row["run_job_id"],
                "room_type": name,
                "room_type_category": normalize_room_type_category(name),
                **_room_samples(row),
            }
            continue
        price = row["price_per_night_eur"]
        current_price = current["sample_price_per_night_eur"]
        if price is not None and (current_price is None or price < current_price):
            current.update(_room_samples(row))
    return collapsed


@dataclass(frozen=True)
class OwnedRoomUnionResult:
    """Counts from one union pass.

    ``candidates`` is the number of distinct owner room types seen;
    ``inserted`` the catalog rows added (would-be rows on a dry run).
    """

    candidates: int
    inserted: int

# Shared SELECT column list for job reads so get_job/list_jobs shapes match
# (house pattern: alerts_repository.NOTIFICATION_COLUMNS).
JOB_COLUMNS = """
                        j.id, j.account_id, j.owned_property_id,
                        j.destination, j.raw_destination, j.canonical_destination,
                        j.check_in, j.check_out, j.adults, j.children, j.rooms,
                        j.job_type, j.room_type_category, COALESCE(j.filters_payload, '{}'::jsonb) AS filters_payload,
                        j.scheduled, j.status, j.requested_at, j.started_at, j.finished_at,
                        j.error_message, j.attempt_count, j.max_attempts, j.next_attempt_at,
                        j.result_summary, j.nearby_destinations, j.radius_km,
                        count(sr.id)::integer AS scrape_runs_count
"""


class QuotaExceededError(Exception):
    """Base for account scrape-quota refusals.

    Deliberately NOT a ValueError: routers map ValueError to 422 (bad input),
    while quota refusals are a transient capacity condition handled centrally
    by the app-level exception handler in ``api.main`` (429).
    """


class TooManyActiveJobsError(QuotaExceededError):
    """Raised when an account already has the maximum queued/running jobs."""


class DailyQuotaExceededError(QuotaExceededError):
    """Raised when an account has exhausted its daily scrape-job quota."""


class ScrapeJobRepository:
    """PostgreSQL repository for RoomRate scrape-job orchestration."""

    def __init__(
        self,
        max_concurrent_jobs_per_account: int = 2,
        max_daily_jobs_per_account: int = 20,
        max_attempts: int = 3,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 900,
        engine_factory: Callable[[], Engine] | None = None,
    ):
        # Limits are injected (from settings via dependencies) so the
        # repository stays decoupled from application configuration.
        self.max_concurrent_jobs_per_account = max_concurrent_jobs_per_account
        self.max_daily_jobs_per_account = max_daily_jobs_per_account
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        # Optional engine seam (same design as the injected connection factory
        # in RoomRatesRepository/PriceHistoryRepository). None falls back to
        # the module-level get_engine inside _engine().
        self._engine_factory = engine_factory

    def _engine(self) -> Engine:
        """Injected factory when present, else the module-level get_engine.

        get_engine is deliberately resolved as a module global at call time
        (not bound at import/def time) so tests that monkeypatch it keep
        working.
        """
        return self._engine_factory() if self._engine_factory else get_engine()

    def create_job(
        self,
        account_id: uuid.UUID,
        request: ScrapeJobCreate,
        scheduled: bool = False,
    ) -> dict[str, Any]:
        """Create a queued scrape job for one account, enforcing account quotas.

        ``scheduled`` marks scheduler-enqueued jobs (rotation fairness and
        scout-cache sharing); it is never client-settable — only
        ``ScrapeJobService.create_job`` passes it through.
        """
        job_id = uuid.uuid4()
        with self._engine().begin() as connection:
            self._enforce_account_limits(connection, account_id)
            if request.owned_property_id and not self._owned_property_belongs_to_account(
                connection,
                account_id=account_id,
                owned_property_id=request.owned_property_id,
            ):
                raise ValueError("owned_property_id does not belong to account")

            try:
                row = self._insert_job(
                    connection, job_id, account_id, request, scheduled, self.max_attempts
                )
            except IntegrityError as exc:
                # Racing duplicate caught by the partial unique index
                # uq_roomrate_scrape_jobs_active_market.
                if "uq_roomrate_scrape_jobs_active_market" in str(exc.orig or exc):
                    raise TooManyActiveJobsError(
                        "An identical scrape job is already queued or running for this market"
                    ) from exc
                raise
        return {**dict(row), "scrape_runs_count": 0}

    @staticmethod
    def _insert_job(
        connection: Any,
        job_id: uuid.UUID,
        account_id: uuid.UUID,
        request: ScrapeJobCreate,
        scheduled: bool,
        max_attempts: int,
    ) -> Any:
        return connection.execute(
            text(
                """
                INSERT INTO roomrate_scrape_jobs (
                    id, account_id, owned_property_id,
                    destination, raw_destination, canonical_destination, check_in, check_out,
                    adults, children, rooms, job_type, room_type_category, filters_payload,
                    scheduled, status, max_attempts, nearby_destinations, radius_km
                )
                VALUES (
                    :id, :account_id, :owned_property_id,
                    :destination, :raw_destination, :canonical_destination, :check_in, :check_out,
                    :adults, :children, :rooms, :job_type, :room_type_category, :filters_payload,
                    :scheduled, 'queued', :max_attempts, :nearby_destinations, :radius_km
                )
                RETURNING
                    id, account_id, owned_property_id,
                    destination, raw_destination, canonical_destination, check_in, check_out,
                    adults, children, rooms, job_type, room_type_category, filters_payload,
                    scheduled, status, requested_at, started_at, finished_at,
                    error_message, attempt_count, max_attempts, next_attempt_at,
                    nearby_destinations, radius_km
                """
            ).bindparams(
                bindparam("filters_payload", type_=JSONB),
                bindparam("nearby_destinations", type_=JSONB),
            ),
            {
                "id": job_id,
                "account_id": account_id,
                "owned_property_id": request.owned_property_id,
                "destination": request.destination,
                "raw_destination": request.raw_destination,
                "canonical_destination": request.canonical_destination,
                "check_in": request.check_in,
                "check_out": request.check_out,
                "adults": request.adults,
                "children": request.children,
                "rooms": request.rooms,
                "job_type": request.job_type,
                "room_type_category": request.room_type_category,
                "filters_payload": request.filters_payload,
                "scheduled": scheduled,
                "max_attempts": max_attempts,
                "nearby_destinations": request.nearby_destinations,
                "radius_km": request.radius_km,
            },
        ).mappings().one()

    def get_job(self, account_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch one scrape job scoped to an account."""
        with self._engine().connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT {JOB_COLUMNS}
                    FROM roomrate_scrape_jobs j
                    LEFT JOIN roomrate_scrape_runs sr ON sr.scrape_job_id = j.id
                    WHERE j.account_id = :account_id
                      AND j.id = :job_id
                    GROUP BY j.id
                    """
                ),
                {"account_id": account_id, "job_id": job_id},
            ).mappings().first()
        return dict(row) if row else None

    def list_jobs(self, account_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        """List recent scrape jobs scoped to an account."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT {JOB_COLUMNS}
                    FROM roomrate_scrape_jobs j
                    LEFT JOIN roomrate_scrape_runs sr ON sr.scrape_job_id = j.id
                    WHERE j.account_id = :account_id
                    GROUP BY j.id
                    ORDER BY j.requested_at DESC
                    LIMIT :limit
                    """
                ),
                {"account_id": account_id, "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def list_queued_jobs(self, limit: int = 3) -> list[dict[str, Any]]:
        """List the oldest queued jobs across ALL accounts for the executor.

        Deliberately not account-scoped: the background executor is a global
        safety net. claim_job makes double execution impossible, so handing
        the same job to two executors is harmless.
        """
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT account_id, id, job_type
                    FROM roomrate_scrape_jobs
                    WHERE status = 'queued'
                      AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                    ORDER BY requested_at ASC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def fail_stale_jobs(self, stale_after_minutes: int = 5) -> int:
        """Recover stale jobs and fail only exhausted attempts; returns count.

        Workers heartbeat every ~30s, so ``stale_after_minutes`` of silence
        means the worker process died (or lost DB access). The fenced
        complete/fail transitions guarantee a swept job can never be
        resurrected by a worker that later comes back. Retryable jobs return
        to the durable queue after an exponential delay. Like every attempt
        transition, the sweep drops the dead attempt's ``result_summary.progress``
        (other summary keys stay) so the map never shows its stale stage.
        """
        with self._engine().begin() as connection:
            rows = connection.execute(
                text(
                    """
                    UPDATE roomrate_scrape_jobs
                    SET
                        status = CASE
                            WHEN attempt_count < max_attempts THEN 'queued'
                            ELSE 'failed'
                        END,
                        error_message = 'heartbeat lost (sweeper)',
                        next_attempt_at = CASE
                            WHEN attempt_count < max_attempts THEN now() + make_interval(
                                secs => LEAST(
                                    :retry_max_seconds,
                                    :retry_base_seconds * CAST(
                                        power(2, GREATEST(attempt_count - 1, 0)) AS integer
                                    )
                                )
                            )
                            ELSE NULL
                        END,
                        started_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE started_at END,
                        finished_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE now() END,
                        claimed_by = CASE WHEN attempt_count < max_attempts THEN NULL ELSE claimed_by END,
                        heartbeat_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE heartbeat_at END,
                        result_summary = CASE WHEN result_summary IS NULL THEN NULL ELSE result_summary - 'progress' END,
                        updated_at = now()
                    WHERE status = 'running'
                      AND heartbeat_at < now() - make_interval(mins => :stale_after_minutes)
                    RETURNING id
                    """
                ),
                {
                    "stale_after_minutes": stale_after_minutes,
                    "retry_base_seconds": self.retry_base_seconds,
                    "retry_max_seconds": self.retry_max_seconds,
                },
            ).all()
        return len(rows)

    def claim_job(self, account_id: uuid.UUID, job_id: uuid.UUID, claimed_by: str) -> bool:
        """Atomically claim one queued job; returns False if another worker won.

        Round 6: the claim drops a previous attempt's ``result_summary.progress``
        (other summary keys stay), so a retried job starts at «Εκκίνηση…»
        instead of the old attempt's last stage.
        """
        with self._engine().begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE roomrate_scrape_jobs
                    SET
                        status = 'running',
                        started_at = now(),
                        attempt_count = attempt_count + 1,
                        next_attempt_at = NULL,
                        claimed_by = :claimed_by,
                        heartbeat_at = now(),
                        error_message = NULL,
                        result_summary = CASE WHEN result_summary IS NULL THEN NULL ELSE result_summary - 'progress' END,
                        updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :job_id
                      AND status = 'queued'
                    RETURNING id
                    """
                ),
                {"account_id": account_id, "job_id": job_id, "claimed_by": claimed_by[:255]},
            ).first()
        return row is not None

    def retry_or_fail(
        self,
        account_id: uuid.UUID,
        job_id: uuid.UUID,
        error_message: str,
        retry_delay_seconds: float,
    ) -> str | None:
        """Requeue a failed attempt or fail it when attempts are exhausted.

        Args:
            account_id: Tenant account that owns the job.
            job_id: Persisted scrape-job identifier.
            error_message: Failure context stored for operations and the UI.
            retry_delay_seconds: Earliest delay before another worker may claim it.

        Returns:
            ``queued`` or ``failed`` when the fenced transition applied;
            otherwise ``None``.

        Either way the failed attempt's ``result_summary.progress`` is dropped
        (other summary keys stay), so a queued or failed job never shows it.
        """
        with self._engine().begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE roomrate_scrape_jobs
                    SET
                        status = CASE
                            WHEN attempt_count < max_attempts THEN 'queued'
                            ELSE 'failed'
                        END,
                        next_attempt_at = CASE
                            WHEN attempt_count < max_attempts
                                THEN now() + make_interval(secs => :retry_delay_seconds)
                            ELSE NULL
                        END,
                        started_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE started_at END,
                        finished_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE now() END,
                        claimed_by = CASE WHEN attempt_count < max_attempts THEN NULL ELSE claimed_by END,
                        heartbeat_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE heartbeat_at END,
                        error_message = :error_message,
                        result_summary = CASE WHEN result_summary IS NULL THEN NULL ELSE result_summary - 'progress' END,
                        updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :job_id
                      AND status = 'running'
                    RETURNING status
                    """
                ),
                {
                    "account_id": account_id,
                    "job_id": job_id,
                    "error_message": error_message[:2_000],
                    "retry_delay_seconds": max(0.0, retry_delay_seconds),
                },
            ).mappings().first()
        return str(row["status"]) if row else None

    def heartbeat(self, account_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """Record liveness for one running job so stuck jobs can be detected."""
        with self._engine().begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE roomrate_scrape_jobs
                    SET heartbeat_at = now(), updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :job_id
                      AND status = 'running'
                    """
                ),
                {"account_id": account_id, "job_id": job_id},
            )

    def complete_job(
        self,
        account_id: uuid.UUID,
        job_id: uuid.UUID,
        refresh_owned_property_id: uuid.UUID | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> tuple[bool, int]:
        """Run post-scrape refresh work and mark the job completed atomically.

        Both the optional owned-property room-type refresh and the status
        update happen in ONE transaction so a crash between them can never
        leave the job stuck in 'running'.

        Returns ``(transition_applied, discovered_room_type_count)``. The
        UPDATE is fenced on ``status = 'running'`` so a worker that lost its
        job to the stale-job sweeper cannot resurrect it to 'completed'.
        """
        with self._engine().begin() as connection:
            discovered_count = 0
            if refresh_owned_property_id is not None:
                discovered_count = self._refresh_owned_property_room_types(
                    connection,
                    account_id=account_id,
                    job_id=job_id,
                    owned_property_id=refresh_owned_property_id,
                )
            result = connection.execute(
                text(
                    """
                    UPDATE roomrate_scrape_jobs
                    SET
                        status = 'completed',
                        finished_at = now(),
                        error_message = NULL,
                        result_summary = :result_summary,
                        updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :job_id
                      AND status = 'running'
                    """
                ).bindparams(bindparam("result_summary", type_=JSONB)),
                {"account_id": account_id, "job_id": job_id, "result_summary": result_summary},
            )
        return result.rowcount > 0, discovered_count

    def mark_failed(self, account_id: uuid.UUID, job_id: uuid.UUID, error_message: str) -> bool:
        """Mark a running scrape job as failed with a short error.

        Returns True when the transition applied; False when the job was no
        longer 'running' (e.g. already swept to failed), so the terminal state
        set by the sweeper is never overwritten. The failed attempt's
        ``result_summary.progress`` is dropped; other summary keys stay.
        """
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE roomrate_scrape_jobs
                    SET
                        status = 'failed',
                        finished_at = now(),
                        error_message = :error_message,
                        result_summary = CASE WHEN result_summary IS NULL THEN NULL ELSE result_summary - 'progress' END,
                        updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :job_id
                      AND status = 'running'
                    """
                ),
                {
                    "account_id": account_id,
                    "job_id": job_id,
                    "error_message": error_message[:2_000],
                },
            )
        return result.rowcount > 0

    @staticmethod
    def _refresh_owned_property_room_types(
        connection: Any,
        account_id: uuid.UUID,
        job_id: uuid.UUID,
        owned_property_id: uuid.UUID,
    ) -> int:
        """Connection-level room-type refresh so callers control the transaction.

        Syncs the discovered room catalog ONLY. It deliberately does not
        choose the property's baseline ``selected_room_type_category``: that
        is the user's explicit step-2 decision, written solely by
        ``OnboardingRepository.set_selected_room_type`` behind PUT
        /owned-property/{id}/selected-room-type. This method used to also
        auto-pick the cheapest discovered room; that made an account look
        fully configured the instant discovery finished, so the setup
        wizard's guard sent slow users straight past steps 2-3.
        """
        result = connection.execute(
            text(
                """
                INSERT INTO roomrate_owned_property_room_types (
                    id,
                    account_id,
                    owned_property_id,
                    first_seen_job_id,
                    room_type,
                    room_type_category,
                    sample_meals,
                    sample_free_cancellation,
                    sample_facilities,
                    sample_price_per_night_eur,
                    is_active
                )
                SELECT DISTINCT ON (rp.room_type, rp.room_type_category)
                    gen_random_uuid(),
                    sr.account_id,
                    :owned_property_id,
                    :job_id,
                    rp.room_type,
                    rp.room_type_category,
                    rp.meals,
                    rp.free_cancellation,
                    NULLIF(rp.package_payload ->> 'facilities', ''),
                    rp.price_per_night_eur,
                    true
                FROM roomrate_scrape_runs sr
                JOIN roomrate_rate_observations ro ON ro.scrape_run_id = sr.id
                JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
                WHERE sr.account_id = :account_id
                  AND sr.scrape_job_id = :job_id
                  AND rp.room_type_category IS NOT NULL
                ORDER BY rp.room_type, rp.room_type_category, rp.price_per_night_eur ASC
                ON CONFLICT (
                    account_id,
                    owned_property_id,
                    room_type,
                    room_type_category
                ) DO UPDATE SET
                    updated_at = now(),
                    sample_meals = EXCLUDED.sample_meals,
                    sample_free_cancellation = EXCLUDED.sample_free_cancellation,
                    sample_facilities = EXCLUDED.sample_facilities,
                    sample_price_per_night_eur = EXCLUDED.sample_price_per_night_eur,
                    is_active = true
                """
            ),
            {
                "account_id": account_id,
                "job_id": job_id,
                "owned_property_id": owned_property_id,
            },
        )
        return result.rowcount if result.rowcount >= 0 else 0

    def union_owned_room_types_from_job(self, account_id: uuid.UUID, job_id: uuid.UUID) -> int:
        """Union the owner's room types seen in one completed job; returns rows added.

        Runs in its OWN transaction after the job's completion committed, so a
        failure here can never roll back or fail the completion.
        """
        with self._engine().begin() as connection:
            return self.union_owned_room_types(connection, account_id=account_id, job_id=job_id).inserted

    @staticmethod
    def union_owned_room_types(
        connection: Any,
        account_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
        dry_run: bool = False,
    ) -> OwnedRoomUnionResult:
        """Add the owner's room types found in scraped runs to the owned catalog.

        Booking lists only the rooms that fit the searched party, and room
        discovery runs once (2 adults), so the catalog missed rooms such as
        the single. The owner's hotel also appears in competitor runs; its
        packages there are unioned in, matched by the owned property's
        ``display_name``. Connection-level so the completion hook
        (``job_id`` = that job's runs) and the backfill script (``job_id``
        None = every completed run of the account) share one code path.

        UNION only: existing rows are never updated, deactivated or renamed,
        ``selected_room_type_category`` is never touched, and a room whose
        cleaned name the property already has (case-insensitive, even when
        the stored name still carries a « N/M» artifact) is skipped — so
        re-running adds nothing.

        Args:
            connection: Open connection; the caller owns the transaction.
            account_id: Tenant account; every read and write is scoped to it.
            job_id: Limit the scan to this job's runs; None scans every
                completed run of the account.
            dry_run: Count what would be added without writing.

        Returns:
            OwnedRoomUnionResult with the distinct room types seen and the
            rows added (would be added on a dry run).
        """
        run_scope = OWNED_ROOM_JOB_RUN_SCOPE if job_id is not None else OWNED_ROOM_HISTORY_RUN_SCOPE
        params: dict[str, Any] = {"account_id": account_id}
        if job_id is not None:
            params["job_id"] = job_id
        rows = connection.execute(
            text(OWNED_ROOM_CANDIDATES_SQL.format(run_scope=run_scope)), params
        ).mappings().all()
        candidates = _collapse_owned_room_candidates(rows)
        if not candidates:
            return OwnedRoomUnionResult(candidates=0, inserted=0)

        existing_rows = connection.execute(
            text(
                """
                SELECT owned_property_id, room_type
                FROM roomrate_owned_property_room_types
                WHERE account_id = :account_id
                """
            ),
            {"account_id": account_id},
        ).mappings().all()
        existing_keys = {
            (row["owned_property_id"], _owned_room_name_key(clean_owned_room_type_name(row["room_type"])))
            for row in existing_rows
        }
        new_rooms = [room for key, room in candidates.items() if key not in existing_keys]
        if dry_run:
            return OwnedRoomUnionResult(candidates=len(candidates), inserted=len(new_rooms))

        inserted = 0
        for room in new_rooms:
            # DO NOTHING (not the discovery upsert's DO UPDATE): a row that
            # appeared concurrently is left exactly as it is.
            created = connection.execute(
                text(
                    """
                    INSERT INTO roomrate_owned_property_room_types (
                        id,
                        account_id,
                        owned_property_id,
                        first_seen_job_id,
                        room_type,
                        room_type_category,
                        sample_meals,
                        sample_free_cancellation,
                        sample_facilities,
                        sample_price_per_night_eur,
                        is_active
                    )
                    VALUES (
                        gen_random_uuid(),
                        :account_id,
                        :owned_property_id,
                        :first_seen_job_id,
                        :room_type,
                        :room_type_category,
                        :sample_meals,
                        :sample_free_cancellation,
                        :sample_facilities,
                        :sample_price_per_night_eur,
                        true
                    )
                    ON CONFLICT (
                        account_id,
                        owned_property_id,
                        room_type,
                        room_type_category
                    ) DO NOTHING
                    RETURNING id
                    """
                ),
                {"account_id": account_id, **room},
            ).first()
            if created is not None:
                inserted += 1
        return OwnedRoomUnionResult(candidates=len(candidates), inserted=inserted)

    def _enforce_account_limits(self, connection: Any, account_id: uuid.UUID) -> None:
        """Reject job creation when concurrency or daily quotas are exhausted."""
        # Serialize concurrent create_job calls per account: under READ
        # COMMITTED two simultaneous transactions could each count below the
        # cap and both insert. The transaction-scoped advisory lock makes the
        # count + insert pair effectively atomic per account (same pattern as
        # AccountsRepository.get_or_create_account_for_identity).
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(CAST(:account_id AS text)))"),
            {"account_id": account_id},
        )
        counts = connection.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE status IN ('queued', 'running')) AS active_jobs,
                    count(*) FILTER (WHERE requested_at >= date_trunc('day', now())) AS daily_jobs
                FROM roomrate_scrape_jobs
                WHERE account_id = :account_id
                """
            ),
            {"account_id": account_id},
        ).mappings().one()
        if counts["active_jobs"] >= self.max_concurrent_jobs_per_account:
            raise TooManyActiveJobsError(
                f"Too many active scrape jobs for this account "
                f"(limit {self.max_concurrent_jobs_per_account}); wait for running jobs to finish"
            )
        if counts["daily_jobs"] >= self.max_daily_jobs_per_account:
            raise DailyQuotaExceededError(
                f"Daily scrape-job quota exceeded for this account "
                f"(limit {self.max_daily_jobs_per_account} per day)"
            )

    @staticmethod
    def _owned_property_belongs_to_account(
        connection: Any,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
    ) -> bool:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT 1
                    FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                      AND is_active = true
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).scalar()
        )
