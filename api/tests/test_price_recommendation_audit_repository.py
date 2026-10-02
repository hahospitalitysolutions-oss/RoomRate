"""SQL-shape tests for pricing recommendation audit, cache and quota."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from api.repositories import price_recommendation_audit_repository as audit_module
from api.repositories.price_recommendation_audit_repository import (
    PriceRecommendationAuditRepository,
)


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


class FakeResult:
    def __init__(self, *, scalar_value=None, rows=None):
        self.scalar_value = scalar_value
        self.rows = rows or []

    def scalar(self):
        return self.scalar_value

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        return self.rows[0]


class FakeConnection:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((str(sql), params))
        return self.results.pop(0)


class FakeEngine:
    def __init__(self, results):
        self.connection = FakeConnection(results)

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        yield self.connection


def test_count_today_is_account_scoped_and_uses_utc(monkeypatch):
    engine = FakeEngine([FakeResult(scalar_value=7)])
    monkeypatch.setattr(audit_module, "get_engine", lambda role="api": engine)

    count = PriceRecommendationAuditRepository().count_today(ACCOUNT_ID)

    sql, params = engine.connection.executed[0]
    assert count == 7
    assert "account_id = :account_id" in sql
    assert "UTC" in sql
    assert params["account_id"] == ACCOUNT_ID


def test_get_cached_requires_matching_account_hash_and_future_expiry(monkeypatch):
    cached_row = {
        "id": UUID(int=9),
        "response_payload": {"recommendation_available": False, "statistics": {}},
        "model_version": "statistical-v1",
        "prompt_version": "v1",
        "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }
    engine = FakeEngine([FakeResult(rows=[cached_row])])
    monkeypatch.setattr(audit_module, "get_engine", lambda role="api": engine)

    result = PriceRecommendationAuditRepository().get_cached(ACCOUNT_ID, "a" * 64, "v1")

    sql, params = engine.connection.executed[0]
    assert result == cached_row
    assert "cache_expires_at > now()" in sql
    assert params == {
        "account_id": ACCOUNT_ID,
        "request_hash": "a" * 64,
        "prompt_version": "v1",
    }


def test_get_cached_filters_on_prompt_version_so_stale_prompts_never_serve(monkeypatch):
    """Review fix: a row audited under a pre-upgrade prompt_version must never
    satisfy a lookup for the current one. The SQL itself carries the equality
    filter, so every FUTURE prompt/model bump is covered automatically."""
    engine = FakeEngine([FakeResult(rows=[])])
    monkeypatch.setattr(audit_module, "get_engine", lambda role="api": engine)

    result = PriceRecommendationAuditRepository().get_cached(
        ACCOUNT_ID, "a" * 64, "2026-09-29.v3"
    )

    sql, params = engine.connection.executed[0]
    assert result is None
    assert "prompt_version = :prompt_version" in sql
    assert params["prompt_version"] == "2026-09-29.v3"


def test_insert_serializes_payloads_and_returns_audit_metadata(monkeypatch):
    created_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    engine = FakeEngine([FakeResult(rows=[{"id": UUID(int=9), "created_at": created_at}])])
    monkeypatch.setattr(audit_module, "get_engine", lambda role="api": engine)

    result = PriceRecommendationAuditRepository().insert(
        account_id=ACCOUNT_ID,
        owned_property_id=PROPERTY_ID,
        request_hash="b" * 64,
        request_payload={"check_in": "2030-07-01"},
        response_payload={"recommendation_available": True},
        source="statistical",
        model_version="statistical-v1",
        prompt_version="v1",
        latency_ms=12,
        cache_minutes=15,
    )

    sql, params = engine.connection.executed[0]
    assert result["created_at"] == created_at
    assert "CAST(:request_payload AS jsonb)" in sql
    assert params["account_id"] == ACCOUNT_ID
    assert params["owned_property_id"] == PROPERTY_ID
    assert params["request_payload"] == '{"check_in": "2030-07-01"}'
    assert params["cache_minutes"] == 15
