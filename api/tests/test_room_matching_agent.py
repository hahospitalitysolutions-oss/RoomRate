"""Tests for the room-matching agent service (spec 2026-09-29 Μέρος Α).

One pass per job scores every competitor room against ALL the owner's room
types (owner decision 2026-11-13). The Anthropic client is always faked
(mirroring the real ``messages.parse`` surface of the installed SDK
0.115.0); no test performs network I/O. Every failure path must end in an
``error`` audit row and ZERO match rows — never an exception to the caller
(spec Α.5).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID

import anthropic
import pytest

from api.schemas.agents import RoomMatchCandidate, RoomMatchProposal, RoomMatchScore
from api.services.room_matching_agent import (
    ROOM_MATCHING_SYSTEM_PROMPT,
    RoomMatchingAgentService,
    _build_agent_chunks,
    _chunk_size,
    _lease_anchor,
    _validated_matches,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
# The selected room type sorts AFTER the triple, so the lease anchor (the
# smallest id) is not simply "the first room".
DOUBLE_ID = UUID("00000000-0000-0000-0000-000000000abc")
TRIPLE_ID = UUID("00000000-0000-0000-0000-000000000aaa")
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
    """In-memory match store keyed per (job, owned room type)."""

    def __init__(self, runs_today=0, matched=(), count_error=None, replace_error=None):
        self.runs_today = runs_today
        self.matched = set(matched)
        self.count_error = count_error
        self.replace_error = replace_error
        self.replace_calls = []
        self.run_rows = []
        self.insert_run_error = None

    def count_runs_today(self, account_id):
        if self.count_error is not None:
            raise self.count_error
        return self.runs_today

    def matched_room_type_ids(self, account_id, scrape_job_id):
        return set(self.matched)

    def replace_job_matches(self, **kwargs):
        if self.replace_error is not None:
            raise self.replace_error
        self.replace_calls.append(kwargs)
        for room_id, rows in kwargs["matches_by_room_type"].items():
            if rows:
                self.matched.add(room_id)
            else:
                self.matched.discard(room_id)
        return sum(len(rows) for rows in kwargs["matches_by_room_type"].values())

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
    def __init__(self, rooms=None, selected=None):
        self.rooms = rooms if rooms is not None else []
        self.selected = selected
        self.calls = []

    def list_room_types(self, account_id, owned_property_id):
        self.calls.append(("list", account_id, owned_property_id))
        return [dict(room) for room in self.rooms]

    def get_selected_room_type(self, account_id, owned_property_id):
        self.calls.append(("selected", account_id, owned_property_id))
        return self.selected


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _double_room(**overrides) -> dict:
    room = {
        "id": DOUBLE_ID,
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


def _triple_room(**overrides) -> dict:
    room = {
        "id": TRIPLE_ID,
        "owned_property_id": OWNED_PROPERTY_ID,
        "room_type": "Triple Room",
        "room_type_category": "triple",
        "sample_price_per_night_eur": 120.0,
        "sample_meals": None,
        "sample_free_cancellation": None,
        "sample_facilities": None,
    }
    room.update(overrides)
    return room


def _owned_rooms() -> list[dict]:
    """The selected double first, then the triple (as ``owned_rooms`` orders them)."""
    return [_double_room(), _triple_room()]


def _job_rows() -> list[dict]:
    """Two competitor properties; property A sells the same room in 2 plans."""
    return [
        {
            "property_id": PROPERTY_A,
            "hotel_name": "Aegean View",
            "property_type": "Hotel",
            "stars": 4,
            "review_score": 8.7,
            "review_count": 412,
            "facilities": "Pool|Parking|WiFi",
            "room_type": "Double Room Sea View",
            "room_type_category": "double",
            "room_attributes": {"capacity": 2, "view": "sea"},
            "room_facilities": "Air conditioning|Balcony|Sea view|Air conditioning",
            "rooms_left": 3,
            "price_per_night_eur": 90.0,
            "discounted_price_per_night_eur": 81.0,
            "cancellation_type": "free",
            "has_genius_discount": True,
            "meals": "Πρωινό",
        },
        {
            "property_id": PROPERTY_A,
            "hotel_name": "Aegean View",
            "property_type": "Hotel",
            "stars": 4,
            "review_score": 8.7,
            "review_count": 412,
            "facilities": "Pool|Parking|WiFi",
            "room_type": "Double Room Sea View",
            "room_type_category": "double",
            "room_attributes": {"capacity": 2, "view": "sea"},
            "room_facilities": "Air conditioning|Balcony|Sea view",
            "rooms_left": 0,
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


def _verdicts(*pairs) -> list[RoomMatchScore]:
    """``(r, score, comparable)`` tuples -> the per-room-type verdicts."""
    return [RoomMatchScore(r=r, score=score, comparable=comparable) for r, score, comparable in pairs]


def _candidate(c=0, scores=None, reasoning="Αντιστοιχεί στο δίκλινο: ίδια θέα και χωρητικότητα."):
    # c=0 is Aegean's "Double Room Sea View" (in a reference pool: ranks
    # first); c=1 is Blue Bay's "Suite with Garden View".
    if scores is None:
        scores = _verdicts((0, 88, True), (1, 30, False))
    return RoomMatchCandidate(c=c, scores=scores, reasoning=reasoning)


def _proposal(*candidates) -> RoomMatchProposal:
    return RoomMatchProposal(matches=list(candidates) or [_candidate()])


def _client_for(proposal) -> _FakeClient:
    return _FakeClient(result=_FakeParseResult(proposal))


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
        onboarding_repository=onboarding_repository
        or FakeOnboardingRepository(rooms=[_triple_room(), _double_room()], selected=_double_room()),
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
        "owned_rooms": _owned_rooms(),
    }
    kwargs.update(overrides)
    return service.run(**kwargs)


def _stored(repository, room_id, call=0) -> list[dict]:
    return repository.replace_calls[call]["matches_by_room_type"][room_id]


def _skipped_row(reason: str) -> dict:
    return {
        "account_id": ACCOUNT_ID,
        "scrape_job_id": JOB_ID,
        "status": "skipped",
        "model": "claude-sonnet-5-5",
        "error_message": reason,
    }


# ---------------------------------------------------------------------------
# Skip paths (no LLM call, no rows, one ``skipped`` audit row with the reason)
# ---------------------------------------------------------------------------


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


def test_only_missing_skips_when_every_room_type_is_matched_and_a_full_run_replaces():
    repository = FakeMatchRepository(matched={DOUBLE_ID, TRIPLE_ID})
    client = _client_for(_proposal())
    service, _, _ = _service(match_repository=repository, client=client)

    auto = _run(service, only_missing=True)
    manual = _run(service, only_missing=False)

    # The automatic runs are idempotent (spec Α.1) — and a no-op leaves no
    # audit row (the map asks once per visit)...
    assert auto.status == "skipped"
    assert auto.skip_reason == "already_matched"
    assert auto.room_types_matched == 2
    assert repository.run_rows[0]["status"] == "ok"
    # ...while «Νέο ταίριασμα» replaces every room type's rows.
    assert manual.status == "completed"
    assert set(repository.replace_calls[0]["matches_by_room_type"]) == {DOUBLE_ID, TRIPLE_ID}


def test_only_missing_scores_just_the_room_types_without_rows():
    repository = FakeMatchRepository(matched={DOUBLE_ID})
    client = _client_for(_proposal(_candidate(scores=_verdicts((0, 70, True)))))
    service, _, _ = _service(match_repository=repository, client=client)

    result = _run(service, only_missing=True)

    payload = json.loads(client.messages.parse_kwargs["messages"][0]["content"])
    # Only the triple is offered — as r=0 of THIS call — and only its rows move.
    assert [room["room_type"] for room in payload["reference_rooms"]] == ["Triple Room"]
    assert set(repository.replace_calls[0]["matches_by_room_type"]) == {TRIPLE_ID}
    assert result.status == "completed"
    assert result.room_types_matched == 2


def test_quota_check_failure_serves_unmetered():
    """Quota tracking being down must not block the run (pricing idiom)."""
    repository = FakeMatchRepository(count_error=RuntimeError("quota table down"))
    service, _, _ = _service(match_repository=repository, client=_client_for(_proposal()))

    result = _run(service)

    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Happy path: one pass, rows for every room type, one audit row
# ---------------------------------------------------------------------------


def test_one_pass_writes_rows_for_every_room_type_and_one_ok_audit_row():
    proposal = _proposal(
        _candidate(c=0, scores=_verdicts((0, 88, True), (1, 25, False))),
        _candidate(
            c=1,
            scores=_verdicts((0, 35, False), (1, 81, True)),
            reasoning="Σουίτα 3 ατόμων: αντιστοιχεί στο τρίκλινο.",
        ),
    )
    client = _client_for(proposal)
    service, repository, _ = _service(client=client)

    result = _run(service)

    # ONE model call covers both room types.
    assert len(client.messages.calls) == 1
    assert result.status == "completed"
    assert result.matches_written == 4
    assert result.room_types_matched == 2
    assert len(repository.replace_calls) == 1
    replace = repository.replace_calls[0]
    assert replace["account_id"] == ACCOUNT_ID
    assert replace["scrape_job_id"] == JOB_ID
    assert replace["model_version"] == "claude-sonnet-5-5"
    double = {(m["room_type"], m["score"], m["comparable"]) for m in _stored(repository, DOUBLE_ID)}
    triple = {(m["room_type"], m["score"], m["comparable"]) for m in _stored(repository, TRIPLE_ID)}
    assert double == {("Double Room Sea View", 88, True), ("Suite with Garden View", 35, False)}
    assert triple == {("Double Room Sea View", 25, False), ("Suite with Garden View", 81, True)}
    assert repository.run_rows == [
        {
            "account_id": ACCOUNT_ID,
            "scrape_job_id": JOB_ID,
            "status": "ok",
            "model": "claude-sonnet-5-5",
            "error_message": None,
        }
    ]


def test_category_match_is_derived_per_room_type_not_asked_of_the_model():
    service, repository, _ = _service(client=_client_for(_proposal()))

    _run(service)

    double = {m["room_type"]: m["category_match"] for m in _stored(repository, DOUBLE_ID)}
    triple = {m["room_type"]: m["category_match"] for m in _stored(repository, TRIPLE_ID)}
    assert double == {"Double Room Sea View": "same"}
    assert triple == {"Double Room Sea View": "similar"}
    assert "category_match" not in RoomMatchCandidate.model_json_schema()["properties"]


def test_call_shape_is_minimal_and_the_system_prompt_is_greek():
    client = _client_for(_proposal())
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
    assert "ΟΛΟΥΣ τους τύπους δωματίων" in kwargs["system"]


def test_each_call_uses_16k_output_the_matching_timeout_and_a_single_retry():
    client = _client_for(_proposal())
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


def test_input_payload_carries_every_room_type_hotel_facts_and_room_facilities():
    client = _client_for(_proposal())
    service, _, rates = _service(client=client)

    _run(service)

    payload = json.loads(client.messages.parse_kwargs["messages"][0]["content"])
    references = payload["reference_rooms"]
    assert [(room["r"], room["room_type"]) for room in references] == [
        (0, "Double Room with Sea View"),
        (1, "Triple Room"),
    ]
    double = references[0]
    assert double["room_type_category"] == "double"
    assert double["sample_price_per_night_eur"] == 95.0
    assert double["facilities"] == ["AC", "WiFi", "Balcony"]
    assert double["meals"] == "Πρωινό"
    assert references[1]["max_persons"] == 3
    assert references[1]["facilities"] == []

    # Hotels once per chunk under h; ids never reach the model.
    assert payload["hotels"] == [
        {
            "h": 0,
            "name": "Aegean View",
            "property_type": "Hotel",
            "stars": 4.0,
            "review_score": 8.7,
            "review_count": 412,
            "facilities": ["Pool", "Parking", "WiFi"],
        },
        {"h": 1, "name": "Blue Bay"},
    ]
    candidates = payload["candidates"]
    # The two plans of the same room collapse into ONE candidate.
    assert [(cand["c"], cand["h"], cand["room_type"]) for cand in candidates] == [
        (0, 0, "Double Room Sea View"),
        (1, 1, "Suite with Garden View"),
    ]
    assert all("property_id" not in cand for cand in candidates)
    room = candidates[0]
    assert room["max_persons"] == 2
    assert room["room_facts"] == {"view": "sea"}
    # The room's own Booking facilities, de-duplicated.
    assert room["room_facilities"] == ["Air conditioning", "Balcony", "Sea view"]
    assert room["rooms_left"] == 3
    plans = room["plans"]
    assert plans["price_min_eur"] == 90.0
    assert plans["price_max_eur"] == 110.0
    assert plans["discounted_price_min_eur"] == 81.0
    assert plans["cancellation_types"] == ["free", "non_refundable"]
    assert plans["meals_options"] == ["Ημιδιατροφή", "Πρωινό"]
    assert plans["has_genius_discount"] is True
    assert plans["package_count"] == 2
    assert "room_facilities" not in candidates[1]

    # The scrape job scopes the read; the own property is excluded and
    # singles stay out (no owned single room).
    assert str(rates.last_filters.scrape_job_id) == str(JOB_ID)
    assert str(rates.last_filters.owned_property_id) == str(OWNED_PROPERTY_ID)
    assert rates.last_filters.include_similar is True
    assert rates.last_filters.room_type_category == "double"


def test_an_owned_single_room_brings_single_rooms_into_the_pool():
    single = _double_room(id=UUID(int=5), room_type="Single Room", room_type_category="single")
    service, _, rates = _service(client=_client_for(_proposal()))

    _run(service, owned_rooms=[_double_room(), single])

    assert rates.last_filters.room_type_category == "single"
    assert rates.last_filters.include_similar is True


# ---------------------------------------------------------------------------
# owned_rooms / run_for_job: every active room type, the selected one first
# ---------------------------------------------------------------------------


def _job(job_type="competitor_search", owned_property_id=OWNED_PROPERTY_ID):
    return SimpleNamespace(id=JOB_ID, job_type=job_type, owned_property_id=owned_property_id)


def test_owned_rooms_lists_every_room_type_with_the_selected_one_first():
    onboarding = FakeOnboardingRepository(
        rooms=[_triple_room(), _double_room(), {"room_type": "No id"}, _double_room()],
        selected=_double_room(),
    )
    service, _, _ = _service(onboarding_repository=onboarding)

    rooms = service.owned_rooms(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert [room["id"] for room in rooms] == [DOUBLE_ID, TRIPLE_ID]


def test_run_for_completed_job_scores_every_room_type_in_one_pass():
    client = _client_for(_proposal())
    service, repository, _ = _service(client=client)

    result = service.run_for_completed_job(ACCOUNT_ID, _job())

    assert result.status == "completed"
    assert len(client.messages.calls) == 1
    payload = json.loads(client.messages.parse_kwargs["messages"][0]["content"])
    assert [room["room_type"] for room in payload["reference_rooms"]] == [
        "Double Room with Sea View",
        "Triple Room",
    ]
    assert set(repository.replace_calls[0]["matches_by_room_type"]) == {DOUBLE_ID, TRIPLE_ID}


def test_run_for_completed_job_is_idempotent_over_existing_rows():
    repository = FakeMatchRepository(matched={DOUBLE_ID, TRIPLE_ID})
    factory = _RaisingFactory()
    service, _, _ = _service(match_repository=repository, client_factory=factory)

    result = service.run_for_completed_job(ACCOUNT_ID, _job())

    assert factory.called is False
    assert result.status == "skipped"
    assert result.skip_reason == "already_matched"


def test_run_for_completed_job_without_key_skips_before_any_lookup():
    onboarding = FakeOnboardingRepository(rooms=_owned_rooms(), selected=_double_room())
    service, _, _ = _service(api_key="", onboarding_repository=onboarding)

    result = service.run_for_completed_job(ACCOUNT_ID, _job())

    assert result.status == "skipped"
    assert result.skip_reason == "no_api_key"
    assert onboarding.calls == []
    assert service.match_repository.run_rows == [_skipped_row("no_api_key")]


def test_run_for_completed_job_without_room_types_returns_none():
    onboarding = FakeOnboardingRepository(rooms=[{"room_type": "No id"}], selected=None)
    service, repository, _ = _service(onboarding_repository=onboarding)

    assert service.run_for_completed_job(ACCOUNT_ID, _job()) is None
    assert repository.run_rows == []


def test_run_for_completed_job_without_an_owned_property_returns_none():
    service, repository, _ = _service()

    assert service.run_for_completed_job(ACCOUNT_ID, _job(owned_property_id=None)) is None
    assert repository.run_rows == []


def test_a_job_without_a_selected_room_type_still_scores_every_room_type():
    onboarding = FakeOnboardingRepository(rooms=_owned_rooms(), selected=None)
    service, repository, _ = _service(
        onboarding_repository=onboarding, client=_client_for(_proposal())
    )

    result = service.run_for_job(ACCOUNT_ID, _job(), only_missing=False, wait_for_run_in_flight=True)

    assert result.status == "completed"
    assert set(repository.replace_calls[0]["matches_by_room_type"]) == {DOUBLE_ID, TRIPLE_ID}


def test_more_room_types_than_the_ceiling_score_the_first_ten(caplog):
    rooms = [_double_room()] + [
        _triple_room(id=UUID(int=100 + n), room_type=f"Room {n}") for n in range(11)
    ]
    client = _client_for(_proposal())
    service, repository, _ = _service(client=client)

    with caplog.at_level("WARNING"):
        _run(service, owned_rooms=rooms)

    payload = json.loads(client.messages.parse_kwargs["messages"][0]["content"])
    assert len(payload["reference_rooms"]) == 10
    assert payload["reference_rooms"][0]["room_type"] == "Double Room with Sea View"
    assert len(repository.replace_calls[0]["matches_by_room_type"]) == 10
    assert any("reference ceiling reached" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Candidate selection: EVERY room is offered (600-room safety ceiling only)
# ---------------------------------------------------------------------------


def _large_job_rows() -> list[dict]:
    """400 rooms: 50 properties x (2 double-pool + 6 cheaper suite rooms).

    Suite prices are unique (50 + 6*i + j) so the rank is exact: every
    room in a reference pool first (despite higher prices), then the rest
    cheapest first. All 400 are offered — no per-property cap.
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
    """700 rooms: 70 properties x (3 double + 6 priced + 1 unpriced suite).

    Double rooms are the DEAREST and suite prices are unique, so the 600
    kept are exact: all 210 doubles, then the 390 cheapest suites
    (properties 0-64); the 30 dearest priced and all 70 unpriced suites fall
    past the ceiling.
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

    chunks = _build_agent_chunks([_double_room()], rows)

    assert len(chunks) == 1
    assert [identity["room_type"] for identity in chunks[0].identities] == [
        f"Room Type {n}" for n in range(7)
    ]
    assert [cand["c"] for cand in chunks[0].payload["candidates"]] == list(range(7))


def test_rooms_in_the_pool_of_any_room_type_rank_first():
    rows = [
        {"property_id": PROPERTY_A, "hotel_name": "A", "room_type": "Suite", "room_type_category": "suite", "price_per_night_eur": 50.0},
        {"property_id": PROPERTY_A, "hotel_name": "A", "room_type": "Triple", "room_type_category": "triple", "price_per_night_eur": 300.0},
        {"property_id": PROPERTY_B, "hotel_name": "B", "room_type": "Twin", "room_type_category": "twin", "price_per_night_eur": 200.0},
    ]

    chunks = _build_agent_chunks(_owned_rooms(), rows)

    # Twin is in the double's pool, Triple is the triple's own category.
    assert [identity["room_type"] for identity in chunks[0].identities] == ["Twin", "Triple", "Suite"]


def test_all_400_rooms_are_offered_pool_rooms_first():
    chunks = _build_agent_chunks([_double_room()], _large_job_rows())

    offered = [identity["room_type"] for chunk in chunks for identity in chunk.identities]
    assert len(chunks) == 10
    assert len(offered) == len(set(offered)) == 400
    assert set(offered[:100]) == {f"Double Room {i}-{k}" for i in range(50) for k in range(2)}
    assert set(offered[100:]) == {f"Suite {i}-{j}" for i in range(50) for j in range(6)}


def test_safety_ceiling_keeps_the_600_most_relevant_of_700_rooms(caplog):
    with caplog.at_level("WARNING"):
        chunks = _build_agent_chunks([_double_room()], _ceiling_job_rows())

    offered = [identity["room_type"] for chunk in chunks for identity in chunk.identities]
    assert len(chunks) == 15
    assert len(offered) == len(set(offered)) == 600
    assert set(offered[:210]) == {f"Double Room {i}-{k}" for i in range(70) for k in range(3)}
    assert set(offered[210:]) == {f"Suite {i}-{j}" for i in range(65) for j in range(6)}
    assert not any(name.startswith("Studio") for name in offered)
    assert any("safety ceiling reached" in record.message for record in caplog.records)


def test_candidate_selection_is_independent_of_row_order():
    rows = _large_job_rows()

    forward = _build_agent_chunks(_owned_rooms(), rows)
    backward = _build_agent_chunks(_owned_rooms(), list(reversed(rows)))

    assert [(c.payload, c.identities) for c in forward] == [
        (c.payload, c.identities) for c in backward
    ]


def test_the_chunk_shrinks_with_the_number_of_room_types():
    # ~160 (candidate, room type) verdicts per call, 16 to 40 candidates.
    assert [_chunk_size(n) for n in (1, 2, 4, 5, 8, 10, 12)] == [40, 40, 40, 32, 20, 16, 16]
    rooms = [_triple_room(id=UUID(int=100 + n), room_type=f"Room {n}") for n in range(8)]

    chunks = _build_agent_chunks(rooms, _large_job_rows())

    assert [len(chunk.identities) for chunk in chunks] == [20] * 20


# ---------------------------------------------------------------------------
# Chunked parallel calls (up to 8 workers)
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
    """Score every candidate against every room type, echoing ``hotel|room``.

    The echo lets a test prove the index round-trip: each stored row's
    reasoning must name the very room it was stored under. A chunk whose
    first candidate is ``fail_when_first_room`` times out instead.
    """

    def respond(kwargs):
        payload = json.loads(kwargs["messages"][0]["content"])
        candidates = payload["candidates"]
        hotels = {hotel["h"]: hotel["name"] for hotel in payload["hotels"]}
        if candidates[0]["room_type"] == fail_when_first_room:
            raise anthropic.APITimeoutError(request=None)
        return _FakeParseResult(
            RoomMatchProposal(
                matches=[
                    RoomMatchCandidate(
                        c=candidate["c"],
                        scores=_verdicts(
                            *((room["r"], 60, True) for room in payload["reference_rooms"])
                        ),
                        reasoning=f"{hotels[candidate['h']]}|{candidate['room_type']}",
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
        # c and h are positions inside the chunk's own lists, restarting at 0.
        assert [cand["c"] for cand in payload["candidates"]] == list(
            range(len(payload["candidates"]))
        )
        assert [hotel["h"] for hotel in payload["hotels"]] == list(range(len(payload["hotels"])))
        assert len(payload["reference_rooms"]) == 2
        assert call["max_tokens"] == 16_000
        assert call["system"] == ROOM_MATCHING_SYSTEM_PROMPT

    assert result.status == "completed"
    # 400 rooms x 2 room types.
    assert result.matches_written == 800
    for room_id in (DOUBLE_ID, TRIPLE_ID):
        rows = _stored(repository, room_id)
        assert len(rows) == 400
        for match in rows:
            assert match["reasoning"] == f"{_hotel_of(match['property_id'])}|{match['room_type']}"
    assert repository.run_rows[0]["status"] == "ok"
    assert repository.run_rows[0]["error_message"] is None


def test_a_failed_chunk_keeps_only_its_rooms_statistical_and_the_run_is_ok():
    # Deterministic rank: chunk 2 = the double rooms of properties 20-39.
    client = _FakeClient(respond=_echo_responder(fail_when_first_room="Double Room 20-0"))
    service, repository, _ = _service(rows=_large_job_rows(), client=client)

    result = _run(service)

    assert len(client.messages.calls) == 10
    assert result.status == "completed"
    assert result.matches_written == 720
    lost = {f"Double Room {i}-{k}" for i in range(20, 40) for k in range(2)}
    for room_id in (DOUBLE_ID, TRIPLE_ID):
        written = {match["room_type"] for match in _stored(repository, room_id)}
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
# Validation of model output
# ---------------------------------------------------------------------------


def test_fractional_scores_round_half_up_and_clamp_instead_of_failing():
    # The SDK validates the answer with TypeAdapter.validate_json, so the
    # rounding must happen at schema-validation time.
    raw = json.dumps(
        {
            "matches": [
                {"c": 0, "reasoning": "x", "scores": [{"r": 0, "score": 87.5, "comparable": True}]},
                {"c": 1, "reasoning": "y", "scores": [{"r": 0, "score": 100.5, "comparable": True}]},
            ]
        }
    )
    proposal = RoomMatchProposal.model_validate_json(raw)
    assert [candidate.scores[0].score for candidate in proposal.matches] == [88, 101]
    assert RoomMatchScore(r=0, score=88.5, comparable=True).score == 89

    service, repository, _ = _service(client=_client_for(proposal))

    result = _run(service)

    assert result.status == "completed"
    assert [match["score"] for match in _stored(repository, DOUBLE_ID)] == [88, 100]


def test_out_of_range_indices_are_dropped_with_a_warning_and_scores_clamped(caplog):
    proposal = _proposal(
        _candidate(scores=_verdicts((0, 150, True), (2, 90, True), (-1, 90, True))),
        _candidate(c=2),  # past the 2-candidate chunk: dropped
        _candidate(c=-1),  # negative: dropped, never wraps to the last room
    )
    service, repository, _ = _service(client=_client_for(proposal))

    with caplog.at_level("WARNING"):
        result = _run(service)

    assert result.status == "completed"
    assert result.matches_written == 1
    assert [match["score"] for match in _stored(repository, DOUBLE_ID)] == [100]
    # A room type the model left out keeps the statistical score.
    assert _stored(repository, TRIPLE_ID) == []
    assert any("unknown candidate index" in record.message for record in caplog.records)
    assert any("room-type index" in record.message for record in caplog.records)


def test_index_mapping_round_trips_to_the_exact_stored_identity():
    rows = _job_rows()
    rows[0] = {**rows[0], "room_type": "  Double Room Sea View "}
    proposal = _proposal(
        _candidate(c=1, scores=_verdicts((0, 35, False))),
        _candidate(c=0, scores=_verdicts((0, 90, True))),
        _candidate(c=40, scores=_verdicts((0, 99, True))),  # out of range: dropped
        _candidate(c=1, scores=_verdicts((0, 10, True))),  # repeated: first one wins
    )
    service, repository, _ = _service(rows=rows, client=_client_for(proposal))

    result = _run(service)

    assert result.matches_written == 2
    stored = [(m["property_id"], m["room_type"], m["score"]) for m in _stored(repository, DOUBLE_ID)]
    # The index maps back to the input's exact identity (UUID object, the
    # stored spelling trimmed), so the read-side join always hits.
    assert stored == [
        (PROPERTY_B, "Suite with Garden View", 35),
        (PROPERTY_A, "Double Room Sea View", 90),
    ]


def test_a_repeated_room_type_verdict_keeps_the_first():
    proposal = _proposal(_candidate(scores=_verdicts((0, 80, True), (0, 20, False))))
    service, repository, _ = _service(client=_client_for(proposal))

    _run(service)

    assert [(m["score"], m["comparable"]) for m in _stored(repository, DOUBLE_ID)] == [(80, True)]


def test_negative_scores_clamp_to_zero_and_reasoning_is_trimmed_to_120():
    proposal = _proposal(_candidate(scores=_verdicts((0, -5, False)), reasoning="α" * 300))
    service, repository, _ = _service(client=_client_for(proposal))

    _run(service)

    match = _stored(repository, DOUBLE_ID)[0]
    assert match["score"] == 0
    assert len(match["reasoning"]) == 120


def test_validated_matches_carry_the_verdict_per_room_type():
    proposal = _proposal(
        _candidate(c=0, scores=_verdicts((0, 85, False), (1, 77, True))),
        _candidate(c=1, scores=_verdicts((1, 30, False))),
    )
    identities = [
        {"property_id": PROPERTY_A, "room_type": "Junior Suite", "room_type_category": "suite"},
        {"property_id": PROPERTY_B, "room_type": "Double Room", "room_type_category": "double"},
    ]

    validated = _validated_matches(proposal, identities, _owned_rooms())

    assert [(m["room_type"], m["score"], m["comparable"]) for m in validated[DOUBLE_ID]] == [
        ("Junior Suite", 85, False),
    ]
    assert [(m["room_type"], m["score"], m["comparable"]) for m in validated[TRIPLE_ID]] == [
        ("Junior Suite", 77, True),
        ("Double Room", 30, False),
    ]


def test_the_prompt_defines_the_comparable_verdict():
    assert "comparable" in ROOM_MATCHING_SYSTEM_PROMPT
    assert "Σουίτα, διαμέρισμα, βίλα" in ROOM_MATCHING_SYSTEM_PROMPT


def test_the_comparable_field_is_required_in_the_structured_output():
    assert "comparable" in RoomMatchScore.model_json_schema()["required"]
    assert {"c", "scores", "reasoning"} <= set(RoomMatchCandidate.model_json_schema()["required"])


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
    service, _, _ = _service(match_repository=repository, client=_client_for(_proposal()))

    result = _run(service)

    assert result.status == "error"
    assert repository.run_rows[0]["status"] == "error"
    assert "db down" in repository.run_rows[0]["error_message"]


def test_audit_write_failure_is_swallowed():
    repository = FakeMatchRepository()
    repository.insert_run_error = RuntimeError("audit table down")
    service, _, _ = _service(match_repository=repository, client=_client_for(_proposal()))

    result = _run(service)

    # The matches were written; only the audit row was lost (logged).
    assert result.status == "completed"
    assert result.matches_written == 2


def test_no_candidate_rows_completes_with_zero_without_an_llm_call():
    factory = _RaisingFactory()
    service, repository, _ = _service(rows=[], client_factory=factory)

    result = _run(service)

    assert factory.called is False
    assert result.status == "completed"
    assert result.matches_written == 0
    # Every room type's scope is still cleared (replace-all) and the run audited.
    assert repository.replace_calls[0]["matches_by_room_type"] == {DOUBLE_ID: [], TRIPLE_ID: []}
    assert repository.run_rows[0]["status"] == "ok"


def test_rows_without_property_id_cannot_be_candidates():
    """Legacy-source rows carry no property_id, so nothing can be stored."""
    factory = _RaisingFactory()
    rows = [{**_job_rows()[0], "property_id": None}]
    service, _, _ = _service(rows=rows, client_factory=factory)

    result = _run(service)

    assert factory.called is False
    assert result.status == "completed"
    assert result.matches_written == 0


# ---------------------------------------------------------------------------
# One run per job: the lease (review fix 2026-09-30). The map's call right
# after a search must never pay for a duplicate of the post-scrape run that
# is still waiting on the model for the same job.
# ---------------------------------------------------------------------------

LEASE_HOLDER = UUID("00000000-0000-0000-0000-00000000c0de")


class LeasingMatchRepository(FakeMatchRepository):
    """Adds the lease store. ``held_polls``: lease checks that still see the other run."""

    def __init__(
        self, *, busy=False, held_polls=0, matched_after_wait=(), matched_after_claim=(), **kwargs
    ):
        super().__init__(**kwargs)
        self.busy = busy
        self.held_polls = held_polls
        self.matched_after_wait = set(matched_after_wait)
        self.matched_after_claim = set(matched_after_claim)
        self.acquire_calls = []
        self.release_calls = []
        self.claimed = False

    def acquire_lease(self, **kwargs):
        self.acquire_calls.append(kwargs)
        if self.busy:
            return None
        self.claimed = True
        self.matched |= self.matched_after_claim
        return LEASE_HOLDER

    def release_lease(self, **kwargs):
        self.release_calls.append(kwargs)

    def lease_held(self, **kwargs):
        if self.held_polls > 0:
            self.held_polls -= 1
            return True
        self.matched |= self.matched_after_wait
        return False


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


def test_the_lease_anchor_is_the_smallest_room_type_id_for_every_caller():
    assert _lease_anchor(_owned_rooms()) == TRIPLE_ID
    assert _lease_anchor(list(reversed(_owned_rooms()))) == TRIPLE_ID


def test_a_run_holds_the_job_lease_and_releases_it():
    repository = LeasingMatchRepository()
    service, _ = _leased_service(repository, client=_client_for(_proposal()))

    result = _run(service)

    assert result.status == "completed"
    assert repository.acquire_calls == [
        {
            "account_id": ACCOUNT_ID,
            "scrape_job_id": JOB_ID,
            "owned_room_type_id": TRIPLE_ID,
            "stale_after_seconds": service.lease_seconds,
        }
    ]
    assert repository.release_calls == [
        {
            "account_id": ACCOUNT_ID,
            "scrape_job_id": JOB_ID,
            "owned_room_type_id": TRIPLE_ID,
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


def test_the_post_scrape_run_skips_while_another_run_holds_the_job():
    repository = LeasingMatchRepository(busy=True)
    factory = _RaisingFactory()
    service, clock = _leased_service(repository, client_factory=factory)

    result = _run(service, only_missing=True, wait_for_run_in_flight=False)

    assert factory.called is False
    assert result.status == "skipped"
    assert result.skip_reason == "in_progress"
    assert repository.run_rows == [_skipped_row("in_progress")]
    assert repository.release_calls == []
    assert clock.sleeps == []  # the background hook never waits


def test_the_endpoint_waits_for_the_run_in_flight_instead_of_paying_twice():
    repository = LeasingMatchRepository(
        busy=True, held_polls=2, matched_after_wait={DOUBLE_ID, TRIPLE_ID}
    )
    factory = _RaisingFactory()
    service, clock = _leased_service(repository, client_factory=factory)

    result = _run(service, only_missing=True, wait_for_run_in_flight=True)

    # No model call, no rewrite: the other run's rows are the answer.
    assert factory.called is False
    assert repository.replace_calls == []
    assert result.status == "completed"
    assert result.room_types_matched == 2
    assert clock.sleeps == [3.0, 3.0]
    # Nothing ran here: no audit row, no quota unit, no lease to release.
    assert repository.run_rows == []
    assert repository.release_calls == []


def test_the_wait_is_bounded_and_reports_in_progress():
    repository = LeasingMatchRepository(busy=True, held_polls=10**6)
    service, clock = _leased_service(
        repository, client_factory=_RaisingFactory(), max_wait_seconds=10.0
    )

    result = _run(service)

    assert result.status == "skipped"
    assert result.skip_reason == "in_progress"
    assert 10.0 <= clock.now <= 13.0
    assert repository.run_rows == [_skipped_row("in_progress")]


def test_a_run_in_flight_that_left_no_rows_reads_as_an_error():
    repository = LeasingMatchRepository(busy=True, held_polls=1)
    service, _ = _leased_service(repository, client_factory=_RaisingFactory())

    result = _run(service)

    assert result.status == "error"
    assert result.matches_written == 0


def test_only_missing_rechecks_the_rows_once_it_holds_the_lease():
    """The previous holder finished between the first check and the claim."""
    repository = LeasingMatchRepository(matched_after_claim={DOUBLE_ID, TRIPLE_ID})
    factory = _RaisingFactory()
    service, _ = _leased_service(repository, client_factory=factory)

    result = _run(service, only_missing=True, wait_for_run_in_flight=False)

    assert factory.called is False
    assert result.skip_reason == "already_matched"
    assert [call["holder"] for call in repository.release_calls] == [LEASE_HOLDER]


def test_an_unavailable_lease_store_runs_unleased():
    """A database without migration 20260930_0028 must not stop matching."""

    class NoLeaseTable(LeasingMatchRepository):
        def acquire_lease(self, **kwargs):
            raise RuntimeError('relation "roomrate_agent_leases" does not exist')

    repository = NoLeaseTable()
    service, _ = _leased_service(repository, client=_client_for(_proposal()))

    result = _run(service)

    assert result.status == "completed"
    assert repository.release_calls == []


def test_the_lease_outlives_the_longest_possible_run():
    service, _, _ = _service(timeout_seconds=120.0)

    # 600 candidates at the smallest chunk (16, for 10 room types) = 38
    # chunks = 5 waves of 8 parallel calls, each up to 120 s with its one
    # retry, plus 120 s of database time.
    assert service.lease_seconds == 5 * 2 * 120.0 + 120.0
