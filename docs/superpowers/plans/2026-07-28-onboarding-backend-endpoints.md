# Onboarding Backend Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the owned property reversible (change it, delete it) and return Booking candidates best-match-first, so the `/setup` wizard can propose a property, let the user correct it, and never trap them with a wrong auto-match.

**Architecture:** Three additions to the existing onboarding stack, each following the established repository → service → router layering. `PUT /owned-property/{id}` swaps the property in one transaction (update fields, drop stale room types, conditionally drop tracked competitors, queue a fresh discovery job) and keeps the same row id so nothing referencing it breaks. `DELETE /owned-property/{id}` relies on the FK cascades already present in the schema. `search_property_candidates` gains the ordering that until now lived only inside `automatic_setup`.

**Tech Stack:** FastAPI, SQLAlchemy Core (raw `text()`), Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-07-27-onboarding-ui-design.md` §3.4, §3.5

**Task 1 review outcome (already applied):** the ordering guarantee moved into
the `search_property_candidates` docstring, a `PropertyCandidateProviderProtocol`
was added (the constructor annotated a concrete class while tests inject a fake),
and the candidate fake now mirrors the real signature and records its call. The
`review_score` tie-break — candidates without a review score sink to the bottom —
got its own test. Tasks 2-5 follow the same fake convention.

**Task 1 re-review outcome (already applied):** the guarantee also had to go in
the **route** docstring of `GET /property-candidates`. FastAPI publishes route
docstrings into the OpenAPI schema and service docstrings nowhere — and the
consumer this ordering exists for is a TypeScript client reading that schema, so
documenting it only in Python left it invisible to the one reader who needs it
(`api/routers/notifications.py` and `api/routers/market.py` already document
response ordering at the router for the same reason). **Any ordering or
sequencing contract this plan adds must be stated at the route, not only in the
service.** The tie-break test was also renamed: "unrated last" holds only inside
the token-overlap tier, since an exact name match scores 100.0 without ever
reading `review_score`.

**Verification baseline before starting:** `python -m pytest api/tests -q --basetemp="<scratchpad>/pytest-tmp"` → 548 passed.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `api/services/onboarding_service.py` | Candidate scoring/ordering, property lifecycle orchestration | Modify: sort candidates; add `replace_owned_property_and_discovery_job`, `delete_owned_property`; extend the repository Protocol |
| `api/repositories/onboarding_repository.py` | Raw SQL for owned properties | Add: `replace_owned_property`, `delete_owned_property` |
| `api/routers/onboarding.py` | HTTP surface | Add: `PUT`/`DELETE /owned-property/{owned_property_id}` |
| `api/tests/test_onboarding_service.py` | Service behaviour with fakes | Modify: extend `FakeOnboardingRepository`; add ordering + replace/delete tests |
| `api/tests/test_onboarding_repository.py` | SQL shape with a fake engine | Add: replace/delete SQL tests |
| `api/tests/test_onboarding_routes.py` | Route wiring with dependency overrides | Add: PUT/DELETE route tests |
| `docs/DOCUMENTATION.md` | API reference | Modify: document both endpoints |

---

### Task 1: Return Booking candidates best-match-first

The wizard preselects the first candidate. Today `search_property_candidates` returns provider order, and the match scoring lives only inside `automatic_setup` — so the wizard would either preselect an arbitrary hotel or duplicate the scoring in TypeScript.

**Files:**
- Modify: `api/services/onboarding_service.py` (`search_property_candidates`, `automatic_setup`)
- Test: `api/tests/test_onboarding_service.py`

- [ ] **Step 1: Write the failing test**

Append to `api/tests/test_onboarding_service.py`:

```python
def test_search_property_candidates_returns_best_match_first():
    """The wizard preselects candidates[0], so ordering is a contract.

    The provider returns whatever Booking's search ranked; only the exact/
    substring name match is meaningful for "which hotel are you".
    """
    provider = FakeCandidateProvider(
        [
            _candidate("Faliraki Beach Resort", review_score=9.4),
            _candidate("Rea Hotel"),
            _candidate("Rea Hotel Annex"),
        ]
    )
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
    )

    candidates = service.search_property_candidates(
        property_name="Rea Hotel",
        location="Faliraki",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=2,
        children=0,
        rooms=1,
        limit=8,
    )

    assert [candidate.display_name for candidate in candidates] == [
        "Rea Hotel",
        "Rea Hotel Annex",
        "Faliraki Beach Resort",
    ]


def test_automatic_setup_picks_the_best_match_not_the_provider_order():
    """automatic_setup must not diverge from what the wizard shows first."""
    provider = FakeCandidateProvider(
        [
            _candidate("Faliraki Beach Resort", review_score=9.4),
            _candidate("Rea Hotel"),
        ]
    )
    service = OnboardingService(
        repository=FakeOnboardingRepository(),
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=provider,
    )

    response = service.automatic_setup(
        ACCOUNT_ID,
        AutomaticSetupRequest(
            property_name="Rea Hotel",
            location="Faliraki",
            check_in=date(2030, 7, 1),
            check_out=date(2030, 7, 5),
        ),
    )

    assert response.selected_candidate.display_name == "Rea Hotel"
```

Add these helpers near the top of the file, after the existing fakes:

```python
def _candidate(display_name: str, review_score: float = 8.0) -> PropertyCandidate:
    return PropertyCandidate(
        candidate_key=display_name.lower().replace(" ", "-"),
        display_name=display_name,
        booking_url=f"https://www.booking.com/hotel/gr/{display_name.lower().replace(' ', '-')}.html",
        city="Faliraki",
        review_score=review_score,
    )


