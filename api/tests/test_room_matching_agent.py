"""Tests for the room-matching agent service (spec 2026-09-29 Μέρος Α).

The Anthropic client is always faked (mirroring the real
``messages.parse`` surface of the installed SDK 0.115.0); no test performs
network I/O. Every failure path must end in an ``error`` audit row and ZERO
match rows — never an exception to the caller (spec Α.5).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID

import anthropic
import pytest

from api.schemas.agents import RoomMatchCandidate, RoomMatchProposal
from api.services.room_matching_agent import (
    ROOM_MATCHING_SYSTEM_PROMPT,
    RoomMatchingAgentService,
    _build_agent_chunks,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
ROOM_TYPE_ID = UUID("00000000-0000-0000-0000-000000000abc")
PROPERTY_A = UUID("00000000-0000-0000-0000-0000000000f1")
PROPERTY_B = UUID("00000000-0000-0000-0000-0000000000f2")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RaisingFactory:
    """A client_factory that fails the test if it is ever called."""

    def __init__(self):
        self.called = False

    def __call__(self):
        self.called = True
        raise AssertionError("client_factory must not be called on this path")


class _FakeParseResult:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class _FakeMessages:
    """``messages.parse`` fake; ``respond(kwargs)`` answers per chunk if given.

    Chunks run on worker threads, so every call is recorded under a lock.
    """

    def __init__(self, result=None, error=None, respond=None):
        self._result = result
        self._error = error
        self._respond = respond
        self._lock = threading.Lock()
        self.calls = []
        self.parse_kwargs = None

    def parse(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            self.parse_kwargs = kwargs
        if self._respond is not None:
            return self._respond(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self, result=None, error=None, respond=None):
        self.messages = _FakeMessages(result=result, error=error, respond=respond)
        self.options = None

    def with_options(self, **options):
        # Mirrors the SDK's per-call copy (``Anthropic.with_options`` in
        # 0.115.0); records the options and returns itself so the
        # ``messages.parse`` kwargs stay inspectable.
        self.options = options
        return self


class FakeMatchRepository:
    def __init__(self, runs_today=0, existing=False, count_error=None, replace_error=None):
        self.runs_today = runs_today
        self.existing = existing
        self.count_error = count_error
        self.replace_error = replace_error
        self.replace_calls = []
        self.run_rows = []
        self.insert_run_error = None

    def count_runs_today(self, account_id):
        if self.count_error is not None:
            raise self.count_error
        return self.runs_today

    def has_matches(self, account_id, scrape_job_id, owned_room_type_id):
        return self.existing

    def replace_matches(self, **kwargs):
        if self.replace_error is not None:
            raise self.replace_error
        self.replace_calls.append(kwargs)
        return len(kwargs["matches"])

    def insert_run(self, **kwargs):
        if self.insert_run_error is not None:
            raise self.insert_run_error
        self.run_rows.append(kwargs)


class FakeRatesRepository:
    def __init__(self, rows):
        self.rows = rows
        self.last_filters = None

    def fetch_room_rates(self, filters):
        self.last_filters = filters
        return self.rows


class FakeOnboardingRepository:
    def __init__(self, selected_room_type=None):
        self.selected_room_type = selected_room_type
        self.calls = []

    def get_selected_room_type(self, account_id, owned_property_id):
        self.calls.append((account_id, owned_property_id))
        return self.selected_room_type


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _owned_room(**overrides) -> dict:
    room = {
        "id": ROOM_TYPE_ID,
        "owned_property_id": OWNED_PROPERTY_ID,
        "room_type": "Double Room with Sea View",
        "room_type_category": "double",
        "sample_price_per_night_eur": 95.0,
        "sample_meals": "Πρωινό",
        "sample_free_cancellation": "Ναι",
        "sample_facilities": "AC|WiFi|Balcony",
    }
    room.update(overrides)
    return room


def _job_rows() -> list[dict]:
    """Two competitor properties; property A sells the same room in 2 plans."""
    return [
        {
            "property_id": PROPERTY_A,
            "hotel_name": "Aegean View",
            "room_type": "Double Room Sea View",
            "room_type_category": "double",
            "room_attributes": {"capacity": 2, "view": "sea"},
            "price_per_night_eur": 90.0,
            "discounted_price_per_night_eur": 81.0,
            "cancellation_type": "free",
            "has_genius_discount": True,
            "meals": "Πρωινό",
        },
        {
            "property_id": PROPERTY_A,
            "hotel_name": "Aegean View",
            "room_type": "Double Room Sea View",
            "room_type_category": "double",
            "room_attributes": {"capacity": 2, "view": "sea"},
            "price_per_night_eur": 110.0,
            "discounted_price_per_night_eur": None,
            "cancellation_type": "non_refundable",
            "has_genius_discount": None,
            "meals": "Ημιδιατροφή",
        },
        {
            "property_id": PROPERTY_B,
            "hotel_name": "Blue Bay",
            "room_type": "Suite with Garden View",
            "room_type_category": "suite",
            "room_attributes": {"capacity": 3, "view": "garden"},
            "price_per_night_eur": 150.0,
            "discounted_price_per_night_eur": None,
            "cancellation_type": None,
            "has_genius_discount": None,
            "meals": "",
        },
    ]


def _candidate(**overrides) -> RoomMatchCandidate:
    # c=0 is Aegean's "Double Room Sea View" (same category ranks first);
    # c=1 is Blue Bay's "Suite with Garden View".
    values = {
        "c": 0,
        "score": 88,
        "category_match": "same",
        "reasoning": "Ίδια κατηγορία, ίδια θέα, ίδια χωρητικότητα.",
        "comparable": True,
    }
    values.update(overrides)
    return RoomMatchCandidate(**values)


def _service(
    *,
    rows=None,
    match_repository=None,
    onboarding_repository=None,
    api_key="sk-test",
    client=None,
    client_factory=None,
    max_daily_runs=30,
    timeout_seconds=30.0,
    **service_kwargs,
):
    rates = FakeRatesRepository(_job_rows() if rows is None else rows)
    repository = match_repository or FakeMatchRepository()
    factory = client_factory
    if factory is None and client is not None:
        factory = lambda: client  # noqa: E731
    service = RoomMatchingAgentService(
        match_repository=repository,
        onboarding_repository=onboarding_repository or FakeOnboardingRepository(),
        rates_repository_factory=lambda account_id: rates,
        api_key=api_key,
        model="claude-sonnet-5-5",
        timeout_seconds=timeout_seconds,
        max_daily_runs=max_daily_runs,
        client_factory=factory,
        **service_kwargs,
    )
    return service, repository, rates


def _run(service, **overrides):
    kwargs = {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "owned_room": _owned_room(),
    }
    kwargs.update(overrides)
    return service.run(**kwargs)


# ---------------------------------------------------------------------------
# Skip paths (no LLM call, no rows, one ``skipped`` audit row with the reason)
# ---------------------------------------------------------------------------


def _skipped_row(reason: str) -> dict:
    return {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "status": "skipped",
        "model": "claude-sonnet-5-5",
        "error_message": reason,
    }


def test_no_api_key_skips_without_building_a_client():
    factory = _RaisingFactory()
    service, repository, _ = _service(api_key="", client_factory=factory)

    result = _run(service)

    assert factory.called is False
    assert result.status == "skipped"
    assert result.skip_reason == "no_api_key"
    assert result.matches_written == 0
    assert repository.replace_calls == []
    assert repository.run_rows == [_skipped_row("no_api_key")]


def test_quota_reached_skips_with_quota_reason_and_a_skipped_audit_row():
    factory = _RaisingFactory()
    repository = FakeMatchRepository(runs_today=7)
    service, _, _ = _service(
        match_repository=repository, client_factory=factory, max_daily_runs=7
    )

    result = _run(service)

    assert factory.called is False
    assert result.status == "skipped"
    assert result.skip_reason == "quota_exceeded"
    assert repository.replace_calls == []
    # The audit row explains «γιατί δεν βγήκε AI εκτίμηση»; the repository's
    # count_runs_today excludes ``skipped`` rows, so it costs no quota.
    assert repository.run_rows == [_skipped_row("quota_exceeded")]


def test_existing_rows_skip_only_when_asked():
    repository = FakeMatchRepository(existing=True)
    proposal = RoomMatchProposal(matches=[_candidate()])
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, _, _ = _service(match_repository=repository, client=client)

    auto = _run(service, skip_if_existing=True)
    manual = _run(service, skip_if_existing=False)

    # The automatic post-scrape trigger is idempotent (spec Α.1)...
    assert auto.status == "skipped"
    assert auto.skip_reason == "already_matched"
    assert repository.run_rows[0] == _skipped_row("already_matched")
    # ...while the manual «Επανεκτίμηση» replaces the rows.
    assert manual.status == "completed"
    assert len(repository.replace_calls) == 1


def test_quota_check_failure_serves_unmetered():
    """Quota tracking being down must not block the run (pricing idiom)."""
    repository = FakeMatchRepository(count_error=RuntimeError("quota table down"))
    proposal = RoomMatchProposal(matches=[_candidate()])
    service, _, _ = _service(
        match_repository=repository, client=_FakeClient(result=_FakeParseResult(proposal))
    )

    result = _run(service)

    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_writes_matches_and_an_ok_audit_row():
    proposal = RoomMatchProposal(
        matches=[
            _candidate(),
            _candidate(
                c=1,
                score=35,
                category_match="similar",
                reasoning="Σουίτα με θέα κήπο, διαφορετική κατηγορία.",
            ),
        ]
    )
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, repository, _ = _service(client=client)

    result = _run(service)

    assert result.status == "completed"
    assert result.matches_written == 2
    replace = repository.replace_calls[0]
    assert replace["account_id"] == ACCOUNT_ID
    assert replace["scrape_job_id"] == JOB_ID
    assert replace["owned_room_type_id"] == ROOM_TYPE_ID
    assert replace["model_version"] == "claude-sonnet-5-5"
    stored = {(match["property_id"], match["room_type"]) for match in replace["matches"]}
    assert stored == {
        (PROPERTY_A, "Double Room Sea View"),
        (PROPERTY_B, "Suite with Garden View"),
    }
    assert repository.run_rows == [
        {
            "account_id": ACCOUNT_ID,
            "scrape_job_id": JOB_ID,
            "status": "ok",
            "model": "claude-sonnet-5-5",
            "error_message": None,
        }
    ]


def test_call_shape_is_minimal_and_the_system_prompt_is_greek():
    proposal = RoomMatchProposal(matches=[_candidate()])
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, _, _ = _service(client=client)

    _run(service)

    kwargs = client.messages.parse_kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["output_format"] is RoomMatchProposal
    assert set(kwargs) <= {"model", "max_tokens", "system", "messages", "output_format"}
    assert "temperature" not in kwargs
    assert "thinking" not in kwargs
    assert kwargs["system"] == ROOM_MATCHING_SYSTEM_PROMPT
    assert "ελληνικά" in kwargs["system"]
    assert "120" in kwargs["system"]


def test_each_call_uses_8k_output_the_matching_timeout_and_a_single_retry():
    """Sizing fix: one 8192-token call / 30 s / 2 retries failed a real job."""
    proposal = RoomMatchProposal(matches=[_candidate()])
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, _, _ = _service(client=client, timeout_seconds=77.5)

    _run(service)

    assert client.options == {"max_retries": 1, "timeout": 77.5}
    assert client.messages.parse_kwargs["max_tokens"] == 16_000


def test_matching_service_wiring_uses_the_matching_timeout(monkeypatch):
    from api import dependencies

    monkeypatch.setattr(dependencies.settings, "anthropic_matching_timeout_seconds", 95.0)
    monkeypatch.setattr(dependencies.settings, "anthropic_timeout_seconds", 12.0)

    # __wrapped__ bypasses the lru_cache so no other test sees this instance.
    service = dependencies.get_room_matching_service.__wrapped__()

    assert service.timeout_seconds == 95.0


# ---------------------------------------------------------------------------
# Candidate selection: EVERY room is offered (600-room safety ceiling only)
# ---------------------------------------------------------------------------


def _large_job_rows() -> list[dict]:
    """400 rooms: 50 properties x (2 same-category + 6 cheaper similar rooms).

    Similar prices are unique (50 + 6*i + j) so the rank is exact: every
    same-category room first (despite higher prices), then the similar
    rooms cheapest first. All 400 are offered — no per-property cap.
    """
    rows = []
    for i in range(50):
        property_id = UUID(int=10_000 + i)
        for k in range(2):
            rows.append(
                {
                    "property_id": property_id,
                    "hotel_name": f"Hotel {i}",
                    "room_type": f"Double Room {i}-{k}",
                    "room_type_category": "double",
                    "price_per_night_eur": 300.0 + i,
                }
            )
        for j in range(6):
            rows.append(
                {
                    "property_id": property_id,
                    "hotel_name": f"Hotel {i}",
                    "room_type": f"Suite {i}-{j}",
                    "room_type_category": "suite",
                    "price_per_night_eur": 50.0 + 6 * i + j,
                }
            )
    return rows


def _ceiling_job_rows() -> list[dict]:
    """700 rooms: 70 properties x (3 same-category + 6 priced + 1 unpriced similar).

    Same-category rooms are the DEAREST and similar prices are unique
    (50 + 6*i + j), so the 600 kept are exact: all 210 same-category rooms,
    then the 390 cheapest similar ones (properties 0-64); the 30 dearest
    priced and all 70 unpriced similar rooms fall past the ceiling.
    """
    rows = []
    for i in range(70):
        property_id = UUID(int=20_000 + i)
        base = {"property_id": property_id, "hotel_name": f"Hotel {i}"}
        for k in range(3):
            rows.append(
                {
                    **base,
                    "room_type": f"Double Room {i}-{k}",
                    "room_type_category": "double",
                    "price_per_night_eur": 900.0 + i,
                }
            )
        for j in range(6):
            rows.append(
                {
                    **base,
                    "room_type": f"Suite {i}-{j}",
                    "room_type_category": "suite",
                    "price_per_night_eur": 50.0 + 6 * i + j,
                }
            )
        rows.append(
            {
                **base,
                "room_type": f"Studio {i}",
                "room_type_category": "suite",
                "price_per_night_eur": None,
            }
        )
    return rows


def test_every_room_type_of_a_property_is_offered_without_a_per_property_cap():
    # 7 room types of ONE property: all 7 are candidates (the old cap was 4),
    # same category first despite the higher price, then cheapest similar.
    rows = [
        {
            "property_id": PROPERTY_A,
            "hotel_name": "Aegean View",
            "room_type": f"Room Type {n}",
            "room_type_category": "double" if n < 2 else "suite",
            "price_per_night_eur": 200.0 + n if n < 2 else 100.0 + n,
        }
        for n in range(7)
    ]

    chunks = _build_agent_chunks(_owned_room(), rows)

    assert len(chunks) == 1
    assert [identity["room_type"] for identity in chunks[0].identities] == [
        f"Room Type {n}" for n in range(7)
    ]
    assert [cand["c"] for cand in chunks[0].payload["candidates"]] == list(range(7))


def test_all_400_rooms_are_offered_same_category_first():
    chunks = _build_agent_chunks(_owned_room(), _large_job_rows())

    offered = [identity["room_type"] for chunk in chunks for identity in chunk.identities]
    assert len(chunks) == 10
    assert len(offered) == len(set(offered)) == 400
    # Every same-category room first, despite higher prices; then the rest.
    assert set(offered[:100]) == {f"Double Room {i}-{k}" for i in range(50) for k in range(2)}
    assert set(offered[100:]) == {f"Suite {i}-{j}" for i in range(50) for j in range(6)}


def test_safety_ceiling_keeps_the_600_most_relevant_of_700_rooms(caplog):
    with caplog.at_level("WARNING"):
        chunks = _build_agent_chunks(_owned_room(), _ceiling_job_rows())

    offered = [identity["room_type"] for chunk in chunks for identity in chunk.identities]
    assert len(chunks) == 15
    assert len(offered) == len(set(offered)) == 600
    # Same category first despite the highest prices, then the cheapest
    # similar rooms; unknown prices rank last, so they are what falls off.
    assert set(offered[:210]) == {f"Double Room {i}-{k}" for i in range(70) for k in range(3)}
    assert set(offered[210:]) == {f"Suite {i}-{j}" for i in range(65) for j in range(6)}
    assert not any(name.startswith("Studio") for name in offered)
    assert any("safety ceiling reached" in record.message for record in caplog.records)


def test_candidate_selection_is_independent_of_row_order():
    rows = _large_job_rows()

    forward = _build_agent_chunks(_owned_room(), rows)
    backward = _build_agent_chunks(_owned_room(), list(reversed(rows)))

    assert [(c.payload, c.identities) for c in forward] == [
        (c.payload, c.identities) for c in backward
    ]


# ---------------------------------------------------------------------------
# Chunked parallel calls (at most 40 candidates per call, up to 8 workers)
# ---------------------------------------------------------------------------


def _record_pool_sizes(monkeypatch) -> list:
    """Record the ``max_workers`` of every executor the service opens."""
    sizes: list = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, max_workers=None, *args, **kwargs):
            sizes.append(max_workers)
            super().__init__(max_workers, *args, **kwargs)

    monkeypatch.setattr("api.services.room_matching_agent.ThreadPoolExecutor", RecordingExecutor)
    return sizes


def _echo_responder(fail_when_first_room=None):
    """Score every candidate of a chunk, echoing ``hotel|room`` as reasoning.

    The echo lets a test prove the index round-trip: each stored row's
    reasoning must name the very room it was stored under. A chunk whose
    first candidate is ``fail_when_first_room`` times out instead.
    """

    def respond(kwargs):
        candidates = json.loads(kwargs["messages"][0]["content"])["candidates"]
        if candidates[0]["room_type"] == fail_when_first_room:
            raise anthropic.APITimeoutError(request=None)
        return _FakeParseResult(
            RoomMatchProposal(
                matches=[
                    RoomMatchCandidate(
                        c=candidate["c"],
                        score=60,
                        category_match="similar",
                        reasoning=f"{candidate['hotel_name']}|{candidate['room_type']}",
                        comparable=True,
                    )
                    for candidate in candidates
                ]
            )
        )

    return respond


def _hotel_of(property_id) -> str:
    return f"Hotel {property_id.int - 10_000}"


def test_400_candidates_run_as_10_chunks_on_8_workers_with_per_chunk_indexing(monkeypatch):
    pool_sizes = _record_pool_sizes(monkeypatch)
    client = _FakeClient(respond=_echo_responder())
    service, repository, _ = _service(rows=_large_job_rows(), client=client)

    result = _run(service)

    assert pool_sizes == [8]  # min(8, 10 chunks): one pool, 8 calls at once
    calls = client.messages.calls
    assert len(calls) == 10
    payloads = [json.loads(call["messages"][0]["content"]) for call in calls]
    assert [len(payload["candidates"]) for payload in payloads] == [40] * 10
    for call, payload in zip(calls, payloads):
        # c is the position inside the chunk's own list, restarting at 0.
        assert [cand["c"] for cand in payload["candidates"]] == list(
            range(len(payload["candidates"]))
        )
        assert payload["reference_room"]["room_type"] == "Double Room with Sea View"
        assert call["max_tokens"] == 16_000
        assert call["system"] == ROOM_MATCHING_SYSTEM_PROMPT
    offered = [(cand["hotel_name"], cand["room_type"]) for p in payloads for cand in p["candidates"]]
    assert len(set(offered)) == 400

    assert result.status == "completed"
    assert result.matches_written == 400
    for match in repository.replace_calls[0]["matches"]:
        assert match["reasoning"] == f"{_hotel_of(match['property_id'])}|{match['room_type']}"
    assert repository.run_rows[0]["status"] == "ok"
    assert repository.run_rows[0]["error_message"] is None


def test_a_failed_chunk_keeps_only_its_rooms_statistical_and_the_run_is_ok():
    # Deterministic rank: chunk 2 = the same-category rooms of properties
    # 20-39, so its first candidate is "Double Room 20-0".
    client = _FakeClient(respond=_echo_responder(fail_when_first_room="Double Room 20-0"))
    service, repository, _ = _service(rows=_large_job_rows(), client=client)

    result = _run(service)

    assert len(client.messages.calls) == 10
    assert result.status == "completed"
    assert result.matches_written == 360
    written = {match["room_type"] for match in repository.replace_calls[0]["matches"]}
    lost = {f"Double Room {i}-{k}" for i in range(20, 40) for k in range(2)}
    assert written.isdisjoint(lost)
    assert "Double Room 19-1" in written and "Double Room 40-0" in written
    # ONE audit row for the whole run: ok, naming the failed chunk.
    assert len(repository.run_rows) == 1
    run = repository.run_rows[0]
    assert run["status"] == "ok"
    assert run["error_message"].startswith("failed chunks: 2 of 10; chunk 2: APITimeoutError")


def test_every_chunk_failing_is_one_error_run_without_rows():
    client = _FakeClient(error=anthropic.APITimeoutError(request=None))
    service, repository, _ = _service(rows=_large_job_rows(), client=client)

    result = _run(service)

    assert len(client.messages.calls) == 10
    assert result.status == "error"
    assert repository.replace_calls == []
    assert len(repository.run_rows) == 1
    assert repository.run_rows[0]["status"] == "error"
    assert "all 10 chunk(s) failed" in repository.run_rows[0]["error_message"]


# ---------------------------------------------------------------------------
# Fractional scores round half-up instead of failing the run
# ---------------------------------------------------------------------------


def test_fractional_scores_round_half_up_and_clamp_instead_of_failing():
    # The SDK validates the answer with TypeAdapter.validate_json, so the
    # rounding must happen at schema-validation time.
    raw = json.dumps(
        {
            "matches": [
                {**_candidate().model_dump(), "score": 87.5},
                {**_candidate().model_dump(), "c": 1, "score": 100.5},
            ]
        }
    )
    proposal = RoomMatchProposal.model_validate_json(raw)
    assert [candidate.score for candidate in proposal.matches] == [88, 101]
    assert RoomMatchCandidate(**{**_candidate().model_dump(), "score": 88.5}).score == 89

    client = _FakeClient(result=_FakeParseResult(proposal))
    service, repository, _ = _service(client=client)

    result = _run(service)

    assert result.status == "completed"
    scores = [match["score"] for match in repository.replace_calls[0]["matches"]]
    assert scores == [88, 100]


def test_input_payload_carries_reference_room_and_plan_summaries():
    proposal = RoomMatchProposal(matches=[_candidate()])
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, _, rates = _service(client=client)

    _run(service)

    payload = json.loads(client.messages.parse_kwargs["messages"][0]["content"])
    reference = payload["reference_room"]
    assert reference["room_type"] == "Double Room with Sea View"
    assert reference["room_type_category"] == "double"
    assert reference["sample_price_per_night_eur"] == 95.0
    assert reference["facilities"] == "AC|WiFi|Balcony"

    # The two plans of the same room collapse into ONE candidate; the
    # same-category room ranks first, and each shows its index c.
    candidates = payload["candidates"]
    assert [(cand["c"], cand["hotel_name"], cand["room_type"]) for cand in candidates] == [
        (0, "Aegean View", "Double Room Sea View"),
        (1, "Blue Bay", "Suite with Garden View"),
    ]
    # Ids never reach the model: it answers by index only.
    assert all("property_id" not in cand for cand in candidates)
    room = candidates[0]
    assert room["max_persons"] == 2
    plans = room["plans"]
    assert plans["price_min_eur"] == 90.0
    assert plans["price_max_eur"] == 110.0
    assert plans["discounted_price_min_eur"] == 81.0
    assert plans["cancellation_types"] == ["free", "non_refundable"]
    assert plans["has_genius_discount"] is True
    assert plans["package_count"] == 2

    # The scrape job scopes the read; the own property is excluded.
    assert str(rates.last_filters.scrape_job_id) == str(JOB_ID)
    assert str(rates.last_filters.owned_property_id) == str(OWNED_PROPERTY_ID)
    assert rates.last_filters.include_similar is True
    assert rates.last_filters.room_type_category == "double"


# ---------------------------------------------------------------------------
# Validation of model output
# ---------------------------------------------------------------------------


def test_out_of_range_indices_are_dropped_with_a_warning_and_scores_clamped(caplog):
    proposal = RoomMatchProposal(
        matches=[
            _candidate(score=150),  # clamps to 100
            _candidate(c=2),  # past the 2-candidate chunk: dropped
            _candidate(c=-1),  # negative: dropped, never wraps to the last room
        ]
    )
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, repository, _ = _service(client=client)

    with caplog.at_level("WARNING"):
        result = _run(service)

    assert result.status == "completed"
    assert result.matches_written == 1
    matches = repository.replace_calls[0]["matches"]
    assert len(matches) == 1
    assert matches[0]["score"] == 100
    assert any("unknown candidate index" in record.message for record in caplog.records)


def test_index_mapping_round_trips_to_the_exact_stored_identity():
    rows = _job_rows()
    rows[0] = {**rows[0], "room_type": "  Double Room Sea View "}
    proposal = RoomMatchProposal(
        matches=[
            _candidate(c=1, score=35, category_match="similar"),
            _candidate(c=0, score=90),
            _candidate(c=40, score=99),  # out of range for this chunk: dropped
            _candidate(c=1, score=10),  # repeated index: first one wins
        ]
    )
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, repository, _ = _service(rows=rows, client=client)

    result = _run(service)

    assert result.matches_written == 2
    stored = [
        (match["property_id"], match["room_type"], match["score"])
        for match in repository.replace_calls[0]["matches"]
    ]
    # The index maps back to the input's exact identity (UUID object, the
    # stored spelling trimmed), so the read-side join always hits.
    assert stored == [
        (PROPERTY_B, "Suite with Garden View", 35),
        (PROPERTY_A, "Double Room Sea View", 90),
    ]


def test_duplicate_candidates_keep_the_first_row():
    proposal = RoomMatchProposal(matches=[_candidate(score=80), _candidate(score=60)])
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, repository, _ = _service(client=client)

    result = _run(service)

    assert result.matches_written == 1
    assert repository.replace_calls[0]["matches"][0]["score"] == 80


def test_negative_scores_clamp_to_zero_and_reasoning_is_trimmed_to_120():
    proposal = RoomMatchProposal(
        matches=[_candidate(score=-5, reasoning="α" * 300)]
    )
    client = _FakeClient(result=_FakeParseResult(proposal))
    service, repository, _ = _service(client=client)

    _run(service)

    match = repository.replace_calls[0]["matches"][0]
    assert match["score"] == 0
    assert len(match["reasoning"]) == 120


# ---------------------------------------------------------------------------
# Failure paths (spec Α.5): no rows, error audit, never an exception
# ---------------------------------------------------------------------------


def test_refusal_yields_an_error_audit_and_no_rows():
    client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    service, repository, _ = _service(client=client)

    result = _run(service)

    assert result.status == "error"
    assert result.matches_written == 0
    assert repository.replace_calls == []
    assert len(repository.run_rows) == 1
    run = repository.run_rows[0]
    assert run["status"] == "error"
    assert "refusal" in run["error_message"]


def test_api_timeout_yields_error_and_never_raises():
    client = _FakeClient(error=anthropic.APITimeoutError(request=None))
    service, repository, _ = _service(client=client)

    result = _run(service)

    assert result.status == "error"
    assert repository.replace_calls == []
    assert repository.run_rows[0]["status"] == "error"


def test_persistence_failure_yields_an_error_audit():
    repository = FakeMatchRepository(replace_error=RuntimeError("db down"))
    proposal = RoomMatchProposal(matches=[_candidate()])
    service, _, _ = _service(
        match_repository=repository, client=_FakeClient(result=_FakeParseResult(proposal))
    )

    result = _run(service)

    assert result.status == "error"
    assert repository.run_rows[0]["status"] == "error"
    assert "db down" in repository.run_rows[0]["error_message"]


def test_audit_write_failure_is_swallowed():
    repository = FakeMatchRepository()
    repository.insert_run_error = RuntimeError("audit table down")
    proposal = RoomMatchProposal(matches=[_candidate()])
    service, _, _ = _service(
        match_repository=repository, client=_FakeClient(result=_FakeParseResult(proposal))
    )

    result = _run(service)

    # The matches were written; only the audit row was lost (logged).
    assert result.status == "completed"
    assert result.matches_written == 1


def test_no_candidate_rows_completes_with_zero_without_an_llm_call():
    factory = _RaisingFactory()
    service, repository, _ = _service(rows=[], client_factory=factory)

    result = _run(service)

    assert factory.called is False
    assert result.status == "completed"
    assert result.matches_written == 0
    # The scope is still cleared (replace-all semantics) and the run audited.
    assert len(repository.replace_calls) == 1
    assert repository.replace_calls[0]["matches"] == []
    assert repository.run_rows[0]["status"] == "ok"


def test_rows_without_property_id_cannot_be_candidates():
    """Legacy-source rows carry no property_id, so nothing can be stored."""
    factory = _RaisingFactory()
    rows = [{**_job_rows()[0], "property_id": None}]
    service, repository, _ = _service(rows=rows, client_factory=factory)

    result = _run(service)

    assert factory.called is False
    assert result.status == "completed"
    assert result.matches_written == 0


# ---------------------------------------------------------------------------
# run_for_completed_job (the post-scrape auto trigger)
# ---------------------------------------------------------------------------


def _job(job_type="competitor_search", owned_property_id=OWNED_PROPERTY_ID):
    return SimpleNamespace(id=JOB_ID, job_type=job_type, owned_property_id=owned_property_id)


def test_run_for_completed_job_resolves_the_selected_room_and_skips_existing():
    onboarding = FakeOnboardingRepository(selected_room_type=_owned_room())
    proposal = RoomMatchProposal(matches=[_candidate()])
    service, repository, _ = _service(
        onboarding_repository=onboarding,
        client=_FakeClient(result=_FakeParseResult(proposal)),
    )

    result = service.run_for_completed_job(ACCOUNT_ID, _job())

    assert onboarding.calls == [(ACCOUNT_ID, OWNED_PROPERTY_ID)]
    assert result.status == "completed"
    assert repository.replace_calls[0]["owned_room_type_id"] == ROOM_TYPE_ID


def test_run_for_completed_job_is_idempotent_over_existing_rows():
    onboarding = FakeOnboardingRepository(selected_room_type=_owned_room())
    repository = FakeMatchRepository(existing=True)
    factory = _RaisingFactory()
    service, _, _ = _service(
        match_repository=repository,
        onboarding_repository=onboarding,
        client_factory=factory,
    )

    result = service.run_for_completed_job(ACCOUNT_ID, _job())

    assert factory.called is False
    assert result.status == "skipped"
    assert result.skip_reason == "already_matched"


def test_run_for_completed_job_without_key_skips_before_any_lookup():
    onboarding = FakeOnboardingRepository(selected_room_type=_owned_room())
    service, _, _ = _service(api_key="", onboarding_repository=onboarding)

    result = service.run_for_completed_job(ACCOUNT_ID, _job())

    assert result.status == "skipped"
    assert result.skip_reason == "no_api_key"
    assert onboarding.calls == []
    assert service.match_repository.run_rows == [_skipped_row("no_api_key")]


def test_run_for_completed_job_without_a_selected_room_returns_none():
    onboarding = FakeOnboardingRepository(selected_room_type=None)
    service, repository, _ = _service(onboarding_repository=onboarding)

    assert service.run_for_completed_job(ACCOUNT_ID, _job()) is None
    assert repository.run_rows == []


def test_run_for_completed_job_without_an_owned_property_returns_none():
    service, repository, _ = _service()

    assert service.run_for_completed_job(ACCOUNT_ID, _job(owned_property_id=None)) is None
    assert repository.run_rows == []


def test_run_for_completed_job_tolerates_a_selected_room_without_an_id():
    """A stale fake/legacy row without ``id`` cannot key match rows."""
    onboarding = FakeOnboardingRepository(selected_room_type={"room_type": "Double"})
    service, repository, _ = _service(onboarding_repository=onboarding)

    assert service.run_for_completed_job(ACCOUNT_ID, _job()) is None
    assert repository.run_rows == []


# ---------------------------------------------------------------------------
# The comparable verdict (owner decision 2026-09-30)
# ---------------------------------------------------------------------------


def test_the_prompt_defines_the_comparable_verdict():
    assert "comparable" in ROOM_MATCHING_SYSTEM_PROMPT
    assert "Σουίτα, διαμέρισμα, βίλα" in ROOM_MATCHING_SYSTEM_PROMPT


def test_the_comparable_field_is_required_in_the_structured_output():
    assert "comparable" in RoomMatchCandidate.model_json_schema()["required"]


def test_validated_matches_carry_the_verdict_to_persistence():
    from api.services.room_matching_agent import _validated_matches

    proposal = RoomMatchProposal(
        matches=[
            _candidate(c=0, score=85, comparable=False),
            _candidate(c=1, score=30, comparable=True),
        ]
    )
    identities = [
        {"property_id": PROPERTY_A, "room_type": "Junior Suite"},
        {"property_id": PROPERTY_B, "room_type": "Double Room"},
    ]

    validated = _validated_matches(proposal, identities)

    assert [(row["room_type"], row["score"], row["comparable"]) for row in validated] == [
        ("Junior Suite", 85, False),
        ("Double Room", 30, True),
    ]


# ---------------------------------------------------------------------------
# One run per (job, owned room): the lease (review fix 2026-09-30). The map's
# automatic run must never pay for a duplicate of the post-scrape run that is
# still waiting on the model for the same scope.
# ---------------------------------------------------------------------------

LEASE_HOLDER = UUID("00000000-0000-0000-0000-00000000c0de")


class LeasingMatchRepository(FakeMatchRepository):
    """Adds the lease store. ``held_polls``: lease checks that still see the other run."""

    def __init__(
        self, *, busy=False, held_polls=0, rows_after_wait=0, existing_after_claim=False, **kwargs
    ):
        super().__init__(**kwargs)
        self.busy = busy
        self.held_polls = held_polls
        self.rows_after_wait = rows_after_wait
        self.existing_after_claim = existing_after_claim
        self.acquire_calls = []
        self.release_calls = []
        self.claimed = False

    def acquire_lease(self, **kwargs):
        self.acquire_calls.append(kwargs)
        if self.busy:
            return None
        self.claimed = True
        return LEASE_HOLDER

    def release_lease(self, **kwargs):
        self.release_calls.append(kwargs)

    def lease_held(self, **kwargs):
        if self.held_polls > 0:
            self.held_polls -= 1
            return True
        return False

    def count_matches(self, account_id, scrape_job_id, owned_room_type_id):
        return self.rows_after_wait

    def has_matches(self, account_id, scrape_job_id, owned_room_type_id):
        return self.existing or (self.claimed and self.existing_after_claim)


class FakeClock:
    """monotonic() + sleep() that only move when the service sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _leased_service(repository, *, client=None, client_factory=None, clock=None, **kwargs):
    clock = clock or FakeClock()
    service, _, _ = _service(
        match_repository=repository,
        client=client,
        client_factory=client_factory,
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )
    return service, clock


