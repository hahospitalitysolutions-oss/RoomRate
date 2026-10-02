import hashlib
import threading
from contextlib import contextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.rate_limit import (
    RATE_LIMIT_LOCK_NAMESPACE,
    PostgresSlidingWindowLimiter,
    RateLimitMiddleware,
    SlidingWindowLimiter,
    client_key,
)


class FakeClock:
    """Deterministic monotonic clock injected into SlidingWindowLimiter."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_client(limiter: SlidingWindowLimiter, limit: int) -> TestClient:
    """Minimal app exposing one limited and one unlimited POST path."""
    test_app = FastAPI()

    @test_app.post("/api/v1/scrape-jobs/")
    async def create_job():
        return {"ok": True}

    @test_app.get("/api/v1/scrape-jobs/")
    async def list_jobs():
        return []

    @test_app.post("/api/v1/agents/price-recommendation")
    async def recommend():
        return {"ok": True}

    @test_app.post("/api/v1/unlimited")
    async def unlimited():
        return {"ok": True}

    test_app.add_middleware(RateLimitMiddleware, limiter=limiter, limit_getter=lambda: limit)
    return TestClient(test_app)


# ----------------------------------------------------------------------------
# SlidingWindowLimiter
# ----------------------------------------------------------------------------


def test_limiter_allows_under_limit_and_blocks_over_limit():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)

    assert limiter.check("k", limit=2) is None
    assert limiter.check("k", limit=2) is None
    retry_after = limiter.check("k", limit=2)

    assert retry_after is not None and 0 < retry_after <= 60


def test_limiter_window_slides_and_frees_slots():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)

    assert limiter.check("k", limit=1) is None
    clock.advance(30)
    assert limiter.check("k", limit=1) == 30  # oldest hit expires in 30s
    clock.advance(31)
    assert limiter.check("k", limit=1) is None  # window slid past the old hit


def test_limiter_zero_limit_disables_counting():
    limiter = SlidingWindowLimiter(clock=FakeClock())
    for _ in range(100):
        assert limiter.check("k", limit=0) is None


def test_limiter_keys_are_isolated():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)

    assert limiter.check("a", limit=1) is None
    assert limiter.check("a", limit=1) is not None  # a is exhausted
    assert limiter.check("b", limit=1) is None  # b is unaffected


def test_limiter_evicts_stale_buckets():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)

    for i in range(50):
        limiter.check(f"one-off-{i}", limit=5)
    clock.advance(121)  # two full windows: sweep triggers, all hits stale
    limiter.check("fresh", limit=5)

    assert set(limiter._buckets) == {"fresh"}


# ----------------------------------------------------------------------------
# client_key
# ----------------------------------------------------------------------------


def _scope(headers: list[tuple[bytes, bytes]], client=("10.0.0.9", 1234)) -> dict:
    return {"type": "http", "headers": headers, "client": client}


def test_client_key_ignores_account_header_from_a_bearer_client():
    """A browser client must not be able to pick its own bucket.

    ``X-RoomRate-Account-ID`` is ignored by the auth layer whenever a bearer
    token is present, but it used to decide the rate-limit bucket — so a
    client sending a fresh random UUID per request landed in a brand-new
    bucket every time and the limit never fired.
    """
    first = client_key(
        _scope([
            (b"authorization", b"Bearer token-abc"),
            (b"x-roomrate-account-id", b"11111111-1111-1111-1111-111111111111"),
        ])
    )
    second = client_key(
        _scope([
            (b"authorization", b"Bearer token-abc"),
            (b"x-roomrate-account-id", b"22222222-2222-2222-2222-222222222222"),
        ])
    )

    assert first == second
    assert "11111111" not in first and "22222222" not in first


def test_client_key_separates_distinct_bearer_tokens():
    one = client_key(_scope([(b"authorization", b"Bearer token-one")]))
    two = client_key(_scope([(b"authorization", b"Bearer token-two")]))

    assert one != two
    # The raw token must not be usable from the bucket key.
    assert "token-one" not in one


def test_client_key_uses_account_header_only_for_internal_api_key_callers():
    scope = _scope([
        (b"x-api-key", b"internal-secret"),
        (b"x-roomrate-account-id", b"abc-123"),
        (b"x-forwarded-for", b"1.2.3.4"),
    ])
    assert client_key(scope) == "account:abc-123"


def test_client_key_falls_back_to_ip_when_account_header_has_no_api_key():
    scope = _scope([(b"x-roomrate-account-id", b"abc-123"), (b"x-forwarded-for", b"1.2.3.4")])
    assert client_key(scope) == "ip:1.2.3.4"


def test_client_key_uses_first_forwarded_for_value():
    scope = _scope([(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")])
    assert client_key(scope) == "ip:1.2.3.4"


def test_client_key_falls_back_to_socket_peer_then_unknown():
    assert client_key(_scope([])) == "ip:10.0.0.9"
    assert client_key(_scope([], client=None)) == "ip:unknown"


# ----------------------------------------------------------------------------
# RateLimitMiddleware (via TestClient)
# ----------------------------------------------------------------------------


def test_under_limit_requests_pass():
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=3)
    for _ in range(3):
        assert client.post("/api/v1/scrape-jobs/").status_code == 200


def test_over_limit_returns_429_with_retry_after():
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=2)
    client.post("/api/v1/scrape-jobs/")
    client.post("/api/v1/scrape-jobs/")

    response = client.post("/api/v1/scrape-jobs/")

    assert response.status_code == 429
    assert response.json() == {"detail": "rate limit exceeded"}
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_disabled_mode_never_limits():
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=0)
    for _ in range(40):
        assert client.post("/api/v1/scrape-jobs/").status_code == 200


def test_per_key_isolation_by_account_header_for_internal_callers():
    """Internal (API-key) callers still get one bucket per account."""
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=1)
    headers_a = {"X-API-Key": "internal-secret", "X-RoomRate-Account-ID": "account-a"}
    headers_b = {"X-API-Key": "internal-secret", "X-RoomRate-Account-ID": "account-b"}

    assert client.post("/api/v1/scrape-jobs/", headers=headers_a).status_code == 200
    assert client.post("/api/v1/scrape-jobs/", headers=headers_a).status_code == 429
    # A different account is not throttled by account-a's bucket.
    assert client.post("/api/v1/scrape-jobs/", headers=headers_b).status_code == 200


def test_bearer_client_cannot_escape_its_bucket_by_rotating_the_account_header():
    """End-to-end version of the bypass: same token, different fake accounts."""
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=1)
    token = {"Authorization": "Bearer session-token"}

    assert client.post("/api/v1/scrape-jobs/", headers=token).status_code == 200
    assert client.post(
        "/api/v1/scrape-jobs/",
        headers={**token, "X-RoomRate-Account-ID": "a-brand-new-uuid"},
    ).status_code == 429


def test_limit_is_shared_across_the_scoped_paths_for_one_key():
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=1)

    assert client.post("/api/v1/scrape-jobs/").status_code == 200
    # Same caller key: a different expensive endpoint hits the same bucket.
    assert client.post("/api/v1/agents/price-recommendation").status_code == 429


def test_unscoped_paths_and_non_post_methods_are_never_limited():
    client = build_client(SlidingWindowLimiter(clock=FakeClock()), limit=1)
    client.post("/api/v1/scrape-jobs/")  # exhausts the bucket

    assert client.post("/api/v1/unlimited").status_code == 200
    assert client.get("/api/v1/scrape-jobs/").status_code == 200


def test_real_app_wiring_returns_429_when_over_limit(monkeypatch):
    # End-to-end through api.main's middleware stack. The bucket is isolated
    # from the rest of the suite by a unique bearer token: an account header
    # alone no longer selects a bucket (a client could otherwise mint a fresh
    # one per request and never hit the limit).
    from api.config import settings
    from api.dependencies import AccountContext, get_account_context, get_scrape_job_service
    from api.main import app
    from api.tests.test_scrape_job_routes import ACCOUNT_ID, FakeScrapeJobService

    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    app.dependency_overrides[get_account_context] = lambda: AccountContext(account_id=ACCOUNT_ID)
    app.dependency_overrides[get_scrape_job_service] = lambda: FakeScrapeJobService()
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer rate-limit-test-{uuid4()}"}
        body = {"destination": "Rhodes", "check_in": "2099-07-01", "check_out": "2099-07-05"}

        first = client.post("/api/v1/scrape-jobs/", json=body, headers=headers)
        second = client.post("/api/v1/scrape-jobs/", json=body, headers=headers)

        assert first.status_code == 202
        assert second.status_code == 429
        assert "Retry-After" in second.headers
        # Security headers still wrap the 429 short-circuit response.
        assert second.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        app.dependency_overrides.clear()


# ----------------------------------------------------------------------------
# PostgresSlidingWindowLimiter: one window per caller for every API process
# ----------------------------------------------------------------------------

class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row


class _Connection:
    """Answers the window COUNT with ``hits``; records every statement."""

    def __init__(self, hits: int, retry_after: float | None):
        self.hits = hits
        self.retry_after = retry_after
        self.statements: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        if sql.startswith("SELECT count(*)"):
            return _Result({"hits": self.hits, "retry_after": self.retry_after})
        return _Result()


class _Engine:
    def __init__(self, hits: int = 0, retry_after: float | None = None):
        self.connection = _Connection(hits, retry_after)
        self.transactions = 0

    @contextmanager
    def begin(self):
        self.transactions += 1
        yield self.connection


def _sql(engine: _Engine) -> list[str]:
    return [sql for sql, _ in engine.connection.statements]


def test_shared_limiter_records_a_hit_under_the_limit_in_one_locked_transaction():
    engine = _Engine(hits=4)
    limiter = PostgresSlidingWindowLimiter(engine_getter=lambda: engine, clock=FakeClock())

    assert limiter.check("token:abc", limit=5) is None

    statements = _sql(engine)
    assert engine.transactions == 1
    # The per-key lock comes first, so concurrent checks of one caller in
    # other processes serialize and cannot both take the last slot.
    assert statements[0] == "SELECT pg_advisory_xact_lock(:namespace, hashtext(:key_hash))"
    assert statements[1].startswith("DELETE FROM roomrate_rate_limit_hits WHERE key_hash = :key_hash")
    assert statements[2].startswith("SELECT count(*) AS hits")
    assert statements[3] == (
        "INSERT INTO roomrate_rate_limit_hits (key_hash, hit_at) VALUES (:key_hash, now())"
    )
    lock_params = engine.connection.statements[0][1]
    assert lock_params["namespace"] == RATE_LIMIT_LOCK_NAMESPACE
    # Only a hash of the caller key reaches the table, never the raw key.
    expected_hash = hashlib.sha256(b"token:abc").hexdigest()
    assert {params.get("key_hash") for _, params in engine.connection.statements} == {expected_hash}


def test_shared_limiter_refuses_at_the_limit_without_recording_the_hit():
    engine = _Engine(hits=5, retry_after=42.5)
    limiter = PostgresSlidingWindowLimiter(engine_getter=lambda: engine, clock=FakeClock())

    assert limiter.check("token:abc", limit=5) == 42.5
    assert not any(sql.startswith("INSERT") for sql in _sql(engine))


def test_shared_limiter_zero_limit_never_touches_the_database():
    engine = _Engine()
    limiter = PostgresSlidingWindowLimiter(engine_getter=lambda: engine, clock=FakeClock())

    assert limiter.check("k", limit=0) is None
    assert engine.transactions == 0


def test_shared_limiter_sweeps_every_expired_row_once_per_window():
    clock = FakeClock()
    engine = _Engine(hits=0)
    limiter = PostgresSlidingWindowLimiter(engine_getter=lambda: engine, clock=clock)
    sweep = "DELETE FROM roomrate_rate_limit_hits WHERE hit_at <= now()"

    limiter.check("a", limit=5)
    clock.advance(61)
    limiter.check("b", limit=5)
    limiter.check("c", limit=5)

    assert sum(sql.startswith(sweep) for sql in _sql(engine)) == 1


def test_shared_limiter_falls_back_to_the_process_window_when_the_database_fails(caplog):
    def broken():
        raise RuntimeError("database down")

    limiter = PostgresSlidingWindowLimiter(engine_getter=broken, clock=FakeClock())

    outcomes = [limiter.check("k", limit=2) for _ in range(3)]

    # Never an error for the caller: the in-process window keeps limiting.
    assert outcomes[:2] == [None, None]
    assert outcomes[2] is not None
    # One warning per window, not one per request.
    assert sum("Shared rate limit unavailable" in record.message for record in caplog.records) == 1


def test_shared_limiter_without_a_database_url_uses_the_process_window(monkeypatch):
    from api import rate_limit

    monkeypatch.setattr(rate_limit.settings, "database_url", "")
    monkeypatch.setattr(
        rate_limit, "get_engine", lambda role="api": (_ for _ in ()).throw(AssertionError("no engine"))
    )
    limiter = PostgresSlidingWindowLimiter(clock=FakeClock())

    assert limiter.check("k", limit=1) is None
    assert limiter.check("k", limit=1) is not None


def test_middleware_shares_the_window_through_postgres_when_a_database_is_configured(monkeypatch):
    from api import rate_limit

    monkeypatch.setattr(rate_limit.settings, "database_url", "postgresql://example/roomrate")
    assert isinstance(RateLimitMiddleware(app=None).limiter, PostgresSlidingWindowLimiter)
    monkeypatch.setattr(rate_limit.settings, "database_url", "")
    assert isinstance(RateLimitMiddleware(app=None).limiter, SlidingWindowLimiter)


def test_middleware_checks_off_the_event_loop():
    """The shared check is a database round trip: it must run in the threadpool."""
    seen: dict[str, threading.Thread] = {}

    class RecordingLimiter(SlidingWindowLimiter):
        def check(self, key, limit):
            seen["check"] = threading.current_thread()
            return None

    test_app = FastAPI()

    @test_app.post("/api/v1/scrape-jobs/")
    async def create_job():
        seen["loop"] = threading.current_thread()
        return {"ok": True}

    test_app.add_middleware(RateLimitMiddleware, limiter=RecordingLimiter(), limit_getter=lambda: 5)

    assert TestClient(test_app).post("/api/v1/scrape-jobs/").status_code == 200
    assert seen["check"] is not seen["loop"]