class FakeCandidateProvider:
    """Records its call, so tests can assert the service forwards arguments.

    The signature mirrors the real provider deliberately: a `**kwargs` stub
    would accept a renamed keyword happily while production raised TypeError.
    """

    def __init__(self, candidates: list[PropertyCandidate]):
        self.candidates = candidates
        self.calls: list[dict] = []

    def search(
        self,
        property_name: str,
        location: str,
        check_in,
        check_out,
        adults: int,
        children: int,
        rooms: int,
        limit: int,
    ) -> list[PropertyCandidate]:
        self.calls.append(
            {
                "property_name": property_name,
                "location": location,
                "check_in": check_in,
                "check_out": check_out,
                "adults": adults,
                "children": children,
                "rooms": rooms,
                "limit": limit,
            }
        )
        return list(self.candidates)
```

> **Convention for every fake in this plan:** mirror the real collaborator's
> signature and record the call. A fake that accepts `**kwargs` cannot catch a
> keyword rename, and one that records nothing cannot verify forwarding.

Ensure these imports exist at the top of the test file (add any that are missing):

```python
from datetime import date

from api.schemas.onboarding import AutomaticSetupRequest, PropertyCandidate
from api.services.onboarding_service import OnboardingService
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
python -m pytest api/tests/test_onboarding_service.py::test_search_property_candidates_returns_best_match_first -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: FAIL — the list comes back in provider order (`Faliraki Beach Resort` first).

- [ ] **Step 3: Sort in the service**

In `api/services/onboarding_service.py`, first put the ordering guarantee in the
docstring of `search_property_candidates` — it is a contract callers depend on
(`automatic_setup` indexes `[0]`), and this file documents non-obvious contracts
in docstrings rather than body comments:

```python
        """Return candidate Booking properties for the onboarding picker.

        Ordered best name match first; ``candidates[0]`` is the best match and
        ties keep the provider's own relevance order. ``automatic_setup`` relies
        on this, so callers do not reimplement the scoring.
        """
```

Then replace the `return` of the same method:

```python
        candidates = self.candidate_provider.search(
            property_name=property_name,
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            limit=limit,
        )
        # One scoring implementation, not one per consumer.
        return sorted(
            candidates,
            key=lambda candidate: _candidate_match_score(property_name, candidate),
            reverse=True,
        )
```

Then simplify `automatic_setup` — replace:

```python
        selected_candidate = max(
            candidates,
            key=lambda candidate: _candidate_match_score(request.property_name, candidate),
        )
```

with:

```python
        # search_property_candidates already ordered by match score.
        selected_candidate = candidates[0]
```

- [ ] **Step 4: Run both tests**

Run:
```bash
python -m pytest api/tests/test_onboarding_service.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: PASS, all tests in the file.

- [ ] **Step 5: Run the full suite**

Run:
```bash
python -m pytest api/tests -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: 550 passed (548 baseline + 2 new).

- [ ] **Step 6: Commit**

```bash
git add api/services/onboarding_service.py api/tests/test_onboarding_service.py
git commit -m "Return Booking candidates best-match-first

The onboarding wizard preselects candidates[0], so ordering is now part of
the service contract instead of logic buried in automatic_setup. Keeps one
scoring implementation rather than duplicating it in the frontend."
```

---

### Task 2: Repository — replace the owned property in one transaction

**Files:**
- Modify: `api/repositories/onboarding_repository.py`
- Test: `api/tests/test_onboarding_repository.py`

> **Task 2 review outcome (folded in below).** A mutation pass over the first
> draft of these tests showed the SQL-text assertions caught a lot but left the
> load-bearing claims open: swapping `begin()` for `connect()` — no transaction
> at all — passed everything, as did binding the wrong field into the `UPDATE`
> or dropping `booking_url` from the SET clause, because no test read the bind
> params. The harness and tests below already include the fixes: `FakeEngine`
> records how it was opened, the same-market test asserts the params, and the
> unused `FakeResult.all()` is gone. **Assert the parameters, not only the SQL
> string** — that applies to the remaining tasks too.

- [ ] **Step 1: Upgrade the test harness**

`api/tests/test_onboarding_repository.py` currently has a minimal harness: its
`FakeEngine` exposes only `connect()` (no `begin()`), `FakeConnection` keeps
just the **last** statement, and `FakeResult` has only `first()`. `replace_owned_property`
runs several statements inside `begin()`, so the harness must grow first.

Replace everything from `class FakeResult:` down to (and including)
`class FakeEngine:` — that is lines 9-48, ending just before the first
`def test_...` (line 51) — with:

```python
class FakeResult:
    """Scripted result: rows for mappings(), rowcount for DELETE/UPDATE."""

    def __init__(self, row=None, rows=None, rowcount: int = 0):
        # `row` keeps the original single-row constructor working.
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        return self._rows[0]


class FakeConnection:
    """Records every statement and returns the next scripted result."""

    def __init__(self, row=None, results=None):
        self.results = list(results) if results is not None else None
        self.row = row
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))
        if self.results is not None:
            return self.results.pop(0) if self.results else FakeResult()
        # Single-row mode: every statement sees the same row.
        return FakeResult(row=self.row)

    @property
    def executed_sql(self) -> str:
        """The most recent statement (kept for the original tests)."""
        return self.executed[-1][0] if self.executed else ""

    @property
    def executed_params(self) -> dict:
        return self.executed[-1][1] if self.executed else {}


class FakeConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeEngine:
    """Records whether the caller opened a transaction or a bare connection.

    Both return the same connection, so without ``opened`` a method that
    dropped ``begin()`` for ``connect()`` -- losing atomicity entirely -- would
    still pass every assertion.
    """

    def __init__(self, connection):
        self.connection = connection
        self.opened: list[str] = []

    def connect(self):
        self.opened.append("connect")
        return FakeConnectionContext(self.connection)

    def begin(self):
        self.opened.append("begin")
        return FakeConnectionContext(self.connection)


def _patch_engine(monkeypatch, connection) -> FakeEngine:
    """Returns the engine so tests can assert how it was opened."""
    engine = FakeEngine(connection)
    monkeypatch.setattr(
        "api.repositories.onboarding_repository.get_engine",
        lambda *args, **kwargs: engine,
    )
    return engine
```

The two existing tests keep passing unchanged: they build
`FakeConnection(row=...)` and read `connection.executed_sql`, both of which the
new class still provides.

