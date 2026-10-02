"""Shared test fakes for the RoomRate API test suite.

Only genuinely duplicated scaffolds live here; bespoke per-file variants
(e.g. the alerts repository's single-result engine or the onboarding
repository's row-echo connection) stay local to their test files.

Three families:

1. Scripted engine — for repository tests that monkeypatch a module-level
   ``get_engine``: FakeResult / FakeConnection / FakeTransaction / FakeEngine
   plus the ``install_scripted_engine`` helper.
2. Echo connection factory — for repositories that take an injected
   connection factory (RoomRatesRepository / PriceHistoryRepository):
   EchoConnection / EchoConnectionContext.
3. DI-override auth fakes — for route/WebSocket tests that override
   ``get_auth_service`` / ``get_accounts_repository``: FakeAuthService /
   FakeAccountsRepository.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

from api.services.auth_service import SupabaseAuthUser

DEFAULT_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# 1. Scripted engine family (monkeypatched module-level get_engine)
# ---------------------------------------------------------------------------


class FakeResult:
    """Scripted result for one connection.execute call."""

    def __init__(self, rows: list[dict] | None = None, rowcount: int = 0):
        self.rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one(self):
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalar(self):
        if not self.rows:
            return None
        return next(iter(self.rows[0].values()))


class FakeConnection:
    """Returns scripted results in order and records every executed statement.

    ``results=None`` runs unscripted: every execute returns a fresh empty
    FakeResult (for tests that only assert on the recorded ``calls``). With a
    list, over-execution raises IndexError on purpose — an unexpected extra
    query should fail the test.
    """

    def __init__(self, results: list[FakeResult] | None = None):
        self.results = list(results) if results is not None else None
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if self.results is None:
            return FakeResult()
        return self.results.pop(0)


class FakeTransaction:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeEngine:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.begin_calls = 0
        self.connect_calls = 0

    def begin(self):
        self.begin_calls += 1
        return FakeTransaction(self.connection)

    def connect(self):
        self.connect_calls += 1
        return FakeTransaction(self.connection)


def install_scripted_engine(
    monkeypatch,
    module_path: str,
    results: list[FakeResult] | None = None,
) -> FakeEngine:
    """Monkeypatch ``<module_path>.get_engine`` to yield a scripted FakeEngine.

    The lambda accepts any signature so it satisfies both bare ``get_engine()``
    calls and role-passing ones (``get_engine(role="api")``).
    """
    engine = FakeEngine(FakeConnection(results))
    monkeypatch.setattr(f"{module_path}.get_engine", lambda *args, **kwargs: engine)
    return engine


# ---------------------------------------------------------------------------
# 2. Echo connection-factory family (injected connection factory)
# ---------------------------------------------------------------------------


class EchoResult:
    """Empty mappings result: the echo family asserts on SQL, not rows."""

    def mappings(self):
        return self

    def all(self):
        return []


class EchoConnection:
    """Records the last executed SQL/params; every query returns no rows."""

    def __init__(self):
        self.executed_sql = ""
        self.executed_params: dict[str, Any] = {}

    def execute(self, sql, params):
        self.executed_sql = str(sql)
        self.executed_params = params
        return EchoResult()


class EchoConnectionContext(AbstractContextManager):
    def __init__(self, connection: EchoConnection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        return False


# ---------------------------------------------------------------------------
# 3. DI-override auth fakes (route / WebSocket tests)
# ---------------------------------------------------------------------------


class FakeAuthService:
    """Stand-in for SupabaseAuthService that maps fixed tokens to users."""

    def __init__(self, token_to_user: dict[str, SupabaseAuthUser] | None = None):
        self.token_to_user = token_to_user or {}

    async def verify_access_token(self, token: str) -> SupabaseAuthUser:
        from fastapi import HTTPException

        user = self.token_to_user.get(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired access token")
        return user


class FakeAccountsRepository:
    """Maps a verified identity to a stable account_id (no DB).

    ``overviews`` maps account_id -> the dict ``get_account_overview`` should
    return; a read for an unconfigured account raises KeyError so route wiring
    mistakes fail the test instead of silently returning data.
    """

    def __init__(
        self,
        account_id: UUID = DEFAULT_ACCOUNT_ID,
        overviews: dict[UUID, dict] | None = None,
    ):
        self.account_id = account_id
        self.overviews = overviews or {}
        self.identity_calls: list[dict] = []

    def get_or_create_account_for_identity(self, auth_provider, auth_subject, email, display_name):
        self.identity_calls.append(
            {
                "auth_provider": auth_provider,
                "auth_subject": auth_subject,
                "email": email,
                "display_name": display_name,
            }
        )
        return self.account_id

    def get_account_overview(self, account_id):
        return self.overviews[account_id]