def test_a_run_holds_the_scope_lease_and_releases_it():
    repository = LeasingMatchRepository()
    proposal = RoomMatchProposal(matches=[_candidate()])
    service, _ = _leased_service(repository, client=_FakeClient(result=_FakeParseResult(proposal)))

    result = _run(service)

    assert result.status == "completed"
    assert repository.acquire_calls == [
        {
            "account_id": ACCOUNT_ID,
            "scrape_job_id": JOB_ID,
            "owned_room_type_id": ROOM_TYPE_ID,
            "stale_after_seconds": service.lease_seconds,
        }
    ]
    assert repository.release_calls == [
        {
            "account_id": ACCOUNT_ID,
            "scrape_job_id": JOB_ID,
            "owned_room_type_id": ROOM_TYPE_ID,
            "holder": LEASE_HOLDER,
        }
    ]


def test_the_lease_is_released_when_the_run_fails():
    repository = LeasingMatchRepository()
    client = _FakeClient(error=anthropic.APITimeoutError(request=None))
    service, _ = _leased_service(repository, client=client)

    result = _run(service)

    assert result.status == "error"
    assert [call["holder"] for call in repository.release_calls] == [LEASE_HOLDER]


def test_the_post_scrape_run_skips_while_another_run_holds_the_scope():
    repository = LeasingMatchRepository(busy=True)
    factory = _RaisingFactory()
    service, clock = _leased_service(repository, client_factory=factory)

    result = _run(service, skip_if_existing=True)

    assert factory.called is False
    assert result.status == "skipped"
    assert result.skip_reason == "in_progress"
    assert repository.run_rows == [_skipped_row("in_progress")]
    assert repository.release_calls == []
    assert clock.sleeps == []  # the background hook never waits