- [ ] **Step 2: Run the existing tests to prove the harness still works**

Run:
```bash
python -m pytest api/tests/test_onboarding_repository.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: 2 passed. If not, the harness rewrite broke them — fix before continuing.

- [ ] **Step 3: Write the failing tests**

Append to `api/tests/test_onboarding_repository.py`:

```python
def test_replace_owned_property_clears_rooms_and_baseline(monkeypatch):
    """Swapping the property must not leave the old hotel's rooms behind."""
    # One scripted result per statement, in execution order: FakeConnection pops
    # them as they run, so a short list starves the final RETURNING.
    connection = FakeConnection(
        results=[
            FakeResult(rows=[{"canonical_destination": "faliraki"}]),  # SELECT ... FOR UPDATE
            FakeResult(rowcount=2),                                    # DELETE room types
            FakeResult(rows=[{"id": OWNED_PROPERTY_ID}]),              # UPDATE ... RETURNING id
        ]
    )
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request(raw_destination="Faliraki")
    )

    assert result == {"id": OWNED_PROPERTY_ID, "market_changed": False}
    joined = " ".join(" ".join(sql.split()) for sql, _ in connection.executed)
    # Old room types always go: they belong to the previous hotel.
    assert "DELETE FROM roomrate_owned_property_room_types" in joined
    # Same market -> tracked competitors are still valid and must survive.
    assert "DELETE FROM roomrate_tracked_competitors" not in joined
    # The baseline room pointed at a deleted room type.
    assert "selected_room_type_category = NULL" in joined
    # The row id is preserved so nothing referencing it breaks.
    assert "UPDATE roomrate_owned_properties" in joined

    # Asserting the SQL text alone would not notice the NEW property's fields
    # never reaching the bind params -- and pointing at a different Booking
    # page is the entire feature.
    update_sql, update_params = connection.executed[-1]
    assert update_params["display_name"] == "Bellezza"
    assert update_params["booking_url"] == "https://www.booking.com/hotel/gr/bellezza.html"
    assert update_params["owned_property_id"] == OWNED_PROPERTY_ID
    # An inactive property must not be resurrected by a replace.
    assert "is_active = true" in " ".join(update_sql.split())
    # Every statement is account-fenced, not only the ownership check.
    assert all(params["account_id"] == ACCOUNT_ID for _, params in connection.executed)


def test_replace_owned_property_drops_tracked_when_the_market_changes(monkeypatch):
    """A different destination makes the tracked competitor set meaningless."""
    connection = FakeConnection(
        results=[
            FakeResult(rows=[{"canonical_destination": "faliraki"}]),  # SELECT ... FOR UPDATE
            FakeResult(rowcount=2),                                    # DELETE room types
            FakeResult(rowcount=1),                                    # DELETE tracked competitors
            FakeResult(rows=[{"id": OWNED_PROPERTY_ID}]),              # UPDATE ... RETURNING id
        ]
    )
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request(raw_destination="Lindos")
    )

    assert result == {"id": OWNED_PROPERTY_ID, "market_changed": True}
    joined = " ".join(" ".join(sql.split()) for sql, _ in connection.executed)
    assert "DELETE FROM roomrate_tracked_competitors" in joined


def test_replace_owned_property_returns_none_for_another_account(monkeypatch):
    """A property that is not ours must be indistinguishable from missing."""
    connection = FakeConnection(results=[FakeResult(rows=[])])
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request()
    )

    assert result is None
    # Nothing was mutated after the ownership check failed.
    # (Not a plain "UPDATE" substring check: the locking read itself is a
    # SELECT ... FOR UPDATE, so that would trip on its own SQL text. Normalized
    # like the other tests, so reformatted SQL cannot pass this vacuously.)
    joined = " ".join(" ".join(sql.split()) for sql, _ in connection.executed)
    assert "DELETE" not in joined
    assert "UPDATE roomrate_owned_properties" not in joined


def test_replace_owned_property_locks_the_row_for_the_whole_transaction(monkeypatch):
    """Without FOR UPDATE two concurrent replaces could both read the old market.

    Both halves matter and neither works alone: a row lock outside a
    transaction is released immediately, and a transaction without the lock
    still lets a concurrent replace read a stale ``canonical_destination`` and
    keep competitors it should have dropped.
    """
    connection = FakeConnection(
        results=[
            FakeResult(rows=[{"canonical_destination": "faliraki"}]),  # SELECT ... FOR UPDATE
            FakeResult(rowcount=2),                                    # DELETE room types
            FakeResult(rows=[{"id": OWNED_PROPERTY_ID}]),              # UPDATE ... RETURNING id
        ]
    )
    engine = _patch_engine(monkeypatch, connection)

    OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request()
    )

    first_sql = " ".join(connection.executed[0][0].split())
    assert "SELECT canonical_destination" in first_sql
    assert "FOR UPDATE" in first_sql
    # begin(), not connect(): the partial states in between are all invalid.
    assert engine.opened == ["begin"]
```

Add this helper near the other helpers in the file:

```python
def _replacement_request(raw_destination: str = "Faliraki") -> OwnedPropertyOnboardingCreate:
    return OwnedPropertyOnboardingCreate(
        display_name="Bellezza",
        booking_url="https://www.booking.com/hotel/gr/bellezza.html",
        city="Faliraki",
        raw_destination=raw_destination,
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
    )
```

Ensure these imports exist at the top of the test file:

```python
from datetime import date

from api.schemas.onboarding import OwnedPropertyOnboardingCreate
```

- [ ] **Step 4: Run tests to verify they fail**

Run:
```bash
python -m pytest api/tests/test_onboarding_repository.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: FAIL with `AttributeError: 'OnboardingRepository' object has no attribute 'replace_owned_property'`.

- [ ] **Step 5: Implement the repository method**

Add to `api/repositories/onboarding_repository.py`, after `create_owned_property`:

