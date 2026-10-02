"""LLM room-matching agent: chunked parallel calls per job, replace-all persistence.

Spec 2026-09-29 Μέρος Α. Per (job, owned room) the service offers EVERY
competitor room of the job to the model (only a 600-room safety ceiling),
splits them into chunks of at most 40 and scores the chunks in PARALLEL
structured-output calls (up to 8 at once); the statistical score is only the
fallback for a failed chunk or a room the model omits. Each call carries the
owner's reference room plus only its chunk:
every candidate summarized from the stored packages (hotel, name, category,
max_persons, room facts, rate-plan summary — price range, cancellation
classes, Genius) under a short integer index ``c``. The model answers by
index only (far fewer output tokens than echoing ids and names); the
service maps indices back (unknown/duplicate ones dropped), clamps scores to
0-100, writes the union of the successful chunks as a full replacement of
the (job, owned room) scope, and audits ONE ``roomrate_agent_runs`` row.

Failure policy (spec Α.5): a failed chunk leaves only its own rooms on the
statistical score — partial results are legitimate (the UI labels them
«Εκτίμηση AI (μερική)») and the run is audited ``ok`` with the failed chunk
numbers in ``error_message``. When EVERY chunk fails (or persistence fails)
the run writes no rows and is audited ``error``; skips (no key, quota,
already matched) write a ``skipped`` audit row with the reason. ``run``
never raises to its caller, so neither the scrape-job hook nor the endpoint
can be broken by this path.

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
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

from api.schemas.agents import RoomMatchProposal
from api.services.market_helpers import as_optional_float
from api.services.market_service import RoomRateFilters, RoomRatesRepositoryProtocol
from api.services.room_matching import attributes_from_dict, extract_room_attributes

logger = logging.getLogger(__name__)

# Output budget PER CHUNK: at most _CHUNK_SIZE index-keyed rows at ~60-150
# tokens each (Greek reasoning dominates). A truncated answer fails parsing
# and costs only that chunk (its rooms keep the statistical score).
_MAX_OUTPUT_TOKENS = 8000
# One call scoring 150 candidates needed ~20k output tokens — beyond the
# timeout at normal generation speed — so candidates go out in chunks that
# are scored in parallel. Up to 8 at once: a typical 4-6-chunk job runs in a
# single wave, i.e. about one chunk's latency.
_CHUNK_SIZE = 40
_MAX_PARALLEL_CALLS = 8
_MAX_REASONING_CHARS = 120
# Package-row budget read from one job (a job caps at ~320 deep-crawl items).
_MAX_JOB_ROWS = 2000
# Cost/latency guard; rooms beyond it stay statistical (an 89-hotel job has ~150-250 rooms).
_MAX_CANDIDATES = 600
# One SDK retry at most: the default 2 retries turned one timeout into a
# ~3x block of the post-scrape hook before the (silent) statistical fallback.
_MAX_RETRIES = 1

ROOM_MATCHING_SYSTEM_PROMPT = (
    "Είσαι αναλυτής φιλοξενίας που αντιστοιχίζει δωμάτια ανταγωνιστών με το "
    "δωμάτιο αναφοράς ενός καταλύματος. Στο μήνυμα χρήστη θα βρεις το δωμάτιο "
    "αναφοράς (reference_room) και μια λίστα υποψήφιων δωματίων (candidates), "
    "το καθένα με ακέραιο δείκτη c, το κατάλυμα (hotel_name) και σύνοψη "
    "πλάνων (εύρος τιμών, κλάσεις ακύρωσης, Genius). Για ΚΑΘΕ υποψήφιο "
    "επίστρεψε μία εγγραφή στο matches με: c ΑΚΡΙΒΩΣ όπως δίνεται στην "
    "είσοδο, score ακέραιο 0-100 για το πόσο συγκρίσιμο είναι με το δωμάτιο "
    "αναφοράς (κατηγορία, χωρητικότητα, θέα, παροχές, επίπεδο τιμής και "
    "πλάνα), category_match \"same\" όταν πρόκειται για την ίδια κατηγορία "
    "δωματίου αλλιώς \"similar\", reasoning έως 120 χαρακτήρες στα "
    "ελληνικά που εξηγεί το σκορ, και comparable true/false. Το comparable "
    "είναι η ετυμηγορία σου για το αν ένας επισκέπτης που ψάχνει το δωμάτιο "
    "αναφοράς θα θεωρούσε ΠΡΑΓΜΑΤΙΚΑ αυτό το δωμάτιο εναλλακτική επιλογή: "
    "συμβατή χωρητικότητα (ίδιος αριθμός επισκεπτών), παρόμοια κλάση, "
    "μέγεθος και διατροφή. Σουίτα, διαμέρισμα, βίλα ή μονάδα με περισσότερα "
    "υπνοδωμάτια ΔΕΝ είναι comparable με standard δίκλινο/twin, εκτός αν "
    "είναι πραγματικά ισοδύναμη· ούτε μονόκλινο με δίκλινο. Το comparable "
    "ορίζει ποια δωμάτια μπαίνουν στο σύνολο σύγκρισης του ιδιοκτήτη. "
    "Βασίσου αποκλειστικά στα δεδομένα της εισόδου: μην εφευρίσκεις δείκτες "
    "και μην επαναλαμβάνεις τον ίδιο δείκτη."
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
    identities: list[dict[str, Any]]  # identities[c] = {"property_id", "room_type"}


@dataclass(frozen=True)
class RoomMatchRunResult:
    """Outcome of one matching run, for both the endpoint and the auto hook."""

    status: Literal["completed", "error", "skipped"]
    matches_written: int = 0
    # "no_api_key" | "quota_exceeded" | "already_matched" (skips only).
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
        # Lazy, shared SDK client (same rationale as the pricing agent: a
        # fresh anthropic.Anthropic per run would pay pool + TLS every time).
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def run(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        owned_room: dict[str, Any],
        *,
        skip_if_existing: bool = False,
    ) -> RoomMatchRunResult:
        """Score one (job, owned room) scope; never raises.

        ``owned_room`` is a ``roomrate_owned_property_room_types`` row (with
        ``id`` and ``owned_property_id``). ``skip_if_existing`` is the
        automatic trigger's idempotency (spec Α.1); the manual endpoint
        passes False so «Επανεκτίμηση» replaces the rows.
        """
        if not self.api_key:
            return self._skip(account_id, scrape_job_id, "no_api_key")
        owned_room_type_id = owned_room["id"]
        try:
            if skip_if_existing and self.match_repository.has_matches(
                account_id, scrape_job_id, owned_room_type_id
            ):
                return self._skip(account_id, scrape_job_id, "already_matched")

            # One quota unit per RUN, however many chunks it fans out into.
            runs_today = self._count_runs_today(account_id)
            if runs_today is not None and runs_today >= self.max_daily_runs:
                return self._skip(account_id, scrape_job_id, "quota_exceeded")

            rows = self.rates_repository_factory(account_id).fetch_room_rates(
                self._job_filters(scrape_job_id, owned_room)
            )
            chunks = _build_agent_chunks(owned_room, rows)
            if not chunks:
                # Nothing to score (empty job or legacy rows without ids):
                # still honour replace-all so a stale set cannot linger.
                self.match_repository.replace_matches(
                    account_id=account_id,
                    scrape_job_id=scrape_job_id,
                    owned_room_type_id=owned_room_type_id,
                    model_version=self.model,
                    matches=[],
                )
                self._record_run(account_id, scrape_job_id, "ok", None)
                return RoomMatchRunResult(status="completed", matches_written=0)

            matches, failed_chunks = self._score_chunks(account_id, scrape_job_id, chunks)
            written = self.match_repository.replace_matches(
                account_id=account_id,
                scrape_job_id=scrape_job_id,
                owned_room_type_id=owned_room_type_id,
                model_version=self.model,
                matches=matches,
            )
            self._record_run(
                account_id, scrape_job_id, "ok", _partial_run_note(failed_chunks, len(chunks))
            )
            return RoomMatchRunResult(status="completed", matches_written=written)
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

    def run_for_completed_job(self, account_id: uuid.UUID, job: Any) -> RoomMatchRunResult | None:
        """Automatic post-scrape trigger for a completed competitor search.

        Resolves the property's SELECTED room as the reference; None when
        there is nothing to run against (no owned property on the job, or no
        selected/discovered room yet). Idempotent per (job, room): existing
        agent rows short-circuit before any billing.
        """
        if not self.api_key:
            # Cheapest possible exit: the common no-credentials deployment
            # must not pay two lookups per completed scrape — only the
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
        selected = self.onboarding_repository.get_selected_room_type(
            account_id, owned_property_id
        )
        if not selected or not selected.get("id"):
            logger.info(
                "Room matching skipped: no selected room type for the property: "
                "account_id=%s owned_property_id=%s",
                account_id,
                owned_property_id,
            )
            return None
        owned_room = dict(selected)
        owned_room.setdefault("owned_property_id", owned_property_id)
        return self.run(account_id, job.id, owned_room, skip_if_existing=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _job_filters(scrape_job_id: uuid.UUID, owned_room: dict[str, Any]) -> RoomRateFilters:
        """The job's package rows: same pool the competitors page reads.

        ``include_similar=True`` with the owned category keeps every
        non-single room plus the comparable pool (a single-room owner still
        gets singles); the own property is excluded like every competitor
        read.
        """
        category = str(owned_room.get("room_type_category") or "").strip() or None
        return RoomRateFilters(
            scrape_job_id=scrape_job_id,
            owned_property_id=owned_room.get("owned_property_id"),
            room_type_category=category,
            include_similar=True,
            limit=_MAX_JOB_ROWS,
        )

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

    def _score_chunks(
        self,
        account_id: uuid.UUID,
        scrape_job_id: uuid.UUID,
        chunks: list[_Chunk],
    ) -> tuple[list[dict[str, Any]], list[tuple[int, Exception]]]:
        """Score every chunk in parallel; a failed chunk only loses its own rooms.

        Returns the validated matches of the successful chunks plus the
        ``(chunk number, error)`` of each failed one. Raises
        RoomMatchingChunksFailedError when EVERY chunk failed, so a total
        loss takes the run's error path (no rows, ``error`` audit).
        """
        # Resolved once on the calling thread; the SDK client (httpx pool)
        # is safe to share across the worker threads.
        base_client = self._client_factory() if self._client_factory else self._get_client()

        def score(chunk: _Chunk) -> tuple[list[dict[str, Any]], Exception | None]:
            try:
                proposal = self._call_model(base_client, chunk.payload)
                return _validated_matches(proposal, chunk.identities), None
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
                return [], exc

        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_CALLS, len(chunks))) as pool:
            # map() keeps the chunk order, so the merged rows are deterministic.
            outcomes = list(pool.map(score, chunks))

        matches = [match for chunk_matches, _ in outcomes for match in chunk_matches]
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
        return matches, failed

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


def _room_attributes(row: dict[str, Any]) -> Any:
    """Stored write-time attributes win; sparse rows fall back to the label."""
    stored = row.get("room_attributes")
    if isinstance(stored, dict) and stored:
        return attributes_from_dict(stored)
    return extract_room_attributes(str(row.get("room_type") or ""))


def _build_agent_chunks(owned_room: dict[str, Any], rows: list[dict[str, Any]]) -> list[_Chunk]:
    """Build the per-call payloads (at most _CHUNK_SIZE candidates each).

    Rooms are keyed by ``(property_id, casefolded room name)`` so the plans
    of one room collapse into one candidate; each chunk's ``identities``
    keeps the exact stored spelling behind every index ``c`` (the read-side
    join relies on it). Ids never reach the model. Rows without a
    ``property_id`` (legacy source) cannot be persisted, so they are not
    offered as candidates at all.
    """
    reference_attrs = extract_room_attributes(str(owned_room.get("room_type") or ""))
    reference_room = {
        "room_type": owned_room.get("room_type"),
        "room_type_category": owned_room.get("room_type_category"),
        "sample_price_per_night_eur": as_optional_float(
            owned_room.get("sample_price_per_night_eur")
        ),
        "max_persons": reference_attrs.capacity,
        "facilities": owned_room.get("sample_facilities"),
        "meals": owned_room.get("sample_meals"),
        "free_cancellation": owned_room.get("sample_free_cancellation"),
    }

    hotel_names: dict[str, str] = {}
    grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        property_id = row.get("property_id")
        room_type = str(row.get("room_type") or "").strip()
        if not property_id or not room_type:
            continue
        property_key = str(property_id)
        hotel_names.setdefault(property_key, str(row.get("hotel_name") or ""))
        room_key = (property_key, room_type.casefold())
        grouped_rows.setdefault(room_key, []).append(row)
        identities.setdefault(room_key, {"property_id": property_id, "room_type": room_type})

    summaries = {room_key: _room_summary(room_rows) for room_key, room_rows in grouped_rows.items()}
    selected_keys = _select_candidates(summaries, owned_room.get("room_type_category"))
    if len(selected_keys) < len(summaries):
        # Practically never: the ceiling is several times a large real job.
        logger.warning(
            "Room-matching safety ceiling reached: offered=%d of %d rooms "
            "(the rest keep their statistical score)",
            len(selected_keys),
            len(summaries),
        )

    # Only offered rooms get an index, so a left-out room can never gain an
    # agent score; ``c`` is the position inside its own chunk's list.
    chunks: list[_Chunk] = []
    for start in range(0, len(selected_keys), _CHUNK_SIZE):
        chunk_keys = selected_keys[start : start + _CHUNK_SIZE]
        candidates = [
            {"c": index, "hotel_name": hotel_names[room_key[0]], **summaries[room_key]}
            for index, room_key in enumerate(chunk_keys)
        ]
        chunks.append(
            _Chunk(
                number=len(chunks) + 1,
                payload={"reference_room": reference_room, "candidates": candidates},
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
    room_facts = {
        "view": attrs.view,
        "has_balcony": attrs.has_balcony,
        "size_sqm": attrs.size_sqm,
        "bed_hint": attrs.bed_hint,
    }
    return {
        "room_type": str(first.get("room_type") or "").strip(),
        "room_type_category": first.get("room_type_category"),
        "max_persons": attrs.capacity,
        "room_facts": {key: value for key, value in room_facts.items() if value is not None},
        "plans": {
            "price_min_eur": min(prices) if prices else None,
            "price_max_eur": max(prices) if prices else None,
            "discounted_price_min_eur": min(discounted) if discounted else None,
            "cancellation_types": cancellation_types,
            "has_genius_discount": any(bool(row.get("has_genius_discount")) for row in room_rows),
            "meals_options": meals_options,
            "package_count": len(room_rows),
        },
    }


def _category_key(value: Any) -> str:
    """Normalized room category for the same/similar comparison ('' = unknown)."""
    return str(value or "").strip().casefold()


def _select_candidates(
    summaries: dict[tuple[str, str], dict[str, Any]], owned_category: Any
) -> list[tuple[str, str]]:
    """Order EVERY room for the model, deterministically, up to the ceiling.

    Rank: same category as the reference room first (the ``same`` of
    ``category_match``), then the cheapest main price (unknown prices last),
    then property id and room name as stable tie-breakers. No per-property
    cap — every room is scored by the model; only the ``_MAX_CANDIDATES``
    safety ceiling, if ever hit, drops the least relevant tail of the rank.
    """
    reference = _category_key(owned_category)

    def rank(room_key: tuple[str, str]) -> tuple[int, bool, float, str, str]:
        summary = summaries[room_key]
        same = bool(reference) and _category_key(summary.get("room_type_category")) == reference
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
) -> list[dict[str, Any]]:
    """Deterministic post-validation of one chunk's answer.

    Each row names its candidate by the chunk index ``c``; an index outside
    ``[0, len(identities))`` is dropped with a warning (the model may not
    invent inventory — and a negative index must not wrap around). Scores
    clamp to [0, 100]; a repeated index keeps the FIRST occurrence (the
    unique constraint would reject the insert otherwise). Reasoning is
    trimmed to the promised 120 characters; the ``comparable`` verdict is
    persisted as given (it alone decides the owner's comparison set).
    """
    validated: list[dict[str, Any]] = []
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
        validated.append(
            {
                "property_id": target["property_id"],
                "room_type": target["room_type"],
                "score": max(0, min(100, int(candidate.score))),
                "category_match": candidate.category_match,
                "reasoning": str(candidate.reasoning or "").strip()[:_MAX_REASONING_CHARS]
                or None,
                "comparable": bool(candidate.comparable),
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
