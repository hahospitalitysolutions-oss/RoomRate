"""LLM room-matching agent: one pass per job scores every owned room type.

Spec 2026-09-29 Μέρος Α, reworked 2026-11-13 (owner decision: matching runs
by itself right after the search, for ALL the owner's room types at once;
«Σύγκριση για» only switches which room type's results are shown).

Per job the service offers EVERY competitor room of the job to the model
(only a 600-room safety ceiling) together with EVERY active room type of the
owner's property as ``reference_rooms``. The candidates are split into
chunks scored in PARALLEL structured-output calls (up to 8 at once); the
chunk size shrinks with the number of room types so each call answers about
the same number of (candidate, room type) pairs. Each call carries:

* ``reference_rooms``: the owner's room types under a short index ``r``
  (name, category, capacity and room facts read from the name, sample
  price, room facilities, meals, cancellation);
* ``hotels``: the chunk's competitor properties under an index ``h``
  (property type, stars, review score and count, property facilities);
* ``candidates``: the chunk's competitor rooms under an index ``c`` (hotel
  ``h``, name, category, max persons, room facts, the room's own Booking
  facilities, rooms left, and a summary of its rate plans — price range,
  cancellation classes, meals, Genius).

The model answers by index only: per candidate one verdict (score 0-100 and
a yes/no ``comparable``) for every reference room, plus one short Greek
reasoning. The service maps the indices back (unknown/duplicate ones
dropped), clamps scores, derives ``category_match`` itself, and writes the
union of the successful chunks as a full replacement of every scored room
type's rows in ONE transaction. One ``roomrate_agent_runs`` audit row and
one quota unit per pass, however many room types and chunks it covers.

Failure policy (spec Α.5): a failed chunk leaves only its own rooms on the
statistical score — partial results are legitimate (the UI labels them
«Εκτίμηση AI (μερική)») and the run is audited ``ok`` with the failed chunk
numbers in ``error_message``. When EVERY chunk fails (or persistence fails)
the run writes no rows and is audited ``error``; skips (no key, quota,
already matched, another run in progress) write a ``skipped`` audit row
with the reason. ``run`` never raises to its caller, so neither the
scrape-job hook nor the endpoint can be broken by this path.

One run per job at a time: a lease row names the run scoring the job, so
the map's call right after the search never pays for a duplicate of the
post-scrape run still waiting on the model — it waits for that run's rows.

Anthropic SDK call shape, per chunk (authoritative for the installed
0.115.0 — keep it minimal, exactly like the pricing agent):
    client.with_options(max_retries=1, timeout=<matching timeout>)
          .messages.parse(
              model=..., max_tokens=..., system=SYSTEM_PROMPT,
              messages=[{"role": "user", "content": <json str>}],
              output_format=RoomMatchProposal,
          )
Only ``model``, ``max_tokens``, ``system``, ``messages``, ``output_format``
are sent — sampling/thinking parameters would 400 on claude-sonnet-5-5. The
explicit client timeout also keeps the SDK's non-streaming 10-minute guard
(which only applies at the default timeout) out of the way.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

from api.schemas.agents import RoomMatchProposal
from api.services.market_helpers import as_optional_float, category_match_for
from api.services.market_service import (
    AGENT_POOL_READ_LIMIT,
    RoomRateFilters,
    RoomRatesRepositoryProtocol,
)
from api.services.room_matching import attributes_from_dict, extract_room_attributes

logger = logging.getLogger(__name__)

# Output budget PER CHUNK, thinking included: claude-sonnet-5-5 thinks by
# default (effort high), and max_tokens caps thinking and answer together. A
# truncated answer fails parsing and costs only that chunk (its rooms keep
# the statistical score); only the tokens actually generated are billed.
_MAX_OUTPUT_TOKENS = 16_000
# A chunk answers about _MAX_PAIRS_PER_CHUNK (candidate, room type) verdicts:
# 40 candidates for up to 4 room types, fewer for more room types (never
# below _MIN_CHUNK_SIZE). One verdict is ~12 output tokens plus one ~40-token
# reasoning per candidate, so a full chunk stays far inside the budget.
_MAX_CHUNK_SIZE = 40
_MAX_PAIRS_PER_CHUNK = 160
# Room types offered per pass. A Booking listing rarely has more; the rest
# (logged) keep the statistical comparison set. The property's selected
# room type is always first, so it is never the one left out.
_MAX_REFERENCE_ROOMS = 10
_MIN_CHUNK_SIZE = _MAX_PAIRS_PER_CHUNK // _MAX_REFERENCE_ROOMS
_MAX_PARALLEL_CALLS = 8
_MAX_REASONING_CHARS = 120
# Facility lists can be long; the first ones carry the signal.
_MAX_ROOM_FACILITIES = 40
_MAX_HOTEL_FACILITIES = 30
# Package-row budget read from one job. Shared with the read side, which
# reads this many broad-pool rows before the verdicts narrow them, so it
# never drops a room the agent judged.
_MAX_JOB_ROWS = AGENT_POOL_READ_LIMIT
# Cost/latency guard; rooms beyond it stay statistical.
_MAX_CANDIDATES = 600
# One SDK retry at most: the default 2 retries turned one timeout into a
# ~3x block of the post-scrape hook before the (silent) statistical fallback.
_MAX_RETRIES = 1
# One run per job at a time (lease, migration 20260930_0028). A lease
# outlives the longest possible run — every chunk wave at the full per-call
# timeout with its retry, plus database time — so only a run that died
# without releasing it ever leaves a stale one behind.
_MAX_CHUNK_WAVES = math.ceil(math.ceil(_MAX_CANDIDATES / _MIN_CHUNK_SIZE) / _MAX_PARALLEL_CALLS)
_LEASE_MARGIN_SECONDS = 120.0
# The endpoint waits this long for a run already scoring the job: under the
# browser's 180 s POST timeout, and past a typical one-wave run.
_MAX_WAIT_FOR_RUN_IN_FLIGHT_SECONDS = 150.0
_LEASE_POLL_SECONDS = 3.0

ROOM_MATCHING_SYSTEM_PROMPT = (
    "Είσαι αναλυτής φιλοξενίας που αντιστοιχίζει δωμάτια ανταγωνιστών με "
    "ΟΛΟΥΣ τους τύπους δωματίων ενός καταλύματος σε ένα πέρασμα. Στο μήνυμα "
    "χρήστη θα βρεις: reference_rooms, τα δωμάτια του ιδιοκτήτη, το καθένα "
    "με ακέραιο δείκτη r· hotels, τα καταλύματα των υποψηφίων με δείκτη h "
    "(τύπος, αστέρια, βαθμολογία, παροχές)· και candidates, τα δωμάτια των "
    "ανταγωνιστών με δείκτη c, το h του καταλύματός τους, κατηγορία, μέγιστα "
    "άτομα, χαρακτηριστικά (θέα, μπαλκόνι, τετραγωνικά, κρεβάτια), παροχές "
    "δωματίου και σύνοψη πλάνων (εύρος τιμών, κλάσεις ακύρωσης, διατροφή, "
    "Genius). Για ΚΑΘΕ υποψήφιο επίστρεψε μία εγγραφή στο matches με: c "
    "ΑΚΡΙΒΩΣ όπως δίνεται· scores με ΜΙΑ εγγραφή για ΚΑΘΕ δωμάτιο αναφοράς, "
    "με r, score ακέραιο 0-100 για το πόσο συγκρίσιμος είναι ο υποψήφιος με "
    "εκείνο το δωμάτιο (κατηγορία, χωρητικότητα, θέα, μέγεθος, παροχές, "
    "επίπεδο τιμής και πλάνα) και comparable true/false· και reasoning έως "
    "120 χαρακτήρες στα ελληνικά που λέει με ποιο δωμάτιο αναφοράς "
    "αντιστοιχεί ο υποψήφιος και γιατί, ή γιατί με κανένα. Το comparable "
    "είναι η ετυμηγορία σου για το αν ένας επισκέπτης που ψάχνει το "
    "συγκεκριμένο δωμάτιο αναφοράς θα θεωρούσε ΠΡΑΓΜΑΤΙΚΑ τον υποψήφιο "
    "εναλλακτική επιλογή: συμβατή χωρητικότητα (ίδιος αριθμός επισκεπτών), "
    "παρόμοια κλάση, μέγεθος και διατροφή. Σουίτα, διαμέρισμα, βίλα ή μονάδα "
    "με περισσότερα υπνοδωμάτια ΔΕΝ είναι comparable με standard "
    "δίκλινο/twin, εκτός αν είναι πραγματικά ισοδύναμη· ούτε μονόκλινο με "
    "δίκλινο. Ένας υποψήφιος μπορεί να είναι comparable με περισσότερα από "
    "ένα δωμάτια αναφοράς ή με κανένα. Το comparable ορίζει ποια δωμάτια "
    "μπαίνουν στο σύνολο σύγκρισης κάθε τύπου δωματίου του ιδιοκτήτη. "
    "Βασίσου αποκλειστικά στα δεδομένα της εισόδου: μην εφευρίσκεις δείκτες "
    "c ή r και μην επαναλαμβάνεις τον ίδιο δείκτη."
)


class RoomMatchingOutputError(RuntimeError):
    """The model returned no usable structured output (refusal/empty parse)."""


class RoomMatchingChunksFailedError(RuntimeError):
    """Every chunk of a run failed, so the whole run falls back to statistics."""


@dataclass(frozen=True)
class _Chunk:
    """One parallel call's input plus its index -> stored-identity map."""

    number: int  # 1-based, as reported in the audit row
    payload: dict[str, Any]
    # identities[c] = {"property_id", "room_type", "room_type_category"}
    identities: list[dict[str, Any]]