```python
    def replace_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> dict[str, Any] | None:
        """Point an existing owned property at a different Booking property.

        One transaction, because the intermediate states are all wrong: room
        types belonging to the previous hotel, or a baseline category that no
        longer exists. Returns ``{"id", "market_changed"}``, or None when the
        property does not belong to this account (the caller 404s).

        The row id is preserved so scrape jobs, alert rules and audits that
        reference it stay valid.
        """
        with self._engine().begin() as connection:
            existing = connection.execute(
                text(
                    """
                    SELECT canonical_destination
                    FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                      AND is_active = true
                    FOR UPDATE
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            ).mappings().first()
            if existing is None:
                return None

            market_changed = existing["canonical_destination"] != request.canonical_destination

            # The discovered rooms describe the OLD hotel.
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_owned_property_room_types
                    WHERE account_id = :account_id
                      AND owned_property_id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            )
            if market_changed:
                # Competitors were selected for a different destination.
                connection.execute(
                    text(
                        """
                        DELETE FROM roomrate_tracked_competitors
                        WHERE account_id = :account_id
                          AND owned_property_id = :owned_property_id
                        """
                    ),
                    {"account_id": account_id, "owned_property_id": owned_property_id},
                )

            row = connection.execute(
                text(
                    """
                    UPDATE roomrate_owned_properties
                    SET
                        display_name = :display_name,
                        booking_url = :booking_url,
                        address = :address,
                        city = :city,
                        raw_destination = :raw_destination,
                        canonical_destination = :canonical_destination,
                        country = :country,
                        property_type = :property_type,
                        latitude = :latitude,
                        longitude = :longitude,
                        location_source = 'booking_search',
                        location_confidence = 0.90,
                        selected_room_type_category = NULL,
                        updated_at = now()
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                      AND is_active = true
                    RETURNING id
                    """
                ),
                {
                    "account_id": account_id,
                    "owned_property_id": owned_property_id,
                    "display_name": request.display_name,
                    "booking_url": str(request.booking_url),
                    "address": request.address,
                    "city": request.city,
                    "raw_destination": request.raw_destination,
                    "canonical_destination": request.canonical_destination,
                    "country": request.country,
                    "property_type": request.property_type,
                    "latitude": request.latitude,
                    "longitude": request.longitude,
                },
            ).mappings().one()
        return {"id": row["id"], "market_changed": market_changed}
```

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_onboarding_repository.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: PASS, all tests in the file (existing ones included).

- [ ] **Step 7: Commit**

```bash
git add api/repositories/onboarding_repository.py api/tests/test_onboarding_repository.py
git commit -m "Add OnboardingRepository.replace_owned_property

Swaps an owned property to a different Booking property in one transaction:
locks the row, drops the previous hotel's room types, drops tracked
competitors only when the destination changes, clears the baseline category
and updates the fields. The row id is preserved so referencing rows survive."
```

---

### Task 3: Repository — delete the owned property

**Files:**
- Modify: `api/repositories/onboarding_repository.py`
- Test: `api/tests/test_onboarding_repository.py`

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_onboarding_repository.py`:

```python
def test_delete_owned_property_is_account_fenced(monkeypatch):
    connection = FakeConnection(results=[FakeResult(rowcount=1)])
    engine = _patch_engine(monkeypatch, connection)

    deleted = OnboardingRepository().delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert deleted is True
    sql, params = connection.executed[0]
    normalized = " ".join(sql.split())
    assert "DELETE FROM roomrate_owned_properties" in normalized
    # The whole predicate, not fragments: SQLAlchemy ignores unused binds, so a
    # dropped fence would leave the params assertion below green (Task 2 lesson).
    assert "WHERE account_id = :account_id AND id = :owned_property_id" in normalized
    # No is_active fence, pinned deliberately: a deactivated property must stay
    # deletable, and a "consistency pass" adding the fence would break that.
    assert "is_active" not in normalized
    assert params == {"account_id": ACCOUNT_ID, "owned_property_id": OWNED_PROPERTY_ID}
    # begin(), not connect(): connect() rolls back on exit, but rowcount is read
    # before the rollback -- the delete would report True while the row survives.
    assert engine.opened == ["begin"]


def test_delete_owned_property_returns_false_when_nothing_matched(monkeypatch):
    """Another account's property is reported as missing, never as forbidden."""
    _patch_engine(monkeypatch, FakeConnection(results=[FakeResult(rowcount=0)]))

    assert OnboardingRepository().delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID) is False