def test_the_endpoint_waits_for_the_run_in_flight_instead_of_paying_twice():
    repository = LeasingMatchRepository(busy=True, held_polls=2, rows_after_wait=5)
    factory = _RaisingFactory()
    service, clock = _leased_service(repository, client_factory=factory)

    result = _run(service, skip_if_existing=False)

    # No model call, no rewrite: the other run's rows are the answer.
    assert factory.called is False
    assert repository.replace_calls == []
    assert result.status == "completed"
    assert result.matches_written == 5
    assert clock.sleeps == [3.0, 3.0]
    # Nothing ran here: no audit row, no quota unit, no lease to release.
    assert repository.run_rows == []
    assert repository.release_calls == []


def test_the_wait_is_bounded_and_reports_in_progress():
    repository = LeasingMatchRepository(busy=True, held_polls=10**6)
    service, clock = _leased_service(repository, client_factory=_RaisingFactory(), max_wait_seconds=10.0)

    result = _run(service)

    assert result.status == "skipped"
    assert result.skip_reason == "in_progress"
    assert 10.0 <= clock.now <= 13.0
    assert repository.run_rows == [_skipped_row("in_progress")]


def test_a_run_in_flight_that_left_no_rows_reads_as_an_error():
    repository = LeasingMatchRepository(busy=True, held_polls=1, rows_after_wait=0)
    service, _ = _leased_service(repository, client_factory=_RaisingFactory())

    result = _run(service)

    assert result.status == "error"
    assert result.matches_written == 0