@dataclass(frozen=True)
class RoomMatchRunResult:
    """Outcome of one matching run, for both the endpoint and the auto hook."""

    status: Literal["completed", "error", "skipped"]
    matches_written: int = 0
    # Owned room types holding agent rows for the job after this call.
    room_types_matched: int = 0
    # "no_api_key" | "quota_exceeded" | "already_matched" | "in_progress"
    # (skips only; "in_progress": another run is still scoring the job).
    skip_reason: str | None = None


class RoomMatchingAgentService:
    """Run the matching agent end-to-end: input, LLM, validation, persistence."""

    def __init__(
        self,
        match_repository: Any,
        onboarding_repository: Any,
        rates_repository_factory: Callable[[uuid.UUID], RoomRatesRepositoryProtocol],
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_daily_runs: int = 30,
        client_factory: Callable[[], Any] | None = None,
        max_wait_seconds: float = _MAX_WAIT_FOR_RUN_IN_FLIGHT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.match_repository = match_repository
        self.onboarding_repository = onboarding_repository
        # Built per account: RoomRatesRepository is account-scoped, and the
        # post-scrape hook runs outside any request context.
        self.rates_repository_factory = rates_repository_factory
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_daily_runs = max_daily_runs
        self._client_factory = client_factory
        self.lease_seconds = (
            _MAX_CHUNK_WAVES * (1 + _MAX_RETRIES) * timeout_seconds + _LEASE_MARGIN_SECONDS
        )
        self.max_wait_seconds = max_wait_seconds
        self._sleep = sleep
        self._clock = clock
        # Lazy, shared SDK client (same rationale as the pricing agent: a
        # fresh anthropic.Anthropic per run would pay pool + TLS every time).
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def run_for_job(
        self,
        account_id: uuid.UUID,
        job: Any,
        *,
        only_missing: bool,
        wait_for_run_in_flight: bool,
    ) -> RoomMatchRunResult | None:
        """Score a completed competitor search against all the property's room types.

        None when there is nothing to run against: no owned property on the
        job, or no active room type discovered for it yet. ``only_missing``
        scores only the room types without rows for the job (the automatic
        runs); False replaces every room type's rows («Νέο ταίριασμα»).
        """
        if not self.api_key:
            # Cheapest possible exit: the common no-credentials deployment
            # must not pay the room lookups per completed scrape — only the
            # best-effort ``skipped`` audit row that explains the absence.
            return self._skip(account_id, getattr(job, "id", None), "no_api_key")
        owned_property_id = getattr(job, "owned_property_id", None)
        if not owned_property_id:
            logger.info(
                "Room matching skipped: scrape job has no owned property: "
                "account_id=%s scrape_job_id=%s",
                account_id,
                getattr(job, "id", None),
            )
            return None
        owned_rooms = self.owned_rooms(account_id, owned_property_id)
        if not owned_rooms:
            logger.info(
                "Room matching skipped: no room type discovered for the property: "
                "account_id=%s owned_property_id=%s",
                account_id,
                owned_property_id,
            )
            return None
        return self.run(
            account_id,
            job.id,
            owned_rooms,
            only_missing=only_missing,
            wait_for_run_in_flight=wait_for_run_in_flight,
        )

    def run_for_completed_job(self, account_id: uuid.UUID, job: Any) -> RoomMatchRunResult | None:
        """Automatic post-scrape trigger: every room type still without rows.

        Idempotent per job: room types that already hold agent rows are not
        re-billed, and a run already scoring the job is not duplicated.
        """
        return self.run_for_job(
            account_id, job, only_missing=True, wait_for_run_in_flight=False
        )

    def owned_rooms(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """The property's active room types, the selected one first.

        Each row is a ``roomrate_owned_property_room_types`` row with ``id``
        and ``owned_property_id``; rows without an id are dropped.
        """
        rooms = [
            dict(room)
            for room in self.onboarding_repository.list_room_types(account_id, owned_property_id)
            if room.get("id")
        ]
        for room in rooms:
            room.setdefault("owned_property_id", owned_property_id)
        selected = self.onboarding_repository.get_selected_room_type(account_id, owned_property_id)
        selected_id = str(selected.get("id")) if selected and selected.get("id") else None
        unique: dict[str, dict[str, Any]] = {}
        for room in rooms:
            unique.setdefault(str(room["id"]), room)
        ordered = list(unique.values())
        ordered.sort(key=lambda room: 0 if str(room["id"]) == selected_id else 1)
        return ordered

    def run(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_rooms: list[dict[str, Any]],
        *,
        only_missing: bool = False,
        wait_for_run_in_flight: bool = True,
    ) -> RoomMatchRunResult:
        """Score one job against the given owned room types; never raises.

        ``owned_rooms`` are ``roomrate_owned_property_room_types`` rows (with
        ``id`` and ``owned_property_id``), the selected room type first.

        One run per job at a time: a lease names the run scoring it. While
        another run holds it, ``wait_for_run_in_flight=False`` (the
        post-scrape hook) skips with ``in_progress``; True (the endpoint)
        waits for that run's rows instead of paying for a duplicate.
        """
        if not self.api_key:
            return self._skip(account_id, scrape_job_id, "no_api_key")
        if not owned_rooms:
            return RoomMatchRunResult(status="completed")
        anchor = _lease_anchor(owned_rooms)
        lease: uuid.UUID | None = None
        try:
            if only_missing and not self._missing_rooms(account_id, scrape_job_id, owned_rooms):
                return self._already_matched(owned_rooms)

            # One quota unit per RUN, however many room types and chunks.
            runs_today = self._count_runs_today(account_id)
            if runs_today is not None and runs_today >= self.max_daily_runs:
                return self._skip(account_id, scrape_job_id, "quota_exceeded")

            claimed, lease = self._acquire_lease(account_id, scrape_job_id, anchor)
            if not claimed:
                if not wait_for_run_in_flight:
                    return self._skip(account_id, scrape_job_id, "in_progress")
                return self._await_run_in_flight(account_id, scrape_job_id, anchor, owned_rooms)

            references = owned_rooms
            if only_missing:
                # The run that held the lease may have finished between the
                # first check and this claim: its rows stand.
                references = self._missing_rooms(account_id, scrape_job_id, owned_rooms)
                if not references:
                    return self._already_matched(owned_rooms)
            references = _capped_references(references)

            rows = self.rates_repository_factory(account_id).fetch_room_rates(
                self._job_filters(scrape_job_id, references)
            )
            chunks = _build_agent_chunks(references, rows)
            if not chunks:
                # Nothing to score (empty job or legacy rows without ids):
                # still honour replace-all so a stale set cannot linger.
                self.match_repository.replace_job_matches(
                    account_id=account_id,
                    scrape_job_id=scrape_job_id,
                    model_version=self.model,
                    matches_by_room_type={room["id"]: [] for room in references},
                )
                self._record_run(account_id, scrape_job_id, "ok", None)
                return RoomMatchRunResult(
                    status="completed",
                    room_types_matched=self._room_types_matched(
                        account_id, scrape_job_id, owned_rooms
                    ),
                )

            matches_by_room_type, failed_chunks = self._score_chunks(
                account_id, scrape_job_id, chunks, references
            )
            written = self.match_repository.replace_job_matches(
                account_id=account_id,
                scrape_job_id=scrape_job_id,
                model_version=self.model,
                matches_by_room_type=matches_by_room_type,
            )
            self._record_run(
                account_id, scrape_job_id, "ok", _partial_run_note(failed_chunks, len(chunks))
            )
            return RoomMatchRunResult(
                status="completed",
                matches_written=written,
                room_types_matched=self._room_types_matched(
                    account_id, scrape_job_id, owned_rooms
                ),
            )
        except Exception as exc:  # noqa: BLE001 — spec Α.5: never raise out of the run
            logger.warning(
                "Room-matching agent run failed; statistical scores keep serving: "
                "account_id=%s scrape_job_id=%s",
                account_id,
                scrape_job_id,
                exc_info=True,
            )
            self._record_run(account_id, scrape_job_id, "error", _short_error(exc))
            return RoomMatchRunResult(status="error", matches_written=0)
        finally:
            if lease is not None:
                self._release_lease(account_id, scrape_job_id, anchor, lease)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _job_filters(
        scrape_job_id: uuid.UUID, references: list[dict[str, Any]]
    ) -> RoomRateFilters:
        """Every package row of the job but the own property's.

        ``include_similar=True`` keeps every non-single room; single rooms
        join the pool only when the owner sells one (``single`` as the
        requested category adds its pool on top).
        """
        categories = [
            str(room.get("room_type_category") or "").strip() or None for room in references
        ]
        category = "single" if "single" in categories else next((c for c in categories if c), None)
        return RoomRateFilters(
            scrape_job_id=scrape_job_id,
            owned_property_id=references[0].get("owned_property_id"),
            room_type_category=category,
            include_similar=True,
            limit=_MAX_JOB_ROWS,
        )

    def _missing_rooms(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_rooms: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """The owned room types without agent rows for the job yet."""
        matched = {str(room_id) for room_id in self.match_repository.matched_room_type_ids(
            account_id, scrape_job_id
        )}
        return [room for room in owned_rooms if str(room["id"]) not in matched]

    def _room_types_matched(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_rooms: list[dict[str, Any]],
    ) -> int:
        """How many of the owned room types hold agent rows now (best-effort)."""
        try:
            matched = {
                str(room_id)
                for room_id in self.match_repository.matched_room_type_ids(
                    account_id, scrape_job_id
                )
            }
        except Exception:
            logger.warning(
                "Room-matching row check failed: account_id=%s scrape_job_id=%s",
                account_id,
                scrape_job_id,
                exc_info=True,
            )
            return 0
        return sum(1 for room in owned_rooms if str(room["id"]) in matched)

    def _count_runs_today(self, account_id: uuid.UUID) -> int | None:
        """Today's run count, or None (unmetered) when the check itself fails."""
        try:
            return self.match_repository.count_runs_today(account_id)
        except Exception:
            # Same posture as the pricing quota: serving beats refusing every
            # account, and the failure is loud in the logs.
            logger.warning(
                "Room-matching quota check failed; serving unmetered: account_id=%s",
                account_id,
                exc_info=True,
            )
            return None

    def _acquire_lease(
        self, account_id: uuid.UUID, scrape_job_id: uuid.UUID, anchor: Any
    ) -> tuple[bool, uuid.UUID | None]:
        """``(claimed, holder)``: claimed is False while another live run holds the job.

        A lease store that fails (e.g. a database without migration
        20260930_0028) must not stop matching: the run goes ahead unleased
        (``(True, None)``), exactly as before leases existed.
        """
        try:
            holder = self.match_repository.acquire_lease(
                account_id=account_id,
                scrape_job_id=scrape_job_id,
                owned_room_type_id=anchor,
                stale_after_seconds=self.lease_seconds,
            )
        except Exception:
            logger.warning(
                "Room-matching lease unavailable; running unleased: "
                "account_id=%s scrape_job_id=%s",
                account_id,
                scrape_job_id,
                exc_info=True,
            )
            return True, None
        return holder is not None, holder

    def _release_lease(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        anchor: Any,
        holder: uuid.UUID,
    ) -> None:
        """Best-effort: an unreleased lease only goes stale after ``lease_seconds``."""
        try:
            self.match_repository.release_lease(
                account_id=account_id,
                scrape_job_id=scrape_job_id,
                owned_room_type_id=anchor,
                holder=holder,
            )
        except Exception:
            logger.warning(
                "Room-matching lease release failed; it expires on its own: "
                "account_id=%s scrape_job_id=%s",
                account_id,
                scrape_job_id,
                exc_info=True,
            )

    def _await_run_in_flight(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        anchor: Any,
        owned_rooms: list[dict[str, Any]],
    ) -> RoomMatchRunResult:
        """Wait (bounded) for the run already scoring this job, then report its rows.

        The endpoint's caller (the map right after a search, «Νέο
        ταίριασμα») gets that run's verdicts instead of paying for a
        duplicate. Still running at the deadline: ``skipped``/``in_progress``
        — its rows appear on a later read. Finished without rows (it
        failed): ``error``, the statistical fallback, exactly as if this
        call had failed.
        """
        deadline = self._clock() + self.max_wait_seconds
        while self._lease_still_held(account_id, scrape_job_id, anchor):
            if self._clock() >= deadline:
                return self._skip(account_id, scrape_job_id, "in_progress")
            self._sleep(_LEASE_POLL_SECONDS)
        matched = self._room_types_matched(account_id, scrape_job_id, owned_rooms)
        if matched:
            return RoomMatchRunResult(status="completed", room_types_matched=matched)
        return RoomMatchRunResult(status="error", matches_written=0)

    def _lease_still_held(
        self, account_id: uuid.UUID, scrape_job_id: uuid.UUID, anchor: Any
    ) -> bool:
        """Whether the other run still holds the job; a failing check stops the wait."""
        try:
            return self.match_repository.lease_held(
                account_id=account_id,
                scrape_job_id=scrape_job_id,
                owned_room_type_id=anchor,
                stale_after_seconds=self.lease_seconds,
            )
        except Exception:
            logger.warning(
                "Room-matching lease check failed; reading the rows as they are: "
                "account_id=%s scrape_job_id=%s",
                account_id,
                scrape_job_id,
                exc_info=True,
            )
            return False

    def _skip(
        self, account_id: uuid.UUID, scrape_job_id: uuid.UUID | None, reason: str
    ) -> RoomMatchRunResult:
        """Audit a skipped run (reason in ``error_message``) and report it.

        The audit row answers «γιατί δεν βγήκε AI εκτίμηση» for skips too;
        ``count_runs_today`` ignores ``skipped`` rows, so skips never burn
        the daily quota.
        """
        self._record_run(account_id, scrape_job_id, "skipped", reason)
        return RoomMatchRunResult(status="skipped", skip_reason=reason)

    @staticmethod
    def _already_matched(owned_rooms: list[dict[str, Any]]) -> RoomMatchRunResult:
        """Every room type already holds verdicts: nothing to do, nothing to audit.

        Not an audit row: the map asks this once per job per visit, and the
        verdicts themselves are the record — a row per visit would only bury
        the runs that matter.
        """
        return RoomMatchRunResult(
            status="skipped", skip_reason="already_matched", room_types_matched=len(owned_rooms)
        )

    def _score_chunks(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        chunks: list[_Chunk],
        references: list[dict[str, Any]],
    ) -> tuple[dict[Any, list[dict[str, Any]]], list[tuple[int, Exception]]]:
        """Score every chunk in parallel; a failed chunk only loses its own rooms.

        Returns the validated rows of the successful chunks grouped by owned
        room type (every reference present, possibly empty) plus the
        ``(chunk number, error)`` of each failed one. Raises
        RoomMatchingChunksFailedError when EVERY chunk failed, so a total
        loss takes the run's error path (no rows, ``error`` audit).
        """
        # Resolved once on the calling thread; the SDK client (httpx pool)
        # is safe to share across the worker threads.
        base_client = self._client_factory() if self._client_factory else self._get_client()

        def score(chunk: _Chunk) -> tuple[dict[Any, list[dict[str, Any]]], Exception | None]:
            try:
                proposal = self._call_model(base_client, chunk.payload)
                return _validated_matches(proposal, chunk.identities, references), None
            except Exception as exc:  # noqa: BLE001 — one chunk must not sink the others
                logger.warning(
                    "Room-matching chunk %d/%d failed; its rooms keep the statistical "
                    "score: account_id=%s scrape_job_id=%s",
                    chunk.number,
                    len(chunks),
                    account_id,
                    scrape_job_id,
                    exc_info=True,
                )
                return {}, exc

        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_CALLS, len(chunks))) as pool:
            # map() keeps the chunk order, so the merged rows are deterministic.
            outcomes = list(pool.map(score, chunks))

        matches_by_room_type: dict[Any, list[dict[str, Any]]] = {
            room["id"]: [] for room in references
        }
        for chunk_matches, _ in outcomes:
            for room_id, rows in chunk_matches.items():
                matches_by_room_type[room_id].extend(rows)
        failed = [
            (chunk.number, error)
            for chunk, (_, error) in zip(chunks, outcomes)
            if error is not None
        ]
        if len(failed) == len(chunks):
            number, error = failed[0]
            raise RoomMatchingChunksFailedError(
                f"all {len(chunks)} chunk(s) failed; chunk {number}: {_short_error(error)}"
            )
        return matches_by_room_type, failed

    def _call_model(self, base_client: Any, input_payload: dict[str, Any]) -> RoomMatchProposal:
        """One structured-output call; raises RoomMatchingOutputError on refusal."""
        # Per-call options on a copy that shares the connection pool: the
        # matching timeout (not the pricing one) and at most one retry.
        client = base_client.with_options(max_retries=_MAX_RETRIES, timeout=self.timeout_seconds)
        response = client.messages.parse(
            model=self.model,
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=ROOM_MATCHING_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    # ensure_ascii=False: the payload is full of Greek room
                    # names; escaped \\u sequences would triple its tokens.
                    "content": json.dumps(input_payload, ensure_ascii=False, default=str),
                }
            ],
            output_format=RoomMatchProposal,
        )
        parsed = getattr(response, "parsed_output", None)
        stop_reason = getattr(response, "stop_reason", None)
        if parsed is None or stop_reason == "refusal":
            raise RoomMatchingOutputError(
                f"no usable structured output (stop_reason={stop_reason})"
            )
        return parsed

    def _record_run(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID | None,
        status: str,
        error_message: str | None,
    ) -> None:
        """Best-effort audit: losing the run row must not fail the run."""
        try:
            self.match_repository.insert_run(
                account_id=account_id,
                scrape_job_id=scrape_job_id,
                status=status,
                model=self.model,
                error_message=error_message,
            )
        except Exception:
            logger.error(
                "Room-matching run audit failed; emitting to the log instead: "
                "account_id=%s scrape_job_id=%s status=%s error=%s",
                account_id,
                scrape_job_id,
                status,
                error_message,
                exc_info=True,
            )

    def _get_client(self) -> Any:
        """Return the cached SDK client, building it once per service instance."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_seconds)


# ---------------------------------------------------------------------------
# Input building and output validation (pure, unit-testable helpers)
# ---------------------------------------------------------------------------


def _lease_anchor(owned_rooms: list[dict[str, Any]]) -> Any:
    """The room type id that keys the job's lease: the same for every caller.

    The lease table is keyed per (job, owned room type); a job-wide run
    claims it under the smallest id among the property's room types, so
    the post-scrape run and the map's call always contend on one row.
    """
    return min((room["id"] for room in owned_rooms), key=str)


def _capped_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most _MAX_REFERENCE_ROOMS room types per pass (selected one first)."""
    if len(references) > _MAX_REFERENCE_ROOMS:
        logger.warning(
            "Room-matching reference ceiling reached: %d of %d room types scored "
            "(the rest keep the statistical comparison set)",
            _MAX_REFERENCE_ROOMS,
            len(references),
        )
    return references[:_MAX_REFERENCE_ROOMS]