```

`FakeResult` already exposes `rowcount` after the Task 2 harness upgrade.

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest api/tests/test_onboarding_repository.py -k delete_owned_property -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: FAIL with `AttributeError: ... has no attribute 'delete_owned_property'`.

- [ ] **Step 3: Implement the repository method**

Add to `api/repositories/onboarding_repository.py`, after `replace_owned_property`:

```python
    def delete_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
    ) -> bool:
        """Delete one owned property; returns whether a row was removed.

        The schema does the cleanup: room types, tracked competitors, alert
        rules and pricing audits are ON DELETE CASCADE, while scrape jobs are
        ON DELETE SET NULL so the scrape history survives. Scrape runs and rate
        observations reference the account, not the property — that market
        price history is deliberately kept.

        Unlike every sibling here, there is no ``is_active`` fence, on
        purpose: a deactivated property can still be deleted. Only a row
        missing for this account returns False.
        """
        with self._engine().begin() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND id = :owned_property_id
                    """
                ),
                {"account_id": account_id, "owned_property_id": owned_property_id},
            )
        return bool(result.rowcount)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_onboarding_repository.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/repositories/onboarding_repository.py api/tests/test_onboarding_repository.py
git commit -m "Add OnboardingRepository.delete_owned_property

Account-fenced delete; the schema's CASCADE/SET NULL rules do the rest, so
market price history survives while property-scoped rows go."
```

---

### Task 4: Service — orchestrate replace and delete

**Files:**
- Modify: `api/services/onboarding_service.py`
- Test: `api/tests/test_onboarding_service.py`

> **Task 4 review outcome (folded in below).** The first draft of this fake
> ignored `replace_owned_property`'s arguments and recorded nothing, so nine
> mutants survived the review's drill: swapped/replaced ids on the repository
> call, an ignored request, a dropped destination fallback, drifted crawl
> bounds, hardcoded stay fields, and a delete that always returns True — the
> exact value Task 5's 404 rides on. The snippets below already record the
> call, use a `raw_destination` that differs from `city` (otherwise the
> fallback is unobservable), assert the full job payload, and pin the False
> branch of delete. Known latent leftover, deliberately not fixed here: the
> create-path test still uses `raw_destination == city`, so the same fallback
> is unpinned there.
>
> The quality review then added three more, folded in below: **occupancy in
> the test request must be non-default** — both schemas default to `(2, 0, 1)`,
> so a replace that dropped the `adults/children/rooms` kwargs entirely was
> refilled identically by Pydantic and passed the whole suite; the duplicated
> `ScrapeJobCreate` block was extracted into a shared `_queue_room_discovery`
> helper (unlike the repository bind dicts, this was a pure cut-paste, and one
> definition means the create and replace paths cannot drift); and the fake's
> `replace_result: dict | None = "default"` sentinel became an honest
> `replace_found: bool`. Rule for the remaining tasks: **when a test asserts a
> forwarded field, give it a value the schema would not fill in by default.**

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_onboarding_service.py`:

```python
def test_replace_owned_property_queues_a_fresh_discovery_job():
    """The new hotel's rooms are unknown, so discovery must run again."""
    repository = FakeOnboardingRepository()
    scrape_jobs = FakeScrapeJobService()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=scrape_jobs,
        candidate_provider=FakeCandidateProvider([]),
    )

    # raw_destination differs from city on purpose: it pins which of the two
    # the job carries. Occupancy is non-default on purpose too: both schemas
    # default to (2, 0, 1), so dropped kwargs would be refilled identically
    # and pass unnoticed.
    request = OwnedPropertyOnboardingCreate(
        display_name="Bellezza",
        booking_url="https://www.booking.com/hotel/gr/bellezza.html",
        city="Faliraki",
        raw_destination="Faliraki, Rhodes",
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
        adults=3,
        children=1,
        rooms=2,
    )
    response = service.replace_owned_property_and_discovery_job(
        ACCOUNT_ID, OWNED_PROPERTY_ID, request
    )

    assert response.owned_property_id == OWNED_PROPERTY_ID
    # The repository saw the same identity and payload the caller sent --
    # swapped UUIDs are still UUIDs, so only recording the call catches that.
    assert repository.replaced == (ACCOUNT_ID, OWNED_PROPERTY_ID, request)
    account_id, job_request = scrape_jobs.created
    assert account_id == ACCOUNT_ID
    assert job_request.owned_property_id == OWNED_PROPERTY_ID
    assert job_request.job_type == "owned_property_room_discovery"
    # The whole stay must reach the job: discovery scrapes real prices, and a
    # dropped or hardcoded field would silently price the wrong stay.
    assert job_request.destination == "Faliraki, Rhodes"
    assert job_request.raw_destination == "Faliraki, Rhodes"
    assert job_request.check_in == date(2030, 7, 1)
    assert job_request.check_out == date(2030, 7, 5)
    assert (job_request.adults, job_request.children, job_request.rooms) == (3, 1, 2)
    # Full payload equality, like the create-path test: target_urls alone
    # would let the crawl bounds drift from the create path unnoticed.
    assert job_request.filters_payload == {
        "target_urls": ["https://www.booking.com/hotel/gr/bellezza.html"],
        "limit": 1,
        "deep_crawl_max_items": 30,
    }


def test_replace_owned_property_returns_none_when_not_owned():
    repository = FakeOnboardingRepository(replace_found=False)
    scrape_jobs = FakeScrapeJobService()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=scrape_jobs,
        candidate_provider=FakeCandidateProvider([]),
    )

    response = service.replace_owned_property_and_discovery_job(
        ACCOUNT_ID,
        OWNED_PROPERTY_ID,
        OwnedPropertyOnboardingCreate(
            display_name="Bellezza",
            booking_url="https://www.booking.com/hotel/gr/bellezza.html",
            city="Faliraki",
            check_in=date(2030, 7, 1),
            check_out=date(2030, 7, 5),
        ),
    )

    assert response is None
    # No scrape may be queued for a property we do not own.
    assert scrape_jobs.created is None


def test_delete_owned_property_delegates_to_the_repository():
    """Forwards the identity pair and returns the repository's answer.

    The False branch matters as much as True: Task 5's route turns it into
    the 404, so a service hardcoding True would delete nothing and say 204.
    """
    repository = FakeOnboardingRepository()
    service = OnboardingService(
        repository=repository,
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=FakeCandidateProvider([]),
    )

    assert service.delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID) is True
    assert repository.deleted == (ACCOUNT_ID, OWNED_PROPERTY_ID)

    missing = FakeOnboardingRepository(delete_result=False)
    service = OnboardingService(
        repository=missing,
        scrape_job_service=FakeScrapeJobService(),
        candidate_provider=FakeCandidateProvider([]),
    )
    assert service.delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID) is False
```

Extend `FakeOnboardingRepository` in the same file:

```python
class FakeOnboardingRepository:
    def __init__(self, replace_found: bool = True, delete_result: bool = True):
        self.replace_found = replace_found
        self.delete_result = delete_result
        self.replaced: tuple | None = None
        self.deleted: tuple | None = None

    def create_owned_property(self, account_id, request):
        return {"id": OWNED_PROPERTY_ID, "account_id": account_id, "display_name": request.display_name}

    def replace_owned_property(self, account_id, owned_property_id, request):
        self.replaced = (account_id, owned_property_id, request)
        if not self.replace_found:
            return None
        return {"id": OWNED_PROPERTY_ID, "market_changed": False}

    def delete_owned_property(self, account_id, owned_property_id):
        self.deleted = (account_id, owned_property_id)
        return self.delete_result

    def list_room_types(self, account_id, owned_property_id):
        return []

    def set_selected_room_type(self, account_id, owned_property_id, room_type_category):
        return True