def test_the_post_scrape_run_rechecks_the_rows_once_it_holds_the_lease():
    """The previous holder finished between the first check and the claim."""
    repository = LeasingMatchRepository(existing_after_claim=True)
    factory = _RaisingFactory()
    service, _ = _leased_service(repository, client_factory=factory)

    result = _run(service, skip_if_existing=True)

    assert factory.called is False
    assert result.skip_reason == "already_matched"
    assert [call["holder"] for call in repository.release_calls] == [LEASE_HOLDER]


def test_an_unavailable_lease_store_runs_unleased():
    """A database without migration 20260930_0028 must not stop matching."""

    class NoLeaseTable(LeasingMatchRepository):
        def acquire_lease(self, **kwargs):
            raise RuntimeError('relation "roomrate_agent_leases" does not exist')

    repository = NoLeaseTable()
    proposal = RoomMatchProposal(matches=[_candidate()])
    service, _ = _leased_service(repository, client=_FakeClient(result=_FakeParseResult(proposal)))

    result = _run(service)

    assert result.status == "completed"
    assert repository.release_calls == []


def test_the_lease_outlives_the_longest_possible_run():
    service, _, _ = _service(timeout_seconds=120.0)

    # 600 candidates = 15 chunks = 2 waves of 8 parallel calls, each up to
    # 120 s with its one retry, plus 120 s of database time.
    assert service.lease_seconds == 2 * 2 * 120.0 + 120.0