def _chunk_size(reference_count: int) -> int:
    """Candidates per call so each call answers ~_MAX_PAIRS_PER_CHUNK verdicts."""
    return max(
        _MIN_CHUNK_SIZE,
        min(_MAX_CHUNK_SIZE, _MAX_PAIRS_PER_CHUNK // max(1, reference_count)),
    )


def _room_attributes(row: dict[str, Any]) -> Any:
    """Stored write-time attributes win; sparse rows fall back to the label."""
    stored = row.get("room_attributes")
    if isinstance(stored, dict) and stored:
        return attributes_from_dict(stored)
    return extract_room_attributes(str(row.get("room_type") or ""))


def _facility_list(value: Any, limit: int) -> list[str]:
    """Split a pipe-separated facilities string (or a list) into unique names."""
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = str(value or "").replace(";", "|").split("|")
    seen: set[str] = set()
    facilities: list[str] = []
    for part in parts:
        name = part.strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        facilities.append(name)
    return facilities[:limit]


def _room_facts(attrs: Any) -> dict[str, Any]:
    facts = {
        "view": attrs.view,
        "has_balcony": attrs.has_balcony,
        "size_sqm": attrs.size_sqm,
        "bed_hint": attrs.bed_hint,
    }
    return {key: value for key, value in facts.items() if value is not None}


def _reference_payload(index: int, owned_room: dict[str, Any]) -> dict[str, Any]:
    """One owned room type as the model sees it."""
    attrs = extract_room_attributes(str(owned_room.get("room_type") or ""))
    return {
        "r": index,
        "room_type": owned_room.get("room_type"),
        "room_type_category": owned_room.get("room_type_category"),
        "max_persons": attrs.capacity,
        "room_facts": _room_facts(attrs),
        "sample_price_per_night_eur": as_optional_float(
            owned_room.get("sample_price_per_night_eur")
        ),
        "facilities": _facility_list(owned_room.get("sample_facilities"), _MAX_ROOM_FACILITIES),
        "meals": owned_room.get("sample_meals"),
        "free_cancellation": owned_room.get("sample_free_cancellation"),
    }


def _hotel_summary(row: dict[str, Any]) -> dict[str, Any]:
    """The property-level facts of a competitor, taken from any of its rows."""
    summary = {
        "name": str(row.get("hotel_name") or ""),
        "property_type": row.get("property_type"),
        "stars": as_optional_float(row.get("stars")),
        "review_score": as_optional_float(row.get("review_score")),
        "review_count": row.get("review_count"),
        "facilities": _facility_list(row.get("facilities"), _MAX_HOTEL_FACILITIES),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def _build_agent_chunks(
    references: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[_Chunk]:
    """Build the per-call payloads (chunk size from the number of room types).

    Rooms are keyed by ``(property_id, casefolded room name)`` so the plans
    of one room collapse into one candidate; each chunk's ``identities``
    keeps the exact stored spelling behind every index ``c`` (the read-side
    join relies on it). Ids never reach the model. Rows without a
    ``property_id`` (legacy source) cannot be persisted, so they are not
    offered as candidates at all.
    """
    reference_rooms = [_reference_payload(index, room) for index, room in enumerate(references)]

    hotels: dict[str, dict[str, Any]] = {}
    grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        property_id = row.get("property_id")
        room_type = str(row.get("room_type") or "").strip()
        if not property_id or not room_type:
            continue
        property_key = str(property_id)
        hotels.setdefault(property_key, _hotel_summary(row))
        room_key = (property_key, room_type.casefold())
        grouped_rows.setdefault(room_key, []).append(row)
        identities.setdefault(
            room_key,
            {
                "property_id": property_id,
                "room_type": room_type,
                "room_type_category": row.get("room_type_category"),
            },
        )

    summaries = {room_key: _room_summary(room_rows) for room_key, room_rows in grouped_rows.items()}
    selected_keys = _select_candidates(
        summaries, [room.get("room_type_category") for room in references]
    )
    if len(selected_keys) < len(summaries):
        # Practically never: the ceiling is above a large real job.
        logger.warning(
            "Room-matching safety ceiling reached: offered=%d of %d rooms "
            "(the rest keep their statistical score)",
            len(selected_keys),
            len(summaries),
        )

    # Only offered rooms get an index, so a left-out room can never gain an
    # agent score; ``c`` is the position inside its own chunk's list and
    # ``h`` the position of its hotel inside the chunk's ``hotels``.
    size = _chunk_size(len(references))
    chunks: list[_Chunk] = []
    for start in range(0, len(selected_keys), size):
        chunk_keys = selected_keys[start : start + size]
        hotel_index: dict[str, int] = {}
        for room_key in chunk_keys:
            hotel_index.setdefault(room_key[0], len(hotel_index))
        candidates = [
            {"c": index, "h": hotel_index[room_key[0]], **summaries[room_key]}
            for index, room_key in enumerate(chunk_keys)
        ]
        chunks.append(
            _Chunk(
                number=len(chunks) + 1,
                payload={
                    "reference_rooms": reference_rooms,
                    "hotels": [
                        {"h": index, **hotels[property_key]}
                        for property_key, index in hotel_index.items()
                    ],
                    "candidates": candidates,
                },
                identities=[identities[room_key] for room_key in chunk_keys],
            )
        )
    return chunks


def _room_summary(room_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse one room's package rows into its facts plus a plan summary."""
    first = room_rows[0]
    attrs = _room_attributes(first)
    prices = [
        price
        for price in (as_optional_float(row.get("price_per_night_eur")) for row in room_rows)
        if price is not None
    ]
    discounted = [
        price
        for price in (
            as_optional_float(row.get("discounted_price_per_night_eur")) for row in room_rows
        )
        if price is not None
    ]
    cancellation_types = sorted(
        {
            str(row.get("cancellation_type")).strip()
            for row in room_rows
            if str(row.get("cancellation_type") or "").strip()
        }
    )
    meals_options = sorted(
        {str(row.get("meals")).strip() for row in room_rows if str(row.get("meals") or "").strip()}
    )
    room_facilities = _facility_list(
        next((row.get("room_facilities") for row in room_rows if row.get("room_facilities")), None),
        _MAX_ROOM_FACILITIES,
    )
    rooms_left = [
        int(value)
        for value in (as_optional_float(row.get("rooms_left")) for row in room_rows)
        if value is not None and value > 0
    ]
    summary: dict[str, Any] = {
        "room_type": str(first.get("room_type") or "").strip(),
        "room_type_category": first.get("room_type_category"),
        "max_persons": attrs.capacity,
        "room_facts": _room_facts(attrs),
    }
    if room_facilities:
        summary["room_facilities"] = room_facilities
    if rooms_left:
        summary["rooms_left"] = max(rooms_left)
    summary["plans"] = {
        "price_min_eur": min(prices) if prices else None,
        "price_max_eur": max(prices) if prices else None,
        "discounted_price_min_eur": min(discounted) if discounted else None,
        "cancellation_types": cancellation_types,
        "has_genius_discount": any(bool(row.get("has_genius_discount")) for row in room_rows),
        "meals_options": meals_options,
        "package_count": len(room_rows),
    }
    return summary


def _select_candidates(
    summaries: dict[tuple[str, str], dict[str, Any]], reference_categories: list[Any]
) -> list[tuple[str, str]]:
    """Order EVERY room for the model, deterministically, up to the ceiling.

    Rank: rooms in the comparable pool of ANY owned room type first, then
    the cheapest main price (unknown prices last), then property id and
    room name as stable tie-breakers. No per-property cap — every room is
    scored by the model; only the ``_MAX_CANDIDATES`` safety ceiling, if
    ever hit, drops the least relevant tail of the rank.
    """
    baselines = [category for category in reference_categories if str(category or "").strip()]

    def rank(room_key: tuple[str, str]) -> tuple[int, bool, float, str, str]:
        summary = summaries[room_key]
        category = summary.get("room_type_category")
        same = any(category_match_for(category, baseline) == "same" for baseline in baselines)
        price = summary["plans"]["price_min_eur"]
        return (
            0 if same else 1,
            price is None,
            price if price is not None else 0.0,
            room_key[0],
            room_key[1],
        )

    return sorted(summaries, key=rank)[:_MAX_CANDIDATES]


def _validated_matches(
    proposal: RoomMatchProposal,
    identities: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> dict[Any, list[dict[str, Any]]]:
    """Deterministic post-validation of one chunk's answer, grouped by room type.

    Each candidate names its room by the chunk index ``c`` and each verdict
    its owned room type by ``r``; an index outside its range is dropped
    with a warning (the model may not invent inventory — and a negative
    index must not wrap around). A repeated ``c``, or a repeated ``r``
    within one candidate, keeps the FIRST occurrence (the unique constraint
    would reject the insert otherwise). A pair the model left out keeps
    the statistical score. Scores clamp to [0, 100]; the reasoning is
    trimmed to the promised 120 characters; ``category_match`` is derived
    from the stored categories (the read side never trusted the model's);
    the ``comparable`` verdict is persisted as given (it alone decides each
    room type's comparison set).
    """
    validated: dict[Any, list[dict[str, Any]]] = {}
    seen: set[int] = set()
    for candidate in proposal.matches:
        index = candidate.c
        if not 0 <= index < len(identities):
            logger.warning(
                "Room-matching agent returned an unknown candidate index; dropped: %r "
                "(chunk size %d)",
                index,
                len(identities),
            )
            continue
        if index in seen:
            logger.warning(
                "Room-matching agent repeated a candidate index; keeping the first: %r",
                index,
            )
            continue
        seen.add(index)
        target = identities[index]
        reasoning = str(candidate.reasoning or "").strip()[:_MAX_REASONING_CHARS] or None
        seen_references: set[int] = set()
        for verdict in candidate.scores:
            reference_index = verdict.r
            if not 0 <= reference_index < len(references) or reference_index in seen_references:
                logger.warning(
                    "Room-matching agent returned an unknown or repeated room-type index; "
                    "dropped: %r (%d room types)",
                    reference_index,
                    len(references),
                )
                continue
            seen_references.add(reference_index)
            reference = references[reference_index]
            validated.setdefault(reference["id"], []).append(
                {
                    "property_id": target["property_id"],
                    "room_type": target["room_type"],
                    "score": max(0, min(100, int(verdict.score))),
                    "category_match": category_match_for(
                        target.get("room_type_category"), reference.get("room_type_category")
                    ),
                    "reasoning": reasoning,
                    "comparable": bool(verdict.comparable),
                }
            )
    return validated


def _partial_run_note(failed_chunks: list[tuple[int, Exception]], total: int) -> str | None:
    """The ``ok`` audit detail of a partial run: failed chunk numbers + first error."""
    if not failed_chunks:
        return None
    numbers = ", ".join(str(number) for number, _ in failed_chunks)
    first_number, first_error = failed_chunks[0]
    note = f"failed chunks: {numbers} of {total}; chunk {first_number}: {_short_error(first_error)}"
    return note[:500]


def _short_error(exc: Exception) -> str:
    """Compact audit-row error text: type plus the first 500 chars of detail."""
    detail = str(exc).strip()
    text = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return text[:500]