```

Ensure `FakeScrapeJobService.__init__` sets `self.created = None` (it already
does) so the "no job queued" assertion is meaningful.

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest api/tests/test_onboarding_service.py -k "replace_owned_property or delete_owned_property" -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: FAIL — `OnboardingService` has no `replace_owned_property_and_discovery_job`.

- [ ] **Step 3: Extend the repository Protocol and the service**

In `api/services/onboarding_service.py`, add to `OnboardingRepositoryProtocol`:

```python
    def replace_owned_property(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> dict | None:
        """Point an existing owned property at a different Booking property."""

    def delete_owned_property(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID
    ) -> bool:
        """Delete one owned property; False when it was not found."""
```

Add these methods to `OnboardingService`, after
`create_owned_property_and_discovery_job`:

```python
    def replace_owned_property_and_discovery_job(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> OwnedPropertyOnboardingResponse | None:
        """Swap the owned property and rediscover the new hotel's rooms.

        Returns None when the property does not exist or is inactive for this
        account, so the router can 404 without revealing whether it exists.
        """
        row = self.repository.replace_owned_property(account_id, owned_property_id, request)
        if row is None:
            return None
        return self._queue_room_discovery(account_id, owned_property_id, request)

    def _queue_room_discovery(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        request: OwnedPropertyOnboardingCreate,
    ) -> OwnedPropertyOnboardingResponse:
        """Queue the room-discovery scrape for one owned property.

        Shared by create and replace so the discovery job cannot drift between
        the two paths: same Booking page target, same crawl bounds, same stay.
        """
        job = self.scrape_job_service.create_job(
            account_id,
            ScrapeJobCreate(
                owned_property_id=owned_property_id,
                job_type=JOB_TYPE_ROOM_DISCOVERY,
                # raw_destination is always backfilled by the schema validator;
                # `or city` is belt-and-braces and keeps the type plainly str.
                destination=request.raw_destination or request.city,
                raw_destination=request.raw_destination,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                rooms=request.rooms,
                filters_payload={
                    "target_urls": [str(request.booking_url)],
                    "limit": 1,
                    "deep_crawl_max_items": 30,
                },
            ),
        )
        return OwnedPropertyOnboardingResponse(owned_property_id=owned_property_id, discovery_job=job)

    def delete_owned_property(
        self, account_id: uuid.UUID, owned_property_id: uuid.UUID
    ) -> bool:
        """Delete one owned property; False when it was not found."""
        return self.repository.delete_owned_property(account_id, owned_property_id)
```

`create_owned_property_and_discovery_job`'s tail collapses to the same helper
(`return self._queue_room_discovery(account_id, row["id"], request)`), so the
job construction exists exactly once.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_onboarding_service.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/onboarding_service.py api/tests/test_onboarding_service.py
git commit -m "Add replace/delete owned-property orchestration to OnboardingService

Replace queues a fresh discovery job against the newly chosen Booking page,
and returns None for a property the account does not own so the router can
404 without disclosing existence."
```

---

### Task 5: Routes — PUT and DELETE /owned-property/{id}

**Files:**
- Modify: `api/routers/onboarding.py`
- Test: `api/tests/test_onboarding_routes.py`

> **Task 5 review outcome (folded in below).** The spec review proved the 202
> test never checked that the discovery job was *scheduled* (only returned),
> unlike the POST tests one screen up — one `run_calls` assertion closes it.
> The quality review added: the PUT docstring must state the 429 quota path
> like its POST siblings (QuotaExceededError is not ValueError, so it escapes
> the route's 422 handler); both routes publish 404 via `responses=` so the
> generated client sees it; the PUT docstring says the response does not
> reveal whether competitors were dropped (re-read them after a replace); the
> replace-404 test is named for what its fake demonstrates (not-found), with
> the tenancy proof cited at the repository layer; and an autouse fixture
> clears `app.dependency_overrides` on teardown so a failed assertion cannot
> leak fakes into later tests. Take-or-leave left as-is: deduplicating the
> fake's two 17-field ScrapeJobResponse literals.

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_onboarding_routes.py`:

```python
def test_replace_owned_property_returns_202_and_the_discovery_job():
    service = FakeOnboardingService()
    client = _client(service)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}",
        json={
            "display_name": "Bellezza",
            "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
            "city": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["owned_property_id"] == str(OWNED_PROPERTY_ID)
    assert body["discovery_job"]["job_type"] == "owned_property_room_discovery"
    # The route forwarded the caller's identity, the path id and the parsed
    # body -- not some other property's.
    account_id, owned_property_id, request = service.replaced
    assert (account_id, owned_property_id) == (ACCOUNT_ID, OWNED_PROPERTY_ID)
    assert request.display_name == "Bellezza"
    # The job was scheduled to actually run, not just returned in the body
    # (process_role is "all" under tests, same as the POST assertions above).
    assert service.scrape_job_service.run_calls == [(ACCOUNT_ID, JOB_ID)]

    app.dependency_overrides.clear()


def test_replace_owned_property_404s_when_not_found():
    """The tenancy half of the story (foreign account -> None) is proven at
    the repository layer; here the fake only demonstrates None -> 404."""
    service = FakeOnboardingService(replace_found=False)
    client = _client(service)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}",
        json={
            "display_name": "Bellezza",
            "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
            "city": "Faliraki",
            "check_in": "2030-07-01",
            "check_out": "2030-07-05",
        },
    )

    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_replace_owned_property_rejects_a_past_check_in():
    """Same guard as create: Booking cannot price a stay that already started."""
    service = FakeOnboardingService()
    client = _client(service)

    response = client.put(
        f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}",
        json={
            "display_name": "Bellezza",
            "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
            "city": "Faliraki",
            "check_in": "2020-07-01",
            "check_out": "2020-07-05",
        },
    )

    assert response.status_code == 422

    app.dependency_overrides.clear()


def test_delete_owned_property_returns_204():
    service = FakeOnboardingService()
    client = _client(service)

    response = client.delete(f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}")

    assert response.status_code == 204
    assert response.content == b""
    assert service.deleted == (ACCOUNT_ID, OWNED_PROPERTY_ID)

    app.dependency_overrides.clear()


def test_delete_owned_property_404s_when_missing():
    service = FakeOnboardingService(delete_result=False)
    client = _client(service)

    response = client.delete(f"/api/v1/onboarding/owned-property/{OWNED_PROPERTY_ID}")

    assert response.status_code == 404

    app.dependency_overrides.clear()
```

This file has **no** `_client()` helper — its tests set
`app.dependency_overrides` inline — and `FakeOnboardingService.__init__` takes
no arguments. Add both, so the new tests read cleanly.

While you are in this class, also fix its `search_property_candidates(self, **kwargs)`
to declare the real `OnboardingService.search_property_candidates` signature
(`property_name, location, check_in, check_out, adults, children, rooms, limit`,
all keyword-passed by the route). This is the same hole the Task 1 review closed
one layer down in `FakeCandidateProvider`, left standing here: today, forwarding
a newly added query parameter the service does not accept still passes the test
while production raises `TypeError` and returns 500. Keep the body as it is.

First extend `FakeOnboardingService`. Change its constructor signature and
append two methods (leave every other existing method untouched):

```python
class FakeOnboardingService:
    def __init__(self, replace_found: bool = True, delete_result: bool = True):
        self.scrape_job_service = FakeScrapeJobService()
        self.created = None
        self.auto_setup_request = None
        self.replace_found = replace_found
        self.delete_result = delete_result
        self.replaced: tuple | None = None
        self.deleted: tuple | None = None

    # ... existing search_property_candidates / create_owned_property_and_discovery_job /
    #     automatic_setup / list_room_types / select_owned_property_room_type stay as-is ...

    def replace_owned_property_and_discovery_job(self, account_id, owned_property_id, request):
        self.replaced = (account_id, owned_property_id, request)
        if not self.replace_found:
            return None
        return OwnedPropertyOnboardingResponse(
            owned_property_id=owned_property_id,
            discovery_job=ScrapeJobResponse(
                id=JOB_ID,
                account_id=account_id,
                owned_property_id=owned_property_id,
                job_type="owned_property_room_discovery",
                room_type_category=None,
                destination=request.city,
                raw_destination=request.raw_destination,
                canonical_destination=request.canonical_destination,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                rooms=request.rooms,
                filters_payload={"target_urls": [str(request.booking_url)]},
                status="queued",
                requested_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
                scrape_runs_count=0,
            ),
        )

    def delete_owned_property(self, account_id, owned_property_id):
        self.deleted = (account_id, owned_property_id)
        return self.delete_result
```

Then add the client helper next to the other module-level helpers:

```python
def _client(service: FakeOnboardingService) -> TestClient:
    """Wire the account context and onboarding service, matching the
    inline overrides the older tests in this file already use."""
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_onboarding_service] = lambda: service
    return TestClient(app)
```

Every import these need (`datetime`, `timezone`, `JOB_ID`, `ACCOUNT_ID`,
`OwnedPropertyOnboardingResponse`, `ScrapeJobResponse`, `TestClient`, `app`,
`get_account_context`, `get_onboarding_service`, `AccountContext`) is already
present at the top of this file.

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest api/tests/test_onboarding_routes.py -k "replace_owned_property or delete_owned_property" -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: FAIL with 404 (not 405: no existing route registers the exact path
`/owned-property/{id}` for any method — POST lives at `/owned-property`
without the id segment — and Starlette only answers 405 on an exact-path,
wrong-method match). The two 404-asserting tests pass vacuously at this
stage; the red phase is carried by the other three.

- [ ] **Step 3: Add the routes**

In `api/routers/onboarding.py`, add after `create_owned_property`:

```python
@router.put(
    "/owned-property/{owned_property_id}",
    response_model=OwnedPropertyOnboardingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"description": "Owned property not found"}},
)
async def replace_owned_property(
    owned_property_id: UUID,
    request: OwnedPropertyOnboardingCreate,
    background_tasks: BackgroundTasks,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> OwnedPropertyOnboardingResponse:
    """Point the owned property at a different Booking property.

    Used when the user rejects the auto-matched candidate. Drops the previous
    hotel's room types and baseline, drops tracked competitors when the
    destination changes, and queues a fresh room discovery. The response does
    not report whether competitors were dropped, so re-read the tracked
    competitor list after a replace.

    Quota refusals (QuotaExceededError) propagate to the app-level handler in
    ``api.main`` and become 429 there.
    """
    _validate_active_stay(request.check_in, request.check_out)
    try:
        response = service.replace_owned_property_and_discovery_job(
            account.account_id, owned_property_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    if settings.process_role == "all":
        background_tasks.add_task(
            service.scrape_job_service.run_job,
            account.account_id,
            response.discovery_job.id,
        )
    return response


@router.delete(
    "/owned-property/{owned_property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Owned property not found"}},
)
async def delete_owned_property(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    service: OnboardingService = Depends(get_onboarding_service),
) -> Response:
    """Delete the owned property and everything scoped to it.

    Room types, tracked competitors, alert rules and pricing audits cascade
    away; scrape jobs survive with a NULL property. Market price history is
    account-scoped and is deliberately kept.
    """
    if not service.delete_owned_property(account.account_id, owned_property_id):
        raise HTTPException(status_code=404, detail="Owned property not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Add the imports this needs at the top of the file:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
```

(`OwnedPropertyOnboardingCreate` and `OwnedPropertyOnboardingResponse` are
already imported in this module for the POST route.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_onboarding_routes.py -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run:
```bash
python -m pytest api/tests -q --basetemp="C:/Users/vagel/AppData/Local/Temp/claude/C--vscode-code-room-project2/2140431d-a68e-4de8-9de8-95e6c8d8d22e/scratchpad/pytest-tmp"
```
Expected: 565 passed (551 after Task 1 + 14 new across Tasks 2-5).

- [ ] **Step 6: Commit**

```bash
git add api/routers/onboarding.py api/tests/test_onboarding_routes.py
git commit -m "Add PUT/DELETE /onboarding/owned-property/{id}

The wizard needs a wrong auto-match to cost one click instead of being
permanent. Both routes are account-fenced and answer 404 (never 403) for a
property belonging to someone else."
```

---

### Task 6: Verify against the live database

The repository tests use a fake engine, so the SQL is asserted but never
executed. This task runs it once for real.

**Files:** none modified.

- [ ] **Step 1: Confirm migrations are at head**

Run:
```bash
python -m alembic current
```
Expected: `20260727_0021 (head)`.

- [ ] **Step 2: Exercise replace and delete against PostgreSQL**

Run:
```bash
PYTHONIOENCODING=utf-8 python -c "
import sys, uuid; sys.path.insert(0,'.')
from datetime import date, timedelta
from api.config import settings
from api.repositories.onboarding_repository import OnboardingRepository
from api.schemas.onboarding import OwnedPropertyOnboardingCreate

account_id = settings.roomrate_default_account_id
repo = OnboardingRepository()
stay = date.today() + timedelta(days=30)

def request(name, destination):
    return OwnedPropertyOnboardingCreate(
        display_name=name,
        booking_url=f'https://www.booking.com/hotel/gr/{name.lower()}.html',
        city=destination, raw_destination=destination,
        check_in=stay, check_out=stay + timedelta(days=4))

created = repo.create_owned_property(account_id, request('PlanProbe', 'Faliraki'))
pid = created['id']
print('created', pid)

same = repo.replace_owned_property(account_id, pid, request('PlanProbe2', 'Faliraki'))
print('same-market replace ->', same)
assert same['market_changed'] is False

moved = repo.replace_owned_property(account_id, pid, request('PlanProbe3', 'Lindos'))
print('cross-market replace ->', moved)
assert moved['market_changed'] is True

foreign = repo.replace_owned_property(uuid.uuid4(), pid, request('X', 'Faliraki'))
print('foreign account replace ->', foreign)
assert foreign is None

print('delete ->', repo.delete_owned_property(account_id, pid))
print('delete again ->', repo.delete_owned_property(account_id, pid))
print('LIVE SQL OK')
"
```
Expected final line: `LIVE SQL OK`, with `same-market replace` reporting
`market_changed: False`, `cross-market` reporting `True`, the foreign account
returning `None`, the first delete `True` and the second `False`.

- [ ] **Step 3: Confirm nothing leaked**

Run:
```bash
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'.')
import sqlalchemy
from api.config import settings
engine = sqlalchemy.create_engine(settings.database_url)
with engine.connect() as c:
    left = c.execute(sqlalchemy.text(
        \"SELECT count(*) FROM roomrate_owned_properties WHERE display_name LIKE 'PlanProbe%'\"
    )).scalar()
print('probe rows left:', left)
"
```
Expected: `probe rows left: 0`.

---

### Task 7: Document both endpoints

**Files:**
- Modify: `docs/DOCUMENTATION.md`

- [ ] **Step 1: Add the reference entries**

Find the `#### POST /api/v1/onboarding/owned-property` section and add
immediately after it:

```markdown
#### PUT /api/v1/onboarding/owned-property/{owned_property_id}
**Purpose:** Point the owned property at a different Booking property, used
when the user rejects the auto-matched candidate in the setup wizard.

**Request body:** identical to the POST create body
(`OwnedPropertyOnboardingCreate`).

**Behaviour (one transaction):**
- updates the property fields and `canonical_destination`
- deletes the previous hotel's discovered room types
- clears `selected_room_type_category` (it pointed at a deleted room type)
- deletes tracked competitors **only when `canonical_destination` changes** —
  a different hotel in the same market keeps the same competitors
- queues a fresh room-discovery scrape against the new Booking URL

The row id is preserved, so scrape jobs, alert rules and pricing audits that
reference the property stay valid.

**Response:** `202` with `{owned_property_id, discovery_job}`.

**Errors:**
```text
404 Owned property not found     # includes properties owned by another account
422 check_in cannot be in the past
```

#### DELETE /api/v1/onboarding/owned-property/{owned_property_id}
**Purpose:** Remove the owned property and everything scoped to it.

| Data | Fate |
|---|---|
| Room types, tracked competitors, alert rules, pricing audits | Deleted (FK CASCADE) |
| Scrape jobs | Kept, with `owned_property_id = NULL` |
| Scrape runs, rate observations (market price history) | Kept — account-scoped, not property-scoped |

**Response:** `204` with an empty body.

**Errors:**
```text
404 Owned property not found
```
```

- [ ] **Step 2: Note the candidate ordering**

Find the `#### GET /api/v1/onboarding/property-candidates` section and append
to its Purpose paragraph:

```markdown
Results are ordered best-match-first: exact name match, then substring
containment, then token overlap with the review score breaking ties (candidates
with no review score sort last). Equal scores keep Booking's own relevance
order. Clients can therefore preselect `candidates[0]`, and `POST /auto-setup`
picks that same head of the list. Note this overrides Booking's ranking for
candidates that do not match the name.
```

- [ ] **Step 3: Commit**

```bash
git add docs/DOCUMENTATION.md
git commit -m "Document PUT/DELETE owned-property and candidate ordering"
```

---

## Done criteria

- [ ] `python -m pytest api/tests -q --basetemp=<scratchpad>/pytest-tmp` → 565 passed
- [ ] `python -m pyflakes api scraper scripts` → only the known
      `scraper/utils.py:256` re-export line (load-bearing, see the cleanup commit)
- [ ] Task 6 live-database run printed `LIVE SQL OK` and `probe rows left: 0`
- [ ] `docs/DOCUMENTATION.md` documents both new endpoints

## What this plan deliberately does not do

The frontend wizard, the setup checklist and the empty states are a separate
plan — they depend on these endpoints existing but nothing here depends on
them. After this plan the API can already: propose a best-match property,
swap it, and delete it.
